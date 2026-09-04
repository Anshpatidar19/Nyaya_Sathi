from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------

class EmailOnly(BaseModel):
    email: EmailStr

class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    state: Optional[str] = None
    preferred_language: str = "en"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    state: Optional[str] = None
    preferred_language: str

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Legal Q&A ----------

class Citation(BaseModel):
    title: str
    source: str
    docid: Optional[str] = None
    url: Optional[str] = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    state: Optional[str] = None
    language: str = "en"
    # Omit to start a new thread; pass an id to continue one.
    conversation_id: Optional[int] = None


class AskResponse(BaseModel):
    # Returned so the client can continue the thread on the next question.
    conversation_id: Optional[int] = None
    title: str
    body: str
    citations: List[Citation]
    next_steps: List[str]


class QueryHistoryItem(BaseModel):
    id: int
    question: str
    answer_title: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Kanoon passthrough ----------

class KanoonSearchResult(BaseModel):
    docid: str
    title: str
    court: Optional[str] = None
    date: Optional[str] = None
    snippet: Optional[str] = None
    url: Optional[str] = None


# ---------- Documents ----------

class DocumentOut(BaseModel):
    id: int
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    note: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

    # ---------- Drafting & review ----------

class DraftRequest(BaseModel):
    doc_type: Optional[str] = None
    instructions: str
    details: Optional[dict] = None


class DraftCitation(BaseModel):
    title: str
    source: str
    url: Optional[str] = None


class DraftResponse(BaseModel):
    title: str
    body: str
    citations: list[DraftCitation] = []
    missing_information: list[str] = []
    notes: list[str] = []
    needs_advocate: bool = False


class ReviewRequest(BaseModel):
    document_text: Optional[str] = None
    document_id: Optional[int] = None      # review a previously uploaded file
    doc_type: Optional[str] = None
    context: Optional[str] = None


class ReviewFlag(BaseModel):
    clause: str
    issue: str
    suggestion: str
    severity: str
    basis: str
    citation: Optional[DraftCitation] = None


class ReviewResponse(BaseModel):
    summary: str
    risk_level: str
    flags: list[ReviewFlag] = []
    missing_clauses: list[str] = []
    truncated: bool = False

# ---------- Conversations ----------

class ConversationOut(BaseModel):
    id: int
    title: str
    mode: str
    updated_at: datetime

    class Config:
        from_attributes = True


class TurnOut(BaseModel):
    """One question/answer pair, with the full original payload so a
    reloaded thread renders exactly as it did live."""
    id: int
    mode: str
    question: str
    answer_title: Optional[str] = None
    answer_body: Optional[str] = None
    payload: Optional[dict] = None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: int
    title: str
    mode: str
    created_at: datetime
    turns: list[TurnOut] = []
