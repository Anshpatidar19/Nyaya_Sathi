import datetime
import json
import logging

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
from sqlalchemy.orm import Session, selectinload

from . import (
    arguments,
    doc_scope,
    drafting,
    kanoon,
    models,
    reasoning,
    schemas,
    statutes,
    storage,
    supabase_auth,
)
from .auth import ensure_profile, get_current_user, require_advocate
from .matters_api import router as matters_router
from .config import settings
from .database import Base, SessionLocal, engine, get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Nyaya Sathi API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


app.include_router(matters_router)


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

    try:
        url = await storage.signed_url(doc.storage_path, expires_in=120)
        async with httpx.AsyncClient(timeout=60.0) as client:
            blob = await client.get(url)
            blob.raise_for_status()
        text = drafting.extract_text(blob.content, doc.content_type, doc.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not read stored document %s: %s", doc.id, exc)
        raise HTTPException(status_code=503, detail="Could not read that document.")

    return doc, text


async def _gate_document(text: str, filename: str) -> None:
    """Refuse anything outside this platform's subject matter.

    422 rather than 400: the request was well-formed, the content just isn't
    something a legal-information system should be explaining.
    """
    verdict = await doc_scope.check(text, filename)
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

    document = None
    if payload.document_id:
        doc, text = await _load_document(db, payload.document_id, current_user)
        await _gate_document(text, doc.filename)
        document = {"filename": doc.filename, "text": text}
        # "Explain this" with a file attached is a complete request; give the
        # model a real instruction rather than an empty string.
        if not question:
            question = f"Explain this document and what it means for me."

    if not question:
        raise HTTPException(status_code=400, detail="Ask a question, or attach a document.")

    title_seed = question if not document else f"{document['filename']} — {question}"
    convo = _get_or_create_conversation(
        db, current_user, payload.conversation_id, title_seed, "ask", payload.matter_id
    )
    history = _thread_history(db, convo.id) if payload.conversation_id else []

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
    return response


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
    # selectinload pulls the turns in the same trip. Without it the lazy
    # relationship fires a second query the moment `.turns` is touched, which
    # on a remote database is another full round trip.
    convo = (
        db.query(models.Conversation)
        .options(selectinload(models.Conversation.turns))
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
                "That file type isn't supported. Upload a PDF, image, Word "
                "document, or plain text file."
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
    """Time-limited download link for one of the caller's own documents."""
    doc = (
        db.query(models.Document)
        .filter(
            models.Document.id == doc_id,
            models.Document.user_id == current_user.id,  # ownership check
        )
        .first()
    )
    if not doc:
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
def draft_types(current_user: models.User = Depends(require_advocate)):
    """Catalogue of document types the drafting agent can produce."""
    return drafting.list_types()


@app.post("/draft", response_model=schemas.DraftResponse)
async def create_draft(
    payload: schemas.DraftRequest,
    current_user: models.User = Depends(require_advocate),
):
    if not payload.instructions.strip():
        raise HTTPException(status_code=400, detail="Describe what you need drafted.")

    try:
        result = await drafting.draft(
            payload.doc_type, payload.instructions, payload.details
        )
    except Exception as exc:
        logger.exception("Draft failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not produce a draft. Try again.")

    if not result["body"]:
        raise HTTPException(
            status_code=422,
            detail="Not enough information to draft this. Add more detail and retry.",
        )
    return result


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

    if payload.document_id:
        doc, doc_text = await _load_document(db, payload.document_id, current_user)
        await _gate_document(doc_text, doc.filename)
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
    current_user: models.User = Depends(require_advocate),
):
    """Red-line a counterparty's document. Accepts raw text or an uploaded file id."""
    text = (payload.document_text or "").strip()
    filename = None

    if payload.document_id and not text:
        doc, text = await _load_document(db, payload.document_id, current_user)
        filename = doc.filename

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Provide document_text, or a document_id of a file you uploaded.",
        )

    # Same gate as /ask. Pasted text goes through it too - a chemistry lab
    # report is no more reviewable for pasting it in by hand.
    await _gate_document(text, filename or "")

    try:
        result = await drafting.review(text, payload.doc_type, payload.context)
        if filename:
            if isinstance(result, dict):
                result["document_name"] = filename
            else:
                result.document_name = filename
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Review failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not review that document.")