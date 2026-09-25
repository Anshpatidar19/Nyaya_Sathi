"""The Find an Advocate assistant.

    POST /network/advocates/recommend

One turn: a natural-language question in, a ranked shortlist out. The
pipeline is deliberately split so the model never touches the ranking:

    question
      -> Gemini parses it into a Requirement (area, matter, city, language)
      -> no city/state in the question? the user's own profile location
         stands in, so "a property lawyer" means one near them
      -> that drives the SAME directory query the search page uses
      -> advocate_match ranks the candidates in Python: nearest first
         (same city, then same state, then elsewhere), and within each
         tier by how closely the profile fits the matter
      -> the reply sentence is a template over that ranking

No number is shown to the user. The internal relevance score still orders
advocates inside a location tier and still gates out profiles that say
nothing about the matter, but a visible "87 match" invited exactly the
"rating" reading the product cannot back up, so cards carry reasons only.

The model's only job is understanding the sentence. It does not choose who
ranks where and it does not write the justification, because a generated
justification is exactly how "highest match score" turns into "best lawyer"
one bad sampling at a time. Every number and every reason on a card comes
out of advocate_match.

Candidates are fetched WIDE and narrowed by score rather than by SQL. If
the practice area were a hard filter, an advocate whose specialisation says
"Criminal Law" would be invisible to a question about a cheque-bounce case
even with "Cheque Bounce" sitting in their practice_areas. Scoring sees the
whole profile; a WHERE clause sees one column.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from . import advocate_match, gemini, models
from .advocate_match import Requirement
from .auth import get_current_user
from .database import get_db
# The card shape, the connection-state helper and the avatar/user mapper
# already exist for the search page. Re-declaring them here would mean two
# card shapes drifting apart, so the bot returns exactly what the grid
# returns, plus the reasons.
from .network_api import AdvocateCard, _card, _short, _state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/network/advocates", tags=["advocate-assistant"])

# Scored in Python, so this is the real cost ceiling. The demo directory is
# far smaller; at a few thousand advocates this becomes a coarse SQL
# prefilter feeding the same scorer.
MAX_CANDIDATES = 150
SHORTLIST = 5

# A score floor alone cannot do this job. Once the score is renormalised
# over the factors that applied, a 25-year criminal generalist with zero
# cheque-bounce experience still lands in the mid 50s on area + experience
# + a filled-in profile, and no floor that lets a genuine match through
# will stop them. So the gate is on RELEVANCE, not on the total: if the
# user named a specific matter and nothing in the profile speaks to it,
# that advocate is not an answer to the question, however senior they are.
#
# The directory has two advocates who list cheque bounce. The honest reply
# is two cards, not five.
MIN_SCORE = 45


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class BotTurn(BaseModel):
    role: str = "user"
    content: str = ""


class RecommendIn(BaseModel):
    message: str = Field(min_length=2, max_length=500)
    history: List[BotTurn] = []


class ScoredAdvocate(BaseModel):
    card: AdvocateCard
    # "city" | "state" | "other" | "any" - how close they are to the
    # location being searched. The UI shows a small "Near you" tag on city.
    location_match: str = "any"
    reasons: List[str] = []


class RecommendOut(BaseModel):
    # "clarify" - too broad to rank; "matches" - a shortlist; "none" - nothing fits.
    kind: str
    reply: str
    understood: Dict[str, Any] = {}
    items: List[ScoredAdvocate] = []
    widened: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# Understanding the question
# ---------------------------------------------------------------------------

_PARSE_SYSTEM = """You turn an Indian legal-help request into search terms. \
You do NOT answer it, recommend anyone, or give legal advice.

Return ONLY a JSON object, no markdown fences:
{
  "practice_area": "the broad area, using ordinary Indian legal vocabulary \
- Criminal Law, Family Law, Property, Corporate, Labour & Employment, \
Consumer, Motor Accident Claims, Tax, Constitutional, Intellectual Property \
- or empty if the user has not said",
  "matter_keywords": ["specific terms from the request that would appear in \
an advocate's profile: cheque bounce, bail, divorce, maintenance, eviction, \
partition, NDPS, GST, wrongful termination, MACT, arbitration"],
  "city": "city named, or empty",
  "state": "Indian state named or clearly implied by the city, or empty",
  "language": "language named as a preference, or empty",
  "min_experience": null or a number if the user asked for a minimum
}

