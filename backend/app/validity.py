"""Legal validity and answer grounding.

Two things a general-purpose model cannot do, and the reason this system is
worth more than a prompt:

1. Know whether a provision is still law. An LLM will cite IPC 302 with total
   confidence in 2026, two years after it was repealed. We know, because the
   status is data on the chunk, not an opinion in the prompt.

2. Say how well an answer is actually supported. Not a number the model made
   up about itself - a band derived from what retrieval found, what the answer
   cited, and whether any of it is repealed.

Both are checkable by `python -m app.eval_validity`, which is the point: a
claim about legal safety should be measurable, not asserted.
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPEAL_MAP = DATA_DIR / "repeal_map.json"

# Section-level status values, in order of how loudly they need saying.
GOOD_LAW = "good_law"
AMENDED = "amended"
REPEALED = "repealed"


# ---------------------------------------------------------------------------
# Repeal / supersession
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _repeal_map() -> Dict[str, Dict[str, Any]]:
    """Section-level successor mapping, e.g. ipc:302 -> bns:103.

    Ships mostly empty on purpose. A wrong mapping is worse than none - it
    would send an advocate to the wrong provision with a confident badge on
    it - so entries carry a `verified` flag and unverified ones are shown
    with a caveat rather than as settled fact.
    """
    if not REPEAL_MAP.exists():
        return {}
    try:
        raw = json.loads(REPEAL_MAP.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read %s: %s", REPEAL_MAP, exc)
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# A successor can name a subsection - IPC 34 became BNS 3(5). The index only
# holds whole sections, so "3(5)" would never resolve to a document. Keep the
# precise reference for display, strip it for lookup.
_SUBSECTION_RE = re.compile(r"\s*\([^)]*\)\s*$")


def base_section(section: str) -> str:
    """'3(5)' -> '3'. The indexable part of a section reference."""
    return _SUBSECTION_RE.sub("", str(section or "").strip())


def successor(act_key: str, section: str) -> Optional[Dict[str, Any]]:
    """Where a repealed section went, if we know."""
    return _repeal_map().get(f"{act_key}:{str(section).strip().upper()}")


def status_for(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Legal status of one retrieved chunk.

    `doc` is a statute chunk from statutes.py. Act-level repeal comes from the
    ACTS registry, which is curated; section-level successors come from the
    map above, which may not be.
    """
    act_key = doc.get("act_key", "")
    section = str(doc.get("section", ""))

    if doc.get("current", True):
        return {
            "status": GOOD_LAW,
            "label": "In force",
            "note": "",
            "successor": None,
            "verified": True,
        }

    succ = successor(act_key, section)
    note = (doc.get("superseded_by") or "This act has been repealed.").rstrip(". ")
    note = f"Repealed by the {note}." if not note.lower().startswith(
        ("repealed", "this act")) else f"{note}."

    if succ:
        target = f"{succ.get('act_short', succ.get('act', ''))} {succ.get('section', '')}".strip()
        verified = bool(succ.get("verified"))
        return {
            "status": REPEALED,
            "label": "Repealed",
            "note": (
                f"{note} The corresponding provision is {target}."
                if verified
                else f"{note} This appears to correspond to {target}, "
                     f"but that mapping has not been verified - check it "
                     f"against the bare act before relying on it."
            ),
            "successor": succ,
            "verified": verified,
        }

    return {
        "status": REPEALED,
        "label": "Repealed",
        "note": note,
        "successor": None,
        "verified": True,
    }


def annotate(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach a status block to each chunk, in place."""
    for d in docs:
        if "act_key" in d:
            d["validity"] = status_for(d)
    return docs


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------
# Deliberately NOT a percentage, and deliberately not asked of the model.
# A model rating its own confidence gives 85% to a hallucinated citation.
# These bands come from things that can be counted.

WELL_GROUNDED = "well_grounded"
PARTLY_GROUNDED = "partly_grounded"
THIN = "thin"

_MARKER_RE = re.compile(r"\[(\d{1,2})\]")


def assess(
    body: str,
    sources: List[Dict[str, Any]],
    citations: List[Dict[str, Any]],
    used_sources: Optional[List[int]] = None,
    top_score: float = 0.0,
    score_gap: float = 0.0,
) -> Dict[str, Any]:
    """How well is this answer actually supported?

    Signals, all measurable:
      - did retrieval return anything at all
      - is there a clear winner, or a flat spread of weak matches
      - does the prose actually cite the sources it was given
      - is any cited provision repealed
      - is a statute involved, or only case law
    """
    reasons: List[str] = []

    n_sources = len(sources or [])

    # The synthesis agent reports which sources it used as a separate field,
    # not as [N] markers in the prose. Counting markers in the body found
    # nothing and marked every correct answer as ungrounded - the field is
    # the real signal. The regex stays as a fallback for callers that don't
    # have it.
    if used_sources is not None:
        n_cited = len({n for n in used_sources if 1 <= n <= n_sources})
    else:
        n_cited = len(set(_MARKER_RE.findall(body or "")))

    # Act overviews carry no act_key - they aren't a single section - but they
    # are still statute, so they count.
    # When the agent named its sources, judge on those. Retrieval commonly
    # returns three provisions and the answer relies on one; flagging the
    # other two as unused noise would be wrong.
    if used_sources:
        considered = [
            sources[i - 1] for i in used_sources if 1 <= i <= n_sources
        ] or list(sources or [])
    else:
        considered = list(sources or [])

    statutes_used = [
        s for s in considered
        if s.get("act_key") or s.get("kind") == "overview"
    ]
    repealed = [
        s for s in statutes_used
        if (s.get("validity") or {}).get("status") == REPEALED
    ]

    # --- the disqualifying cases first -----------------------------------
    if n_sources == 0:
        return {
            "level": THIN,
            "label": "Not grounded",
            "reasons": ["No statute or judgment was retrieved for this question."],
            "sources": 0,
            "cited": 0,
            "repealed": 0,
        }

    # With a single source there is nothing for a [1] marker to disambiguate,
    # and the act-overview path deliberately writes plain prose. Requiring
    # markers there marked correct, well-sourced answers as ungrounded.
    needs_markers = n_sources > 1 or used_sources is not None

    if n_cited == 0 and needs_markers:
        reasons.append(
            "The answer does not point to any of the retrieved sources, so it "
            "rests on general reasoning rather than a provision."
        )

    if not statutes_used:
        reasons.append(
            "No bare-act provision was retrieved - the answer rests on case "
            "law alone. The governing act may not be in the index."
        )

    if repealed:
        names = ", ".join(
            f"{s.get('act_short', s.get('act', ''))} {s.get('section', '')}".strip()
            for s in repealed[:3]
        )
        reasons.append(f"Cites repealed law: {names}. Check the successor provision.")

    if top_score and score_gap is not None and top_score > 0 and score_gap < 0.15:
        reasons.append(
            "Several provisions matched about equally well, so the most "
            "relevant one may not be first."
        )

    # --- band -------------------------------------------------------------
    if (n_cited == 0 and needs_markers) or not statutes_used:
        level, label = THIN, "Lightly grounded"
    elif repealed or reasons:
        level, label = PARTLY_GROUNDED, "Partly grounded"
    else:
        level, label = WELL_GROUNDED, "Well grounded"
        reasons.append(
            f"Every point traces to {n_cited} of {n_sources} retrieved "
            f"source{'s' if n_sources != 1 else ''}, all in force."
        )

    return {
        "level": level,
        "label": label,
        "reasons": reasons,
        "sources": n_sources,
        "cited": n_cited,
        "repealed": len(repealed),
    }