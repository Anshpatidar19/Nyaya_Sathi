import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "dev-only-insecure-secret")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    indian_kanoon_api_token: str = os.getenv("INDIAN_KANOON_API_TOKEN", "")
    indian_kanoon_base_url: str = os.getenv(
        "INDIAN_KANOON_BASE_URL", "https://api.indiankanoon.org"
    )

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    # Model used to READ scanned pages, photos and handwriting (OCR). Empty
    # means "same as GEMINI_MODEL". Set it to a non-lite Flash model if
    # handwriting accuracy matters more than cost - only uploads that
    # actually need OCR use it; text PDFs and Word files never call it.
    gemini_ocr_model: str = os.getenv("GEMINI_OCR_MODEL", "")

    # --- Groq fallback -----------------------------------------------------
    # Groq is NOT a second primary. It is called only when Gemini returns a
    # 503 / UNAVAILABLE ("model is experiencing high demand") on the original
    # call AND on one backed-off retry. Any other Gemini error (400, 401, 403,
    # 429, parse failures) is raised exactly as before.
    #
    # With GROQ_API_KEY unset, or LLM_FALLBACK_ENABLED=0, the fallback is off
    # and a double 503 fails the request the way it always did.
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    # llama-3.3-70b-versatile was shut down for free/dev tiers on 16 Aug 2026;
    # gpt-oss-120b is Groq's recommended replacement.
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    llm_fallback_enabled: bool = os.getenv("LLM_FALLBACK_ENABLED", "1") not in ("0", "false", "False")
    # Base delay before the single Gemini retry. Actual wait is this plus up
    # to 50% jitter, so two concurrent requests don't retry in lockstep.
    gemini_retry_base_delay: float = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "1.0"))
    # Quota (429) fallback. On a 429 Gemini is NOT retried - that only spends
    # another request against a limit already hit - the call goes straight to
    # Groq and Gemini is skipped for a cooldown: the retryDelay Gemini sends,
    # or 60s if it sends none, capped at GEMINI_QUOTA_COOLDOWN_MAX. A daily
    # limit uses the full cap. When the cooldown ends Gemini is tried first
    # again, so it returns as primary as soon as the quota resets.
    # LLM_FALLBACK_ENABLED=0 still switches off both fallbacks.
    llm_quota_fallback_enabled: bool = os.getenv("LLM_QUOTA_FALLBACK_ENABLED", "1") not in ("0", "false", "False")
    gemini_quota_cooldown_max: float = float(os.getenv("GEMINI_QUOTA_COOLDOWN_MAX", "300"))
    # DEVELOPMENT ONLY - makes Gemini calls fail with a fake 503 before any
    # network request is sent, so the fallback can be tested without burning
    # quota. Leave empty in any real deployment.
    #   once      - first attempt fails, the retry reaches Gemini for real
    #   always    - both attempts fail, so Groq answers
    #   quota     - every Gemini call fails with a fake 429 (30s retryDelay),
    #               so Groq answers and the cooldown starts
    #   midstream - streaming calls fail AFTER a few words have been sent
    #               (tests the no-duplicate-text path); buffered calls
    #               behave like "always"
    gemini_simulate_503: str = os.getenv("GEMINI_SIMULATE_503", "")

    # --- Answer cache ------------------------------------------------------
    # A finished answer is stored in Postgres and replayed when the same
    # question is asked again, which turns a ~10s answer into well under a
    # second. Only plain first-turn questions are cached - never one with a
    # document, a matter, or earlier turns behind it.
    #
    # ANSWER_CACHE=0 switches it off entirely (nothing read, nothing
    # written). TTL is in days: statutes and judgments don't change often,
    # but a stale answer should not live forever. MAX_ROWS caps the table -
    # the least recently used rows are pruned past it.
    answer_cache_enabled: bool = os.getenv("ANSWER_CACHE", "1") not in ("0", "false", "False")
    answer_cache_ttl_days: float = float(os.getenv("ANSWER_CACHE_TTL_DAYS", "7"))
    answer_cache_max_rows: int = int(os.getenv("ANSWER_CACHE_MAX_ROWS", "500"))
    # Answers are replayed as a stream so the reader sees the same typing
    # effect as a live answer, rather than a wall of text appearing at once.
    # 0 sends the whole body in one go.
    answer_cache_replay_cps: int = int(os.getenv("ANSWER_CACHE_REPLAY_CPS", "900"))

    # --- Usage / cost logging ----------------------------------------------
    # Every model call prints an LLM_CALL line in the backend terminal, and
    # every request that used a model prints a QUERY_COST total - see
    # usage_log.py. USAGE_LOG=0 switches both off. USAGE_LOG_FORMAT=json
    # prints one JSON object per line instead of key=value, for piping into
    # jq or a spreadsheet.
    usage_log: bool = os.getenv("USAGE_LOG", "1") not in ("0", "false", "False")
    usage_log_format: str = os.getenv("USAGE_LOG_FORMAT", "kv")
    # Rupees per dollar for estimated_cost_inr. Exchange rates move; set the
    # day's rate in .env if the INR column needs to be exact.
    usd_inr_rate: float = float(os.getenv("USD_INR_RATE", "88.0"))
    # Optional price overrides, USD per 1M tokens as [input, output], e.g.
    # LLM_PRICES_JSON={"gemini-3.1-flash-lite": [0.25, 1.50]}
    # Defaults live in usage_log._DEFAULT_PRICES.
    llm_prices_json: str = os.getenv("LLM_PRICES_JSON", "")

    # Supabase Postgres. Session pooler URI from
    # Project Settings -> Database -> Connection string -> URI
    database_url: str = os.getenv("DATABASE_URL", "")

    # Supabase Storage
    supabase_url: str = os.getenv("SUPABASE_URL", "")          # https://<ref>.supabase.co
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    # Public key, used for signup/login calls. Safe to expose.
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    # Dashboard -> Project Settings -> API -> JWT Settings -> "JWT Secret".
    # Optional, but strongly recommended: with this set, get_current_user
    # verifies a token's signature locally instead of calling Supabase's
    # Auth API over the network on every request. That network call is what
    # made every authenticated endpoint - not just chat history - slow.
    # Leave unset and the app still works, just falls back to the network
    # check (with its own short cache) exactly as before.
    supabase_jwt_secret: str = os.getenv("SUPABASE_JWT_SECRET", "")
    # Where Supabase sends users after they click a confirmation or reset link.
    site_url: str = os.getenv("SITE_URL", "http://localhost:5173")  # service_role key
    supabase_bucket: str = os.getenv("SUPABASE_BUCKET", "user-documents")

    # Profile pictures. A SEPARATE, PUBLIC bucket - on purpose.
    #
    # `supabase_bucket` above is private and every download goes through a
    # signed URL, which is right for an FIR copy or a property deed. An
    # avatar is different: it is shown to every user who searches the
    # directory, and signing one URL per row would add ~30 round trips to
    # Supabase for a single page of search results. Keeping them in their own
    # public bucket means the legal documents bucket stays private and
    # nothing in it is ever served by a plain URL.
    supabase_avatar_bucket: str = os.getenv("SUPABASE_AVATAR_BUCKET", "avatars")

    # --- Vector retrieval --------------------------------------------------
    # Pinecone holds embeddings of the bare acts. Everything here is optional:
    # with no key set the dense fallback stays off and retrieval is BM25 only,
    # exactly as before.
    #
    # (This block used to be declared twice, identically, further down. Two
    # copies of the same pydantic field is harmless but means an edit to one
    # silently does nothing, so it is now declared once.)
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str = os.getenv("PINECONE_INDEX", "nyaya-sathi")
    pinecone_cloud: str = os.getenv("PINECONE_CLOUD", "aws")
    pinecone_region: str = os.getenv("PINECONE_REGION", "us-east-1")

    # Namespace for acts ingested from a third-party dataset. Separate from
    # the hand-checked `statutes` namespace on purpose: deleting this one
    # deletes exactly the extension corpus and nothing else, which is what
    # makes trying an ingest reversible.
    #
    # Worth being explicit about, because it is a common wrong assumption:
    # deleting a GIT BRANCH does not delete these vectors. Pinecone is
    # external state. Rollback is
    #     python -m app.ingest_hf_acts --rollback
    pinecone_ns_statute_ext: str = os.getenv("PINECONE_NS_STATUTE_EXT", "statutes-ext")

    # Comma-separated namespaces the dense fallback searches, in order. Empty
    # means [statutes, <pinecone_ns_statute_ext>]. Set it to just "statutes"
    # to switch the extension corpus off at read time without deleting it -
    # which is the fastest way to A/B whether it actually helped.
    pinecone_statute_namespaces: str = os.getenv("PINECONE_STATUTE_NAMESPACES", "")

    # Keep at 0 until the index is built. Turning it on beforehand makes the
    # first live query create an empty index, which blocks for about a minute
    # and then matches nothing.
    dense_fallback: bool = os.getenv("DENSE_FALLBACK", "0") not in ("0", "false", "False")

    # Measured with `python -m app.dense --calibrate`, not guessed. Coverage
    # is the signal that matters: it catches BM25 scoring a section highly on
    # one common word and getting the answer wrong.
    dense_min_score: float = float(os.getenv("DENSE_MIN_SCORE", "8.0"))
    dense_min_coverage: float = float(os.getenv("DENSE_MIN_COVERAGE", "0.34"))

    # --- Source links ------------------------------------------------------
    # Public base URL of THIS backend. Source cards whose Indian Kanoon page
    # hasn't been resolved yet link to <this>/sources/statute/<act>/<section>,
    # which redirects straight to the exact document. Must be reachable from
    # the user's browser; the default matches the frontend's default API URL.
    public_api_url: str = os.getenv("PUBLIC_API_URL", "http://127.0.0.1:8000")

    # --- Demo seed ---------------------------------------------------------
    # Password given to every seeded demo account. Only ever used by
    # `python -m app.seed_demo`; nothing at runtime reads it. Override it in
    # .env if you would rather not have the value sitting in the repo.
    demo_password: str = os.getenv("DEMO_PASSWORD", "DemoPass@2026")
    # Every seeded account's email is at this domain. example.com is
    # IANA-reserved and undeliverable, so a stray send can never reach a real
    # inbox, and it doubles as the marker for "this is not a real account".
    demo_email_domain: str = os.getenv("DEMO_EMAIL_DOMAIN", "example.com")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy backend/.env.example to backend/.env and "
        "paste your Supabase connection string."
    )