"""Supabase Auth (GoTrue) REST client.

Credentials live in Supabase's `auth.users` table - that's what makes users
show up under Authentication in the dashboard. Our own `public.users` table
survives as a PROFILE table (name, state, preferred_language) linked by
`auth_id`, so query_logs and documents keep their integer foreign keys.

Endpoints used (all under {SUPABASE_URL}/auth/v1):
    POST /admin/users            create a user (service_role, no email confirm)
    POST /token?grant_type=password  exchange email+password for a session
    GET  /user                   validate a token and read the user
    GET  /admin/users            list (used by the linking script)

Registration goes through the ADMIN endpoint with email_confirm=true rather
than /signup, so users can log in immediately without an email round-trip.
Flip CONFIRM_EMAIL to False->True only if you add a real confirmation flow.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


class AuthError(RuntimeError):
    """Raised for auth failures. `status` mirrors the GoTrue status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _base() -> str:
    if not settings.supabase_url:
        raise AuthError("SUPABASE_URL is not set. See backend/.env.example.", 500)
    return settings.supabase_url.rstrip("/") + "/auth/v1"


def _anon_headers() -> Dict[str, str]:
    if not settings.supabase_anon_key:
        raise AuthError("SUPABASE_ANON_KEY is not set. See backend/.env.example.", 500)
    return {
        "apikey": settings.supabase_anon_key,
        "Content-Type": "application/json",
    }


def _admin_headers() -> Dict[str, str]:
    if not settings.supabase_service_key:
        raise AuthError("SUPABASE_SERVICE_KEY is not set. See backend/.env.example.", 500)
    return {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
    }


def _message(resp: httpx.Response, fallback: str) -> str:
    try:
        data = resp.json()
    except Exception:
        return fallback
    return (
        data.get("msg")
        or data.get("message")
        or data.get("error_description")
        or data.get("error")
        or fallback
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def sign_up(
    email: str, password: str, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Register through the public signup endpoint.

    This is the path that triggers Supabase's confirmation email. The admin
    endpoint below can create a user with email_confirm=True, but that skips
    verification entirely - which is precisely what lets someone register as
    nobody@nowhere.fake.

    Returns the GoTrue response. When confirmation is required the response
    carries a user but NO session, so the caller must not expect a token.
    """
    payload: Dict[str, Any] = {"email": email, "password": password}
    if metadata:
        payload["data"] = metadata

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"{_base()}/signup", headers=_anon_headers(), json=payload)

    if resp.status_code >= 400:
        msg = _message(resp, "Could not create the account.")
        low = msg.lower()
        if "already" in low or "registered" in low:
            raise AuthError("An account with this email already exists.", 400)
        if "invalid" in low and "email" in low:
            raise AuthError("That email address doesn't look valid.", 400)
        if "password" in low:
            raise AuthError(msg, 400)
        raise AuthError(msg, resp.status_code)

    return resp.json()


def needs_confirmation(signup_response: Dict[str, Any]) -> bool:
    """True when Supabase created the user but withheld a session.

    With email confirmation on, /signup returns the user and no access_token
    until the link is clicked.
    """
    if signup_response.get("access_token"):
        return False
    user = signup_response.get("user") or signup_response
    return not user.get("email_confirmed_at") and not user.get("confirmed_at")


async def resend_confirmation(email: str) -> None:
    """Ask Supabase to send the confirmation email again."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{_base()}/resend",
            headers=_anon_headers(),
            json={"type": "signup", "email": email},
        )
    if resp.status_code >= 400:
        raise AuthError(_message(resp, "Could not resend the email."), resp.status_code)


async def send_password_reset(email: str, redirect_to: Optional[str] = None) -> None:
    """Trigger Supabase's password reset email.

    Never reveals whether the address is registered - the caller should
    report success either way.
    """
    payload: Dict[str, Any] = {"email": email}
    if redirect_to:
        payload["redirect_to"] = redirect_to
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        await client.post(f"{_base()}/recover", headers=_anon_headers(), json=payload)


async def admin_create_user(
    email: str,
    password: Optional[str] = None,
    password_hash: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a user in auth.users. Returns the GoTrue user object.

    Pass `password` for a new signup, or `password_hash` to import an
    existing bcrypt hash so the user's current password keeps working.
    """
    payload: Dict[str, Any] = {"email": email, "email_confirm": True}
    if password:
        payload["password"] = password
    if password_hash:
        payload["password_hash"] = password_hash
    if metadata:
        payload["user_metadata"] = metadata

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{_base()}/admin/users", headers=_admin_headers(), json=payload
        )

    if resp.status_code >= 400:
        msg = _message(resp, "Could not create the account.")
        if "already" in msg.lower() or resp.status_code == 422:
            raise AuthError("An account with this email already exists.", 400)
        raise AuthError(msg, resp.status_code)

    return resp.json()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def sign_in(email: str, password: str) -> Dict[str, Any]:
    """Exchange email + password for a session. Returns the token payload."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{_base()}/token",
            headers=_anon_headers(),
            params={"grant_type": "password"},
            json={"email": email, "password": password},
        )

    if resp.status_code >= 400:
        detail = _message(resp, "").lower()
        # Worth distinguishing: someone who registered and never clicked the
        # link would otherwise be stuck retyping a correct password.
        if "confirm" in detail or "not_confirmed" in detail:
            raise AuthError(
                "Please confirm your email first. Check your inbox for the "
                "link we sent you.",
                403,
            )
        # Otherwise don't leak whether the address exists.
        raise AuthError("Incorrect email or password.", 401)

    data = resp.json()
    if not data.get("access_token"):
        raise AuthError("Incorrect email or password.", 401)
    return data


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------

# --- token validation cache ------------------------------------------------
# Every authenticated request was making a round trip to Supabase, over a
# fresh TLS connection, just to ask who the caller was. On a page that fires
# four requests that is four handshakes and four round trips before any work
# starts - which is what made Matters and the chat history feel slow.
#
# The cache holds a verified token for a minute. Access tokens are valid for
# an hour anyway, so a 60-second window narrows the revocation gap to almost
# nothing while removing nearly all the latency.
_TOKEN_TTL = 60.0        # seconds a good token is trusted without re-checking
_NEGATIVE_TTL = 5.0      # a rejected token is remembered only briefly
_CACHE_MAX = 512

# token -> (expires_at, user or None)
_token_cache: Dict[str, tuple] = {}

# One client, reused. A new AsyncClient per call means a new TLS handshake
# per call, which on a connection to another continent is most of the cost.
_client: Optional[httpx.AsyncClient] = None


def _shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _client


def _cache_get(token: str) -> Optional[tuple]:
    hit = _token_cache.get(token)
    if not hit:
        return None
    if time.monotonic() > hit[0]:
        _token_cache.pop(token, None)
        return None
    return hit


def _cache_put(token: str, user: Optional[Dict[str, Any]]) -> None:
    if len(_token_cache) > _CACHE_MAX:
        # Cheap eviction: drop anything already expired, then the oldest.
        now = time.monotonic()
        for k in [k for k, v in _token_cache.items() if v[0] < now]:
            _token_cache.pop(k, None)
        while len(_token_cache) > _CACHE_MAX:
            _token_cache.pop(next(iter(_token_cache)), None)

    ttl = _TOKEN_TTL if user else _NEGATIVE_TTL
    _token_cache[token] = (time.monotonic() + ttl, user)


def invalidate_token(access_token: str) -> None:
    """Forget a cached token - call on logout or password change."""
    _token_cache.pop(access_token, None)


# A page load fires several requests at once. Without this they all miss the
# empty cache simultaneously and all make the same call. The first one now
# does the work; the rest await its result.
_inflight: Dict[str, "asyncio.Future"] = {}


async def _fetch_user(access_token: str) -> Optional[Dict[str, Any]]:
    try:
        resp = await _shared_client().get(
            f"{_base()}/user",
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {access_token}",
            },
        )
    except httpx.HTTPError as exc:
        logger.warning("Auth check failed to reach Supabase: %s", exc)
        return None   # not cached - a network blip shouldn't lock the user out

    user = resp.json() if resp.status_code == 200 else None
    _cache_put(access_token, user)
    return user


