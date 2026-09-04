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

import logging
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

async def get_user(access_token: str) -> Optional[Dict[str, Any]]:
    """Validate a Supabase access token. Returns the user, or None if invalid.

    Asking GoTrue directly rather than verifying the JWT locally means we
    don't have to care whether the project signs with a shared secret or an
    asymmetric key, and revoked tokens stop working immediately.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{_base()}/user",
                headers={
                    "apikey": settings.supabase_anon_key,
                    "Authorization": f"Bearer {access_token}",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("Auth check failed to reach Supabase: %s", exc)
        return None

    if resp.status_code != 200:
        return None
    return resp.json()


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