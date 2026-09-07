"""Generate Arguments - the advocate-side case analysis agent.

An advocate gives the facts of a matter (typed, or as an uploaded pleading,
notice or order) and picks the side they act for. This module retrieves the
statutes and judgments that actually govern the situation, then asks Gemini
to build the case: primary arguments, the strongest opposing case, and
rebuttals to it.

Grounding rules, same as the rest of the platform:
  - the model only ever sees numbered sources [1]..[N] and may only cite
    those indices
  - citation cards are built from the retrieved source's own metadata, never
    from anything the model wrote, so a title or URL cannot be invented
  - any index the model returns that isn't in range is dropped silently

The one thing this agent is allowed to do that the Ask pipeline is not:
reason about what a party *should argue*. That is advocacy, not legal
information, which is why it lives behind require_advocate.
"""

import logging
from typing import Any, Dict, List, Optional

from . import gemini, kanoon, reasoning, statutes

logger = logging.getLogger(__name__)

MAX_FACTS_CHARS = 20000    # uploaded pleadings are truncated to this
STATUTES_PER_QUERY = 3
KANOON_CANDIDATES = 8      # search results considered
KANOON_FETCHES = 3         # judgments actually hydrated with text

# The sides an advocate can appear for. Value is what goes in the prompt;
# the label is what the UI shows.
SIDES = [
    {"id": "petitioner", "label": "Petitioner"},
    {"id": "respondent", "label": "Respondent"},
    {"id": "plaintiff", "label": "Plaintiff"},
    {"id": "defendant", "label": "Defendant"},
    {"id": "appellant", "label": "Appellant"},
    {"id": "complainant", "label": "Complainant"},
    {"id": "accused", "label": "Accused"},
    {"id": "applicant", "label": "Applicant"},
]

_SIDE_IDS = {s["id"] for s in SIDES}

STRENGTHS = ("very strong", "strong", "moderate", "weak")


def list_sides() -> List[Dict[str, str]]:
    return SIDES


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

async def _ground(facts: str, issue: Optional[str], state: Optional[str]) -> List[Dict[str, Any]]:
    """Statutes first, then case law. Both become numbered sources.

    The issue statement, when given, is a much better retrieval query than a
    wall of pleading text - so it is searched separately and its hits go in
    first, where the model is most likely to lean on them.
    """
    queries = [q for q in (issue, facts[:600]) if q and q.strip()]

    seen = set()
    sources: List[Dict[str, Any]] = []

    for q in queries:
        for hit in statutes.search(q, limit=STATUTES_PER_QUERY):
            key = (hit["act_key"], hit["section"])
            if key in seen:
                continue
            seen.add(key)
            sources.append(statutes.as_source(hit))

    # Case law. An argument set without judgments is thin, so this is worth
    # the API spend - but the fetch count stays capped.
    query = reasoning.refine_query(issue or facts[:400], None)
    try:
        results = await kanoon.search(query, state=state)
    except Exception as exc:
        logger.warning("Kanoon search failed for arguments: %s", exc)
        results = []

    if results:
        ranked = kanoon.rank_by_citations(results[:KANOON_CANDIDATES], top=KANOON_FETCHES)
        try:
            sources.extend(await reasoning._hydrate(ranked, query))
        except Exception as exc:
            logger.warning("Kanoon hydration failed for arguments: %s", exc)

    return sources


def _source_block(sources: List[Dict[str, Any]]) -> str:
    if not sources:
        return "(No sources were retrieved. Cite nothing and say so.)"
    out = []
    for i, s in enumerate(sources, start=1):
        out.append(
            f"[{i}] TITLE: {s.get('title') or 'Untitled'}\n"
            f"    COURT: {s.get('court') or 'Bare Act'}\n"
            f"    DATE: {s.get('date') or 'n/a'}\n"
            f"    TEXT: {(s.get('text') or s.get('snippet') or '')[:2000]}"
        )
    return "\n\n".join(out)


