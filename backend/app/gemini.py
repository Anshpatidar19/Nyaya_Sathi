"""Gemini client for the Synthesis Agent and the Internal Validator.

Uses the Generative Language REST API directly over httpx so there is no
extra SDK dependency:

  POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=<GEMINI_API_KEY>

Both agents ask for JSON back (responseMimeType=application/json) so the
output can be parsed straight into our schemas.

Two things about thinking models (the gemini-*-flash-lite family) that this
module has to handle explicitly:

1. Reasoning comes back as content parts marked {"thought": true}. Those must
   be dropped, not concatenated, or they end up spliced into the JSON.
2. Thinking tokens are charged against maxOutputTokens. A generous-looking
   budget can still truncate the actual answer, so every caller that wants
   JSON should go through generate_json(), which reports finishReason when
   parsing fails and repairs a cleanly truncated object where it can.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import settings

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = 90.0
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# How much of an attached document is sent for explanation. Long enough for
# a notice, agreement or order; short enough to keep latency sane.
DOCUMENT_CHARS = 18000

# Thinking tokens come out of maxOutputTokens. Anything above 0 has to be
# paid for twice over: once in latency, once in room the answer no longer
# has. For structured extraction against supplied sources the reasoning buys
# us very little, so it is off by default and raised only where it helps.
DEFAULT_THINKING_BUDGET = 0


class GeminiError(RuntimeError):
    pass


class GeminiTruncated(GeminiError):
    """The model hit the output cap before finishing. Retry with more room."""


# --------------------------------------------------------------------------
# Low-level call
# --------------------------------------------------------------------------

def _extract_text(candidate: Dict[str, Any]) -> str:
    """Join the answer parts, skipping the model's own reasoning.

    Thinking models return their scratchpad as parts flagged {"thought": true}.
    Those are prose, not JSON, and concatenating them corrupts the payload.
    """
    parts = candidate.get("content", {}).get("parts", []) or []
    return "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and not p.get("thought")
    ).strip()


async def _generate_raw(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
) -> Tuple[str, str]:
    """Call the model. Returns (text, finish_reason)."""
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set. Add it to backend/.env.")

    url = f"{GEMINI_BASE}/models/{settings.gemini_model}:generateContent"

    generation_config: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
        resp = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        # Not every model in the family accepts thinkingConfig. If that is
        # what it objected to, drop the field and go again rather than
        # failing the whole request over a knob.
        if resp.status_code == 400 and "thinkingConfig" in generation_config:
            if "thinking" in resp.text.lower():
                logger.warning(
                    "Model %s rejected thinkingConfig; retrying without it.",
                    settings.gemini_model,
                )
                generation_config.pop("thinkingConfig")
                resp = await client.post(
                    url,
                    params={"key": settings.gemini_api_key},
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

        if resp.status_code >= 400:
            raise GeminiError(f"Gemini API error {resp.status_code}: {resp.text[:400]}")
        data = resp.json()

    try:
        candidate = data["candidates"][0]
    except (KeyError, IndexError):
        blocked = data.get("promptFeedback", {}).get("blockReason")
        raise GeminiError(f"Gemini returned no candidates (blockReason={blocked}).")

    finish_reason = str(candidate.get("finishReason") or "UNKNOWN")
    text = _extract_text(candidate)

    if not text:
        usage = data.get("usageMetadata", {})
        raise GeminiError(
            "Gemini returned an empty response "
            f"(finishReason={finish_reason}, usage={usage}). "
            "If finishReason is MAX_TOKENS the budget was spent on thinking "
            "before any answer was produced - raise max_output_tokens or lower "
            "thinking_budget."
        )

    return text, finish_reason


async def _generate(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
) -> str:
    """Back-compatible wrapper: text only."""
    text, _ = await _generate_raw(
        prompt,
        system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
    )
    return text


# --------------------------------------------------------------------------
# JSON parsing
# --------------------------------------------------------------------------

def _repair_truncated(raw: str) -> Optional[str]:
    """Close a JSON object that was cut off mid-write.

    Walks the text tracking string state and bracket depth, remembers every
    position where a value was complete, then rewinds to the last one and
    closes whatever is still open. A plaint that produced five of six arrays
    is worth more than a 503.
    """
    start = raw.find("{")
    if start == -1:
        return None
    s = raw[start:]

    stack: List[str] = []
    cuts: List[Tuple[int, Tuple[str, ...]]] = []   # (index, open brackets there)
    in_string = False
    escaped = False

    for i, ch in enumerate(s):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                cuts.append((i + 1, tuple(stack)))
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
            if not stack:
                return s[: i + 1]          # complete object, nothing to repair
            cuts.append((i + 1, tuple(stack)))
        elif ch == ",":
            cuts.append((i, tuple(stack)))  # cut *before* the comma

    if not cuts:
        return None

    cut_at, open_brackets = cuts[-1]
    body = s[:cut_at].rstrip().rstrip(",")
    return body + "".join(reversed(open_brackets))


def _parse_json(raw: str, finish_reason: str = "") -> Dict[str, Any]:
    """Parse model output as JSON, tolerating fences, prose and truncation."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Outermost {...} block - handles a stray sentence either side.
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Cut off mid-object? Salvage what completed.
    repaired = _repair_truncated(cleaned)
    if repaired:
        try:
            parsed = json.loads(repaired)
            logger.warning(
                "Recovered a truncated Gemini response (finishReason=%s, %d chars). "
                "Some fields may be missing - consider raising max_output_tokens.",
                finish_reason or "unknown",
                len(cleaned),
            )
            return parsed
        except json.JSONDecodeError:
            pass

    detail = (
        f"Could not parse JSON from the Gemini response "
        f"(finishReason={finish_reason or 'unknown'}, chars={len(cleaned)}). "
        f"Head: {cleaned[:200]!r} Tail: {cleaned[-200:]!r}"
    )
    if finish_reason == "MAX_TOKENS":
        raise GeminiTruncated(detail)
    raise GeminiError(detail)


