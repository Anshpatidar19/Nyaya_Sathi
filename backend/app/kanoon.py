"""Thin client around the Indian Kanoon API.

Kanoon is metered and prepaid, so this module is written to spend as few
calls as possible per question:

  * search()          one call, scoped by court where we know the user's state
  * fragment()        pulls only the passages matching the query, instead of
                      downloading a 1,000-page judgment and truncating it
  * an in-process cache means the same judgment is never fetched twice
  * rank_by_citations() sorts search hits by how often they've been cited, so
                      the calls we do spend go on the judgments that matter

Endpoints:
  POST /search/?formInput=<query>&pagenum=<n>        search results
  POST /doc/<docid>/                                 full document
  POST /docfragment/<docid>/?formInput=<query>       matching passages only
  POST /docmeta/<docid>/                             metadata only
"""

import html
import logging
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

KANOON_TIMEOUT = 20.0
_CACHE_MAX = 128

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Judgments and statutes are immutable, so caching within a process is safe
# and stops repeat questions from costing anything.
_doc_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

# Counters so you can see what a session actually cost.
CALL_COUNTS = {"search": 0, "fragment": 0, "doc": 0, "meta": 0, "cache_hits": 0}

# The /docfragment/ endpoint is not enabled on every Kanoon token. When it
# isn't, it answers HTTP 200 with {"errmsg": "Error in evaluting the
# fragments"} - a success status carrying a failure. Two of those in a row
# and we stop calling it for the life of the process, because every attempt
# is billed and every attempt is wasted.
FRAGMENTS_ENABLED = getattr(settings, "kanoon_use_fragments", True)
_fragment_failures = 0
_FRAGMENT_FAILURE_LIMIT = 2


class KanoonError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Court scoping
# ---------------------------------------------------------------------------
# Kanoon's `doctypes:` filter accepts court slugs. Scoping a search to the
# user's own High Court plus the Supreme Court returns precedent that actually
# binds where they live, instead of a random High Court three states away.

STATE_COURTS = {
    "andhra pradesh": "andhra", "arunachal pradesh": "gauhati", "assam": "gauhati",
    "bihar": "patna", "chhattisgarh": "chattisgarh", "delhi": "delhi",
    "goa": "bombay", "gujarat": "gujarat", "haryana": "punjab",
    "himachal pradesh": "himachal", "jammu and kashmir": "jk", "jharkhand": "jharkhand",
    "karnataka": "karnataka", "kerala": "kerala", "madhya pradesh": "madhyapradesh",
    "maharashtra": "bombay", "manipur": "manipur", "meghalaya": "meghalaya",
    "mizoram": "gauhati", "nagaland": "gauhati", "odisha": "orissa",
    "orissa": "orissa", "punjab": "punjab", "rajasthan": "rajasthan",
    "sikkim": "sikkim", "tamil nadu": "chennai", "telangana": "telangana",
    "tripura": "tripura", "uttar pradesh": "allahabad", "uttarakhand": "uttaranchal",
    "west bengal": "kolkata",
}


def court_scope(state: Optional[str]) -> str:
    """Build a doctypes filter for the user's jurisdiction."""
    if not state:
        return "doctypes: judgments"
    slug = STATE_COURTS.get(state.strip().lower())
    if not slug:
        return "doctypes: judgments"
    return f"doctypes: supremecourt,{slug}"


def strip_html(raw: str) -> str:
    """Kanoon returns text as HTML; flatten it to plain text."""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _headers() -> Dict[str, str]:
    if not settings.indian_kanoon_api_token:
        raise KanoonError(
            "INDIAN_KANOON_API_TOKEN is not set. Add it to backend/.env - "
            "see .env.example."
        )
    return {"Authorization": f"Token {settings.indian_kanoon_api_token}"}


