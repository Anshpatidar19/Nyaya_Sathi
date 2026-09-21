"""The Find an Advocate assistant.

    POST /network/advocates/recommend

One turn: a natural-language question in, a ranked shortlist out. The
pipeline is deliberately split so the model never touches the ranking:

    question
      -> Gemini parses it into a Requirement (area, matter, city, language)
      -> that drives the SAME directory query the search page uses
      -> advocate_match scores the candidates in Python
      -> the reply sentence is a template over the scores

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
# returns, plus the score.
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


class FactorOut(BaseModel):
    key: str
    label: str
    detail: str
    points: float
    max_points: int


class ScoredAdvocate(BaseModel):
    card: AdvocateCard
    score: int
    reasons: List[str] = []
    factors: List[FactorOut] = []


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

def _candidates(db: Session, req: Requirement, city_filter: bool) -> List:
    """The directory, filtered only where a filter cannot wrongly exclude.

    City is the one safe narrowing - someone asking for an advocate in
    Indore does not want Chennai - and even that is dropped and retried if
    it empties the list. Practice area is never a WHERE clause; that is the
    scorer's job.
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

    if city_filter and req.city:
        like = f"%{req.city.strip()}%"
        query = query.filter(
            or_(
                models.User.city.ilike(like),
                models.AdvocateProfile.practice_city.ilike(like),
            )
        )

    if req.min_experience is not None:
        query = query.filter(
            models.AdvocateProfile.years_experience >= req.min_experience
        )

    return query.limit(MAX_CANDIDATES).all()


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
# whole feature: the score is presented as a match score computed from
# profile fields, and it cannot drift into "top rated" because no model is
# writing this sentence.
def _reply_line(req: Requirement, count: int, widened: bool, thin: bool = False) -> str:
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
    where = f" in {req.city}" if req.city else ""

    if thin:
        return (
            f"No advocate in the directory is a close match for {what}{where}. "
            f"These are the nearest profiles, but none of them lists that work "
            f"specifically - worth checking their profiles before you connect."
        )

    if widened:
        return (
            f"No listed advocate{where} matches {what} closely, so these are the "
            f"nearest matches from elsewhere. Match scores are calculated from "
            f"profile information - practice area, relevant experience, location, "
            f"years in practice - and are not user ratings."
        )

    plural = "advocate" if count == 1 else "advocates"
    return (
        f"These {count} {plural} have the strongest match scores for {what}{where}. "
        f"The scores come from profile information such as practice area, relevant "
        f"experience, location and years in practice. They are not user ratings, "
        f"and no advocate here has been ranked by case outcomes."
    )


CLARIFY = (
    "Happy to help you find someone. What kind of legal matter is it - "
    "criminal, property, family, employment, a cheque bounce, something else? "
    "Telling me the specific issue gets you a far better match than the city alone."
)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/recommend", response_model=RecommendOut)
async def recommend_advocates(
    payload: RecommendIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Ask a question first.")

    try:
        req = await parse_requirement(message, payload.history)
    except Exception as exc:
        logger.exception("Advocate query parse failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not read that request. Try rephrasing it.",
        )

    understood = {
        "practice_area": req.practice_area,
        "matter_keywords": req.matter_keywords,
        "city": req.city,
        "language": req.language,
        "min_experience": req.min_experience,
    }

    # "Best lawyer in Indore" is unanswerable as asked, and guessing an area
    # would be worse than asking. One short question, then rank properly.
    if req.is_broad():
        return RecommendOut(kind="clarify", reply=CLARIFY, understood=understood)

    rows = _candidates(db, req, city_filter=True)
    widened = False
    if not rows and req.city:
        rows = _candidates(db, req, city_filter=False)
        widened = True

    if not rows:
        return RecommendOut(
            kind="none",
            reply=(
                "There are no listed advocates matching that in the directory yet. "
                "Try a broader area, or drop the city."
            ),
            understood=understood,
        )

    scored = []
    for user, prof in rows:
        result = advocate_match.score_advocate(req, user, prof)
        scored.append((result, user, prof))

    scored.sort(key=lambda s: s[0]["score"], reverse=True)

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

    conns = _connections(db, current_user.id, [u.id for _, u, _ in top])

    items = [
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
            score=result["score"],
            reasons=advocate_match.top_reasons(result["factors"]),
            factors=[FactorOut(**f) for f in result["factors"]],
        )
        for result, user, prof in top
    ]

    return RecommendOut(
        kind="matches",
        reply=_reply_line(req, len(items), widened, thin),
        understood=understood,
        items=items,
        widened=widened,
        note=(
            f"Showing advocates outside {req.city}."
            if widened and req.city
            else ""
        ),
    )