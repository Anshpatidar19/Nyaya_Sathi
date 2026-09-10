"""Matter management. Advocate-only.

A matter is the case file: the client, the court, the dated timeline, the
hearing notes, the papers, and the research done on it. Everything else in
the platform can be filed under one.

Ownership is checked on every route by loading through `_owned()` rather
than trusting an id from the client, so one advocate's matter id is a 404 to
another rather than a data leak.
"""

import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, literal
from sqlalchemy.orm import Session

from . import models, schemas, storage
from .auth import require_advocate
from .database import get_db

router = APIRouter(prefix="/matters", tags=["matters"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _owned(db: Session, matter_id: int, user: models.User) -> models.Matter:
    matter = (
        db.query(models.Matter)
        .filter(models.Matter.id == matter_id, models.Matter.user_id == user.id)
        .first()
    )
    if not matter:
        raise HTTPException(status_code=404, detail="Matter not found.")
    return matter


def _next_hearing(db: Session, matter_id: int) -> Optional[models.MatterEvent]:
    """The soonest thing still ahead. Anything marked done is skipped even if
    its date hasn't passed - an adjourned hearing shouldn't keep showing."""
    today = datetime.date.today()
    return (
        db.query(models.MatterEvent)
        .filter(
            models.MatterEvent.matter_id == matter_id,
            models.MatterEvent.event_date >= today,
            models.MatterEvent.done.is_(False),
        )
        .order_by(models.MatterEvent.event_date)
        .first()
    )


def _counts(db: Session, matter_id: int) -> dict:
    """Counts for ONE matter. Only used by the detail route - the list route
    uses _counts_bulk, because doing this per row meant five round trips per
    matter to a database on the other end of a network."""
    def n(model):
        return (
            db.query(func.count(model.id))
            .filter(model.matter_id == matter_id)
            .scalar()
            or 0
        )

    return {
        "event_count": n(models.MatterEvent),
        "note_count": n(models.MatterNote),
        "document_count": n(models.Document),
        "research_count": n(models.Conversation),
    }


def _counts_bulk(db: Session, matter_ids: List[int]) -> Dict[int, dict]:
    """All four counts for every matter, in ONE round trip.

    This used to be four separate GROUP BY queries - one per table. Each one
    is cheap on the database itself, but on a remote database (Supabase, not
    localhost) the network round trip dwarfs the query time, so four queries
    meant paying that trip four times before the page could render. A UNION
    ALL of all four, tagged with which table each row came from, is still
    one statement - one round trip - no matter how many tables it covers.
    """
    blank = {
        "event_count": 0, "note_count": 0,
        "document_count": 0, "research_count": 0,
    }
    out = {mid: dict(blank) for mid in matter_ids}
    if not matter_ids:
        return out

    def part(model, key: str):
        return (
            db.query(
                model.matter_id.label("matter_id"),
                func.count(model.id).label("n"),
                literal(key).label("kind"),
            )
            .filter(model.matter_id.in_(matter_ids))
            .group_by(model.matter_id)
        )

    combined = part(models.MatterEvent, "event_count").union_all(
        part(models.MatterNote, "note_count"),
        part(models.Document, "document_count"),
        part(models.Conversation, "research_count"),
    )

    for mid, n, kind in combined.all():
        if mid in out:
            out[mid][kind] = n

    return out


def _next_hearings_bulk(
    db: Session, matter_ids: List[int]
) -> Dict[int, models.MatterEvent]:
    """Soonest upcoming entry per matter, in one query.

    Ordered ascending and inserted only if unseen, so the first row for each
    matter wins - the earliest. Avoids a correlated subquery, which Postgres
    would handle fine but which is harder to read.
    """
    if not matter_ids:
        return {}

    rows = (
        db.query(models.MatterEvent)
        .filter(
            models.MatterEvent.matter_id.in_(matter_ids),
            models.MatterEvent.event_date >= datetime.date.today(),
            models.MatterEvent.done.is_(False),
        )
        .order_by(models.MatterEvent.event_date)
        .all()
    )

    out: Dict[int, models.MatterEvent] = {}
    for ev in rows:
        out.setdefault(ev.matter_id, ev)
    return out


def _as_out(db: Session, matter: models.Matter) -> dict:
    data = {
        c.name: getattr(matter, c.name) for c in matter.__table__.columns
    }
    data.update(_counts(db, matter.id))
    data["next_hearing"] = _next_hearing(db, matter.id)
    return data


def _touch(db: Session, matter: models.Matter) -> None:
    """Bump the matter's updated_at when a child row changes, so the list
    sorts by real activity rather than by when the title was last edited."""
    matter.updated_at = datetime.datetime.utcnow()


# ---------------------------------------------------------------------------
# Matters
# ---------------------------------------------------------------------------

@router.get("", response_model=List[schemas.MatterOut])
def list_matters(
    status_filter: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    query = db.query(models.Matter).filter(models.Matter.user_id == current_user.id)

    if status_filter in ("active", "archived"):
        query = query.filter(models.Matter.status == status_filter)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            models.Matter.title.ilike(like)
            | models.Matter.client_name.ilike(like)
            | models.Matter.case_number.ilike(like)
            | models.Matter.court.ilike(like)
        )

    matters = query.order_by(models.Matter.updated_at.desc()).all()

    ids = [m.id for m in matters]
    counts = _counts_bulk(db, ids)
    hearings = _next_hearings_bulk(db, ids)

    out = []
    for m in matters:
        data = {c.name: getattr(m, c.name) for c in m.__table__.columns}
        data.update(counts.get(m.id, {}))
        data["next_hearing"] = hearings.get(m.id)
        out.append(data)
    return out


@router.post("", response_model=schemas.MatterOut, status_code=status.HTTP_201_CREATED)
def create_matter(
    payload: schemas.MatterCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = models.Matter(user_id=current_user.id, **payload.model_dump())
    db.add(matter)
    db.commit()
    db.refresh(matter)
    return _as_out(db, matter)


@router.get("/upcoming", response_model=List[schemas.MatterEventOut])
def upcoming_events(
    days: int = 60,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Everything ahead across all matters - what the calendar plots.

    Declared before /{matter_id} so "upcoming" isn't swallowed as an id.
    """
    today = datetime.date.today()
    until = today + datetime.timedelta(days=max(1, min(days, 365)))
    return (
        db.query(models.MatterEvent)
        .filter(
            models.MatterEvent.user_id == current_user.id,
            models.MatterEvent.event_date >= today,
            models.MatterEvent.event_date <= until,
            models.MatterEvent.done.is_(False),
        )
        .order_by(models.MatterEvent.event_date)
        .all()
    )


@router.get("/{matter_id}", response_model=schemas.MatterDetail)
def get_matter(
    matter_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    data = _as_out(db, matter)
    data["events"] = matter.events
    data["notes_entries"] = matter.notes_entries
    data["documents"] = matter.documents
    data["research"] = (
        db.query(models.Conversation)
        .filter(models.Conversation.matter_id == matter.id)
        .order_by(models.Conversation.updated_at.desc())
        .all()
    )
    return data


@router.patch("/{matter_id}", response_model=schemas.MatterOut)
def update_matter(
    matter_id: int,
    payload: schemas.MatterUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    # exclude_unset: the detail page saves one section at a time, so an
    # absent field means "leave it alone", not "set it to null".
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(matter, field, value)
    db.commit()
    db.refresh(matter)
    return _as_out(db, matter)


@router.delete("/{matter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_matter(
    matter_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Deletes the matter, its timeline and its notes.

    Documents and research threads are unlinked, not destroyed - losing a
    client's papers because a case file was tidied away would be indefensible.
    """
    matter = _owned(db, matter_id, current_user)

    db.query(models.Document).filter(
        models.Document.matter_id == matter.id
    ).update({"matter_id": None})
    db.query(models.Conversation).filter(
        models.Conversation.matter_id == matter.id
    ).update({"matter_id": None})

    db.delete(matter)
    db.commit()


# ---------------------------------------------------------------------------
# Timeline events
# ---------------------------------------------------------------------------

@router.post(
    "/{matter_id}/events",
    response_model=schemas.MatterEventOut,
    status_code=status.HTTP_201_CREATED,
)
def create_event(
    matter_id: int,
    payload: schemas.MatterEventCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    event = models.MatterEvent(
        matter_id=matter.id, user_id=current_user.id, **payload.model_dump()
    )
    db.add(event)
    _touch(db, matter)
    db.commit()
    db.refresh(event)
    return event


@router.patch("/{matter_id}/events/{event_id}", response_model=schemas.MatterEventOut)
def update_event(
    matter_id: int,
    event_id: int,
    payload: schemas.MatterEventUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    event = (
        db.query(models.MatterEvent)
        .filter(
            models.MatterEvent.id == event_id,
            models.MatterEvent.matter_id == matter.id,
        )
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    _touch(db, matter)
    db.commit()
    db.refresh(event)
    return event


@router.delete(
    "/{matter_id}/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_event(
    matter_id: int,
    event_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    event = (
        db.query(models.MatterEvent)
        .filter(
            models.MatterEvent.id == event_id,
            models.MatterEvent.matter_id == matter.id,
        )
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    db.delete(event)
    _touch(db, matter)
    db.commit()


# ---------------------------------------------------------------------------
# Dated notes - what actually happened
# ---------------------------------------------------------------------------

@router.post(
    "/{matter_id}/notes",
    response_model=schemas.MatterNoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_note(
    matter_id: int,
    payload: schemas.MatterNoteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    note = models.MatterNote(
        matter_id=matter.id, user_id=current_user.id, **payload.model_dump()
    )
    db.add(note)
    _touch(db, matter)
    db.commit()
    db.refresh(note)
    return note


@router.patch("/{matter_id}/notes/{note_id}", response_model=schemas.MatterNoteOut)
def update_note(
    matter_id: int,
    note_id: int,
    payload: schemas.MatterNoteUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    note = (
        db.query(models.MatterNote)
        .filter(
            models.MatterNote.id == note_id,
            models.MatterNote.matter_id == matter.id,
        )
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    _touch(db, matter)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{matter_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    matter_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    matter = _owned(db, matter_id, current_user)
    note = (
        db.query(models.MatterNote)
        .filter(
            models.MatterNote.id == note_id,
            models.MatterNote.matter_id == matter.id,
        )
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    db.delete(note)
    _touch(db, matter)
    db.commit()


# ---------------------------------------------------------------------------
# Filing existing things under a matter
# ---------------------------------------------------------------------------

@router.patch("/link/document/{doc_id}", response_model=schemas.DocumentOut)
def link_document(
    doc_id: int,
    payload: schemas.MatterLink,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """File an already-uploaded document under a matter, or pass null to
    take it out of one."""
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

    if payload.matter_id is not None:
        _owned(db, payload.matter_id, current_user)   # ownership of the target

    doc.matter_id = payload.matter_id
    db.commit()
    db.refresh(doc)
    return doc


@router.patch("/link/conversation/{conversation_id}", response_model=schemas.ConversationOut)
def link_conversation(
    conversation_id: int,
    payload: schemas.MatterLink,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
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

    if payload.matter_id is not None:
        _owned(db, payload.matter_id, current_user)

    convo.matter_id = payload.matter_id
    db.commit()
    db.refresh(convo)
    return convo


@router.delete(
    "/{matter_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_matter_document(
    matter_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_advocate),
):
    """Remove a document from the matter and from storage."""
    matter = _owned(db, matter_id, current_user)
    doc = (
        db.query(models.Document)
        .filter(
            models.Document.id == doc_id,
            models.Document.matter_id == matter.id,
            models.Document.user_id == current_user.id,
        )
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    await storage.delete(doc.storage_path)
    db.delete(doc)
    _touch(db, matter)
    db.commit()