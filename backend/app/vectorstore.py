"""Pinecone index for statutes and cached judgments.

Design notes worth keeping in mind when this gets extended:

*Deterministic IDs.* A vector's ID is derived from what it contains -
`statute:bns:103:v1:0` - never a UUID. Re-running ingestion is therefore an
upsert that overwrites in place instead of a second copy. This is what makes
the future update agent safe: re-indexing an amended section cannot leave a
stale twin behind.

*Versions are explicit, not implied.* When a section is amended, the old text
gets `is_current: False` and an `effective_to` date; the new text arrives as
`v2` with `is_current: True`. Both stay retrievable - an advocate arguing
about conduct from 2019 needs the 2019 text - but ordinary queries filter to
current law, so an amended-away provision can never be presented as live.

*Namespaces separate corpora, not jurisdictions.* Statutes and judgments live
in different namespaces because they are refreshed on completely different
schedules. Jurisdiction is a metadata filter, not a namespace, because a
single query often needs central law and state law together.

*A corpus you can throw away.* Acts ingested from a third-party dataset go
into their own namespace (NS_STATUTE_EXT) rather than mixing into the
hand-checked one. Deleting that namespace deletes exactly those vectors and
nothing else, which is what makes trying an ingest a reversible decision. Note
that a git branch does NOT do this - Pinecone is external state and survives
`git branch -D`. Rollback is `delete_namespace()` or `--rollback`, never a
branch delete.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .chunking import Chunk
from .embeddings import DIMENSIONS

logger = logging.getLogger(__name__)

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


_ST = _settings()
INDEX_NAME = (getattr(_ST, "pinecone_index", "") if _ST else "") or \
    os.getenv("PINECONE_INDEX", "nyaya-sathi")
NS_STATUTE = "statutes"
NS_JUDGMENT = "judgments"

# Extension corpus. Separate namespace so it is independently deletable.
NS_STATUTE_EXT = (getattr(_ST, "pinecone_ns_statute_ext", "") if _ST else "") or \
    os.getenv("PINECONE_NS_STATUTE_EXT", "statutes-ext")

# Every namespace dense retrieval should search, in order. Pinecone queries one
# namespace per call, so this is a loop at read time - two cheap ANN lookups
# reusing one embedding, not two embedding calls.
def statute_namespaces() -> List[str]:
    raw = (getattr(_ST, "pinecone_statute_namespaces", "") if _ST else "") or \
        os.getenv("PINECONE_STATUTE_NAMESPACES", "")
    if raw:
        return [n.strip() for n in raw.split(",") if n.strip()]
    return [NS_STATUTE, NS_STATUTE_EXT]

# Pinecone rejects an upsert batch over ~4MB. 100 vectors of 768 floats plus
# metadata sits well inside that.
UPSERT_BATCH = 100

_OPEN_ENDED = 99991231


class VectorStoreError(RuntimeError):
    pass


def _client():
    try:
        from pinecone import Pinecone
    except ImportError as exc:  # pragma: no cover
        raise VectorStoreError(
            "pinecone is not installed. Run: pip install pinecone"
        ) from exc
    st = _settings()
    key = (getattr(st, "pinecone_api_key", "") if st else "") or \
        os.getenv("PINECONE_API_KEY")
    if not key:
        raise VectorStoreError("PINECONE_API_KEY is not set. Add it to backend/.env")
    return Pinecone(api_key=key)


def ensure_index(*, cloud: str = "", region: str = ""):
    """Create the index if it isn't there, then return a handle."""
    st = _settings()
    cloud = cloud or (getattr(st, "pinecone_cloud", "") if st else "") or "aws"
    region = region or (getattr(st, "pinecone_region", "") if st else "") or "us-east-1"
    pc = _client()
    existing = [i["name"] for i in pc.list_indexes()]
    if INDEX_NAME not in existing:
        from pinecone import ServerlessSpec

        logger.info("Creating Pinecone index %s (%d dims)", INDEX_NAME, DIMENSIONS)
        pc.create_index(
            name=INDEX_NAME,
            dimension=DIMENSIONS,
            metric="cosine",
            spec=ServerlessSpec(cloud=cloud, region=region),
        )
    return pc.Index(INDEX_NAME)


# --- writing --------------------------------------------------------------

def _sanitise(md: Dict[str, Any]) -> Dict[str, Any]:
    """Pinecone metadata takes str, int, float, bool and list-of-str only.
    Nulls are dropped rather than coerced, so a missing value and an empty
    value stay distinguishable."""
    out: Dict[str, Any] = {}
    for k, v in md.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
            out[k] = list(v)
        else:
            out[k] = str(v)
    return out


def upsert(index, chunks: Sequence[Chunk], vectors: Sequence[List[float]],
           namespace: str, *, progress: bool = False) -> int:
    if len(chunks) != len(vectors):
        raise VectorStoreError(
            f"{len(chunks)} chunks but {len(vectors)} vectors - refusing to "
            "upsert a misaligned batch."
        )
    payload = [
        {"id": c.chunk_id, "values": v, "metadata": _sanitise(c.metadata)}
        for c, v in zip(chunks, vectors)
    ]
    done = 0
    for start in range(0, len(payload), UPSERT_BATCH):
        batch = payload[start : start + UPSERT_BATCH]
        index.upsert(vectors=batch, namespace=namespace)
        done += len(batch)
        if progress:
            print(f"    upserted {done}/{len(payload)}", flush=True)
    return done


