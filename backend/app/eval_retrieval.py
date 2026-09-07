"""Retrieval eval. Run: python -m app.eval_retrieval

Answers the only question that matters about the statute index: when someone
describes a situation in their own words, does the section that governs it
come back?

Everything downstream depends on this. A perfect prompt cannot save an answer
built on the wrong provision, so this is the number to watch when tuning
BM25 weights, the query refiner, or the companion-section logic.

No API calls, no cost, runs in about a second. Run it after every retrieval
change - a score that moves is the difference between engineering and
guessing.

Usage:
    python -m app.eval_retrieval              # summary + misses
    python -m app.eval_retrieval -k 5         # score at top-5 instead of 3
    python -m app.eval_retrieval -v           # show every case, not just misses
    python -m app.eval_retrieval --json out.json   # save for comparing runs
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import statutes

QUESTIONS = Path(__file__).resolve().parent.parent / "data" / "eval_questions.json"


def load_cases() -> List[Dict[str, Any]]:
    if not QUESTIONS.exists():
        sys.exit(f"No question set at {QUESTIONS}")
    with open(QUESTIONS, encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def key(hit: Dict[str, Any]) -> str:
    """The identity statutes.py uses internally: 'bns:103'."""
    return f"{hit['act_key']}:{hit['section']}"


def run(k: int) -> Dict[str, Any]:
    cases = load_cases()
    results = []

    for case in cases:
        hits = statutes.search(case["q"], limit=k)
        got = [key(h) for h in hits][:k]
        expected = case["expect"]

        # Pass if ANY expected section appears. Several provisions often
        # govern one situation - theft and snatching, hurt and grievous hurt -
        # and insisting on one exact answer would penalise a correct result.
        hit_at_k = any(e in got for e in expected)
        hit_at_1 = bool(got) and got[0] in expected
        rank = next((i + 1 for i, g in enumerate(got) if g in expected), None)

        results.append({
            "id": case["id"],
            "tier": case.get("tier", "semantic"),
            "q": case["q"],
            "expected": expected,
            "got": got,
            "got_titles": [h["title"] for h in hits][:k],
            "hit": hit_at_k,
            "hit_at_1": hit_at_1,
            "rank": rank,
        })

    return {"k": k, "results": results}


def summarise(report: Dict[str, Any]) -> Dict[str, Any]:
    rows = report["results"]
    total = len(rows)
    hits = sum(r["hit"] for r in rows)
    top1 = sum(r["hit_at_1"] for r in rows)

    # Mean reciprocal rank: rewards putting the right section first rather
    # than merely somewhere in the list. 1.0 is always first, 0.5 is always
    # second. Recall@k alone can't see that difference.
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / total if total else 0.0

    by_tier = {}
    for r in rows:
        t = by_tier.setdefault(r["tier"], {"n": 0, "hit": 0})
        t["n"] += 1
        t["hit"] += r["hit"]

    return {"total": total, "hits": hits, "top1": top1, "mrr": mrr, "by_tier": by_tier}


def pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.1f}%" if d else "n/a"


def main() -> None:
    ap = argparse.ArgumentParser(description="Statute retrieval eval")
    ap.add_argument("-k", type=int, default=3, help="top-k to score at (default 3)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every case")
    ap.add_argument("--json", dest="json_out", help="write the full report to a file")
    ap.add_argument("--sweep", action="store_true",
                    help="try several ranking weights and report the best")
    ap.add_argument("--verify", action="store_true",
                    help="check every expected section exists, and print its title")
    ap.add_argument("--prune", action="store_true",
                    help="drop cases whose expected sections aren't in the index")
    args = ap.parse_args()

    acts = statutes.available_acts()
    if not acts:
        sys.exit(
            "No statute data loaded. Run the ingest scripts first:\n"
            "  python -m app.ingest_bns\n"
            "  python -m app.ingest_constitution"
        )

    if args.verify:
        # Ground truth written from memory is worth exactly nothing. This
        # prints what each expected key actually resolves to, so a wrong
        # section number shows up as MISSING rather than as a silent miss
        # that looks like a retrieval failure.
        by_key = statutes._index()["by_key"]
        cases = load_cases()
        missing = 0
        print()
        for case in cases:
            for key in case["expect"]:
                doc = by_key.get(key)
                if doc:
                    print(f"  OK       {key:<18} {doc['title'][:60]}")
                else:
                    missing += 1
                    print(f"  MISSING  {key:<18} <- not in the index "
                          f"({case['id']})")
        print(f"\n  {missing} expected section(s) not in the index.")
        if missing:
            print("  Either the act isn't ingested, or the section number is "
                  "wrong. Fix the question set before reading the score.\n")
        else:
            print("  Ground truth checks out.\n")
        return

    if args.prune:
        # A case whose expected section isn't in the corpus can never pass, so
        # it isn't measuring retrieval - it's just dragging the score down and
        # hiding real misses. Drop those, and trim expectations that name a
        # section the corpus doesn't have.
        by_key = statutes._index()["by_key"]
        raw = json.loads(QUESTIONS.read_text(encoding="utf-8"))
        kept, dropped, trimmed = [], [], []
        for case in raw["cases"]:
            have = [k for k in case["expect"] if k in by_key]
            if not have:
                dropped.append((case["id"], case["expect"]))
                continue
            if len(have) != len(case["expect"]):
                trimmed.append((case["id"],
                                [k for k in case["expect"] if k not in by_key]))
                case["expect"] = have
            kept.append(case)

        raw["cases"] = kept
        QUESTIONS.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print()
        for cid, exp in dropped:
            print(f"  DROPPED  {cid:<26} {', '.join(exp)} not in the index")
        for cid, gone in trimmed:
            print(f"  TRIMMED  {cid:<26} removed {', '.join(gone)}")
        print(f"\n  {len(kept)} cases kept, {len(dropped)} dropped, "
              f"{len(trimmed)} trimmed.")
        print("  Re-run the eval for a score that only counts answerable "
              "questions.\n")
        return

    if args.sweep:
        # Ranking weights are the one thing worth tuning empirically, and the
        # question set is the only honest way to do it. Scoring changes need
        # no re-index, so each pass is a second.
        print("\n  repealed  priority     R@3     R@1     MRR")
        print("  " + "-" * 44)
        best = None
        for rep in (1.0, 0.85, 0.7, 0.55, 0.4, 0.25):
            for pri in (0.0, 0.05, 0.08, 0.12, 0.2):
                statutes.REPEALED_MULT = rep
                statutes.PRIORITY_STEP = pri
                sm = summarise(run(args.k))
                row = (sm["hits"], sm["mrr"], rep, pri)
                if best is None or row[:2] > best[:2]:
                    best = row
                print(f"  {rep:>8.2f}  {pri:>8.2f}  "
                      f"{pct(sm['hits'], sm['total']):>6}  "
                      f"{pct(sm['top1'], sm['total']):>6}  {sm['mrr']:>6.3f}")
        print(f"\n  Best: REPEALED_MULT={best[2]}, PRIORITY_STEP={best[3]}"
              f"  ({pct(best[0], len(load_cases()))} R@{args.k}, MRR {best[1]:.3f})")
        print("  Set these at the top of statutes.py if you want to keep them.\n")
        return

    report = run(args.k)
    s = summarise(report)
    k = args.k

    print()
    print("=" * 66)
    print(f"  RETRIEVAL EVAL  ·  {len(acts)} act(s) indexed  ·  k={k}")
    print("=" * 66)
    print(f"  Recall@{k}   {s['hits']}/{s['total']}   {pct(s['hits'], s['total'])}")
    print(f"  Recall@1   {s['top1']}/{s['total']}   {pct(s['top1'], s['total'])}")
    print(f"  MRR        {s['mrr']:.3f}")
    print()
    for tier, t in sorted(s["by_tier"].items()):
        print(f"  {tier:<10} {t['hit']}/{t['n']}   {pct(t['hit'], t['n'])}")
    print()

    misses = [r for r in report["results"] if not r["hit"]]

    if args.verbose:
        print("-" * 66)
        for r in report["results"]:
            mark = "PASS" if r["hit"] else "FAIL"
            at = f" @{r['rank']}" if r["rank"] else ""
            print(f"  [{mark}{at}] {r['id']}: {r['q']}")
        print()

    if misses:
        print("-" * 66)
        print(f"  MISSES ({len(misses)})")
        print("-" * 66)
        for r in misses:
            print(f"\n  {r['id']}")
            print(f"    asked:    {r['q']}")
            print(f"    expected: {', '.join(r['expected'])}")
            if r["got"]:
                # The titles are what actually diagnose a miss - a wrong
                # section that reads plausibly means the query terms matched
                # the wrong provision, which is a different fix from an empty
                # result set.
                for gk, gt in zip(r["got"], r["got_titles"]):
                    print(f"    got:      {gk:<16} {gt}")
            else:
                print("    got:      (nothing)")
        print()
    else:
        print("  No misses.\n")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump({"summary": s, **report}, fh, indent=2, ensure_ascii=False)
        print(f"  Full report written to {args.json_out}\n")


if __name__ == "__main__":
    main()