"""Retrieval spot-check and eval-miss listing.

    python -m app.check_retrieval           # the validation queries
    python -m app.check_retrieval --misses  # which eval questions fail, and why

The --misses mode is the useful one after a corpus change. `dense --calibrate`
tells you how many questions fail; this tells you WHICH, what came back
instead, and whether the fallback would even have fired - which is what
decides whether a miss is a threshold problem or a retrieval problem.

Throwaway diagnostic. Delete when you are done with it.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from . import statutes, validity

QUERIES = [
    "What is POCSO?",
    "What is the POCSO Act?",
    "What does the Dowry Prohibition Act say?",
    "What is domestic violence under Indian law?",
    "What is the SC/ST Atrocities Act?",
    "What does the Hindu Succession Act say?",
    "What is the Indian Succession Act?",
    # title-word and colloquial queries - nothing matched these before the
    # five new acts landed, so they are the real test of this batch
    "POCSO 4",
    "aggravated sexual assault punishment",
    "residence order shared household",
    "casteist slur in public view",
    "daughter share in ancestral property",
    "dowry demand penalty",
    "protection officer domestic incident report",
    "who inherits when a Hindu man dies without a will",
    "my child was abused by a teacher",
    "thrown out of my husband's house",
]


def spot_check() -> None:
    print(f"corpus: {len(statutes._index()['docs']):,} sections across "
          f"{len({d['act_key'] for d in statutes._index()['docs']})} acts\n")
    for q in QUERIES:
        ov = statutes.act_overview(q)
        if ov:
            print(f"{q:<48} OVERVIEW  {ov['title'][:46]}")
            continue
        hits = statutes.search(q, limit=3)
        shown = ", ".join(f"{h['act_short']} {h['section']}" for h in hits)
        flags = ""
        if any(not h.get("current", True) for h in hits):
            flags = "  [includes repealed]"
        print(f"{q:<48} {shown}{flags}")


def misses() -> None:
    """List failing eval questions with the fallback decision for each."""
    from . import dense

    path = Path(__file__).resolve().parent.parent / "data" / "eval_questions.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    docs = statutes._index()["docs"]

    bad, threshold_fixable = [], 0
    for case in cases:
        q = case["q"]
        got = [f"{h['act_key']}:{h['section']}"
               for h in statutes.search(q, limit=3)]
        if any(e in got for e in case["expect"]):
            continue
        terms = statutes._expand(q)
        scored = statutes._bm25_scored(q, docs, 3)
        top_score = scored[0][0] if scored else 0.0
        cov = dense.coverage(terms, scored[0][1]) if scored else 0.0
        would_fire = (top_score < dense.MIN_SCORE) or (cov < dense.MIN_COVERAGE)
        threshold_fixable += would_fire
        # Is the expected act even in the corpus? A question expecting a
        # section from an act we never ingested is not a retrieval failure.
        expect_acts = {e.split(":")[0] for e in case["expect"]}
        have_acts = {d["act_key"] for d in docs}
        absent = sorted(expect_acts - have_acts)
        bad.append((q, case["expect"], got, top_score, cov, would_fire, absent))

    print(f"{len(cases)} questions, {len(bad)} missed @3\n")
    print(f"  fallback would fire on {threshold_fixable} of {len(bad)} misses "
          f"(score<{dense.MIN_SCORE}, coverage<{dense.MIN_COVERAGE})")
    no_act = [b for b in bad if b[6]]
    if no_act:
        print(f"  {len(no_act)} miss(es) expect an act that is NOT in the corpus - "
              f"not a retrieval failure")
    print()

    for q, exp, got, sc, cov, fire, absent in bad:
        print(f"  Q  {q[:78]}")
        print(f"     expect {exp}   got {got or '[]'}")
        tag = "fallback fires" if fire else "BM25 confident but WRONG"
        print(f"     score {sc:5.1f}  coverage {cov:.2f}  -> {tag}")
        if absent:
            print(f"     act(s) not in corpus: {', '.join(absent)}")
        print()

    confident_wrong = [b for b in bad if not b[5] and not b[6]]
    print(f"  {len(confident_wrong)} miss(es) are BM25 scoring a wrong section "
          f"highly - no threshold change fixes these.")
    print("  Those need a _CONCEPTS entry, a COMPANIONS pair, or a better title.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--misses", action="store_true",
                    help="list failing eval questions instead of spot-checking")
    args = ap.parse_args()
    logging.disable(logging.INFO)
    misses() if args.misses else spot_check()


if __name__ == "__main__":
    main()