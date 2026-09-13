"""Measure where database time actually goes.

    python -m app.dbping

Run from backend/, with .env in place. Read-only: it issues SELECTs and one
rolled-back transaction, and writes nothing.

Why this exists. A single indexed query with LIMIT 60 was taking 2.3 seconds,
which is not query cost - it is round-trip cost, paid several times per
request. But "several times" was a guess, and so was "the region is far away".
This separates the three things that get confused with each other:

    connect        - TCP + TLS + Postgres startup to a fresh connection
    round trip     - SELECT 1 on an ALREADY OPEN connection. This is pure
                     network latency to your database and is the number that
                     multiplies by however many queries a request makes.
    query          - the real /conversations query, minus the round trip

If `round trip` is ~10-30ms you are near your database and the problem is the
NUMBER of queries per request. If it is 200ms+, the database is far away and
every fix worth making is about making fewer calls, not faster ones.

`pre_ping` is measured separately because database.py sets pool_pre_ping=True,
which issues a SELECT 1 before handing out a pooled connection - so it adds
one round trip to every checkout. It is there to drop dead connections rather
than error on them, so do not remove it on the strength of this number alone;
the point is to know what it costs.
"""

from __future__ import annotations

import statistics
import time

from sqlalchemy import create_engine, text

from .config import settings

REPEATS = 7


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:7.1f} ms"


def _report(label: str, samples: list[float], note: str = "") -> float:
    median = statistics.median(samples)
    print(
        f"  {label:<22} {_ms(median)}   "
        f"(min {_ms(min(samples)).strip()}, max {_ms(max(samples)).strip()})"
        f"{'  ' + note if note else ''}"
    )
    return median


def main() -> None:
    url = settings.database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set.")

    # Host and port only - never the password, which is in the same string.
    shown = url.split("@")[-1] if "@" in url else url
    print(f"\nDatabase: {shown}")
    if ":6543" in url:
        print("Mode:     transaction pooler (NullPool - a new connection per request)")
    elif url.startswith("sqlite"):
        print("Mode:     SQLite (local file - nothing here will be slow)")
    else:
        print("Mode:     session pooler / direct connection (client-side pool)")
    print()

    # --- 1. Cost of opening a brand-new connection -------------------------
    # Deliberately its own engine with no pooling, so each iteration really
    # does reconnect. This is what a NullPool setup pays on every request.
    cold = create_engine(url, pool_pre_ping=False)
    connects: list[float] = []
    for _ in range(3):
        t = time.perf_counter()
        with cold.connect() as conn:
            conn.execute(text("SELECT 1"))
        connects.append(time.perf_counter() - t)
    cold.dispose()
    _report("connect + 1 query", connects, "<- paid per request on :6543")

    # --- 2. Pure round trip on a warm connection ---------------------------
    warm = create_engine(url, pool_pre_ping=False)
    with warm.connect() as conn:
        conn.execute(text("SELECT 1"))          # warm it, don't measure it
        trips: list[float] = []
        for _ in range(REPEATS):
            t = time.perf_counter()
            conn.execute(text("SELECT 1"))
            trips.append(time.perf_counter() - t)
        rtt = _report("round trip (SELECT 1)", trips, "<- multiplies per query")

        # --- 3. The query that was taking 2.3 seconds ----------------------
        # Any user will do; this is about shape, not about whose rows they are.
        owner = conn.execute(
            text("SELECT id FROM users ORDER BY id LIMIT 1")
        ).scalar()
        if owner is None:
            print("  (no rows in users - skipping the /conversations query)")
        else:
            queries: list[float] = []
            for _ in range(REPEATS):
                t = time.perf_counter()
                conn.execute(
                    text(
                        "SELECT * FROM conversations WHERE user_id = :uid "
                        "ORDER BY updated_at DESC LIMIT 60"
                    ),
                    {"uid": owner},
                ).fetchall()
                queries.append(time.perf_counter() - t)
            q = _report("/conversations query", queries)
            work = max(q - rtt, 0.0)
            print(
                f"    of which network ~{_ms(rtt).strip()}, "
                f"actual database work ~{_ms(work).strip()}"
            )
    warm.dispose()

    # --- 4. What pool_pre_ping adds ---------------------------------------
    pinged = create_engine(url, pool_size=5, pool_pre_ping=True)
    checkouts: list[float] = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        with pinged.connect() as conn:      # pre_ping fires here
            pass
        checkouts.append(time.perf_counter() - t)
    pinged.dispose()
    _report("pooled checkout", checkouts, "<- pool_pre_ping cost")

    print(
        "\nReading this: multiply the round trip by the number of queries a\n"
        "request makes. get_current_user alone does a pre_ping, a possible\n"
        "GoTrue call, and ensure_profile's one-or-two queries before the\n"
        "endpoint's own query runs.\n"
    )


if __name__ == "__main__":
    main()