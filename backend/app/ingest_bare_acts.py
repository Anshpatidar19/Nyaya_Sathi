"""Ingest bare acts from the Hugging Face dataset into backend/data/.

    pip install datasets
    python -m app.ingest_bare_acts              # the curated set
    python -m app.ingest_bare_acts --list       # just print what's available
    python -m app.ingest_bare_acts --all        # every act (34k sections)

Source: mratanusarkar/Indian-Laws (34,244 rows, 1,021 acts).
Columns: act_title, section, law.

The Constitution is deliberately skipped: coi.json is already ingested and
carries chapter_title, which this dataset does not. Ingesting both would put
two copies of every Article in the index, competing for the same query.

The `law` column holds the act name, the chapter heading and the section text
run together as one blob. Splitting the title out of that is the whole job -
BM25 weights the title several times over, so leaving it buried in the body
would waste the strongest signal in the index.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ingest")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATASET = "mratanusarkar/Indian-Laws"

# Acts already ingested from their own, better-structured files. Matched as a
# lowercase substring of act_title.
SKIP = ["constitution of india"]

# act_title substring -> (output filename, act_key).
# Filenames match the `file` values already declared in statutes.ACTS, so an
# ingested act becomes searchable with no registry edit. Anything not listed
# here is ignored unless --all is passed.
# EXACT act_title -> output filename. Exact, not substring: "limitation act"
# as a substring matched "Delimitation Act, 2002", and "information technology
# act" matched "Indian Institutes of Information Technology Act, 2014". Both
# then merged into the wrong file and their section numbers collided, which is
# how the IPC ended up with 12 sections instead of 500.
CURATED: Dict[str, str] = {
    "indian contract act, 1872":            "contract.json",
    "transfer of property act, 1882":       "tpa.json",
    "negotiable instruments act, 1881":     "nia.json",
    "right to information act, 2005":       "rti.json",
    "code of civil procedure, 1908":        "cpc.json",
    "code of criminal procedure act, 1973": "crpc.json",
    "indian penal code, 1860":              "ipc.json",
    "indian evidence act, 1872":            "iea.json",
    "consumer protection act, 2019":        "cpa.json",
    "specific relief act, 1963":            "specific_relief.json",
    "limitation act, 1963":                 "limitation.json",
    "arbitration and conciliation act, 1996": "arbitration.json",
    "information technology act, 2000":     "ita.json",
    "hindu marriage act, 1955":             "hma.json",
    "motor vehicles act, 1988":             "MVA.json",
}


# "Chapter VI Protection of Information 28. Security and confidentiality.- ..."
# The chapter heading, where present, sits between the act name and the
# section number.
_CHAPTER_RE = re.compile(
    r"\bChapter\s+([IVXLC]+|\d+)\s*[-–—:.]?\s*(.{0,90}?)\s*(?=\d+\s*[A-Z]?\s*\.)",
    re.IGNORECASE | re.DOTALL,
)

# "28. Security and confidentiality of information.- (1) The Authority..."
# The title is what sits between the section number and the first ".-", which
# is the India Code convention for ending a marginal note.
# Marginal notes end differently across acts in this source. The 1872-era
# acts often use an em dash with no preceding period, the newer ones use the
# India Code ".-" convention. Try them in order of how safe they are: a
# dash-terminated note can't swallow the first sentence, a period-terminated
# one can, so the dash forms go first.
_TITLE_PATTERNS = [
    # "73. Compensation for loss.- When a contract has been broken"
    re.compile(r"^\s*\d+[A-Za-z]*\s*\.\s*(.{3,160}?)\s*\.\s*[-\u2013\u2014]\s"),
    # "73. Compensation for loss.—When a contract has been broken"
    re.compile(r"^\s*\d+[A-Za-z]*\s*\.\s*(.{3,160}?)\s*\.\s*[-\u2013\u2014]"),
    # "73. Compensation for loss—When a contract has been broken"
    re.compile(r"^\s*\d+[A-Za-z]*\s*\.\s*(.{3,160}?)\s*[\u2013\u2014]\s*"),
    # "73. Compensation for loss: When a contract has been broken"
    re.compile(r"^\s*\d+[A-Za-z]*\s*\.\s*(.{3,160}?)\s*:\s"),
    # Last resort: the note is the first sentence, and the next one starts
    # with a capital or a bracketed sub-clause. Riskier, so it runs last.
    re.compile(r"^\s*\d+[A-Za-z]*\s*\.\s*(.{3,120}?)\.\s+(?=[A-Z(\d])"),
]


# The 1872-era acts mark the title with a LINE BREAK and nothing else:
#
#     Indian Contract Act, 1872
#     10. What agreements are contracts
#     All agreements are contracts if they are made by...
#
# _clean() flattens newlines, which destroys the only boundary there is - so
# the title has to be read off the raw text first, before any normalising.
_RAW_TITLE_RE = re.compile(
    r"^\s*(?:\d+\[)?\s*\d+[A-Za-z]*\s*\.\s*(?P<title>[^\n]{3,160}?)\s*$",
    re.MULTILINE,
)


def _title_from_raw(raw: str, section: str) -> str:
    """Read the marginal note off the line the section number opens."""
    if not raw:
        return ""
    sec = str(section).strip()
    lines = raw.split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        m = re.match(rf"^(?:\d+\[)?\s*{re.escape(sec)}\s*\.\s*(.+)$", line)
        if not m:
            continue

        parts = [m.group(1).strip()]
        # A long marginal note wraps: "12. What is a sound mind for the" /
        # "purposes of contracting" / "A person is said to be...". The body
        # starts at the first line beginning with a capital, so keep taking
        # lowercase continuation lines.
        for nxt in lines[i + 1:]:
            nxt = nxt.strip()
            if not nxt:
                continue
            if not nxt[:1].islower():
                break
            parts.append(nxt)
            if sum(len(p) for p in parts) > 140:
                break

        title = _clean(" ".join(parts))
        # A line ending in a separator is the ".-" style, handled elsewhere.
        title = re.sub(r"[\s.:\-\u2013\u2014]+$", "", title)
        # If the whole section fitted on one line, this is body text, not a
        # marginal note - reject anything sentence-shaped.
        if 3 <= len(title) <= 140 and not re.search(r"[.;] ", title):
            return title
        return ""
    return ""


def _title_of(body: str) -> str:
    for pat in _TITLE_PATTERNS:
        m = pat.match(body)
        if m:
            title = _clean(m.group(1))
            # A "title" with sentence punctuation in it is a runaway match.
            if 3 <= len(title) <= 140 and title.count(";") == 0:
                return title
    return ""


# Editorial markers like "1[" and "]" that wrap amended text.
_AMEND_RE = re.compile(r"\d+\[|\]")


def _clean(text: str) -> str:
    text = _AMEND_RE.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _parse(act_title: str, section: str, law: str) -> Optional[Dict[str, str]]:
    """Split one raw row into chapter / title / body.

    Anchors on the section number rather than stripping a known prefix. The
    act name is repeated at the start of most rows but not all, and not always
    in the same form as act_title - anchoring survives both.

    Returns None when there is no usable body; `_normalize()` in statutes.py
    drops those anyway, and keeping them would only inflate the counts.
    """
    text = _clean(law)
    if not text or len(text) < 20:
        return None

    sec = str(section).strip()

    chapter = ""
    m = _CHAPTER_RE.search(text[:400])
    if m:
        chapter = _clean(f"Chapter {m.group(1)} \u2014 {m.group(2)}")

    # Cut everything before "<section>." - that removes the repeated act name
    # and the chapter heading in one step, however they were worded.
    anchor = re.search(rf"(?<![\d]){re.escape(sec)}\s*\.\s", text)
    body = text[anchor.start():].strip() if anchor else text

    # Raw first: the line break is a stronger signal than any separator, and
    # it is the only one the older acts give us.
    title = _title_from_raw(law, section) or _title_of(body)

    return {
        "chapter_title": chapter,
        "Section": sec,
        "section_title": title,
        "section_desc": body,
    }


def _target(act_title: str, take_all: bool) -> Optional[str]:
    low = act_title.lower().strip()
    if any(sk in low for sk in SKIP):
        return None
    if low in CURATED:
        return CURATED[low]
    if not take_all:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", low).strip("_")[:60]
    return f"ba_{slug}.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest Indian bare acts")
    ap.add_argument("--list", action="store_true", help="print act titles and exit")
    ap.add_argument("--all", action="store_true", help="ingest every act, not just the curated set")
    ap.add_argument("--sample", metavar="ACT_TITLE",
                    help="print raw rows for one act and exit (for debugging parsing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, write nothing")
    args = ap.parse_args()

    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("pip install datasets")

    logger.info("Downloading %s ...", DATASET)
    ds = load_dataset(DATASET, split="train")
    logger.info("%d rows, %d acts", len(ds), len(set(ds["act_title"])))

    if args.list:
        from collections import Counter
        counts = Counter(ds["act_title"])
        for t in sorted(counts):
            mark = " *" if t.lower() in CURATED else ""
            logger.info("  %5d  %s%s", counts[t], t, mark)
        logger.info("\n  * = in the curated set")
        return

    if args.sample:
        # Parsing is the fragile part and the format differs between acts.
        # Rather than guess twice, look at the raw rows.
        want = args.sample.lower().strip()
        shown = 0
        for row in ds:
            if row["act_title"].lower().strip() != want:
                continue
            logger.info("\n--- section %s ---", row["section"])
            logger.info("RAW : %s", (row["law"] or "")[:400])
            parsed = _parse(row["act_title"], row["section"], row["law"])
            if parsed:
                logger.info("TITLE  : %r", parsed["section_title"])
                logger.info("CHAPTER: %r", parsed["chapter_title"])
                logger.info("BODY   : %s", parsed["section_desc"][:120])
            else:
                logger.info("DROPPED")
            shown += 1
            if shown >= 5:
                break
        if not shown:
            logger.info("No rows with that exact title. Check --list for the exact spelling.")
        return

    buckets: Dict[str, List[Dict[str, str]]] = {}
    skipped_acts = set()
    matched_acts: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    dropped: Dict[str, int] = {}

    for row in ds:
        act_title = row["act_title"]
        filename = _target(act_title, args.all)
        if not filename:
            skipped_acts.add(act_title)
            continue

        seen[filename] = seen.get(filename, 0) + 1
        parsed = _parse(act_title, row["section"], row["law"])
        if parsed:
            buckets.setdefault(filename, []).append(parsed)
            matched_acts[filename] = act_title
        else:
            dropped[filename] = dropped.get(filename, 0) + 1

    if not buckets:
        logger.warning(
            "Nothing matched. Run --list to see the act titles, then add the "
            "ones you want to CURATED at the top of this file."
        )
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for filename, rows in sorted(buckets.items()):
        # Section numbers arrive as strings, so "10" sorts before "9". Sort
        # numerically where possible so the files read in statute order.
        def sort_key(r):
            m = re.match(r"(\d+)([A-Za-z]*)", r["Section"])
            return (int(m.group(1)), m.group(2)) if m else (10**9, r["Section"])

        rows.sort(key=sort_key)
        if not args.dry_run:
            path = DATA_DIR / filename
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

        titled = sum(1 for r in rows if r["section_title"])
        chaptered = sum(1 for r in rows if r["chapter_title"])
        pct = (100 * titled // len(rows)) if rows else 0
        flag = "  <-- check --sample" if pct < 50 else ""
        logger.info(
            "  %-22s %4d rows -> %4d kept  %3d%% titled  %3d chaptered   %s%s",
            filename, seen.get(filename, 0), len(rows), pct, chaptered,
            matched_acts.get(filename, ""), flag,
        )
        total += len(rows)

    logger.info("\nWrote %d sections across %d files to %s", total, len(buckets), DATA_DIR)
    if args.dry_run:
        logger.info("(dry run - nothing written)")
    if not args.all:
        logger.info(
            "\n%d other acts were skipped. Run --list to see them, or --all to "
            "take everything (34k sections - expect retrieval precision to drop).",
            len(skipped_acts),
        )
    logger.info("\nNow run:  python -m app.eval_retrieval")


if __name__ == "__main__":
    main()