async def get_user(access_token: str) -> Optional[Dict[str, Any]]:
    """Validate a Supabase access token. Returns the user, or None if invalid.

    Asking GoTrue directly rather than verifying the JWT locally means we
    don't have to care whether the project signs with a shared secret or an
    asymmetric key, and revoked tokens stop working within the cache window.
    """
    cached = _cache_get(access_token)
    if cached:
        return cached[1]

    existing = _inflight.get(access_token)
    if existing is not None:
        return await asyncio.shield(existing)

    task = asyncio.ensure_future(_fetch_user(access_token))
    _inflight[access_token] = task
    try:
        return await asyncio.shield(task)
    finally:
        _inflight.pop(access_token, None)


# ---------------------------------------------------------------------------
# Admin helpers (used by the linking script)
# ---------------------------------------------------------------------------

async def list_users(page: int = 1, per_page: int = 200) -> list:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{_base()}/admin/users",
            headers=_admin_headers(),
            params={"page": page, "per_page": per_page},
        )
    if resp.status_code >= 400:
        raise AuthError(_message(resp, "Could not list users."), resp.status_code)
    data = resp.json()
    return data.get("users", data if isinstance(data, list) else [])

async def update_password(access_token: str, new_password: str) -> None:
    """Set a new password using a recovery token from the reset email.

    GoTrue's recovery link carries a short-lived access token in the URL
    fragment. Exchanging it here rather than in the browser keeps the anon
    key and the raw token out of our frontend's network log.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.put(
            f"{_base()}/user",
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"password": new_password},
        )

    if resp.status_code >= 400:
        msg = _message(resp, "Could not update the password.").lower()
        if "expired" in msg or "invalid" in msg or resp.status_code == 401:
            raise AuthError(
                "That reset link has expired or has already been used. "
                "Request a new one.",
                400,
            )
        raise AuthError(_message(resp, "Could not update the password."), resp.status_code)