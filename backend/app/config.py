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

    # --- Vector retrieval (optional) --------------------------------------
    # Everything here can be left unset. With no PINECONE_API_KEY the dense
    # fallback simply never runs and retrieval behaves exactly as before.
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str = os.getenv("PINECONE_INDEX", "nyaya-sathi")
    pinecone_cloud: str = os.getenv("PINECONE_CLOUD", "aws")
    pinecone_region: str = os.getenv("PINECONE_REGION", "us-east-1")

    # Keep at 0 until the index is built. Turning it on beforehand makes the
    # first live query create an empty index, which blocks for about a minute
    # and then matches nothing.
    dense_fallback: bool = os.getenv("DENSE_FALLBACK", "0") not in ("0", "false", "False")

    # Measured with `python -m app.dense --calibrate`, not guessed. Coverage
    # is the signal that matters: it catches BM25 scoring a section highly on
    # one common word and getting the answer wrong.
    dense_min_score: float = float(os.getenv("DENSE_MIN_SCORE", "8.0"))
    dense_min_coverage: float = float(os.getenv("DENSE_MIN_COVERAGE", "0.34"))

    # --- Vector retrieval ------------------------------------------------
    # Pinecone holds embeddings of the bare acts. Everything here is optional:
    # with no key set, the dense fallback stays off and retrieval is BM25 only,
    # exactly as before.
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    pinecone_index: str = os.getenv("PINECONE_INDEX", "nyaya-sathi")
    pinecone_cloud: str = os.getenv("PINECONE_CLOUD", "aws")
    pinecone_region: str = os.getenv("PINECONE_REGION", "us-east-1")

    # Leave at 0 until the index has actually been built - see
    # `python -m app.ingest_vectors --statutes`. Turning it on before then
    # makes every query create an empty index and burn embedding calls.
    dense_fallback: bool = os.getenv("DENSE_FALLBACK", "0") not in ("0", "false", "False")
    # Thresholds from `python -m app.dense --calibrate`, not guesswork.
    dense_min_score: float = float(os.getenv("DENSE_MIN_SCORE", "8.0"))
    dense_min_coverage: float = float(os.getenv("DENSE_MIN_COVERAGE", "0.34"))

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy backend/.env.example to backend/.env and "
        "paste your Supabase connection string."
    )