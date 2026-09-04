import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    preferred_language = Column(String, default="en")
    state = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    queries = relationship("QueryLog", back_populates="user")
    conversations = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan"
    )
    documents = relationship(
        "Document", back_populates="user", cascade="all, delete-orphan"
    )


class Conversation(Base):
    """A thread of questions. Follow-ups need somewhere to hang off.

    Title is taken from the first question so the sidebar has something
    readable without a separate summarisation call.
    """

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False, default="New conversation")
    mode = Column(String, nullable=False, default="ask")   # ask | draft | review
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    user = relationship("User", back_populates="conversations")
    turns = relationship(
        "QueryLog",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="QueryLog.created_at",
    )


class QueryLog(Base):
    """One turn: a question and the answer that was returned."""

    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Nullable so rows that predate threading still load.
    conversation_id = Column(
        Integer, ForeignKey("conversations.id"), nullable=True, index=True
    )
    # Which surface produced this turn, so a reloaded thread renders correctly.
    mode = Column(String, nullable=False, default="ask")
    # Full response payload, so a refreshed page shows the same rich result
    # (citations, flags, next steps) rather than plain text.
    payload_json = Column(Text, nullable=True)
    question = Column(Text, nullable=False)
    answer_title = Column(String, nullable=True)
    answer_body = Column(Text, nullable=True)
    citations_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="queries")
    conversation = relationship("Conversation", back_populates="turns")


class Document(Base):
    """A file a user uploaded - notice, agreement, FIR copy, etc.

    The bytes live in Supabase Storage; this table holds the metadata and the
    storage path. `storage_path` is the key inside the bucket, not a URL -
    URLs are signed on demand so the bucket can stay private.
    """

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String, nullable=False)          # original name, for display
    storage_path = Column(String, nullable=False, unique=True)
    content_type = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    note = Column(Text, nullable=True)                 # user's own description
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="documents")