"""Translation of generated answers into Indian languages.

The interface stays in English. What gets translated is the model's own
prose - the explanation, the title, the suggested next steps - and nothing
else.

Why translation happens AFTER generation rather than during it:

  Retrieval is BM25 over English bare acts. The validator compares the
  answer's claims against English source excerpts. validity.assess() reads
  English text. Generating the answer in Kannada would leave every one of
  those layers comparing across a language boundary, and the honest parts of
  this system are exactly the parts that would break. So the pipeline is
  unchanged: retrieve, synthesise, validate - all in English - and translate
  the finished, validated answer on request.

  It also makes switching fast. The English original is always available to
  translate from, so moving between languages is one small call, and a
  cached one after that.

What is never translated:

  Act names, section numbers, case names, court names and citation numbers
  are legal identifiers, not natural language. "Section 103 of the Bharatiya
  Nyaya Sanhita" is how the provision is cited in a Kannada judgment too.
  Translating it produces a string that cannot be looked up, quoted in a
  filing, or searched for - which is worse than leaving it in English.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from . import gemini

logger = logging.getLogger(__name__)

# Kept to what the interface offers. Adding one means adding it here and in
# the frontend picker - deliberately not open-ended, because an unlisted
# language would silently produce an untested register of legal prose.
LANGUAGES: Dict[str, Dict[str, str]] = {
    "en": {"name": "English", "native": "English"},
    "hi": {"name": "Hindi", "native": "हिन्दी"},
    "mr": {"name": "Marathi", "native": "मराठी"},
    "ta": {"name": "Tamil", "native": "தமிழ்"},
    "te": {"name": "Telugu", "native": "తెలుగు"},
    "kn": {"name": "Kannada", "native": "ಕನ್ನಡ"},
}

DEFAULT_LANGUAGE = "en"


def is_supported(code: Optional[str]) -> bool:
    return bool(code) and code in LANGUAGES


def normalise(code: Optional[str]) -> str:
    """Fall back to English rather than failing on an unknown code."""
    return code if is_supported(code) else DEFAULT_LANGUAGE


_TRANSLATE_SYSTEM = """You are a legal translator working for an Indian \
legal-information platform. You are given text written in English by a legal \
assistant, and you render it into {language} ({native}).

This is translation, not rewriting. Keep every claim, qualification, number and \
condition exactly as it is. Do not add explanation the English did not have. Do \
not remove a caveat because it reads awkwardly. Do not soften or strengthen \
anything. If the English says a court MAY do something, the translation must not \
say it WILL.

LEAVE THESE IN ENGLISH, in Latin script, exactly as written:
- Act names: Bharatiya Nyaya Sanhita, Code of Criminal Procedure, Indian Contract \
Act, Constitution of India, and every other statute name
- Section, article and order numbers: "Section 103", "Article 21", "Order VII \
Rule 11" - including the words Section, Article, Order and Rule themselves
- Short forms: BNS, BNSS, CrPC, IPC, CPC, RTI, FIR, NI Act
- Case names and party names: Dashrath Rupsingh Rathod v. State of Maharashtra
- Court names: Supreme Court, Madhya Pradesh High Court, District Consumer \
Disputes Redressal Commission
- Named portals, forms and schemes: e-Daakhil, Vakalatnama, Aadhaar
- All digits, dates, amounts and time limits, in Latin numerals

These are identifiers, not words. A person takes them to a court clerk or types \
them into a search box. Translated, they stop working. Around them, the sentence \
should read naturally in {language} - the identifier sits inside {language} \
grammar, it does not turn the sentence into English.

Write the way a {language} newspaper writes about law: everyday vocabulary, not \
Sanskritised or literary register. If a legal concept has a common {language} \
word people actually use, use it. If it does not, keep the English term rather \
than inventing one.

Return ONLY a JSON object with this exact shape:
{{
  "title": "the title, translated",
  "body": "the body, translated, with the SAME paragraph breaks (blank lines)",
  "next_steps": ["each step, translated, in the same order"]
}}

