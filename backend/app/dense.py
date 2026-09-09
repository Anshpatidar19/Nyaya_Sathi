"""Dense retrieval, used as a fallback when BM25 looks unsure.

Why a fallback rather than always-on: the embedding call is the only part of
vector search with real latency (search itself is sub-millisecond), so paying
it on every query buys nothing on the queries BM25 already answers well - and
those are most of them.

Why *two* triggers rather than a score threshold:

    "How do I resolve a property boundary dispute with my neighbour?"

returned Constitution Article 131 ("Disputes between the Government of India
and one or more States") with a healthy BM25 score, because the section really
does contain "dispute" and "property". A low-score trigger would not have
fired. So the second signal is coverage: what fraction of the question's
content words the top hit actually contains. Article 131 matches "dispute" and
nothing else - "boundary", "neighbour", "resolve" are all absent - so coverage
is low even though the score is not. Low coverage means BM25 matched on one
common word and guessed.

Thresholds are measured, not chosen. Run:

    python -m app.dense --calibrate

which sweeps both against the eval question set and reports where the fallback
would fire on questions BM25 already gets right (wasted calls) versus on ones
it gets wrong (the point of the exercise).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Set from --calibrate. The defaults are deliberately cautious: they fire the
# fallback on clearly weak results only. Tighten them once you have numbers.
def _settings():
    """See embeddings._settings - .env reaches pydantic, not os.environ."""
    try:
        from .config import settings
        return settings
    except Exception:
        return None


_ST = _settings()

MIN_SCORE = float(getattr(_ST, "dense_min_score", None)
                  or os.getenv("DENSE_MIN_SCORE", "8.0"))
MIN_COVERAGE = float(getattr(_ST, "dense_min_coverage", None)
                     or os.getenv("DENSE_MIN_COVERAGE", "0.34"))

# Off unless explicitly enabled. An index that has not been built yet is worse
# than no index: every query would create an empty one and pay for embeddings
# that match nothing.
ENABLED = bool(getattr(_ST, "dense_fallback", None)) if _ST is not None else \
    os.getenv("DENSE_FALLBACK", "0") not in ("0", "false", "False")


def coverage(query_terms: List[str], doc: Dict[str, Any]) -> float:
    """Fraction of the query's distinct content terms present in the top hit.

    Uses the same tokens BM25 scored on, so this measures what BM25 actually
    saw rather than a second, differently-tokenised opinion.
    """
    terms = set(query_terms)
    if not terms:
        return 1.0
    have = set(doc.get("_tokens") or [])
    return len(terms & have) / len(terms)


def should_fall_back(scored: List[Tuple[float, Dict[str, Any]]],
                     query_terms: List[str]) -> Tuple[bool, str]:
    """Decide whether BM25's answer is trustworthy. Returns (fire, reason)."""
    if not ENABLED:
        return False, ""
    if not scored:
        return True, "no BM25 hits"
    top_score, top_doc = scored[0]
    if top_score < MIN_SCORE:
        return True, f"low score ({top_score:.1f} < {MIN_SCORE})"
    cov = coverage(query_terms, top_doc)
    if cov < MIN_COVERAGE:
        return True, f"low coverage ({cov:.2f} < {MIN_COVERAGE})"
    return False, ""


def dense_hits(query: str, limit: int, *, jurisdictions: Optional[List[str]] = None,
               current_only: bool = True) -> List[Dict[str, Any]]:
    """Vector search, mapped back onto the BM25 index's documents.

    Returning the same dict objects statutes.py already produces matters: the
    validity layer, the repeal badge and as_source() all read fields off those
    dicts, so a parallel shape here would break citation traceability.
    """
    from . import embeddings, statutes, vectorstore

    try:
        vec = embeddings.embed_query(query)
        index = vectorstore.ensure_index()
        matches = vectorstore.query(
            index, vec,
            namespace=vectorstore.NS_STATUTE,
            top_k=limit * 3,   # room to dedupe multi-chunk sections
            filter=vectorstore.build_filter(
                current_only=current_only, jurisdictions=jurisdictions),
        )
    except Exception as exc:
        # A retrieval fallback must never take the request down with it. If
        # Pinecone or the embedding API is unavailable, BM25's answer stands.
        logger.warning("Dense fallback unavailable (%s); keeping BM25 result", exc)
        return []

    by_key = statutes._index()["by_key"]
    out: List[Dict[str, Any]] = []
    seen = set()
    for m in matches:
        key = f"{m.get('act_key')}:{m.get('section')}"
        if key in seen:
            continue          # several chunks of one long section
        doc = by_key.get(key)
        if not doc:
            continue          # indexed but no longer in the local corpus
        seen.add(key)
        out.append(doc)
        if len(out) >= limit:
            break
    return out


