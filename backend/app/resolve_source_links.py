"""Pre-resolve Indian Kanoon document ids for statute sections.

    python -m app.resolve_source_links --acts mva,nia --dry-run   # count only
    python -m app.resolve_source_links --acts mva,nia             # resolve those
    python -m app.resolve_source_links                            # everything

Each unresolved section costs ONE Kanoon search; already-resolved sections
and recent misses cost nothing, so re-running is cheap and safe. Results land
in backend/data/kanoon_source_ids.json - commit that file and every source
card becomes a direct Kanoon link from the first render, on every machine.

Acts that already have their own per-section pages (BNS/IPC/CrPC/BNSS on
devgan.in, the Constitution) are skipped unless --include-direct is passed,
because their cards are already direct.
"""

import argparse
import asyncio
import sys

from . import kanoon, source_links, statutes


def _targets(acts, include_direct):
    for key, doc in statutes._index()["by_key"].items():
        if acts and doc["act_key"] not in acts:
            continue
        if doc["section"].lower() == "preamble":
            continue
        if not include_direct and doc.get("url") and not source_links.is_search_url(doc["url"]):
            continue
        entry = source_links._load().get(key)
        if entry and entry.get("docid"):
            continue
        if source_links._is_fresh_miss(entry):
            continue
        yield doc


async def _run(docs, concurrency):
    sem = asyncio.Semaphore(concurrency)
    found = missed = 0

    async def one(doc):
        nonlocal found, missed
        async with sem:
            tid = await source_links.resolve(doc)
            if tid:
                found += 1
            else:
                missed += 1
            done = found + missed
            if done % 25 == 0 or done == len(docs):
                print(f"  {done}/{len(docs)}  resolved={found}  no-match={missed}", flush=True)

    await asyncio.gather(*(one(d) for d in docs))
    await kanoon.close_client()
    return found, missed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--acts", default="", help="comma-separated act keys (e.g. mva,nia,hma)")
    ap.add_argument("--dry-run", action="store_true", help="count sections; spend nothing")
    ap.add_argument("--include-direct", action="store_true",
                    help="also resolve acts that already have per-section pages")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    acts = {a.strip() for a in args.acts.split(",") if a.strip()}
    unknown = acts - set(statutes.ACTS)
    if unknown:
        sys.exit(f"Unknown act keys: {', '.join(sorted(unknown))}. "
                 f"Known: {', '.join(sorted(statutes.ACTS))}")

    docs = list(_targets(acts, args.include_direct))
    by_act = {}
    for d in docs:
        by_act[d["act_key"]] = by_act.get(d["act_key"], 0) + 1
    print(f"{len(docs)} sections to resolve (= {len(docs)} Kanoon searches):")
    for k, n in sorted(by_act.items(), key=lambda x: -x[1]):
        print(f"  {k:<16} {n}")

    if args.dry_run or not docs:
        return

    found, missed = asyncio.run(_run(docs, args.concurrency))
    print(f"\nDone. resolved={found} no-match={missed} "
          f"searches spent={kanoon.CALL_COUNTS['search']}")
    print(f"Mapping saved to {source_links.CACHE_PATH} - commit it.")


if __name__ == "__main__":
    main()