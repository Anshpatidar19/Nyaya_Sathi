"""Synthesis + validator layer.

Agentic Orchestration Layer:
  1. Query Refinement   - strip filler, detect act-overview questions
  2. Retrieval Agents   - local bare acts (statutes.py) + Indian Kanoon case law
  3. Synthesis Agent    - Gemini turns retrieved material into plain language
  4. Internal Validator - a second Gemini pass checks the answer is grounded

Statutes lead, case law supports. The section states the rule; the judgments
show how courts have read it.
"""

import asyncio
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from . import gemini, kanoon, routing, statutes, validity
from .config import settings
from .schemas import AskResponse, Citation

logger = logging.getLogger(__name__)

TOP_K = 3          # case-law results considered from Kanoon
TOP_STATUTES = 3   # bare-act sections retrieved locally
DOC_CHARS = 6000   # cap on grounding text per judgment (fallback path only)

# ---------------------------------------------------------------------------
# Support resources
# ---------------------------------------------------------------------------
# Some questions come from someone living the problem, not researching it.
# A section number alone is not a useful answer to "my husband beats me".
# These are appended to next_steps - they never replace the legal answer.

_SUPPORT_PATTERNS = re.compile(
    r"\b(my husband|my wife|my in-?laws|my partner|my boyfriend|my father|"
    r"beats? me|hits? me|hurt me|threaten(?:s|ed)? me|abus\w+|"
    r"domestic violence|dowry|molest\w*|rape[d]?|assault(?:ed)? me|"
    r"stalk\w+|harass\w+ me|afraid|unsafe|scared)\b",
    re.IGNORECASE,
)

_SUPPORT_STEPS = [
    "Women's Helpline: 181 (free, 24x7, nationwide). Police emergency: 112.",
    "District Legal Services Authority offices give free legal aid — you do not need to pay a lawyer to start.",
]


def needs_support(question: str) -> bool:
    return bool(_SUPPORT_PATTERNS.search(question))


# ---------------------------------------------------------------------------
# Agent 1 - Query refinement
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "can", "my", "me", "i", "a", "an", "the", "is", "are", "do", "does", "what",
    "how", "if", "to", "of", "in", "on", "for", "and", "or", "it", "be", "was",
    "should", "will", "would", "am", "have", "has", "get", "got", "please",
    "tell", "about", "doing", "there",
}


# A follow-up like "what about the deposit?" or "and if he refuses?" has
# almost no retrievable content on its own. Carrying terms from the previous
# turn is what makes threading work rather than just look like it does.
_FOLLOWUP_RE = re.compile(
    r"^\s*(and |but |so |then |also |what about|how about|what if|and if|"
    r"can he|can she|can they|can i|does that|is that|why|why not|"
    r"tell me more|more on that|explain that|elaborate|go on)\b",
    re.IGNORECASE,
)


def is_followup(question: str, history: Optional[List[Dict[str, str]]]) -> bool:
    """A short question, or one opening with a connective, inside a thread."""
    if not history:
        return False
    q = question.strip()
    if _FOLLOWUP_RE.match(q):
        return True
    # Very short questions inside a thread are almost always continuations.
    return len(q.split()) <= 6


def expand_followup(question: str, history: Optional[List[Dict[str, str]]]) -> str:
    """Prepend the previous question's terms so retrieval has something to
    work with. "What about the deposit?" alone retrieves nothing useful."""
    if not history or not is_followup(question, history):
        return question
    prior = history[-1].get("question", "")
    return f"{prior} {question}".strip()[:500]


def refine_query(question: str, state: Optional[str]) -> str:
    """Strip conversational filler so Kanoon's search sees legal terms."""
    words = [w.strip("?.,!\"'()") for w in question.lower().split()]
    keywords = [w for w in words if w and w not in _STOPWORDS]
    query = " ".join(keywords) if keywords else question
    if state:
        query = f"{query} {state}"
    return query[:400]


# Sections and acts named inside an uploaded document. A notice that cites
# "Section 138 of the Negotiable Instruments Act" tells us exactly what to
# retrieve - far better than guessing from "explain this".
_DOC_CITE_RE = re.compile(
    r"\b(?:section|sec\.?|s\.)\s*(\d+[A-Za-z-]*)"
    r"(?:\s*(?:of|,)?\s*(?:the\s+)?([A-Z][A-Za-z ]{4,60}?(?:Act|Sanhita|Code|Adhiniyam)))?",
    re.IGNORECASE,
)

