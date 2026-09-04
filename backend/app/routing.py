"""Decides whether a question needs Indian Kanoon at all.

Kanoon is prepaid and metered - one search plus up to three document
fetches per question. Many questions don't need any of that:

    "give me the preamble of the constitution"  -> the text is local
    "what is BNS 85"                            -> the section is local
    "tell me about the BNS"                     -> a hand-written overview

Case law earns its cost when the user has a *situation* rather than a
lookup: how courts have actually applied a provision, what counts as
sufficient notice, whether something amounts to cruelty. That's what
judgments answer and bare acts don't.

`decide()` returns a Decision with a `reason` so the log tells you why
each call was or wasn't made.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import statutes


@dataclass
class Decision:
    call_kanoon: bool
    reason: str
    doc_fetches: int = 2   # how many judgments to hydrate if we do call


# --- Signals that the answer is already local -----------------------------

# "give me the preamble", "text of article 21", "what does section 85 say"
_LOOKUP_RE = re.compile(
    r"\b(preamble|what (?:is|are|does)|what'?s|define|definition of|meaning of|"
    r"text of|full text|state the|give me (?:the )?(?:text|wording)|wording of|"
    r"which section|which article|list the)\b",
    re.IGNORECASE,
)

# Pure recall of a provision, no dispute attached.
_STATUTE_ONLY_RE = re.compile(
    r"\b(section|sec\.?|s\.?|u/s|article|art\.?)\s*\d",
    re.IGNORECASE,
)

# --- Signals that case law is genuinely wanted ----------------------------

_CASE_LAW_RE = re.compile(
    r"\b(case|cases|judgment|judgement|judgments|precedent|ruling|verdict|"
    r"held|court held|supreme court|high court|landmark|citation|"
    r"has any court|what did the court|case law|decided)\b",
    re.IGNORECASE,
)

# Someone describing their own situation. Bare acts state the rule; judgments
# show how it's applied, which is what these questions actually need.
_SITUATION_RE = re.compile(
    r"\b(my |i (?:was|am|have|had|want|need|got|received|paid|bought|signed)|"
    r"can i|should i|do i|am i|someone|neighbour|neighbor|landlord|tenant|"
    r"employer|boss|husband|wife|company|refused|denied|cheated|threatened|"
    r"how do i|what can i do|what should i|is it legal|am i entitled)\b",
    re.IGNORECASE,
)


def decide(
    question: str,
    statute_hits: List[Dict[str, Any]],
    overview: Optional[Dict[str, Any]] = None,
) -> Decision:
    """Should this question spend Kanoon credit?"""

    # 1. Act-level overview - answered from a hand-written summary.
    if overview:
        return Decision(False, "act overview, answered locally")

    # 2. Preamble and other named local texts.
    if statutes._PREAMBLE_RE.search(question):
        return Decision(False, "preamble, text held locally")

    # 3. Explicit citation lookup: "what is BNS 85", "article 21".
    #    lookup_section() already matched it exactly - judgments add nothing
    #    to a request for the text of a provision.
    exact = statutes.lookup_section(question)
    wants_cases = bool(_CASE_LAW_RE.search(question))
    if exact and not wants_cases:
        return Decision(False, "exact section lookup, text held locally")

    # 4. The user explicitly asked about cases. Always worth the call.
    if wants_cases:
        return Decision(True, "user asked for case law", doc_fetches=3)

    # 5. Definitional phrasing with a solid local match - "what is cheating",
    #    "define grievous hurt". The section defines it; a judgment doesn't
    #    make the definition clearer.
    if (
        _LOOKUP_RE.search(question)
        and statute_hits
        and not _SITUATION_RE.search(question)
    ):
        return Decision(False, "definitional question, statute answers it")

    # 6. A described situation - this is where case law earns its keep.
    if _SITUATION_RE.search(question):
        return Decision(True, "situational question, case law applies the rule")

    # 7. Bare citation with no other signal, e.g. "s. 138".
    if _STATUTE_ONLY_RE.search(question) and statute_hits:
        return Decision(False, "provision named, no dispute described")

    # 8. Nothing matched locally - Kanoon is the only source left.
    if not statute_hits:
        return Decision(True, "no local statute match, case law is the only source", doc_fetches=3)

    # 9. Default: local statutes plus a light case-law check.
    return Decision(True, "general legal question", doc_fetches=2)