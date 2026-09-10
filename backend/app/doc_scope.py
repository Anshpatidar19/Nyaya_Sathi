"""Scope gates for what this system agrees to read and act on.

Two gates live here:

  check()          - uploaded documents
  check_request()  - text the user typed into the box

Nyaya Sathi answers questions about Indian law. A physics paper, a medical
report or a restaurant menu is not something this system should summarise -
not because summarising is hard, but because an answer wrapped in legal
framing implies legal grounding that isn't there.

The same reasoning applies to typed input. A keyboard mash is not a
drafting instruction, but a generative model will happily treat it as one
and return a plausible-looking legal notice built entirely of bracketed
placeholders. That document is worse than no document: it looks like work
product, so a user may act on it, and nothing in it came from anything they
said. Refusing is the only honest answer.

The gate runs in two stages so it stays cheap:

  1. Keyword scoring - free, catches the obvious cases both ways
  2. Gemini classifier - one small call, only for genuinely ambiguous text

If Gemini isn't configured or the call fails, the keyword verdict stands.
Failing open on an ambiguous document is the right default: the worst case
is a mediocre answer, not a wrong legal claim, and the synthesis step still
refuses to invent law it has no source for.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from . import gemini

logger = logging.getLogger(__name__)

# Below this, there isn't enough text to classify or to answer from.
MIN_CHARS = 200

# How much of the document the classifier looks at. The first couple of
# thousand characters carry the title, parties and subject matter - that's
# what decides the question.
CLASSIFY_CHARS = 3000

# Words that mark Indian legal writing. Deliberately broad: notices,
# agreements, judgments, FIRs, applications and pleadings all qualify.
_LEGAL_TERMS = re.compile(
    r"\b("
    r"act|section|clause|sub-?section|schedule|article|ordinance|statute|"
    r"agreement|contract|deed|lease|licen[cs]e|indemnit\w*|arbitrat\w*|"
    r"plaintiff|defendant|petitioner|respondent|appellant|complainant|accused|"
    r"court|tribunal|magistrate|judge|bench|judgment|decree|order|writ|"
    r"affidavit|vakalatnama|plaint|written statement|pleading|summons|"
    r"notice|legal notice|show cause|fir|charge ?sheet|bail|remand|"
    r"advocate|counsel|solicitor|bar council|jurisdiction|cause of action|"
    r"hereinafter|whereas|witnesseth|hereby|thereof|herein|aforesaid|"
    r"lessor|lessee|landlord|tenant|vendor|purchaser|mortgagor|mortgagee|"
    r"plaintiffs|damages|compensation|penalty|liabilit\w+|breach|"
    r"ipc|crpc|bns|bnss|bsa|cpc|nia?ct|negotiable instruments|"
    r"constitution of india|high court|supreme court|district court|"
    r"consumer forum|rti|right to information|power of attorney|"
    r"stamp duty|registrar|notary|witness|executed|undersigned"
    r")\b",
    re.IGNORECASE,
)

# Strong markers of subjects this platform has no business explaining.
_OUT_OF_SCOPE_TERMS = re.compile(
    r"\b("
    r"photosynthesis|mitochondri\w+|chromosome|enzyme|molecule|molar mass|"
    r"stoichiometr\w+|valency|covalent|periodic table|titration|"
    r"velocity|acceleration|momentum|thermodynamic\w*|electromagnet\w*|"
    r"quantum|wavelength|amplitude|frequenc\w+ of the wave|newton'?s law|"
    r"kinetic energy|potential energy|circuit diagram|resistor|capacitor|"
    r"theorem|hypotenuse|derivative|integral of|matrix multiplication|"
    r"algorithm complexity|neural network|machine learning|source code|"
    r"haemoglobin|hemoglobin|blood pressure|dosage|mg/dl|prescription|"
    r"diagnos\w+|symptoms|radiolog\w+|biopsy|prognosis"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class Verdict:
    in_scope: bool
    reason: str
    doc_kind: str = "document"


def _keyword_score(text: str) -> tuple[int, int]:
    """Returns (legal hits, out-of-scope hits) on the sample we classify."""
    sample = text[:CLASSIFY_CHARS]
    return (
        len(set(m.group(0).lower() for m in _LEGAL_TERMS.finditer(sample))),
        len(set(m.group(0).lower() for m in _OUT_OF_SCOPE_TERMS.finditer(sample))),
    )


REJECTION = (
    "This document doesn't look like an Indian legal document. Nyaya Sathi "
    "only reads notices, agreements, judgments, orders, FIRs, applications "
    "and similar legal material — summarising anything else would give the "
    "answer a legal authority it doesn't have. Upload a legal document, or "
    "ask your question in the box instead."
)


async def check(text: str, filename: str = "") -> Verdict:
    """Decide whether this document is something the platform should read."""
    stripped = (text or "").strip()

    if len(stripped) < MIN_CHARS:
        return Verdict(
            False,
            "There wasn't enough readable text in that file. If it's a scanned "
            "image, it would need OCR first — try a text PDF or a Word file.",
            "unreadable",
        )

    legal, off_topic = _keyword_score(stripped)

    # Clearly legal, nothing pulling the other way: accept without spending
    # a model call.
    if legal >= 6 and off_topic == 0:
        return Verdict(True, "", "legal document")

    # Clearly something else: reject without spending a model call.
    if off_topic >= 3 and legal <= 2:
        return Verdict(False, REJECTION, "out of scope")

    # Ambiguous. Ask the model.
    try:
        result = await gemini.classify_document(stripped[:CLASSIFY_CHARS], filename)
    except Exception as exc:
        logger.info("Scope classifier unavailable (%s); using keyword verdict", exc)
        if legal >= 3:
            return Verdict(True, "", "legal document")
        return Verdict(False, REJECTION, "out of scope")

    if result.get("in_scope"):
        return Verdict(True, "", result.get("doc_kind") or "legal document")

    subject = (result.get("subject") or "").strip()
    detail = f" It looks like {subject} material." if subject else ""
    return Verdict(False, REJECTION + detail, "out of scope")

# ---------------------------------------------------------------------------
# Typed input
# ---------------------------------------------------------------------------

# Shorter than this isn't a request, whatever the characters are. Kept low so
# that real short questions ("can I be evicted?") sail through.
MIN_REQUEST_CHARS = 8

# Runs of letters, Latin or Devanagari. Digits and punctuation are stripped
# out first, so "85,000" and "12 August 2026" don't skew the word count.
_WORD_RUN = re.compile(r"[A-Za-z\u0900-\u097F]+")

# Any script that isn't Latin. Consonant-and-vowel heuristics are an English
# assumption, so a question in Hindi, Tamil or Kannada must skip them
# entirely - the alternative is refusing users who typed perfectly good
# Devanagari because it has no "aeiou".
_NON_LATIN = re.compile(r"[^\x00-\x7F]")

# Legal shorthand people legitimately type, which the vowel test would
# otherwise throw out.
_KNOWN_SHORTHAND = {
    "ipc", "crpc", "bns", "bnss", "bsa", "cpc", "rti", "fir", "nda", "mou",
    "llp", "gst", "pan", "tds", "hra", "noc", "poa", "sc", "hc", "ni",
}

_VOWELS = set("aeiouy")


def _word_like(token: str) -> bool:
    """Could a person have meant to type this?

    Not a dictionary check - it only asks whether the token has the shape of
    a word in a language that uses vowels. "cheque" passes, "ghljdsglkd"
    doesn't.
    """
    low = token.lower()
    if low in _KNOWN_SHORTHAND:
        return True
    if len(low) > 20:                       # longer than any real English word
        return False
    if not (set(low) & _VOWELS):            # no vowel at all
        return False

    vowels = sum(1 for ch in low if ch in _VOWELS)

    # Five or more consonants in a row happens in keyboard mash and almost
    # nowhere else. A handful of real words ("strengths") trip it; that only
    # matters if one is the entire request, and the multi-token rule below
    # forgives it anywhere else.
    run = 0
    for ch in low:
        run = 0 if ch in _VOWELS else run + 1
        if run >= 5:
            return False

    # Long and starved of vowels. English runs around a third; "asdkjhaskjdh"
    # is under a fifth.
    if len(low) >= 8 and vowels / len(low) < 0.25:
        return False

    return True


GIBBERISH = (
    "That doesn't read as a request Nyaya Sathi can act on. Describe the "
    "situation in a sentence or two — who is involved, what happened, and "
    "what you need — and it will have something real to work from."
)

TOO_SHORT = (
    "There isn't enough there to work from. Describe what you need in a "
    "sentence or two."
)


def check_request(text: str, *, allow_short: bool = False) -> Verdict:
    """Decide whether typed input is a real request.

    allow_short waives the length floor for cases where a document carries
    the context and the typed part is only a nudge - "summarise", "explain".
    The gibberish tests still run.

    Runs before retrieval and before any model call, because the failure this
    prevents is the model being fluent about nothing: hand it a keyboard mash
    and it returns a notice-shaped document with every fact bracketed. Cheap,
    local and deterministic - no API call, so it costs nothing to run on
    every request.

    Deliberately conservative. It only refuses input it can positively show
    is malformed; anything it cannot judge, including every non-Latin script,
    passes through to the normal pipeline.
    """
    stripped = (text or "").strip()

    if not stripped:
        return Verdict(True, "", "empty") if allow_short else Verdict(
            False, TOO_SHORT, "too short"
        )

    if not allow_short and len(stripped) < MIN_REQUEST_CHARS:
        return Verdict(False, TOO_SHORT, "too short")

    # Not English - the tests below don't apply, and guessing would refuse
    # legitimate users. Let it through.
    if _NON_LATIN.search(stripped):
        return Verdict(True, "", "request")

    tokens = _WORD_RUN.findall(stripped)

    # All digits and punctuation, no letters anywhere.
    if not tokens:
        return Verdict(False, GIBBERISH, "gibberish")

    # Punctuation soup: "gob';fk;ghljdsglkd" is mostly separators. Real
    # writing sits well under this even with heavy comma use.
    letters = sum(len(t) for t in tokens)
    symbols = sum(1 for ch in stripped if not ch.isalnum() and not ch.isspace())
    if letters and symbols > letters * 0.4:
        return Verdict(False, GIBBERISH, "gibberish")

    good = sum(1 for t in tokens if _word_like(t))

    # One token, and it isn't word-shaped: "asdkjhaskjdh".
    if len(tokens) == 1:
        if not good:
            return Verdict(False, GIBBERISH, "gibberish")
        return Verdict(True, "", "request")

    # Several tokens: refuse only when most of them are unreadable. A real
    # request with one typo or an odd surname stays well above this.
    if good / len(tokens) < 0.5:
        return Verdict(False, GIBBERISH, "gibberish")

    return Verdict(True, "", "request")