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

# Act-level status values. These are a different axis from the section-level
# ones above and the distinction matters: a section can be amended inside an
# act that is perfectly live, and a section can be untouched inside an act
# that has been repealed wholesale.
#
#   live       in force, consolidated text
#   repealed   no longer in force; `successor_act_key` says what replaced it
#   amending   an amendment act - modifies another act, never stands alone.
#              Nothing with this status should ever be in the corpus; the
#              value exists so the ingestion filter can name what it dropped.
#   successor  in force, and it is what replaced a repealed act. Practically
#              identical to `live`; the distinction is what lets us answer
#              "CrPC 154 is now BNSS 173" instead of just "CrPC is repealed".
ACT_LIVE = "live"
ACT_REPEALED = "repealed"
ACT_AMENDING = "amending"
ACT_SUCCESSOR = "successor"
ACT_STATUSES = (ACT_LIVE, ACT_REPEALED, ACT_AMENDING, ACT_SUCCESSOR)


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


@lru_cache(maxsize=1)
def _act_map() -> Dict[str, Dict[str, Any]]:
    """Act-level status, from the `_acts` block of repeal_map.json.

    Kept in the same file as the section map on purpose. Act status and
    section successors are one dataset - splitting them across two files is
    how they drift apart, and a drifted repeal map is the failure mode this
    whole layer exists to prevent.
    """
    if not REPEAL_MAP.exists():
        return {}
    try:
        raw = json.loads(REPEAL_MAP.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read %s: %s", REPEAL_MAP, exc)
        return {}
    acts = raw.get("_acts") or {}
    return acts if isinstance(acts, dict) else {}


def act_status(act_key: str) -> Dict[str, Any]:
    """Act-level status block for one act key.

    Falls back to `live` rather than raising, because an act present in the
    corpus but absent from the map is a gap in the map, not a reason to fail
    a user's query. The `known` flag makes the gap visible to callers and to
    `python -m app.validity --audit`.
    """
    rec = _act_map().get(act_key)
    if not rec:
        return {"act_key": act_key, "status": ACT_LIVE, "known": False,
                "verified": False}
    out = dict(rec)
    out["act_key"] = act_key
    out["known"] = True
    out.setdefault("status", ACT_LIVE)
    out.setdefault("verified", False)
    return out


def successor_act(act_key: str) -> Optional[Dict[str, Any]]:
    """The act that replaced this one, if any. `crpc` -> the BNSS block."""
    rec = act_status(act_key)
    nxt = rec.get("successor_act_key")
    return act_status(nxt) if nxt else None


def is_amending(act_key: str) -> bool:
    """True for an amendment act. The ingestion filter refuses these."""
    return act_status(act_key).get("status") == ACT_AMENDING


def audit(act_keys) -> Dict[str, Any]:
    """Which acts in the corpus have no entry in the act map.

    Run after adding acts. An act missing here is silently treated as live,
    which is exactly the kind of quiet wrong answer this system is meant to
    avoid.
    """
    missing = [k for k in act_keys if not act_status(k)["known"]]
    unresolved = [
        k for k in act_keys
        if act_status(k).get("status") == ACT_REPEALED
        and not act_status(k).get("successor_act_key")
    ]
    return {"total": len(list(act_keys)), "missing_from_act_map": missing,
            "repealed_without_successor": unresolved}


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
    act = act_status(act_key)

    if doc.get("current", True):
        # A live act and a successor act are both "in force". The label
        # differs only so the UI can say what a successor act replaced,
        # which is the thing a user asking about old law actually needs.
        note = ""
        if act.get("status") == ACT_SUCCESSOR and act.get("supersedes_act_key"):
            prior = act_status(act["supersedes_act_key"])
            if prior.get("known"):
                note = (
                    f"In force since 1 July 2024. This act replaced the "
                    f"{prior.get('act', prior['act_key'])}."
                )
        return {
            "status": GOOD_LAW,
            "label": "In force",
            "note": note,
            "successor": None,
            "verified": True,
            "act_status": act.get("status", ACT_LIVE),
            "act_status_known": act.get("known", False),
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
            "act_status": act.get("status", ACT_REPEALED),
            "act_status_known": act.get("known", False),
        }

    # No section-level mapping. Fall back to the act-level one, which is a
    # weaker but still useful answer: we cannot say which section replaced
    # this one, but we can say which act did.
    succ_act = successor_act(act_key)
    if succ_act and succ_act.get("known"):
        note = (
            f"{note} The act that replaced it is the "
            f"{succ_act.get('act', succ_act['act_key'])}, but we do not have a "
            f"section-level mapping for this provision - find the "
            f"corresponding section there before relying on it."
        )

    return {
        "status": REPEALED,
        "label": "Repealed",
        "note": note,
        "successor": None,
        "verified": True,
        "act_status": act.get("status", ACT_REPEALED),
        "act_status_known": act.get("known", False),
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

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Validity layer checks.")
    ap.add_argument("--audit", action="store_true",
                    help="report corpus acts missing from the act-level map")
    args = ap.parse_args()

    if not args.audit:
        ap.print_help()
        return

    from . import statutes

    keys = sorted({d["act_key"] for d in statutes._index()["docs"]})
    rep = audit(keys)
    print(f"{rep['total']} acts in the corpus\n")

    by_status: Dict[str, List[str]] = {}
    for k in keys:
        by_status.setdefault(act_status(k).get("status", "?"), []).append(k)
    for st in sorted(by_status):
        print(f"  {st:<10} {len(by_status[st]):>3}  {', '.join(sorted(by_status[st]))}")

    print()
    if rep["missing_from_act_map"]:
        print("  MISSING from repeal_map._acts (treated as live, which may be wrong):")
        for k in rep["missing_from_act_map"]:
            print(f"    - {k}")
    else:
        print("  Every corpus act has an act-level status entry.")

    if rep["repealed_without_successor"]:
        print("\n  Repealed with no successor_act_key:")
        for k in rep["repealed_without_successor"]:
            print(f"    - {k}")


if __name__ == "__main__":
    _cli()