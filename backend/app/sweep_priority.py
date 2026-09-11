"""Sweep the ranking knobs now that procedural codes are in the corpus.

    python -m app.sweep_priority                 # the grid
    python -m app.sweep_priority --stale          # eval questions to update
    python -m app.sweep_priority --apply 0.06 2   # print the edits to make

THE PROBLEM THIS MEASURES

Adding the BNSS (531 sections) and BSA (170) put a lot of procedural text in
a corpus that was mostly substantive. Procedure shares vocabulary with
everything - "police", "arrest", "Court", "child", "taken", "information" -
so on a question like "my phone was taken from my bag" the BNSS can outscore
BNS 303 (theft) despite theft being the actual answer.

Two knobs decide this, and they only work together:

  ACTS[key]["priority"]   per-act rank, lower sorts first
  PRIORITY_STEP           how much each step down that ladder costs

PRIORITY_STEP is currently 0.0, which means the priority ladder does nothing
at all - it was measured as better at 0 back when the corpus was substantive
law plus the Constitution. That measurement is now stale: there was no
procedural code to demote then.

bnss and bsa were given priority 0 (same as the BNS) when they were added,
which was a mistake on the same axis - it says "a procedural section is as
likely to be the answer as the offence provision", and it is not.

WHAT IT DOES NOT DO

It changes nothing. It prints a grid so you can see the trade-off, and
--apply prints the two edits to make by hand. Ranking weights are the kind of
thing that should never be silently rewritten by a script.

Throwaway. Delete once the numbers are settled.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from . import statutes, validity

EVAL = Path(__file__).resolve().parent.parent / "data" / "eval_questions.json"

# Acts whose sections are procedure or evidence rather than substantive
# rights and offences. These are the candidates for demotion.
PROCEDURAL = ("bnss", "bsa", "crpc", "iea", "cpc")


def _cases() -> List[dict]:
    return json.loads(EVAL.read_text(encoding="utf-8"))["cases"]


def score(cases: List[dict], limit: int = 3) -> Tuple[int, int, float, List[dict]]:
    statutes._index.cache_clear()
    r3 = r1 = 0
    mrr = 0.0
    missed = []
    for c in cases:
        got = [f"{h['act_key']}:{h['section']}" for h in statutes.search(c["q"], limit)]
        hits = [i for i, g in enumerate(got) if g in set(c["expect"])]
        if hits:
            r3 += 1
            mrr += 1.0 / (hits[0] + 1)
            if hits[0] == 0:
                r1 += 1
        else:
            missed.append({"id": c["id"], "q": c["q"], "expect": c["expect"],
                           "got": got})
    return r3, r1, mrr / max(len(cases), 1), missed


def sweep(cases: List[dict]) -> None:
    orig_step = statutes.PRIORITY_STEP
    orig_pri = {k: statutes.ACTS[k].get("priority", 1) for k in statutes.ACTS}

    print(f"\n{'-' * 74}\nRANKING SWEEP\n{'-' * 74}")
    base_r3, base_r1, base_mrr, _ = score(cases)
    print(f"  current   PRIORITY_STEP={orig_step}  procedural priority="
          f"{orig_pri.get('bnss')}   R@3={base_r3}/{len(cases)}  "
          f"R@1={base_r1}  MRR={base_mrr:.3f}\n")

    print(f"  {'STEP':>6}{'proc_pri':>10}{'R@3':>7}{'R@1':>7}{'MRR':>8}   delta")
    best = (base_r3, base_mrr, orig_step, orig_pri.get("bnss", 0))
    for step in (0.0, 0.03, 0.06, 0.10, 0.15, 0.20):
        for pri in (0, 1, 2, 3):
            statutes.PRIORITY_STEP = step
            for k in PROCEDURAL:
                if k in statutes.ACTS:
                    statutes.ACTS[k]["priority"] = pri
            r3, r1, mrr, _ = score(cases)
            d = r3 - base_r3
            flag = ""
            if (r3, mrr) > (best[0], best[1]):
                best = (r3, mrr, step, pri)
                flag = "  <- best so far"
            print(f"  {step:>6}{pri:>10}{r3:>7}{r1:>7}{mrr:>8.3f}   "
                  f"{d:+d}{flag}")
        print()

    statutes.PRIORITY_STEP = orig_step
    for k, v in orig_pri.items():
        statutes.ACTS[k]["priority"] = v
    statutes._index.cache_clear()

    print(f"{'-' * 74}")
    print(f"  best: PRIORITY_STEP={best[2]}  procedural priority={best[3]}  "
          f"R@3={best[0]} (was {base_r3})")
    print(f"  Nothing changed. Re-run with --apply {best[2]} {best[3]} for the edits.")
    print(f"{'-' * 74}\n")


def stale(cases: List[dict]) -> None:
    """Eval questions whose expected answer is a repealed section whose
    successor the corpus now retrieves correctly.

    These are not retrieval failures - they are questions written before
    1 July 2024. Counting them as misses hides real regressions behind
    noise, and 'fixing' retrieval to satisfy them would mean preferring
    repealed law.
    """
    _, _, _, missed = score(cases)
    print(f"\n{'-' * 74}\nSTALE EVAL QUESTIONS\n{'-' * 74}")
    found = 0
    for m in missed:
        suggestions = []
        for exp in m["expect"]:
            act, sec = exp.split(":", 1)
            rec = validity.successor_for(act, sec) if hasattr(
                validity, "successor_for") else None
            if rec is None:
                raw = json.loads(
                    (Path(__file__).resolve().parent.parent / "data"
                     / "repeal_map.json").read_text(encoding="utf-8"))
                rec = raw.get(f"{act}:{sec}")
            if not rec or not isinstance(rec, dict):
                continue
            target = f"{rec.get('act_key')}:{rec.get('section')}"
            if target in m["got"]:
                suggestions.append((exp, target, rec.get("verified", False)))
        if not suggestions:
            continue
        found += 1
        print(f"\n  {m['id']}")
        print(f"    q       {m['q'][:70]}")
        print(f"    expect  {m['expect']}")
        print(f"    got     {m['got']}")
        for old, new, ver in suggestions:
            mark = "verified" if ver else "UNVERIFIED mapping"
            print(f"    -> replace {old} with {new}   ({mark})")
    if not found:
        print("  None - every miss is a real retrieval failure.")
    else:
        print(f"\n  {found} question(s) expect a repealed section whose successor")
        print("  is already being retrieved. Update eval_questions.json rather")
        print("  than tuning retrieval to prefer repealed law.")
    print(f"\n{'-' * 74}\n")


def apply_hint(step: float, pri: int) -> None:
    print(f"\nTwo edits, both by hand:\n")
    print(f"1. backend/app/statutes.py")
    print(f"     PRIORITY_STEP = {step}")
    print(f"   (was {statutes.PRIORITY_STEP} - the ladder was inert, so per-act")
    print(f"   priority had no effect at all)\n")
    print(f"2. backend/app/statutes_ext.py, in EXT_ACTS")
    for k in ("bnss", "bsa"):
        if k in statutes.ACTS:
            print(f"     {k}: \"priority\": {pri},   "
                  f"(was {statutes.ACTS[k].get('priority')})")
    print(f"\n   Procedure and evidence sort below substantive offences, so")
    print(f"   'my phone was taken' reaches BNS 303 rather than a BNSS")
    print(f"   section about property seizure.\n")
    print("Then re-run:  python -m app.sweep_priority")
    print("and:          python -m app.dense --calibrate\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale", action="store_true",
                    help="list eval questions expecting repealed sections")
    ap.add_argument("--apply", nargs=2, metavar=("STEP", "PRI"),
                    help="print the edits for a chosen (step, priority)")
    args = ap.parse_args()
    logging.disable(logging.INFO)

    cases = _cases()
    if args.stale:
        stale(cases)
    elif args.apply:
        apply_hint(float(args.apply[0]), int(args.apply[1]))
    else:
        sweep(cases)


if __name__ == "__main__":
    main()