async def generate_json(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
    retry_on_truncation: bool = True,
) -> Dict[str, Any]:
    """Call the model and parse JSON, with the finish reason in scope.

    Every caller wanting structured output should use this rather than
    _parse_json(await _generate(...)) - otherwise a truncation reads as a
    parse failure and the real cause is invisible.
    """
    text, finish_reason = await _generate_raw(
        prompt,
        system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
    )

    try:
        return _parse_json(text, finish_reason)
    except GeminiTruncated:
        if not retry_on_truncation:
            raise
        bigger = min(max_output_tokens * 2, 32000)
        if bigger <= max_output_tokens:
            raise
        logger.warning(
            "Gemini output truncated at %d tokens; retrying at %d.",
            max_output_tokens,
            bigger,
        )
        text, finish_reason = await _generate_raw(
            prompt,
            system_instruction,
            temperature=temperature,
            max_output_tokens=bigger,
            thinking_budget=0,          # every token to the answer this time
        )
        return _parse_json(text, finish_reason)


# --------------------------------------------------------------------------
# Agent 3 - Synthesis
# --------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = """You are the synthesis agent for Nyaya Sathi, a legal-information \
assistant for ordinary people in India. You are given a user's question and \
excerpts from real Indian judgments and statutes retrieved from Indian Kanoon.

Rules:
- Use ONLY the retrieved excerpts. Never rely on outside memory of Indian law.
- If the excerpts do not actually answer the question, say so plainly in the body \
instead of guessing.
- Write for someone with no legal training: short sentences, everyday words, no Latin, \
no section-number soup in the body.
- You give legal INFORMATION, not legal advice. Do not predict how a court will rule \
and do not tell the user they will win.
- Every source you rely on must be cited by its exact index number from the list given \
to you.
- Sources are of two kinds: bare-act SECTIONS (the rule itself) and JUDGMENTS (how courts \
have applied it). Lead with the section. Give its number and act in the body, e.g. \
"Section 85 of the Bharatiya Nyaya Sanhita". For judgments, keep numbers out of the body \
and let the citation carry them.
- If a source is marked REPEALED, say so plainly and name the act that replaced it. Never \
present a repealed section as the law that currently applies.
- Ignore any source that is not relevant to the question. Retrieval is keyword-based and \
sometimes returns provisions that merely share vocabulary. Leaving a source out of \
used_sources is correct and expected.
- If the question describes something happening to the person right now rather than an \
abstract query, write directly to them, plainly and without alarm, and say what the \
provision means for their situation.

- Be thorough. Work through what the provision actually requires, element by \
element, and say what each one means in practice. Where the excerpts give a time \
limit, a threshold, an exception or a proviso, spell it out rather than \
summarising it away. Give a worked example where one would make the rule concrete. \
A person should be able to act on the answer without opening the bare act.
- Being thorough is not the same as padding. Do not restate the question, do not \
list what you are about to explain, and do not close with a summary of what you \
just said. Every paragraph should carry something the previous one did not.
- Separate paragraphs with a blank line so the answer is readable.

