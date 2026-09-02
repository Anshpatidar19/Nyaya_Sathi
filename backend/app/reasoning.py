"""Synthesis + validator layer.

This module implements the "Agentic Orchestration Layer" from the spec:
  1. Query Refinement  - handled upstream (frontend sends plain text)
  2. Retrieval Agent    - queries Indian Kanoon (see kanoon.py)
  3. Synthesis Agent     - LLM turns retrieved judgments/statutes into a
                           plain-language answer + next steps
  4. Internal Validator  - checks the synthesized answer is actually
                           grounded in the retrieved text before it is
                           returned to the user

Right now `answer_question()` runs a small curated fallback bank so the
product is fully demoable with zero API keys configured. Once
INDIAN_KANOON_API_TOKEN and GEMINI_API_KEY are set in backend/.env, wire
`run_live_pipeline()` in as the primary path and keep the fallback bank
as a last resort if retrieval comes back empty.
"""

from typing import Dict, List

from . import kanoon
from .config import settings
from .schemas import AskResponse, Citation

# ---- Curated fallback bank (works with no API keys, demonstrates the UI) ----

_FALLBACK_BANK: List[Dict] = [
    {
        "keywords": ["evict", "landlord", "tenant", "rent"],
        "title": "Not without proper notice",
        "body": (
            "Your landlord must give written notice and follow the process "
            "set out in your state's rent law before you can be asked to "
            "leave. If there's no written agreement, the notice period is "
            "usually longer, not shorter."
        ),
        "citations": [
            {"title": "State Rent Control Act", "source": "Eviction & notice provisions"},
            {"title": "Transfer of Property Act, 1882", "source": "Section 106 · Tenancy at will"},
        ],
        "next_steps": [
            "Ask your landlord for the eviction notice in writing.",
            "If none was given, reply in writing that you have not received valid notice.",
        ],
    },
    {
        "keywords": ["cheque", "bounce", "dishonour", "dishonor"],
        "title": "You have 30 days to send a demand notice",
        "body": (
            "The payee must send a written demand notice within 30 days of "
            "the bounce, and the drawer then has 15 days to pay. If payment "
            "still doesn't come, a complaint can be filed within one month "
            "after that."
        ),
        "citations": [
            {"title": "Negotiable Instruments Act, 1881", "source": "Section 138 · Dishonour of cheque"},
            {"title": "Negotiable Instruments Act, 1881", "source": "Section 142 · Filing timeline"},
        ],
        "next_steps": [
            "Send a written demand notice within 30 days of the bounce.",
            "Wait 15 days for payment before filing a complaint.",
        ],
    },
    {
        "keywords": ["fired", "terminate", "job", "employee", "notice period", "retrench"],
        "title": "Not without notice or pay in lieu",
        "body": (
            "Not unless it's for proven misconduct — permanent employees "
            "are entitled to notice under standing orders, and any "
            "retrenchment also requires a month's notice and compensation."
        ),
        "citations": [
            {"title": "Industrial Employment (Standing Orders) Act, 1946", "source": "Model standing orders"},
            {"title": "Industrial Disputes Act, 1947", "source": "Section 25F · Retrenchment"},
        ],
        "next_steps": [
            "Ask for the termination reason and notice period in writing.",
            "Check whether retrenchment compensation is due under Section 25F.",
        ],
    },
    {
        "keywords": ["refund", "faulty", "defective", "product", "consumer"],
        "title": "Yes, you can seek a refund or replacement",
        "body": (
            "A defective product entitles you to a repair, replacement, or "
            "refund, and you can file a complaint even without a lawyer. "
            "E-commerce purchases carry the same protection."
        ),
        "citations": [
            {"title": "Consumer Protection Act, 2019", "source": "Right to seek redress"},
            {"title": "Consumer Protection (E-Commerce) Rules, 2020", "source": "Marketplace liability"},
        ],
        "next_steps": [
            "Contact the seller in writing and keep proof of purchase.",
            "If unresolved, file a complaint on the e-Daakhil portal.",
        ],
    },
]

_DEFAULT_ANSWER = {
    "title": "Here's what generally applies",
    "body": (
        "This demo's curated answer bank doesn't cover that question yet. "
        "Once Indian Kanoon retrieval and the language model are "
        "connected, this will pull the relevant statute and judgments "
        "directly instead of falling back to a canned response."
    ),
    "citations": [],
    "next_steps": [
        "Try one of the sample questions to see a fully worked example.",
        "Connect INDIAN_KANOON_API_TOKEN and GEMINI_API_KEY to answer live questions.",
    ],
}


def _match_fallback(question: str) -> Dict:
    q = question.lower()
    for entry in _FALLBACK_BANK:
        if any(kw in q for kw in entry["keywords"]):
            return entry
    return _DEFAULT_ANSWER


async def run_live_pipeline(question: str, state: str | None) -> AskResponse | None:
    """Retrieval Agent -> Synthesis Agent -> Internal Validator.

    Returns None (so the caller can fall back to the demo bank) if the
    Kanoon token or Gemini key isn't configured, or if retrieval comes
    back empty. Replace the synthesis/validator section with real calls
    to Gemini once GEMINI_API_KEY is set - the retrieval half already
    works against the live Indian Kanoon API.
    """
    if not settings.indian_kanoon_api_token:
        return None

    search_query = f"{question} {state}" if state else question
    results = await kanoon.search(search_query)
    if not results:
        return None

    # TODO: send `question` + top `results` snippets to Gemini for the
    # synthesis step, then run the internal validator agent to confirm
    # the answer is actually supported by the retrieved text before
    # returning it. Left as a stub so the endpoint is easy to complete
    # once GEMINI_API_KEY is available.
    top = results[:3]
    citations = [
        Citation(title=r["title"] or "Untitled", source=r.get("court") or "Indian Kanoon", docid=r["docid"], url=r["url"])
        for r in top
    ]
    return AskResponse(
        title="Retrieved from Indian Kanoon",
        body=(
            "Live retrieval found matching material, but the synthesis "
            "step (Gemini) is not wired up in this build yet - add "
            "GEMINI_API_KEY and complete run_live_pipeline() in "
            "app/reasoning.py to turn these into a plain-language answer."
        ),
        citations=citations,
        next_steps=["Connect the synthesis + validator agents to finish this pipeline."],
    )


async def answer_question(question: str, state: str | None = None) -> AskResponse:
    live = await run_live_pipeline(question, state)
    if live is not None:
        return live

    match = _match_fallback(question)
    return AskResponse(
        title=match["title"],
        body=match["body"],
        citations=[Citation(**c) for c in match["citations"]],
        next_steps=match["next_steps"],
    )
