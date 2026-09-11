"""Extend the bare-act corpus from the `mratanusarkar/Indian-Laws` dataset.

    python -m app.ingest_hf_acts --dry-run                  # report only
    python -m app.ingest_hf_acts --write-local              # write data/*.json
    python -m app.ingest_hf_acts --write-local --vectors    # + Pinecone
    python -m app.ingest_hf_acts --rollback                 # undo the vectors

The dataset has ~34k rows. This script does NOT take a percentage of them. It
takes an allowlist - statutes_ext.EXT_ACTS - and drops everything else. That
is the difference between a legal corpus and a pile of text: an act nobody
curated is an act that can be cited wrongly, and "60% of the rows" is not a
legal criterion.

Order of operations, and why:

  1. Load the dataset.
  2. Reject amendment acts, rules/regulations, and anything off the allowlist.
     Amendment acts first, because they are the failure that looks most like
     success - the text parses fine and reads like law, but it is a diff.
  3. Drop rows whose act+section already exists in the local corpus.
  4. Drop exact-duplicate rows by normalised content hash. The dataset carries
     the same section under several act_title spellings.
  5. Report. Stop, unless told to write.
  6. Write data/<act>.json in the same shape statutes._normalize() reads.
  7. Chunk with chunk_statute(), embed, upsert - the existing pipeline,
     unchanged, so the new acts behave identically to the old ones.

Idempotence comes from deterministic chunk ids (`statute:pocso:4:v1:0`) plus a
Pinecone fetch before embedding: a second run finds the ids already present,
skips the embedding call, and upserts nothing. Running it twice is a no-op,
not a duplicate.

Rollback: the vectors go into their own namespace (PINECONE_NS_STATUTE_EXT,
default `statutes-ext`) and carry a `corpus_batch` tag. `--rollback` deletes
the namespace. Deleting a *git branch* does not delete them - Pinecone is
external state and knows nothing about your branches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import statutes, statutes_ext, validity
from .statutes_ext import (
    CORPUS_BATCH,
    HF_DATASET,
    HF_URL,
    acts_for_tiers,
    match_act_key,
    normalise_title,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Below this, a row is a stub - "[Repealed]", "Omitted", a bare cross
# reference. Embedding it wastes a call and puts a meaningless chunk in the
# retrieval pool where it can outrank a real provision on a short query.
MIN_BODY_CHARS = 60

# Fraction of an act's rows whose parsed section number may disagree with the
# `section` column before the act is refused. Deliberately low: a handful of
# disagreements is a parser edge case, a majority means the column is a row
# counter and no section identity in that act can be trusted.
MISALIGN_LIMIT = 0.10

# Untitled rows are a warning, not a refusal - a section with no heading still
# retrieves on its body text, it just loses the 4x title weighting.
UNTITLED_LIMIT = 0.50

_OMITTED_RE = re.compile(
    r"^\s*[\[\(]?\s*(?:repealed|omitted|deleted|rep\.|\*+)\b", re.IGNORECASE)

# The `law` field is NOT bare section text. Every row is shaped:
#
#     <act title, repeated verbatim>
#     [Chapter III.- Of the Execution of unprivileged Wills]      <- optional
#     63. Execution of unprivileged wills.-
#     Every testator, not being a soldier ...
#
# The first parser anchored ^ on the section number and therefore matched
# nothing at all - every section in the corpus came through with an empty
# title. That is expensive rather than merely untidy: statutes._normalize()
# repeats the title FOUR TIMES in the BM25 token list, so an empty title is a
# measurable recall loss on exactly the queries titles are there to serve.
#
# So parsing is line-based now: strip the repeated act title, capture the
# chapter/part heading instead of discarding it, then read the section number
# and heading off the line that follows.

_WS = re.compile(r"[ \t\u00a0]+")
_MULTI_NL = re.compile(r"\n{2,}")

# Chapter / Part / lettered-group headings. POCSO uses "E.-Sexual Harassment
# and Punishment Therefor"; the ISA uses "Chapter III.- Of the Execution of
# unprivileged Wills"; the DV Act uses "Chapter I Preliminary".
_CHAPTER_RE = re.compile(
    r"^(?:chapter|part|schedule)\b.*$|^[A-Z]\s*\.\s*-\s*\S.*$|^[IVXLC]+\s*\.?\s*$",
    re.IGNORECASE,
)
_PREAMBLE_WORDS = re.compile(
    r"^(?:preliminary|introduction|definitions?)$", re.IGNORECASE)

_NUM_RE = re.compile(r"^(?P<num>\d{1,3}[A-Z]{0,3})\s*\.?\s*(?P<rest>.*)$")

# Only a real heading terminator counts: ".-", ". -", ".\u2014", or a dash
# ending the heading line. A bare full stop was allowed in the first version
# and it matched the last period in the whole window, dragging two subsections
# of body text into the title.
_TERM_RE = re.compile(
    r"^(?P<title>.{2,160}?)\s*(?:\.\s*[-\u2013\u2014]|[-\u2013\u2014]\s*$)", re.DOTALL)

# A line opening with one of these is operative text, not a heading.
_SUBSEC_LINE_RE = re.compile(
    r"^(?:\(\d+\)|\([a-z]\)|\([ivx]+\)|\d+\s*\.\s|[a-z]\s*\.\s|[ivx]+\s*\.\s|"
    r"Rule\s+\d+|Provided|Explanation|Illustration|Exception)", re.IGNORECASE)

# Some acts (the Hindu Succession Act, the SC/ST Act) write headings with no
# terminator at all: "10 Distribution of property among heirs in class I of
# the\n\nSchedule\nThe property of an intestate shall be divided ...". There is
# no punctuation to find, so the heading is accumulated line by line and
# stopped on the first line that reads like a provision. A finite verb is the
# signal that earns its keep - headings are noun phrases, provisions are not.
_BODY_VERB_RE = re.compile(
    r"\b(?:shall|means|may|is\s+said|are\s+said|whoever|subject\s+to|"
    r"in\s+this\s+act|for\s+the\s+purposes|no\s+person|every\s+person|"
    r"nothing\s+in|where\s+the|if\s+any|this\s+act|the\s+central\s+government|"
    r"the\s+state\s+government|notwithstanding)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    s = _WS.sub(" ", (s or "").replace("\r", "").replace("\u00a0", " "))
    return _MULTI_NL.sub("\n\n", s).strip()


def _clean_lines(s: str) -> List[str]:
    s = (s or "").replace("\r", "").replace("\u00a0", " ")
    return [ln.strip() for ln in _WS.sub(" ", s).split("\n")]


def _title_key(t: str) -> str:
    """Fold an act title for comparison, dropping EVERY article.

    Not just a leading "The": the `act_title` column says "Scheduled Castes
    and Scheduled Tribes (Prevention of Atrocities) Act, 1989" while the law
    body's first line says "Scheduled Castes and The Scheduled Tribes ...".
    That one internal article was enough to make the repeated title line
    unrecognisable, so it stayed in the body, the section number was never
    found, and all 23 sections of the act parsed with no title.
    """
    t = re.sub(r"\bthe\b", " ", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", "", t)


def split_heading(law_text: str, section_hint: str = "", act_title: str = ""
                  ) -> Tuple[str, str, str, str]:
    """(section, chapter, title, body) from one dataset row's `law` field.

    Never drops text. If no heading can be identified the whole thing becomes
    the body and the title is empty - a section with no title still retrieves,
    whereas a section whose first sentence got eaten by a greedy regex is
    silently wrong.
    """
    lines = _clean_lines(law_text)
    if not any(ln for ln in lines):
        return (str(section_hint or "").strip(), "", "", "")

    i = 0
    # 1. the repeated act-title line(s)
    key = _title_key(act_title)
    while i < len(lines):
        ln = lines[i]
        if not ln:
            i += 1
            continue
        n = _title_key(ln)
        if key and n and (n == key or (len(n) > 12 and (n in key or key in n))):
            i += 1
            continue
        # No act_title to compare against - fall back to shape.
        if not key and re.match(
                r"^(?:the\s+)?[A-Z].*\bact,?\s*(?:19|20)\d\d\.?$", ln, re.IGNORECASE):
            i += 1
            continue
        break

    # 2. chapter / part / lettered-group headings. Captured, not discarded:
    #    the chapter heading is what lets "fundamental rights" find Article 21,
    #    and it feeds both the BM25 tokens and the citation breadcrumb.
    chapter_bits: List[str] = []
    while i < len(lines):
        ln = lines[i]
        if not ln:
            i += 1
            continue
        if _NUM_RE.match(ln):
            break
        if _CHAPTER_RE.match(ln) or _PREAMBLE_WORDS.match(ln) or len(ln) < 70:
            chapter_bits.append(ln.rstrip(":").strip())
            i += 1
            continue
        break
    chapter = re.sub(r"\s*\.\s*-\s*", " - ",
                     " ".join(b for b in chapter_bits if b)).strip(" -:")

    rest_lines = lines[i:]
    joined = _MULTI_NL.sub("\n\n", "\n".join(rest_lines)).strip()
    if not joined:
        return (str(section_hint or "").strip(), chapter, "",
                _MULTI_NL.sub("\n\n", "\n".join(lines)).strip())

    m = _NUM_RE.match(rest_lines[0])
    if not m:
        return (str(section_hint or "").strip(), chapter, "", joined)
    num = m.group("num")

    # 3. heading with an explicit terminator (".-", ". -", trailing dash)
    after = "\n".join([m.group("rest")] + rest_lines[1:]).strip()
    flat = after[:400].replace("\n", " ")
    tm = _TERM_RE.match(flat)
    title = ""
    body = ""
    if (tm and len(tm.group("title")) <= 160
            and not _SUBSEC_LINE_RE.match(tm.group("title"))
            # A candidate containing a subsection marker is body, not heading.
            and not re.search(r"\((?:\d+|[a-z]|[ivx]+)\)", tm.group("title"))):
        title = tm.group("title")
        # Map the match end back into the newline-bearing text: flat and after
        # differ only in that newlines became spaces, so the offsets line up
        # character for character.
        body = after[tm.end():].strip()
    else:
        # 4. no terminator - accumulate heading-ish lines, stop at a provision
        acc: List[str] = []
        j = 0
        for j, ln in enumerate(rest_lines):
            cand = m.group("rest") if j == 0 else ln
            if not cand:
                continue
            if j > 0 and (_SUBSEC_LINE_RE.match(cand)
                          or _BODY_VERB_RE.search(cand)
                          or len(cand) > 95):
                break
            acc.append(cand)
            if sum(len(a) for a in acc) > 150:
                break
        title = " ".join(acc)
        body = "\n".join(rest_lines[j:]).strip() if j else after

    title = _WS.sub(" ", title).strip(" .-\u2013\u2014")
    body = _MULTI_NL.sub("\n\n", body).strip()
    return (num, chapter, title, body or joined)


def content_key(act_key: str, section: str, body: str) -> str:
    """Normalised content hash, for dedupe across act_title spellings.

    Whitespace, case and punctuation are stripped before hashing, because the
    same section appears in the dataset with different spacing and different
    quote characters. Hashing the raw text would treat those as distinct and
    the dedupe would do nothing.
    """
    norm = re.sub(r"[^a-z0-9]+", "", (body or "").lower())
    return hashlib.sha256(f"{act_key}|{section}|{norm}".encode()).hexdigest()[:16]


def text_hash(body: str) -> str:
    """Stable fingerprint of a section body, stored as metadata."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# what we already have
