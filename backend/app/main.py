import asyncio
import datetime
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session, joinedload

from . import (
    arguments,
    doc_intel,
    doc_extract,
    doc_scope,
    drafting,
    gemini,
    kanoon,
    models,
    reasoning,
    schemas,
    source_links,
    statutes,
    storage,
    translate,
    supabase_auth,
)
from .auth import ensure_profile, get_current_user, require_advocate
from .matters_api import router as matters_router
from .network_api import router as network_router
from .doc_intel_api import router as doc_intel_router
from .advocate_bot_api import router as advocate_bot_router
from .source_links_api import router as source_links_router
from .config import settings
from .database import Base, SessionLocal, engine, get_db
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# httpx logs every request at INFO, full URL included - and Gemini takes its
# API key as a query parameter, so the key ends up in the terminal, in any log
# file, and in any screenshot. Warnings still surface; the URLs do not.
logging.getLogger("httpx").setLevel(logging.WARNING)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Nyaya Sathi API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse(obj: dict) -> str:
    """One SSE frame. default=str so datetimes in the payload don't blow up."""
    return f"data: {json.dumps(obj, default=str)}\n\n"


# Paths whose timing is noise. The badge poller hits /network/unread on a
# timer from every mounted component, and logging each one buries the
# requests worth reading.
_QUIET_PATHS = {"/network/unread", "/network/me", "/health"}


@app.middleware("http")
async def log_request_time(request, call_next):
    """Wall-clock per request, measured outside the handler.

    This exists because of a specific confusion: the TIMING line in
    reasoning.py starts when the handler body starts, which is AFTER auth,
    the conversation lookup and the request gate, and it stops when the
    generator finishes. So "total=13.41s" and a stopwatch reading 16s are
    both correct and the difference is invisible. This closes it - subtract
    TIMING's total from this number and the remainder is everything around
    the answer rather than in it.
    """
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path not in _QUIET_PATHS:
        logger.info(
            "REQUEST %s %s -> %s in %.2fs",
            request.method, request.url.path, response.status_code,
            time.perf_counter() - start,
        )
    return response


# Keep-alive pool for downloading stored documents out of Supabase Storage.
# A fresh AsyncClient per download repeats the TLS handshake to the storage
# host every time, and a document is re-read on every question asked about it.
_blob_client: httpx.AsyncClient | None = None


def _get_blob_client() -> httpx.AsyncClient:
    global _blob_client
    if _blob_client is None or _blob_client.is_closed:
        _blob_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _blob_client


# --- Uploaded document caches ---------------------------------------------
# Both are keyed on storage_path, which is safe because a stored object is
# immutable: /documents writes a new path for every upload and nothing ever
# rewrites one in place. A deleted document's entry is dead weight at worst,
# and it is evicted by size.
#
# What this removes from the critical path of a document question: a signed
# URL round trip to Supabase, the download, PDF/DOCX text extraction, and a
# whole Gemini call for the scope classifier - all of it repeated, in full,
# on every follow-up question about the same file.
_DOC_CACHE_MAX = 32
# Extracted text is cached in doc_extract, shared with document intelligence.
_doc_scope_ok: "OrderedDict[str, bool]" = OrderedDict()


def _cache_get(cache: "OrderedDict[str, Any]", key: str):
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    return None


def _cache_put(cache: "OrderedDict[str, Any]", key: str, value) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _DOC_CACHE_MAX:
        cache.popitem(last=False)


@app.on_event("shutdown")
async def shutdown_clients():
    """Close the keep-alive pools so reload and restart are clean."""
    await gemini.close_client()
    await kanoon.close_client()
    await doc_intel.close_client()
    await doc_extract.close_client()
    global _blob_client
    if _blob_client is not None and not _blob_client.is_closed:
        await _blob_client.aclose()
    _blob_client = None


@app.on_event("startup")
async def startup_checks():
    """Fail loudly at boot rather than silently giving worse answers later."""
    acts = statutes.available_acts()
    if acts:
        logger.info("Statute index loaded: %s", ", ".join(acts))
    else:
        logger.warning(
            "No statute data found in backend/data/. Run: "
            "python -m app.ingest_bns  and  python -m app.ingest_constitution"
        )

    try:
        await storage.ensure_bucket()
        logger.info("Supabase Storage bucket ready: %s", storage.settings.supabase_bucket)
    except storage.StorageError as exc:
        logger.warning("Supabase Storage not configured: %s", exc)

    # Warm the dense-retrieval path so the first user to ask a question does
    # not pay for it. Pinecone's list_indexes / describe_index / build-a-host-
    # specific-client sequence is three control-plane round trips, and it was
    # landing inside somebody's retrieve() as a one-off couple of seconds that
    # looked like slow retrieval. Free, unlike an embedding call, which is why
    # only this half is warmed - the first real question still pays one
    # embedding round trip.
    if settings.dense_fallback:
        try:
            from . import vectorstore

            await asyncio.to_thread(vectorstore.ensure_index)
            logger.info("Pinecone index handle warmed: %s", vectorstore.INDEX_NAME)
        except Exception as exc:
            logger.warning("Could not warm the Pinecone index (%s)", exc)


