"""Legal safety eval. Run: python -m app.eval_validity

Retrieval eval asks "did we find the right section?". This asks the questions
that make the system a legal tool rather than a search box:

  1. Citation parsing  - does "s. 138 NI Act" resolve to a section, or get
                         tokenised into noise?
  2. Repeal detection  - is repealed law flagged, every time?
  3. Repeal mapping    - does data/repeal_map.json point at sections that
                         actually exist, and is it marked verified?
  4. Grounding bands   - does the assessment separate a well-supported answer
                         from an unsupported one?

No model calls, no cost. Run it after touching statutes.py, validity.py or
the repeal map.
"""

import argparse
import re
import sys
from typing import Any, Dict, List

from . import statutes, validity

# --- 1. Citation parsing --------------------------------------------------
# (query, expected act_key or None if we only care that SOMETHING resolves)
CITATION_CASES = [
    ("what does BNS 103 say", "bns"),
    ("BNS section 318", "bns"),
    ("section 103 of the Bharatiya Nyaya Sanhita", "bns"),
    ("Article 21", "coi"),
    ("article 32 of the constitution", "coi"),
    ("art. 14", "coi"),
    ("s. 138 NI Act", "nia"),
    ("section 138 of the Negotiable Instruments Act", "nia"),
    ("u/s 73 Indian Contract Act", "contract"),
    ("section 114 of the Transfer of Property Act", "tpa"),
    ("section 6 RTI Act", "rti"),
    ("IPC 302", "ipc"),
    ("section 302 of the Indian Penal Code", "ipc"),
]

# Case citations should be RECOGNISED (routed to case law), not looked up.
CASE_CITATION_CASES = [
    "AIR 1973 SC 1461",
    "(2017) 10 SCC 1",
    "2019 SCC OnLine SC 1005",
    "what did the court hold in AIR 1978 SC 597",
]

NON_CITATIONS = [
    "my landlord took 3 months rent in advance",
    "I was beaten by 4 men",
    "the accident happened in 2019",
]


def eval_citations(verbose: bool) -> Dict[str, Any]:
    available = {d["act_key"] for d in statutes._index()["docs"]}
    hits = miss = skipped = 0
    problems: List[str] = []

    by_key = statutes._index()["by_key"]
    for query, expect_act in CITATION_CASES:
        if expect_act not in available:
            skipped += 1
            continue
        # Skip cases where the section itself isn't in the corpus. The IPC
        # rows in the bare-acts dataset stop at 12 sections, so "IPC 302"
        # failing says nothing about the citation parser - which is what this
        # is meant to be testing.
        num = re.search(r"(\d{1,3}[A-Za-z]?)", query)
        if num and f"{expect_act}:{num.group(1)}" not in by_key:
            skipped += 1
            continue
        found = statutes.lookup_section(query)
        keys = {d["act_key"] for d in found}
        if expect_act in keys:
            hits += 1
            if verbose:
                print(f"    PASS  {query}")
        else:
            miss += 1
            problems.append(
                f"{query!r} -> expected {expect_act}, got "
                f"{sorted(keys) or 'nothing'}"
            )

    case_hits = 0
    for q in CASE_CITATION_CASES:
        if statutes.case_citations(q):
            case_hits += 1
        else:
            problems.append(f"case citation not recognised: {q!r}")

    # A bare number in a sentence about rent should not be read as a section.
    false_positives = 0
    for q in NON_CITATIONS:
        if statutes.case_citations(q):
            false_positives += 1
            problems.append(f"false case-citation match: {q!r}")

    return {
        "hits": hits, "miss": miss, "skipped": skipped,
        "case_hits": case_hits, "case_total": len(CASE_CITATION_CASES),
        "false_positives": false_positives,
        "problems": problems,
    }


# --- 2 & 3. Repeal detection and mapping ----------------------------------

