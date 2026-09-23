"""Synthesis + validator layer.

Agentic Orchestration Layer:
  0. Fast Path          - greetings and small talk answered directly, with
                           no retrieval and no Gemini call at all
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
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from . import gemini, kanoon, routing, statutes, validity
from .config import settings
from .schemas import AskResponse, Citation

logger = logging.getLogger(__name__)

TOP_K = 3          # case-law results considered from Kanoon
TOP_STATUTES = 3   # bare-act sections retrieved locally
DOC_CHARS = 6000   # cap on grounding text per judgment (fallback path only)

# Cap on the query-matched fragment kept per judgment.
#
# This one is worth understanding, because it was uncapped and it is the
# single largest lever on time-to-first-word. /docfragment/ returns *every*
# passage that matched, concatenated - routinely 15,000-30,000 characters for
# a long judgment. Three of those is ~20k tokens of prompt that the model has
# to read before it can emit a single character of answer, and then the
# validator reads them again. The passages are already ranked by the match, so
# the tail is the weakest material in the response: cutting it costs almost
# nothing in grounding and removes seconds of prefill.
FRAGMENT_CHARS = 4000

# A slow judgment must not set the latency for the answer. Fragments are
# fetched concurrently, so this is a per-judgment deadline: past it, the
# search headline (already paid for, already query-matched) stands in and the
# source stays in play rather than the whole answer waiting.
#
# 3.5s, not 6: measured retrieve times of 9s on case-law questions were mostly
# this step, and a fragment call that hasn't answered in three and a half
# seconds is not usually about to.
HYDRATE_TIMEOUT = 3.5

# Streaming only: send the finished payload - citations, next steps, grounding
# - as soon as synthesis ends, and run the validator after it, emitting a
# "revised" frame if it rejects the draft.
#
# This does not weaken the check. The streaming path has ALREADY shown the
# reader every word of the unvalidated draft by the time the validator runs,
# so holding the citations back protects nothing; it just adds the validator's
# round trip to the wait on every answer, including the overwhelming majority
# that pass. The validator still runs, still blocks the stored answer, and
# still replaces the body visibly when it objects.
#
# Set to False to go back to validating before anything is sent.
STREAM_DONE_BEFORE_VALIDATION = True


@dataclass
class Retrieved:
    """What retrieval produced, kept together so nothing is recomputed.

    `statute_hits` used to be thrown away and then re-derived in _finalise()
    by calling statutes.search() a second time - a second full BM25 pass over
    the corpus, and with the dense fallback enabled a second embedding call
    and a second pair of Pinecone lookups, all to answer a question that had
    already been answered.
    """
    sources: List[Dict[str, Any]] = field(default_factory=list)
    statute_hits: List[Dict[str, Any]] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Agent 0 - Fast path for greetings and small talk
# ---------------------------------------------------------------------------
# "hi" was going through the full pipeline: BM25 retrieval, a Kanoon search
# for the word "hi", a Gemini synthesis call, and a Gemini validator call -
# several seconds of latency and two paid API calls to answer something that
# needs neither law nor a language model. Matched here, it costs one regex
# check and returns immediately. Anything that isn't a clean, standalone
# greeting falls through to the real pipeline unchanged - "hi, can my
# landlord evict me?" is a legal question, not small talk, and must not be
# short-circuited.

_GREETING_ONLY_RE = re.compile(
    r"^(?:"
    r"(?:hi+|hello+|hey+|yo|hola|namaste[a-z]*)(?:\s+there)?|"
    r"good\s*(?:morning|afternoon|evening|night)|"
    r"how\s+are\s+you|how\s+r\s+u|how'?s\s+it\s+going|what'?s\s+up|"
    r"who\s+are\s+you|what\s+(?:are\s+you|can\s+you\s+do)|"
    r"thanks?(?:\s+a\s+lot)?|thank\s+you(?:\s+(?:so\s+much|very\s+much|a\s+lot))?|"
    r"ok(?:ay)?|cool|great|nice(?:\s+one)?|got\s+it|"
    r"bye+|good\s*bye|see\s+you|take\s+care"
    r")[\s!.,?]*$",
    re.IGNORECASE,
)

# Keeps a real question from matching by accident - "hi" is four characters,
# "how are you doing given my landlord just filed an eviction notice" is not.
_GREETING_MAX_CHARS = 40

_GREETING_TITLE = "Hi there"

_GREETING_NEXT_STEPS = [
    "Ask a specific question, e.g. \"can my landlord evict me without notice?\"",
]


def greeting_reply(question: str) -> Optional[Dict[str, Any]]:
    """A canned, instant reply for small talk - None for anything else.

    Deliberately conservative: this only fires when the ENTIRE message is a
    greeting, thanks, or sign-off, not when one merely appears in it.
    """
    q = question.strip()
    if not q or len(q) > _GREETING_MAX_CHARS:
        return None
    if not _GREETING_ONLY_RE.match(q):
        return None

    ql = q.lower()
    if "thank" in ql:
        body = (
            "You're welcome! If anything else comes up — a notice, a "
            "dispute, a question about your rights — I'm here."
        )
    elif any(w in ql for w in ("bye", "see you", "take care")):
        body = "Take care. Come back any time you need a legal question answered."
    elif "who are you" in ql or "what are you" in ql or "what can you do" in ql:
        body = (
            "I'm Nyaya Sathi — I answer legal questions in plain language, "
            "grounded in Indian statutes and case law, and I can also draft "
            "and review common documents. Ask me anything, e.g. \"how do I "
            "file an RTI application?\""
        )
    else:
        body = (
            "Hello! I'm Nyaya Sathi. Ask me a legal question in plain "
            "language — for example, \"what are my rights if I'm arrested?\" "
            "or \"can my landlord raise my rent without notice?\""
        )

    return {"title": _GREETING_TITLE, "body": body, "next_steps": list(_GREETING_NEXT_STEPS)}


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
                    text = frag[:FRAGMENT_CHARS]
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

    async def _one_guarded(r: Dict[str, Any]) -> Dict[str, Any]:
        """_one() with a deadline. Past it, fall back to the headline.

        Concurrency stops the fetches queueing behind each other, but it does
        not help when one judgment is simply slow - gather() waits for the
        last one. The headline is a query-matched snippet the search call
        already paid for, so timing out degrades one source rather than the
        whole answer.
        """
        try:
            return await asyncio.wait_for(_one(r), timeout=HYDRATE_TIMEOUT)
        except asyncio.TimeoutError:
            headline = kanoon.strip_html(r.get("snippet") or "")
            logger.warning(
                "Kanoon text fetch for %s exceeded %.1fs; using the headline",
                r.get("docid"), HYDRATE_TIMEOUT,
            )
            return {**r, "text": headline, "headline": headline}

    # Fetched together, not one after another. These are independent HTTP
    # calls to the same host - waiting for each in turn made the slowest
    # judgment set the latency for all of them. Same number of billed calls,
    # a third of the wall time.
    hydrated: List[Dict[str, Any]] = list(
        await asyncio.gather(*(_one_guarded(r) for r in results))
    )
    return hydrated


def _local_sources(
    retrieval_text: str,
    overview: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The local half of retrieval. Synchronous on purpose - see retrieve().

    Returns (sources, statute_hits).
    """
    statute_hits = [] if overview else statutes.search(retrieval_text, limit=TOP_STATUTES)

    if overview:
        sources = [statutes.overview_as_source(overview)]
    else:
        sources = [statutes.as_source(s) for s in statute_hits]

    # Stamp each statute chunk with whether it is still law. Done here, once,
    # so every downstream consumer - synthesis, citation cards, the grounding
    # assessment - sees the same status.
    validity.annotate(sources)
    return sources, statute_hits


