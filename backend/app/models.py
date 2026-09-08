import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
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

    # "user" or "advocate". Chosen at signup and never verified - this is a
    # product distinction (which tools you see), not a security boundary.
    role = Column(String, nullable=False, default="user", server_default="user")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    queries = relationship("QueryLog", back_populates="user")
    matters = relationship(
        "Matter", back_populates="user", cascade="all, delete-orphan"
    )
    conversations = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan"
    )
    documents = relationship(
        "Document", back_populates="user", cascade="all, delete-orphan"
    )


class Matter(Base):
    """A case file. Everything an advocate does for one client, in one place.

    Advocate-only in practice - the API gates it - but the column lives on
    users like any other, so a role change doesn't orphan rows.
    """

    __tablename__ = "matters"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    title = Column(String, nullable=False)              # "Meridian v. Sundaram"
    client_name = Column(String, nullable=True)
    case_number = Column(String, nullable=True)         # "CS 118/2026"
    court = Column(String, nullable=True)
    # Which party the advocate appears for - reused by Generate Arguments.
    side = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    # Free-form matter-level notes, distinct from the dated hearing notes.
    notes = Column(Text, nullable=True)
    # Comma-separated. A join table would be tidier but this is a demo and
    # tags are only ever read as a whole.
    tags = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active")   # active | archived
    urgent = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    user = relationship("User", back_populates="matters")
    events = relationship(
        "MatterEvent",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="MatterEvent.event_date",
    )
    notes_entries = relationship(
        "MatterNote",
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="MatterNote.note_date.desc()",
    )
    documents = relationship("Document", back_populates="matter")
    conversations = relationship("Conversation", back_populates="matter")


class MatterEvent(Base):
    """A dated entry on the matter's timeline - hearing, filing, deadline,
    client meeting. `done` is what lets past hearings drop out of the
    upcoming list without being deleted."""

    __tablename__ = "matter_events"

    id = Column(Integer, primary_key=True, index=True)
    matter_id = Column(Integer, ForeignKey("matters.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    kind = Column(String, nullable=False, default="hearing")
    title = Column(String, nullable=False)
    event_date = Column(Date, nullable=False, index=True)
    event_time = Column(String, nullable=True)          # "11:00 AM", free text
    location = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    done = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    matter = relationship("Matter", back_populates="events")


class MatterNote(Base):
    """What actually happened on a given date. Written after the hearing,
    which is why it is separate from the event that scheduled it."""

    __tablename__ = "matter_notes"

    id = Column(Integer, primary_key=True, index=True)
    matter_id = Column(Integer, ForeignKey("matters.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    note_date = Column(Date, nullable=False, index=True)
    title = Column(String, nullable=True)
    body = Column(Text, nullable=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    matter = relationship("Matter", back_populates="notes_entries")


class Conversation(Base):
    """A thread of questions. Follow-ups need somewhere to hang off.

    Title is taken from the first question so the sidebar has something
    readable without a separate summarisation call.
    """

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Nullable: research done outside any matter is still worth keeping.
    matter_id = Column(Integer, ForeignKey("matters.id"), nullable=True, index=True)
    title = Column(String, nullable=False, default="New conversation")
    mode = Column(String, nullable=False, default="ask")   # ask | draft | review | argue
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    user = relationship("User", back_populates="conversations")
    matter = relationship("Matter", back_populates="conversations")
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
    # Filed under a matter, or loose if uploaded straight into a chat.
    matter_id = Column(Integer, ForeignKey("matters.id"), nullable=True, index=True)
    filename = Column(String, nullable=False)          # original name, for display
    storage_path = Column(String, nullable=False, unique=True)
    content_type = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    note = Column(Text, nullable=True)                 # user's own description
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="documents")
    matter = relationship("Matter", back_populates="documents")


class AnswerTranslation(Base):
    """A translated copy of one answer, kept so switching back is instant.

    The English original stays in QueryLog and is never overwritten - it is
    what the validator passed and what the grounding badge was computed
    against. This table holds renderings of it, nothing more.

    Cached rather than regenerated because translation is deterministic
    enough that paying for it twice buys nothing, and because a user toggling
    between two languages to compare wording should not wait twice.
    """

    __tablename__ = "answer_translations"
    __table_args__ = (
        UniqueConstraint("query_log_id", "language", name="uq_translation_turn_lang"),
    )

    id = Column(Integer, primary_key=True, index=True)
    query_log_id = Column(
        Integer, ForeignKey("query_logs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    language = Column(String(8), nullable=False)

    title = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    next_steps_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)