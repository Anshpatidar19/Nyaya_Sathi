"""Push existing public.users rows into Supabase Auth.

After this runs, everyone who signed up before the switch appears under
Authentication in the Supabase dashboard, and their profile row is linked
by auth_id.

Run ONCE from the backend directory, after migrate_to_supabase:

    python -m app.link_users_to_supabase_auth

Passwords: the script first tries to import each user's existing bcrypt hash
so their current password keeps working. If your Supabase project rejects
password_hash, it falls back to creating the account with a temporary
password, which it prints - give that to the user and have them change it.

Safe to re-run: users already linked (auth_id set) or already present in
Supabase Auth are skipped.
"""

import asyncio
import secrets
import sys

from sqlalchemy.orm import Session

from . import models, supabase_auth
from .database import SessionLocal


async def link_all() -> int:
    db: Session = SessionLocal()
    created = linked = skipped = 0
    temp_passwords = []

    try:
        # Map existing Supabase Auth users by email so re-runs don't duplicate.
        try:
            existing = {
                (u.get("email") or "").lower(): u["id"]
                for u in await supabase_auth.list_users()
            }
        except supabase_auth.AuthError as exc:
            print(f"Could not list Supabase Auth users: {exc}")
            return 1

        users = db.query(models.User).order_by(models.User.id).all()
        print(f"Found {len(users)} local user(s).\n")

        for user in users:
            if user.auth_id:
                print(f"  skip    {user.email} (already linked)")
                skipped += 1
                continue

            email_key = (user.email or "").lower()

            # Already in Supabase Auth from an earlier partial run - just link.
            if email_key in existing:
                user.auth_id = existing[email_key]
                db.commit()
                print(f"  link    {user.email} (existed in Auth)")
                linked += 1
                continue

            metadata = {
                "name": user.name,
                "state": user.state,
                "preferred_language": user.preferred_language or "en",
            }

            supa_user = None

            # 1. Try importing the existing bcrypt hash - password unchanged.
            if user.hashed_password:
                try:
                    supa_user = await supabase_auth.admin_create_user(
                        email=user.email,
                        password_hash=user.hashed_password,
                        metadata=metadata,
                    )
                    print(f"  create  {user.email} (existing password preserved)")
                except supabase_auth.AuthError as exc:
                    print(f"          hash import rejected ({exc}); using a temporary password")

            # 2. Fall back to a temporary password.
            if supa_user is None:
                temp = secrets.token_urlsafe(16)
                try:
                    supa_user = await supabase_auth.admin_create_user(
                        email=user.email, password=temp, metadata=metadata
                    )
                    temp_passwords.append((user.email, temp))
                    print(f"  create  {user.email} (TEMPORARY password issued)")
                except supabase_auth.AuthError as exc:
                    print(f"  FAILED  {user.email}: {exc}")
                    continue

            user.auth_id = supa_user["id"]
            db.commit()
            created += 1

    finally:
        db.close()

    print(f"\nCreated: {created}   Linked: {linked}   Skipped: {skipped}")

    if temp_passwords:
        print("\n" + "=" * 60)
        print("TEMPORARY PASSWORDS - save these now, they are not stored:")
        for email, pw in temp_passwords:
            print(f"  {email}  ->  {pw}")
        print("Have each user log in and change their password.")
        print("=" * 60)

    print("\nCheck Authentication -> Users in the Supabase dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(link_all()))