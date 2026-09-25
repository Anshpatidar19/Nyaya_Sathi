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

Fallback: if Gemini answers 503 / UNAVAILABLE ("high demand"), the call is
retried once after a short backoff. If the retry is also a 503, the same
prompt - retrieved sources included - is sent to Groq (groq_client.py). Every
other error is raised unchanged - except a quota error (429), which skips
the retry, goes straight to Groq, and puts Gemini on a short cooldown so the
calls that follow don't each spend a round trip rediscovering the limit.
The switch happens inside _generate_raw and
_stream_raw, so every caller (synthesis, validation, drafting, review,
arguments, translation) gets it without knowing it exists, and the output
goes through the same parsing and shaping whichever model wrote it.

The API key is sent as the x-goog-api-key header, not a ?key= query
parameter: httpx logs every request URL at INFO, which put the key in the
server log on every call.
"""

import asyncio
import base64
import json
import logging
import random
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from . import groq_client, usage_log
from .config import settings

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT = 90.0

# Streaming stall detection. A healthy stream sends its first chunk within a
# few seconds and then keeps coming; a stream that goes quiet has stalled
# (Gemini under load), and waiting it out only moves the failure later.
#
#   FIRST_CHUNK_TIMEOUT  - longest wait for the first chunk: prompt prefill
#                          plus the model's (minimal) thinking
#   CHUNK_GAP_TIMEOUT    - longest silence allowed once text is flowing
#
# Enforced by a watchdog in _gemini_stream_raw. A stall goes straight to
# Groq with no Gemini retry: a model that just stalled for 25s usually
# stalls again, and the retry was what turned one stall into three minutes.
# httpx's own read timeout stays as a looser backstop.
FIRST_CHUNK_TIMEOUT = 25.0
CHUNK_GAP_TIMEOUT = 20.0
GEMINI_STREAM_TIMEOUT = httpx.Timeout(GEMINI_TIMEOUT, read=60.0)
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
    """status_code is set only when the error came from an API response.
    retry_after is the wait Gemini asked for, when it said (429s do)."""

    def __init__(
        self,
        message: str = "",
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
        per_day: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.per_day = per_day


class GeminiTruncated(GeminiError):
    """The model hit the output cap before finishing. Retry with more room."""


class GeminiUnavailable(GeminiError):
    """Transient 503 / UNAVAILABLE - the model is overloaded right now.

    A subclass of GeminiError on purpose: every existing `except GeminiError`
    in the app still catches it, so if the fallback is off or fails, callers
    behave exactly as they did before this existed.
    """


class GeminiStalled(GeminiUnavailable):
    """Gemini accepted the request and then went silent (a read timeout or
    the stream watchdog). Handled like a 503, except it is NOT retried on
    Gemini - it goes straight to the fallback."""


class GeminiQuotaExceeded(GeminiError):
    """429 / RESOURCE_EXHAUSTED - our quota is spent, per minute or per day.

    Also a GeminiError subclass, for the same reason as GeminiUnavailable.
    """


# --------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------
# Two conditions switch to Groq, and they are handled differently:
#
#   503  Google's side is busy. Transient - retry Gemini once, then Groq.
#   429  OUR quota is spent. Retrying Gemini only spends another request
#        against a limit already hit, so: no retry, straight to Groq, and
#        skip Gemini for a cooldown.
#
# Everything else - 400, 401, 403, parse failures - is a bug or a config
# problem another provider should not paper over. Raised unchanged.

_UNAVAILABLE_MARKERS = (
    "unavailable",            # gRPC status UNAVAILABLE, "temporarily unavailable"
    "high demand",            # "This model is currently experiencing high demand"
    "overloaded",             # "The model is overloaded. Please try again later."
)


def _is_unavailable(status: Optional[int], text: str) -> bool:
    """The 503 rule, shared by the raise site and the helper.

    - No status  -> False. The error did not come from the API (a JSON parse
      failure, an empty response). Its message can quote the model's own
      output, which may well contain the word "unavailable".
    - 503        -> True.
    - other 5xx  -> True only if the body says UNAVAILABLE / high demand /
      overloaded.
    - 4xx        -> False, whatever the body says.
    """
    if status is None:
        return False
    if status == 503:
        return True
    if 500 <= status < 600:
        lowered = (text or "").lower()
        return any(m in lowered for m in _UNAVAILABLE_MARKERS)
    return False


def is_gemini_503_error(error: BaseException) -> bool:
    """True only for the transient "model is overloaded" condition."""
    if isinstance(error, GeminiUnavailable):
        return True
    if isinstance(error, GeminiQuotaExceeded):
        return False
    if isinstance(error, GeminiError):
        return _is_unavailable(error.status_code, str(error))
    if isinstance(error, httpx.HTTPStatusError):
        return _is_unavailable(error.response.status_code, error.response.text)
    return False


def is_gemini_quota_error(error: BaseException) -> bool:
    """True for a 429 from the Gemini API - and nothing else.

    Status code only, no text matching: a 429 is unambiguous, and the words
    "quota" or "exhausted" can turn up in a legal answer.
    """
    if isinstance(error, GeminiQuotaExceeded):
        return True
    if isinstance(error, GeminiError):
        return error.status_code == 429
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429
    return False


_RETRY_IN_RE = re.compile(r"retry in\s+([\d.]+)\s*s", re.IGNORECASE)


def _retry_hint(text: str) -> Tuple[Optional[float], bool]:
    """(seconds Gemini asked us to wait, whether a per-day quota was hit).

    Read from the full error body: RetryInfo.retryDelay ("37s") in the
    structured details, else "Please retry in 37.2s." in the message. The
    quota id names the window - GenerateRequestsPerDayPerProjectPerModel-...
    means waiting a minute will not help.
    """
    per_day = "perday" in (text or "").lower()
    delay: Optional[float] = None
    try:
        details = (json.loads(text).get("error") or {}).get("details") or []
        for d in details:
            raw = str(d.get("retryDelay") or "")
            if raw.endswith("s"):
                delay = float(raw[:-1])
                break
    except (ValueError, TypeError, AttributeError):
        pass
    if delay is None:
        m = _RETRY_IN_RE.search(text or "")
        if m:
            try:
                delay = float(m.group(1))
            except ValueError:
                pass
    return delay, per_day


def _network_error(exc: BaseException, where: str) -> GeminiUnavailable:
    """A timeout or dropped connection, reported as the 503 it effectively is.

    httpx raises its own exceptions (ReadTimeout, ConnectError,
    RemoteProtocolError...) which are not GeminiErrors, so they used to
    skip the retry and the Groq fallback entirely and fail the answer with
    "Unexpected streaming synthesis error". Wrapped here so every existing
    503 path handles them. A timeout is a stall (no Gemini retry); a
    dropped or refused connection is an ordinary 503 (one retry).
    """
    cls = GeminiStalled if isinstance(exc, httpx.TimeoutException) else GeminiUnavailable
    return cls(
        f"Gemini API error 503: {type(exc).__name__} on {where} "
        f"(no response from the model - treated as unavailable)",
        status_code=503,
    )


def _thinking_config(model_name: str, budget: Optional[int]) -> Dict[str, Any]:
    """Thinking control in the form this model actually understands.

    Gemini 3.x replaced the numeric thinkingBudget with thinkingLevel. The
    old field is still accepted for backward compatibility, but Gemini 3
    Flash-Lite cannot switch thinking fully off, so a budget of 0 is not a
    reliable "don't think" - and an unset level can default to HIGH, which
    is seconds of silent reasoning before the first streamed word. Sending
    the level explicitly makes the latency predictable. 2.x models keep the
    budget, which they do honour.
    """
    if (model_name or "").lower().startswith("gemini-3"):
        if budget is None or budget <= 0:
            level = "minimal"
        elif budget <= 2048:
            level = "low"
        elif budget <= 8192:
            level = "medium"
        else:
            level = "high"
        return {"thinkingLevel": level}
    return {"thinkingBudget": budget}


def _api_error(status: int, detail: Any) -> GeminiError:
    """Build the right exception for a failed API response."""
    text = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
    message = f"Gemini API error {status}: {text[:400]}"
    if status == 429:
        delay, per_day = _retry_hint(text)      # full body, before truncation
        return GeminiQuotaExceeded(message, status_code=status,
                                   retry_after=delay, per_day=per_day)
    cls = GeminiUnavailable if _is_unavailable(status, text) else GeminiError
    return cls(message, status_code=status)


# --------------------------------------------------------------------------
# Quota cooldown
# --------------------------------------------------------------------------
# After a 429, text calls skip Gemini and go straight to Groq until the
# cooldown ends. Without it, a spent daily quota would make every call try
# Gemini first - and an answer is several calls (synthesis, validator,
# sometimes a revision) - just to be told no each time.
#
# Process-wide and in-memory on purpose: a restart clears it, and Gemini is
# simply tried again. When it expires the next call goes to Gemini first, so
# Gemini returns as primary on its own the moment the quota is back.

QUOTA_COOLDOWN_DEFAULT = 60.0      # when Gemini does not say how long
QUOTA_COOLDOWN_MIN = 10.0

_quota_cooldown_until = 0.0        # time.monotonic() deadline


def _quota_cooldown_remaining() -> float:
    return max(0.0, _quota_cooldown_until - time.monotonic())


def _start_quota_cooldown(exc: GeminiError) -> float:
    global _quota_cooldown_until
    cap = max(settings.gemini_quota_cooldown_max, QUOTA_COOLDOWN_MIN)
    if exc.per_day:
        seconds = cap           # a daily limit will not reset in a minute
    else:
        seconds = min(max(exc.retry_after or QUOTA_COOLDOWN_DEFAULT,
                          QUOTA_COOLDOWN_MIN), cap)
    _quota_cooldown_until = time.monotonic() + seconds
    return seconds


def _clear_quota_cooldown() -> None:
    """A Gemini call just succeeded, so the quota is evidently back."""
    global _quota_cooldown_until
    if _quota_cooldown_until:
        logger.info("Gemini quota available again - cooldown cleared")
    _quota_cooldown_until = 0.0


# --------------------------------------------------------------------------
# Fallback switches, backoff, dev simulation
# --------------------------------------------------------------------------

def _fallback_ready() -> bool:
    """Master switch - covers both the 503 and the 429 fallback."""
    return bool(settings.llm_fallback_enabled and settings.groq_api_key)


def _quota_fallback_ready() -> bool:
    return _fallback_ready() and bool(settings.llm_quota_fallback_enabled)


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter. Only attempt 0 is ever used today
    (one retry), but the shape is right if the retry count is ever raised."""
    base = max(settings.gemini_retry_base_delay, 0.0) * (2 ** attempt)
    return base + random.uniform(0, base / 2)


