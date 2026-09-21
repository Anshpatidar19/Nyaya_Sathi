"""Routes for AI document intelligence inside an advocate-client chat.

Mounted under /network/threads so it sits beside the chat it belongs to,
but kept in its own module: network_api.py is already the longest file in
the backend and this is a self-contained feature with its own prompts,
caches and failure modes.

    POST /network/threads/{thread_id}/documents/{document_id}/analyze
    POST /network/threads/{thread_id}/documents/{document_id}/questions
    POST /network/threads/{thread_id}/documents/{document_id}/ask

Authorisation, in one place (_chat_document below) and never trusted from
the client:

  * the caller must be a participant in the thread
  * the document must have been SENT IN THAT THREAD - a document id the
    caller owns but which was never shared here is a 404, so this cannot
    become a way to run AI over someone's private uploads by guessing ids
  * the caller must be an advocate

The last rule is a product decision rather than a security one: the feature
exists to cut an advocate's document-review and client-questioning work.
Opening it to clients means deleting the require_advocate dependency here
and the isAdvocate check in the frontend - nothing else.

Failures are mapped rather than leaked: an unreadable file is a 400 with
the extractor's own message (it says whether OCR is needed), a Gemini or
storage problem is a 503. The response never carries a partial object -
an advocate acting on half an extraction is the thing to avoid.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import doc_intel, gemini, models, storage
from .auth import require_advocate
from .database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/network/threads", tags=["document-intelligence"])

# Messages read back for context. Enough to cover the conversation that led
# to the document being sent without turning every analyse click into a
# large prompt.
CONTEXT_MESSAGES = 40


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DocumentRef(BaseModel):
    id: int
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


class Party(BaseModel):
    name: str
    role: str = "other"
    organisation: str = ""


class DatedEvent(BaseModel):
    date: str
    event: str = ""


class LawRef(BaseModel):
    act: str = ""
    section: str = ""
    context: str = ""


class Clause(BaseModel):
    heading: str = ""
    summary: str
    kind: str = "other"


class Amount(BaseModel):
    amount: str
    purpose: str = ""


class Gap(BaseModel):
    issue: str
    why_it_matters: str = ""
    severity: str = "medium"


class AnalysisOut(BaseModel):
    document: DocumentRef
    document_type: str
    type_code: str = "other"
    confidence: str = "medium"
    summary: str = ""
    parties: List[Party] = []
    dates: List[DatedEvent] = []
    claims: List[str] = []
    laws: List[LawRef] = []
    clauses: List[Clause] = []
    amounts: List[Amount] = []
    key_facts: List[str] = []
    gaps: List[Gap] = []
    truncated: bool = False
    cached: bool = False


class SuggestedQuestion(BaseModel):
    question: str
    why: str = ""
    gap: str = ""
    category: str = "facts"
    priority: str = "medium"


class QuestionsOut(BaseModel):
    document: DocumentRef
    document_type: str = ""
    questions: List[SuggestedQuestion] = []
    already_known: List[str] = []
    truncated: bool = False
    cached: bool = False


class AskTurn(BaseModel):
    role: str = "user"          # "user" (the advocate) or "assistant"
    content: str = ""


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    # The document thread so far, sent back each time. Nothing is stored
    # server-side: this conversation is scratch work about one file, not a
    # record of the matter.
    history: List[AskTurn] = []


class AskOut(BaseModel):
    answer: str
    found_in_document: bool = False
    evidence: List[str] = []
    note: str = ""


# ---------------------------------------------------------------------------
# Authorisation + loading
# ---------------------------------------------------------------------------

def _chat_document(
    db: Session, thread_id: int, document_id: int, user: models.User
) -> models.Document:
    """The document, only if it was shared in a thread this user is in.

    Membership is part of the query rather than a check afterwards, so there
    is no path where the row is loaded first and authorised second. Anything
    that fails is a 404 and not a 403 - a 403 would confirm which documents
    and threads exist.
    """
    doc = (
        db.query(models.Document)
        .join(models.ChatMessage, models.ChatMessage.id == models.Document.message_id)
        .join(
            models.ChatParticipant,
            models.ChatParticipant.thread_id == models.ChatMessage.thread_id,
        )
        .filter(
            models.Document.id == document_id,
            models.ChatMessage.thread_id == thread_id,
            models.ChatParticipant.user_id == user.id,
        )
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return doc


def _thread_context(db: Session, thread_id: int, advocate_id: int) -> str:
    """Recent conversation, as the question engine sees it.

    The newest messages, not the ones around the document. Anything said
    after a file arrives is usually the client explaining it, and it is
    also where the answers to the last round of questions land - which is
    what lets the engine stop re-asking them.
    """
    rows = (
        db.query(models.ChatMessage)
        .filter(models.ChatMessage.thread_id == thread_id)
        .order_by(models.ChatMessage.id.desc())
        .limit(CONTEXT_MESSAGES)
        .all()
    )
    rows.reverse()
    return doc_intel.chat_context(
        [
            {
                "role": "advocate" if m.sender_id == advocate_id else "client",
                "content": m.content,
            }
            for m in rows
        ]
    )


async def _text_of(doc: models.Document) -> str:
    try:
        return await doc_intel.load_text(doc.storage_path, doc.content_type, doc.filename)
    except ValueError as exc:
        # Unreadable file - a scan, a photo, an unsupported type. The
        # extractor's message is the useful one ("needs OCR"), so it is
        # passed through rather than replaced.
        raise HTTPException(status_code=400, detail=str(exc))
    except storage.StorageError as exc:
        logger.error("Storage read failed for document %s: %s", doc.id, exc)
        raise HTTPException(status_code=503, detail="Could not read that document.")
    except Exception as exc:
        logger.exception("Could not load document %s: %s", doc.id, exc)
        raise HTTPException(status_code=503, detail="Could not read that document.")


def _ref(doc: models.Document) -> DocumentRef:
    return DocumentRef(
        id=doc.id,
        filename=doc.filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
    )


def _ai_failure(what: str, exc: Exception) -> HTTPException:
    logger.exception("%s failed: %s", what, exc)
    if isinstance(exc, gemini.GeminiError):
        return HTTPException(
            status_code=503,
            detail="The AI service did not return a usable result. Try again.",
        )
    return HTTPException(status_code=503, detail=f"Could not {what}. Try again.")


# ---------------------------------------------------------------------------
# 1. Analyze
# ---------------------------------------------------------------------------

@router.post(
    "/{thread_id}/documents/{document_id}/analyze",
    response_model=AnalysisOut,
)
async def analyze_document(
    thread_id: int,
    document_id: int,
    force: bool = Query(
        default=False, description="Re-run even if an extraction is cached."
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Structured extraction of one document shared in this thread."""
    doc = _chat_document(db, thread_id, document_id, current_user)
    text = await _text_of(doc)

    if force:
        doc_intel.forget(doc.id)

    context = _thread_context(db, thread_id, current_user.id)

    try:
        analysis, cached = await doc_intel.analysis_for(
            doc.id, text, doc.filename, context, force=force
        )
    except Exception as exc:
        raise _ai_failure("analyse that document", exc)

    return AnalysisOut(document=_ref(doc), cached=cached, **analysis)


