"""Canonical, direct links for statute citations.

The problem this solves
-----------------------
A source card used to link to an Indian Kanoon *search*:

    https://indiankanoon.org/search/?formInput=MV+Act+section+160

which lands the user on a page of 5,000+ results and makes them find the
section a second time. A citation should open the cited provision itself.

Indian Kanoon does host every section as its own document -
"Section 160 in The Motor Vehicles Act, 1988" is /doc/<tid>/ - but the tid is
an opaque number. It cannot be derived from the act name and section number;
it has to be looked up once. So:

  1. resolve() looks the section up through the Kanoon API (doctypes: laws),
     accepts a hit ONLY if its title is exactly "<Unit> <n> in <this act>",
     and stores the tid in backend/data/kanoon_source_ids.json. One billable
     search per section, ever. A wrong-section match is worse than no match,
     so a near miss is rejected rather than accepted.

  2. direct_url() is what as_source() calls. It never touches the network:
       - tid already known       -> https://indiankanoon.org/doc/<tid>/
       - act has its own per-section page (devgan.in, Constitution) -> that
       - otherwise               -> this backend's /sources/statute/<act>/<n>,
                                    which resolves and 302-redirects straight
                                    to the document. The user never sees a
                                    results list.

  3. If Kanoon has no page for a section (or the API is down / no token), the
     redirect endpoint serves the exact section text Nyaya Sathi cited, from
     the local corpus. Still the cited provision, still never a search page.

Run `python -m app.resolve_source_links` once to pre-resolve every section,
then commit kanoon_source_ids.json - after that every card is a direct
Kanoon link from the first render and no API call is spent at runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = DATA_DIR / "kanoon_source_ids.json"

KANOON_DOC_URL = "https://indiankanoon.org/doc/{tid}/"

# A section Kanoon genuinely doesn't have is retried after this long, not on
# every click - each attempt is a billed search.
NEGATIVE_TTL_SECONDS = 30 * 24 * 3600

_RESOLVE_TIMEOUT = 8.0


# ---------------------------------------------------------------------------
# Persistent cache
# ---------------------------------------------------------------------------
# {"version": 1, "ids": {"mva:160": {"docid": "1234", "title": "...", "at": 0}}}
# `title` is Kanoon's own title for the matched document, kept so anyone can
# audit a mapping by eye. `docid: null` is a remembered miss.

_file_lock = threading.Lock()
_cache: Optional[Dict[str, Dict[str, Any]]] = None
_key_locks: Dict[str, asyncio.Lock] = {}


def _load() -> Dict[str, Dict[str, Any]]:
    global _cache
    if _cache is None:
        with _file_lock:
            if _cache is None:
                try:
                    raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                    _cache = dict(raw.get("ids", {}))
                except FileNotFoundError:
                    _cache = {}
                except Exception as exc:
                    logger.warning("Could not read %s (%s); starting empty", CACHE_PATH, exc)
                    _cache = {}
    return _cache


def _save() -> None:
    """Atomic write: a crash mid-write must not corrupt the mapping file."""
    ids = _load()
    with _file_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"version": 1, "ids": dict(sorted(ids.items()))},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.replace(tmp, CACHE_PATH)


def _key(act_key: str, section: str) -> str:
    return f"{act_key}:{section}"


def cached_docid(act_key: str, section: str) -> Optional[str]:
    entry = _load().get(_key(act_key, section))
    return entry.get("docid") if entry else None


def _is_fresh_miss(entry: Optional[Dict[str, Any]]) -> bool:
    return bool(entry) and not entry.get("docid") and (
        time.time() - float(entry.get("at") or 0) < NEGATIVE_TTL_SECONDS
    )


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

def is_search_url(url: Optional[str]) -> bool:
    """True for any results-list URL - the thing a citation must never be."""
    if not url:
        return False
    p = urlparse(url)
    return "indiankanoon.org" in p.netloc and p.path.rstrip("/").endswith("/search")


def resolver_url(act_key: str, section: str) -> str:
    # Lazy: config raises without DATABASE_URL, and statutes.py (which
    # imports this module) must stay importable for the offline eval.
    try:
        from .config import settings
        base = (settings.public_api_url or "").rstrip("/")
    except Exception:
        base = os.getenv("PUBLIC_API_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/sources/statute/{quote(act_key, safe='')}/{quote(section, safe='')}"


def is_resolver_url(url: Optional[str]) -> bool:
    return bool(url) and "/sources/statute/" in url


def direct_url(doc: Dict[str, Any]) -> Optional[str]:
    """Best direct link for a statute doc. Synchronous, no network."""
    act_key, section = doc.get("act_key"), doc.get("section")
    if not act_key or not section:
        return doc.get("url") if not is_search_url(doc.get("url")) else None

    tid = cached_docid(act_key, section)
    if tid:
        return KANOON_DOC_URL.format(tid=tid)

    own = doc.get("url")
    if own and not is_search_url(own):
        return own

    return resolver_url(act_key, section)


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

_TITLE_RE = re.compile(r"^\s*(section|article|rule|order)\s+(\S+)\s+in\s+(.+?)\s*$", re.I)
_YEAR_RE = re.compile(r"\b(1[6-9]|20)\d\d\b")


def _norm_act(name: str, drop_year: bool = False) -> str:
    s = name.lower().replace("&", " and ")
    if drop_year:
        s = _YEAR_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(t for t in s.split() if t != "the")


def _norm_section(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def title_matches(kanoon_title: str, unit: str, section: str, act_name: str) -> bool:
    """Is this Kanoon document exactly <unit> <section> of <act_name>?

    Strict on purpose: "Section 16 in The Motor Vehicles Act, 1988" must not
    satisfy a lookup for section 160, and "Section 160 in The Railways Act"
    must not satisfy the MV Act.
    """
    m = _TITLE_RE.match(kanoon_title or "")
    if not m:
        return False
    k_unit, k_sec, k_act = m.groups()
    if k_unit.lower() != (unit or "Section").lower():
        return False
    if _norm_section(k_sec) != _norm_section(section):
        return False

    ours, theirs = _norm_act(act_name), _norm_act(k_act)
    if ours == theirs:
        return True
    # Kanoon omits the year on some acts ("Article 21 in Constitution of
    # India"). Compare without years only when one side has none; if both
    # carry a year they must agree, or the 1986 and 2019 Consumer Protection
    # Acts would be interchangeable.
    y_ours, y_theirs = _YEAR_RE.search(act_name), _YEAR_RE.search(k_act)
    if y_ours and y_theirs:
        return False
    return _norm_act(act_name, True) == _norm_act(k_act, True)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

async def resolve(doc: Dict[str, Any], *, force: bool = False) -> Optional[str]:
    """Kanoon tid for this statute section, looked up once and remembered.

    Returns None when Kanoon has no matching document or can't be reached;
    the caller falls back to the section text, never to a search page.
    """
    act_key, section = doc.get("act_key"), doc.get("section")
    if not act_key or not section or section.lower() == "preamble":
        return None

    key = _key(act_key, section)
    entry = _load().get(key)
    if entry and entry.get("docid"):
        return entry["docid"]
    if not force and _is_fresh_miss(entry):
        return None

    # Two clicks on the same new card must cost one search, not two.
    lock = _key_locks.setdefault(key, asyncio.Lock())
    async with lock:
        entry = _load().get(key)
        if entry and entry.get("docid"):
            return entry["docid"]
        if not force and _is_fresh_miss(entry):
            return None

        from . import kanoon  # lazy: keeps statutes importable with no API setup

        unit = doc.get("unit") or "Section"
        act_name = doc.get("act") or ""
        # Phrase query first (tightest ranking); the plain title as a second
        # try only if the first returned no exact match. Kanoon section
        # documents are titled "<Unit> <n> in The <Act>, <year>".
        queries = [f'"{unit} {section} in" {act_name}', f"{unit} {section} in The {act_name}"]
        hit = None
        for query in queries:
            try:
                results = await asyncio.wait_for(
                    kanoon.search(query, doctypes="doctypes: laws"), _RESOLVE_TIMEOUT
                )
            except Exception as exc:
                # Transient: do NOT remember as a miss, or one network blip
                # would hide the direct link for a month.
                logger.warning("Kanoon lookup for %s failed (%s)", key, exc)
                return None
            hit = next(
                (r for r in results
                 if title_matches(r.get("title", ""), unit, section, act_name)),
                None,
            )
            if hit:
                break
        _load()[key] = {
            "docid": hit["docid"] if hit else None,
            "title": hit["title"] if hit else None,
            "at": int(time.time()),
        }
        try:
            _save()
        except Exception as exc:
            logger.warning("Could not persist source id for %s (%s)", key, exc)

        if hit:
            logger.info("Resolved %s -> Kanoon doc %s (%s)", key, hit["docid"], hit["title"])
            return hit["docid"]
        logger.info("No Kanoon document matches %s (%s %s in %s)", key, unit, section, act_name)
        return None


# ---------------------------------------------------------------------------
# Upgrading links stored before this change
# ---------------------------------------------------------------------------
# Conversation payloads saved earlier carry search URLs. They are rewritten on
# read so old chats get direct links too - no migration needed.

_legacy_patterns: Optional[list] = None


def _legacy_table():
    global _legacy_patterns
    if _legacy_patterns is None:
        from . import statutes

        pats = []
        for act_key, meta in statutes.ACTS.items():
            tpl = meta.get("url_template")
            if isinstance(tpl, str) and "{section}" in tpl and is_search_url(tpl.replace("{section}", "1")):
                rx = re.escape(tpl).replace(re.escape("{section}"), r"([^+&]+)")
                pats.append((re.compile("^" + rx + "$", re.I), act_key))
        _legacy_patterns = pats
    return _legacy_patterns


def _legacy_to_doc(url: str) -> Optional[Dict[str, Any]]:
    from . import statutes

    # 1. Per-act templates, hand-written and statutes_ext ones alike:
    #    ...formInput=section+160+indian+contract+act
    for rx, act_key in _legacy_table():
        m = rx.match(url)
        if m:
            return statutes.get(act_key, m.group(1))

    # 2. as_source's old fallback: ...formInput=MV+Act+section+160
    q = parse_qs(urlparse(url).query).get("formInput", [""])[0].strip()
    m = re.match(r"^(.*?)\s+section\s+(\S+)$", q, re.I)
    if m:
        short = m.group(1).strip().lower()
        for act_key, meta in statutes.ACTS.items():
            if (meta.get("short") or "").lower() == short:
                return statutes.get(act_key, m.group(2))

    return None


def _upgrade_url(url: str) -> str:
    if is_search_url(url):
        doc = _legacy_to_doc(url)
        return direct_url(doc) if doc else url
    if is_resolver_url(url):
        # Resolved since it was stored? Hand out the Kanoon link directly.
        parts = urlparse(url).path.rstrip("/").split("/")
        if len(parts) >= 2:
            tid = cached_docid(unquote(parts[-2]), unquote(parts[-1]))
            if tid:
                return KANOON_DOC_URL.format(tid=tid)
    return url


def upgrade_links(obj: Any) -> Any:
    """Walk a stored payload and replace every stale source URL in place."""
    try:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "url" and isinstance(v, str):
                    obj[k] = _upgrade_url(v)
                else:
                    upgrade_links(v)
        elif isinstance(obj, list):
            for item in obj:
                upgrade_links(item)
    except Exception as exc:  # a link upgrade must never break loading a chat
        logger.warning("Link upgrade skipped (%s)", exc)
    return obj