def fuse(bm25: List[Dict[str, Any]], dense: List[Dict[str, Any]],
         limit: int) -> List[Dict[str, Any]]:
    """Reciprocal rank fusion.

    Even when the fallback fires, BM25's hits are kept rather than discarded -
    it may have been unsure and still right, and RRF rewards anything both
    retrievers rank highly. k=60 is the standard constant; it flattens the
    contribution of deep ranks so a single retriever's tail can't dominate.
    """
    k = 60.0
    scores: Dict[str, float] = {}
    docs: Dict[str, Dict[str, Any]] = {}
    for ranking in (bm25, dense):
        for rank, d in enumerate(ranking):
            key = f"{d['act_key']}:{d['section']}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            docs[key] = d
    ordered = sorted(scores, key=lambda x: scores[x], reverse=True)
    return [docs[key] for key in ordered[:limit]]


# --- calibration ----------------------------------------------------------

def calibrate() -> None:
    """Sweep thresholds against the eval set.

    Reports, for each candidate threshold, how often the fallback would fire
    on questions BM25 already answers correctly (cost, no benefit) versus on
    questions it gets wrong (the cases worth paying for). Pick the threshold
    that catches most misses while firing least on hits.
    """
    import json
    from pathlib import Path
    from . import statutes

    path = Path(__file__).resolve().parent.parent / "data" / "eval_questions.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    docs = statutes._index()["docs"]

    rows = []
    for case in cases:
        terms = statutes._expand(case["q"])
        scored = _scored(case["q"], docs, terms)
        top = scored[0] if scored else None
        # Correctness must come from the real search path - citation lookup,
        # preamble handling and companion sections all change the answer.
        # Scoring it off raw BM25 would understate the system and calibrate
        # the trigger against a pipeline that doesn't exist.
        got = [f"{h['act_key']}:{h['section']}" for h in statutes.search(case["q"], limit=3)]
        correct = any(e in got for e in case["expect"])
        rows.append({
            "correct": correct,
            "score": top[0] if top else 0.0,
            "cov": coverage(terms, top[1]) if top else 0.0,
        })

    hits = [r for r in rows if r["correct"]]
    misses = [r for r in rows if not r["correct"]]
    print(f"{len(cases)} questions: {len(hits)} correct @3, {len(misses)} missed\n")

    print("  score threshold   fires on misses   fires on hits (wasted)")
    for t in (2, 4, 6, 8, 10, 12, 15):
        fm = sum(1 for r in misses if r["score"] < t)
        fh = sum(1 for r in hits if r["score"] < t)
        print(f"    < {t:<14} {fm:>3}/{len(misses):<14} {fh:>3}/{len(hits)}")

    print("\n  coverage threshold  fires on misses   fires on hits (wasted)")
    for t in (0.2, 0.25, 0.34, 0.4, 0.5, 0.6):
        fm = sum(1 for r in misses if r["cov"] < t)
        fh = sum(1 for r in hits if r["cov"] < t)
        print(f"    < {t:<14} {fm:>3}/{len(misses):<14} {fh:>3}/{len(hits)}")

    print("\n  Pick the row catching most misses per wasted call, then set")
    print("  DENSE_MIN_SCORE / DENSE_MIN_COVERAGE in .env accordingly.")


def _scored(query: str, docs, terms) -> List[Tuple[float, Dict[str, Any]]]:
    """BM25 with scores retained. Mirrors statutes._bm25 - if you tune the
    weights there, mirror the change here or calibration drifts."""
    import math
    from collections import Counter
    from . import statutes

    idx = statutes._index()
    if not terms:
        return []
    n = len(docs)
    df = idx["df"]
    avg_len = idx["avg_len"] or 1.0
    idf = {t: math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
           for t in set(terms)}
    out = []
    for d in docs:
        tf = Counter(d["_tokens"])
        dl = len(d["_tokens"]) or 1
        s = 0.0
        for t in terms:
            f = tf.get(t, 0)
            if not f:
                continue
            s += idf[t] * (f * (statutes._K1 + 1)) / (
                f + statutes._K1 * (1 - statutes._B + statutes._B * dl / avg_len))
        if s > 0:
            out.append((s * statutes._rank_weight(d), d))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    args = ap.parse_args()
    if args.calibrate:
        calibrate()
    else:
        ap.print_help()