"""Scope gate for uploaded documents.

Nyaya Sathi answers questions about Indian law. A physics paper, a medical
report or a restaurant menu is not something this system should summarise -
not because summarising is hard, but because an answer wrapped in legal
framing implies legal grounding that isn't there.

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