def _simulate_mode() -> str:
    return (settings.gemini_simulate_503 or "").strip().lower()


def _simulated_failure(attempt: int) -> Optional[GeminiError]:
    """Dev-only switch (GEMINI_SIMULATE_503). See config.py.

    Returns the fake error to raise, or None. Checked before any request is
    sent, so a simulated failure costs no quota.
    """
    mode = _simulate_mode()
    if mode == "quota":
        logger.warning("SIMULATED Gemini 429 (GEMINI_SIMULATE_503=quota)")
        return GeminiQuotaExceeded(
            "Gemini API error 429: simulated - You exceeded your current quota.",
            status_code=429, retry_after=30.0,
        )
    if mode in ("always", "midstream") or (mode == "once" and attempt == 0):
        logger.warning("SIMULATED Gemini 503 (GEMINI_SIMULATE_503=%s, attempt=%d)",
                       mode, attempt + 1)
        return GeminiUnavailable(
            "Gemini API error 503: simulated - This model is currently "
            "experiencing high demand.",
            status_code=503,
        )
    return None


# --------------------------------------------------------------------------
# Low-level call
# --------------------------------------------------------------------------

# One client for the process. A fresh AsyncClient per call means a new TLS
# handshake to generativelanguage.googleapis.com every time - paid twice per
# answer, since synthesis and validation are separate calls.
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=GEMINI_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _client


