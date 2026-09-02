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

from typing import Any, Dict, List, Optional

import httpx

from .config import settings

KANOON_TIMEOUT = 20.0


class KanoonError(RuntimeError):
    pass


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
        results.append(
            {
                "docid": str(doc.get("tid")),
                "title": doc.get("title"),
                "court": doc.get("docsource"),
                "date": doc.get("publishdate"),
                "snippet": doc.get("headline"),
                "url": f"https://indiankanoon.org/doc/{doc.get('tid')}/",
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
