from datetime import date, datetime
from typing import List, Literal, Optional

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
    # Chosen on the signup card. Anything else is rejected by pydantic, so a
    # hand-crafted request can't invent a third role.
    role: Literal["user", "advocate"] = "user"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    state: Optional[str] = None
    preferred_language: str
    role: str = "user"

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
    # Short when a document is attached - "explain this" is a fair question.
    question: str = Field(min_length=1, max_length=1000)
    state: Optional[str] = None
    language: str = "en"
    # Omit to start a new thread; pass an id to continue one.
    conversation_id: Optional[int] = None
    # Ask about a file the user uploaded via POST /documents.
    document_id: Optional[int] = None
    # File this thread under a matter (advocates only).
    matter_id: Optional[int] = None


class Grounding(BaseModel):
    """How well the answer is supported. Computed from retrieval and citation
    signals, not asked of the model."""
    level: str = "thin"              # well_grounded | partly_grounded | thin
    label: str = ""
    reasons: List[str] = []
    sources: int = 0
    cited: int = 0
    repealed: int = 0


class AskResponse(BaseModel):
    # Returned so the client can continue the thread on the next question.
    conversation_id: Optional[int] = None
    title: str
    body: str
    citations: List[Citation]
    next_steps: List[str]
    grounding: Optional[Grounding] = None
    # Echoed back so the thread can show which file the answer was about.
    document_name: Optional[str] = None


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
    matter_id: Optional[int] = None
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
    document_name: Optional[str] = None


# ---------- Password reset ----------

class PasswordReset(BaseModel):
    """Completes a reset. `access_token` comes from the emailed link's URL
    fragment - Supabase puts it there, the frontend reads it and posts it
    back so the password change is made server-side."""
    access_token: str
    new_password: str = Field(min_length=8, max_length=128)

# ---------- Generate Arguments (advocate) ----------

class ArgumentsRequest(BaseModel):
    """Either facts, a document_id, or both. The side is required - an
    argument set with no party to argue for is just a summary."""
    facts: Optional[str] = Field(default=None, max_length=30000)
    document_id: Optional[int] = None
    side: str = "petitioner"
    issue: Optional[str] = Field(default=None, max_length=500)
    court: Optional[str] = Field(default=None, max_length=160)
    state: Optional[str] = None


class Authority(BaseModel):
    title: str
    source: str
    docid: Optional[str] = None
    url: Optional[str] = None


class CaseOverview(BaseModel):
    parties: str = ""
    material_facts: str = ""
    cause_of_action: str = ""
    stage: str = ""
    relief_sought: str = ""


class FactSplit(BaseModel):
    supporting: list[str] = []
    opposing: list[str] = []
    disputed: list[str] = []


class LegalIssue(BaseModel):
    question: str
    elements: list[str] = []
    burden: str = ""
    importance: str = "medium"
    authorities: list[Authority] = []


class Argument(BaseModel):
    title: str
    proposition: str = ""
    legal_basis: str = ""
    application: str = ""
    strength: str = "moderate"
    strength_reason: str = ""
    authorities: list[Authority] = []


class AlternativeArgument(BaseModel):
    title: str
    proposition: str = ""
    legal_basis: str = ""
    kind: str = "other"
    authorities: list[Authority] = []


class OpposingArgument(BaseModel):
    title: str
    position: str = ""
    legal_basis: str = ""
    strength: str = "moderate"
    authorities: list[Authority] = []


class Rebuttal(BaseModel):
    opposing_argument: str = ""
    response: str = ""
    basis: str = ""
    authorities: list[Authority] = []


class Weakness(BaseModel):
    issue: str = ""
    risk: str = ""
    mitigation: str = ""


class EvidenceItem(BaseModel):
    item: str
    category: str = "other"
    status: str = "available"
    importance: str = "medium"


class QAPair(BaseModel):
    question: str
    answer: str = ""


class OpposingQuestion(BaseModel):
    question: str
    response: str = ""