app.include_router(matters_router)
# Advocate discovery, connection requests, private chat and notifications.
# Everything it owns is under /network, so it cannot collide with the AI
# surface's /conversations or /documents routes.
app.include_router(network_router)
# AI document intelligence for files shared in a chat thread. Under
# /network/threads/{id}/documents/{id}/..., so it lives with the chat it
# serves without adding another feature to network_api.py.
app.include_router(doc_intel_router)
# The Find an Advocate assistant. Registered after the network router so
# /network/advocates/recommend sits beside the directory it searches.
app.include_router(advocate_bot_router)

# Source cards open the exact cited provision - see source_links.py.
app.include_router(source_links_router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------- Auth ----------------

@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    """Create the account in Supabase Auth and send a confirmation email.

    With email confirmation on, Supabase returns a user but NO session, so
    there is no token to hand back. The user must click the link first.
    That is the whole point: an address nobody controls never gets confirmed,
    so fake signups can't reach the app.
    """
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    try:
        result = await supabase_auth.sign_up(
            email=payload.email,
            password=payload.password,
            metadata={
                "name": payload.name,
                "state": payload.state,
                "preferred_language": payload.preferred_language,
                "role": payload.role,
            },
        )
    except supabase_auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    supa_user = result.get("user") or result
    auth_id = supa_user.get("id")

    # Create the profile row now so the user's details aren't lost, but it
    # stays inert until they confirm and log in.
    if auth_id:
        ensure_profile(
            db,
            auth_id=auth_id,
            email=payload.email,
            name=payload.name,
            state=payload.state,
            preferred_language=payload.preferred_language,
            role=payload.role,
        )

    if supabase_auth.needs_confirmation(result):
        return {
            "confirmation_required": True,
            "email": payload.email,
            "message": (
                "Almost there. We've sent a confirmation link to "
                f"{payload.email} — click it, then log in."
            ),
        }

    # Confirmation is switched off in the Supabase dashboard: log them in.
    session = await supabase_auth.sign_in(payload.email, payload.password)
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    return {
        "confirmation_required": False,
        "access_token": session["access_token"],
        "token_type": "bearer",
        "user": schemas.UserOut.model_validate(user),
    }


@app.post("/auth/resend-confirmation")
async def resend_confirmation(payload: schemas.EmailOnly):
    try:
        await supabase_auth.resend_confirmation(payload.email)
    except supabase_auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"message": "If that address is registered, we've sent the link again."}


@app.post("/auth/forgot-password")
async def forgot_password(payload: schemas.EmailOnly):
    """Always reports success - confirming which addresses exist would let
    anyone enumerate the user list."""
    await supabase_auth.send_password_reset(
        payload.email, redirect_to=f"{settings.site_url}/reset"
    )
    return {"message": "If that address is registered, a reset link is on its way."}


@app.post("/auth/reset-password")
async def reset_password(payload: schemas.PasswordReset):
    """Finish a reset. The frontend reads the recovery token out of the
    emailed link's URL fragment and posts it here with the new password."""
    try:
        await supabase_auth.update_password(payload.access_token, payload.new_password)
    except supabase_auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"message": "Password updated. You can log in with it now."}


@app.post("/auth/login", response_model=schemas.TokenOut)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm uses "username" as the field name; we treat it as email.
    try:
        session = await supabase_auth.sign_in(form_data.username, form_data.password)
    except supabase_auth.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    supa_user = session.get("user") or {}
    meta = supa_user.get("user_metadata") or {}

    user = ensure_profile(
        db,
        auth_id=supa_user.get("id"),
        email=supa_user.get("email") or form_data.username,
        name=meta.get("name"),
        state=meta.get("state"),
        preferred_language=meta.get("preferred_language", "en"),
        role=meta.get("role"),
    )

    return schemas.TokenOut(
        access_token=session["access_token"],
        user=schemas.UserOut.model_validate(user),
    )


