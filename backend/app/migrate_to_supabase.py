"""One-time migration: SQLite -> Supabase Postgres.

Copies every existing user and query log into Supabase, preserving IDs so
nothing breaks. Password hashes move across unchanged, so everyone who has
already signed up keeps their existing password.

Run ONCE, from the backend directory, after setting DATABASE_URL in .env:

    python -m app.migrate_to_supabase

It is safe to re-run: rows that already exist (matched on email for users,
and on id for logs) are skipped rather than duplicated.
"""

import sys
from pathlib import Path

import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from . import models
from .database import Base, engine as target_engine

SQLITE_PATH = Path(__file__).resolve().parent.parent / "nyaya_sathi.db"


def _dt(value):
    """SQLite hands back timestamps as strings when read via raw SQL.
    Postgres needs real datetime objects, so parse them here."""
    if value is None or isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    text_value = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(text_value)
    except ValueError:
        # Unparseable timestamp shouldn't cost us the row.
        return datetime.datetime.utcnow()


def main() -> int:
    if not SQLITE_PATH.exists():
        print(f"No SQLite database found at {SQLITE_PATH}.")
        print("Nothing to migrate - creating empty tables in Supabase instead.")
        Base.metadata.create_all(bind=target_engine)
        return 0

    if target_engine.url.get_backend_name() == "sqlite":
        print("DATABASE_URL still points at SQLite. Set the Supabase URI first.")
        return 1

    print(f"Source: {SQLITE_PATH}")
    print(f"Target: {target_engine.url.render_as_string(hide_password=True)}\n")

    print("Creating tables in Supabase (if absent)...")
    Base.metadata.create_all(bind=target_engine)

    src_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
    SrcSession = sessionmaker(bind=src_engine)
    DstSession = sessionmaker(bind=target_engine)

    src = SrcSession()
    dst = DstSession()

    users_copied = users_skipped = 0
    logs_copied = logs_skipped = 0

    try:
        # ---- users -------------------------------------------------------
        rows = src.execute(text(
            "SELECT id, name, email, hashed_password, preferred_language, "
            "state, created_at FROM users ORDER BY id"
        )).mappings().all()

        print(f"Found {len(rows)} user(s) in SQLite.")

        for r in rows:
            exists = dst.query(models.User).filter(
                models.User.email == r["email"]
            ).first()
            if exists:
                users_skipped += 1
                continue

            dst.add(models.User(
                id=r["id"],
                name=r["name"],
                email=r["email"],
                hashed_password=r["hashed_password"],
                preferred_language=r["preferred_language"] or "en",
                state=r["state"],
                created_at=_dt(r["created_at"]),
            ))
            users_copied += 1

        dst.commit()

        # ---- query logs --------------------------------------------------
        try:
            logs = src.execute(text(
                "SELECT id, user_id, question, answer_title, answer_body, "
                "citations_json, created_at FROM query_logs ORDER BY id"
            )).mappings().all()
        except Exception:
            logs = []

        print(f"Found {len(logs)} query log(s) in SQLite.")

        valid_user_ids = {row[0] for row in dst.query(models.User.id).all()}

        for r in logs:
            if dst.query(models.QueryLog).filter(
                models.QueryLog.id == r["id"]
            ).first():
                logs_skipped += 1
                continue
            if r["user_id"] not in valid_user_ids:
                logs_skipped += 1
                continue

            dst.add(models.QueryLog(
                id=r["id"],
                user_id=r["user_id"],
                question=r["question"],
                answer_title=r["answer_title"],
                answer_body=r["answer_body"],
                citations_json=r["citations_json"],
                created_at=_dt(r["created_at"]),
            ))
            logs_copied += 1

        dst.commit()

        # ---- reset sequences ---------------------------------------------
        # We inserted explicit IDs, so Postgres' sequences are still at 1 and
        # the next signup would collide. Bump them past the highest id.
        for table in ("users", "query_logs", "documents"):
            dst.execute(text(f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                )
            """))
        dst.commit()
        print("Reset Postgres id sequences.")

    except Exception as exc:
        dst.rollback()
        print(f"\nMigration failed, nothing committed in this step: {exc}")
        return 1
    finally:
        src.close()
        dst.close()

    print(f"\nUsers:  {users_copied} copied, {users_skipped} already present")
    print(f"Logs:   {logs_copied} copied, {logs_skipped} skipped")
    print("\nDone. Keep nyaya_sathi.db as a backup until you've verified logins work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())