async def _post(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{settings.indian_kanoon_base_url}{path}"
    async with httpx.AsyncClient(timeout=KANOON_TIMEOUT) as client:
        resp = await client.post(url, headers=_headers(), params=params or {})
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search(
    query: str,
    page: int = 0,
    state: Optional[str] = None,
    doctypes: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search Kanoon. One billable call.

    `state` scopes results to that state's High Court plus the Supreme Court.
    `doctypes` overrides the filter entirely - pass "doctypes: laws" to search
    bare acts rather than judgments.
    """
    scoped = query
    if doctypes:
        scoped = f"{query} {doctypes}"
    elif state:
        scoped = f"{query} {court_scope(state)}"

    CALL_COUNTS["search"] += 1
    data = await _post("/search/", {"formInput": scoped, "pagenum": page})

    results = []
    for doc in data.get("docs", []):
        tid = doc.get("tid")
        if tid is None:
            continue
        results.append(
            {
                "docid": str(tid),
                "title": strip_html(doc.get("title") or "") or "Untitled",
                "court": doc.get("docsource"),
                "date": doc.get("publishdate"),
                # The headline is a query-matched snippet. It's already paid
                # for by the search call, so it's free context and free UI copy.
                "snippet": strip_html(doc.get("headline") or ""),
                "cited_by": int(doc.get("numcitedby") or 0),
                "num_cites": int(doc.get("numcites") or 0),
                "url": f"https://indiankanoon.org/doc/{tid}/",
            }
        )
    return results


def rank_by_citations(results: List[Dict[str, Any]], top: int = 3) -> List[Dict[str, Any]]:
    """Re-rank search hits by precedential weight.

    Kanoon orders by keyword relevance. A judgment cited 400 times is more
    likely to state settled law than one cited twice, so blending citation
    count into the ordering puts the calls we're about to spend on the
    judgments most worth reading. Original rank still counts - a landmark
    case about something else is not useful.
    """
    scored = []
    for rank, r in enumerate(results):
        # Relevance decays with rank; citations add weight on a log-ish scale.
        relevance = 1.0 / (rank + 1)
        weight = min(r.get("cited_by", 0), 1000) / 1000.0
        scored.append((relevance + 0.6 * weight, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top]]


# ---------------------------------------------------------------------------
# Document text
# ---------------------------------------------------------------------------

def _harvest_text(node: Any, out: List[str], depth: int = 0) -> None:
    """Pull every substantial string out of a JSON response.

    The fragment endpoint's exact field names aren't documented, and guessing
    them wrong is expensive: a miss used to trigger a full /doc/ fetch, so we
    paid twice per judgment. Walking the structure means we get the text
    whatever it's called.
    """
    if depth > 6:
        return
    if isinstance(node, str):
        cleaned = strip_html(node)
        if len(cleaned) > 40:          # skip ids, dates, court names
            out.append(cleaned)
    elif isinstance(node, list):
        for item in node:
            _harvest_text(item, out, depth + 1)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key in ("tid", "docid", "publishdate", "docsource", "numcites",
                       "numcitedby", "covers", "url"):
                continue
            _harvest_text(value, out, depth + 1)


class FragmentUnavailable(KanoonError):
    """The fragment endpoint errored. Callers may fall back to /doc/."""


async def fragment(docid: str, query: str) -> str:
    """Fetch only the passages of a judgment that match the query.

    This is the win over get_document(). A judgment can run to hundreds of
    pages; the first 6,000 characters are cause title, counsel appearances
    and procedural history - the least useful part. Fragments return the
    passages that actually matched.

    Returns the text, or "" if the endpoint answered but had nothing usable.
    Raises FragmentUnavailable only if the CALL itself failed - that
    distinction matters, because falling back to /doc/ costs a second call
    and must not happen just because a response was empty.
    """
    cache_key = f"frag:{docid}:{query[:80]}"
    if cache_key in _doc_cache:
        CALL_COUNTS["cache_hits"] += 1
        _doc_cache.move_to_end(cache_key)
        return _doc_cache[cache_key].get("text", "")

    global FRAGMENTS_ENABLED, _fragment_failures

    if not FRAGMENTS_ENABLED:
        raise FragmentUnavailable("fragment endpoint disabled for this session")

    try:
        CALL_COUNTS["fragment"] += 1
        data = await _post(f"/docfragment/{docid}/", {"formInput": query})
    except Exception as exc:
        logger.info("Fragment call failed for %s (%s)", docid, exc)
        raise FragmentUnavailable(str(exc)) from exc

    # HTTP 200 carrying an error body - the endpoint isn't available to us.
    errmsg = data.get("errmsg") if isinstance(data, dict) else None
    if errmsg:
        _fragment_failures += 1
        if _fragment_failures >= _FRAGMENT_FAILURE_LIMIT:
            FRAGMENTS_ENABLED = False
            logger.warning(
                "Disabling /docfragment/ for this session after %d failures "
                "(%r). Falling back to full documents. If your Kanoon plan "
                "includes fragments, set KANOON_USE_FRAGMENTS=true and check "
                "the query syntax.",
                _fragment_failures, errmsg,
            )
        raise FragmentUnavailable(str(errmsg))

    _fragment_failures = 0   # a good response resets the breaker

    parts: List[str] = []
    _harvest_text(data, parts)

    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)

    text = "\n\n".join(unique).strip()

    if not text:
        # 200 OK but nothing usable. Log the shape once so the parser can be
        # tightened, but do NOT fall back - that would double the bill.
        logger.warning(
            "Fragment for %s returned no text. Response keys: %s",
            docid, list(data.keys()) if isinstance(data, dict) else type(data).__name__,
        )

    _remember(cache_key, {"text": text})
    return text


async def get_document(docid: str) -> Dict[str, Any]:
    """Fetch a full document. Cached, because judgments don't change."""
    if docid in _doc_cache:
        CALL_COUNTS["cache_hits"] += 1
        _doc_cache.move_to_end(docid)
        return _doc_cache[docid]

    CALL_COUNTS["doc"] += 1
    data = await _post(f"/doc/{docid}/")
    _remember(docid, data)
    return data


async def get_doc_metadata(docid: str) -> Dict[str, Any]:
    CALL_COUNTS["meta"] += 1
    return await _post(f"/docmeta/{docid}/")


def _remember(key: str, value: Dict[str, Any]) -> None:
    _doc_cache[key] = value
    _doc_cache.move_to_end(key)
    while len(_doc_cache) > _CACHE_MAX:
        _doc_cache.popitem(last=False)


def usage() -> Dict[str, Any]:
    """Billable call counts for this process. Useful for watching spend."""
    return {**CALL_COUNTS, "fragments_enabled": FRAGMENTS_ENABLED}