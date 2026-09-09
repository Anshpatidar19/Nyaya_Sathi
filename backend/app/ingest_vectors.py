"""Build and refresh the Pinecone index.

    python -m app.ingest_vectors --dry-run      # chunk and report, no API calls
    python -m app.ingest_vectors --statutes     # embed and upsert bare acts
    python -m app.ingest_vectors --statutes --force   # re-embed unchanged too
    python -m app.ingest_vectors --stats        # what's in the index now

Incremental by default: a section whose content_hash already matches what's
in the index is skipped without an embedding call. That is what makes the
future update agent cheap - a daily run over 2,900 sections costs nothing
when nothing has changed.

NOTE this is a separate script from ingest_bare_acts. That one regenerates
backend/data/*.json from Hugging Face and will overwrite a hand-curated
ipc.json. This one only reads those files.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

from . import chunking, embeddings, statutes, vectorstore


def _statute_docs() -> List[Dict[str, Any]]:
    """Reuse the BM25 index's normalised docs so both retrievers agree on what
    a section is. One definition of identity, not two."""
    return statutes._index()["docs"]


def _existing_hashes(index, namespace: str) -> Dict[str, str]:
    """chunk_id -> content_hash for everything already indexed.

    Pinecone has no scan, so this pages through with a zero-vector query. Fine
    at this corpus size; if the corpus grows past ~10k chunks, keep a local
    manifest file instead.
    """
    out: Dict[str, str] = {}
    try:
        res = index.query(
            vector=[0.0] * embeddings.DIMENSIONS,
            top_k=10000,
            namespace=namespace,
            include_metadata=True,
        )
    except Exception as exc:
        print(f"  could not read existing vectors ({exc}); treating index as empty")
        return out
    for m in res.get("matches", []):
        md = m.get("metadata") or {}
        if "content_hash" in md:
            out[m["id"]] = md["content_hash"]
    return out


def ingest_statutes(*, dry_run: bool, force: bool) -> None:
    docs = _statute_docs()
    if not docs:
        sys.exit("No statute docs. Check backend/data/*.json")

    chunks: List[chunking.Chunk] = []
    for d in docs:
        chunks.extend(chunking.chunk_statute(d))

    split = sum(1 for c in chunks if c.metadata["chunk_count"] > 1)
    print(f"  {len(docs)} sections -> {len(chunks)} chunks "
          f"({split} from sections that needed splitting)")

    by_act: Dict[str, int] = {}
    for c in chunks:
        by_act[c.metadata["act_key"]] = by_act.get(c.metadata["act_key"], 0) + 1
    for act, n in sorted(by_act.items()):
        print(f"    {act:<12} {n:>5}")

    if dry_run:
        longest = max(chunks, key=lambda c: len(c.embed_text))
        print(f"\n  longest chunk: {len(longest.embed_text)} chars "
              f"({longest.chunk_id})")
        print("\n  --- sample embed_text ---")
        print("  " + chunks[0].embed_text[:400].replace("\n", "\n  "))
        print("\n  DRY RUN - nothing embedded or written.")
        return

    index = vectorstore.ensure_index()
    known = {} if force else _existing_hashes(index, vectorstore.NS_STATUTE)
    todo = [c for c in chunks
            if force or known.get(c.chunk_id) != c.metadata["content_hash"]]

    print(f"\n  {len(chunks) - len(todo)} unchanged, {len(todo)} to embed")
    if not todo:
        print("  nothing to do.")
        return

    print("  embedding and upserting (safe to interrupt - rerun resumes)...")
    done = 0
    try:
        for offset, vectors in embeddings.embed_documents_batched(
                [c.embed_text for c in todo]):
            batch = todo[offset : offset + len(vectors)]
            vectorstore.upsert(index, batch, vectors, vectorstore.NS_STATUTE)
            done += len(batch)
            print(f"    {done}/{len(todo)}", flush=True)
    except KeyboardInterrupt:
        print(f"\n  interrupted - {done} vectors saved. Rerun to continue.")
        return
    except Exception as exc:
        print(f"\n  stopped after {done} vectors: {exc}")
        print("  Already-written vectors are kept. Rerun to continue from here.")
        print("  If this is a quota error, wait for the daily reset or enable")
        print("  billing (Tier 1 needs no minimum spend).")
        return
    print(f"\n  done - {done} vectors in namespace '{vectorstore.NS_STATUTE}'")


def ingest_judgment(docid: str, paragraphs: List[str], meta: Dict[str, Any]) -> int:
    """Index one cached judgment. Called from the Kanoon path, not the CLI.

    Deletes the document's existing chunks first: re-chunking can produce a
    different number of parts, and an upsert alone would leave the extras
    behind as orphans pointing at text that no longer exists.
    """
    court_info = chunking.classify_court(meta.get("court", ""))
    chunks = chunking.chunk_judgment(
        docid,
        paragraphs,
        case_name=meta.get("title", ""),
        court=meta.get("court", ""),
        court_level=court_info["court_level"],
        jurisdiction=court_info["jurisdiction"],
        date=meta.get("date", ""),
        citation=meta.get("citation", ""),
    )
    if not chunks:
        return 0
    index = vectorstore.ensure_index()
    vectorstore.delete_parent(index, f"judgment:{docid}", vectorstore.NS_JUDGMENT)
    vectors = embeddings.embed_documents([c.embed_text for c in chunks])
    return vectorstore.upsert(index, chunks, vectors, vectorstore.NS_JUDGMENT)


def show_stats() -> None:
    index = vectorstore.ensure_index()
    st = vectorstore.stats(index)
    print(f"  index: {vectorstore.INDEX_NAME}")
    print(f"  dimension: {st.get('dimension')}")
    print(f"  total vectors: {st.get('total_vector_count')}")
    for ns, info in (st.get("namespaces") or {}).items():
        print(f"    {ns:<12} {info.get('vector_count')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--statutes", action="store_true", help="ingest bare acts")
    ap.add_argument("--stats", action="store_true", help="show index stats")
    ap.add_argument("--dry-run", action="store_true",
                    help="chunk and report without embedding or writing")
    ap.add_argument("--force", action="store_true",
                    help="re-embed even unchanged chunks")
    args = ap.parse_args()

    if args.stats:
        show_stats()
        return
    if args.statutes or args.dry_run:
        ingest_statutes(dry_run=args.dry_run, force=args.force)
        return
    ap.print_help()


if __name__ == "__main__":
    main()