Rules:
- Copy the city as written. Do not expand "Indore" into a region.
- matter_keywords are the SPECIFIC matter, not the broad area. "I need a \
criminal lawyer for a cheque bounce case" gives practice_area "Criminal Law" \
and matter_keywords ["cheque bounce"].
- Leave a field empty rather than guessing. Empty is a usable answer; a \
wrong city is not.
- "best", "top" and "good" carry no information. Ignore them.
- If the request names no area and no specific matter, return both empty."""


async def parse_requirement(message: str, history: List[BotTurn]) -> Requirement:
    context = ""
    for turn in history[-4:]:
        who = "USER" if turn.role == "user" else "ASSISTANT"
        body = " ".join((turn.content or "").split())[:300]
        if body:
            context += f"{who}: {body}\n"

    prompt = (
        (f"EARLIER IN THIS CONVERSATION:\n{context}\n" if context else "")
        + f"REQUEST: {message}\n\nReturn the JSON now."
    )

    data = await gemini.generate_json(
        prompt, _PARSE_SYSTEM,
        temperature=0.0, max_output_tokens=700, thinking_budget=0,
    )

    def clean(value: Any, limit: int = 80) -> Optional[str]:
        text = str(value or "").strip()[:limit]
        return text or None

    keywords = []
    for kw in (data.get("matter_keywords") or [])[:8]:
        text = str(kw or "").strip()[:60]
        if text:
            keywords.append(text)

    min_exp = data.get("min_experience")
    try:
        min_exp = int(min_exp) if min_exp is not None else None
        if min_exp is not None and not (0 <= min_exp <= 70):
            min_exp = None
    except (TypeError, ValueError):
        min_exp = None

    return Requirement(
        practice_area=clean(data.get("practice_area")),
        matter_keywords=keywords,
        city=clean(data.get("city")),
        state=clean(data.get("state")),
        language=clean(data.get("language"), 40),
        min_experience=min_exp,
    )


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _base_query(db: Session, req: Requirement):
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
    if req.min_experience is not None:
        query = query.filter(
            models.AdvocateProfile.years_experience >= req.min_experience
        )
    return query


def _candidates(db: Session, req: Requirement) -> List:
    """The directory, local advocates guaranteed a place in the pool.

    Nothing is a hard filter any more - an Indore request still sees
    Bhopal and Chennai, just ranked after Indore. But the pool is capped,
    so the city and state slices are pulled first: at a few thousand
    advocates a plain LIMIT would otherwise drop the very people the
    ranking is meant to put on top. Practice area is never a WHERE clause;
    that is the scorer's job.
    """
    pools = []
    if req.city:
        like = f"%{req.city.strip()}%"
        pools.append(
            _base_query(db, req)
            .filter(
                or_(
                    models.User.city.ilike(like),
                    models.AdvocateProfile.practice_city.ilike(like),
                )
            )
            .limit(MAX_CANDIDATES)
            .all()
        )
    if req.state:
        pools.append(
            _base_query(db, req)
            .filter(models.User.state.ilike(req.state.strip()))
            .limit(MAX_CANDIDATES)
            .all()
        )
    pools.append(_base_query(db, req).limit(MAX_CANDIDATES).all())

    seen = set()
    rows = []
    for pool in pools:
        for user, prof in pool:
            if user.id in seen:
                continue
            seen.add(user.id)
            rows.append((user, prof))
    return rows[: MAX_CANDIDATES * 2]


def _connections(db: Session, viewer_id: int, other_ids: List[int]) -> Dict[int, Any]:
    """One query for every connection on the shortlist, not one per card."""
    if not other_ids:
        return {}
    rows = (
        db.query(models.Connection)
        .options(joinedload(models.Connection.thread))
        .filter(
            or_(
                (models.Connection.requester_id == viewer_id)
                & (models.Connection.receiver_id.in_(other_ids)),
                (models.Connection.receiver_id == viewer_id)
                & (models.Connection.requester_id.in_(other_ids)),
            )
        )
        .all()
    )
    out: Dict[int, Any] = {}
    for c in rows:
        other = c.receiver_id if c.requester_id == viewer_id else c.requester_id
        out[other] = c
    return out


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------

# Templates, not generated text. This is the load-bearing decision in the
# whole feature: the ranking is described as location first, then profile
# fit, and it cannot drift into "top rated" because no model is writing
# this sentence.
def _reply_line(
    req: Requirement,
    count: int,
    widened: bool,
    thin: bool = False,
    from_profile: bool = False,
) -> str:
    # Name the SPECIFIC matter, not the broad area. The area is usually
    # inferred by the parser; the matter is what the person actually said,
    # and echoing the inference back ("matches for Criminal Law" when they
    # asked about a cheque bounce) reads as if the question was ignored.
    if req.matter_keywords:
        what = ", ".join(req.matter_keywords[:2])
        if req.practice_area:
            what += f" ({req.practice_area})"
    else:
        what = req.practice_area or "your matter"

    place = req.city or req.state
    if place and from_profile:
        place_phrase = f"{place} (your profile location)"
    else:
        place_phrase = place

    if thin:
        where = f" near {place_phrase}" if place else ""
        return (
            f"No advocate in the directory is a close match for {what}{where}. "
            f"These are the nearest profiles, but none of them lists that work "
            f"specifically - worth checking their profiles before you connect."
        )

    if widened and place:
        fallback = (
            f"others in {req.state} first, then elsewhere"
            if req.state and req.city
            else "the nearest matches from elsewhere"
        )
        return (
            f"No listed advocate in {place_phrase} matches {what} closely, so "
            f"these are {fallback}. They are ordered by location, then by how "
            f"closely their profile fits your matter - not by ratings."
        )

    plural = "advocate" if count == 1 else "advocates"
    if place:
        return (
            f"Here {'is' if count == 1 else 'are'} {count} {plural} for {what}, "
            f"nearest to {place_phrase} first. Within each location they are "
            f"ordered by how closely their profile fits your matter - practice "
            f"area, relevant experience and years in practice. No ratings or "
            f"case outcomes are used."
        )
    return (
        f"Here {'is' if count == 1 else 'are'} {count} {plural} for {what}, "
        f"ordered by how closely their profile fits your matter. Add a city to "
        f"your profile or your question to see advocates near you first."
    )


CLARIFY = (
    "Happy to help you find someone. What kind of legal matter is it - "
    "criminal, property, family, employment, a cheque bounce, something else? "
    "Telling me the specific issue gets you a far better match than the city alone."
)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _progress(report, stage: str, detail: str) -> None:
    """One step for the live progress line in the chat - see main._stream_job."""
    if report is None:
        return
    try:
        report(stage, detail)
    except Exception:
        logger.debug("Progress report failed for stage %s", stage)


@router.post("/recommend", response_model=RecommendOut)
async def recommend_advocates(
    payload: RecommendIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return await _recommend_impl(payload, db, current_user)


@router.post("/recommend/stream")
async def recommend_advocates_stream(
    payload: RecommendIn,
    current_user: models.User = Depends(get_current_user),
):
    """/recommend with live progress, as Server-Sent Events.

    Imported at call time: main.py imports this router, so a module-level
    import of main would be circular. By the time a request arrives both
    modules are fully loaded.
    """
    from .main import _stream_job
    return _stream_job(
        lambda db, report: _recommend_impl(payload, db, current_user, report),
        response_model=RecommendOut,
    )


async def _recommend_impl(
    payload: RecommendIn,
    db: Session,
    current_user: models.User,
    report=None,
) -> RecommendOut:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Ask a question first.")

    _progress(report, "search", "Understanding what kind of lawyer you need")
    try:
        req = await parse_requirement(message, payload.history)
    except Exception as exc:
        logger.exception("Advocate query parse failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not read that request. Try rephrasing it.",
        )

    # No place in the question: the user's own profile location stands in.
    # Someone in Bhopal asking for "a property lawyer" means one in Bhopal,
    # and making them type it every time is friction with no upside. Only
    # filled when the question named neither a city nor a state, so an
    # explicit "in Indore" always wins over the profile.
    from_profile = False
    if not req.city and not req.state:
        profile_city = (current_user.city or "").strip() or None
        profile_state = (current_user.state or "").strip() or None
        if profile_city or profile_state:
            req.city, req.state = profile_city, profile_state
            from_profile = True

    understood = {
        "practice_area": req.practice_area,
        "matter_keywords": req.matter_keywords,
        "city": req.city,
        "state": req.state,
        "location_source": "profile" if from_profile else ("query" if (req.city or req.state) else None),
        "language": req.language,
        "min_experience": req.min_experience,
    }

    # "Best lawyer in Indore" is unanswerable as asked, and guessing an area
    # would be worse than asking. One short question, then rank properly.
    if req.is_broad():
        return RecommendOut(kind="clarify", reply=CLARIFY, understood=understood)

    place = req.city or req.state
    what = ", ".join(req.matter_keywords[:2]) or req.practice_area or "your matter"
    _progress(
        report, "fetch",
        f"Searching the directory for {what}" + (f" near {place}" if place else ""),
    )
    rows = _candidates(db, req)
    _progress(
        report, "analyze",
        f"Ranking {len(rows)} advocate{'s' if len(rows) != 1 else ''} by location and fit",
    )

    if not rows:
        return RecommendOut(
            kind="none",
            reply=(
                "There are no listed advocates matching that in the directory yet. "
                "Try a broader area."
            ),
            understood=understood,
        )

    scored = []
    for user, prof in rows:
        result = advocate_match.score_advocate(req, user, prof)
        tier, loc_detail, loc_label = advocate_match.location_tier(
            req.city, req.state, prof, user
        )
        scored.append((result, tier, loc_detail, loc_label, user, prof))

    # Nearest first, then best fit within the tier. This is the whole
    # ranking rule, and it is deliberately this blunt: a user asked for
    # location to come first, so a same-city advocate who clears the
    # relevance gate outranks a stronger profile two hundred km away.
    scored.sort(key=lambda s: (s[1], -s[0]["score"]))

    def relevant(result) -> bool:
        """Did anything in this profile actually speak to the matter asked
        about? A zero on matter_relevance means literally nothing did."""
        if not req.matter_keywords:
            return result["score"] >= MIN_SCORE
        matter = next(
            (f for f in result["factors"] if f["key"] == "matter_relevance"), None
        )
        return bool(matter and matter["points"] > 0) and result["score"] >= MIN_SCORE

    top = [s for s in scored if relevant(s[0])][:SHORTLIST]
    thin = False
    if not top:
        # Nothing clears the bar. Show the nearest few rather than nothing,
        # and say plainly that they are weak matches.
        top = scored[:3]
        thin = True

    has_location = bool(req.city or req.state)
    # Widened when a location was in play but nobody in the shortlist is in
    # the requested city (or, with only a state, the requested state).
    best_tier = min(s[1] for s in top)
    target_tier = (
        advocate_match.LOCAL_CITY if req.city else advocate_match.SAME_STATE
    )
    widened = has_location and not thin and best_tier > target_tier

    _progress(report, "finalize", "Preparing your shortlist")
    conns = _connections(db, current_user.id, [s[4].id for s in top])

    items = []
    for result, tier, loc_detail, loc_label, user, prof in top:
        reasons = advocate_match.top_reasons(result["factors"], limit=2)
        if loc_detail:
            reasons = [loc_detail] + reasons
        items.append(
            ScoredAdvocate(
                card=AdvocateCard(
                    user=_card(user),
                    specialization=prof.specialization,
                    practice_areas=prof.practice_areas,
                    courts=prof.courts,
                    languages=prof.languages,
                    years_experience=prof.years_experience,
                    current_firm=prof.current_firm,
                    practice_city=prof.practice_city,
                    short_bio=_short(prof.professional_bio or user.bio),
                    connection=_state(conns.get(user.id), current_user.id),
                ),
                location_match=loc_label,
                reasons=reasons,
            )
        )

    place = req.city or req.state
    return RecommendOut(
        kind="matches",
        reply=_reply_line(req, len(items), widened, thin, from_profile),
        understood=understood,
        items=items,
        widened=widened,
        note=(f"No close match in {place} - showing the nearest others." if widened and place else ""),
    )