# ---------------------------------------------------------------------------

def local_corpus_index() -> Tuple[set, set, Dict[str, int]]:
    """(act_keys present, {act_key:section} present, per-act counts).

    Read from statutes._index(), not from the files, so "already present"
    means exactly what the retrieval engine already serves.
    """
    docs = statutes._index()["docs"]
    acts = {d["act_key"] for d in docs}
    pairs = {f"{d['act_key']}:{d['section']}" for d in docs}
    counts = Counter(d["act_key"] for d in docs)
    return acts, pairs, dict(counts)


def pinecone_present(namespaces: List[str]) -> Optional[Dict[str, Any]]:
    """What Pinecone already holds, or None if it is unreachable.

    Unreachable is not an error here: the dry run should still work with no
    Pinecone key set, it just cannot tell you what is in the index.
    """
    try:
        from . import vectorstore

        index = vectorstore.ensure_index()
        st = vectorstore.stats(index)
        ns = st.get("namespaces") or getattr(st, "namespaces", {}) or {}
        return {
            "total": st.get("total_vector_count", 0),
            "namespaces": {k: (v.get("vector_count") if isinstance(v, dict)
                               else getattr(v, "vector_count", 0))
                           for k, v in ns.items()},
        }
    except Exception as exc:
        logger.info("Pinecone not reachable (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

class Report:
    """Everything the dry run needs to print. Built even on a real run, so
    the ingest and the report can never disagree about what happened."""

    def __init__(self) -> None:
        self.rows_seen = 0
        self.selected: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.excluded_titles: Dict[str, Counter] = defaultdict(Counter)
        self.already_present = Counter()
        self.dupes = Counter()
        self.stubs = Counter()
        self.unparsed = Counter()
        self.dataset_titles = Counter()
        # The `section` column is a row index in some acts, not a section
        # number. The Indian Succession Act is the clear case: row 10 holds
        # section 63, row 11 holds section 65, and row 1 holds sections 1 AND
        # 2 in one 2,881-character blob. Ingesting that produces
        # `statute:isa:10` containing section 63's text, cited as section 10 -
        # a wrong citation carrying a confident in-force badge, which is the
        # single worst failure this corpus can produce. So the mismatch is
        # counted, reported, and (above a threshold) refuses the act outright.
        self.misaligned = Counter()
        self.untitled = Counter()
        self.per_act_rows = Counter()

    @property
    def n_selected(self) -> int:
        return sum(len(v) for v in self.selected.values())


def select(rows, allow: Dict[str, Dict[str, Any]], *, replace: bool) -> Report:
    """Walk the dataset once and bucket every row.

    `replace=False` (the default) skips any act+section already in the local
    corpus. `replace=True` keeps them, for the case where the dataset text is
    better than what is on disk - but that overwrites hand-checked text, so
    it is opt-in and loud.
    """
    rep = Report()
    _, have_pairs, _ = local_corpus_index()
    seen_hashes: set = set()

    for row in rows:
        rep.rows_seen += 1
        title_raw = (row.get("act_title") or "").strip()
        rep.dataset_titles[normalise_title(title_raw)] += 1

        act_key, reason = match_act_key(title_raw, allow)
        if not act_key:
            rep.excluded_titles[reason][title_raw] += 1
            continue

        rep.per_act_rows[act_key] += 1
        hint = str(row.get("section") or "").strip()
        section, chapter, sec_title, body = split_heading(
            row.get("law") or "", hint, title_raw)
        section = (section or hint).strip().upper()

        if not section:
            rep.unparsed[act_key] += 1
            continue
        # Parsed number vs the column. Disagreement means the column is a row
        # index, so the section identity cannot be trusted for this act.
        if hint and section.upper() != hint.upper():
            rep.misaligned[act_key] += 1
        if not sec_title:
            rep.untitled[act_key] += 1
        if not body or len(body) < MIN_BODY_CHARS or _OMITTED_RE.match(body):
            rep.stubs[act_key] += 1
            continue

        pair = f"{act_key}:{section}"
        if pair in have_pairs and not replace:
            rep.already_present[act_key] += 1
            continue

        h = content_key(act_key, section, body)
        if h in seen_hashes:
            rep.dupes[act_key] += 1
            continue
        seen_hashes.add(h)

        # Same section number arriving twice with DIFFERENT text - two
        # versions of the act in the dataset. Keep the longer one: the
        # consolidated text of a section is a superset of the pre-amendment
        # text far more often than the reverse.
        prior = next((r for r in rep.selected[act_key] if r["Section"] == section), None)
        if prior is not None:
            if len(body) > len(prior["section_desc"]):
                prior.update({"section_title": sec_title, "section_desc": body,
                              "text_hash": text_hash(body)})
            rep.dupes[act_key] += 1
            continue

        rep.selected[act_key].append({
            "chapter_title": chapter or (row.get("chapter")
                                         or row.get("chapter_title") or ""),
            "Section": section,
            "section_title": sec_title,
            "section_desc": body,
            "text_hash": text_hash(body),
        })

    for k in rep.selected:
        rep.selected[k].sort(key=_section_sort_key)
    return rep


def _section_sort_key(row: Dict[str, Any]):
    """'12A' sorts after '12' and before '13'."""
    m = re.match(r"(\d+)([A-Z]*)", str(row.get("Section", "")))
    return (int(m.group(1)), m.group(2)) if m else (10**9, str(row.get("Section")))


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def print_report(rep: Report, allow: Dict[str, Dict[str, Any]],
                 pine: Optional[Dict[str, Any]], namespace: str,
                 dry_only: bool = True) -> None:
    acts_have, _, counts = local_corpus_index()
    line = "-" * 74

    print(f"\n{line}\nDRY RUN - {HF_DATASET}\n{line}")
    print(f"dataset rows scanned      {rep.rows_seen:,}")
    print(f"distinct act titles       {len(rep.dataset_titles):,}")
    print(f"allowlist size            {len(allow)} acts "
          f"({', '.join(sorted({v['tier'] for v in allow.values()}))})")
    print(f"local corpus before       {sum(counts.values()):,} sections "
          f"across {len(acts_have)} acts")
    if pine:
        print(f"pinecone total            {pine['total']:,} vectors")
        for ns, n in sorted(pine["namespaces"].items()):
            print(f"    namespace {ns:<18} {n:,}")
    else:
        print("pinecone                  not reachable (no key, or index absent)")

    # --- selected ---------------------------------------------------------
    print(f"\n{line}\nSELECTED\n{line}")
    if not rep.selected:
        print("  nothing selected")
    else:
        print(f"  {'act_key':<18}{'sections':>9}{'~chunks':>9}  {'status':<10} act")
        total_chunks = 0
        for k in sorted(rep.selected, key=lambda x: -len(rep.selected[x])):
            rows = rep.selected[k]
            meta = allow[k]
            ch = estimate_chunks(rows)
            total_chunks += ch
            print(f"  {k:<18}{len(rows):>9}{ch:>9}  {meta['status']:<10}{meta['name']}")
        print(f"\n  {'TOTAL':<18}{rep.n_selected:>9}{total_chunks:>9}"
              f"  -> {total_chunks:,} vectors into namespace '{namespace}'")

    # --- allowlisted but not found ---------------------------------------
    missing = [k for k in allow if k not in rep.selected
               and not rep.already_present.get(k)]
    if missing:
        print(f"\n{line}\nON THE ALLOWLIST BUT NOT FOUND IN THE DATASET\n{line}")
        for k in sorted(missing):
            print(f"  {k:<18}{allow[k]['name']}")
        print("\n  These need a different source - India Code, or the bare act.")
        print("  Their statutes_ext entry is harmless meanwhile: no data file")
        print("  means _index() skips the act entirely.")

    # --- already present --------------------------------------------------
    if rep.already_present:
        print(f"\n{line}\nALREADY IN THE LOCAL CORPUS - SKIPPED\n{line}")
        for k, n in rep.already_present.most_common():
            print(f"  {k:<18}{n:>6} sections already present")

    # --- excluded ---------------------------------------------------------
    print(f"\n{line}\nEXCLUDED\n{line}")
    for reason in sorted(rep.excluded_titles, key=lambda r: -sum(rep.excluded_titles[r].values())):
        titles = rep.excluded_titles[reason]
        print(f"\n  {reason}: {sum(titles.values()):,} rows, "
              f"{len(titles):,} distinct titles")
        for t, n in titles.most_common(6):
            print(f"      {n:>6}  {t[:64]}")
        if len(titles) > 6:
            print(f"      ... and {len(titles) - 6:,} more titles")

    if rep.dupes:
        print(f"\n  duplicate records dropped: {sum(rep.dupes.values()):,}")
        for k, n in rep.dupes.most_common(8):
            print(f"      {n:>6}  {k}")
    if rep.stubs:
        print(f"\n  stub / omitted / too-short rows dropped: {sum(rep.stubs.values()):,}")
    if rep.unparsed:
        print(f"  rows with no resolvable section number: {sum(rep.unparsed.values()):,}")

    # --- parse health -----------------------------------------------------
    print(f"\n{line}\nPARSE HEALTH\n{line}")
    print(f"  {'act_key':<18}{'rows':>6}{'untitled':>10}{'misaligned':>12}  verdict")
    blocked = []
    for k in sorted(rep.selected):
        rows = rep.per_act_rows[k] or 1
        unt, mis = rep.untitled.get(k, 0), rep.misaligned.get(k, 0)
        verdict = "ok"
        if mis / rows > MISALIGN_LIMIT:
            verdict = "REFUSED - section column is a row index"
            blocked.append(k)
        elif unt / rows > UNTITLED_LIMIT:
            verdict = "warn - most sections have no heading"
        print(f"  {k:<18}{rows:>6}{unt:>10}{mis:>12}  {verdict}")
    if blocked:
        print(f"\n  {len(blocked)} act(s) refused: {', '.join(blocked)}")
        print("  The parsed section number disagrees with the `section` column")
        print("  for most rows, so a chunk id like statute:<act>:10 would hold")
        print("  a different section's text and cite it under the wrong number.")
        print("  Source these from India Code instead. Override with --allow-misaligned")
        print("  only if you have checked the rows by hand.")

    # --- act status audit -------------------------------------------------
    print(f"\n{line}\nACT-LEVEL STATUS (repeal_map._acts)\n{line}")
    keys = sorted(set(acts_have) | set(rep.selected))
    by_status: Dict[str, List[str]] = defaultdict(list)
    for k in keys:
        by_status[validity.act_status(k).get("status", "?")].append(k)
    for st in sorted(by_status):
        print(f"  {st:<10}{len(by_status[st]):>4}  {', '.join(sorted(by_status[st]))}")
    unknown = [k for k in keys if not validity.act_status(k)["known"]]
    if unknown:
        print(f"\n  NOT IN THE ACT MAP (defaulting to live - fix before ingesting):")
        for k in unknown:
            print(f"      {k}")

    print(f"\n{line}")
    if dry_only:
        print("Nothing has been written. Re-run with --write-local [--vectors].")
    else:
        print("Filtering done. Writing now - see below.")
    print(f"{line}\n")


def estimate_chunks(rows: List[Dict[str, Any]]) -> int:
    """Chunk count without calling the chunker on every row.

    chunk_statute() splits only above MAX_STATUTE_CHARS, and almost nothing
    is. Counting the exceptions is exact enough for a capacity estimate.
    """
    from .chunking import MAX_STATUTE_CHARS

    n = 0
    for r in rows:
        body = r["section_desc"]
        n += 1 if len(body) <= MAX_STATUTE_CHARS else -(-len(body) // MAX_STATUTE_CHARS)
    return n


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def blocked_acts(rep: Report) -> List[str]:
    """Acts whose section numbering cannot be trusted - see MISALIGN_LIMIT."""
    out = []
    for k in rep.selected:
        rows = rep.per_act_rows[k] or 1
        if rep.misaligned.get(k, 0) / rows > MISALIGN_LIMIT:
            out.append(k)
    return sorted(out)


def write_local(rep: Report, allow: Dict[str, Dict[str, Any]]) -> List[str]:
    """Write data/<act>.json in the shape statutes._normalize() already reads.

    Deliberately the same on-disk format as the existing acts, so there is one
    loader and one normaliser rather than a second path for "new" acts. An act
    ingested this way is indistinguishable to the retrieval engine from one
    that was there before, which is what we want.
    """
    written = []
    for act_key, rows in sorted(rep.selected.items()):
        meta = allow[act_key]
        path = DATA_DIR / meta["file"]
        payload = [
            {
                "chapter_title": r["chapter_title"],
                "Section": r["Section"],
                "section_title": r["section_title"],
                "section_desc": r["section_desc"],
                # Provenance travels with the data, not just with the vectors.
                # Someone reading data/pocso.json a year from now should be
                # able to tell where it came from without digging through git.
                "source": HF_DATASET,
                "source_url": HF_URL,
                "text_hash": r["text_hash"],
            }
            for r in rows
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        written.append(str(path.relative_to(DATA_DIR.parent)))
        print(f"  wrote {path.name:<26}{len(payload):>5} sections")
    return written


def ingest_vectors(act_keys: List[str], *, namespace: str, force: bool = False,
                   dry: bool = False) -> Dict[str, int]:
    """Chunk, embed and upsert - reusing the existing pipeline unchanged.

    Idempotence: chunk ids are deterministic, so before embedding anything we
    ask Pinecone which ids it already holds and drop those. A second run
    therefore costs one fetch and zero embedding calls.
    """
    from . import embeddings, vectorstore
    from .chunking import chunk_statute

    statutes._index.cache_clear()          # pick up the files just written
    docs = [d for d in statutes._index()["docs"] if d["act_key"] in set(act_keys)]
    if not docs:
        print("  no documents for those act keys - did --write-local run?")
        return {"chunks": 0, "skipped": 0, "upserted": 0}

    chunks = []
    for d in docs:
        d = dict(d)
        d["corpus_batch"] = CORPUS_BATCH
        d["source"] = HF_DATASET
        d["source_url"] = HF_URL
        chunks.extend(chunk_statute(d))
    print(f"  {len(docs):,} sections -> {len(chunks):,} chunks")

    index = vectorstore.ensure_index()

    skipped = 0
    if not force:
        have = vectorstore.fetch_existing_ids(
            index, [c.chunk_id for c in chunks], namespace)
        before = len(chunks)
        chunks = [c for c in chunks if c.chunk_id not in have]
        skipped = before - len(chunks)
        if skipped:
            print(f"  {skipped:,} chunks already in '{namespace}' - skipping "
                  f"(idempotent re-run)")
    if not chunks:
        print("  nothing new to upsert.")
        return {"chunks": 0, "skipped": skipped, "upserted": 0}

    if dry:
        print(f"  would embed and upsert {len(chunks):,} chunks")
        return {"chunks": len(chunks), "skipped": skipped, "upserted": 0}

    texts = [c.embed_text for c in chunks]
    upserted = 0
    # Streamed rather than embed-everything-then-write: a 429 at batch 40
    # should not discard 39 batches of paid-for work.
    for offset, vectors in embeddings.embed_documents_batched(texts):
        batch = chunks[offset : offset + len(vectors)]
        upserted += vectorstore.upsert(index, batch, vectors, namespace)
        print(f"    {upserted:,}/{len(chunks):,}", flush=True)

    return {"chunks": len(chunks), "skipped": skipped, "upserted": upserted}


def rollback(namespace: str, *, batch: bool = False) -> None:
    """Undo an extension ingest.

    Default drops the whole namespace, which is why the extension lives in its
    own one. `--rollback-batch` deletes by the corpus_batch tag instead, for
    the case where it was ingested into the shared namespace.
    """
    from . import vectorstore

    index = vectorstore.ensure_index()
    if batch:
        print(f"  deleting corpus_batch={CORPUS_BATCH} from '{namespace}'")
        vectorstore.delete_batch(index, CORPUS_BATCH, namespace)
    else:
        print(f"  deleting entire namespace '{namespace}'")
        vectorstore.delete_namespace(index, namespace)
    print("  done. Local data/*.json files are untouched - delete those by hand")
    print("  and remove the statutes_ext entry if you want a full revert.")


# ---------------------------------------------------------------------------
# dataset loading
# ---------------------------------------------------------------------------

def load_rows(limit: Optional[int], local_path: Optional[str]):
    """Yield dataset rows as plain dicts.

    `--local-file` takes a JSON/JSONL export instead of the Hub, which is what
    you want on a machine with no network to huggingface.co, and what makes
    the filter logic testable against a fixture.
    """
    if local_path:
        p = Path(local_path)
        if p.suffix == ".jsonl":
            with p.open(encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    if limit and i >= limit:
                        return
                    if line.strip():
                        yield json.loads(line)
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        for i, row in enumerate(data):
            if limit and i >= limit:
                return
            yield row
        return

    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit(
            "The `datasets` package is not installed.\n"
            "  pip install datasets\n"
            "Or export the dataset once and pass --local-file <path>."
        )

    # Streaming: 34k rows is not large, but streaming avoids a multi-hundred-MB
    # Arrow cache on a laptop and lets --limit stop early.
    ds = load_dataset(HF_DATASET, split="train", streaming=True)
    for i, row in enumerate(ds):
        if limit and i >= limit:
            return
        yield dict(row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=f"Extend the bare-act corpus from {HF_DATASET}.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen and write nothing (default)")
    ap.add_argument("--write-local", action="store_true",
                    help="write backend/data/<act>.json for selected acts")
    ap.add_argument("--vectors", action="store_true",
                    help="chunk, embed and upsert to Pinecone")
    ap.add_argument("--tier", nargs="+", default=["priority", "core"],
                    choices=list(statutes_ext.TIERS),
                    help="which allowlist tiers to include")
    ap.add_argument("--acts", nargs="+", default=None,
                    help="restrict to these act keys (e.g. --acts pocso scst)")
    ap.add_argument("--namespace", default=None,
                    help="Pinecone namespace (default PINECONE_NS_STATUTE_EXT)")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N dataset rows (for a fast smoke test)")
    ap.add_argument("--local-file", default=None,
                    help="read a JSON/JSONL export instead of the Hub")
    ap.add_argument("--replace", action="store_true",
                    help="do not skip sections already in the local corpus "
                         "(OVERWRITES hand-checked text - think first)")
    ap.add_argument("--allow-misaligned", action="store_true",
                    help="write acts whose parsed section numbers disagree "
                         "with the dataset's section column (dangerous - it "
                         "produces citations under the wrong section number)")
    ap.add_argument("--force", action="store_true",
                    help="re-embed chunks already in Pinecone")
    ap.add_argument("--rollback", action="store_true",
                    help="delete the extension namespace and exit")
    ap.add_argument("--rollback-batch", action="store_true",
                    help="delete by corpus_batch tag instead of by namespace")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from . import vectorstore
    namespace = args.namespace or vectorstore.NS_STATUTE_EXT

    if args.rollback or args.rollback_batch:
        rollback(namespace, batch=args.rollback_batch)
        return

    allow = acts_for_tiers(args.tier)
    if args.acts:
        unknown = [a for a in args.acts if a not in statutes_ext.EXT_ACTS]
        if unknown:
            sys.exit(f"Unknown act keys: {', '.join(unknown)}")
        allow = {k: statutes_ext.EXT_ACTS[k] for k in args.acts}

    print(f"Loading {HF_DATASET}"
          + (f" from {args.local_file}" if args.local_file else " (streaming)")
          + " ...")
    rep = select(load_rows(args.limit, args.local_file), allow, replace=args.replace)

    print_report(rep, allow, pinecone_present([namespace]), namespace,
                 dry_only=not (args.write_local or args.vectors))

    if not (args.write_local or args.vectors):
        return

    blocked = blocked_acts(rep)
    if blocked and not args.allow_misaligned:
        for k in blocked:
            rep.selected.pop(k, None)
        print(f"Skipping {', '.join(blocked)} - section numbering not trustworthy.")
        print("Pass --allow-misaligned to override after checking by hand.\n")
        if not rep.selected:
            print("Nothing left to write.")
            return

    if args.write_local:
        print("Writing local corpus files:")
        write_local(rep, allow)
        print()

    if args.vectors:
        keys = sorted(rep.selected)
        print(f"Ingesting vectors into namespace '{namespace}':")
        res = ingest_vectors(keys, namespace=namespace, force=args.force,
                             dry=args.dry_run)
        print(f"\n  chunks considered {res['chunks']:,}   "
              f"skipped(existing) {res['skipped']:,}   "
              f"upserted {res['upserted']:,}")
        print("\n  Turn the fallback on once this looks right:")
        print("    DENSE_FALLBACK=1  in backend/.env")
        print(f"  Rollback:  python -m app.ingest_hf_acts --rollback "
              f"--namespace {namespace}")


if __name__ == "__main__":
    main()