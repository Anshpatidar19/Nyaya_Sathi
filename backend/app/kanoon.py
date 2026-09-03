"""Thin client around the Indian Kanoon API.

Indian Kanoon (https://api.indiankanoon.org) is used as the retrieval
dataset for this platform: it gives full judgment/statute text, search,
and a structural layer (facts / issues / arguments / precedent analysis /
conclusion per paragraph, plus precedent-citation sentiment) that this
app reuses rather than rebuilding a legal NLP pipeline from scratch.

All calls require an API token, passed as an Authorization header.
Sign up for access at https://api.indiankanoon.org and set
INDIAN_KANOON_API_TOKEN in backend/.env.

Docs referenced for these endpoints:
  POST /search/?formInput=<query>&pagenum=<n>       -> search results
  POST /doc/<docid>/                                 -> full document + structure
  POST /docmeta/<docid>/                             -> metadata only
"""

import html
import re
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

KANOON_TIMEOUT = 20.0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class KanoonError(RuntimeError):
    pass


def strip_html(raw: str) -> str:
    """Kanoon returns judgment text as HTML; flatten it to plain text.

    Used both for search headlines (which contain <b> match highlights) and
    for full documents before they're sent to the synthesis agent.
    """
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


async def search(query: str, page: int = 0) -> List[Dict[str, Any]]:
    """Search Indian Kanoon and return a simplified list of results."""
    url = f"{settings.indian_kanoon_base_url}/search/"
    params = {"formInput": query, "pagenum": page}
    async with httpx.AsyncClient(timeout=KANOON_TIMEOUT) as client:
        resp = await client.post(url, headers=_headers(), params=params)
        resp.raise_for_status()
        data = resp.json()

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
                "snippet": strip_html(doc.get("headline") or ""),
                # Public, human-readable page — this is what the UI links to.
                "url": f"https://indiankanoon.org/doc/{tid}/",
            }
        )
    return results


async def get_document(docid: str) -> Dict[str, Any]:
    """Fetch full document text plus the structural paragraph analysis
    (facts / issues / arguments / precedent analysis / conclusion) and
    precedent citation classification that Indian Kanoon provides.
    """
    url = f"{settings.indian_kanoon_base_url}/doc/{docid}/"
    async with httpx.AsyncClient(timeout=KANOON_TIMEOUT) as client:
        resp = await client.post(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def get_doc_metadata(docid: str) -> Dict[str, Any]:
    url = f"{settings.indian_kanoon_base_url}/docmeta/{docid}/"
    async with httpx.AsyncClient(timeout=KANOON_TIMEOUT) as client:
        resp = await client.post(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()