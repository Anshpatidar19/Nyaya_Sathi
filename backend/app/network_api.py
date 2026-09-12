"""Advocate discovery, connection requests, private chat and notifications.

This is the "I have a legal problem -> I found an advocate -> we're talking"
half of the platform. The AI half (/ask, /draft, /matters) is untouched.

Everything is mounted under /network so nothing collides with the existing
/conversations (AI threads) or /documents routes.

Authorisation rules, all enforced here rather than in the frontend:

  * You can only read or write your own profile.
  * A connection can only be accepted or rejected by its RECEIVER, and only
    cancelled by its REQUESTER, and only while it is pending.
  * A chat thread is readable only by rows in chat_participants for it.
    An id belonging to someone else's thread is a 404, not a 403 - a 403
    would confirm the thread exists.
  * A thread cannot exist without an accepted connection behind it, so
    "are we allowed to talk" is never a separate question.

Schemas live in this file rather than in schemas.py. That file is the AI
surface's contract and is already long; keeping this feature's request and
response shapes next to the handlers that use them means one file to read
when something here is wrong.
"""

import datetime
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from . import models, storage
from .auth import get_current_user, require_advocate
from .config import settings
from .database import get_db

router = APIRouter(prefix="/network", tags=["network"])


# Shown in the chat header and on the advocate profile. Required by the
# product boundary: accepting a connection is not accepting a case.
CONNECTION_DISCLAIMER = (
    "A connection on Nyaya Sathi is an initial communication channel only. "
    "It does not create an advocate-client relationship, does not mean the "
    "advocate has accepted your case, and messages here are not a formal "
    "legal opinion. Representation is subject to the advocate's agreement "
    "and applicable professional requirements."
)

MAX_MESSAGE_CHARS = 5000


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class UserCard(BaseModel):
    """The minimum needed to render a person anywhere - request card,
    connection row, chat header, notification."""

    id: int
    name: str
    role: str
    city: Optional[str] = None
    state: Optional[str] = None
    avatar_url: Optional[str] = None
    is_demo: bool = False


class AdvocateProfileIn(BaseModel):
    bar_council_number: Optional[str] = Field(default=None, max_length=80)
    years_experience: Optional[int] = Field(default=None, ge=0, le=70)
    professional_bio: Optional[str] = Field(default=None, max_length=3000)
    current_firm: Optional[str] = Field(default=None, max_length=200)
    practice_city: Optional[str] = Field(default=None, max_length=120)
    specialization: Optional[str] = Field(default=None, max_length=120)
    practice_areas: Optional[str] = Field(default=None, max_length=500)
    courts: Optional[str] = Field(default=None, max_length=500)
    languages: Optional[str] = Field(default=None, max_length=300)
    llb_college: Optional[str] = Field(default=None, max_length=200)
    llb_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    llm_college: Optional[str] = Field(default=None, max_length=200)
    other_qualifications: Optional[str] = Field(default=None, max_length=500)
    previous_firms: Optional[str] = Field(default=None, max_length=2000)
    notable_experience: Optional[str] = Field(default=None, max_length=2000)
    is_listed: bool = True


class AdvocateProfileOut(AdvocateProfileIn):
    id: int
    user_id: int

    class Config:
        from_attributes = True


class MyProfileOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    city: Optional[str] = None
    state: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    preferred_language: str = "en"
    is_demo: bool = False
    advocate_profile: Optional[AdvocateProfileOut] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    city: Optional[str] = Field(default=None, max_length=120)
    state: Optional[str] = Field(default=None, max_length=120)
    bio: Optional[str] = Field(default=None, max_length=2000)


class ConnectionState(BaseModel):
    """How the viewer currently stands with the person being viewed.

    `direction` is "outgoing" when the viewer sent it, "incoming" when they
    received it. The frontend uses status+direction to pick between
    [Connect] / [Request Sent] / [Respond] / [Message].
    """

    status: str = "none"       # none | pending | accepted | rejected | cancelled
    direction: Optional[str] = None
    connection_id: Optional[int] = None
    thread_id: Optional[int] = None


class AdvocateCard(BaseModel):
    user: UserCard
    specialization: Optional[str] = None
    practice_areas: Optional[str] = None
    courts: Optional[str] = None
    languages: Optional[str] = None
    years_experience: Optional[int] = None
    current_firm: Optional[str] = None
    practice_city: Optional[str] = None
    short_bio: Optional[str] = None
    connection: ConnectionState = ConnectionState()


class AdvocateSearchOut(BaseModel):
    items: List[AdvocateCard]
    total: int
    page: int
    per_page: int
    pages: int


class AdvocateDetailOut(BaseModel):
    user: UserCard
    bio: Optional[str] = None
    profile: Optional[AdvocateProfileOut] = None
    connection: ConnectionState = ConnectionState()
    disclaimer: str = CONNECTION_DISCLAIMER


class ConnectionCreate(BaseModel):
    receiver_id: int
    # The "why I'm reaching out" line. Capped short on purpose: case detail
    # belongs in the chat, after the advocate has accepted.
    intro_message: Optional[str] = Field(default=None, max_length=500)


