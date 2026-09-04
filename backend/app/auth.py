"""Auth layer. Credentials are owned by Supabase Auth; this module maps a
Supabase access token onto the local profile row in `public.users`.

Password hashing lives in Supabase now, so there's no bcrypt here. The
helpers below are kept only for the migration script and for local SQLite
development.
"""

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models, supabase_auth
from .database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def ensure_profile(
    db: Session,
    auth_id: str,
    email: str,
    name: Optional[str] = None,
    state: Optional[str] = None,
    preferred_language: str = "en",
) -> models.User:
    """Find (or create, or back-fill) the local profile for a Supabase user.

    Three cases:
      - already linked by auth_id  -> return it
      - exists by email but unlinked -> attach auth_id (this is how migrated
        users get connected the first time they log in)
      - no row at all -> create one
    """
    user = db.query(models.User).filter(models.User.auth_id == auth_id).first()
    if user:
        return user

    user = db.query(models.User).filter(models.User.email == email).first()
    if user:
        user.auth_id = auth_id
        db.commit()
        db.refresh(user)
        return user

    user = models.User(
        auth_id=auth_id,
        email=email,
        name=name or (email.split("@")[0] if email else "User"),
        state=state,
        preferred_language=preferred_language or "en",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.User:
    supa_user = await supabase_auth.get_user(token)
    if not supa_user or not supa_user.get("id"):
        raise CREDENTIALS_EXCEPTION

    meta = supa_user.get("user_metadata") or {}
    return ensure_profile(
        db,
        auth_id=supa_user["id"],
        email=supa_user.get("email") or "",
        name=meta.get("name"),
        state=meta.get("state"),
        preferred_language=meta.get("preferred_language", "en"),
    )


# --- legacy helpers -------------------------------------------------------
# Only used by migrate/link scripts. Application login no longer touches these.

try:
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

except ImportError:  # passlib removed after migration - that's fine
    def hash_password(password: str) -> str:
        raise RuntimeError("passlib is not installed; Supabase Auth handles hashing.")

    def verify_password(plain_password: str, hashed_password: str) -> bool:
        raise RuntimeError("passlib is not installed; Supabase Auth handles hashing.")