async def close_client() -> None:
    """Called from the FastAPI shutdown hook. Closes the Groq client too, so
    main.py does not need to know the fallback exists."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    await groq_client.close_client()


def _auth_headers() -> Dict[str, str]:
    # Header, not ?key= - see the module docstring.
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": settings.gemini_api_key,
    }


def _extract_text(candidate: Dict[str, Any], strip: bool = True) -> str:
    """Join the answer parts, skipping the model's own reasoning.

    Thinking models return their scratchpad as parts flagged {"thought": true}.
    Those are prose, not JSON, and concatenating them corrupts the payload.

    strip=False for streaming. A stream calls this once per chunk, and
    stripping each chunk deletes any space that happens to fall on a chunk
    boundary - which reads as words run together ("mustfirst", "Ifyou") in
    the finished answer, since the accumulated buffer is what gets parsed.
    Stripping the whole response once at the end is correct; stripping every
    fragment of it is not.
    """
    parts = candidate.get("content", {}).get("parts", []) or []
    joined = "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and not p.get("thought")
    )
    return joined.strip() if strip else joined


def _parts(prompt: str, media: Optional[List[Tuple[str, bytes]]] = None) -> List[Dict[str, Any]]:
    """Request parts: inline files first, then the instruction text.

    Files go before the text on purpose - Gemini's guidance for document
    and image input is that the question should follow the material.
    """
    parts: List[Dict[str, Any]] = []
    for mime, blob in media or []:
        parts.append({
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(blob).decode("ascii"),
            }
        })
    parts.append({"text": prompt})
    return parts


async def _gemini_generate_raw(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
    media: Optional[List[Tuple[str, bytes]]] = None,
    model: Optional[str] = None,
    attempt: int = 0,
) -> Tuple[str, str]:
    """One Gemini call, no fallback. Returns (text, finish_reason).

    `media` is a list of (mime_type, bytes) sent inline before the prompt -
    images and PDFs, for OCR. `model` overrides settings.gemini_model for
    this one call (document reading can use a stronger vision model than
    the text pipeline without touching anything else).
    """
    model_name = model or settings.gemini_model
    started = time.perf_counter()
    usage: Dict[str, Any] = {}
    try:
        text, finish_reason, usage = await _gemini_generate_once(
            prompt, system_instruction, temperature, max_output_tokens,
            thinking_budget, media, model_name, attempt,
        )
    except GeminiError as exc:
        # A failed call is logged too: a 503 that was retried and a 429 that
        # went to Groq are part of what a query cost in time, even when
        # Google bills nothing for them.
        inp, out, tot = usage_log.gemini_tokens(getattr(exc, "usage", None))
        usage_log.record(
            model=model_name, input_tokens=inp, output_tokens=out, total_tokens=tot,
            latency_ms=(time.perf_counter() - started) * 1000,
            status="error", error=str(exc),
        )
        raise
    inp, out, tot = usage_log.gemini_tokens(usage)
    usage_log.record(
        model=model_name, input_tokens=inp, output_tokens=out, total_tokens=tot,
        latency_ms=(time.perf_counter() - started) * 1000, status="success",
    )
    return text, finish_reason


async def _gemini_generate_once(
    prompt: str,
    system_instruction: str,
    temperature: float,
    max_output_tokens: int,
    thinking_budget: Optional[int],
    media: Optional[List[Tuple[str, bytes]]],
    model_name: str,
    attempt: int,
) -> Tuple[str, str, Dict[str, Any]]:
    """The HTTP half of _gemini_generate_raw. Returns (text, finish, usage).

    Split out so the caller can time and cost every outcome in one place.
    An empty response still consumed tokens, so its GeminiError carries the
    usage block as `.usage` for the cost line.
    """
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set. Add it to backend/.env.")

    fake = _simulated_failure(attempt)
    if fake:
        raise fake

    url = f"{GEMINI_BASE}/models/{model_name}:generateContent"

    generation_config: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = _thinking_config(model_name, thinking_budget)

    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": _parts(prompt, media)}],
        "generationConfig": generation_config,
    }

    client = _get_client()
    try:
        resp = await client.post(url, json=payload, headers=_auth_headers())

        # Not every model in the family accepts thinkingConfig. If that is
        # what it objected to, drop the field and go again rather than
        # failing the whole request over a knob.
        if resp.status_code == 400 and "thinkingConfig" in generation_config:
            if "thinking" in resp.text.lower():
                logger.warning(
                    "Model %s rejected thinkingConfig; retrying without it.",
                    model_name,
                )
                generation_config.pop("thinkingConfig")
                resp = await client.post(url, json=payload, headers=_auth_headers())
    except httpx.TransportError as exc:
        # TransportError covers every timeout (ReadTimeout, ConnectTimeout,
        # ...) plus dropped and refused connections.
        raise _network_error(exc, "request") from exc

    if resp.status_code >= 400:
        raise _api_error(resp.status_code, resp.text)
    data = resp.json()

    try:
        candidate = data["candidates"][0]
    except (KeyError, IndexError):
        blocked = data.get("promptFeedback", {}).get("blockReason")
        raise GeminiError(f"Gemini returned no candidates (blockReason={blocked}).")

    finish_reason = str(candidate.get("finishReason") or "UNKNOWN")
    text = _extract_text(candidate)

    # thoughtsTokenCount is the question this logging exists to answer: a
    # thinkingBudget of 0 is accepted by the API but not always honoured, and
    # thinking tokens are generated before any answer appears. If this is
    # non-zero, most of the wait is reasoning the user never sees.
    usage = data.get("usageMetadata") or {}
    logger.info(
        "GEMINI in=%s out=%s thoughts=%s finish=%s",
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        usage.get("thoughtsTokenCount", 0),
        finish_reason,
    )

    if not text:
        usage = data.get("usageMetadata", {})
        empty = GeminiError(
            "Gemini returned an empty response "
            f"(finishReason={finish_reason}, usage={usage}). "
            "If finishReason is MAX_TOKENS the budget was spent on thinking "
            "before any answer was produced - raise max_output_tokens or lower "
            "thinking_budget."
        )
        empty.usage = usage   # billed even though unusable - see the caller
        raise empty

    return text, finish_reason, usage


async def _groq_complete(
    call: Dict[str, Any],
    gemini_exc: GeminiError,
) -> Tuple[str, str]:
    """Groq as the fallback. If Groq fails too, the Gemini error is what gets
    raised, so callers see the same failure they always did."""
    try:
        text, finish = await groq_client.complete_json(
            call["prompt"], call["system_instruction"],
            temperature=call["temperature"],
            max_output_tokens=call["max_output_tokens"],
        )
    except groq_client.GroqError as gexc:
        logger.error("Groq fallback failed: %s", gexc)
        raise gemini_exc from gexc
    logger.info("Groq fallback succeeded")
    return text, finish


def _on_quota_error(exc: GeminiError, media) -> None:
    """Decide what a 429 does. Returns only if Groq should take the call;
    raises the 429 unchanged otherwise."""
    if media:
        # Groq cannot read images or PDFs, so nothing can serve this call.
        # No cooldown is started from here: text calls will find out about
        # the quota on their own next call and fall back then.
        logger.warning("Gemini quota exceeded (429) on a document/image call - "
                       "no text-only fallback possible")
        raise exc
    if not _quota_fallback_ready():
        raise exc
    seconds = _start_quota_cooldown(exc)
    logger.warning("Gemini quota exceeded (429%s) - switching to Groq fallback; "
                   "skipping Gemini for %.0fs",
                   ", daily limit" if exc.per_day else "", seconds)


async def _generate_raw(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
    media: Optional[List[Tuple[str, bytes]]] = None,
    model: Optional[str] = None,
) -> Tuple[str, str]:
    """Gemini first; Groq only on 503 (after one retry) or 429 (no retry).

    Same signature and return value as before the fallback existed; every
    caller in the app goes through here.
    """
    call = dict(
        prompt=prompt,
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_budget=thinking_budget,
        media=media,
        model=model,
    )

    # Quota cooldown: skip Gemini entirely for text calls. Image/PDF calls
    # still try Gemini - nothing else can serve them, and if the quota has
    # come back, their success clears the cooldown for everyone.
    remaining = _quota_cooldown_remaining()
    if remaining and not media and _quota_fallback_ready():
        logger.info("Gemini quota cooldown active (%.0fs left) - using Groq", remaining)
        return await _groq_complete(call, GeminiQuotaExceeded(
            "Gemini API error 429: quota cooldown active", status_code=429))

    unavailable: Optional[GeminiError] = None
    for attempt in (0, 1):
        if attempt == 0:
            logger.info("Gemini request started (model=%s)", model or settings.gemini_model)
        else:
            await asyncio.sleep(_backoff(0))
        try:
            result = await _gemini_generate_raw(**call, attempt=attempt)
            logger.info("Gemini %s succeeded", "retry" if attempt else "request")
            _clear_quota_cooldown()
            return result
        except GeminiError as exc:
            if is_gemini_quota_error(exc):
                _on_quota_error(exc, media)          # raises unless Groq should answer
                return await _groq_complete(call, exc)
            if not is_gemini_503_error(exc):
                raise
            unavailable = exc
            if isinstance(exc, GeminiStalled):
                logger.warning("Gemini stalled (%s) - skipping retry", exc)
                break
            if attempt == 0:
                logger.warning("Gemini returned 503 - retrying")

    assert unavailable is not None
    if media:
        # Groq's text models cannot read inline PDFs or images, so an OCR
        # call has nowhere to go. Fail it the way it always failed.
        logger.warning("Gemini unavailable on a document/image call - "
                       "no text-only fallback possible")
        raise unavailable
    if not _fallback_ready():
        logger.warning("Gemini unavailable - Groq fallback is disabled")
        raise unavailable

    logger.warning("Gemini unavailable - switching to Groq fallback (model=%s)",
                   settings.groq_model)
    return await _groq_complete(call, unavailable)


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
    media: Optional[List[Tuple[str, bytes]]] = None,
    model: Optional[str] = None,
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
        media=media,
        model=model,
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
            media=media,
            model=model,
        )
        return _parse_json(text, finish_reason)


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------
# The answer is JSON, but the user only ever reads one field of it. Rather
# than waiting for the whole object to close, we pull the "body" string out
# of the partial JSON as it arrives and emit the new characters. Everything
# else - title, used_sources, next_steps - is parsed normally once the
# stream finishes, which is also when it is actually needed.

_BODY_KEY_RE = re.compile(r'"body"\s*:\s*"')

_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b",
    "f": "\f", '"': '"', "\\": "\\", "/": "/",
}


def _partial_body(raw: str) -> str:
    """Decode as much of the "body" string as has arrived.

    Recomputed from the whole buffer on each chunk rather than kept as
    incremental state: a chunk can split a unicode escape down the middle,
    and re-deriving is simpler than resuming mid-escape.
    """
    m = _BODY_KEY_RE.search(raw)
    if not m:
        return ""

    out: List[str] = []
    i = m.end()
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= n:
                break                      # escape split across chunks
            nxt = raw[i + 1]
            if nxt == "u":
                if i + 5 >= n + 1 or i + 6 > n:
                    break
                try:
                    out.append(chr(int(raw[i + 2:i + 6], 16)))
                except ValueError:
                    pass
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break                          # body closed
        out.append(ch)
        i += 1
    return "".join(out)


async def _gemini_stream_raw(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
    attempt: int = 0,
) -> AsyncIterator[Tuple[str, Any]]:
    """One Gemini stream, no fallback, with its cost line.

    The LLM_CALL line is written when the stream ends however it ends -
    finished, failed (503, stall, 429) or abandoned by a reader who closed
    the tab - with whatever usage Gemini had reported by then.
    """
    started = time.perf_counter()
    meta: Dict[str, Any] = {"usage": {}}
    status, error = "success", None
    try:
        async for item in _gemini_stream_once(
            prompt, system_instruction, temperature, max_output_tokens,
            thinking_budget, attempt, meta,
        ):
            yield item
    except GeminiError as exc:
        status, error = "error", str(exc)
        raise
    except (GeneratorExit, asyncio.CancelledError):
        status, error = "cancelled", "stream closed before it finished"
        raise
    except Exception as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        inp, out, tot = usage_log.gemini_tokens(meta.get("usage"))
        usage_log.record(
            model=settings.gemini_model, input_tokens=inp, output_tokens=out,
            total_tokens=tot, latency_ms=(time.perf_counter() - started) * 1000,
            status=status, error=error,
        )


async def _gemini_stream_once(
    prompt: str,
    system_instruction: str,
    temperature: float,
    max_output_tokens: int,
    thinking_budget: Optional[int],
    attempt: int,
    meta: Dict[str, Any],
) -> AsyncIterator[Tuple[str, Any]]:
    """One Gemini stream, no fallback.

    Yields ("delta", new_text) as the body arrives, then ("raw", full_json).
    The caller parses the final payload with the same _parse_json used by the
    non-streaming path, so truncation repair and finishReason reporting behave
    identically.
    """
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set. Add it to backend/.env.")

    midstream = _simulate_mode() == "midstream"
    if not midstream:
        fake = _simulated_failure(attempt)
        if fake:
            raise fake

    url = f"{GEMINI_BASE}/models/{settings.gemini_model}:streamGenerateContent"

    generation_config: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = _thinking_config(
            settings.gemini_model, thinking_budget
        )

    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    buf = ""            # raw JSON accumulated so far
    emitted = 0         # characters of body already sent
    finish_reason = "UNKNOWN"
    usage: Dict[str, Any] = {}
    started = time.perf_counter()
    first_chunk_at: Optional[float] = None

    client = _get_client()

    def _open():
        return client.stream(
            "POST",
            url,
            params={"alt": "sse"},
            json=payload,
            headers=_auth_headers(),
            timeout=GEMINI_STREAM_TIMEOUT,
        )

    try:
        for try_without_thinking in (False, True):
            async with _open() as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    # Same courtesy as the non-streaming call: a model that
                    # rejects the thinking field gets the request again
                    # without it, instead of failing the answer over a knob.
                    if (
                        resp.status_code == 400
                        and not try_without_thinking
                        and "thinkingConfig" in generation_config
                        and b"thinking" in body.lower()
                    ):
                        logger.warning(
                            "Model %s rejected %s; streaming again without it.",
                            settings.gemini_model, generation_config["thinkingConfig"],
                        )
                        generation_config.pop("thinkingConfig")
                        continue
                    raise _api_error(resp.status_code, body)

                lines = resp.aiter_lines()
                while True:
                    # The watchdog. Waiting on one line at a time with a
                    # deadline is what turns "Gemini went quiet" into a
                    # GeminiStalled in 20-25s instead of a raw ReadTimeout
                    # after 90.
                    deadline = FIRST_CHUNK_TIMEOUT if first_chunk_at is None else CHUNK_GAP_TIMEOUT
                    try:
                        line = await asyncio.wait_for(lines.__anext__(), timeout=deadline)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        phase = (
                            f"stream: no first chunk in {FIRST_CHUNK_TIMEOUT:.0f}s"
                            if first_chunk_at is None
                            else f"stream: silent {CHUNK_GAP_TIMEOUT:.0f}s after {emitted} body chars"
                        )
                        raise GeminiStalled(
                            f"Gemini API error 503: stalled ({phase})", status_code=503
                        )

                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if first_chunk_at is None:
                        first_chunk_at = time.perf_counter()

                    # An overload can also arrive AFTER the 200, as an error
                    # object inside the stream. Raised with its real status.
                    if isinstance(obj, dict) and obj.get("error"):
                        err = obj["error"] if isinstance(obj["error"], dict) else {}
                        raise _api_error(int(err.get("code") or 500), json.dumps(err))

                    if isinstance(obj, dict) and obj.get("usageMetadata"):
                        usage = obj["usageMetadata"]
                        meta["usage"] = usage

                    try:
                        candidate = obj["candidates"][0]
                    except (KeyError, IndexError):
                        continue

                    finish_reason = str(candidate.get("finishReason") or finish_reason)
                    buf += _extract_text(candidate, strip=False)

                    body_so_far = _partial_body(buf)
                    if len(body_so_far) > emitted:
                        yield "delta", body_so_far[emitted:]
                        emitted = len(body_so_far)

                    if midstream and emitted > 0:
                        fake = _simulated_failure(attempt)
                        if fake:
                            raise fake
            break
    except httpx.TransportError as exc:
        # Backstop for anything the watchdog did not catch: a dropped
        # connection, or httpx's own read timeout.
        where = "stream (after %d body chars)" % emitted if emitted else "stream (before any output)"
        raise _network_error(exc, where) from exc

    # First-chunk time and thought tokens are the two numbers that explain a
    # slow answer: a high first-chunk time with thoughts > 0 means the model
    # is reasoning silently before it writes.
    logger.info(
        "GEMINI stream first_chunk=%.2fs total=%.2fs in=%s out=%s thoughts=%s",
        (first_chunk_at - started) if first_chunk_at else -1.0,
        time.perf_counter() - started,
        usage.get("promptTokenCount"),
        usage.get("candidatesTokenCount"),
        usage.get("thoughtsTokenCount", 0),
    )
    logger.info("GEMINI stream chars=%d finish=%s", len(buf), finish_reason)
    yield "raw", (buf.strip(), finish_reason)


async def _groq_stream_raw(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
) -> AsyncIterator[Tuple[str, Any]]:
    """Groq's stream in the exact shape of _gemini_stream_raw: the same
    ("delta", ...) pieces of the body, then ("raw", (json, finish_reason))."""
    buf = ""
    emitted = 0
    finish_reason = "UNKNOWN"

    async for kind, value in groq_client.stream_json(
        prompt, system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    ):
        if kind == "finish":
            finish_reason = value
            continue
        buf += value
        body_so_far = _partial_body(buf)
        if len(body_so_far) > emitted:
            yield "delta", body_so_far[emitted:]
            emitted = len(body_so_far)

    yield "raw", (groq_client.strip_reasoning(buf), finish_reason)


async def _stream_raw(
    prompt: str,
    system_instruction: str,
    temperature: float = 0.2,
    max_output_tokens: int = 2048,
    thinking_budget: Optional[int] = DEFAULT_THINKING_BUDGET,
) -> AsyncIterator[Tuple[str, Any]]:
    """Gemini stream first; Groq only on 503 (after one retry) or 429.

    Yields ("delta", text) and finally ("raw", (json, finish_reason)), as
    before - plus, in one rare case, ("reset", None).

    The problem a stream adds: if a failure arrives after some of the body
    has already gone to the browser, whatever answers next starts from the
    beginning, and streaming its deltas would print the opening twice. So:

    - failure before any text went out: the next attempt streams normally.
      The reader never knows anything happened.
    - failure after text went out: yield ("reset", None) once, then run the
      next attempt silently (deltas swallowed). The caller replaces the
      partial body with the finished one when "raw" arrives.
    """
    call = dict(
        prompt=prompt,
        system_instruction=system_instruction,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )

    streamed = False        # any body text reached the caller
    silent = False          # "reset" sent; swallow deltas from now on
    failure: Optional[GeminiError] = None

    remaining = _quota_cooldown_remaining()
    if remaining and _quota_fallback_ready():
        logger.info("Gemini quota cooldown active (%.0fs left) - using Groq", remaining)
        failure = GeminiQuotaExceeded("Gemini API error 429: quota cooldown active",
                                      status_code=429)
    else:
        for attempt in (0, 1):
            if attempt == 0:
                logger.info("Gemini request started (stream, model=%s)", settings.gemini_model)
            else:
                await asyncio.sleep(_backoff(0))
            try:
                async for kind, value in _gemini_stream_raw(
                    **call, thinking_budget=thinking_budget, attempt=attempt
                ):
                    if kind == "delta":
                        if silent:
                            continue
                        streamed = True
                    yield kind, value
                logger.info("Gemini %s succeeded", "retry" if attempt else "request")
                _clear_quota_cooldown()
                return
            except GeminiError as exc:
                quota = is_gemini_quota_error(exc)
                if quota:
                    _on_quota_error(exc, None)       # raises unless Groq should answer
                elif not is_gemini_503_error(exc):
                    raise
                failure = exc
                if streamed and not silent:
                    silent = True
                    yield "reset", None
                if quota:
                    break                            # no Gemini retry on a 429
                if isinstance(exc, GeminiStalled):
                    # A stall is not retried - see FIRST_CHUNK_TIMEOUT.
                    logger.warning("Gemini stalled%s (%s) - skipping retry",
                                   " mid-stream" if streamed else "", exc)
                    if not _fallback_ready():
                        logger.warning("Groq fallback is disabled")
                        raise
                    break
                if attempt == 0:
                    logger.warning("Gemini unavailable%s (%s) - retrying",
                                   " mid-stream" if streamed else "", exc)
        else:
            # Both attempts were 503s (the loop did not break on a 429).
            if not _fallback_ready():
                logger.warning("Gemini retry returned 503 - Groq fallback is disabled")
                raise failure
            logger.warning("Gemini retry returned 503 - switching to Groq fallback (model=%s)",
                           settings.groq_model)

    assert failure is not None
    if isinstance(failure, GeminiStalled):
        logger.warning("Switching to Groq fallback after a stall (model=%s)", settings.groq_model)
    try:
        async for kind, value in _groq_stream_raw(**call):
            if kind == "delta" and silent:
                continue
            yield kind, value
    except groq_client.GroqError as gexc:
        logger.error("Groq fallback failed: %s", gexc)
        raise failure from gexc
    logger.info("Groq fallback succeeded")


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

- If a MATTER CONTEXT is supplied, the question comes from the advocate handling \
that case, inside its case file. Answer for that matter: apply the retrieved law to \
its facts, court and the side the advocate appears for, wherever the sources support \
it. The matter context is facts about the case, never a source of law - do not cite \
it, and do not state a legal rule because the case file mentions it. Keep act names \
and section numbers exactly as the sources give them.

Return ONLY a JSON object with this exact shape:
{
  "title": "a short direct answer, max 10 words, no trailing period",
  "body": "4-7 paragraphs of plain-language explanation, separated by blank lines",
  "used_sources": [1, 2],
  "next_steps": ["concrete practical action", "another action"]
}"""


