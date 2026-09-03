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
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")  # service_role key
    supabase_bucket: str = os.getenv("SUPABASE_BUCKET", "user-documents")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy backend/.env.example to backend/.env and "
        "paste your Supabase connection string."
    )