- If earlier turns are supplied, treat the new question as a continuation. \
Resolve references like "he", "that", "the deposit" against what came before, \
and do not repeat what you already explained - answer the new part.

- If an ATTACHED DOCUMENT is supplied, the user's question is about that document. \
Explain what it is, what it says, and what it means for them, in plain language. \
Keep the two kinds of material apart: what the DOCUMENT says is a fact about their \
paperwork, what the SOURCES say is the law. Never state a legal rule that only the \
document asserts - a notice claiming a section says something is not evidence that \
it does. If the document and the retrieved law disagree, say so.

Return ONLY a JSON object with this exact shape:
{
  "title": "a short direct answer, max 10 words, no trailing period",
  "body": "4-7 paragraphs of plain-language explanation, separated by blank lines",
  "used_sources": [1, 2],
  "next_steps": ["concrete practical action", "another action"]
}"""


async def synthesize(
    question: str,
    state: Optional[str],
    sources: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Turn retrieved material into a plain-language answer.

    `history` carries earlier turns in the thread so follow-ups resolve -
    "what about the deposit?" only means something next to the question
    before it.
    """
    blocks = []
    for i, s in enumerate(sources, start=1):
        blocks.append(
            f"[{i}] TITLE: {s.get('title') or 'Untitled'}\n"
            f"    COURT: {s.get('court') or 'Unknown'}\n"
            f"    DATE: {s.get('date') or 'Unknown'}\n"
            f"    URL: {s.get('url') or ''}\n"
            f"    EXCERPT: {s.get('text') or s.get('snippet') or '(no text available)'}"
        )

    location = f"\nUser's state: {state}" if state else ""

    prior = ""
    if history:
        turns = []
        for h in history[-4:]:          # last few turns is plenty of context
            turns.append(f"  User: {h.get('question','')}")
            answer = (h.get("answer") or "")[:600]
            if answer:
                turns.append(f"  You: {answer}")
        if turns:
            prior = (
                "EARLIER IN THIS CONVERSATION (for context - the new question "
                "may refer back to it):\n" + "\n".join(turns) + "\n\n"
            )

    attached = ""
    if document and document.get("text"):
        attached = (
            "ATTACHED DOCUMENT"
            f" ({document.get('filename') or 'uploaded file'}):\n"
            "-----\n"
            f"{document['text'][:DOCUMENT_CHARS]}\n"
            "-----\n\n"
        )

    prompt = (
        f"{prior}"
        f"{attached}"
        f"USER QUESTION: {question}{location}\n\n"
        f"RETRIEVED SOURCES FROM INDIAN KANOON:\n\n"
        + "\n\n".join(blocks)
        + "\n\nWrite the plain-language answer now, as JSON."
    )

    data = await generate_json(
        prompt,
        _SYNTHESIS_SYSTEM,
        temperature=0.25,
        # An attached document needs room to be explained properly.
        # Fuller answers need room. A document explanation needs more
        # again, since the document itself has to be worked through.
        max_output_tokens=4000 if document else 3000,
    )

    used = data.get("used_sources") or []
    if not isinstance(used, list):
        used = []
    return {
        "title": str(data.get("title") or "Here's what the law says").strip(),
        "body": str(data.get("body") or "").strip(),
        "used_sources": [int(n) for n in used if str(n).isdigit()],
        "next_steps": [str(s).strip() for s in (data.get("next_steps") or []) if str(s).strip()],
    }


# --------------------------------------------------------------------------
# Agent 4 - Internal Validator
# --------------------------------------------------------------------------

_VALIDATOR_SYSTEM = """You are the internal validator for a legal-information system. \
You are given a user's question, the source excerpts that were retrieved, and a draft \
answer written by another agent.

Check the draft for exactly these failures:
1. Any factual claim that is NOT supported by the source excerpts (hallucination).
2. Wording that crosses from legal information into legal advice or a prediction of \
   the outcome of the user's case.
3. An answer that does not actually address the question that was asked.

Be strict about invented facts but do not fail an answer merely for being general or \
cautious.

Telling the reader to consult a lawyer, an advocate, a legal aid clinic or the relevant \
authority is NOT legal advice and must never be flagged. It is the safest thing this \
system can say, and stripping it leaves the reader worse off. The same goes for \
naming the user's own state or city when suggesting where to get help - that is \
practical signposting, not a legal claim.

What rule 2 is actually for: predicting that the user will win or lose, telling them \
what to plead, or asserting that a provision applies to their facts when the sources \
do not establish it.

Return ONLY a JSON object:
{
  "grounded": true or false,
  "issues": ["short description of each problem found"],
  "revised_body": "if grounded is false, a corrected body that only states what the \
sources support; otherwise an empty string"
}"""


