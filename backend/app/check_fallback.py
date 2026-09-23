"""Offline check for the Gemini -> Groq fallback (503 and 429).

    python -m app.check_fallback

No network, no quota: both APIs are replaced with an in-process mock
transport, so the real request/parse/stream code runs against scripted
responses. Exits non-zero if any scenario fails.
"""

import asyncio
import json
import os
import sys

# config.py refuses to import without these. Mock values only - nothing
# here talks to a database or a real API.
os.environ.setdefault("DATABASE_URL", "postgresql://check:check@localhost/check")

import httpx  # noqa: E402

from . import gemini, groq_client  # noqa: E402
from .config import settings  # noqa: E402

ANSWER = {
    "title": "Test answer",
    "body": "Section 85 of the Bharatiya Nyaya Sanhita covers this.",
    "used_sources": [1],
    "next_steps": ["Speak to a lawyer."],
}
GROQ_ANSWER = dict(ANSWER, body="Groq wrote this. Section 85 still applies.")

QUOTA_MINUTE = {"error": {
    "code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": "You exceeded your current quota. Please retry in 37.5s.",
    "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"},
    ]}}
QUOTA_DAY = {"error": {
    "code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": "You exceeded your current quota.",
    "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"},
    ]}}

OVERLOADED = {"error": {"code": 503, "status": "UNAVAILABLE",
                        "message": "This model is currently experiencing high demand."}}


def _gemini_ok(payload=ANSWER):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]},
                            "finishReason": "STOP"}]}


def _gemini_sse(payload=ANSWER, fail_after=None):
    """Gemini SSE body. fail_after=N sends N chunks then an in-stream 503."""
    text = json.dumps(payload)
    pieces = [text[i:i + 20] for i in range(0, len(text), 20)]
    frames = []
    for n, piece in enumerate(pieces):
        if fail_after is not None and n == fail_after:
            frames.append(f"data: {json.dumps(OVERLOADED)}\n\n")
            break
        frames.append("data: " + json.dumps({"candidates": [{
            "content": {"parts": [{"text": piece}]},
            "finishReason": "STOP" if n == len(pieces) - 1 else None}]}) + "\n\n")
    return "".join(frames)


def _groq_ok():
    return {"choices": [{"message": {"content": json.dumps(GROQ_ANSWER)},
                         "finish_reason": "stop"}], "usage": {}}


def _groq_sse():
    text = json.dumps(GROQ_ANSWER)
    frames = [
        "data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 15]},
                                            "finish_reason": None}]}) + "\n\n"
        for i in range(0, len(text), 15)
    ]
    frames.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n")
    frames.append("data: [DONE]\n\n")
    return "".join(frames)


