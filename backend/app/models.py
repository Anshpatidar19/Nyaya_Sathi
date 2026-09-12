import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
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

    # --- networking profile fields ----------------------------------------
    # Added for the advocate-discovery feature. All nullable: accounts that
    # existed before this feature keep working and simply show an incomplete
    # profile until the owner fills it in.
    city = Column(String, nullable=True, index=True)
    bio = Column(Text, nullable=True)
    # Key inside the avatars bucket, not a URL. Unlike legal documents,
    # avatars live in a PUBLIC bucket - they are shown to every user in
    # search results, and signing a URL per row would mean 30 extra round
    # trips to Supabase for one page of results.
    avatar_path = Column(String, nullable=True)

    # True only for seeded test accounts. This is what makes the demo data
    # removable in one filtered delete that cannot reach a real account.
    is_demo = Column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )

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
    advocate_profile = relationship(
        "AdvocateProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
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
    # Indexed: the matters list always orders by this, newest activity first.
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow,
        index=True,
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
    """A thread of questions put to the AI. Follow-ups need somewhere to hang
    off.

    NOTE: this is the AI Q&A thread, NOT the advocate chat. Person-to-person
    messaging lives in ChatThread / ChatMessage below. The two were kept
    under different names deliberately - reusing this table for chat would
    have broken /conversations, /ask/history and the Matter research tab.

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
    # Indexed: the conversation list always orders by this, newest first.
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow,
        index=True,
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
    # Indexed: /ask/history filters on this for every page load. Missing this
    # index meant a full scan of query_logs - across every user, not just the
    # one asking - which is why history got slower as the table grew.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
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
    # Indexed: /ask/history orders by this, on top of the user_id filter.
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

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
    # Set when the file was shared as a chat attachment. Access control then
    # follows the thread's participants rather than the uploader alone, so
    # the advocate on the other side can open what the client sent.
    message_id = Column(
        Integer, ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    filename = Column(String, nullable=False)          # original name, for display
    storage_path = Column(String, nullable=False, unique=True)
    content_type = Column(String, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    note = Column(Text, nullable=True)                 # user's own description
    # Indexed: /documents orders by this (the attach-a-file picker, and the
    # Matter detail page's documents tab).
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    user = relationship("User", back_populates="documents")
    matter = relationship("Matter", back_populates="documents")
    message = relationship("ChatMessage", back_populates="attachments")


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


# ===========================================================================
# Advocate discovery, connections and messaging
# ===========================================================================
#
# Everything below is the "Find an Advocate -> connect -> chat" layer. Three
# design notes, because they are the decisions that would otherwise look
# arbitrary later:
#
#   1. Chat lives in ChatThread/ChatMessage, never in Conversation. See the
#      note on Conversation above.
#   2. A chat thread hangs off an accepted Connection, not off two user ids.
#      That means "can these two talk?" is answered by the connection's
#      status - there is no way to open a thread without an acceptance.
#   3. Practice areas, courts and languages are comma-separated strings
#      rather than join tables. Searching them is an ILIKE, which is fine at
#      this scale and keeps the migration to one file. If the advocate list
#      ever grows past a few thousand rows, these become join tables and the
#      search endpoint is the only thing that has to change.


# Connection status values. Kept as plain strings rather than a Postgres enum
# so adding a state later is a code change, not a migration.
CONNECTION_PENDING = "pending"
CONNECTION_ACCEPTED = "accepted"
CONNECTION_REJECTED = "rejected"
CONNECTION_CANCELLED = "cancelled"

CONNECTION_STATUSES = {
    CONNECTION_PENDING,
    CONNECTION_ACCEPTED,
    CONNECTION_REJECTED,
    CONNECTION_CANCELLED,
}


class AdvocateProfile(Base):
    """The professional half of an advocate's account.

    Separate from User rather than a pile of nullable columns on it, because
    a client account has none of these fields and because the existence of
    this row is what makes an advocate appear in search. An advocate account
    with no profile row is invisible until its owner fills one in - which is
    deliberate: it keeps real accounts that signed up before this feature out
    of the directory until they opt in.
    """

    __tablename__ = "advocate_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )

    # Professional identity
    bar_council_number = Column(String, nullable=True)
    years_experience = Column(Integer, nullable=True, index=True)
    professional_bio = Column(Text, nullable=True)
    current_firm = Column(String, nullable=True)
    # Where they actually practise, when it differs from the city on the
    # account (an advocate living in Dewas but practising in Indore).
    practice_city = Column(String, nullable=True, index=True)

    # The headline area, shown on the search card.
    specialization = Column(String, nullable=True, index=True)
    # Comma-separated. "Criminal Law, Bail Matters, Cheque Bounce"
    practice_areas = Column(String, nullable=True)
    # Comma-separated. "District Court Indore, MP High Court"
    courts = Column(String, nullable=True)
    # Comma-separated. "Hindi, English"
    languages = Column(String, nullable=True)

    # Education
    llb_college = Column(String, nullable=True)
    llb_year = Column(Integer, nullable=True)
    llm_college = Column(String, nullable=True)
    other_qualifications = Column(String, nullable=True)

    # Experience narrative
    previous_firms = Column(Text, nullable=True)
    notable_experience = Column(Text, nullable=True)

    # An advocate can take themselves out of the directory without deleting
    # their profile. Search filters on this.
    is_listed = Column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    user = relationship("User", back_populates="advocate_profile")


class Connection(Base):
    """A request from one account to another, and its outcome.

    The unique constraint on (requester_id, receiver_id) is what makes
    duplicate requests impossible at the database level rather than only in
    the handler. A re-request after a rejection UPDATES this row back to
    pending instead of inserting a second one - so the pair always has
    exactly one row, and its status is the whole story.
    """

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("requester_id", "receiver_id", name="uq_connection_pair"),
        Index("ix_connections_receiver_status", "receiver_id", "status"),
        Index("ix_connections_requester_status", "requester_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    requester_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    receiver_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status = Column(
        String, nullable=False, default=CONNECTION_PENDING,
        server_default=CONNECTION_PENDING,
    )
    # The one-line "why I'm reaching out" shown on the advocate's request
    # card. Not privileged case detail - that belongs in the chat, after the
    # connection exists.
    intro_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    requester = relationship("User", foreign_keys=[requester_id])
    receiver = relationship("User", foreign_keys=[receiver_id])
    thread = relationship(
        "ChatThread",
        back_populates="connection",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ChatThread(Base):
    """A private one-to-one conversation, created when a connection is
    accepted.

    One thread per connection, enforced by the unique FK. `last_message_at`
    is denormalised so the conversation list can order by recency without
    aggregating over chat_messages on every page load.
    """

    __tablename__ = "chat_threads"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(
        Integer, ForeignKey("connections.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_message_at = Column(
        DateTime, default=datetime.datetime.utcnow, index=True
    )

    connection = relationship("Connection", back_populates="thread")
    participants = relationship(
        "ChatParticipant", back_populates="thread", cascade="all, delete-orphan"
    )
    messages = relationship(
        "ChatMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatParticipant(Base):
    """Who is allowed in a thread.

    Redundant with the connection's two user ids today, and that is on
    purpose: every read and write checks membership against THIS table, so
    the authorisation check is one indexed lookup and does not change shape
    if a thread ever holds three people.
    """

    __tablename__ = "chat_participants"
    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_participant_thread_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(
        Integer, ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # When this participant last opened the thread. Unread counts are derived
    # from this rather than from per-message read receipts, which would mean
    # a write per message per reader.
    last_read_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    thread = relationship("ChatThread", back_populates="participants")
    user = relationship("User")


class ChatMessage(Base):
    """One message in a thread."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        # The message poll is "everything in this thread after id N", which
        # is exactly this index.
        Index("ix_chat_messages_thread_id_id", "thread_id", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(
        Integer, ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sender_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    # Set when the OTHER participant reads it. Per-message so the sender can
    # see a tick; the unread COUNT uses participant.last_read_at instead.
    read_at = Column(DateTime, nullable=True)

    thread = relationship("ChatThread", back_populates="messages")
    sender = relationship("User")
    # Files shared with this message. Documents point here, not the reverse,
    # so an attachment can be dropped without touching the message.
    attachments = relationship("Document", back_populates="message")


# Notification types. Strings, again so a new event is not a migration.
NOTIFY_CONNECTION_REQUEST = "connection_request"
NOTIFY_CONNECTION_ACCEPTED = "connection_accepted"
NOTIFY_CONNECTION_REJECTED = "connection_rejected"
NOTIFY_NEW_MESSAGE = "new_message"
NOTIFY_NEW_ATTACHMENT = "new_attachment"


class Notification(Base):
    """An event worth telling one account about.

    Deliberately denormalised: title and body are written at creation time
    rather than rendered from the related rows on read. A notification should
    still read correctly after the thing it refers to changes or is deleted.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # The bell badge is "count where recipient = me and not read".
        Index("ix_notifications_recipient_unread", "recipient_id", "is_read"),
    )

    id = Column(Integer, primary_key=True, index=True)
    recipient_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)

    # All nullable and all SET NULL on delete: a notification outliving its
    # subject is better than a delete that fails on a foreign key.
    related_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    related_connection_id = Column(
        Integer, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
    related_thread_id = Column(
        Integer, ForeignKey("chat_threads.id", ondelete="SET NULL"), nullable=True
    )

    is_read = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    recipient = relationship("User", foreign_keys=[recipient_id])
    related_user = relationship("User", foreign_keys=[related_user_id])