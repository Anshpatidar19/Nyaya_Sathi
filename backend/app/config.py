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

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./nyaya_sathi.db")

    class Config:
        env_file = ".env"


settings = Settings()
