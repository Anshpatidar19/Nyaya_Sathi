"""Gemini client for the Synthesis Agent and the Internal Validator.

Uses the Generative Language REST API directly over httpx so there is no
extra SDK dependency:

  POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=<GEMINI_API_KEY>

Both agents ask for JSON back (responseMimeType=application/json) so the
output can be parsed straight into our schemas.
"""

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

GEMINI_TIMEOUT = 60.0
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Low-level call
# --------------------------------------------------------------------------

async def _generate(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
) -> str:
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set. Add it to backend/.env.")

    url = f"{GEMINI_BASE}/models/{settings.gemini_model}:generateContent"
    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }

    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
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

    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise GeminiError("Gemini returned an empty response.")
    return text


def _parse_json(raw: str) -> Dict[str, Any]:
    """Parse model output as JSON, tolerating ```json fences or stray prose."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the outermost {...} block.
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise GeminiError("Could not parse JSON from the Gemini response.")


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

Return ONLY a JSON object with this exact shape:
{
  "title": "a short direct answer, max 10 words, no trailing period",
  "body": "2-4 short paragraphs of plain-language explanation",
  "used_sources": [1, 2],
  "next_steps": ["concrete practical action", "another action"]
}"""


async def synthesize(question: str, state: Optional[str], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn retrieved Kanoon material into a plain-language answer."""
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
    prompt = (
        f"USER QUESTION: {question}{location}\n\n"
        f"RETRIEVED SOURCES FROM INDIAN KANOON:\n\n"
        + "\n\n".join(blocks)
        + "\n\nWrite the plain-language answer now, as JSON."
    )

    data = _parse_json(await _generate(prompt, _SYNTHESIS_SYSTEM, temperature=0.25))

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

    data = _parse_json(await _generate(prompt, _VALIDATOR_SYSTEM, temperature=0.0))
    return {
        "grounded": bool(data.get("grounded", True)),
        "issues": [str(i) for i in (data.get("issues") or [])],
        "revised_body": str(data.get("revised_body") or "").strip(),
    }