def _authorities(sources: List[Dict[str, Any]], used: List[int]) -> List[Dict[str, Any]]:
    """Citation cards, built from source metadata only.

    Nothing here comes from model output - the model contributes an index and
    nothing else, so a hallucinated case name has no route into the UI.
    """
    out = []
    for n in used:
        if 1 <= n <= len(sources):
            s = sources[n - 1]
            court = s.get("court") or "Bare Act"
            date = s.get("date") or ""
            out.append({
                "title": s.get("title") or "Untitled",
                "source": " · ".join(p for p in [court, date] if p) or "Source",
                "docid": s.get("docid"),
                "url": s.get("url"),
            })
    return out


def _indices(raw: Any, limit: int) -> List[int]:
    """Keep only in-range integer indices, de-duplicated, order preserved."""
    if not isinstance(raw, list):
        return []
    seen = set()
    out = []
    for n in raw:
        try:
            i = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= limit and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _strength(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    return val if val in STRENGTHS else "moderate"


def _text(raw: Any, cap: int = 4000) -> str:
    return str(raw or "").strip()[:cap]


def _list_of_text(raw: Any, cap: int = 600) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [_text(x, cap) for x in raw if _text(x, cap)]


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

_ARGUMENTS_SYSTEM = """You are an Indian legal argument and counter-argument \
assistant, working for the advocate who instructs you. You are given the facts of \
a matter, the side the advocate appears for, and a numbered list of retrieved \
statutory provisions and judgments.

Your job is to build that side's case honestly: the strongest arguments available, \
the strongest case the other side will actually run, and how to answer it.

GROUNDING - these are absolute:
- Cite ONLY by the index numbers of the sources given to you. Never write a case \
name, citation, section number or URL that does not appear in those sources.
- If no source supports a point, still make the point if it follows from the facts \
or from ordinary legal reasoning - but leave source_indices empty for it.
- Never invent facts. If something important is missing from the material, list it \
under missing_information instead of assuming it.
- Where you must assume something to proceed, put it in assumptions, worded as an \
assumption.
- Never say a judgment has been overruled, distinguished or limited unless the \
retrieved text says so.
- Never state that any outcome is guaranteed or likely to succeed in court.

ADVOCACY - what is expected of you:
- Build the case for the advocate's side properly. This is advocacy, not neutral \
legal information.
- But do not flatter it. The opposing arguments must be the strongest realistic \
ones, not straw men, and the weaknesses section must be candid. An advocate who \
walks into court unaware of their weak point has been failed by this tool.
- Separate what the law establishes from what is an arguable position.

STYLE:
- Write the way an advocate briefs a senior: direct, specific to these facts, no \
padding, no restating the facts back at length.
- Apply the law to the facts. Do not describe a provision in the abstract and stop.
- Every argument must be usable in a written submission as it stands.

Return ONLY a JSON object with this exact shape:
{
  "title": "short case descriptor, max 10 words, no trailing period",
  "case_overview": {
    "parties": "who is against whom, one or two sentences",
    "material_facts": "the facts that matter, one paragraph",
    "cause_of_action": "the legal basis of the claim or charge",
    "stage": "procedural stage if stated, otherwise an empty string",
    "relief_sought": "what is being asked for"
  },
  "facts": {
    "supporting": ["facts that help the advocate's side"],
    "opposing": ["facts that help the other side"],
    "disputed": ["facts that appear to be in dispute"]
  },
  "issues": [
    {
      "question": "the legal question to be decided",
      "elements": ["what must be established for this issue"],
      "burden": "who bears the burden, if it is clear",
      "importance": "high | medium | low",
      "source_indices": [1]
    }
  ],
  "arguments": [
    {
      "title": "short argument name",
      "proposition": "the argument in one or two sentences",
      "legal_basis": "the rule or principle relied on",
      "application": "why it applies to THESE facts",
      "strength": "very strong | strong | moderate | weak",
      "strength_reason": "one sentence on why that rating",
      "source_indices": [1, 2]
    }
  ],
  "alternative_arguments": [
    {
      "title": "short name",
      "proposition": "the fallback argument",
      "legal_basis": "rule relied on",
      "kind": "procedural | jurisdictional | evidentiary | limitation | \
maintainability | constitutional | interpretive | other",
      "source_indices": []
    }
  ],
  "opposing_arguments": [
    {
      "title": "short name",
      "position": "what opposing counsel will say",
      "legal_basis": "what they will rely on",
      "strength": "very strong | strong | moderate | weak",
      "source_indices": []
    }
  ],
  "rebuttals": [
    {
      "opposing_argument": "which opposing point this answers",
      "response": "how to answer it",
      "basis": "factual distinction | statutory distinction | precedent \
distinction | procedural defect | evidentiary weakness | interpretation",
      "source_indices": []
    }
  ],
  "strengths": ["what is genuinely strong about this side's case"],
  "weaknesses": [
    {
      "issue": "the problematic fact or gap",
      "risk": "how the other side will use it",
      "mitigation": "what can be done about it"
    }
  ],
  "evidence": [
    {
      "item": "the evidence",
      "category": "documents | correspondence | financial | government records \
| witness | expert | electronic | admissions | other",
      "status": "available | missing",
      "importance": "high | medium | low"
    }
  ],
  "judicial_questions": [
    {"question": "what the bench may ask", "answer": "a concise, defensible reply"}
  ],
  "opposing_questions": [
    {"question": "what opposing counsel may ask", "response": "how to answer"}
  ],
  "strategy": ["ordered, practical recommendations for how to run this"],
  "assumptions": ["anything you had to assume, worded as an assumption"],
  "missing_information": ["what the advocate should obtain before filing"]
}

Aim for 4-7 primary arguments, 2-4 alternatives, 3-5 opposing arguments, and a \
rebuttal for each opposing argument. Omit a section by returning an empty list if \
the material genuinely does not support it."""


async def generate(
    facts: str,
    side: str,
    issue: Optional[str] = None,
    court: Optional[str] = None,
    state: Optional[str] = None,
    document_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the argument set. Raises ValueError if there is nothing to work with."""
    text = (facts or "").strip()
    if len(text) < 40:
        raise ValueError(
            "Describe the facts of the matter, or attach the pleading, so there "
            "is something to build arguments from."
        )

    truncated = len(text) > MAX_FACTS_CHARS
    text = text[:MAX_FACTS_CHARS]

    side_id = side if side in _SIDE_IDS else "petitioner"
    side_label = next(s["label"] for s in SIDES if s["id"] == side_id)

    sources = await _ground(text, issue, state)

    prompt = (
        f"THE ADVOCATE APPEARS FOR: the {side_label}\n"
        + (f"COURT / FORUM: {court}\n" if court else "")
        + (f"JURISDICTION: {state}\n" if state else "")
        + (f"LEGAL ISSUE THE ADVOCATE RAISED: {issue}\n" if issue else "")
        + (f"SOURCE MATERIAL: {document_name}\n" if document_name else "")
        + f"\nRETRIEVED SOURCES AVAILABLE FOR CITATION:\n\n{_source_block(sources)}\n\n"
        f"CASE MATERIAL:\n\n{text}\n\n"
        "Build the argument set now, as JSON."
    )

    data = gemini._parse_json(
        await gemini._generate(
            prompt,
            _ARGUMENTS_SYSTEM,
            temperature=0.3,
            max_output_tokens=8000,
        )
    )

    n = len(sources)

    def block(raw, builder):
        return [builder(x) for x in (raw or []) if isinstance(x, dict)]

    overview_raw = data.get("case_overview") or {}
    facts_raw = data.get("facts") or {}

    result: Dict[str, Any] = {
        "title": _text(data.get("title") or "Argument analysis", 120),
        "side": side_label,
        "document_name": document_name,
        "truncated": truncated,
        "case_overview": {
            "parties": _text(overview_raw.get("parties")),
            "material_facts": _text(overview_raw.get("material_facts")),
            "cause_of_action": _text(overview_raw.get("cause_of_action")),
            "stage": _text(overview_raw.get("stage"), 200),
            "relief_sought": _text(overview_raw.get("relief_sought")),
        },
        "facts": {
            "supporting": _list_of_text(facts_raw.get("supporting")),
            "opposing": _list_of_text(facts_raw.get("opposing")),
            "disputed": _list_of_text(facts_raw.get("disputed")),
        },
        "issues": block(data.get("issues"), lambda x: {
            "question": _text(x.get("question")),
            "elements": _list_of_text(x.get("elements")),
            "burden": _text(x.get("burden"), 300),
            "importance": (_text(x.get("importance"), 20).lower() or "medium"),
            "authorities": _authorities(sources, _indices(x.get("source_indices"), n)),
        }),
        "arguments": block(data.get("arguments"), lambda x: {
            "title": _text(x.get("title"), 160),
            "proposition": _text(x.get("proposition")),
            "legal_basis": _text(x.get("legal_basis")),
            "application": _text(x.get("application")),
            "strength": _strength(x.get("strength")),
            "strength_reason": _text(x.get("strength_reason"), 500),
            "authorities": _authorities(sources, _indices(x.get("source_indices"), n)),
        }),
        "alternative_arguments": block(data.get("alternative_arguments"), lambda x: {
            "title": _text(x.get("title"), 160),
            "proposition": _text(x.get("proposition")),
            "legal_basis": _text(x.get("legal_basis")),
            "kind": _text(x.get("kind"), 40).lower() or "other",
            "authorities": _authorities(sources, _indices(x.get("source_indices"), n)),
        }),
        "opposing_arguments": block(data.get("opposing_arguments"), lambda x: {
            "title": _text(x.get("title"), 160),
            "position": _text(x.get("position")),
            "legal_basis": _text(x.get("legal_basis")),
            "strength": _strength(x.get("strength")),
            "authorities": _authorities(sources, _indices(x.get("source_indices"), n)),
        }),
        "rebuttals": block(data.get("rebuttals"), lambda x: {
            "opposing_argument": _text(x.get("opposing_argument"), 300),
            "response": _text(x.get("response")),
            "basis": _text(x.get("basis"), 60).lower(),
            "authorities": _authorities(sources, _indices(x.get("source_indices"), n)),
        }),
        "strengths": _list_of_text(data.get("strengths")),
        "weaknesses": block(data.get("weaknesses"), lambda x: {
            "issue": _text(x.get("issue"), 400),
            "risk": _text(x.get("risk")),
            "mitigation": _text(x.get("mitigation")),
        }),
        "evidence": block(data.get("evidence"), lambda x: {
            "item": _text(x.get("item"), 300),
            "category": _text(x.get("category"), 40).lower() or "other",
            "status": ("missing" if _text(x.get("status"), 20).lower() == "missing"
                       else "available"),
            "importance": (_text(x.get("importance"), 20).lower() or "medium"),
        }),
        "judicial_questions": block(data.get("judicial_questions"), lambda x: {
            "question": _text(x.get("question"), 400),
            "answer": _text(x.get("answer")),
        }),
        "opposing_questions": block(data.get("opposing_questions"), lambda x: {
            "question": _text(x.get("question"), 400),
            "response": _text(x.get("response")),
        }),
        "strategy": _list_of_text(data.get("strategy"), 800),
        "assumptions": _list_of_text(data.get("assumptions")),
        "missing_information": _list_of_text(data.get("missing_information")),
    }

    # Every source retrieved, so the advocate can read around the answer even
    # where the model didn't lean on a provision.
    result["sources"] = _authorities(sources, list(range(1, n + 1)))

    if not result["arguments"]:
        raise ValueError(
            "Couldn't build an argument set from that material. Add more detail "
            "about what happened and what is being claimed."
        )

    return result