class ConnectionOut(BaseModel):
    id: int
    status: str
    direction: str                 # outgoing | incoming
    intro_message: Optional[str] = None
    created_at: datetime.datetime
    updated_at: Optional[datetime.datetime] = None
    thread_id: Optional[int] = None
    other: UserCard
    # Filled in for advocates so "My Clients" and the request list can show
    # what the person is coming about.
    other_specialization: Optional[str] = None


class MessageOut(BaseModel):
    id: int
    thread_id: int
    sender_id: int
    content: str
    created_at: datetime.datetime
    read_at: Optional[datetime.datetime] = None
    mine: bool = False


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ThreadOut(BaseModel):
    id: int
    connection_id: int
    other: UserCard
    other_specialization: Optional[str] = None
    last_message: Optional[str] = None
    last_message_at: Optional[datetime.datetime] = None
    unread_count: int = 0


class ThreadDetailOut(BaseModel):
    id: int
    connection_id: int
    other: UserCard
    other_specialization: Optional[str] = None
    me_id: int
    disclaimer: str = CONNECTION_DISCLAIMER


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    body: Optional[str] = None
    is_read: bool
    created_at: datetime.datetime
    related_user_id: Optional[int] = None
    related_connection_id: Optional[int] = None
    related_thread_id: Optional[int] = None