_DOC_ACT_RE = re.compile(
    r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,5}\s+"
    r"(?:Act|Sanhita|Code|Adhiniyam|Rules|Regulations))(?:,?\s*(\d{4}))?"
)


def document_query(text: str, limit: int = 6) -> str:
    """Pull the legal anchors out of a document to drive retrieval.

    Retrieval on the raw text is hopeless - a rent agreement is thousands of
    words of boilerplate. The section and act references are the signal.
    """
    head = (text or "")[:6000]
    terms: List[str] = []

    for m in _DOC_CITE_RE.finditer(head):
        section, act = m.group(1), (m.group(2) or "").strip()
        terms.append(f"section {section} {act}".strip())
        if len(terms) >= limit:
            break

    if len(terms) < limit:
        for m in _DOC_ACT_RE.finditer(head):
            act = m.group(1).strip()
            if act and act not in terms:
                terms.append(act)
            if len(terms) >= limit:
                break

    # De-duplicate, keep order.
    seen = set()
    unique = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return " ".join(unique)[:400]


# ---------------------------------------------------------------------------
# Agent 2 - Retrieval
# ---------------------------------------------------------------------------

async def _hydrate(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Attach judgment text so synthesis is grounded, not headline-guessing.

    Three tiers, cheapest first:
      1. the query-matched fragment  - the passages that actually matched
      2. the full document, truncated - fallback if fragments aren't available
      3. the search headline          - already paid for, costs nothing extra

    Tier 1 matters. A judgment can run to hundreds of pages, and the first
    6,000 characters are the cause title, counsel appearances and procedural
    history - the least useful part of the document.
    """
    async def _one(r: Dict[str, Any]) -> Dict[str, Any]:
        headline = kanoon.strip_html(r.get("snippet") or "")
        text = headline
        docid = r.get("docid")

        if docid:
            try:
                frag = await kanoon.fragment(docid, query)
                if frag:
                    text = frag
                # An empty fragment is NOT a reason to fetch the full document:
                # that would mean two billed calls per judgment. The search
                # headline below is already paid for and is enough to keep the
                # source in play.
            except kanoon.FragmentUnavailable:
                # The fragment call itself failed, so falling back is the only
                # way to get text for this judgment. One call, not two.
                try:
                    doc = await kanoon.get_document(docid)
                    full = kanoon.strip_html(doc.get("doc") or "")
                    if full:
                        text = full[:DOC_CHARS]
                except Exception as exc:
                    logger.warning("Kanoon doc fetch failed for %s: %s", docid, exc)
            except Exception as exc:
                logger.warning("Kanoon text fetch failed for %s: %s", docid, exc)

        return {**r, "text": text, "headline": headline}

    # Fetched together, not one after another. These are independent HTTP
    # calls to the same host - waiting for each in turn made the slowest
    # judgment set the latency for all of them. Same number of billed calls,
    # a third of the wall time.
    hydrated: List[Dict[str, Any]] = list(
        await asyncio.gather(*(_one(r) for r in results))
    )
    return hydrated


async def retrieve(
    question: str,
    state: Optional[str],
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Bare acts first, then case law. Either source alone is enough to answer."""
    # With a document attached, "explain this" carries no retrievable terms.
    # The provisions the document itself names are what to look up.
    doc_terms = document_query(document["text"]) if document and document.get("text") else ""
    retrieval_text = f"{question} {doc_terms}".strip() if doc_terms else question

    # Act-level question ("tell me about BNS") - BM25 can't help, answer directly.
    overview = statutes.act_overview(retrieval_text)
    statute_hits = [] if overview else statutes.search(retrieval_text, limit=TOP_STATUTES)

    if overview:
        sources = [statutes.overview_as_source(overview)]
    else:
        sources = [statutes.as_source(s) for s in statute_hits]

    # Stamp each statute chunk with whether it is still law. Done here, once,
    # so every downstream consumer - synthesis, citation cards, the grounding
    # assessment - sees the same status.
    validity.annotate(sources)

    decision = routing.decide(retrieval_text, statute_hits, overview)

    if not decision.call_kanoon:
        logger.info("Kanoon skipped (%s) - saved ~%d calls", decision.reason, 1 + 2)
        return sources

    logger.info("Kanoon called (%s)", decision.reason)

    query = refine_query(retrieval_text, None)   # state goes in the court filter now
    try:
        results = await kanoon.search(query, state=state)
    except Exception as exc:
        logger.warning("Kanoon search failed: %s", exc)
        return sources

    if not results:
        # An empty result set can mean a dry balance - the API returns nothing
        # rather than erroring when credit runs out.
        logger.warning("Kanoon returned no results for %r - check API balance", query)
        return sources

    # Spend document fetches on the judgments most worth reading.
    ranked = kanoon.rank_by_citations(results[:TOP_K * 2], top=decision.doc_fetches)
    sources.extend(await _hydrate(ranked, query))

    return sources


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def run_live_pipeline(
    question: str,
    state: str | None,
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
) -> AskResponse | None:
    """Retrieval -> Synthesis -> Internal Validator."""
    if not settings.gemini_api_key:
        return None

    t0 = time.perf_counter()
    sources = await retrieve(question, state, history, document)
    t_retrieve = time.perf_counter() - t0

    # With a document attached, an empty source list is survivable: the
    # document itself is the material to explain. Without one, there is
    # nothing to ground an answer in and we should not invent it.
    if not sources and not document:
        return None

    t1 = time.perf_counter()
    try:
        draft = await gemini.synthesize(question, state, sources, history, document)
    except gemini.GeminiError as exc:
        logger.warning("Gemini synthesis failed: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Unexpected synthesis error: %s", exc)
        return None

    t_synth = time.perf_counter() - t1

    body = draft["body"]
    if not body:
        return None

    # --- Internal validator ---
    # Skipped when a document is attached: the validator checks claims against
    # retrieved sources, and most of a document explanation is grounded in the
    # document instead. Running it here flags correct answers as unsupported.
    t2 = time.perf_counter()
    try:
        verdict = (
            {"grounded": True, "issues": [], "revised_body": ""}
            if document
            else await gemini.validate(question, draft, sources, state)
        )
        t_validate = time.perf_counter() - t2
        # One line per answer, so the split is visible without a profiler.
        # Guessing which stage is slow is how the wrong thing gets optimised.
        logger.info(
            "TIMING retrieve=%.2fs synth=%.2fs validate=%.2fs total=%.2fs "
            "(%d sources, %d chars)",
            t_retrieve, t_synth, t_validate, time.perf_counter() - t0,
            len(sources), len(body),
        )
        if not verdict["grounded"]:
            logger.info("Validator flagged answer: %s", verdict["issues"])
            if verdict["revised_body"]:
                body = verdict["revised_body"]
            else:
                body = (
                    "The provisions and judgments retrieved for this question "
                    "don't clearly answer it, so a reliable plain-language "
                    "summary can't be given here. The sources below are the "
                    "closest matches — it's worth reading them, or speaking to "
                    "a lawyer, before acting."
                )
    except Exception as exc:
        logger.warning("Validator step failed, returning unvalidated draft: %s", exc)

    return _finalise(question, body, draft, sources)


def _finalise(
    question: str,
    body: str,
    draft: Dict[str, Any],
    sources: List[Dict[str, Any]],
) -> AskResponse:
    """Citations, next steps and the grounding badge.

    Shared by the buffered and streaming paths. Everything here depends on
    the finished answer, which is why the streaming endpoint can only send it
    after the last character has arrived.
    """
    # --- Citations: the sources synthesis actually used ---
    used = draft["used_sources"]
    chosen = [sources[i - 1] for i in used if 1 <= i <= len(sources)] or sources

    citations = [
        Citation(
            title=s.get("title") or "Untitled",
            source=" · ".join(p for p in [s.get("court"), s.get("date")] if p) or "Source",
            docid=s.get("docid"),
            url=s.get("url"),
            # The headline is a query-matched snippet already paid for by the
            # search call. Showing it turns the source list from a
            # bibliography into something the user can actually judge.
            snippet=(s.get("headline") or s.get("snippet") or "")[:260] or None,
        )
        for s in chosen
    ]

    next_steps = draft["next_steps"] or [
        "Read the linked provisions and judgments above for the full text.",
        "Consider speaking to a lawyer or a legal aid clinic before you act.",
    ]

    # Statutes outside the ingested acts that matter here (e.g. the DV Act).
    for law in statutes.related_laws(statutes.search(question, limit=TOP_STATUTES)):
        next_steps.append(f"Also look at the {law['name']}. {law['why']}")

    if needs_support(question):
        next_steps.extend(_SUPPORT_STEPS)

    # How well supported this answer actually is. Derived from what retrieval
    # returned and what the prose cites - never asked of the model, which
    # would happily rate a hallucinated citation at 85%.
    grounding = validity.assess(
        body=body,
        sources=sources,
        citations=[c.model_dump() for c in citations],
        used_sources=used,
    )

    # A repealed provision in the sources is worth saying out loud, not just
    # badging - an advocate acting on IPC 302 in 2026 has a real problem.
    for s_ in sources:
        v = s_.get("validity") or {}
        if v.get("status") == validity.REPEALED and v.get("note"):
            note = f"{s_.get('act_short', '')} {s_.get('section', '')}: {v['note']}".strip()
            if note not in next_steps:
                next_steps.insert(0, note)
            break

    return AskResponse(
        title=draft["title"],
        body=body,
        citations=citations,
        next_steps=next_steps,
        grounding=grounding,
    )


async def answer_question_stream(
    question: str,
    state: str | None = None,
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """Stream the answer as it is written.

    Yields:
      ("delta", str)          - new characters of the body
      ("done", AskResponse)   - citations, next steps and the grounding badge
      ("revised", str)        - a corrected body, when the validator rejects
                                what was already shown

    The validator still runs and still blocks the "done" event, so nothing is
    stored or badged unchecked. What changes is that the reader is not staring
    at a spinner while it happens. On the rare occasion the validator rejects
    the draft, the body is replaced - visibly - rather than never having been
    shown, which is the honest trade for the wait being gone.
    """
    if not settings.gemini_api_key:
        yield "error", "GEMINI_API_KEY is not set."
        return

    t0 = time.perf_counter()
    sources = await retrieve(question, state, history, document)
    t_retrieve = time.perf_counter() - t0

    if not sources and not document:
        yield "error", "Nothing relevant was retrieved for this question."
        return

    t1 = time.perf_counter()
    draft: Optional[Dict[str, Any]] = None
    try:
        async for kind, value in gemini.synthesize_stream(
            question, state, sources, history, document
        ):
            if kind == "delta":
                yield "delta", value
            else:
                draft = value
    except gemini.GeminiError as exc:
        logger.warning("Gemini streaming synthesis failed: %s", exc)
        yield "error", "The answer could not be generated. Please try again."
        return
    except Exception as exc:
        logger.exception("Unexpected streaming synthesis error: %s", exc)
        yield "error", "The answer could not be generated. Please try again."
        return
    t_synth = time.perf_counter() - t1

    if not draft or not draft.get("body"):
        yield "error", "The answer came back empty. Please try again."
        return

    body = draft["body"]

    # --- Internal validator ---
    # Skipped for documents, exactly as in the buffered path.
    t2 = time.perf_counter()
    try:
        verdict = (
            {"grounded": True, "issues": [], "revised_body": ""}
            if document
            else await gemini.validate(question, draft, sources, state)
        )
        if not verdict["grounded"]:
            logger.info("Validator flagged answer: %s", verdict["issues"])
            body = verdict["revised_body"] or (
                "The provisions and judgments retrieved for this question "
                "don't clearly answer it, so a reliable plain-language "
                "summary can't be given here. The sources below are the "
                "closest matches - it's worth reading them, or speaking to "
                "a lawyer, before acting."
            )
            # The reader has already seen the draft, so say plainly that it
            # has been replaced rather than swapping it out silently.
            yield "revised", body
    except Exception as exc:
        logger.warning("Validator step failed, returning unvalidated draft: %s", exc)
    t_validate = time.perf_counter() - t2

    logger.info(
        "TIMING(stream) retrieve=%.2fs synth=%.2fs validate=%.2fs total=%.2fs "
        "(%d sources, %d chars)",
        t_retrieve, t_synth, t_validate, time.perf_counter() - t0,
        len(sources), len(body),
    )

    yield "done", _finalise(question, body, draft, sources)


async def answer_question(
    question: str,
    state: str | None = None,
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
) -> AskResponse:
    """Answer a question, optionally about an attached document.

    `document` is {"filename": str, "text": str} and must already have passed
    the scope gate in doc_scope - this layer trusts that it is legal material.
    """
    live = await run_live_pipeline(question, state, history, document)
    if live is not None:
        return live

    return AskResponse(
        title="Couldn't answer that one",
        body=(
            "This question couldn't be answered from the statutes and judgments "
            "available right now. That usually means the retrieval step found "
            "nothing relevant, or the language model isn't configured — check "
            "that GEMINI_API_KEY is set and that backend/data/bns.json exists "
            "(run: python -m app.ingest_bns)."
        ),
        citations=[],
        next_steps=[
            "Try naming the section directly, e.g. \"what is BNS 85\".",
            "Or describe the situation in plain words, e.g. \"my landlord locked me out\".",
        ],
    )