# ---------------------------------------------------------------------------
# 2. Suggest questions
# ---------------------------------------------------------------------------

@router.post(
    "/{thread_id}/documents/{document_id}/questions",
    response_model=QuestionsOut,
)
async def suggest_questions(
    thread_id: int,
    document_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """What the advocate still needs to ask the client about this document.

    Runs the extraction first when it has not been run. The questions are
    only as good as the gap list behind them, and asking the model to find
    the gaps and phrase the questions in a single pass produced generic
    checklists - the two-step version is what makes them specific to the
    file.
    """
    doc = _chat_document(db, thread_id, document_id, current_user)
    text = await _text_of(doc)

    if force:
        doc_intel.forget(doc.id)

    context = _thread_context(db, thread_id, current_user.id)

    try:
        await doc_intel.analysis_for(doc.id, text, doc.filename, context, force=False)
        result, cached = await doc_intel.questions_for(
            doc.id, text, doc.filename, context, force=force
        )
    except Exception as exc:
        raise _ai_failure("suggest questions for that document", exc)

    return QuestionsOut(document=_ref(doc), cached=cached, **result)


# ---------------------------------------------------------------------------
# 3. Ask about the document
# ---------------------------------------------------------------------------

@router.post(
    "/{thread_id}/documents/{document_id}/ask",
    response_model=AskOut,
)
async def ask_about_document(
    thread_id: int,
    document_id: int,
    payload: AskIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Q&A scoped to one document. Answers say when they are not in the file."""
    doc = _chat_document(db, thread_id, document_id, current_user)
    text = await _text_of(doc)

    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question first.")

    history: List[Dict[str, Any]] = [
        {"role": t.role, "content": t.content} for t in payload.history
    ]

    try:
        result = await doc_intel.ask(
            text[: doc_intel.DOC_CHARS],
            question,
            filename=doc.filename,
            history=history,
            truncated=len(text) > doc_intel.DOC_CHARS,
        )
    except Exception as exc:
        raise _ai_failure("answer that question", exc)

    return AskOut(**result)