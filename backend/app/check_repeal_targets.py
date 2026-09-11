"""Does every repeal_map mapping point at a section that actually exists?

    python -m app.check_repeal_targets

This is the check repeal_map.json's own _readme asks for: "check that no
mapping points at a section that does not exist in the current indexed
corpus." A mapping whose target is not in the corpus is not harmless - the
UI promises the user a successor section it then cannot show them.

Three failure modes, and they need different fixes:

  SUBSECTION TARGET   the map says BNS "3(5)" but the corpus indexes plain
                      section numbers, so get("bns", "3(5)") returns nothing.
                      The concordance is RIGHT - IPC 34 really does land in
                      BNS 3(5) - but the corpus has no such key. Fix by
                      resolving to the parent section for lookup while keeping
                      the precise citation for display.

  ACT ABSENT          the target act has no data file yet (BNSS, BSA). Fix by
                      ingesting the act, or accept it: the act-level notice
                      still fires, which is what makes the mapping useful.

  SOURCE ABSENT       the OLD section is missing from the local corpus, so the
                      mapping can never be reached. Fix the source act's JSON.

Throwaway diagnostic. Delete when done.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from . import statutes

REPEAL_MAP = Path(__file__).resolve().parent.parent / "data" / "repeal_map.json"
_SUB = re.compile(r"^(?P<base>\d+[A-Z]*)\s*\((?P<sub>[^)]+)\)$")


def main() -> None:
    raw = json.loads(REPEAL_MAP.read_text(encoding="utf-8"))
    maps = {k: v for k, v in raw.items() if not k.startswith("_")}
    acts_in_corpus = {d["act_key"] for d in statutes._index()["docs"]}
    line = "-" * 74

    print(f"\n{line}\nREPEAL MAP TARGET CHECK\n{line}")
    print(f"  {len(maps)} section mappings")

    unverified = {k: v for k, v in maps.items() if not v.get("verified")}
    print(f"  verified:true  {len(maps) - len(unverified)}")
    print(f"  verified:false {len(unverified)}")
    if unverified:
        print(f"\n{line}\nVERIFIED: FALSE\n{line}")
        for k, v in unverified.items():
            print(f"  {k} -> {v.get('act_short')} {v.get('section')}")
            src = v.get("source", "")
            for i in range(0, min(len(src), 300), 68):
                print(f"      {src[i:i+68]}")

    buckets: Counter = Counter()
    problems = {"subsection": [], "act_absent": [], "source_absent": [],
                "target_absent": []}

    for key, rec in maps.items():
        old_act, old_sec = key.split(":", 1)
        new_act = rec.get("act_key") or ""
        new_sec = str(rec.get("section") or "")

        if not statutes.get(old_act, old_sec):
            problems["source_absent"].append((key, f"{old_act}:{old_sec}"))
            buckets["source not in corpus"] += 1
            continue
        if new_act not in acts_in_corpus:
            problems["act_absent"].append((key, new_act))
            buckets["target act not ingested"] += 1
            continue
        if statutes.get(new_act, new_sec):
            buckets["resolves cleanly"] += 1
            continue

        m = _SUB.match(new_sec)
        if m and statutes.get(new_act, m.group("base")):
            problems["subsection"].append(
                (key, new_sec, f"{new_act}:{m.group('base')}"))
            buckets["subsection target (parent exists)"] += 1
        else:
            problems["target_absent"].append((key, f"{new_act}:{new_sec}"))
            buckets["target section missing"] += 1

    print(f"\n{line}\nRESOLUTION\n{line}")
    for label, n in buckets.most_common():
        print(f"  {label:<38}{n:>5}")

    if problems["subsection"]:
        print(f"\n{line}\nSUBSECTION TARGETS - concordance is right, corpus "
              f"cannot key them\n{line}")
        for key, target, parent in problems["subsection"]:
            print(f"  {key:<14} -> {target:<10} (parent {parent} exists)")
        print("\n  These are real mappings that silently resolve to nothing.")
        print("  validity.status_for() should fall back to the parent section")
        print("  for the lookup while still displaying the precise citation.")

    if problems["act_absent"]:
        print(f"\n{line}\nTARGET ACT NOT INGESTED\n{line}")
        by_act: Counter = Counter(a for _, a in problems["act_absent"])
        for act, n in by_act.most_common():
            print(f"  {act:<8}{n:>4} mapping(s) point here")
        print("\n  The act-level notice still fires, so these stay useful.")

    if problems["source_absent"]:
        print(f"\n{line}\nSOURCE SECTION MISSING FROM THE LOCAL CORPUS\n{line}")
        for key, where in problems["source_absent"]:
            print(f"  {key:<14} ({where} not indexed - mapping unreachable)")

    if problems["target_absent"]:
        print(f"\n{line}\nTARGET SECTION MISSING (act present)\n{line}")
        for key, where in problems["target_absent"]:
            print(f"  {key:<14} -> {where}")

    print(f"\n{line}\n")


if __name__ == "__main__":
    main()