class Script:
    """Scripted responses per provider, consumed in order. Records calls."""

    leaked_key = False      # class-level: any scenario, any request

    def __init__(self, gemini_responses, groq_responses=()):
        self.gemini = list(gemini_responses)
        self.groq = list(groq_responses)
        self.gemini_calls = 0
        self.groq_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if "key=" in str(request.url):
            Script.leaked_key = True
        if request.url.host == "api.groq.com":
            self.groq_calls += 1
            status, body = self.groq.pop(0)
        else:
            self.gemini_calls += 1
            status, body = self.gemini.pop(0)
        if isinstance(body, str):
            return httpx.Response(status, text=body,
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(status, json=body)


def _install(script: Script) -> None:
    gemini._quota_cooldown_until = 0.0      # every scenario starts clean
    transport = httpx.MockTransport(script.handler)
    gemini._client = httpx.AsyncClient(transport=transport)
    groq_client._client = httpx.AsyncClient(transport=transport)


async def _collect_stream():
    deltas, events = [], []
    async for kind, value in gemini.synthesize_stream("q", None, [{"title": "BNS 85"}]):
        events.append(kind)
        if kind == "delta":
            deltas.append(value)
        elif kind == "replace":
            events.append(("replace", value))
        elif kind == "draft":
            draft = value
    return "".join(deltas), events, draft


async def main() -> int:
    settings.gemini_api_key = "test-gemini-key"
    settings.groq_api_key = "test-groq-key"
    settings.llm_fallback_enabled = True
    settings.gemini_retry_base_delay = 0.0
    settings.gemini_simulate_503 = ""
    settings.llm_quota_fallback_enabled = True
    settings.gemini_quota_cooldown_max = 300.0

    failures = 0

    def check(name, cond):
        nonlocal failures
        print(("PASS  " if cond else "FAIL  ") + name)
        failures += 0 if cond else 1

    # --- detection -------------------------------------------------------
    E = gemini.GeminiError
    check("503 detected", gemini.is_gemini_503_error(E("x", status_code=503)))
    check("500 + UNAVAILABLE detected",
          gemini.is_gemini_503_error(E("Gemini API error 500: UNAVAILABLE", status_code=500)))
    check("400 not detected",
          not gemini.is_gemini_503_error(E("Gemini API error 400: unavailable", status_code=400)))
    check("429 not treated as 503", not gemini.is_gemini_503_error(E("x", status_code=429)))
    check("429 detected as quota", gemini.is_gemini_quota_error(E("x", status_code=429)))
    check("503 not treated as quota", not gemini.is_gemini_quota_error(E("x", status_code=503)))
    check("parse failure quoting 'quota' not treated as quota",
          not gemini.is_gemini_quota_error(E("Could not parse JSON. Head: 'quota exhausted'")))
    q = gemini._api_error(429, json.dumps(QUOTA_MINUTE))
    check("retryDelay read (37s, per-minute)", q.retry_after == 37.0 and not q.per_day)
    q = gemini._api_error(429, json.dumps(QUOTA_DAY))
    check("daily quota recognised", q.per_day)
    check("parse failure quoting 'unavailable' not detected",
          not gemini.is_gemini_503_error(E("Could not parse JSON. Head: 'remedy unavailable'")))
    check("unrelated exception not detected", not gemini.is_gemini_503_error(ValueError("503")))

    # --- buffered --------------------------------------------------------
    s = Script([(200, _gemini_ok())]); _install(s)
    out = await gemini.synthesize("q", None, [{"title": "BNS 85"}])
    check("success: Gemini answer, Groq never called",
          out["body"] == ANSWER["body"] and s.groq_calls == 0)

    s = Script([(503, OVERLOADED), (200, _gemini_ok())]); _install(s)
    out = await gemini.synthesize("q", None, [{"title": "BNS 85"}])
    check("503 then success: retried once, Groq never called",
          out["body"] == ANSWER["body"] and s.gemini_calls == 2 and s.groq_calls == 0)

    s = Script([(503, OVERLOADED), (503, OVERLOADED)], [(200, _groq_ok())]); _install(s)
    out = await gemini.synthesize("q", None, [{"title": "BNS 85"}])
    check("503 twice: Groq answers in the same shape",
          out["body"] == GROQ_ANSWER["body"] and out["used_sources"] == [1]
          and s.gemini_calls == 2 and s.groq_calls == 1)

    for code in (400, 401, 403):
        s = Script([(code, {"error": {"code": code, "message": "nope"}})]); _install(s)
        try:
            await gemini.synthesize("q", None, [])
            ok = False
        except gemini.GeminiError as exc:
            ok = not isinstance(exc, gemini.GeminiUnavailable)
        check(f"{code}: raised as before, no retry, no Groq",
              ok and s.gemini_calls == 1 and s.groq_calls == 0)

    s = Script([(503, OVERLOADED), (503, OVERLOADED)]); _install(s)
    try:
        await gemini.generate_json("p", "sys", media=[("image/png", b"x")])
        ok = False
    except gemini.GeminiUnavailable:
        ok = True
    check("image/PDF call: no Groq fallback (text-only model)", ok and s.groq_calls == 0)

    settings.llm_fallback_enabled = False
    s = Script([(503, OVERLOADED), (503, OVERLOADED)]); _install(s)
    try:
        await gemini.synthesize("q", None, [])
        ok = False
    except gemini.GeminiUnavailable:
        ok = True
    check("fallback disabled: 503 raised, Groq never called", ok and s.groq_calls == 0)
    settings.llm_fallback_enabled = True

    s = Script([(503, OVERLOADED), (503, OVERLOADED)], [(500, {"error": {"message": "down"}})]); _install(s)
    try:
        await gemini.synthesize("q", None, [])
        ok = False
    except gemini.GeminiUnavailable:
        ok = True
    check("Groq also fails: original 503 raised (existing error path)", ok)

    # --- quota (429) -----------------------------------------------------
    s = Script([(429, QUOTA_MINUTE)], [(200, _groq_ok()), (200, _groq_ok())]); _install(s)
    out = await gemini.synthesize("q", None, [{"title": "BNS 85"}])
    left = gemini._quota_cooldown_remaining()
    check("429: no Gemini retry, Groq answers, cooldown ~37s",
          out["body"] == GROQ_ANSWER["body"] and s.gemini_calls == 1
          and s.groq_calls == 1 and 30 < left <= 37)
    await gemini.synthesize("q", None, [{"title": "BNS 85"}])
    check("during cooldown: Gemini skipped entirely", s.gemini_calls == 1 and s.groq_calls == 2)

    s = Script([(429, QUOTA_DAY)], [(200, _groq_ok())]); _install(s)
    await gemini.synthesize("q", None, [])
    check("daily quota: cooldown uses the full cap",
          gemini._quota_cooldown_remaining() > 290)

    s = Script([(429, QUOTA_MINUTE), (200, _gemini_ok())], [(200, _groq_ok())]); _install(s)
    await gemini.synthesize("q", None, [])
    gemini._quota_cooldown_until = 1.0      # pretend the cooldown expired
    out = await gemini.synthesize("q", None, [])
    check("after cooldown: Gemini is primary again",
          out["body"] == ANSWER["body"] and s.gemini_calls == 2)

    s = Script([(429, QUOTA_MINUTE), (200, _gemini_ok())], [(200, _groq_ok())]); _install(s)
    await gemini.synthesize("q", None, [])
    await gemini.generate_json("p", "sys", media=[("image/png", b"x")])
    check("image call during cooldown still tries Gemini; success clears cooldown",
          s.gemini_calls == 2 and gemini._quota_cooldown_remaining() == 0)

    s = Script([(503, OVERLOADED), (429, QUOTA_MINUTE)], [(200, _groq_ok())]); _install(s)
    out = await gemini.synthesize("q", None, [])
    check("503 then 429 on retry: Groq answers, cooldown starts",
          out["body"] == GROQ_ANSWER["body"] and gemini._quota_cooldown_remaining() > 0)

    settings.llm_quota_fallback_enabled = False
    s = Script([(429, QUOTA_MINUTE)]); _install(s)
    try:
        await gemini.synthesize("q", None, [])
        ok = False
    except gemini.GeminiQuotaExceeded:
        ok = True
    check("quota fallback disabled: 429 raised as before, no Groq, no cooldown",
          ok and s.groq_calls == 0 and gemini._quota_cooldown_remaining() == 0)
    settings.llm_quota_fallback_enabled = True

    s = Script([(429, QUOTA_MINUTE)], [(500, {"error": {"message": "down"}})]); _install(s)
    try:
        await gemini.synthesize("q", None, [])
        ok = False
    except gemini.GeminiQuotaExceeded:
        ok = True
    check("429 and Groq also fails: the 429 is raised", ok)

    # --- streaming -------------------------------------------------------
    s = Script([(200, _gemini_sse())]); _install(s)
    text, events, draft = await _collect_stream()
    check("stream success: Gemini deltas, no Groq",
          text == ANSWER["body"] and draft["body"] == ANSWER["body"] and s.groq_calls == 0)

    s = Script([(503, OVERLOADED), (503, OVERLOADED)], [(200, _groq_sse())]); _install(s)
    text, events, draft = await _collect_stream()
    check("stream 503 before any text: Groq streams, no replace",
          text == GROQ_ANSWER["body"] and "replace" not in events and s.groq_calls == 1)

    s = Script([(200, _gemini_sse(fail_after=4)), (503, OVERLOADED)], [(200, _groq_sse())]); _install(s)
    text, events, draft = await _collect_stream()
    replaced = [e[1] for e in events if isinstance(e, tuple)]
    check("stream 503 mid-answer: no duplicated text, body replaced once",
          GROQ_ANSWER["body"] not in text and replaced == [GROQ_ANSWER["body"]]
          and draft["body"] == GROQ_ANSWER["body"])

    s = Script([(200, _gemini_sse(fail_after=4)), (200, _gemini_sse())]); _install(s)
    text, events, draft = await _collect_stream()
    replaced = [e[1] for e in events if isinstance(e, tuple)]
    check("stream 503 mid-answer, retry succeeds: replaced with Gemini body",
          replaced == [ANSWER["body"]] and s.groq_calls == 0)

    s = Script([(429, QUOTA_MINUTE)], [(200, _groq_sse()), (200, _groq_sse())]); _install(s)
    text, events, draft = await _collect_stream()
    check("stream 429: Groq streams, no Gemini retry",
          text == GROQ_ANSWER["body"] and s.gemini_calls == 1 and "replace" not in events)
    text, events, draft = await _collect_stream()
    check("stream during cooldown: Gemini skipped",
          text == GROQ_ANSWER["body"] and s.gemini_calls == 1 and s.groq_calls == 2)

    check("API keys never sent in a URL", not Script.leaked_key)

    await gemini.close_client()
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))