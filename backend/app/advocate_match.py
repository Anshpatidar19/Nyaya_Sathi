"""Advocate Match Score.

A transparent, explainable score over fields that actually exist in
`advocate_profiles`. No ratings, no reviews, no success rates, no case
outcomes - none of those are in the schema and inventing them would make
the number a lie dressed as data.

Two factors from the original brief are deliberately NOT here:

  Verification. There is no verification field. `bar_council_number` is a
  string the advocate typed in themselves, and `User.role` is - by its own
  comment - never verified. It is scored as "enrollment number on file",
  which is exactly what it is, and the UI must say that too.

  Availability. No such field. `is_listed` is a directory on/off switch:
  an unlisted advocate never reaches this module, so it cannot discriminate
  between the ones that do.

Location is not a scored factor either. It used to be 15 points inside
the total, which meant a strong specialist two cities away could outrank a
decent one in the user's own city - and the brief now is the opposite:
nearest first. Location is therefore a separate TIER (location_tier) that
the caller sorts on before relevance, and the score below is purely "how
well does this profile fit the matter". The score is used for ordering and
for the relevance gate only; it is never shown to the user.

Their weight went to the factors that can be evidenced. Everything is in
WEIGHTS, one dict, so reweighting is a one-line change and real ratings can
be added later as another entry without touching the scorers.

Every scorer returns a 0..1 fraction AND the sentence explaining it. That
pairing is the point: a card can show why it ranked where it did using the
same numbers that put it there, instead of asking a model to invent a
justification after the fact.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Relevance is worth 70 of the 100 points, split between the broad area and
# the specific matter. The split is not fixed, because which of the two
# carries the information depends on what was asked - see weights_for().
#
# The specific matter outweighs the area whenever both are present. The
# reason is that the area is usually INFERRED: someone asking about a
# cheque-bounce case never said "Criminal Law", the parser supplied it. An
# inferred label must not outrank the thing the person actually said, or
# every criminal advocate scores identically on the 40 points that matter
# most and the query may as well have been the category alone.
WEIGHTS: Dict[str, int] = {
    "practice_area": 30,
    "matter_relevance": 40,
    "experience": 12,
    "languages": 6,
    "completeness": 6,
    "enrollment": 6,
}


def weights_for(req: "Requirement") -> Dict[str, int]:
    """Relevance weight follows whichever signal the user actually gave.

    With only a broad area, the area is the whole relevance signal and
    absorbs the matter weight. With only a specific matter, the reverse.
    A factor weighted 0 here is dropped from the score entirely rather
    than scored as zero - see score_advocate.
    """
    has_area = bool(req.practice_area)
    has_matter = bool(req.matter_keywords)

    if has_area and has_matter:
        return dict(WEIGHTS)
    if has_area:
        return {**WEIGHTS, "practice_area": 70, "matter_relevance": 0}
    if has_matter:
        return {**WEIGHTS, "practice_area": 0, "matter_relevance": 70}
    return dict(WEIGHTS)

# Experience stops earning at this point. Past it, more years is not more
# relevant - it is just more years, and letting it run unbounded is how a
# generalist climbs over a specialist.
EXPERIENCE_CEILING = 20

# Words that carry no matching signal. "law" and "lawyer" appear in almost
# every profile, so leaving them in makes everyone look like a match.
STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "in", "at", "of", "my", "me", "i",
    "need", "want", "find", "looking", "help", "case", "matter", "issue",
    "problem", "best", "good", "top", "advocate", "advocates", "lawyer",
    "lawyers", "legal", "law", "please", "someone", "who", "with", "about",
    "is", "are", "have", "has", "can", "do", "does", "near", "some",
}


@dataclass
class Requirement:
    """What the user asked for, after parsing. Every field optional."""

    practice_area: Optional[str] = None
    matter_keywords: List[str] = field(default_factory=list)
    city: Optional[str] = None
    state: Optional[str] = None
    language: Optional[str] = None
    min_experience: Optional[int] = None

    def is_broad(self) -> bool:
        """Too vague to rank honestly.

        A city alone is not enough: "best lawyer in Indore" can only be
        answered by asking what the matter is. A practice area or any
        matter keyword is enough to proceed.
        """
        return not self.practice_area and not self.matter_keywords


@dataclass
class Factor:
    key: str
    label: str
    detail: str
    points: float
    max_points: int


def tokens(text: Optional[str]) -> set:
    if not text:
        return set()
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {w for w in cleaned.split() if len(w) > 2 and w not in STOPWORDS}


def _csv(text: Optional[str]) -> List[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Individual scorers. Each returns (fraction 0..1, detail sentence).
# ---------------------------------------------------------------------------

def score_practice_area(req: Requirement, prof: Any) -> Tuple[float, str]:
    """Overlap between the asked-for area and what the advocate practises.

    `specialization` counts for more than `practice_areas`: the headline
    area is what someone actually does most days, while the tag list is
    broader and cheaper to pad.
    """
    want = tokens(req.practice_area) if req.practice_area else set()
    if not want:
        return 0.0, "No specific practice area requested"

    spec = tokens(prof.specialization)
    areas = tokens(prof.practice_areas)

    spec_hit = len(want & spec) / len(want)
    area_hit = len(want & areas) / len(want)

    fraction = min(1.0, spec_hit + 0.55 * area_hit)

    if spec_hit >= 0.8:
        detail = f"Specialises in {prof.specialization}"
    elif spec_hit > 0:
        detail = f"Practises {prof.specialization}, which overlaps your matter"
    elif area_hit > 0:
        matched = [a for a in _csv(prof.practice_areas) if tokens(a) & want]
        detail = "Lists " + ", ".join(matched[:2]) + " among their practice areas"
    else:
        detail = f"Main area is {prof.specialization or 'unspecified'}, not your matter"

    return fraction, detail


def score_matter_relevance(req: Requirement, prof: Any, user: Any) -> Tuple[float, str]:
    """How much of the specific matter shows up in this profile.

    This is where "cheque bounce" beats "Criminal Law" as a query. The tag
    list, the courts and the experience narrative are all searched, and a
    court hit counts double - an advocate who actually appears at MACT is
    better evidence for a motor-accident claim than one who lists insurance
    disputes as a tag.
    """
    want = set()
    for kw in req.matter_keywords:
        want |= tokens(kw)
    if not want:
        return 0.0, "No specific matter details given"

    courts = tokens(prof.courts)
    narrative = (
        tokens(prof.practice_areas)
        | tokens(prof.notable_experience)
        | tokens(prof.professional_bio)
        | tokens(user.bio)
        | tokens(prof.specialization)
    )

    court_hits = want & courts
    other_hits = (want & narrative) - court_hits

    # Divide by the terms asked for, NOT by twice that. The earlier version
    # doubled the denominator to make room for the court bonus, which
    # quietly capped every profile at half marks unless the matter words
    # appeared in the courts column - and "cheque bounce" is never a court
    # name. A court hit is now a bonus on top, and the result is clamped.
    hits = len(court_hits) + len(other_hits)
    fraction = min(1.0, (hits + len(court_hits)) / len(want))

    if court_hits:
        forum = next(
            (c for c in _csv(prof.courts) if tokens(c) & court_hits), None
        )
        detail = f"Appears at {forum}" if forum else "Appears at a relevant forum"
    elif other_hits:
        # Name the practice-area tag that matched, not the raw tokens that
        # matched it. "Profile mentions bounce, cheque" is the search
        # engine's internals leaking onto a card an advocate's client is
        # reading; "Lists Cheque Bounce among their practice areas" is the
        # same fact stated in the profile's own words.
        tags = [a for a in _csv(prof.practice_areas) if tokens(a) & other_hits]
        if tags:
            detail = "Lists " + " and ".join(tags[:2]) + " among their practice areas"
        else:
            detail = "Bio describes work on this kind of matter"
    else:
        detail = "Nothing in the profile speaks to this specific matter"

    return fraction, detail


# Location tiers. Lower sorts first.
LOCAL_CITY = 0
SAME_STATE = 1
ELSEWHERE = 2


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def location_tier(
    city: Optional[str], state: Optional[str], prof: Any, user: Any
) -> Tuple[int, Optional[str], str]:
    """(tier, detail sentence, label) for how close this advocate is.

    Exact city beats same state beats elsewhere. Both city fields are
    checked: an advocate living in Dewas who practises in Indore is an
    Indore advocate for this purpose.

    With no location at all - none asked for and none on the user's
    profile - everyone shares one tier and the detail is None, so the card
    does not claim a proximity that was never measured.
    """
    want_city, want_state = _norm(city), _norm(state)
    if not want_city and not want_state:
        return LOCAL_CITY, None, "any"

    here = {_norm(user.city), _norm(prof.practice_city)}
    here.discard("")

    if want_city and want_city in here:
        where = prof.practice_city or user.city
        return LOCAL_CITY, f"Practises in {where}", "city"

    if want_state and _norm(user.state) == want_state:
        base = user.city or prof.practice_city
        if base:
            return SAME_STATE, f"Based in {base}, {user.state}", "state"
        return SAME_STATE, f"Based in {user.state}", "state"

    elsewhere = ", ".join(p for p in [prof.practice_city or user.city, user.state] if p)
    return ELSEWHERE, f"Based in {elsewhere or 'another state'}", "other"


def score_experience(req: Requirement, prof: Any) -> Tuple[float, str]:
    years = prof.years_experience
    if years is None:
        return 0.0, "Years of practice not stated"

    fraction = min(1.0, years / EXPERIENCE_CEILING)
    plural = "" if years == 1 else "s"
    return fraction, f"{years} year{plural} in practice"


def score_languages(req: Requirement, prof: Any) -> Tuple[float, str]:
    spoken = _csv(prof.languages)
    if not req.language:
        # Not asked for, so this factor is dropped from the total rather
        # than scored. The value here is never used.
        return 0.0, ("Speaks " + ", ".join(spoken)) if spoken else "Languages not listed"

    want = req.language.strip().lower()
    for lang in spoken:
        if want in lang.lower():
            return 1.0, f"Speaks {lang}"
    return 0.0, f"Does not list {req.language}"


def score_completeness(prof: Any, user: Any) -> Tuple[float, str]:
    """A filled-in profile is a weak but real signal, and it is honest:
    it measures the profile, and says so, rather than pretending to
    measure the advocate."""
    checks = [
        bool(prof.professional_bio),
        bool(prof.practice_areas),
        bool(prof.courts),
        bool(prof.languages),
        bool(prof.years_experience is not None),
        bool(prof.current_firm or prof.previous_firms),
        bool(prof.llb_college),
        bool(user.avatar_path),
    ]
    filled = sum(checks)
    fraction = filled / len(checks)
    return fraction, f"Profile {round(fraction * 100)}% complete"


def score_enrollment(prof: Any) -> Tuple[float, str]:
    """NOT verification. The number is self-entered and nothing checks it
    against the Bar Council roll, so the label says only that it is on
    file."""
    if prof.bar_council_number:
        return 1.0, "Bar Council enrollment number on file (self-declared)"
    return 0.0, "No enrollment number on file"


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------

def score_advocate(req: Requirement, user: Any, prof: Any) -> Dict[str, Any]:
    """Returns {score, factors} where score is 0-100.

    A factor the user did not ask about is DROPPED, not scored neutral.
    Handing every advocate the same half-marks for a location nobody
    requested does not change the order, but it hands out free points that
    squeeze the whole field into a narrow band - the reason four advocates
    with very different relevance all landed between 66 and 77. The score
    is now renormalised over the factors that actually applied, so it
    answers "how well does this advocate fit what you asked", not "how
    filled-in is this row".
    """
    weights = weights_for(req)
    applicable = {
        "practice_area": weights["practice_area"] > 0,
        "matter_relevance": weights["matter_relevance"] > 0,
        "experience": True,
        "languages": bool(req.language),
        "completeness": True,
        "enrollment": True,
    }

    raw = [
        ("practice_area", "Practice area", *score_practice_area(req, prof)),
        ("matter_relevance", "Relevant experience", *score_matter_relevance(req, prof, user)),
        ("experience", "Years of practice", *score_experience(req, prof)),
        ("languages", "Language", *score_languages(req, prof)),
        ("completeness", "Profile detail", *score_completeness(prof, user)),
        ("enrollment", "Enrollment", *score_enrollment(prof)),
    ]

    factors: List[Factor] = []
    total = 0.0
    available = 0
    for key, label, fraction, detail in raw:
        if not applicable[key]:
            continue
        weight = weights[key]
        points = round(fraction * weight, 1)
        total += points
        available += weight
        factors.append(Factor(key, label, detail, points, weight))

    score = round(100 * total / available) if available else 0
    score = max(0, min(100, score))

    return {
        "score": score,
        "factors": [f.__dict__ for f in factors],
    }


def top_reasons(factors: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    """The strongest factors, for the "why this advocate" line.

    Ranked by how much of each factor's available weight was actually
    earned, not by raw points - otherwise the 40-point practice-area factor
    appears at the top of every card even when it scored badly, and the
    explanation stops explaining anything.
    """
    ranked = sorted(
        factors,
        key=lambda f: (f["points"] / f["max_points"] if f["max_points"] else 0),
        reverse=True,
    )
    return [f["detail"] for f in ranked if f["points"] > 0][:limit]