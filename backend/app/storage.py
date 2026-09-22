"""Supabase Storage client.

Two buckets, for two different kinds of file:

  settings.supabase_bucket        PRIVATE. Legal documents. Nothing is served
                                  by a public URL - the API hands out
                                  short-lived signed URLs instead, so a
                                  leaked link expires and one user can never
                                  guess another user's path.

  settings.supabase_avatar_bucket PUBLIC. Profile pictures only. These are
                                  shown to every user who browses the
                                  advocate directory, and signing a URL per
                                  row would mean one Supabase round trip per
                                  search result. Keeping them separate is
                                  what lets the documents bucket stay fully
                                  private.

Paths are namespaced by user: users/<user_id>/<uuid>-<safe-filename>
                              avatars/<user_id>/<uuid>-<safe-filename>

Uses the Storage REST API over httpx rather than the supabase-py SDK, to
avoid pulling in another dependency tree.
"""

import logging
import re
import uuid
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 60.0

# Uploads get their own, longer budget. A phone photo of a document is
# several MB, and Supabase Storage can take over a minute to answer on a slow
# connection or a cold free-tier project. A read timeout used to escape as an
# unhandled httpx exception - a raw 500 - instead of the endpoint's friendly
# "could not store that file, please try again".
UPLOAD_TIMEOUT = httpx.Timeout(connect=15.0, write=120.0, read=120.0, pool=15.0)

# Types we accept. Everything else is rejected - this is a legal-documents
# feature, not general file hosting, and it keeps executables out.
ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    # Phone photos: iPhones save HEIC by default, and a photographed
    # handwritten application is exactly the case OCR is for.
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}

MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# Avatars are a narrower case: images only, and much smaller. A 10 MB PNG as
# someone's profile picture would be downloaded on every search page.
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageError(RuntimeError):
    pass


def _base() -> str:
    if not settings.supabase_url or not settings.supabase_service_key:
        raise StorageError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY are not set. Add them to "
            "backend/.env - see .env.example."
        )
    return settings.supabase_url.rstrip("/") + "/storage/v1"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.supabase_service_key}"}


def _bucket(name: Optional[str] = None) -> str:
    return name or settings.supabase_bucket


def safe_name(filename: str) -> str:
    """Strip anything that could break a path or escape the user's folder."""
    name = (filename or "file").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = _UNSAFE.sub("_", name).strip("._-") or "file"
    return name[:120]


def build_path(user_id: int, filename: str) -> str:
    return f"users/{user_id}/{uuid.uuid4().hex}-{safe_name(filename)}"


def build_avatar_path(user_id: int, filename: str) -> str:
    return f"avatars/{user_id}/{uuid.uuid4().hex}-{safe_name(filename)}"


async def upload(
    path: str, data: bytes, content_type: str, bucket: Optional[str] = None
) -> str:
    """Put bytes in the bucket. Returns the storage path."""
    url = f"{_base()}/object/{_bucket(bucket)}/{path}"
    headers = {
        **_headers(),
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "false",
    }
    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
        for attempt in (1, 2):
            try:
                resp = await client.post(url, headers=headers, content=data)
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise StorageError(f"Upload timed out: {exc!r}")
                logger.warning("Storage upload timed out for %s; retrying once", path)
                continue
            except httpx.HTTPError as exc:
                raise StorageError(f"Upload failed: {exc!r}")

            # A timeout doesn't mean the first attempt failed - Supabase may
            # have stored the file and only the reply was slow. With x-upsert
            # false the retry then reports a duplicate, which here means
            # success: the path is unique to this one upload.
            if attempt == 2 and resp.status_code in (400, 409) and (
                "exist" in resp.text.lower() or "duplicate" in resp.text.lower()
            ):
                return path
            if resp.status_code >= 400:
                raise StorageError(f"Upload failed ({resp.status_code}): {resp.text[:300]}")
            return path
    return path


async def signed_url(
    path: str, expires_in: int = 3600, bucket: Optional[str] = None
) -> str:
    """Time-limited download link for a private object."""
    url = f"{_base()}/object/sign/{_bucket(bucket)}/{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            url, headers=_headers(), json={"expiresIn": expires_in}
        )
    if resp.status_code >= 400:
        raise StorageError(f"Could not sign URL ({resp.status_code}): {resp.text[:300]}")

    signed = resp.json().get("signedURL") or resp.json().get("signedUrl")
    if not signed:
        raise StorageError("Storage did not return a signed URL.")
    return settings.supabase_url.rstrip("/") + "/storage/v1" + signed


def public_url(path: str, bucket: Optional[str] = None) -> str:
    """Plain URL for an object in a PUBLIC bucket.

    No network call - the URL is just built from the path, which is why it
    can be handed out for every row of a search result. Only ever use this
    for the avatars bucket; calling it on a private object returns a URL
    that correctly 400s.
    """
    if not settings.supabase_url:
        raise StorageError("SUPABASE_URL is not set. See backend/.env.example.")
    base = settings.supabase_url.rstrip("/") + "/storage/v1"
    return f"{base}/object/public/{_bucket(bucket)}/{path}"


async def delete(path: str, bucket: Optional[str] = None) -> None:
    url = f"{_base()}/object/{_bucket(bucket)}/{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.delete(url, headers=_headers())
    if resp.status_code >= 400:
        # Don't fail the whole request if the object was already gone.
        logger.warning("Storage delete failed (%s): %s", resp.status_code, resp.text[:200])


async def ensure_bucket(
    bucket: Optional[str] = None,
    public: bool = False,
    file_size_limit: int = MAX_BYTES,
) -> None:
    """Create the bucket on first boot if it doesn't exist.

    Private by default. Called with no arguments at startup for the
    documents bucket; the avatars bucket is created lazily on the first
    avatar upload so startup doesn't grow another network call.
    """
    name = _bucket(bucket)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(f"{_base()}/bucket/{name}", headers=_headers())
        if resp.status_code == 200:
            return

        resp = await client.post(
            f"{_base()}/bucket",
            headers=_headers(),
            json={
                "id": name,
                "name": name,
                "public": public,
                "file_size_limit": file_size_limit,
            },
        )
    if resp.status_code >= 400 and "already exists" not in resp.text.lower():
        logger.warning("Could not create bucket: %s %s", resp.status_code, resp.text[:200])


async def ensure_avatar_bucket() -> None:
    """The public avatars bucket. Separate helper so callers can't
    accidentally pass public=True for the documents bucket."""
    await ensure_bucket(
        settings.supabase_avatar_bucket, public=True, file_size_limit=MAX_AVATAR_BYTES
    )