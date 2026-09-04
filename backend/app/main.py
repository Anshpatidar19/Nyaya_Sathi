import datetime
import json
import logging

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
from sqlalchemy.orm import Session

from . import drafting, kanoon, models, reasoning, schemas, statutes, storage, supabase_auth
from .auth import ensure_profile, get_current_user
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
        payload.email, redirect_to=f"{settings.site_url}/login"
    )
    return {"message": "If that address is registered, a reset link is on its way."}


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
    )

    return schemas.TokenOut(
        access_token=session["access_token"],
        user=schemas.UserOut.model_validate(user),
    )


@app.get("/auth/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user


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
    db: Session, user: models.User, conversation_id: int | None, first_question: str, mode: str
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
    convo = _get_or_create_conversation(
        db, current_user, payload.conversation_id, payload.question, "ask"
    )
    history = _thread_history(db, convo.id) if payload.conversation_id else []

    answer = await reasoning.answer_question(
        payload.question, payload.state or current_user.state, history
    )

    log = models.QueryLog(
        user_id=current_user.id,
        conversation_id=convo.id,
        mode="ask",
        question=payload.question,
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

    doc = models.Document(
        user_id=current_user.id,
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
def draft_types(current_user: models.User = Depends(get_current_user)):
    """Catalogue of document types the drafting agent can produce."""
    return drafting.list_types()


@app.post("/draft", response_model=schemas.DraftResponse)
async def create_draft(
    payload: schemas.DraftRequest,
    current_user: models.User = Depends(get_current_user),
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


@app.post("/review", response_model=schemas.ReviewResponse)
async def review_document(
    payload: schemas.ReviewRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Red-line a counterparty's document. Accepts raw text or an uploaded file id."""
    text = (payload.document_text or "").strip()

    if payload.document_id and not text:
        doc = (
            db.query(models.Document)
            .filter(
                models.Document.id == payload.document_id,
                models.Document.user_id == current_user.id,   # ownership check
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
        except Exception as exc:
            logger.exception("Could not read stored document %s: %s", doc.id, exc)
            raise HTTPException(status_code=503, detail="Could not read that document.")

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Provide document_text, or a document_id of a file you uploaded.",
        )

    try:
        return await drafting.review(text, payload.doc_type, payload.context)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Review failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not review that document.")