class ArgumentsResponse(BaseModel):
    title: str
    side: str
    document_name: Optional[str] = None
    truncated: bool = False
    case_overview: CaseOverview
    facts: FactSplit
    issues: list[LegalIssue] = []
    arguments: list[Argument] = []
    alternative_arguments: list[AlternativeArgument] = []
    opposing_arguments: list[OpposingArgument] = []
    rebuttals: list[Rebuttal] = []
    strengths: list[str] = []
    weaknesses: list[Weakness] = []
    evidence: list[EvidenceItem] = []
    judicial_questions: list[QAPair] = []
    opposing_questions: list[OpposingQuestion] = []
    strategy: list[str] = []
    assumptions: list[str] = []
    missing_information: list[str] = []
    sources: list[Authority] = []


# ---------- Matters (advocate) ----------

MATTER_STATUS = Literal["active", "archived"]
EVENT_KIND = Literal["hearing", "filing", "deadline", "meeting", "other"]


class MatterBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    client_name: Optional[str] = Field(default=None, max_length=160)
    case_number: Optional[str] = Field(default=None, max_length=120)
    court: Optional[str] = Field(default=None, max_length=200)
    side: Optional[str] = Field(default=None, max_length=40)
    description: Optional[str] = Field(default=None, max_length=4000)
    notes: Optional[str] = Field(default=None, max_length=8000)
    tags: Optional[str] = Field(default=None, max_length=300)
    urgent: bool = False


class MatterCreate(MatterBase):
    pass


class MatterUpdate(BaseModel):
    """Every field optional - the detail page saves one section at a time."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    client_name: Optional[str] = None
    case_number: Optional[str] = None
    court: Optional[str] = None
    side: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    urgent: Optional[bool] = None
    status: Optional[MATTER_STATUS] = None


class MatterEventBase(BaseModel):
    kind: EVENT_KIND = "hearing"
    title: str = Field(min_length=1, max_length=200)
    event_date: date
    event_time: Optional[str] = Field(default=None, max_length=40)
    location: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=4000)
    done: bool = False


class MatterEventCreate(MatterEventBase):
    pass


class MatterEventUpdate(BaseModel):
    kind: Optional[EVENT_KIND] = None
    title: Optional[str] = None
    event_date: Optional[date] = None
    event_time: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    done: Optional[bool] = None


class MatterEventOut(MatterEventBase):
    id: int
    matter_id: int

    class Config:
        from_attributes = True


class MatterNoteBase(BaseModel):
    note_date: date
    title: Optional[str] = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


class MatterNoteCreate(MatterNoteBase):
    pass


class MatterNoteUpdate(BaseModel):
    note_date: Optional[date] = None
    title: Optional[str] = None
    body: Optional[str] = None


class MatterNoteOut(MatterNoteBase):
    id: int
    matter_id: int
    updated_at: datetime

    class Config:
        from_attributes = True


class MatterOut(MatterBase):
    """List row. Counts and next hearing are computed, not stored - keeping
    them in the table would mean invalidating on every child write."""
    id: int
    status: str
    created_at: datetime
    updated_at: datetime
    event_count: int = 0
    note_count: int = 0
    document_count: int = 0
    research_count: int = 0
    next_hearing: Optional[MatterEventOut] = None

    class Config:
        from_attributes = True


class MatterDetail(MatterOut):
    events: list[MatterEventOut] = []
    notes_entries: list[MatterNoteOut] = []
    documents: list[DocumentOut] = []
    research: list["ConversationOut"] = []


class MatterLink(BaseModel):
    """Move a document or a research thread into (or out of) a matter."""
    matter_id: Optional[int] = None


# ---------- Conversations ----------

class ConversationOut(BaseModel):
    id: int
    title: str
    mode: str
    updated_at: datetime
    matter_id: Optional[int] = None

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


# MatterDetail references ConversationOut, which is defined below it.
MatterDetail.model_rebuild()