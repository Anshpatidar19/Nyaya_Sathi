"""Groq client - the FALLBACK synthesis provider.

Gemini is the primary model for everything in Nyaya Sathi. This module is
only ever reached from gemini.py, and only after Gemini has returned a 503 /
UNAVAILABLE twice in a row (the original call plus one backed-off retry).
Nothing else in the app should import it: routing a call here directly would
bypass the "Gemini first" rule the fallback is built around.

Groq speaks the OpenAI chat-completions dialect:

  POST https://api.groq.com/openai/v1/chat/completions
  Authorization: Bearer <GROQ_API_KEY>

The key travels in a header, never in the URL, so it cannot end up in an
access log or in httpx's request logging.

What this module deliberately does NOT do: retrieval, citation building, or
any shaping of the answer. It receives the exact prompt and system
instruction gemini.py built - retrieved statutes, judgments and all - and
returns raw text. Parsing, truncation repair and response shaping stay in
gemini.py, so both providers go through identical post-processing and the
frontend cannot tell which one wrote the answer.
"""

import json
import logging
import re
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx

from .config import settings

logger = logging.getLogger(__name__)

GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_TIMEOUT = 90.0

# Reasoning models (gpt-oss) count their hidden reasoning against
# max_completion_tokens - the same trap as Gemini's thinking budget. Give the
# reasoning its own room on top of what the caller asked for, so a 3,000-token
# answer is not cut short by 1,000 tokens of thought.
REASONING_HEADROOM = 1024

# Groq's JSON mode requires the word "json" somewhere in the messages. Every
# Nyaya Sathi system prompt already says it, but a prompt edit elsewhere
# should not be able to break the fallback, so it is stated once more here.
_JSON_ONLY = (
    "\n\nRespond with a single valid JSON object and nothing else - "
    "no markdown fences, no commentary before or after it."
)

# Some open models put their reasoning inline in <think> tags. Stripped so it
# can never be spliced into the JSON (the same bug the Gemini thought-part
# filter exists to prevent).
_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


class GroqError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=GROQ_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }


def _require_key() -> None:
    if not settings.groq_api_key:
        raise GroqError("GROQ_API_KEY is not set.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _is_reasoning_model(model: str) -> bool:
    return model.startswith("openai/gpt-oss")


def _payload(
    prompt: str,
    system_instruction: str,
    temperature: float,
    max_output_tokens: int,
    stream: bool,
    strict: bool,
) -> Dict[str, Any]:
    """Build the request body.

    strict=True asks for JSON mode and hides reasoning. strict=False is the
    plain retry used when Groq rejects either of those for the configured
    model - the prompt still demands JSON and gemini._parse_json tolerates
    fences and stray prose, so plain mode is a safe second attempt.
    """
    model = settings.groq_model
    reasoning = _is_reasoning_model(model)
    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction + _JSON_ONLY},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_completion_tokens": max_output_tokens + (REASONING_HEADROOM if reasoning else 0),
        "stream": stream,
    }
    if strict:
        body["response_format"] = {"type": "json_object"}
        if reasoning:
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
    return body


def _finish(reason: Optional[str]) -> str:
    """Map OpenAI finish reasons onto Gemini's, so gemini._parse_json's
    truncation handling (which keys on MAX_TOKENS) works unchanged."""
    if not reason:
        return "UNKNOWN"
    return {"length": "MAX_TOKENS", "stop": "STOP"}.get(reason, reason.upper())


def strip_reasoning(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _error_summary(resp: httpx.Response) -> str:
    """Status plus Groq's error type and message - never the raw body.

    A JSON-validation failure echoes the model's partial output back in
    `failed_generation`, which is legal content about the user's matter and
    has no business in a log line.
    """
    try:
        err = (resp.json() or {}).get("error") or {}
        kind = err.get("code") or err.get("type") or "error"
        msg = str(err.get("message") or "")[:200]
    except (ValueError, AttributeError):
        kind, msg = "error", ""
    return f"Groq API error {resp.status_code} ({kind}): {msg}"


async def _post(body: Dict[str, Any]) -> httpx.Response:
    try:
        return await _get_client().post(
            f"{GROQ_BASE}/chat/completions", json=body, headers=_headers()
        )
    except httpx.HTTPError as exc:
        raise GroqError(f"Groq request failed: {type(exc).__name__}") from exc


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

async def complete_json(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
) -> Tuple[str, str]:
    """One-shot completion. Returns (text, finish_reason) like gemini._generate_raw."""
    _require_key()

    resp = await _post(_payload(prompt, system_instruction, temperature,
                                max_output_tokens, stream=False, strict=True))
    if resp.status_code == 400:
        logger.warning("Groq rejected strict JSON mode (%s); retrying in plain mode",
                       _error_summary(resp))
        resp = await _post(_payload(prompt, system_instruction, temperature,
                                    max_output_tokens, stream=False, strict=False))
    if resp.status_code >= 400:
        raise GroqError(_error_summary(resp))

    try:
        data = resp.json()
        choice = data["choices"][0]
    except (ValueError, KeyError, IndexError) as exc:
        raise GroqError("Groq returned no choices.") from exc

    text = strip_reasoning((choice.get("message") or {}).get("content") or "")
    finish = _finish(choice.get("finish_reason"))

    usage = data.get("usage") or {}
    logger.info(
        "GROQ model=%s in=%s out=%s finish=%s",
        settings.groq_model,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        finish,
    )

    if not text:
        raise GroqError(f"Groq returned an empty response (finish={finish}).")
    return text, finish


async def stream_json(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
) -> AsyncIterator[Tuple[str, str]]:
    """Stream a completion. Yields ("text", chunk) ... then ("finish", reason).

    If Groq refuses to stream in JSON mode for the configured model (a 400 on
    open), this falls back to complete_json() and yields the whole text as a
    single chunk - still correct, just not incremental.
    """
    _require_key()

    finish = "UNKNOWN"
    fallback_to_complete = False
    body = _payload(prompt, system_instruction, temperature,
                    max_output_tokens, stream=True, strict=True)

    try:
        async with _get_client().stream(
            "POST", f"{GROQ_BASE}/chat/completions", json=body, headers=_headers()
        ) as resp:
            if resp.status_code == 400:
                await resp.aread()
                logger.warning("Groq refused to stream in JSON mode (%s); "
                               "using a single non-streamed completion",
                               _error_summary(resp))
                fallback_to_complete = True
            elif resp.status_code >= 400:
                await resp.aread()
                raise GroqError(_error_summary(resp))
            else:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        err = obj["error"] if isinstance(obj["error"], dict) else {}
                        raise GroqError(
                            f"Groq stream error ({err.get('code') or err.get('type') or 'error'})"
                        )
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield "text", piece
                    if choice.get("finish_reason"):
                        finish = _finish(choice["finish_reason"])
    except httpx.HTTPError as exc:
        raise GroqError(f"Groq stream failed: {type(exc).__name__}") from exc

    if fallback_to_complete:
        text, finish = await complete_json(
            prompt, system_instruction, temperature, max_output_tokens
        )
        yield "text", text

    logger.info("GROQ stream model=%s finish=%s", settings.groq_model, finish)
    yield "finish", finish