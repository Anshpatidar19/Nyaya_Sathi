"""Synthesis + validator layer.

Agentic Orchestration Layer:
  1. Query Refinement   - strip filler, detect act-overview questions
  2. Retrieval Agents   - local bare acts (statutes.py) + Indian Kanoon case law
  3. Synthesis Agent    - Gemini turns retrieved material into plain language
  4. Internal Validator - a second Gemini pass checks the answer is grounded

Statutes lead, case law supports. The section states the rule; the judgments
show how courts have read it.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from . import gemini, kanoon, statutes
from .config import settings
from .schemas import AskResponse, Citation

logger = logging.getLogger(__name__)

TOP_K = 3          # case-law results from Kanoon
TOP_STATUTES = 3   # bare-act sections retrieved locally
DOC_CHARS = 6000   # grounding text per judgment

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


# ---------------------------------------------------------------------------
# Agent 2 - Retrieval
# ---------------------------------------------------------------------------

async def _hydrate(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach real judgment text so synthesis is grounded, not headline-guessing."""
    hydrated: List[Dict[str, Any]] = []
    for r in results:
        text = kanoon.strip_html(r.get("snippet") or "")
        docid = r.get("docid")
        if docid:
            try:
                doc = await kanoon.get_document(docid)
                full = kanoon.strip_html(doc.get("doc") or "")
                if full:
                    text = full[:DOC_CHARS]
            except Exception as exc:
                logger.warning("Kanoon doc fetch failed for %s: %s", docid, exc)
        hydrated.append({**r, "text": text})
    return hydrated


async def retrieve(
    question: str,
    state: Optional[str],
    history: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Bare acts first, then case law. Either source alone is enough to answer."""
    # Act-level question ("tell me about BNS") - BM25 can't help, answer directly.
    overview = statutes.act_overview(question)
    if overview:
        return [statutes.overview_as_source(overview)]

    statute_hits = statutes.search(question, limit=TOP_STATUTES)
    sources = [statutes.as_source(s) for s in statute_hits]

    try:
        results = await kanoon.search(refine_query(question, state))
    except Exception as exc:
        logger.warning("Kanoon search failed: %s", exc)
        results = []

    if results:
        sources.extend(await _hydrate(results[:TOP_K]))

    return sources


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def run_live_pipeline(
    question: str,
    state: str | None,
    history: Optional[List[Dict[str, str]]] = None,
) -> AskResponse | None:
    """Retrieval -> Synthesis -> Internal Validator."""
    if not settings.gemini_api_key:
        return None

    sources = await retrieve(question, state, history)
    if not sources:
        return None

    try:
        draft = await gemini.synthesize(question, state, sources, history)
    except gemini.GeminiError as exc:
        logger.warning("Gemini synthesis failed: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Unexpected synthesis error: %s", exc)
        return None

    body = draft["body"]
    if not body:
        return None

    # --- Internal validator ---
    try:
        verdict = await gemini.validate(question, draft, sources)
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

    # --- Citations: the sources synthesis actually used ---
    used = draft["used_sources"]
    chosen = [sources[i - 1] for i in used if 1 <= i <= len(sources)] or sources

    citations = [
        Citation(
            title=s.get("title") or "Untitled",
            source=" · ".join(p for p in [s.get("court"), s.get("date")] if p) or "Source",
            docid=s.get("docid"),
            url=s.get("url"),
        )
        for s in chosen
    ]

    next_steps = draft["next_steps"] or [
        "Read the linked provisions and judgments above for the full text.",
        "Consider speaking to a lawyer or a legal aid clinic before you act.",
    ]

    # Statutes outside the ingested acts that matter here (e.g. the DV Act).
    statute_hits = statutes.search(question, limit=TOP_STATUTES)
    for law in statutes.related_laws(statute_hits):
        next_steps.append(f"Also look at the {law['name']}. {law['why']}")

    if needs_support(question):
        next_steps.extend(_SUPPORT_STEPS)

    return AskResponse(
        title=draft["title"],
        body=body,
        citations=citations,
        next_steps=next_steps,
    )


async def answer_question(
    question: str,
    state: str | None = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> AskResponse:
    live = await run_live_pipeline(question, state, history)
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