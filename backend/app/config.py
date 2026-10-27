"""Settings, read from environment variables (or backend/.env when running locally)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neon pooled connection string (host contains "-pooler"). Used for everything the API does.
    database_url: str
    # Neon direct connection string. Only scripts/apply_sql.py needs it.
    direct_database_url: str | None = None

    # The dashboard (Next.js on Vercel) sends this in the X-API-Key header.
    api_key: str

    # The fraud analyst that approve / reject decisions are recorded under (Neha Iyer in the seed data).
    analyst_user_id: str = "a0000000-0000-0000-0000-000000000006"

    # Browsers never call this API directly (Next.js proxies it), so CORS can stay closed.
    # Set to a comma separated list if you want to call the API from another origin.
    allowed_origins: str = ""

    # Housekeeping + notification delivery loop inside the API process.
    background_jobs: bool = True
    background_interval_seconds: int = 60
    # Chance that a simulated notification delivery fails (exercises retry / backoff / dead letters).
    notify_failure_rate: float = 0.0


settings = Settings()
