"""Fill the answer cache before a demo.

    python -m app.warm_cache                 # the questions on the Ask page
    python -m app.warm_cache --state "Madhya Pradesh"
    python -m app.warm_cache -f questions.txt
    python -m app.warm_cache --list          # what is cached now
    python -m app.warm_cache --clear

Each question is run through the real pipeline - retrieval, synthesis, the
grounding check - and the finished answer is stored, so the first person to
ask it on stage gets the fast path instead of paying for it live. Run it
once after a restart, or any time the statute data changes.

Questions already cached are skipped unless --force is passed.
"""

import argparse
import asyncio
import logging
import sys
import time
from typing import List, Optional

from . import answer_cache, models, reasoning
from .config import settings
from .database import SessionLocal

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("warm_cache")

# The chip questions on the Ask page, plus the ones a demo tends to reach
# for. Keep this in step with USER_CHIPS / CHIPS in frontend/src/pages/Ask.jsx.
DEFAULT_QUESTIONS = [
    "What is the Bharatiya Nyaya Sanhita, and how is it different from the IPC?",
    "What are my fundamental rights under the Constitution?",
    "How do I file an FIR, and what can I do if the police refuse to register one?",
    "My cheque bounced — what should I do now?",
    "I was involved in a road accident where the other driver was injured. "
    "What legal consequences could I face?",
    "How do I file a complaint in the consumer forum?",
    "How do I file an RTI application?",
    "What are my rights if I am arrested, and how does bail work?",
]


async def warm_one(question: str, state: Optional[str], force: bool) -> str:
    """Answer one question and store it. Returns a short status word."""
    db = SessionLocal()
    try:
        if not force:
            existing = await asyncio.to_thread(answer_cache.lookup, db, question, state)
            if existing is not None:
                return "already cached"

        t0 = time.perf_counter()
        answer = await reasoning.answer_question(question, state, [], None, None)
        took = time.perf_counter() - t0

        payload = answer.model_dump()
        if not (payload.get("body") or "").strip():
            return "no answer - not cached"

        await asyncio.to_thread(answer_cache.store, db, question, state, payload)
        return f"cached in {took:.1f}s ({len(answer.citations)} sources)"
    finally:
        db.close()


async def warm(questions: List[str], state: Optional[str], force: bool) -> None:
    print(f"Warming {len(questions)} question(s)"
          + (f" for state {state!r}" if state
             else " with no state (served to every user)") + "\n")
    for i, q in enumerate(questions, start=1):
        short = q if len(q) <= 68 else q[:65] + "..."
        print(f"  [{i}/{len(questions)}] {short}")
        try:
            print(f"        -> {await warm_one(q, state, force)}")
        except Exception as exc:
            print(f"        -> FAILED: {exc}")
        # A beat between questions: warming is not a load test, and a burst
        # of calls is the fastest way to a 429 right before a demo.
        if i < len(questions):
            await asyncio.sleep(1.0)
    print("\nDone. Check what is stored with: python -m app.warm_cache --list")


def show() -> None:
    db = SessionLocal()
    try:
        rep = answer_cache.report(db, limit=50)
    finally:
        db.close()
    print(f"Answer cache: {'on' if rep['enabled'] else 'OFF'}, "
          f"{rep['rows']} row(s), TTL {rep['ttl_days']} day(s)")
    print(f"{rep['asks']} ask(s), {rep['hits']} served from cache\n")
    if not rep["top"]:
        print("  (empty)")
        return
    for r in rep["top"]:
        short = r["question"] if len(r["question"]) <= 62 else r["question"][:59] + "..."
        print(f"  {r['asks']:>3} asked | {r['hits']:>3} served | "
              f"{r['sources']} src | {short}")


def clear() -> None:
    db = SessionLocal()
    try:
        n = db.query(models.CachedAnswer).delete()
        db.commit()
        print(f"Cleared {n} cached answer(s).")
    except Exception as exc:
        db.rollback()
        print(f"Could not clear the cache: {exc}")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", help="answer as if asked from this state. "
                                    "Omit it: a state-less entry is served to "
                                    "every user, whatever their profile says")
    ap.add_argument("-f", "--file", help="file with one question per line")
    ap.add_argument("-q", "--question", action="append",
                    help="a single question (repeatable)")
    ap.add_argument("--force", action="store_true",
                    help="re-answer questions that are already cached")
    ap.add_argument("--list", action="store_true", help="show what is cached")
    ap.add_argument("--clear", action="store_true", help="empty the cache")
    args = ap.parse_args()

    if args.list:
        show()
        return
    if args.clear:
        clear()
        return

    if not settings.answer_cache_enabled:
        print("ANSWER_CACHE is off in .env - nothing would be stored. Set "
              "ANSWER_CACHE=1 and run again.")
        sys.exit(1)
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set, so no answer can be generated.")
        sys.exit(1)

    questions = list(args.question or [])
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            questions += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not questions:
        questions = DEFAULT_QUESTIONS

    asyncio.run(warm(questions, args.state, args.force))


if __name__ == "__main__":
    main()