def supersede(index, parent_id: str, namespace: str, effective_to: int) -> None:
    """Mark every existing vector of a document as no longer current.

    Call this *before* upserting a new version. Pinecone has no transaction, so
    the order matters: closing the old version first means a query landing
    mid-update sees no current version briefly, rather than two.
    """
    res = index.query(
        vector=[0.0] * DIMENSIONS,
        top_k=100,
        namespace=namespace,
        filter={"parent_id": {"$eq": parent_id}, "is_current": {"$eq": True}},
        include_metadata=True,
    )
    for match in res.get("matches", []):
        index.update(
            id=match["id"],
            set_metadata={"is_current": False, "legal_status": "amended",
                          "effective_to": effective_to},
            namespace=namespace,
        )


def delete_parent(index, parent_id: str, namespace: str) -> None:
    """Remove every chunk of a document. For re-chunking, where old chunk
    indices may no longer exist and would otherwise be orphaned."""
    index.delete(filter={"parent_id": {"$eq": parent_id}}, namespace=namespace)


def delete_namespace(index, namespace: str) -> None:
    """Drop an entire namespace.

    This is the rollback path for an extension ingest: one call, everything
    in that namespace gone, the hand-checked `statutes` namespace untouched.
    Pinecone treats deleting a namespace that does not exist as a no-op on
    some plans and a 404 on others, so the caller should tolerate both.
    """
    index.delete(delete_all=True, namespace=namespace)


def delete_batch(index, corpus_batch: str, namespace: str) -> None:
    """Delete by the `corpus_batch` metadata tag.

    Finer-grained than dropping a namespace: use this when the extension was
    ingested into the shared `statutes` namespace instead of its own, so a
    namespace delete would take the original corpus with it.

    Serverless indexes do not support delete-by-filter on every plan. If this
    raises, fall back to delete_namespace or to deleting the known chunk ids.
    """
    index.delete(filter={"corpus_batch": {"$eq": corpus_batch}}, namespace=namespace)


def fetch_existing_ids(index, ids: Sequence[str], namespace: str) -> set:
    """Which of these chunk ids are already in the index?

    This is what makes ingestion idempotent without a second source of truth.
    Chunk ids are deterministic (statute:pocso:4:v1:0), so a re-run can ask
    Pinecone directly what it already has and skip the embedding call for
    those - the embedding call being the only expensive part.

    Fetch is capped per request, so this batches.
    """
    have = set()
    ids = list(ids)
    for start in range(0, len(ids), 100):
        batch = ids[start : start + 100]
        try:
            res = index.fetch(ids=batch, namespace=namespace)
        except Exception as exc:
            logger.warning("fetch failed (%s); treating batch as absent", exc)
            continue
        vectors = getattr(res, "vectors", None)
        if vectors is None and isinstance(res, dict):
            vectors = res.get("vectors") or {}
        have.update((vectors or {}).keys())
    return have


# --- reading --------------------------------------------------------------

def build_filter(
    *,
    current_only: bool = True,
    act_keys: Optional[Iterable[str]] = None,
    jurisdictions: Optional[Iterable[str]] = None,
    court_levels: Optional[Iterable[str]] = None,
    as_of: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble a Pinecone metadata filter.

    `as_of` (YYYYMMDD) asks for the law as it stood on a date, which is a
    different question from "current law" and the one that matters when
    advising on past conduct.
    """
    f: Dict[str, Any] = {}
    if as_of is not None:
        f["effective_from"] = {"$lte": as_of}
        f["effective_to"] = {"$gte": as_of}
    elif current_only:
        f["is_current"] = {"$eq": True}
    if act_keys:
        f["act_key"] = {"$in": list(act_keys)}
    if jurisdictions:
        f["jurisdiction"] = {"$in": list(jurisdictions)}
    if court_levels:
        f["court_level"] = {"$in": list(court_levels)}
    return f


def query_many(index, vector: List[float], *, namespaces: Sequence[str],
               top_k: int = 5, filter: Optional[Dict[str, Any]] = None
               ) -> List[Dict[str, Any]]:
    """Query several namespaces with one vector and merge by score.

    A namespace that does not exist yet returns nothing rather than raising,
    so this stays safe before the extension corpus has been ingested.
    """
    out: List[Dict[str, Any]] = []
    for ns in namespaces:
        try:
            out.extend(query(index, vector, namespace=ns, top_k=top_k, filter=filter))
        except Exception as exc:
            logger.warning("Namespace %s unavailable (%s); skipping", ns, exc)
    out.sort(key=lambda m: m.get("_score", 0.0), reverse=True)
    return out[:top_k]


def query(index, vector: List[float], *, namespace: str, top_k: int = 5,
          filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    res = index.query(
        vector=vector,
        top_k=top_k,
        namespace=namespace,
        filter=filter or None,
        include_metadata=True,
    )
    out = []
    for m in res.get("matches", []):
        md = dict(m.get("metadata") or {})
        md["_score"] = m.get("score", 0.0)
        md["_id"] = m.get("id")
        out.append(md)
    return out


def stats(index) -> Dict[str, Any]:
    return index.describe_index_stats()