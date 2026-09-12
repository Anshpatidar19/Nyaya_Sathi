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