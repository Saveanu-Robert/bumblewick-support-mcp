"""Configuration for the Bumblewick support desk.

All knobs are environment-driven (prefix ``BUMBLEWICK_``) so the same server can
run with strict limits in production and looser ones in a demo. Keeping policy in
config — not scattered through the code — is one of the "do it correctly" lessons
of the series.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BUMBLEWICK_", env_file=".env", extra="ignore"
    )

    #: Path to the SQLite database file.
    db_path: str = "bumblewick.db"
    #: Refunds at or under this amount (cents) are auto-approved; larger ones
    #: require an explicit human-in-the-loop ``confirm=True``.
    auto_refund_limit_cents: int = 2000
    #: Maximum refunds a single customer may receive within the rolling window.
    refund_rate_limit: int = 3
    #: Length of the refund rate-limit window, in hours.
    refund_rate_window_hours: int = 24


def get_settings() -> Settings:
    """Return a fresh Settings instance (reads env / .env each call)."""
    return Settings()
