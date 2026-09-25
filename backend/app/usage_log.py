"""Per-call and per-query LLM cost logging, printed to the terminal.

Every model call in the app (Gemini generate/stream, the Groq fallback, and
query embeddings) reports here. Two kinds of line come out:

  LLM_CALL    one per model call, with the fields below
  QUERY_COST  one per HTTP request that made at least one model call (or was
              served from the answer cache): the totals for that query

LLM_CALL fields, in this order:
  input_tokens, output_tokens, total_tokens, latency_ms,
  estimated_cost_usd, estimated_cost_inr, status, error

  status  success  - Gemini answered
          fallback - Groq answered in place of Gemini
          error    - the call failed (503, 429, stall, empty, parse ...)
          cancelled- the client went away mid-stream

Gemini bills thinking tokens as output, so output_tokens here is
candidatesTokenCount + thoughtsTokenCount - the number you are charged for.
Failed calls (4xx/5xx) are not billed by Google, so they cost 0 unless the
API still returned a usage block.

Prices are USD per 1M tokens and can be overridden without touching code:
  LLM_PRICES_JSON='{"gemini-3.1-flash-lite": [0.25, 1.50]}'
  USD_INR_RATE=88.0
  USAGE_LOG=0           switch the whole thing off
  USAGE_LOG_FORMAT=json one JSON object per line instead of key=value

The per-request totals travel in a contextvar that holds a mutable object.
That matters: asyncio.to_thread work (the embedding call, the answer cache
lookup) runs on a copy of the context, and a value set there would never
reach the QUERY_COST line. Mutating a shared object is seen everywhere.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("app.usage")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default) not in ("0", "false", "False", "")


def _settings():
    try:
        from .config import settings
        return settings
    except Exception:
        return None


def _enabled() -> bool:
    st = _settings()
    return bool(getattr(st, "usage_log", _flag("USAGE_LOG", "1")))


def _json_format() -> bool:
    st = _settings()
    fmt = getattr(st, "usage_log_format", None) or os.getenv("USAGE_LOG_FORMAT", "kv")
    return str(fmt).lower() == "json"


def usd_inr_rate() -> float:
    st = _settings()
    rate = getattr(st, "usd_inr_rate", None)
    if rate:
        return float(rate)
    try:
        return float(os.getenv("USD_INR_RATE", "88.0"))
    except ValueError:
        return 88.0


# USD per 1M tokens: (input, output). Matched by prefix, longest first, so
# "gemini-3.1-flash-lite-preview" still resolves to the flash-lite row.
_DEFAULT_PRICES: Dict[str, Tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-flash": (0.50, 3.00),
    "gemini-3.1-pro": (2.00, 12.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-embedding-001": (0.15, 0.0),
    "openai/gpt-oss-120b": (0.15, 0.75),
    "openai/gpt-oss-20b": (0.10, 0.50),
}

_prices_cache: Optional[Dict[str, Tuple[float, float]]] = None


def _prices() -> Dict[str, Tuple[float, float]]:
    global _prices_cache
    if _prices_cache is not None:
        return _prices_cache
    prices = dict(_DEFAULT_PRICES)
    st = _settings()
    raw = getattr(st, "llm_prices_json", "") or os.getenv("LLM_PRICES_JSON", "")
    if raw:
        try:
            for model, pair in json.loads(raw).items():
                prices[str(model)] = (float(pair[0]), float(pair[1]))
        except Exception as exc:
            logger.warning("LLM_PRICES_JSON ignored (%s)", exc)
    _prices_cache = prices
    return prices


def price_for(model: str) -> Optional[Tuple[float, float]]:
    model = (model or "").strip()
    if model.startswith("models/"):
        model = model[len("models/"):]
    prices = _prices()
    if model in prices:
        return prices[model]
    for key in sorted(prices, key=len, reverse=True):
        if model.startswith(key):
            return prices[key]
    return None


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    price = price_for(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def estimate_tokens(text: str) -> int:
    """Rough token count for APIs that return none (embeddings, a Groq
    stream without a usage frame). ~4 characters per token for English."""
    return max(1, len(text or "") // 4) if text else 0


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------

@dataclass
class CallRecord:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: int
    cost_usd: Optional[float]
    status: str
    error: Optional[str]


@dataclass
class RequestUsage:
    method: str = ""
    path: str = ""
    started: float = field(default_factory=time.perf_counter)
    calls: List[CallRecord] = field(default_factory=list)
    cache_hit: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_current: contextvars.ContextVar[Optional[RequestUsage]] = contextvars.ContextVar(
    "nyaya_request_usage", default=None
)


def begin_request(method: str, path: str):
    """Called by the HTTP middleware. Returns (usage, token)."""
    usage = RequestUsage(method=method, path=path)
    token = _current.set(usage)
    return usage, token


def end_request(token) -> None:
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        pass


def current() -> Optional[RequestUsage]:
    return _current.get()


def mark_cache_hit() -> None:
    usage = _current.get()
    if usage is not None:
        usage.cache_hit = True


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _fmt_usd(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.6f}"


def _fmt_inr(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def _clean_error(error: Optional[str]) -> Optional[str]:
    if not error:
        return None
    text = " ".join(str(error).split())
    # Never let anything key-shaped reach the terminal.
    text = re.sub(r"(?i)(key=)[A-Za-z0-9_\-.]{8,}", r"\1<redacted>", text)
    text = re.sub(r"\bAIza[0-9A-Za-z_\-]{20,}", "<redacted>", text)
    text = re.sub(r"\bgsk_[0-9A-Za-z]{10,}", "<redacted>", text)
    return text[:240]


def _kv(fields: List[Tuple[str, Any]]) -> str:
    parts = []
    for key, value in fields:
        if value is None or value == "":
            value = "-"
        value = str(value)
        if " " in value or "=" in value:
            value = json.dumps(value)
        parts.append(f"{key}={value}")
    return " ".join(parts)


def _emit(tag: str, fields: List[Tuple[str, Any]]) -> None:
    if _json_format():
        logger.info("%s %s", tag, json.dumps(dict(fields), default=str))
    else:
        logger.info("%s %s", tag, _kv(fields))


def record(
    *,
    model: str,
    input_tokens: Optional[int] = 0,
    output_tokens: Optional[int] = 0,
    total_tokens: Optional[int] = None,
    latency_ms: Optional[float] = 0,
    status: str = "success",
    error: Optional[str] = None,
    tokens_estimated: bool = False,
) -> None:
    """Log one model call and add it to the current request's totals.

    `model` is used only to look up the price; it is not printed.
    Never raises: cost logging must not be able to break an answer.
    """
    if not _enabled():
        return
    try:
        inp = int(input_tokens or 0)
        out = int(output_tokens or 0)
        tot = int(total_tokens) if total_tokens else inp + out
        lat = int(round(latency_ms or 0))
        cost = estimate_cost_usd(model, inp, out)
        rate = usd_inr_rate()
        usage = _current.get()
        err = _clean_error(error)

        rec = CallRecord(inp, out, tot, lat, cost, status, err)
        if usage is not None:
            with usage.lock:
                usage.calls.append(rec)

        fields: List[Tuple[str, Any]] = [
            ("input_tokens", inp),
            ("output_tokens", out),
            ("total_tokens", tot),
            ("latency_ms", lat),
            ("estimated_cost_usd", _fmt_usd(cost)),
            ("estimated_cost_inr", _fmt_inr(None if cost is None else cost * rate)),
            ("status", status),
            ("error", err),
        ]
        if tokens_estimated:
            fields.append(("tokens_estimated", "true"))
        _emit("LLM_CALL", fields)
    except Exception as exc:  # pragma: no cover - logging must never fail a call
        logger.debug("usage record failed: %s", exc)


def summarize(usage: Optional[RequestUsage], http_status: Optional[int] = None) -> None:
    """The per-query total. Printed once, after the response body has fully
    gone out - for a streamed answer that includes the validator."""
    if usage is None or not _enabled():
        return
    with usage.lock:
        calls = list(usage.calls)
    if not calls and not usage.cache_hit:
        return
    try:
        inp = sum(c.input_tokens for c in calls)
        out = sum(c.output_tokens for c in calls)
        tot = sum(c.total_tokens for c in calls)
        priced = [c.cost_usd for c in calls if c.cost_usd is not None]
        cost = sum(priced) if priced or not calls else None
        rate = usd_inr_rate()

        if not calls:
            status = "cache_hit"
        elif all(c.status in ("error", "cancelled") for c in calls):
            status = "error"
        elif any(c.status == "fallback" for c in calls):
            status = "fallback"
        else:
            status = "success"
        failed = sum(1 for c in calls if c.status == "error")
        errors = "; ".join(sorted({c.error for c in calls if c.error}))[:240] or None

        _emit("QUERY_COST", [
            ("endpoint", f"{usage.method} {usage.path}".strip()),
            ("llm_calls", len(calls)),
            ("failed_calls", failed),
            ("input_tokens", inp),
            ("output_tokens", out),
            ("total_tokens", tot),
            ("latency_ms", int((time.perf_counter() - usage.started) * 1000)),
            ("estimated_cost_usd", _fmt_usd(cost)),
            ("estimated_cost_inr", _fmt_inr(None if cost is None else cost * rate)),
            ("status", status),
            ("http_status", http_status),
            ("error", errors),
        ])
    except Exception as exc:  # pragma: no cover
        logger.debug("usage summary failed: %s", exc)


# ---------------------------------------------------------------------------
# Helpers for the model clients
# ---------------------------------------------------------------------------

def gemini_tokens(usage_meta: Optional[Dict[str, Any]]) -> Tuple[int, int, Optional[int]]:
    """(input, billed_output, total) from a Gemini usageMetadata block.
    Thinking tokens are billed as output, so they are counted as output."""
    u = usage_meta or {}
    inp = int(u.get("promptTokenCount") or 0)
    out = int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)
    total = u.get("totalTokenCount")
    return inp, out, int(total) if total else None


def openai_tokens(usage_meta: Optional[Dict[str, Any]]) -> Tuple[int, int, Optional[int]]:
    u = usage_meta or {}
    inp = int(u.get("prompt_tokens") or 0)
    out = int(u.get("completion_tokens") or 0)
    total = u.get("total_tokens")
    return inp, out, int(total) if total else None