async def validate(question: str, draft: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    blocks = []
    for i, s in enumerate(sources, start=1):
        blocks.append(
            f"[{i}] {s.get('title') or 'Untitled'} ({s.get('court') or 'Unknown'})\n"
            f"    EXCERPT: {s.get('text') or s.get('snippet') or '(no text available)'}"
        )

    prompt = (
        f"USER QUESTION: {question}\n\n"
        f"SOURCE EXCERPTS:\n\n" + "\n\n".join(blocks) + "\n\n"
        f"DRAFT ANSWER:\nTitle: {draft.get('title')}\nBody: {draft.get('body')}\n"
        f"Next steps: {draft.get('next_steps')}\n\n"
        "Validate the draft now, as JSON."
    )

    # A revised body can be as long as the draft it replaces, so the default
    # 2048 is not enough headroom here.
    data = await generate_json(
        prompt, _VALIDATOR_SYSTEM, temperature=0.0, max_output_tokens=3000
    )
    return {
        "grounded": bool(data.get("grounded", True)),
        "issues": [str(i) for i in (data.get("issues") or [])],
        "revised_body": str(data.get("revised_body") or "").strip(),
    }

# --------------------------------------------------------------------------
# Agent 5 - Document scope classifier
# --------------------------------------------------------------------------
# Runs before anything is explained. Cheap, single-purpose, and deliberately
# permissive about *which kind* of legal document it is - the only question
# is whether Indian law is the right lens for reading it at all.

_SCOPE_SYSTEM = """You classify uploaded documents for an Indian legal-information \
platform. You are given the opening text of one document.

Decide whether it is legal material that an Indian lawyer or a person with a legal \
problem would bring to a legal service.

IN SCOPE - answer true:
- contracts, agreements, deeds, leases, rent agreements, MOUs, employment offers
- legal notices, show-cause notices, demand notices, replies to notices
- court documents: judgments, orders, decrees, plaints, written statements, \
affidavits, petitions, bail applications, vakalatnamas
- police documents: FIRs, charge sheets, complaints
- government or regulatory documents: RTI applications and replies, tax notices, \
municipal orders, licences, registration certificates
- bare acts, rules, ordinances, statutory instruments
- insurance policies, loan documents, property papers, wills, powers of attorney
- HR policy documents, terms of service, privacy policies, compliance material
- anything setting out rights, obligations, penalties or procedure

OUT OF SCOPE - answer false:
- academic or scientific material: physics, chemistry, biology, mathematics, \
engineering, computer science
- medical records, prescriptions, lab reports, diagnostic material
- literature, journalism, marketing copy, recipes, manuals, personal correspondence \
with no legal content
- financial statements or spreadsheets with no legal or regulatory framing
- anything where the subject matter is not law and no legal question arises

When a document is mixed, judge it by the question a reader would bring to it. A \
medical bill attached to an insurance claim is in scope; a lab report alone is not. \
When genuinely torn, answer true - a cautious answer is better than a wrong refusal.

Return ONLY a JSON object:
{
  "in_scope": true or false,
  "doc_kind": "short label, e.g. 'legal notice', 'rent agreement', 'physics notes'",
  "subject": "if out of scope, the subject area in 1-3 words, e.g. 'chemistry'; \
otherwise an empty string",
  "reason": "one short sentence"
}"""


async def classify_document(text: str, filename: str = "") -> Dict[str, Any]:
    """Is this document something a legal platform should read?

    Returns the parsed verdict. Raises GeminiError if the call fails, so the
    caller can fall back to its own heuristic rather than silently refusing.
    """
    name = f"FILENAME: {filename}\n\n" if filename else ""
    prompt = (
        f"{name}DOCUMENT TEXT (opening):\n-----\n{text}\n-----\n\n"
        "Classify this document now, as JSON."
    )

    data = await generate_json(
        prompt, _SCOPE_SYSTEM, temperature=0.0, max_output_tokens=400
    )

    return {
        "in_scope": bool(data.get("in_scope")),
        "doc_kind": str(data.get("doc_kind") or "").strip(),
        "subject": str(data.get("subject") or "").strip(),
        "reason": str(data.get("reason") or "").strip(),
    }