def _matter_block(matter: Optional[Dict[str, Any]]) -> str:
    """The case file, as the model sees it. Empty outside a matter."""
    if not matter or not matter.get("brief"):
        return ""
    return (
        "MATTER CONTEXT (the advocate's own case file - facts about the case, "
        "not law):\n-----\n"
        f"{matter['brief']}\n"
        "-----\n\n"
    )


def _synthesis_prompt(
    question: str,
    state: Optional[str],
    sources: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
    matter: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the synthesis prompt.

    Shared by the buffered and streaming paths so the two can never drift
    apart - a prompt change that only landed in one of them would give the
    same question two different answers depending on the endpoint.
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

    return (
        f"{_matter_block(matter)}"
        f"{prior}"
        f"{attached}"
        f"USER QUESTION: {question}{location}\n\n"
        f"RETRIEVED SOURCES FROM INDIAN KANOON:\n\n"
        + "\n\n".join(blocks)
        + "\n\nWrite the plain-language answer now, as JSON."
    )


# An attached document needs room to be explained properly, since the document
# itself has to be worked through on top of the law.
def _synthesis_tokens(document: Optional[Dict[str, str]]) -> int:
    return 4000 if document else 3000


def _shape_draft(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise the model's JSON into the shape the pipeline expects."""
    used = data.get("used_sources") or []
    if not isinstance(used, list):
        used = []
    return {
        "title": str(data.get("title") or "Here's what the law says").strip(),
        "body": str(data.get("body") or "").strip(),
        "used_sources": [int(n) for n in used if str(n).isdigit()],
        "next_steps": [str(s).strip() for s in (data.get("next_steps") or []) if str(s).strip()],
    }


async def synthesize(
    question: str,
    state: Optional[str],
    sources: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
    matter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Turn retrieved material into a plain-language answer."""
    data = await generate_json(
        _synthesis_prompt(question, state, sources, history, document, matter),
        _SYNTHESIS_SYSTEM,
        temperature=0.25,
        max_output_tokens=_synthesis_tokens(document),
    )
    return _shape_draft(data)


async def synthesize_stream(
    question: str,
    state: Optional[str],
    sources: List[Dict[str, Any]],
    history: Optional[List[Dict[str, str]]] = None,
    document: Optional[Dict[str, str]] = None,
    matter: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[Tuple[str, Any]]:
    """Same answer as synthesize(), delivered as it is written.

    Yields ("delta", text) for each new run of body characters, then
    ("draft", dict) with the fully parsed answer. The draft is authoritative:
    the deltas are the same characters, but the caller should use the parsed
    body for anything it stores.

    If the provider failed part-way through and a retry or the fallback
    finished the answer, ("replace", body) comes just before "draft": the
    deltas already sent are a fragment of an abandoned attempt and the
    caller must swap them for this body.
    """
    raw = ""
    finish_reason = "UNKNOWN"
    reset = False

    async for kind, value in _stream_raw(
        _synthesis_prompt(question, state, sources, history, document, matter),
        _SYNTHESIS_SYSTEM,
        temperature=0.25,
        max_output_tokens=_synthesis_tokens(document),
    ):
        if kind == "delta":
            yield "delta", value
        elif kind == "reset":
            reset = True
        else:
            raw, finish_reason = value

    draft = _shape_draft(_parse_json(raw, finish_reason))
    if reset:
        yield "replace", draft["body"]
    yield "draft", draft


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

The NEXT STEPS are practical directions, not legal claims, and the source excerpts \
are not expected to support them. Naming the forum, commission, tribunal, court, \
police station, portal or office where a person goes to file - and saying to gather \
documents, keep copies or check a deadline - is signposting. Never flag a next step \
merely because the excerpts do not mention it. Apply rules 1 and 2 to the BODY.

The user's own state is supplied to you below when it is known. It is a fact about \
the user, not something the draft invented. Never flag the draft for naming it.

A MATTER CONTEXT may also be supplied: the advocate's own case file. Its facts - the \
parties, the court, the side, what happened - are supplied facts, not inventions. \
Never flag the draft for restating or applying them. Rule 1 still applies to any \
statement of LAW, which must come from the source excerpts.

What rule 2 is actually for: predicting that the user will win or lose, telling them \
what to plead, or asserting that a provision applies to their facts when the sources \
do not establish it.

Return ONLY a JSON object:
{
  "grounded": true or false,
  "issues": ["short description of each problem found"]
}"""


# Asked only when the check above fails - which is rare. Keeping the rewrite
# in its own call means the common path never pays for the tokens.
_REVISION_SYSTEM = """You are correcting a draft answer for a legal-information \
system. You are given the user's question, the source excerpts, the draft, and the \
problems found with it.

Rewrite the body so it states only what the sources support. Keep everything that \
was correct, keep the plain-language style, and keep any pointer to a lawyer or \
legal aid. Remove or qualify only what the issues identify.

Return ONLY a JSON object:
{
  "revised_body": "the corrected body"
}"""


# How much of each source the validator sees.
#
# This needs care, and getting it wrong is expensive in a way that is not
# obvious: too short and the validator flags claims the source DOES support,
# which fails a correct answer, triggers the revision pass, and replaces good
# prose with a hedge. That costs a second 3,000-token call AND quality. So the
# budget is per source KIND, not one number for everything.
#
# Statutes and overviews: effectively untruncated. A bare-act section IS the
# ground truth being checked against, and the long ones are long for a reason
# - BNS 101 runs 6,400 characters because the five exceptions to murder are
# part of the section. Cutting it at 1,200 deletes every exception, and the
# validator then correctly reports that "grave and sudden provocation" appears
# in no excerpt. 18% of the corpus is over 1,200 characters; the cap below
# clears all but a handful of outliers.
#
# Judgments: these are the genuinely bloated sources, and unlike a section a
# judgment fragment is a ranked list of matched passages, so the tail is its
# weakest material. A judgment is also supporting material - it shows how a
# rule was applied, it is not the rule - so a shorter excerpt is enough to
# confirm a claim came from somewhere real.
VALIDATOR_STATUTE_CHARS = 8000
VALIDATOR_JUDGMENT_CHARS = 1200
VALIDATOR_UNUSED_CHARS = 500

_STATUTE_KINDS = ("section", "overview")


def _validator_cap(source: Dict[str, Any], index: int, used: set) -> int:
    if source.get("kind") in _STATUTE_KINDS:
        return VALIDATOR_STATUTE_CHARS
    if not used or index in used:
        return VALIDATOR_JUDGMENT_CHARS
    return VALIDATOR_UNUSED_CHARS


async def validate(
    question: str,
    draft: Dict[str, Any],
    sources: List[Dict[str, Any]],
    state: Optional[str] = None,
    matter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Index order is preserved on purpose: the numbers the draft cites in
    # used_sources have to keep pointing at the same sources here.
    used = set(draft.get("used_sources") or [])
    blocks = []
    for i, s in enumerate(sources, start=1):
        excerpt = (s.get("text") or s.get("snippet") or "(no text available)")
        cap = _validator_cap(s, i, used)
        clipped = excerpt[:cap]
        # Say so when it happened. Otherwise the validator reads a cut-off
        # section as the whole provision and flags the missing part.
        if len(excerpt) > cap:
            clipped += "\n    [... excerpt truncated - the provision continues]"
        blocks.append(
            f"[{i}] {s.get('title') or 'Untitled'} ({s.get('court') or 'Unknown'})\n"
            f"    EXCERPT: {clipped}"
        )

    # The synthesis agent is told the user's state, so the draft can legitimately
    # name it. Withholding it here made the validator read a supplied fact as an
    # invented one and burn a revision call correcting a correct answer.
    location = f"USER'S STATE: {state}\n\n" if state else ""

    prompt = (
        f"{location}"
        f"{_matter_block(matter)}"
        f"USER QUESTION: {question}\n\n"
        f"SOURCE EXCERPTS:\n\n" + "\n\n".join(blocks) + "\n\n"
        f"DRAFT ANSWER:\nTitle: {draft.get('title')}\nBody: {draft.get('body')}\n"
        f"Next steps (practical signposting - not subject to rules 1 and 2): "
        f"{draft.get('next_steps')}\n\n"
        "Validate the draft now, as JSON."
    )

    # Pass one: verdict only. A pass/fail plus a short list of issues is a few
    # hundred tokens, not three thousand - and it is what happens on almost
    # every answer. Asking for the rewrite in the same breath made every clean
    # answer wait for a revision that was never going to be used.
    data = await generate_json(
        prompt, _VALIDATOR_SYSTEM, temperature=0.0, max_output_tokens=600
    )

    grounded = bool(data.get("grounded", True))
    issues = [str(i) for i in (data.get("issues") or [])]

    if grounded or not issues:
        return {"grounded": grounded, "issues": issues, "revised_body": ""}

    # Pass two: only now is a rewrite worth paying for.
    try:
        fix = await generate_json(
            prompt + "\n\nPROBLEMS FOUND:\n" + "\n".join(f"- {i}" for i in issues)
            + "\n\nRewrite the body now, as JSON.",
            _REVISION_SYSTEM,
            temperature=0.0,
            max_output_tokens=3000,
        )
        revised = str(fix.get("revised_body") or "").strip()
    except GeminiError as exc:
        logger.warning("Revision pass failed, caller will use its fallback: %s", exc)
        revised = ""

    return {"grounded": False, "issues": issues, "revised_body": revised}

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