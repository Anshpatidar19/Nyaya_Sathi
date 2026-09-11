"""Scrape BNSS and BSA bare act text into the local corpus format.

    python -m app.scrape_bnss_bsa --act bnss --dry-run      # fetch 5, show them
    python -m app.scrape_bnss_bsa --act bnss                # all 531 sections
    python -m app.scrape_bnss_bsa --act bsa                 # all 170 sections
    python -m app.scrape_bnss_bsa --act bnss --verify       # check a cached scrape

WHY A SCRAPER AT ALL

The `mratanusarkar/Indian-Laws` dataset predates the 2023 criminal laws - it
carries "Code of Criminal Procedure Act, 1973" with 484 rows and has no BNSS
or BSA. There is no BNSS/BSA dataset on Hugging Face with full section text.
So the two acts the corpus most needs have to come from the web.

ACCEPTANCE TEST, NOT A VIBE CHECK

BNSS has 531 sections in 39 chapters; BSA has 170 in 12 chapters. Those counts
are load-bearing here because the repeal clause is the LAST section of each
act, and repeal_map.json already cites BNSS s.531 and BSA s.170 as the repeal
provisions. Same pattern as the BNS: 358 sections, repeal at s.358. So if a
scrape does not yield exactly 531 and 170, it is incomplete and the script
says so. (advocatekhoj's index page advertises "591 sections" for BNSS - that
is their row counter including chapter headings, not a section count. It is
also why the count check matters.)

ON THE SOURCE

The statutory text itself is a government work. The site's own compilation,
navigation and styling are theirs, so this takes ONLY the bare section text
and records India Code as the citation URL rather than passing the scrape off
as authoritative. Requests are serialised with a delay; there is no reason to
hammer someone's server for a portfolio project.

Verify before trusting. `--verify` spot-checks the sections that
repeal_map.json actually points at - BNSS 173 (FIR), 482 (anticipatory bail),
144 (maintenance); BSA 63 (electronic evidence), 2 (definitions) - because a
wrong mapping target is worse than a missing one.

CACHING

Every fetched page is cached under backend/.cache/<act>/. A re-run costs
nothing and resumes where it stopped, so an interrupted scrape is not a
problem and tweaking the parser does not mean re-fetching 531 pages.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BACKEND = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND / "data"
CACHE_DIR = BACKEND / ".cache"

BASE = "https://www.advocatekhoj.com/library/bareacts"

ACTS = {
    "bnss": {
        "slug": "bharatiyanagarik2023",
        "file": "bnss.json",
        "name": "Bharatiya Nagarik Suraksha Sanhita, 2023",
        "expect_sections": 531,
        "expect_chapters": 39,
        "india_code": "https://www.indiacode.nic.in/handle/123456789/20099",
        # Sections repeal_map.json points at. If these are wrong, the repeal
        # badges are wrong, which is the failure that matters most.
        "spot_check": {
            "173": "information in cognizable cases",
            "482": "direction for grant of bail",
            "144": "maintenance of wives, children and parents",
            "35": "arrest without warrant",
            "531": "repeal and savings",
        },
    },
    "bsa": {
        # NOTE the three a's: "bharatiyaaakshya2023", not "...sakshya...".
        # That is a typo on the source site, confirmed against live section
        # URLs. It was first guessed from the BNSS pattern and every section
        # 404'd - which is why the slug is verified here rather than inferred.
        "slug": "bharatiyaaakshya2023",
        "file": "bsa.json",
        "name": "Bharatiya Sakshya Adhiniyam, 2023",
        "expect_sections": 170,
        "expect_chapters": 12,
        "india_code": "https://www.indiacode.nic.in/handle/123456789/20063",
        "spot_check": {
            "2": "definitions",
            "63": "electronic",
            "170": "repeal",
        },
    },
}

# --- html -> text ---------------------------------------------------------

_SCRIPTS = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.I | re.S)
_BR = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END = re.compile(r"</(p|div|li|tr|h[1-6]|td|table)\s*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\u00a0]+")


def to_text(raw: str) -> List[str]:
    s = _SCRIPTS.sub(" ", raw)
    s = _BR.sub("\n", s)
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s).replace("\u00a0", " ")
    s = _WS.sub(" ", s)
    return [ln.strip() for ln in s.split("\n")]


# The section page body sits between the breadcrumb/share furniture and the
# Back / Act Index / Next navigation. Anchor on the numbered heading line and
# stop at the first navigation or footer marker - more robust than guessing
# CSS classes that will change the next time the site is redesigned.
_STOP = re.compile(
    r"^(?:back|act index|next|company|for clients|sawal jawab|for advocates|"
    r"blogs|law school|advertise|law library|sc judgments|advocatekhoj|"
    r"\u00a9\s*\d{4}|powered by|information provided on)\b", re.I)

_CHAPTER_LINE = re.compile(r"^(chapter|part)\s+[IVXLCDM\d]+", re.I)

# Lettered subdivision inside a chapter: "A.-Summons", "B.\u2014Warrant of
# arrest", "C.- Proclamation and attachment". These appear ONLY on the page
# that opens the subdivision, exactly like chapter headings, so they need
# their own anchor and their own forward-fill slot. Treating them as part of
# the chapter string made s.72-93 claim "A.-Summons" when they are under B, C
# and D - a wrong breadcrumb on 22 sections.
_SUBPART_LINE = re.compile(
    r"^(?P<letter>[A-Z])\s*[.\u2014\u2013-]{1,3}\s*(?P<name>[A-Z][^.]{2,60})$")

# Site furniture that sits between the breadcrumb and the section heading, in
# exactly the position a chapter heading would occupy.
_FURNITURE = re.compile(
    r"^(?:add advocatekhoj|share on|bharatiya |the bharatiya |law library|"
    r"bare acts|post a|join as|log in|register|follow us|search|home)",
    re.I)

# The same furniture, removable from ANYWHERE in a line. Inline <a> tags do
# not create a line break, so a share link can end up sharing a line with the
# heading that follows it - "Add AdvocateKhoj as a Preferred Source
# B.\u2014Warrant of arrest". Stripping before matching means one stray inline
# link cannot hide a chapter or subdivision heading.
_FURNITURE_STRIP = re.compile(
    r"(?:add advocatekhoj as a preferred source|share on [a-z ()]+|"
    r"go to homepage|act index back|follow us)", re.I)


def parse_section(raw: str, number: str) -> Optional[Dict[str, str]]:
    """(chapter, title, body) for one section page, or None if not found."""
    # Strip site furniture from every line up front, not just in the chapter
    # scan. Inline <a> tags create no line break, so a share link can end up
    # prefixed onto whatever follows it - including the numbered heading
    # itself ("Add AdvocateKhoj as a Preferred Source 64. Summons how
    # served."), which made the heading regex miss and the whole section come
    # back unparsed.
    lines = [_WS.sub(" ", _FURNITURE_STRIP.sub(" ", ln)).strip(" >|")
             for ln in to_text(raw)]

    # Heading: "173. Information in cognizable cases." Some pages omit the
    # trailing period, and a few titles run onto a second line.
    head = re.compile(rf"^{re.escape(number)}\s*\.\s*(?P<title>.*)$")
    idx = -1
    title = ""
    for i, ln in enumerate(lines):
        m = head.match(ln)
        # A match inside the nav furniture would be a false positive; the real
        # heading is followed within a few lines by subsection text.
        if m and len(ln) < 220:
            idx, title = i, m.group("title").strip()
            break
    if idx < 0:
        return None

    # Chapter heading sits immediately above, as "Chapter XIII" then its
    # name. It is present ONLY on the first section of each chapter, so most
    # pages legitimately have none - scrape() forward-fills those.
    #
    # A chapter is returned only when an actual "Chapter N" / "Part N" line is
    # found. Accepting any short line above the heading instead looked fine
    # until a page without a chapter heading yielded the chapter
    # "Add AdvocateKhoj as a Preferred Source" - a short line, not a footer
    # marker, sitting exactly where a chapter would be. Requiring the anchor
    # makes a missing chapter come back empty, which is recoverable, instead
    # of wrong, which is not.
    chapter_parts: List[str] = []
    subpart = ""
    found_anchor = False
    # Scan back 10 lines, not 6, and allow a heading line up to 200 chars.
    # The old 110-char cap silently broke on Chapter VIII of the BNSS, whose
    # title runs to 113 characters ("Reciprocal Arrangements for Assistance in
    # Certain Matters and Procedure for Attachment and Forfeiture of
    # Property") - the scan bailed before reaching the "Chapter VIII" line, so
    # 14 sections were attributed to Chapter VII.
    for ln in reversed(lines[max(0, idx - 10):idx]):
        if not ln:
            continue
        if _CHAPTER_LINE.match(ln):
            chapter_parts.insert(0, ln)
            found_anchor = True
            break
        sub = _SUBPART_LINE.match(ln)
        if sub and not subpart:
            subpart = f"{sub.group('letter')}.-{sub.group('name').strip()}"
            found_anchor = True
            continue
        if len(ln) < 200 and not _STOP.match(ln) and not _FURNITURE.match(ln):
            chapter_parts.insert(0, ln)
        else:
            break

    # A chapter is returned ONLY when a "Chapter N" / "Part N" line was
    # actually found. Without that gate, any short line above the heading
    # becomes the chapter - which is how BSA s.4 and s.5 ended up with the
    # chapter "Closely connected facts", wiping out Chapter II.
    #
    # "Closely connected facts" is a real heading, just an UNLETTERED
    # subdivision - the BSA groups sections that way, where the BNSS uses
    # "A.-Summons". So an orphan heading with no chapter anchor above it is
    # treated as a subdivision instead: the nearest line to the heading
    # becomes the subpart, and forward-fill keeps the chapter it belongs to.
    if found_anchor and chapter_parts and _CHAPTER_LINE.match(chapter_parts[0]):
        chapter = " - ".join(p.rstrip(":").strip() for p in chapter_parts if p)
    else:
        chapter = ""
        if chapter_parts and not subpart:
            subpart = chapter_parts[-1].rstrip(":").strip()

    body_lines: List[str] = []
    for ln in lines[idx + 1:]:
        if _STOP.match(ln):
            break
        if ln:
            body_lines.append(ln)
    body = "\n".join(body_lines).strip()

    # A title that spilled onto the next line: first body line is short, has
    # no subsection marker, and the title looks truncated.
    if body_lines and not title:
        title, body = body_lines[0].rstrip("."), "\n".join(body_lines[1:]).strip()

    return {"chapter": chapter, "subpart": subpart,
            "title": title.rstrip("."), "body": body}


# --- fetching -------------------------------------------------------------

def fetch(url: str, cache: Path, delay: float, retries: int = 3) -> Optional[str]:
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")
    try:
        import httpx
    except ImportError:
        sys.exit("httpx is required: pip install httpx")

    headers = {"User-Agent": "Nyaya-Sathi-corpus-builder/1.0 (student project)"}
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as c:
                r = c.get(url, headers=headers)
            if r.status_code == 200:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(r.text, encoding="utf-8")
                time.sleep(delay)
                return r.text
            if r.status_code == 404:
                return None
            print(f"    HTTP {r.status_code} on {url}")
        except Exception as exc:
            print(f"    {type(exc).__name__} on {url}: {exc}")
        time.sleep(min(2 ** attempt, 8))
    return None


def scrape(act_key: str, limit: Optional[int], delay: float,
           refetch: bool) -> Tuple[List[dict], List[str]]:
    meta = ACTS[act_key]
    cache = CACHE_DIR / act_key
    if refetch and cache.exists():
        for f in cache.glob("*.html"):
            f.unlink()

    total = limit or meta["expect_sections"]
    rows: List[dict] = []
    missing: List[str] = []
    # The site prints the chapter heading only on the FIRST section of each
    # chapter - s.1 and s.173 carry theirs, s.2 to s.5 do not. So the chapter
    # is forward-filled: whatever was last seen applies until a new heading
    # appears. This only works because sections are walked in order, which is
    # also why the loop is sequential rather than concurrent.
    current_chapter = ""
    # Subdivision forward-fills too, but RESETS on a new chapter - otherwise
    # "A.-Summons" from Chapter VI leaks into Chapter VII.
    current_subpart = ""

    for n in range(1, total + 1):
        num = str(n)
        url = f"{BASE}/{meta['slug']}/{num}.php"
        raw = fetch(url, cache / f"{num}.html", delay)
        if not raw:
            missing.append(num)
            continue
        parsed = parse_section(raw, num)
        if not parsed or len(parsed["body"]) < 20:
            missing.append(num)
            continue
        if parsed["chapter"]:
            if parsed["chapter"] != current_chapter:
                current_subpart = ""
            current_chapter = parsed["chapter"]
        if parsed["subpart"]:
            current_subpart = parsed["subpart"]
        breadcrumb = " - ".join(
            b for b in (current_chapter, current_subpart) if b)
        rows.append({
            "chapter_title": breadcrumb,
            "Section": num,
            "section_title": parsed["title"],
            "section_desc": parsed["body"],
            # Citation points at India Code, not at the page this was read
            # from. The scrape is a convenience; the official text is the
            # thing a user should be sent to.
            "source": "advocatekhoj.com (text), India Code (citation)",
            "source_url": meta["india_code"],
        })
        if n % 25 == 0 or n == total:
            print(f"    {n}/{total}  ({len(rows)} parsed, {len(missing)} missing)")

    return rows, missing


# --- reporting ------------------------------------------------------------

def verify(act_key: str, rows: List[dict]) -> bool:
    meta = ACTS[act_key]
    line = "-" * 72
    by_num = {r["Section"]: r for r in rows}
    ok = True

    print(f"\n{line}\nVERIFICATION - {meta['name']}\n{line}")
    got, want = len(rows), meta["expect_sections"]
    verdict = "OK" if got == want else "INCOMPLETE"
    print(f"  sections parsed   {got} / {want}   {verdict}")
    if got != want:
        ok = False

    chapters = {r["chapter_title"] for r in rows if r["chapter_title"]}
    want_ch = meta["expect_chapters"]
    print(f"  distinct chapters {len(chapters)} / {want_ch}"
          + ("   OK" if len(chapters) == want_ch else "   check this"))
    no_chapter = [r["Section"] for r in rows if not r["chapter_title"]]
    if no_chapter:
        print(f"  sections with no chapter {len(no_chapter)}  {no_chapter[:12]}")
        # Forward-fill should leave none. Any here means a chapter heading was
        # missed on a page that opens a chapter, so the sections after it are
        # attributed to the PREVIOUS chapter - wrong breadcrumbs, not a
        # retrieval failure, so it warns rather than blocks.
    untitled = [r["Section"] for r in rows if not r["section_title"]]
    print(f"  untitled sections {len(untitled)}"
          + (f"  {untitled[:12]}" if untitled else ""))
    if len(untitled) > got * 0.1:
        ok = False

    print(f"\n  spot checks (sections repeal_map.json points at):")
    for num, expect in meta["spot_check"].items():
        row = by_num.get(num)
        if not row:
            print(f"    s.{num:<5} MISSING")
            ok = False
            continue
        hay = f"{row['section_title']} {row['section_desc'][:400]}".lower()
        hit = expect.lower() in hay
        if not hit:
            ok = False
        print(f"    s.{num:<5} {'match' if hit else 'MISMATCH':<9} "
              f"{row['section_title'][:52]!r}")
        if not hit:
            print(f"           expected to contain {expect!r}")

    lens = sorted(len(r["section_desc"]) for r in rows)
    if lens:
        print(f"\n  body length  min {lens[0]}  median {lens[len(lens)//2]}  "
              f"max {lens[-1]}")
    print(f"\n{line}")
    print("  PASS - safe to write" if ok else
          "  FAIL - do not ingest until this is resolved")
    print(f"{line}\n")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--act", required=True, choices=list(ACTS))
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch only --limit sections and print them")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N sections (default: all)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between requests (be polite)")
    ap.add_argument("--refetch", action="store_true",
                    help="clear the cache and fetch again")
    ap.add_argument("--force", action="store_true",
                    help="write the data file even if verification fails")
    args = ap.parse_args()

    meta = ACTS[args.act]
    limit = args.limit or (5 if args.dry_run else None)

    print(f"Scraping {meta['name']} from {BASE}/{meta['slug']}/")
    print(f"  cache: {CACHE_DIR / args.act}   delay: {args.delay}s")
    if not limit:
        est = meta["expect_sections"] * args.delay / 60
        print(f"  {meta['expect_sections']} sections, roughly {est:.0f} min "
              f"on a cold cache\n")

    rows, missing = scrape(args.act, limit, args.delay, args.refetch)

    if args.dry_run:
        for r in rows:
            print(f"\n  s.{r['Section']}  chapter={r['chapter_title']!r}")
            print(f"     title: {r['section_title']!r}")
            print(f"     body : {r['section_desc'][:200]!r}")
        if missing:
            print(f"\n  could not parse: {missing}")
        print("\n  Dry run - nothing written. Drop --dry-run to scrape all.")
        return

    if missing:
        print(f"\n  {len(missing)} section(s) not parsed: "
              f"{missing[:20]}{' ...' if len(missing) > 20 else ''}")

    ok = verify(args.act, rows)
    if not ok and not args.force:
        print("  Not writing. Inspect the cache, fix the parser, or pass --force.")
        return

    out = DATA_DIR / meta["file"]
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"  wrote {out.relative_to(BACKEND)}  ({len(rows)} sections)\n")
    print("  Next:")
    print("    python -m app.check_repeal_targets     # mappings now resolve?")
    print("    python -m app.validity --audit")
    print(f"    python -m app.ingest_hf_acts --write-local --vectors "
          f"--acts {args.act}   # embeds from the local file")


if __name__ == "__main__":
    main()