The next_steps array must have exactly the same number of items as the input."""


def _system_for(code: str) -> str:
    lang = LANGUAGES[code]
    return _TRANSLATE_SYSTEM.format(language=lang["name"], native=lang["native"])


async def translate_answer(
    title: str,
    body: str,
    next_steps: Optional[List[str]],
    target: str,
) -> Dict[str, Any]:
    """Translate a finished answer. One call for all three fields.

    Batched deliberately: three separate calls would be three round trips and
    three chances for the register to drift between the title and the body.
    """
    code = normalise(target)
    if code == DEFAULT_LANGUAGE:
        return {"title": title, "body": body, "next_steps": next_steps or []}

    steps = [s for s in (next_steps or []) if str(s).strip()]

    prompt = (
        "Translate the following. Return JSON only.\n\n"
        f"TITLE:\n{title}\n\n"
        f"BODY:\n{body}\n\n"
        f"NEXT STEPS ({len(steps)} items, return exactly {len(steps)}):\n"
        + json.dumps(steps, ensure_ascii=False, indent=1)
    )

    # Indian scripts cost more tokens per character than English, so the
    # budget has to exceed what the English original needed - and by more
    # than this used to assume. A broad, comparative question ("what is the
    # BNS and how does it differ from the IPC") produces a long English
    # answer, and dividing that length by 2 under-provisioned the Devanagari/
    # Tamil/etc rendering of it: the translation call would get cut off
    # mid-object, fail to parse, and surface as "That translation couldn't
    # be produced" - while short, narrow answers translated fine. The
    # multiplier and ceiling are both raised so long answers have real
    # headroom instead of relying on generate_json's truncation retry to
    # cover the gap every time.
    budget = min(16000, max(3000, int(len(body) * 1.4) + 1500))

    data = await gemini.generate_json(
        prompt,
        _system_for(code),
        temperature=0.1,          # translation, not composition
        max_output_tokens=budget,
    )

    out_steps = data.get("next_steps") or []
    if not isinstance(out_steps, list):
        out_steps = []
    out_steps = [str(s).strip() for s in out_steps if str(s).strip()]

    # A short translation is a failed translation, not a concise one. Falling
    # back to the English step is better than dropping a next step entirely.
    if len(out_steps) != len(steps):
        logger.warning(
            "Translation returned %d next steps for %d input steps (%s); "
            "padding from the English.",
            len(out_steps), len(steps), code,
        )
        out_steps = (out_steps + steps[len(out_steps):])[:len(steps)]

    translated_body = str(data.get("body") or "").strip()
    if not translated_body:
        raise gemini.GeminiError("Translation returned an empty body.")

    return {
        "title": str(data.get("title") or title).strip(),
        "body": translated_body,
        "next_steps": out_steps,
    }


_DOC_SYSTEM = """You are a legal translator working for an Indian \
legal-information platform. You are given a drafted legal document written in \
English, and you render it into {language} ({native}).

Keep the document usable. That means:
- Every heading, numbered clause and paragraph break stays exactly where it is. \
The structure of a legal document carries meaning.
- Blanks and placeholders - ______, [NAME], <DATE> - stay exactly as written.
- Act names, section numbers, case names, court names, form names and short \
forms (BNS, CrPC, FIR, Vakalatnama) stay in English, in Latin script. So do all \
digits, dates and amounts.
- Formal register. This is a document that may be filed, not an explanation.

Translate the operative language and nothing else. Do not add clauses, do not \
remove clauses, do not renumber anything.

Return ONLY a JSON object:
{{
  "body": "the translated document, structure and line breaks preserved"
}}"""


async def translate_document(text: str, target: str) -> str:
    """Translate a drafted document, preserving its structure.

    Separate from translate_answer because the constraints are different: a
    draft has clause numbering and blanks to fill, and reads formally rather
    than plainly.
    """
    code = normalise(target)
    if code == DEFAULT_LANGUAGE:
        return text

    lang = LANGUAGES[code]
    system = _DOC_SYSTEM.format(language=lang["name"], native=lang["native"])

    # Same under-provisioning risk as translate_answer for a long document -
    # see the comment there. Raised in step with it.
    budget = min(20000, max(3000, int(len(text) * 1.4) + 1500))

    data = await gemini.generate_json(
        f"Translate this document. Return JSON only.\n\n-----\n{text}\n-----",
        system,
        temperature=0.1,
        max_output_tokens=budget,
    )

    out = str(data.get("body") or "").strip()
    if not out:
        raise gemini.GeminiError("Document translation returned an empty body.")
    return out