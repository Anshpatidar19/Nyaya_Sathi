"""Answer cache.

A finished answer - body, citations with their Kanoon links, next steps and
the grounding badge - is written to Postgres and replayed whole when the
same question comes back. The repeat costs one indexed SELECT instead of
retrieval, two Gemini calls and (usually) two Indian Kanoon calls.

Three rules the rest of the app depends on:

  1. *Only complete answers are stored.* A row is written after the whole
     pipeline has run, including the grounding check, so a replay is the
     validated answer with the badge it actually earned - never a draft.

  2. *Only plain questions are cached.* A question with a document, a
     matter, or earlier turns behind it depends on context the row does not
     carry, so those are neither read nor written. `is_cacheable()` is the
     single place that decides.

  3. *Nothing here changes an answer.* Retrieval, synthesis and the
     validator are untouched; this layer either has the answer or gets out
     of the way. A cache failure is logged and ignored - a broken cache
     must never break an answer.

Why Postgres and not the in-process dict in kanoon.py: that one dies with
the process. Every restart, and every --reload while developing, put the
demo questions back to a cold ten-second answer. This survives restarts and
is shared by every machine pointing at the same Supabase project.
"""

import datetime
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, usage_log
from .config import settings

logger = logging.getLogger(__name__)

# Punctuation and case carry no meaning for matching: "Can my landlord evict
# me?" and "can my landlord evict me" are one question. Anything else -
# different words, different order - is a different question and gets its
# own row, because paraphrase matching would serve the wrong answer.
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# A question this short is a greeting or a fragment; the pipeline has its own
# fast path for those and there is nothing worth storing.
MIN_QUESTION_CHARS = 12


def _normalise(question: str) -> str:
    text = _PUNCT_RE.sub(" ", (question or "").lower())
    return _WS_RE.sub(" ", text).strip()


def cache_key(question: str, state: Optional[str]) -> str:
    """The lookup key: normalised question + state.

    State is part of the key because it reaches both the retrieval court
    filter and the prompt, so the same question asked from two states can
    legitimately produce different answers.
    """
    basis = f"{_normalise(question)}|{_normalise(state or '')}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def is_cacheable(
    question: str,
    *,
    document_id: Optional[int] = None,
    matter_id: Optional[int] = None,
    conversation_id: Optional[int] = None,
    has_history: bool = False,
) -> bool:
    """Whether this question may be read from or written to the cache.

    Anything carrying context beyond the question itself is excluded: an
    attached document, a matter's case file, or a follow-up whose meaning
    depends on what came before ("and if he refuses?").
    """
    if not settings.answer_cache_enabled:
        return False

    reason = None
    if document_id:
        reason = "a document is attached"
    elif matter_id:
        reason = "it is scoped to a matter"
    elif conversation_id or has_history:
        # The commonest surprise: re-asking inside an open thread is a
        # follow-up, whose answer depends on the turns before it. Start a
        # new question to reuse a cached answer.
        reason = "it is a follow-up in an open thread"
    elif len((question or "").strip()) < MIN_QUESTION_CHARS:
        reason = "it is too short to be worth caching"

    if reason:
        logger.info("Answer cache skipped: %s", reason)
        return False
    return True


def _ttl_cutoff() -> Optional[datetime.datetime]:
    days = settings.answer_cache_ttl_days
    if days <= 0:
        return None          # no expiry
    return datetime.datetime.utcnow() - datetime.timedelta(days=days)


def lookup(db: Session, question: str, state: Optional[str]) -> Optional[Dict[str, Any]]:
    """The stored answer for this question, or None.

    Two keys are tried: the state-specific one first, then the state-less
    one. The fallback exists because state is part of the key (it reaches
    the court filter and the prompt) but most stored answers do not depend
    on it - and without the fallback, an answer warmed with no state never
    served a signed-in user, whose profile state is always sent. The
    state-less answer is the generic one, so serving it is a fair miss, not
    a wrong answer.

    Also counts the ask. An expired row is left in place rather than
    deleted - the next store() overwrites it, and deleting on a read path
    means a write inside what should be a read.
    """
    keys = [cache_key(question, state)]
    if state:
        keys.append(cache_key(question, None))
    try:
        row = None
        for i, key in enumerate(keys):
            row = (
                db.query(models.CachedAnswer)
                .filter(models.CachedAnswer.cache_key == key)
                .first()
            )
            if row is not None:
                if i:
                    logger.info("Answer cache: matched the state-less entry for %r", question[:60])
                break
        if row is None:
            logger.info("Answer cache MISS for %r (state=%r)", question[:60], state)
            return None

        cutoff = _ttl_cutoff()
        stale = cutoff is not None and (row.created_at or cutoff) < cutoff

        row.ask_count = (row.ask_count or 0) + 1
        if not stale:
            row.hit_count = (row.hit_count or 0) + 1
            row.last_used_at = datetime.datetime.utcnow()
        db.commit()

        if stale:
            logger.info("Answer cache: expired entry for %r - answering fresh", question[:60])
            return None

        payload = json.loads(row.payload_json)
        # Never replayed as pending: the badge in the row is the checked one.
        payload["grounding_pending"] = False
        # The thread this answer lands in is decided per request.
        payload.pop("conversation_id", None)
        payload.pop("query_log_id", None)
        # No model call is made for this request; QUERY_COST says so.
        usage_log.mark_cache_hit()
        logger.info(
            "Answer cache HIT (asked %d times, served %d) for %r",
            row.ask_count, row.hit_count, question[:60],
        )
        return payload
    except Exception as exc:
        db.rollback()
        logger.warning("Answer cache lookup failed, answering fresh: %s", exc)
        return None