def eval_repeal(verbose: bool) -> Dict[str, Any]:
    docs = statutes._index()["docs"]
    by_key = statutes._index()["by_key"]
    problems: List[str] = []

    repealed_acts = {
        k for k, m in statutes.ACTS.items() if not m["current"]
    }
    indexed_repealed = [d for d in docs if d["act_key"] in repealed_acts]

    # Every chunk from a repealed act must come back flagged. No exceptions -
    # a single unflagged one is the failure mode that matters.
    unflagged = 0
    for d in indexed_repealed:
        st = validity.status_for(d)
        if st["status"] != validity.REPEALED:
            unflagged += 1
            problems.append(f"not flagged as repealed: {d['act_key']}:{d['section']}")

    # Every chunk from a current act must NOT be flagged.
    false_alarms = 0
    for d in docs:
        if d["act_key"] in repealed_acts:
            continue
        if validity.status_for(d)["status"] == validity.REPEALED:
            false_alarms += 1
            problems.append(f"wrongly flagged: {d['act_key']}:{d['section']}")

    # Mapping integrity: every successor must exist in the index, or the badge
    # sends the reader to a provision we cannot show them.
    mapping = validity._repeal_map()
    dangling = 0
    unverified = 0
    for src, dest in mapping.items():
        # Subsection references ("3(5)") are indexed at section level, so
        # check the base section exists rather than the exact string.
        key = f"{dest.get('act_key')}:{validity.base_section(dest.get('section'))}"
        if key not in by_key:
            dangling += 1
            problems.append(f"mapping {src} -> {key}, but {key} is not in the index")
        if not dest.get("verified"):
            unverified += 1

    return {
        "indexed_repealed": len(indexed_repealed),
        "unflagged": unflagged,
        "false_alarms": false_alarms,
        "mappings": len(mapping),
        "dangling": dangling,
        "unverified": unverified,
        "problems": problems,
    }


# --- 4. Grounding bands ---------------------------------------------------

def eval_grounding(verbose: bool) -> Dict[str, Any]:
    live = {"act_key": "bns", "section": "103", "act_short": "BNS",
            "title": "Punishment for murder", "current": True}
    dead = {"act_key": "ipc", "section": "302", "act_short": "IPC",
            "title": "Punishment for murder", "current": False,
            "superseded_by": "Bharatiya Nyaya Sanhita, 2023"}
    judgment = {"docid": "123", "title": "Some v. Other", "court": "SC"}

    validity.annotate([live, dead])

    # The agent reports which sources it used as a list of indices, so that
    # is what these exercise - not markers in the prose, which it never writes.
    cases = [
        ("used a live statute",
         "The punishment is life imprisonment.", [live], [1], validity.WELL_GROUNDED),
        ("no sources at all",
         "The law says various things.", [], [], validity.THIN),
        ("several sources, used none",
         "The law says various things.", [live, live, live], [], validity.THIN),
        ("single source, used it",
         "The punishment is life imprisonment.", [live], [1], validity.WELL_GROUNDED),
        ("case law only",
         "As held, the test is settled.", [judgment], [1], validity.THIN),
        ("used repealed law",
         "The police must register an FIR.", [dead], [1], validity.PARTLY_GROUNDED),
        ("out-of-range index ignored",
         "The law says various things.", [live], [7], validity.THIN),
    ]

    hits = 0
    problems: List[str] = []
    for label, body, sources, used, expected in cases:
        got = validity.assess(body, sources, [], used_sources=used)
        if got["level"] == expected:
            hits += 1
            if verbose:
                print(f"    PASS  {label} -> {got['label']}")
        else:
            problems.append(f"{label}: expected {expected}, got {got['level']}")

    return {"hits": hits, "total": len(cases), "problems": problems}


# --- runner ---------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Legal safety eval")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    acts = statutes.available_acts()
    if not acts:
        sys.exit("No statute data loaded. Run the ingest scripts first.")

    print()
    print("=" * 66)
    print(f"  LEGAL SAFETY EVAL  ·  {len(acts)} act(s) indexed")
    print("=" * 66)

    c = eval_citations(args.verbose)
    total_c = c["hits"] + c["miss"]
    print(f"\n  Citation lookup     {c['hits']}/{total_c}"
          f"   ({c['skipped']} skipped - act not ingested)")
    print(f"  Case citations      {c['case_hits']}/{c['case_total']} recognised,"
          f" {c['false_positives']} false positives")

    r = eval_repeal(args.verbose)
    print(f"\n  Repealed sections   {r['indexed_repealed']} indexed,"
          f" {r['unflagged']} UNFLAGGED, {r['false_alarms']} wrongly flagged")
    print(f"  Repeal mappings     {r['mappings']} defined,"
          f" {r['dangling']} dangling, {r['unverified']} unverified")

    g = eval_grounding(args.verbose)
    print(f"\n  Grounding bands     {g['hits']}/{g['total']}")

    problems = c["problems"] + r["problems"] + g["problems"]
    if problems:
        print("\n" + "-" * 66)
        print(f"  PROBLEMS ({len(problems)})")
        print("-" * 66)
        for p in problems[:40]:
            print(f"    {p}")
        if len(problems) > 40:
            print(f"    ... and {len(problems) - 40} more")
        print()
    else:
        print("\n  No problems.\n")

    # The one that should never be non-zero.
    if r["unflagged"]:
        print("  *** Repealed law is being presented as current. Fix before demoing. ***\n")
        sys.exit(1)


if __name__ == "__main__":
    main()