@app.get("/auth/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user


# ---------------- Uploaded documents ----------------

async def _load_document(
    db: Session, doc_id: int, user: models.User
) -> tuple[models.Document, str]:
    """Fetch one of the caller's own documents and extract its text.

    Ownership is checked here rather than trusted from the client, so a
    document_id belonging to someone else is a 404, not a data leak.
    """
    doc = (
        db.query(models.Document)
        .filter(
            models.Document.id == doc_id,
            models.Document.user_id == user.id,
        )
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # One extraction pipeline for every format - text PDFs, Word, photos,
    # scans and handwriting (Gemini OCR). It caches in memory and saves the
    # result beside the file, and upload already started it in the
    # background, so a question rarely waits on extraction at all.
    try:
        text = await doc_extract.load(doc.storage_path, doc.content_type, doc.filename)
    except ValueError as exc:
        # Includes doc_extract.Unreadable: too unclear to read honestly.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Could not read stored document %s: %s", doc.id, exc)
        raise HTTPException(status_code=503, detail="Could not read that document.")

    return doc, text


async def _gate_document(text: str, filename: str, cache_key: str | None = None) -> None:
    """Refuse anything outside this platform's subject matter.

    422 rather than 400: the request was well-formed, the content just isn't
    something a legal-information system should be explaining.

    The verdict is remembered per stored object. The classifier is a Gemini
    call on the critical path, and re-running it on the fifth question about
    the same rent agreement cannot reach a different answer - the text it
    classifies is byte-identical. A rejection is deliberately NOT cached: the
    call is only paid once per rejected document anyway, since the request
    fails before anything else happens.
    """
    if cache_key and _cache_get(_doc_scope_ok, cache_key):
        return

    verdict = await doc_scope.check(text, filename)
    if not verdict.in_scope:
        raise HTTPException(status_code=422, detail=verdict.reason)

    if cache_key:
        _cache_put(_doc_scope_ok, cache_key, True)


def _gate_request(text: str, *, allow_short: bool = False) -> None:
    """Refuse typed input that isn't a request at all.

    Runs before retrieval and before any model call. Without it a keyboard
    mash reaches the drafting agent, which obliges with a notice-shaped
    document whose every fact is a bracketed placeholder - work product in
    appearance, invented in substance. 422 for the same reason the document
    gate uses it: the request was well formed, the content wasn't.

    Synchronous and local: no API call, so it is free to run on every path.
    """
    verdict = doc_scope.check_request(text, allow_short=allow_short)
    if not verdict.in_scope:
        raise HTTPException(status_code=422, detail=verdict.reason)


# ---------------- Ask (core Q&A) ----------------

HISTORY_TURNS = 4     # earlier turns fed back to the model


def _thread_history(db: Session, conversation_id: int) -> list[dict]:
    rows = (
        db.query(models.QueryLog)
        .filter(models.QueryLog.conversation_id == conversation_id)
        .order_by(models.QueryLog.created_at.desc())
        .limit(HISTORY_TURNS)
        .all()
    )
    return [
        {"question": r.question, "answer": r.answer_body or ""}
        for r in reversed(rows)
    ]


def _get_or_create_conversation(
    db: Session,
    user: models.User,
    conversation_id: int | None,
    first_question: str,
    mode: str,
    matter_id: int | None = None,
) -> models.Conversation:
    if conversation_id:
        convo = (
            db.query(models.Conversation)
            .filter(
                models.Conversation.id == conversation_id,
                models.Conversation.user_id == user.id,   # ownership check
            )
            .first()
        )
        if convo:
            return convo

    convo = models.Conversation(
        user_id=user.id,
        matter_id=matter_id,
        title=first_question[:80],
        mode=mode,
    )
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


@app.post("/ask", response_model=schemas.AskResponse)
async def ask(
    payload: schemas.AskRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    question = payload.question.strip()

    # A clean "hi" / "thanks" / "who are you" is answered by the fast path
    # in reasoning.answer_question below and must skip the request gate -
    # otherwise a 5-character greeting never gets that far, since the gate's
    # length floor rejects it as "too short" first.
    if reasoning.greeting_reply(question) is None:
        # A document attached alongside carries the context, so a one-word
        # nudge is legitimate there and only the gibberish tests apply.
        _gate_request(question, allow_short=bool(payload.document_id))

    document = None
    if payload.document_id:
        doc, text = await _load_document(db, payload.document_id, current_user)
        await _gate_document(text, doc.filename, doc.storage_path)
        document = {"filename": doc.filename, "text": text}
        # "Explain this" with a file attached is a complete request; give the
        # model a real instruction rather than an empty string.
        if not question:
            question = f"Explain this document and what it means for me."

    if not question:
        raise HTTPException(status_code=400, detail="Ask a question, or attach a document.")

    title_seed = question if not document else f"{document['filename']} — {question}"
    # to_thread for the same reason as the streamed write below: these are
    # blocking psycopg2 round trips to a remote database inside an async
    # handler, and inline they stall every other request in the process.
    # Sequential, never concurrent, so the request-scoped Session is only
    # ever touched by one thread at a time.
    convo = await asyncio.to_thread(
        _get_or_create_conversation,
        db, current_user, payload.conversation_id, title_seed, "ask", payload.matter_id,
    )
    history = (
        await asyncio.to_thread(_thread_history, db, convo.id)
        if payload.conversation_id else []
    )

    answer = await reasoning.answer_question(
        question, payload.state or current_user.state, history, document
    )
    if document:
        answer.document_name = document["filename"]

    log = models.QueryLog(
        user_id=current_user.id,
        conversation_id=convo.id,
        mode="ask",
        question=title_seed,
        answer_title=answer.title,
        answer_body=answer.body,
        citations_json=json.dumps([c.model_dump() for c in answer.citations]),
        payload_json=json.dumps(answer.model_dump(), default=str),
    )
    db.add(log)
    convo.updated_at = datetime.datetime.utcnow()
    db.commit()

    response = answer.model_dump()
    response["conversation_id"] = convo.id
    # The turn id is what /translate/answer works from - without it a live
    # answer can't be re-rendered until the thread is reloaded.
    response["query_log_id"] = log.id
    return response


@app.post("/ask/stream")
async def ask_stream(
    payload: schemas.AskRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Same answer as /ask, sent as it is written.

    Server-Sent Events. Each line is `data: {"type": ..., ...}`:
      delta    - more body text
      revised  - the validator replaced the body; show this instead
      done     - citations, next steps, grounding, conversation_id
      error    - nothing usable; the client should show the message

    Retrieval and validation are unchanged. Only the wait is different.
    """
    question = payload.question.strip()

    # See /ask - a clean greeting skips the gate so the fast path in
    # reasoning.answer_question_stream can answer it instead of it being
    # rejected as "too short".
    if reasoning.greeting_reply(question) is None:
        _gate_request(question, allow_short=bool(payload.document_id))

    document = None
    if payload.document_id:
        doc, text = await _load_document(db, payload.document_id, current_user)
        await _gate_document(text, doc.filename, doc.storage_path)
        document = {"filename": doc.filename, "text": text}
        if not question:
            question = "Explain this document and what it means for me."

    if not question:
        raise HTTPException(status_code=400, detail="Ask a question, or attach a document.")

    title_seed = question if not document else f"{document['filename']} — {question}"
    convo = await asyncio.to_thread(
        _get_or_create_conversation,
        db, current_user, payload.conversation_id, title_seed, "ask", payload.matter_id,
    )
    history = (
        await asyncio.to_thread(_thread_history, db, convo.id)
        if payload.conversation_id else []
    )
    state = payload.state or current_user.state
    convo_id = convo.id
    # Captured before the generator starts. FastAPI closes the request-scoped
    # session as soon as this handler returns, but events() runs *after* that,
    # while the response streams. Touching an ORM instance in there raises
    # DetachedInstanceError, so pull out the plain values now.
    user_id = current_user.id

    def _save(answer) -> int | None:
        """Persist the finished turn. Returns the QueryLog id, or None.

        A fresh session: `db` belongs to a request that has already returned
        by the time the generator runs. Opened late and closed straight after,
        so it holds a connection only for the write itself.
        """
        write_db = SessionLocal()
        try:
            log = models.QueryLog(
                user_id=user_id,
                conversation_id=convo_id,
                mode="ask",
                question=title_seed,
                answer_title=answer.title,
                answer_body=answer.body,
                citations_json=json.dumps([c.model_dump() for c in answer.citations]),
                payload_json=json.dumps(answer.model_dump(), default=str),
            )
            write_db.add(log)
            write_db.query(models.Conversation).filter(
                models.Conversation.id == convo_id
            ).update({"updated_at": datetime.datetime.utcnow()})
            write_db.commit()
            return log.id
        except Exception as exc:
            write_db.rollback()
            logger.exception("Could not save streamed answer: %s", exc)
            return None
        finally:
            write_db.close()

    def _apply_revision(log_id: int | None, body: str) -> None:
        """Correct a stored turn after the validator replaced its body.

        The validator now runs after the payload is sent, so on the rare
        rejection the row already holds the draft. A reloaded thread has to
        show what the reader ended up seeing, not what was sent first.
        """
        if log_id is None:
            return
        write_db = SessionLocal()
        try:
            log = write_db.get(models.QueryLog, log_id)
            if log is None:
                return
            log.answer_body = body
            try:
                payload_obj = json.loads(log.payload_json or "{}")
                payload_obj["body"] = body
                log.payload_json = json.dumps(payload_obj, default=str)
            except (TypeError, ValueError):
                pass
            write_db.commit()
        except Exception as exc:
            write_db.rollback()
            logger.exception("Could not apply the validator's revision: %s", exc)
        finally:
            write_db.close()

    async def events():
        sent_done = False
        log_id = None
        try:
            async for kind, value in reasoning.answer_question_stream(
                question, state, history, document
            ):
                if kind == "delta":
                    yield _sse({"type": "delta", "text": value})

                elif kind == "revised":
                    # This can now arrive either side of "done", depending on
                    # reasoning.STREAM_DONE_BEFORE_VALIDATION. After it, the
                    # stored row needs correcting as well as the screen.
                    if sent_done:
                        await asyncio.to_thread(_apply_revision, log_id, value)
                    yield _sse({"type": "revised", "body": value})

                elif kind == "error":
                    yield _sse({"type": "error", "message": value})
                    return

                elif kind == "done":
                    answer = value
                    if document:
                        answer.document_name = document["filename"]

                    # Saved and sent inside the loop rather than after it, so
                    # the citations reach the reader the moment they exist
                    # instead of waiting on whatever the generator does next.
                    #
                    # In a thread, and this matters more than it looks. These
                    # are synchronous psycopg2 calls to a REMOTE Postgres, and
                    # this generator is async - so run inline they block the
                    # whole event loop for the duration of the round trip.
                    # The validator's HTTP response cannot even be read while
                    # that happens, which is why a verdict call returning 17
                    # output tokens was being measured at 4.6 seconds.
                    log_id = await asyncio.to_thread(_save, answer)

                    final = answer.model_dump()
                    final["conversation_id"] = convo_id
                    # None when the save failed, which the client reads as
                    # "this answer can't be translated" rather than crashing
                    # on a missing id.
                    final["query_log_id"] = log_id
                    sent_done = True
                    yield _sse({"type": "done", "answer": final})
        except Exception as exc:
            logger.exception("Streaming answer failed: %s", exc)
            if not sent_done:
                yield _sse({
                    "type": "error",
                    "message": "The answer could not be completed.",
                })
            return

        if not sent_done:
            yield _sse({"type": "error", "message": "The answer could not be completed."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers SSE by default and the stream arrives all at once
            # at the end, which looks exactly like the bug this replaces.
            "X-Accel-Buffering": "no",
        },
    )


# ---------------- Languages ----------------
# The interface stays in English. These endpoints translate generated output
# only - the answer prose and drafted documents - and never the UI.

@app.get("/languages")
def list_languages():
    """What the picker offers. Served from the backend so the frontend list
    and the translator can't disagree about which codes are valid."""
    return [
        {"code": code, "name": meta["name"], "native": meta["native"]}
        for code, meta in translate.LANGUAGES.items()
    ]


@app.post("/translate/answer")
async def translate_answer(
    payload: schemas.TranslateAnswerRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Render a stored answer in another language.

    The English original is never overwritten: it is what the validator
    passed and what the grounding badge was computed against. This returns a
    rendering of it.
    """
    language = translate.normalise(payload.language)

    turn = (
        db.query(models.QueryLog)
        .filter(
            models.QueryLog.id == payload.query_log_id,
            models.QueryLog.user_id == current_user.id,
        )
        .first()
    )
    if not turn:
        raise HTTPException(status_code=404, detail="That answer wasn't found.")

    original = json.loads(turn.payload_json) if turn.payload_json else {}
    english = {
        "title": turn.answer_title or original.get("title") or "",
        "body": turn.answer_body or original.get("body") or "",
        "next_steps": original.get("next_steps") or [],
    }

    if language == translate.DEFAULT_LANGUAGE:
        return {"language": language, "cached": True, **english}

    cached = (
        db.query(models.AnswerTranslation)
        .filter(
            models.AnswerTranslation.query_log_id == turn.id,
            models.AnswerTranslation.language == language,
        )
        .first()
    )
    if cached:
        return {
            "language": language,
            "cached": True,
            "title": cached.title,
            "body": cached.body,
            "next_steps": json.loads(cached.next_steps_json or "[]"),
        }

    try:
        result = await translate.translate_answer(
            english["title"], english["body"], english["next_steps"], language
        )
    except Exception as exc:
        logger.warning("Translation to %s failed: %s", language, exc)
        raise HTTPException(
            status_code=503,
            detail="That translation couldn't be produced. The English answer is unchanged.",
        )

    try:
        db.add(models.AnswerTranslation(
            query_log_id=turn.id,
            language=language,
            title=result["title"],
            body=result["body"],
            next_steps_json=json.dumps(result["next_steps"], ensure_ascii=False),
        ))
        db.commit()
    except Exception as exc:
        # A cache write failing is not a reason to withhold the translation.
        db.rollback()
        logger.warning("Could not cache translation: %s", exc)

    return {"language": language, "cached": False, **result}


@app.post("/translate/text")
async def translate_text(
    payload: schemas.TranslateTextRequest,
    current_user: models.User = Depends(get_current_user),
):
    """Translate a drafted document.

    Not cached: a draft is edited between requests, so a cache keyed on the
    text would miss constantly and a cache keyed on the draft id would go
    stale. Clause numbering, blanks and placeholders are preserved.
    """
    language = translate.normalise(payload.language)
    if language == translate.DEFAULT_LANGUAGE:
        return {"language": language, "body": payload.text}

    try:
        body = await translate.translate_document(payload.text, language)
    except Exception as exc:
        logger.warning("Document translation to %s failed: %s", language, exc)
        raise HTTPException(
            status_code=503,
            detail="That translation couldn't be produced. The English draft is unchanged.",
        )
    return {"language": language, "body": body}


@app.get("/ask/history", response_model=list[schemas.QueryHistoryItem])
def ask_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.QueryLog)
        .filter(models.QueryLog.user_id == current_user.id)
        .order_by(models.QueryLog.created_at.desc())
        .limit(50)
        .all()
    )


# ---------------- Conversations ----------------

@app.get("/conversations", response_model=list[schemas.ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.Conversation)
        .filter(models.Conversation.user_id == current_user.id)
        .order_by(models.Conversation.updated_at.desc())
        .limit(60)
        .all()
    )


@app.get("/conversations/{conversation_id}", response_model=schemas.ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Full thread, with each turn's original payload so a refreshed page
    renders the same citations and flags it showed live."""
    # joinedload pulls the conversation AND its turns in ONE query (a LEFT
    # JOIN), not two. On a remote database each round trip is the real cost,
    # not the row count - selectinload's second query was doubling the wait
    # on every single chat switch, which is what made this feel slow on a
    # local backend talking to a remote Supabase database.
    convo = (
        db.query(models.Conversation)
        .options(joinedload(models.Conversation.turns))
        .filter(
            models.Conversation.id == conversation_id,
            models.Conversation.user_id == current_user.id,
        )
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    turns = []
    for t in convo.turns:
        payload = None
        if t.payload_json:
            try:
                payload = json.loads(t.payload_json)
            except json.JSONDecodeError:
                payload = None
        # Chats saved before direct source links carry Kanoon search URLs.
        # Rewrite them on read so old threads open the exact section too.
        if payload is not None:
            source_links.upgrade_links(payload)
        turns.append(
            schemas.TurnOut(
                id=t.id,
                mode=t.mode or "ask",
                question=t.question,
                answer_title=t.answer_title,
                answer_body=t.answer_body,
                payload=payload,
                created_at=t.created_at,
            )
        )

    return schemas.ConversationDetail(
        id=convo.id, title=convo.title, mode=convo.mode,
        created_at=convo.created_at, turns=turns,
    )


@app.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    convo = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.id == conversation_id,
            models.Conversation.user_id == current_user.id,
        )
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    db.delete(convo)
    db.commit()


# ---------------- Documents ----------------
# Files live in a PRIVATE Supabase Storage bucket. Nothing is served by a
# public URL - downloads go through /documents/{id}/url, which checks
# ownership first and returns a short-lived signed link.

@app.post("/documents", response_model=schemas.DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    note: str | None = Form(None),
    matter_id: int | None = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if file.content_type not in storage.ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "That file type isn't supported. Upload a PDF, Word document, "
                "photo (JPG, PNG, WEBP, HEIC) or plain text file."
            ),
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > storage.MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Files must be under {storage.MAX_BYTES // (1024 * 1024)} MB.",
        )

    # The Content-Type header is the browser's claim; the first bytes are
    # evidence. A renamed executable labelled application/pdf stops here.
    if doc_extract.detect_kind(data, file.content_type, file.filename or "") is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "That file doesn't look like the type it claims to be. Upload "
                "a PDF, Word document, photo or text file."
            ),
        )

    path = storage.build_path(current_user.id, file.filename)
    try:
        await storage.upload(path, data, file.content_type)
    except storage.StorageError as exc:
        logger.error("Upload failed for user %s: %s", current_user.id, exc)
        raise HTTPException(status_code=503, detail="Could not store that file. Please try again.")

    # Only file it under a matter the caller actually owns.
    if matter_id is not None:
        owns = (
            db.query(models.Matter)
            .filter(
                models.Matter.id == matter_id,
                models.Matter.user_id == current_user.id,
            )
            .first()
        )
        if not owns:
            raise HTTPException(status_code=404, detail="Matter not found.")

    doc = models.Document(
        user_id=current_user.id,
        matter_id=matter_id,
        filename=storage.safe_name(file.filename),
        storage_path=path,
        content_type=file.content_type,
        size_bytes=len(data),
        note=note,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Read it now, while the user is still typing their question - text
    # extraction for PDFs and Word, Gemini OCR for photos, scans and
    # handwriting. Runs in the background; this response does not wait.
    doc_extract.prewarm(path, data, file.content_type, doc.filename)
    return doc


@app.get("/documents", response_model=list[schemas.DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.Document)
        .filter(models.Document.user_id == current_user.id)
        .order_by(models.Document.created_at.desc())
        .all()
    )


@app.get("/documents/{doc_id}/url")
async def document_url(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Time-limited download link.

    Normally the caller's own document. If it was sent as a chat attachment,
    the other participant in that thread can open it too - see
    Document.message_id.
    """
    doc = db.query(models.Document).filter(models.Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    allowed = doc.user_id == current_user.id
    if not allowed and doc.message_id is not None:
        allowed = (
            db.query(models.ChatParticipant)
            .join(
                models.ChatMessage,
                models.ChatMessage.thread_id == models.ChatParticipant.thread_id,
            )
            .filter(
                models.ChatMessage.id == doc.message_id,
                models.ChatParticipant.user_id == current_user.id,
            )
            .first()
            is not None
        )
    if not allowed:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        url = await storage.signed_url(doc.storage_path, expires_in=3600)
    except storage.StorageError as exc:
        logger.error("Signing failed for doc %s: %s", doc_id, exc)
        raise HTTPException(status_code=503, detail="Could not generate a download link.")

    return {"url": url, "expires_in": 3600}


@app.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    doc = (
        db.query(models.Document)
        .filter(
            models.Document.id == doc_id,
            models.Document.user_id == current_user.id,
        )
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    await storage.delete(doc.storage_path)
    await doc_extract.forget(doc.storage_path)
    db.delete(doc)
    db.commit()


# ---------------- Indian Kanoon passthrough (for the research/citation view) ----------------

@app.get("/kanoon/search", response_model=list[schemas.KanoonSearchResult])
async def kanoon_search(q: str, current_user: models.User = Depends(get_current_user)):
    try:
        results = await kanoon.search(q)
    except kanoon.KanoonError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return results


@app.get("/kanoon/doc/{docid}")
async def kanoon_doc(docid: str, current_user: models.User = Depends(get_current_user)):
    try:
        return await kanoon.get_document(docid)
    except kanoon.KanoonError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

# ---------------- Drafting & review ----------------

@app.get("/draft/types")
def draft_types(current_user: models.User = Depends(get_current_user)):
    """Catalogue of document types the drafting agent can produce.

    Open to every logged-in account, not just advocates - most of these
    (legal notices, RTI applications, rent agreements) are things an
    ordinary person drafts for themselves. Types that genuinely need a
    lawyer to settle before filing are flagged via `needs_advocate` instead
    of hidden outright, so the frontend can warn rather than block.
    """
    return drafting.list_types()


@app.post("/draft", response_model=schemas.DraftResponse)
async def create_draft(
    payload: schemas.DraftRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not payload.instructions.strip():
        raise HTTPException(status_code=400, detail="Describe what you need drafted.")

    # Drafting is where this matters most: with nothing real to work from the
    # model fills a whole notice with bracketed placeholders, and a document
    # that looks finished is one a user may act on.
    _gate_request(payload.instructions)

    try:
        result = await drafting.draft(
            payload.doc_type, payload.instructions, payload.details
        )
    except Exception as exc:
        logger.exception("Draft failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not produce a draft. Try again.")

    if not result["body"]:
        # The model declined - either the instructions described nothing it
        # could work from, or they were outside legal drafting. Its own note
        # is more useful than a generic line, so prefer it when there is one.
        note = next((n for n in result.get("notes") or [] if n.strip()), "")
        raise HTTPException(
            status_code=422,
            detail=note
            or "Not enough information to draft this. Add more detail and retry.",
        )

    # Saved the same way /ask saves a turn, so drafts show up in the sidebar
    # history and can be reopened later - this used to not happen at all.
    title_seed = payload.instructions[:80]
    convo = _get_or_create_conversation(
        db, current_user, payload.conversation_id, title_seed, "draft"
    )
    log = models.QueryLog(
        user_id=current_user.id,
        conversation_id=convo.id,
        mode="draft",
        question=title_seed,
        answer_title=result["title"],
        answer_body=result["body"],
        payload_json=json.dumps(result, default=str),
    )
    db.add(log)
    convo.updated_at = datetime.datetime.utcnow()
    # flush (not commit) assigns log.id right away, without the extra round
    # trip that reading it back AFTER commit would cost - session-per-request
    # already expires attributes on commit, so this avoids paying for that.
    db.flush()
    query_log_id = log.id
    db.commit()

    result["conversation_id"] = convo.id
    result["query_log_id"] = query_log_id
    return result


def _docx_filename(title: str) -> str:
    """A safe, readable .docx filename from a draft's title."""
    slug = re.sub(r"[^\w\s-]", "", title or "draft").strip()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-").lower()
    return f"{slug or 'draft'}.docx"


@app.get("/draft/{query_log_id}/docx")
async def download_draft_docx(
    query_log_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Download a generated draft as a Word document.

    Rebuilds the .docx from the SAVED payload, not by asking Gemini again -
    so what downloads is exactly what was shown and reviewed on screen, not
    a fresh (and possibly different) generation.
    """
    log = (
        db.query(models.QueryLog)
        .filter(
            models.QueryLog.id == query_log_id,
            models.QueryLog.user_id == current_user.id,
            models.QueryLog.mode == "draft",
        )
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="Draft not found.")

    data = {}
    if log.payload_json:
        try:
            data = json.loads(log.payload_json)
        except json.JSONDecodeError:
            data = {}

    title = data.get("title") or log.answer_title or "Draft document"
    try:
        buf = drafting.to_docx(
            title=title,
            body=data.get("body") or log.answer_body or "",
            citations=data.get("citations") or [],
            missing_information=data.get("missing_information") or [],
            notes=data.get("notes") or [],
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_docx_filename(title)}"'},
    )


@app.get("/arguments/sides")
def argument_sides(current_user: models.User = Depends(require_advocate)):
    """The parties an advocate can appear for. Advocate-only, like the tool."""
    return arguments.list_sides()


@app.post("/arguments", response_model=schemas.ArgumentsResponse)
async def generate_arguments(
    payload: schemas.ArgumentsRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Build arguments, the opposing case, and rebuttals for one side.

    Advocate-only: this produces advocacy, not the neutral legal information
    the Ask surface gives. A general user asking "what should I argue" should
    be talking to a lawyer, not to this.
    """
    facts = (payload.facts or "").strip()
    document_name = None

    # Same gate as everywhere else. An advocate mashing the keyboard should
    # get a nudge, not a confidently argued case built from nothing.
    _gate_request(facts, allow_short=bool(payload.document_id))

    if payload.document_id:
        doc, doc_text = await _load_document(db, payload.document_id, current_user)
        await _gate_document(doc_text, doc.filename, doc.storage_path)
        document_name = doc.filename
        # Typed facts lead - they are the advocate's framing of the matter,
        # and the document is the raw material behind it.
        facts = f"{facts}\n\n---\n\n{doc_text}".strip() if facts else doc_text

    if not facts:
        raise HTTPException(
            status_code=400,
            detail="Describe the facts of the matter, or attach the case document.",
        )

    try:
        return await arguments.generate(
            facts=facts,
            side=payload.side,
            issue=payload.issue,
            court=payload.court,
            state=payload.state or current_user.state,
            document_name=document_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Argument generation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not generate arguments for that matter. Try again.",
        )


@app.post("/review", response_model=schemas.ReviewResponse)
async def review_document(
    payload: schemas.ReviewRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Red-line a counterparty's document. Accepts raw text or an uploaded file id.

    Open to every logged-in account. Anyone can be handed a rent agreement
    or a notice to sign - they don't need to be an advocate to want the
    risky clauses flagged before they do.
    """
    text = (payload.document_text or "").strip()
    filename = None
    context = payload.context

    # An uploaded file is always the document to review. Text typed
    # alongside it - "check the legitimacy of this" - is an instruction
    # about the file, not the document itself, so it must not replace the
    # file's content. Previously this only loaded the file when the text
    # box was empty, so any accompanying caption silently discarded the
    # attachment and got reviewed as if IT were the document - a dozen
    # characters, always short-circuited as unreadable.
    if payload.document_id:
        doc, doc_text = await _load_document(db, payload.document_id, current_user)
        filename = doc.filename
        if text:
            context = f"{context}\n\n{text}".strip() if context else text
        text = doc_text

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Provide document_text, or a document_id of a file you uploaded.",
        )

    # Pasted text gets the request gate first. The document gate's short-text
    # message talks about scanned files needing OCR, which is nonsense advice
    # for something the user typed into the box by hand.
    if not filename:
        _gate_request(text)

    # Same gate as /ask. Pasted text goes through it too - a chemistry lab
    # report is no more reviewable for pasting it in by hand.
    await _gate_document(text, filename or "")

    try:
        result = await drafting.review(text, payload.doc_type, context)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Review failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not review that document.")

    if filename:
        if isinstance(result, dict):
            result["document_name"] = filename
        else:
            result.document_name = filename

    # Same history save as /draft - a review is a turn too, and previously
    # vanished the moment the response left the server.
    title_seed = f"Review: {filename}" if filename else (payload.doc_type or "Document review")
    convo = _get_or_create_conversation(
        db, current_user, payload.conversation_id, title_seed[:80], "review"
    )
    log = models.QueryLog(
        user_id=current_user.id,
        conversation_id=convo.id,
        mode="review",
        question=title_seed[:80],
        answer_title=result.get("summary", "Document review")[:120] if isinstance(result, dict) else "Document review",
        answer_body=result.get("summary") if isinstance(result, dict) else None,
        payload_json=json.dumps(result, default=str),
    )
    db.add(log)
    convo.updated_at = datetime.datetime.utcnow()
    db.commit()

    if isinstance(result, dict):
        result["conversation_id"] = convo.id
    else:
        result.conversation_id = convo.id
    return result