class UnreadCountOut(BaseModel):
    notifications: int
    messages: int
    requests: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _me(db: Session, current_user: models.User) -> models.User:
    """The caller's row, loaded through THIS request's session.

    get_current_user shares the request session today, so `current_user` is
    usually already attached and this is a cheap identity-map hit. It is
    still worth going through here rather than mutating the injected object:
    a handler that writes to a User belonging to another session raises
    "not persistent within this Session" on commit, which is a confusing
    failure for what looks like a plain field update.
    """
    user = db.get(models.User, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    return user


def _avatar_url(user: Optional[models.User]) -> Optional[str]:
    """Public URL for a stored avatar, or None so the frontend draws its
    initials placeholder. Never raises - a storage misconfiguration should
    cost a picture, not the whole response."""
    if not user or not user.avatar_path:
        return None
    try:
        return storage.public_url(
            user.avatar_path, bucket=settings.supabase_avatar_bucket
        )
    except storage.StorageError:
        return None


def _card(user: Optional[models.User]) -> Optional[UserCard]:
    if not user:
        return None
    return UserCard(
        id=user.id,
        name=user.name,
        role=user.role or "user",
        city=user.city,
        state=user.state,
        avatar_url=_avatar_url(user),
        is_demo=bool(user.is_demo),
    )


def _short(text: Optional[str], limit: int = 180) -> Optional[str]:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _connection_between(
    db: Session, a_id: int, b_id: int
) -> Optional[models.Connection]:
    """The single row for this pair, in whichever direction it was created.

    There is at most one, guaranteed by uq_connection_pair, which is why a
    re-request updates rather than inserts.
    """
    return (
        db.query(models.Connection)
        .filter(
            or_(
                (models.Connection.requester_id == a_id)
                & (models.Connection.receiver_id == b_id),
                (models.Connection.requester_id == b_id)
                & (models.Connection.receiver_id == a_id),
            )
        )
        .first()
    )


def _state(conn: Optional[models.Connection], me_id: int) -> ConnectionState:
    if not conn:
        return ConnectionState()
    # A cancelled or rejected request should look like "no connection yet" to
    # the person who can act next, so the Connect button comes back. The raw
    # status is still returned for the badge.
    return ConnectionState(
        status=conn.status,
        direction="outgoing" if conn.requester_id == me_id else "incoming",
        connection_id=conn.id,
        thread_id=conn.thread.id if conn.thread else None,
    )


def _thread_or_404(
    db: Session, thread_id: int, user: models.User
) -> models.ChatThread:
    """Load a thread ONLY if the caller is a participant.

    The membership check is part of the query, not a check afterwards, so
    there is no path where a thread is loaded first and authorised second.
    A non-participant gets 404 rather than 403 - 403 would confirm that the
    thread exists and who it belongs to.
    """
    thread = (
        db.query(models.ChatThread)
        .join(
            models.ChatParticipant,
            models.ChatParticipant.thread_id == models.ChatThread.id,
        )
        .filter(
            models.ChatThread.id == thread_id,
            models.ChatParticipant.user_id == user.id,
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return thread


def _my_participant(
    db: Session, thread_id: int, user_id: int
) -> Optional[models.ChatParticipant]:
    return (
        db.query(models.ChatParticipant)
        .filter(
            models.ChatParticipant.thread_id == thread_id,
            models.ChatParticipant.user_id == user_id,
        )
        .first()
    )


def _other_participant_user(
    db: Session, thread: models.ChatThread, me_id: int
) -> Optional[models.User]:
    row = (
        db.query(models.User)
        .join(
            models.ChatParticipant,
            models.ChatParticipant.user_id == models.User.id,
        )
        .filter(
            models.ChatParticipant.thread_id == thread.id,
            models.ChatParticipant.user_id != me_id,
        )
        .first()
    )
    return row


def _specialization_of(db: Session, user_id: int) -> Optional[str]:
    row = (
        db.query(models.AdvocateProfile.specialization)
        .filter(models.AdvocateProfile.user_id == user_id)
        .first()
    )
    return row[0] if row else None


def _notify(
    db: Session,
    recipient_id: int,
    type_: str,
    title: str,
    body: Optional[str] = None,
    related_user_id: Optional[int] = None,
    related_connection_id: Optional[int] = None,
    related_thread_id: Optional[int] = None,
    collapse: bool = False,
) -> None:
    """Queue a notification. Does not commit - the caller's commit covers it,
    so a failed request never leaves a notification for something that did
    not happen.

    `collapse=True` skips creation if an unread notification of the same type
    already exists for the same thread. Used for messages: ten messages in a
    row should be one unread bell, not ten.
    """
    if collapse and related_thread_id is not None:
        existing = (
            db.query(models.Notification.id)
            .filter(
                models.Notification.recipient_id == recipient_id,
                models.Notification.type == type_,
                models.Notification.related_thread_id == related_thread_id,
                models.Notification.is_read.is_(False),
            )
            .first()
        )
        if existing:
            return

    db.add(
        models.Notification(
            recipient_id=recipient_id,
            type=type_,
            title=title,
            body=body,
            related_user_id=related_user_id,
            related_connection_id=related_connection_id,
            related_thread_id=related_thread_id,
        )
    )


# ---------------------------------------------------------------------------
# My profile
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MyProfileOut)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The caller's own profile, including the advocate half if they have one.

    Separate from /auth/me on purpose: /auth/me is the session bootstrap and
    is called on every page load, so it stays small.
    """
    me = _me(db, current_user)
    profile = (
        db.query(models.AdvocateProfile)
        .filter(models.AdvocateProfile.user_id == me.id)
        .first()
    )
    return MyProfileOut(
        id=me.id,
        name=me.name,
        email=me.email,
        role=me.role or "user",
        city=me.city,
        state=me.state,
        bio=me.bio,
        avatar_url=_avatar_url(me),
        preferred_language=me.preferred_language or "en",
        is_demo=bool(me.is_demo),
        advocate_profile=(
            AdvocateProfileOut.model_validate(profile) if profile else None
        ),
    )


@router.patch("/me", response_model=MyProfileOut)
def update_my_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Edit your own account fields. There is no user id in the path, which
    is the simplest possible guarantee that you cannot edit anyone else."""
    me = _me(db, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(me, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    return get_my_profile(db=db, current_user=me)


@router.put("/me/advocate-profile", response_model=AdvocateProfileOut)
def upsert_advocate_profile(
    payload: AdvocateProfileIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Create or replace the caller's professional profile.

    require_advocate is what stops a client account from listing itself in
    the advocate directory by hand-crafting this request.
    """
    me = _me(db, current_user)
    profile = (
        db.query(models.AdvocateProfile)
        .filter(models.AdvocateProfile.user_id == me.id)
        .first()
    )
    if not profile:
        profile = models.AdvocateProfile(user_id=me.id)
        db.add(profile)

    for field, value in payload.model_dump().items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return AdvocateProfileOut.model_validate(profile)


@router.post("/me/avatar", response_model=MyProfileOut)
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Replace the caller's profile picture.

    Validated by declared content type AND by actual byte length - a client
    can lie about Content-Length, so the size check happens after reading.
    """
    if file.content_type not in storage.ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Profile pictures must be a JPEG, PNG or WebP image.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > storage.MAX_AVATAR_BYTES:
        mb = storage.MAX_AVATAR_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=400, detail=f"Profile pictures must be under {mb} MB."
        )

    me = _me(db, current_user)
    bucket = settings.supabase_avatar_bucket
    try:
        # Lazy: creating this at startup would add a network call to every
        # boot for a bucket most sessions never touch.
        await storage.ensure_avatar_bucket()
        path = storage.build_avatar_path(me.id, file.filename or "avatar")
        await storage.upload(path, data, file.content_type, bucket=bucket)
    except storage.StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    old_path = me.avatar_path
    me.avatar_path = path
    db.commit()

    # Best effort, and after the commit: an orphaned old image is a wasted
    # few kilobytes, whereas deleting first and then failing to commit would
    # leave the user with no picture at all.
    if old_path:
        try:
            await storage.delete(old_path, bucket=bucket)
        except storage.StorageError:
            pass

    return get_my_profile(db=db, current_user=me)


@router.delete("/me/avatar", response_model=MyProfileOut)
async def delete_avatar(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    me = _me(db, current_user)
    old_path = me.avatar_path
    me.avatar_path = None
    db.commit()

    if old_path:
        try:
            await storage.delete(
                old_path, bucket=settings.supabase_avatar_bucket
            )
        except storage.StorageError:
            pass

    return get_my_profile(db=db, current_user=me)


# ---------------------------------------------------------------------------
# Advocate search
# ---------------------------------------------------------------------------

@router.get("/advocates", response_model=AdvocateSearchOut)
def search_advocates(
    q: Optional[str] = Query(default=None, max_length=120),
    name: Optional[str] = Query(default=None, max_length=120),
    city: Optional[str] = Query(default=None, max_length=120),
    state: Optional[str] = Query(default=None, max_length=120),
    specialization: Optional[str] = Query(default=None, max_length=120),
    practice_area: Optional[str] = Query(default=None, max_length=120),
    court: Optional[str] = Query(default=None, max_length=120),
    language: Optional[str] = Query(default=None, max_length=60),
    min_experience: Optional[int] = Query(default=None, ge=0, le=70),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The advocate directory.

    Note the join is INNER on advocate_profiles: an advocate account with no
    professional profile is not in the directory at all. That is what keeps
    accounts that signed up before this feature - and any half-finished
    advocate account - out of results until their owner fills a profile in.

    Matching is ILIKE over comma-separated columns. At a few hundred rows
    that is a fast sequential scan; if the directory ever reaches thousands,
    practice_areas/courts/languages become join tables and this is the only
    handler that changes.
    """
    query = (
        db.query(models.User, models.AdvocateProfile)
        .join(
            models.AdvocateProfile,
            models.AdvocateProfile.user_id == models.User.id,
        )
        .filter(
            models.User.role == "advocate",
            models.AdvocateProfile.is_listed.is_(True),
        )
    )

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                models.User.name.ilike(like),
                models.AdvocateProfile.specialization.ilike(like),
                models.AdvocateProfile.practice_areas.ilike(like),
                models.AdvocateProfile.professional_bio.ilike(like),
                models.AdvocateProfile.current_firm.ilike(like),
            )
        )
    if name:
        query = query.filter(models.User.name.ilike(f"%{name.strip()}%"))
    if city:
        like = f"%{city.strip()}%"
        # Either where they live or where they practise - searching "Indore"
        # should find the advocate who lives in Dewas and appears in Indore.
        query = query.filter(
            or_(
                models.User.city.ilike(like),
                models.AdvocateProfile.practice_city.ilike(like),
            )
        )
    if state:
        query = query.filter(models.User.state.ilike(f"%{state.strip()}%"))
    if specialization:
        query = query.filter(
            models.AdvocateProfile.specialization.ilike(f"%{specialization.strip()}%")
        )
    if practice_area:
        like = f"%{practice_area.strip()}%"
        query = query.filter(
            or_(
                models.AdvocateProfile.practice_areas.ilike(like),
                models.AdvocateProfile.specialization.ilike(like),
            )
        )
    if court:
        query = query.filter(models.AdvocateProfile.courts.ilike(f"%{court.strip()}%"))
    if language:
        query = query.filter(
            models.AdvocateProfile.languages.ilike(f"%{language.strip()}%")
        )
    if min_experience is not None:
        query = query.filter(
            models.AdvocateProfile.years_experience >= min_experience
        )

    # count() on the filtered query before pagination, so `total` reflects
    # the filters and not the table.
    total = query.order_by(None).count()

    rows = (
        query.order_by(
            models.AdvocateProfile.years_experience.desc().nullslast(),
            models.User.name.asc(),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # One query for every connection this viewer has with anyone on this
    # page, rather than one per card.
    other_ids = [u.id for u, _ in rows]
    conn_by_other: dict = {}
    if other_ids:
        conns = (
            db.query(models.Connection)
            .options(joinedload(models.Connection.thread))
            .filter(
                or_(
                    (models.Connection.requester_id == current_user.id)
                    & (models.Connection.receiver_id.in_(other_ids)),
                    (models.Connection.receiver_id == current_user.id)
                    & (models.Connection.requester_id.in_(other_ids)),
                )
            )
            .all()
        )
        for c in conns:
            other = (
                c.receiver_id if c.requester_id == current_user.id else c.requester_id
            )
            conn_by_other[other] = c

    items = [
        AdvocateCard(
            user=_card(user),
            specialization=profile.specialization,
            practice_areas=profile.practice_areas,
            courts=profile.courts,
            languages=profile.languages,
            years_experience=profile.years_experience,
            current_firm=profile.current_firm,
            practice_city=profile.practice_city,
            short_bio=_short(profile.professional_bio or user.bio),
            connection=_state(conn_by_other.get(user.id), current_user.id),
        )
        for user, profile in rows
    ]

    pages = (total + per_page - 1) // per_page if total else 0
    return AdvocateSearchOut(
        items=items, total=total, page=page, per_page=per_page, pages=pages
    )


@router.get("/advocates/filters")
def advocate_filter_options(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Distinct values for the filter dropdowns, derived from what is
    actually in the directory. Hard-coding these lists means a seeded city
    that isn't in the list becomes unsearchable from the UI."""

    def _distinct(column):
        rows = (
            db.query(column)
            .join(
                models.User,
                models.AdvocateProfile.user_id == models.User.id,
            )
            .filter(
                models.AdvocateProfile.is_listed.is_(True),
                column.isnot(None),
                column != "",
            )
            .distinct()
            .all()
        )
        return sorted({r[0].strip() for r in rows if r[0]})

    cities = (
        db.query(models.User.city)
        .join(
            models.AdvocateProfile,
            models.AdvocateProfile.user_id == models.User.id,
        )
        .filter(
            models.User.role == "advocate",
            models.AdvocateProfile.is_listed.is_(True),
            models.User.city.isnot(None),
            models.User.city != "",
        )
        .distinct()
        .all()
    )
    states = (
        db.query(models.User.state)
        .join(
            models.AdvocateProfile,
            models.AdvocateProfile.user_id == models.User.id,
        )
        .filter(
            models.User.role == "advocate",
            models.AdvocateProfile.is_listed.is_(True),
            models.User.state.isnot(None),
            models.User.state != "",
        )
        .distinct()
        .all()
    )

    # languages and courts are comma-separated, so they need splitting.
    def _split_distinct(column):
        values = set()
        for (raw,) in (
            db.query(column)
            .filter(
                models.AdvocateProfile.is_listed.is_(True),
                column.isnot(None),
                column != "",
            )
            .all()
        ):
            for part in (raw or "").split(","):
                part = part.strip()
                if part:
                    values.add(part)
        return sorted(values)

    return {
        "cities": sorted({c[0].strip() for c in cities if c[0]}),
        "states": sorted({s[0].strip() for s in states if s[0]}),
        "specializations": _distinct(models.AdvocateProfile.specialization),
        "courts": _split_distinct(models.AdvocateProfile.courts),
        "languages": _split_distinct(models.AdvocateProfile.languages),
    }


@router.get("/advocates/{user_id}", response_model=AdvocateDetailOut)
def get_advocate(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    user = (
        db.query(models.User)
        .filter(models.User.id == user_id, models.User.role == "advocate")
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Advocate not found.")

    profile = (
        db.query(models.AdvocateProfile)
        .filter(models.AdvocateProfile.user_id == user.id)
        .first()
    )
    # An unlisted advocate is reachable by direct link only if you are
    # already connected - otherwise the profile is as good as gone.
    conn = _connection_between(db, current_user.id, user.id)
    if profile and not profile.is_listed and not (
        conn and conn.status == models.CONNECTION_ACCEPTED
    ):
        raise HTTPException(status_code=404, detail="Advocate not found.")

    return AdvocateDetailOut(
        user=_card(user),
        bio=user.bio,
        profile=AdvocateProfileOut.model_validate(profile) if profile else None,
        connection=_state(conn, current_user.id),
    )


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _connection_out(
    db: Session, conn: models.Connection, me_id: int
) -> ConnectionOut:
    outgoing = conn.requester_id == me_id
    other = conn.receiver if outgoing else conn.requester
    return ConnectionOut(
        id=conn.id,
        status=conn.status,
        direction="outgoing" if outgoing else "incoming",
        intro_message=conn.intro_message,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
        thread_id=conn.thread.id if conn.thread else None,
        other=_card(other),
        other_specialization=(
            _specialization_of(db, other.id)
            if other and (other.role or "") == "advocate"
            else None
        ),
    )


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def create_connection(
    payload: ConnectionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Send a connection request to an advocate.

    Duplicates are impossible by construction: the pair has at most one row,
    so a second request either 409s (still pending, or already connected) or
    revives the existing row (previously rejected or cancelled).
    """
    if payload.receiver_id == current_user.id:
        raise HTTPException(
            status_code=400, detail="You can't send a request to yourself."
        )

    receiver = (
        db.query(models.User).filter(models.User.id == payload.receiver_id).first()
    )
    if not receiver or (receiver.role or "user") != "advocate":
        raise HTTPException(status_code=404, detail="Advocate not found.")

    intro = (payload.intro_message or "").strip() or None
    existing = _connection_between(db, current_user.id, receiver.id)

    if existing:
        if existing.status == models.CONNECTION_ACCEPTED:
            raise HTTPException(
                status_code=409, detail="You're already connected."
            )
        if existing.status == models.CONNECTION_PENDING:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A request is already pending."
                    if existing.requester_id == current_user.id
                    else "This advocate has already sent you a request."
                ),
            )

        # Rejected or cancelled: reuse the row, flipping direction if the
        # other side is now the one asking.
        existing.requester_id = current_user.id
        existing.receiver_id = receiver.id
        existing.status = models.CONNECTION_PENDING
        existing.intro_message = intro
        existing.created_at = datetime.datetime.utcnow()
        conn = existing
    else:
        conn = models.Connection(
            requester_id=current_user.id,
            receiver_id=receiver.id,
            status=models.CONNECTION_PENDING,
            intro_message=intro,
        )
        db.add(conn)

    db.flush()   # need conn.id for the notification

    _notify(
        db,
        recipient_id=receiver.id,
        type_=models.NOTIFY_CONNECTION_REQUEST,
        title=f"{current_user.name} sent you a connection request",
        body=intro,
        related_user_id=current_user.id,
        related_connection_id=conn.id,
    )

    db.commit()
    db.refresh(conn)
    return _connection_out(db, conn, current_user.id)


@router.get("/connections", response_model=List[ConnectionOut])
def list_connections(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Accepted connections in both directions - "My Advocates" for a client,
    "My Clients" for an advocate. Same route, because the only difference is
    which side of the row the caller is on."""
    conns = (
        db.query(models.Connection)
        .options(
            joinedload(models.Connection.requester),
            joinedload(models.Connection.receiver),
            joinedload(models.Connection.thread),
        )
        .filter(
            models.Connection.status == models.CONNECTION_ACCEPTED,
            or_(
                models.Connection.requester_id == current_user.id,
                models.Connection.receiver_id == current_user.id,
            ),
        )
        .order_by(models.Connection.updated_at.desc())
        .all()
    )
    return [_connection_out(db, c, current_user.id) for c in conns]


@router.get("/connections/requests", response_model=List[ConnectionOut])
def list_incoming_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Pending requests waiting on the caller to accept or decline."""
    conns = (
        db.query(models.Connection)
        .options(
            joinedload(models.Connection.requester),
            joinedload(models.Connection.receiver),
        )
        .filter(
            models.Connection.receiver_id == current_user.id,
            models.Connection.status == models.CONNECTION_PENDING,
        )
        .order_by(models.Connection.created_at.desc())
        .all()
    )
    return [_connection_out(db, c, current_user.id) for c in conns]


@router.get("/connections/sent", response_model=List[ConnectionOut])
def list_sent_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Requests the caller has sent that are still pending."""
    conns = (
        db.query(models.Connection)
        .options(
            joinedload(models.Connection.requester),
            joinedload(models.Connection.receiver),
        )
        .filter(
            models.Connection.requester_id == current_user.id,
            models.Connection.status == models.CONNECTION_PENDING,
        )
        .order_by(models.Connection.created_at.desc())
        .all()
    )
    return [_connection_out(db, c, current_user.id) for c in conns]


def _pending_as_receiver(
    db: Session, connection_id: int, user: models.User
) -> models.Connection:
    """Load a pending request the caller is the RECEIVER of.

    Both conditions are in the query: accepting a request addressed to
    someone else, or re-accepting one already handled, both come back as 404
    rather than doing anything.
    """
    conn = (
        db.query(models.Connection)
        .filter(
            models.Connection.id == connection_id,
            models.Connection.receiver_id == user.id,
            models.Connection.status == models.CONNECTION_PENDING,
        )
        .first()
    )
    if not conn:
        raise HTTPException(status_code=404, detail="Request not found.")
    return conn


@router.patch("/connections/{connection_id}/accept", response_model=ConnectionOut)
def accept_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Accept a request and open the private thread.

    Thread creation is in the same transaction as the status change, so
    there is no window where a connection is accepted but has nowhere to
    talk.
    """
    conn = _pending_as_receiver(db, connection_id, current_user)
    conn.status = models.CONNECTION_ACCEPTED

    if not conn.thread:
        thread = models.ChatThread(connection_id=conn.id)
        db.add(thread)
        db.flush()
        db.add(models.ChatParticipant(thread_id=thread.id, user_id=conn.requester_id))
        db.add(models.ChatParticipant(thread_id=thread.id, user_id=conn.receiver_id))
    else:
        thread = conn.thread

    _notify(
        db,
        recipient_id=conn.requester_id,
        type_=models.NOTIFY_CONNECTION_ACCEPTED,
        title=f"{current_user.name} accepted your connection request",
        body="You can now discuss your matter in a private conversation.",
        related_user_id=current_user.id,
        related_connection_id=conn.id,
        related_thread_id=thread.id,
    )

    db.commit()
    db.refresh(conn)
    return _connection_out(db, conn, current_user.id)


@router.patch("/connections/{connection_id}/reject", response_model=ConnectionOut)
def reject_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    conn = _pending_as_receiver(db, connection_id, current_user)
    conn.status = models.CONNECTION_REJECTED

    _notify(
        db,
        recipient_id=conn.requester_id,
        type_=models.NOTIFY_CONNECTION_REJECTED,
        title=f"{current_user.name} declined your connection request",
        body="You can search for other advocates in Find an Advocate.",
        related_user_id=current_user.id,
        related_connection_id=conn.id,
    )

    db.commit()
    db.refresh(conn)
    return _connection_out(db, conn, current_user.id)


@router.delete("/connections/{connection_id}", status_code=204)
def cancel_connection(
    connection_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Withdraw a request you sent. Requester-only, pending-only - the
    conditions are in the query, so cancelling someone else's request is a
    404."""
    conn = (
        db.query(models.Connection)
        .filter(
            models.Connection.id == connection_id,
            models.Connection.requester_id == current_user.id,
            models.Connection.status == models.CONNECTION_PENDING,
        )
        .first()
    )
    if not conn:
        raise HTTPException(status_code=404, detail="Request not found.")

    conn.status = models.CONNECTION_CANCELLED

    # Drop the advocate's unread notification too - a request that no longer
    # exists shouldn't keep sitting on their dashboard.
    db.query(models.Notification).filter(
        models.Notification.related_connection_id == conn.id,
        models.Notification.type == models.NOTIFY_CONNECTION_REQUEST,
        models.Notification.is_read.is_(False),
    ).delete(synchronize_session=False)

    db.commit()
    return None


# ---------------------------------------------------------------------------
# Threads and messages
# ---------------------------------------------------------------------------

@router.get("/threads", response_model=List[ThreadOut])
def list_threads(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The conversation list. Ordered by last_message_at, which is
    denormalised onto the thread so this doesn't aggregate over messages."""
    rows = (
        db.query(models.ChatThread, models.ChatParticipant)
        .join(
            models.ChatParticipant,
            models.ChatParticipant.thread_id == models.ChatThread.id,
        )
        .filter(models.ChatParticipant.user_id == current_user.id)
        .order_by(models.ChatThread.last_message_at.desc())
        .all()
    )
    if not rows:
        return []

    thread_ids = [t.id for t, _ in rows]

    # Latest message per thread, in one pass rather than one query per row.
    latest_ids = [
        r[0]
        for r in db.query(func.max(models.ChatMessage.id))
        .filter(models.ChatMessage.thread_id.in_(thread_ids))
        .group_by(models.ChatMessage.thread_id)
        .all()
    ]
    last_by_thread: dict = {}
    if latest_ids:
        for msg in (
            db.query(models.ChatMessage)
            .filter(models.ChatMessage.id.in_(latest_ids))
            .all()
        ):
            last_by_thread[msg.thread_id] = msg

    # The other participant for every thread, in one query.
    others = (
        db.query(models.ChatParticipant.thread_id, models.User)
        .join(models.User, models.User.id == models.ChatParticipant.user_id)
        .filter(
            models.ChatParticipant.thread_id.in_(thread_ids),
            models.ChatParticipant.user_id != current_user.id,
        )
        .all()
    )
    other_by_thread = {tid: user for tid, user in others}

    advocate_ids = [
        u.id for u in other_by_thread.values() if (u.role or "") == "advocate"
    ]
    spec_by_user: dict = {}
    if advocate_ids:
        spec_by_user = {
            uid: spec
            for uid, spec in db.query(
                models.AdvocateProfile.user_id, models.AdvocateProfile.specialization
            )
            .filter(models.AdvocateProfile.user_id.in_(advocate_ids))
            .all()
        }

    out: List[ThreadOut] = []
    for thread, participant in rows:
        last = last_by_thread.get(thread.id)
        other = other_by_thread.get(thread.id)

        unread_q = db.query(func.count(models.ChatMessage.id)).filter(
            models.ChatMessage.thread_id == thread.id,
            models.ChatMessage.sender_id != current_user.id,
        )
        if participant.last_read_at:
            unread_q = unread_q.filter(
                models.ChatMessage.created_at > participant.last_read_at
            )
        unread = unread_q.scalar() or 0

        out.append(
            ThreadOut(
                id=thread.id,
                connection_id=thread.connection_id,
                other=_card(other),
                other_specialization=spec_by_user.get(other.id) if other else None,
                last_message=_short(last.content, 90) if last else None,
                last_message_at=last.created_at if last else thread.last_message_at,
                unread_count=unread,
            )
        )
    return out


@router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
def get_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    thread = _thread_or_404(db, thread_id, current_user)
    other = _other_participant_user(db, thread, current_user.id)
    return ThreadDetailOut(
        id=thread.id,
        connection_id=thread.connection_id,
        other=_card(other),
        other_specialization=(
            _specialization_of(db, other.id)
            if other and (other.role or "") == "advocate"
            else None
        ),
        me_id=current_user.id,
    )


@router.get("/threads/{thread_id}/messages", response_model=List[MessageOut])
def list_messages(
    thread_id: int,
    after_id: Optional[int] = Query(
        default=None,
        ge=0,
        description="Return only messages with a higher id. This is the poll.",
    ),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Message history, or just what's new.

    `after_id` is the whole real-time story for now: the client holds the
    last id it has seen and polls for anything above it, which hits
    ix_chat_messages_thread_id_id directly. Swapping this for a WebSocket
    later means pushing the same rows down a socket - no schema change.
    """
    thread = _thread_or_404(db, thread_id, current_user)

    query = db.query(models.ChatMessage).filter(
        models.ChatMessage.thread_id == thread.id
    )
    if after_id:
        query = query.filter(models.ChatMessage.id > after_id)
        rows = query.order_by(models.ChatMessage.id.asc()).limit(limit).all()
    else:
        # Opening a thread should show the END of the history, not the start,
        # so take the newest `limit` and flip them back into reading order.
        rows = (
            query.order_by(models.ChatMessage.id.desc()).limit(limit).all()
        )
        rows.reverse()

    return [
        MessageOut(
            id=m.id,
            thread_id=m.thread_id,
            sender_id=m.sender_id,
            content=m.content,
            created_at=m.created_at,
            read_at=m.read_at,
            mine=m.sender_id == current_user.id,
        )
        for m in rows
    ]


@router.post("/threads/{thread_id}/messages", response_model=MessageOut, status_code=201)
def send_message(
    thread_id: int,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    thread = _thread_or_404(db, thread_id, current_user)

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message can't be empty.")

    # Belt and braces: pydantic already caps this, but the connection must
    # still be accepted at send time. A thread can outlive its connection
    # being revoked later.
    conn = (
        db.query(models.Connection)
        .filter(models.Connection.id == thread.connection_id)
        .first()
    )
    if not conn or conn.status != models.CONNECTION_ACCEPTED:
        raise HTTPException(
            status_code=403, detail="This conversation is no longer active."
        )

    now = datetime.datetime.utcnow()
    msg = models.ChatMessage(
        thread_id=thread.id,
        sender_id=current_user.id,
        content=content,
        created_at=now,
    )
    db.add(msg)
    thread.last_message_at = now

    other = _other_participant_user(db, thread, current_user.id)
    if other:
        _notify(
            db,
            recipient_id=other.id,
            type_=models.NOTIFY_NEW_MESSAGE,
            title=f"New message from {current_user.name}",
            body=_short(content, 120),
            related_user_id=current_user.id,
            related_connection_id=thread.connection_id,
            related_thread_id=thread.id,
            collapse=True,
        )

    db.commit()
    db.refresh(msg)
    return MessageOut(
        id=msg.id,
        thread_id=msg.thread_id,
        sender_id=msg.sender_id,
        content=msg.content,
        created_at=msg.created_at,
        read_at=msg.read_at,
        mine=True,
    )


@router.post("/threads/{thread_id}/read")
def mark_thread_read(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Called when the caller has the thread open.

    Moves their read cursor (which drives unread counts) and stamps read_at
    on the other side's messages (which drives the sender's tick).
    """
    thread = _thread_or_404(db, thread_id, current_user)
    now = datetime.datetime.utcnow()

    participant = _my_participant(db, thread.id, current_user.id)
    if participant:
        participant.last_read_at = now

    db.query(models.ChatMessage).filter(
        models.ChatMessage.thread_id == thread.id,
        models.ChatMessage.sender_id != current_user.id,
        models.ChatMessage.read_at.is_(None),
    ).update({"read_at": now}, synchronize_session=False)

    # Opening the thread is reading the notification about it.
    db.query(models.Notification).filter(
        models.Notification.recipient_id == current_user.id,
        models.Notification.related_thread_id == thread.id,
        models.Notification.type == models.NOTIFY_NEW_MESSAGE,
        models.Notification.is_read.is_(False),
    ).update({"is_read": True}, synchronize_session=False)

    db.commit()
    return {"ok": True, "read_at": now}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@router.get("/notifications", response_model=List[NotificationOut])
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Notification).filter(
        models.Notification.recipient_id == current_user.id
    )
    if unread_only:
        query = query.filter(models.Notification.is_read.is_(False))

    rows = query.order_by(models.Notification.created_at.desc()).limit(limit).all()
    return [
        NotificationOut(
            id=n.id,
            type=n.type,
            title=n.title,
            body=n.body,
            is_read=n.is_read,
            created_at=n.created_at,
            related_user_id=n.related_user_id,
            related_connection_id=n.related_connection_id,
            related_thread_id=n.related_thread_id,
        )
        for n in rows
    ]


@router.get("/unread", response_model=UnreadCountOut)
def unread_counts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The three badge numbers, in one request.

    The sidebar needs all three on every page, and three separate endpoints
    meant three round trips for numbers that are cheap to count together.
    """
    notifications = (
        db.query(func.count(models.Notification.id))
        .filter(
            models.Notification.recipient_id == current_user.id,
            models.Notification.is_read.is_(False),
        )
        .scalar()
        or 0
    )
    requests = (
        db.query(func.count(models.Connection.id))
        .filter(
            models.Connection.receiver_id == current_user.id,
            models.Connection.status == models.CONNECTION_PENDING,
        )
        .scalar()
        or 0
    )

    messages = 0
    for thread_id, last_read_at in (
        db.query(models.ChatParticipant.thread_id, models.ChatParticipant.last_read_at)
        .filter(models.ChatParticipant.user_id == current_user.id)
        .all()
    ):
        q = db.query(func.count(models.ChatMessage.id)).filter(
            models.ChatMessage.thread_id == thread_id,
            models.ChatMessage.sender_id != current_user.id,
        )
        if last_read_at:
            q = q.filter(models.ChatMessage.created_at > last_read_at)
        messages += q.scalar() or 0

    return UnreadCountOut(
        notifications=notifications, messages=messages, requests=requests
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    note = (
        db.query(models.Notification)
        .filter(
            models.Notification.id == notification_id,
            models.Notification.recipient_id == current_user.id,
        )
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Notification not found.")

    note.is_read = True
    db.commit()
    db.refresh(note)
    return NotificationOut(
        id=note.id,
        type=note.type,
        title=note.title,
        body=note.body,
        is_read=note.is_read,
        created_at=note.created_at,
        related_user_id=note.related_user_id,
        related_connection_id=note.related_connection_id,
        related_thread_id=note.related_thread_id,
    )


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    updated = (
        db.query(models.Notification)
        .filter(
            models.Notification.recipient_id == current_user.id,
            models.Notification.is_read.is_(False),
        )
        .update({"is_read": True}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "updated": updated}


@router.get("/disclaimer")
def get_disclaimer():
    """One source of truth for the text, so the profile page, the connect
    dialog and the chat header can't drift apart."""
    return {"disclaimer": CONNECTION_DISCLAIMER}