def store(
    db: Session,
    question: str,
    state: Optional[str],
    payload: Dict[str, Any],
) -> None:
    """Save a finished answer. Overwrites any existing row for the question.

    Called only after the grounding check has run, so `grounding` holds the
    final badge. Refuses a payload that is still marked pending - storing
    one would serve an unchecked badge on every later replay.
    """
    if not settings.answer_cache_enabled:
        return
    if payload.get("grounding_pending"):
        logger.debug("Answer cache: not storing an answer whose check is still pending")
        return
    if not (payload.get("body") or "").strip():
        return

    key = cache_key(question, state)
    body = {k: v for k, v in payload.items() if k not in ("conversation_id", "query_log_id")}
    body["grounding_pending"] = False
    blob = json.dumps(body, default=str)

    try:
        row = (
            db.query(models.CachedAnswer)
            .filter(models.CachedAnswer.cache_key == key)
            .first()
        )
        if row is None:
            db.add(models.CachedAnswer(
                cache_key=key,
                question=question.strip()[:2000],
                state=state,
                payload_json=blob,
            ))
        else:
            # A re-ask that missed (expired, or the row was written before
            # this answer finished). Keep the counts, replace the answer.
            row.payload_json = blob
            row.question = question.strip()[:2000]
            row.state = state
            row.created_at = datetime.datetime.utcnow()
            row.last_used_at = datetime.datetime.utcnow()
        db.commit()
        logger.info("Answer cache STORED %r (state=%r)", question.strip()[:60], state)
        _prune(db)
    except Exception as exc:
        db.rollback()
        # A unique-constraint clash means another request stored the same
        # answer a moment earlier, which is a success, not a problem.
        logger.info("Answer cache store skipped: %s", exc)


def _prune(db: Session) -> None:
    """Keep the table to ANSWER_CACHE_MAX_ROWS, dropping least recent first."""
    cap = settings.answer_cache_max_rows
    if cap <= 0:
        return
    try:
        total = db.query(func.count(models.CachedAnswer.id)).scalar() or 0
        if total <= cap:
            return
        doomed = (
            db.query(models.CachedAnswer.id)
            .order_by(models.CachedAnswer.last_used_at.asc())
            .limit(total - cap)
            .all()
        )
        ids = [d[0] for d in doomed]
        if ids:
            (db.query(models.CachedAnswer)
               .filter(models.CachedAnswer.id.in_(ids))
               .delete(synchronize_session=False))
            db.commit()
            logger.info("Answer cache pruned %d row(s) past the %d cap", len(ids), cap)
    except Exception as exc:
        db.rollback()
        logger.warning("Answer cache prune failed: %s", exc)


def replay_chunks(body: str) -> List[str]:
    """A stored body, split for replay.

    A cached answer is streamed rather than dropped on screen whole: the
    reader sees the same card and the same typing as a live answer, only
    faster. Split on paragraph boundaries so a chunk is never half a word.
    """
    cps = settings.answer_cache_replay_cps
    if cps <= 0 or not body:
        return [body] if body else []

    # ~12 frames a second is smooth without flooding the SSE stream.
    target = max(40, cps // 12)
    chunks: List[str] = []
    buf = ""
    for para in re.split(r"(\n{2,})", body):
        buf += para
        if len(buf) >= target:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def report(db: Session, limit: int = 20) -> Dict[str, Any]:
    """What the cache holds and how much it has saved. For /admin/cache."""
    rows = (
        db.query(models.CachedAnswer)
        .order_by(models.CachedAnswer.ask_count.desc())
        .limit(limit)
        .all()
    )
    totals = db.query(
        func.count(models.CachedAnswer.id),
        func.coalesce(func.sum(models.CachedAnswer.ask_count), 0),
        func.coalesce(func.sum(models.CachedAnswer.hit_count), 0),
    ).one()
    return {
        "enabled": settings.answer_cache_enabled,
        "ttl_days": settings.answer_cache_ttl_days,
        "rows": int(totals[0]),
        "asks": int(totals[1]),
        "hits": int(totals[2]),
        "top": [
            {
                "question": r.question,
                "state": r.state,
                "asks": r.ask_count,
                "hits": r.hit_count,
                "sources": len((json.loads(r.payload_json) or {}).get("citations") or []),
                "cached_at": r.created_at,
                "last_used_at": r.last_used_at,
            }
            for r in rows
        ],
    }