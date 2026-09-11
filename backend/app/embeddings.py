"""Gemini embeddings for the vector index.

One thing here is easy to get wrong and expensive to debug: Gemini's embedding
endpoint takes a `task_type`, and documents and queries must use *different*
values. Embedding a query with RETRIEVAL_DOCUMENT puts it in the wrong region
of the space and quietly costs you a chunk of recall, with no error to notice.
So there are two functions rather than one with a flag, and the task type is
not a caller's choice.

Model: text-embedding-004, 768 dimensions. Same API key as the rest of the
Gemini work, so nothing new to configure or rotate.

The key is sent as the `x-goog-api-key` HEADER, never as a `?key=` query
parameter. That is not a style preference. httpx logs the full request URL at
INFO level, so a key in the query string is printed on every single embedding
call - and an ingestion run prints it dozens of times, into a terminal that
routinely gets pasted into a chat, an issue, or a screenshot. A header is not
logged. Gemini accepts both, so there is no reason to use the one that leaks.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from typing import Iterable, List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

# text-embedding-004 was retired; gemini-embedding-001 replaces it.
#
# The model defaults to 3072 dimensions and supports Matryoshka truncation to
# smaller sizes. We ask for 768: a quarter of the storage for a reported ~0.3%
# quality loss, and it keeps the Pinecone index small.
#
# IMPORTANT: only 3072-dimension output is pre-normalized by the API. At any
# other size the vectors come back with varying magnitudes, which distorts
# cosine similarity - silently, with no error, just worse results. So we
# normalize here. If you ever change OUTPUT_DIM to 3072 the normalization
# becomes a no-op, which is harmless.
MODEL = "gemini-embedding-001"
DIMENSIONS = 768
_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"

# Gemini caps a batch embed request at 100 inputs.
# Free-tier quota is tight and a batch of N counts as N requests against it.
# Smaller batches with pacing between them survive the limit; override with
# EMBED_BATCH_SIZE / EMBED_PAUSE_SECONDS if you enable billing.
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "20"))
PAUSE_SECONDS = float(os.getenv("EMBED_PAUSE_SECONDS", "1.5"))
_TIMEOUT = 60.0
_MAX_RETRIES = 7


class EmbeddingError(RuntimeError):
    pass


def _settings():
    """Read config the way the rest of the app does.

    `.env` is loaded by pydantic-settings into the Settings object, NOT into
    os.environ, so os.getenv() alone returns nothing for a key that is only in
    the file. Falling back to os.getenv keeps this module usable standalone.
    """
    try:
        from .config import settings
        return settings
    except Exception:
        return None


def _api_key() -> str:
    st = _settings()
    key = (getattr(st, "gemini_api_key", "") if st else "") or \
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise EmbeddingError(
            "GEMINI_API_KEY is not set. Add it to backend/.env - and if it has "
            "ever been committed, rotate it first."
        )
    return key


def _retry_after(response) -> Optional[float]:
    """Pull retryDelay ('37s') out of a 429 body, if present."""
    try:
        for detail in response.json().get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if delay:
                return float(str(delay).rstrip("s")) + 1.0
    except Exception:
        pass
    return None


def _redact(text: str) -> str:
    """Strip anything key-shaped out of an error string before it is raised.

    Google echoes the request URL back inside some error bodies, so even with
    header auth a 400 can carry a key into a traceback.
    """
    text = re.sub(r"(?i)(key=)[A-Za-z0-9_\-.]{8,}", r"\1<redacted>", text)
    return re.sub(r"\bAQ\.[A-Za-z0-9_\-]{8,}", "<redacted>", text)


def _post(url: str, payload: dict) -> dict:
    """POST with backoff. 429 and 5xx are retried; 4xx are not, because a bad
    request will fail identically every time and retrying just hides it."""
    last: Optional[Exception] = None
    headers = {"x-goog-api-key": _api_key(),
               "Content-Type": "application/json"}
    for attempt in range(_MAX_RETRIES):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                r = client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                # Google returns a RetryInfo with the delay it actually wants.
                # Honouring it beats blind doubling, which either waits too
                # little and burns a retry or too long and stalls the run.
                wait = _retry_after(r) or min(2 ** attempt, 64)
                logger.warning("Embedding API %s, retrying in %ss", r.status_code, wait)
                time.sleep(wait)
                last = EmbeddingError(
                    f"HTTP {r.status_code}: {_redact(r.text[:200])}")
                continue
            raise EmbeddingError(
                f"HTTP {r.status_code}: {_redact(r.text[:300])}")
        except httpx.RequestError as exc:
            wait = 2 ** attempt
            logger.warning("Embedding request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
            last = exc
    raise EmbeddingError(f"Embedding failed after {_MAX_RETRIES} attempts: {last}")


def _normalize(values: List[float]) -> List[float]:
    """Scale to unit length. Required at any output dimensionality other than
    3072 - see the note on MODEL above."""
    mag = math.sqrt(sum(v * v for v in values))
    if mag == 0:
        return values
    return [v / mag for v in values]


def _embed(texts: Sequence[str], task_type: str) -> List[List[float]]:
    if not texts:
        return []
    # No `?key=` - see the module docstring. The key travels in a header.
    url = f"{_ENDPOINT}:batchEmbedContents"
    payload = {
        "requests": [
            {
                "model": f"models/{MODEL}",
                "content": {"parts": [{"text": t}]},
                "taskType": task_type,
                "outputDimensionality": DIMENSIONS,
            }
            for t in texts
        ]
    }
    data = _post(url, payload)
    vectors = [_normalize(e["values"]) for e in data.get("embeddings", [])]
    if len(vectors) != len(texts):
        raise EmbeddingError(
            f"Asked for {len(texts)} embeddings, got {len(vectors)}. "
            "Refusing to continue - a silent misalignment here would attach "
            "the wrong vector to the wrong section."
        )
    return vectors


def embed_documents(texts: Sequence[str], *, progress: bool = False) -> List[List[float]]:
    """Embed corpus text. Batched; safe to call with the whole corpus."""
    out: List[List[float]] = []
    total = len(texts)
    for start in range(0, total, BATCH_SIZE):
        batch = list(texts[start : start + BATCH_SIZE])
        out.extend(_embed(batch, "RETRIEVAL_DOCUMENT"))
        if progress:
            print(f"    embedded {min(start + BATCH_SIZE, total)}/{total}", flush=True)
        if PAUSE_SECONDS and start + BATCH_SIZE < total:
            time.sleep(PAUSE_SECONDS)
    return out


def embed_documents_batched(texts: Sequence[str]):
    """Yield (offset, vectors) per batch so the caller can persist as it goes.

    Embedding everything before writing anything means one 429 at batch 25
    discards 24 batches of paid-for work. Streaming lets ingestion resume.
    """
    total = len(texts)
    for start in range(0, total, BATCH_SIZE):
        batch = list(texts[start : start + BATCH_SIZE])
        yield start, _embed(batch, "RETRIEVAL_DOCUMENT")
        if PAUSE_SECONDS and start + BATCH_SIZE < total:
            time.sleep(PAUSE_SECONDS)


def embed_query(text: str) -> List[float]:
    """Embed a user question. Different task type from documents - see module
    docstring."""
    return _embed([text], "RETRIEVAL_QUERY")[0]


def self_test() -> None:
    """Confirm the key works and the dimension is what the index expects."""
    v = embed_query("punishment for criminal breach of trust")
    if len(v) != DIMENSIONS:
        raise EmbeddingError(f"Expected {DIMENSIONS} dims, got {len(v)}")
    mag = math.sqrt(sum(x * x for x in v))
    if abs(mag - 1.0) > 1e-3:
        raise EmbeddingError(f"Vector is not unit length ({mag:.4f})")
    print(f"Embeddings OK - {MODEL}, {len(v)} dimensions, normalized")


if __name__ == "__main__":
    self_test()