async def retrieve(
    question: str,
    state: Optional[str],
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
) -> Retrieved:
    """Bare acts first, then case law. Either source alone is enough to answer.

    Two things about the shape of this function are about latency rather than
    retrieval quality:

    *The local pass runs in a worker thread.* statutes.search() is pure-Python
    BM25 over the whole corpus, and when the dense fallback fires it also makes
    a blocking embedding request and two Pinecone lookups - with retries that
    call time.sleep(). All of that on the event loop stalls every other request
    in the process, including the one streaming an answer to someone else. It
    is the same work, just not in the way.

    *The Kanoon search starts before the local pass finishes*, whenever the
    routing decision can be read off the wording alone (see routing.prejudge).
    They query unrelated systems; running them in sequence meant paying for
    both in series for no reason. Billed calls are unchanged - the gate still
    decides, and a question routed away from Kanoon never starts the search.
    """
    # With a document attached, "explain this" carries no retrievable terms.
    # The provisions the document itself names are what to look up.
    doc_terms = document_query(document["text"]) if document and document.get("text") else ""
    retrieval_text = f"{question} {doc_terms}".strip() if doc_terms else question

    # Act-level question ("tell me about BNS") - BM25 can't help, answer
    # directly. Pure regex against a dictionary, so it stays inline.
    overview = statutes.act_overview(retrieval_text)

    local_task = asyncio.create_task(
        asyncio.to_thread(_local_sources, retrieval_text, overview)
    )

    query = refine_query(retrieval_text, None)   # state goes in the court filter now
    early = routing.prejudge(retrieval_text, overview)
    search_task = None
    if early is not None and early.call_kanoon:
        search_task = asyncio.create_task(kanoon.search(query, state=state))

    t_start = time.perf_counter()
    try:
        sources, statute_hits = await local_task
    except Exception:
        if search_task is not None:
            search_task.cancel()
        raise
    t_local = time.perf_counter() - t_start

    decision = early or routing.decide(retrieval_text, statute_hits, overview)
    found = Retrieved(sources=sources, statute_hits=statute_hits)

    if not decision.call_kanoon:
        # Only reachable with a live search_task if prejudge and decide
        # disagreed, which they cannot - but cancelling is free and means a
        # future edit to either can't leak a billed call.
        if search_task is not None:
            search_task.cancel()
        logger.info(
            "RETRIEVE local=%.2fs kanoon=skipped (%s) - saved ~%d calls",
            t_local, decision.reason, 1 + 2,
        )
        return found

    t_search = time.perf_counter()
    try:
        if search_task is None:
            results = await kanoon.search(query, state=state)
        else:
            results = await search_task
    except Exception as exc:
        logger.warning("Kanoon search failed: %s", exc)
        return found
    t_search = time.perf_counter() - t_search

    if not results:
        # An empty result set can mean a dry balance - the API returns nothing
        # rather than erroring when credit runs out.
        logger.warning("Kanoon returned no results for %r - check API balance", query)
        return found

    # Spend document fetches on the judgments most worth reading.
    t_hydrate = time.perf_counter()
    ranked = kanoon.rank_by_citations(results[:TOP_K * 2], top=decision.doc_fetches)
    found.sources.extend(await _hydrate(ranked, query))
    t_hydrate = time.perf_counter() - t_hydrate

    # Split out because the totals alone don't say which half to work on, and
    # "retrieve=9.25s" was three very different things added together. Note
    # that `search` reads as ~0 when it overlapped the local pass - that is
    # the overlap working, not the call being free.
    logger.info(
        "RETRIEVE local=%.2fs search=%.2fs hydrate=%.2fs (%s, %d judgments)",
        t_local, t_search, t_hydrate, decision.reason, len(ranked),
    )

    return found


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

    # Fast path: a plain "hi" needs no document context to answer, and
    # skipping it here means the greeting still short-circuits even when the
    # frontend happens to send one along.
    if not document:
        greeting = greeting_reply(question)
        if greeting:
            return AskResponse(
                title=greeting["title"],
                body=greeting["body"],
                citations=[],
                next_steps=greeting["next_steps"],
                grounding=None,
            )

    t0 = time.perf_counter()
    found = await retrieve(question, state, history, document)
    sources = found.sources
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
        overview_only = bool(sources) and all(
            s.get("kind") == "overview" for s in sources
        )
        verdict = (
            {"grounded": True, "issues": [], "revised_body": ""}
            if (document or overview_only)
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

    return _finalise(question, body, draft, found)


def _finalise(
    question: str,
    body: str,
    draft: Dict[str, Any],
    found: Retrieved,
) -> AskResponse:
    """Citations, next steps and the grounding badge.

    Shared by the buffered and streaming paths. Everything here depends on
    the finished answer, which is why the streaming endpoint can only send it
    after the last character has arrived.
    """
    sources = found.sources

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
    #
    # This used to call statutes.search() again, from scratch, on every single
    # answer - a full second BM25 pass, plus a second embedding call and two
    # more Pinecone lookups whenever the dense fallback was enabled, purely to
    # re-derive hits retrieval had already computed a few seconds earlier.
    # Same sections, same pointers, none of the wait.
    for law in statutes.related_laws(found.statute_hits):
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

    The validator always runs. With STREAM_DONE_BEFORE_VALIDATION set it runs
    after "done" rather than before it, so "revised" can arrive last: the
    reader gets the citations as soon as the prose is finished instead of
    waiting on a check that passes almost every time, and on the rare
    rejection the body is replaced - visibly - as it was before. Callers must
    therefore treat a later "revised" as authoritative over the body inside
    "done", and correct anything they stored from it.
    """
    if not settings.gemini_api_key:
        yield "error", "GEMINI_API_KEY is not set."
        return

    # Fast path: same short-circuit as the buffered pipeline. Sent as a
    # single delta rather than trickling character-by-character - there's
    # nothing to gain from typing out "Hello!" slowly, and the whole point
    # is to skip the wait, not relocate it.
    if not document:
        greeting = greeting_reply(question)
        if greeting:
            yield "delta", greeting["body"]
            yield "done", AskResponse(
                title=greeting["title"],
                body=greeting["body"],
                citations=[],
                next_steps=greeting["next_steps"],
                grounding=None,
            )
            return

    t0 = time.perf_counter()
    found = await retrieve(question, state, history, document)
    sources = found.sources
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
            elif kind == "replace":
                # The model failed after part of the answer had streamed, and
                # a retry (or the Groq fallback) wrote the whole answer again.
                # The fragment on screen is swapped for the finished body via
                # the existing "revised" event - the same one the validator
                # uses - so the frontend needs no change. Sent before "done",
                # so there is no stored row to correct yet.
                yield "revised", value
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
    # Skipped for documents, exactly as in the buffered path - and for an
    # act-overview answer, for a related reason.
    #
    # An overview is a hand-written paragraph about a whole act: what it
    # replaced, how many sections it has, what it covers. It cannot support a
    # section-level claim, so "murder is in BNS 103" is unsupported by the
    # only excerpt available and the validator rejects it every single time -
    # then pays for a 3,000-token revision that replaces a correct answer
    # with a hedge. Two Gemini calls to make the answer worse.
    #
    # The honest alternative is to retrieve real sections alongside the
    # overview so the claims ARE checkable; that changes what these questions
    # return and wants benchmarking against the question set first, which is
    # why it isn't done here.
    overview_only = bool(sources) and all(
        s.get("kind") == "overview" for s in sources
    )
    skip_validation = bool(document) or overview_only
    if overview_only:
        logger.info("Validator skipped: act-overview answer, nothing to check against")

    async def _verdict() -> Dict[str, Any]:
        if skip_validation:
            return {"grounded": True, "issues": [], "revised_body": ""}
        return await gemini.validate(question, draft, sources, state)

    def _replacement(verdict: Dict[str, Any]) -> Optional[str]:
        """The body to show instead, or None if the draft stands."""
        if verdict["grounded"]:
            return None
        logger.info("Validator flagged answer: %s", verdict["issues"])
        return verdict["revised_body"] or (
            "The provisions and judgments retrieved for this question "
            "don't clearly answer it, so a reliable plain-language "
            "summary can't be given here. The sources below are the "
            "closest matches - it's worth reading them, or speaking to "
            "a lawyer, before acting."
        )

    t2 = time.perf_counter()

    if STREAM_DONE_BEFORE_VALIDATION and not skip_validation:
        # The validator's round trip used to sit between the last streamed
        # word and the citations appearing - about a second and a half of
        # spinner on every answer, including the ~95% the validator passes
        # without comment. It starts here and runs while the payload is
        # assembled and sent, so the reader gets the sources immediately and
        # the check still completes before this generator ends.
        task = asyncio.create_task(_verdict())
        try:
            yield "done", _finalise(question, body, draft, found)
        except GeneratorExit:
            # The client hung up while we were handing over the payload. The
            # validator is in flight and nothing is left to read its verdict,
            # so cancel it rather than leaving an orphaned task holding a
            # connection for the rest of the request's lifetime.
            task.cancel()
            raise

        try:
            replacement = _replacement(await task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Validator step failed, leaving the draft as sent: %s", exc)
            replacement = None

        t_validate = time.perf_counter() - t2
        if replacement:
            # The reader has already seen the draft, so say plainly that it
            # has been replaced rather than swapping it out silently. The
            # caller uses this to correct the stored answer too.
            body = replacement
            yield "revised", body
    else:
        try:
            replacement = _replacement(await _verdict())
            if replacement:
                body = replacement
                yield "revised", body
        except Exception as exc:
            logger.warning("Validator step failed, returning unvalidated draft: %s", exc)
        t_validate = time.perf_counter() - t2
        yield "done", _finalise(question, body, draft, found)

    logger.info(
        "TIMING(stream) retrieve=%.2fs synth=%.2fs validate=%.2fs total=%.2fs "
        "(%d sources, %d chars)",
        t_retrieve, t_synth, t_validate, time.perf_counter() - t0,
        len(sources), len(body),
    )


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