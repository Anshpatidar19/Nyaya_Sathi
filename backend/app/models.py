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

    # Links this profile row to Supabase Auth (auth.users.id, a UUID).
    # Nullable so rows that predate the switch can be back-filled on login.
    auth_id = Column(String, unique=True, index=True, nullable=True)

    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)

    # Supabase Auth owns passwords now. Kept nullable so the old hashes stay
    # readable during migration; nothing in the app reads this any more.
    hashed_password = Column(String, nullable=True)
    preferred_language = Column(String, default="en")
    state = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    queries = relationship("QueryLog", back_populates="user")
    documents = relationship(
        "Document", back_populates="user", cascade="all, delete-orphan"
    )


class QueryLog(Base):
    """Every question a user asks, and the answer that was returned."""

    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer_title = Column(String, nullable=True)
    answer_body = Column(Text, nullable=True)
    citations_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="queries")


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