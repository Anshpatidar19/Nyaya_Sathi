from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------

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


class AskResponse(BaseModel):
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