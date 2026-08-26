"""
Configuration settings for JobFinderOS.
Loads from environment variables using pydantic-settings.

Reuses the TalentHive AI setup: same GLM_API_KEY and Z.ai endpoint,
so an existing TalentHive key works unchanged.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "JobFinderOS"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # Database — SQLite by default for zero-config local use.
    # Set DATABASE_URL to a PostgreSQL URL for production.
    DATABASE_URL: str = "sqlite:///./jobfinderos.db"

    # AI — same Z.ai endpoint as TalentHive, model tuned for speed.
    # glm-4.6 + thinking disabled: ~5s per match vs 75-90s with reasoning on.
    # Set GLM_THINKING=enabled for deeper (much slower) reasoning.
    GLM_API_KEY: str = ""
    GLM_BASE_URL: str = "https://api.z.ai/api/coding/paas/v4"
    GLM_MODEL: str = "glm-5.1"
    GLM_THINKING: str = "disabled"  # disabled | enabled

    # Email applications (Resend — same provider TalentHive used)
    RESEND_API_KEY: str = ""
    APPLY_FROM_EMAIL: str = ""

    # Matching
    MAX_JOBS_PER_MATCH_RUN: int = 25  # glm-4.6 no-thinking: ~5-10s per job
    MATCH_KEEP_MIN_SCORE: int = 25  # below this a match never enters the queue
    MATCH_STALE_DAYS: int = 30  # pending matches older than this are auto-passed
    MAX_POSTING_AGE_DAYS: int = 30  # postings older than this are never stored
    MATCH_TIME_BUDGET_SECONDS: int = 420  # hard stop; frontend pipeline timeout is 600s
    COMPOSIO_API_KEY: str = ""  # integrations layer (Settings page)

    # Auth (fastapi-users) — generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    AUTH_SECRET: str = "dev-insecure-secret-change-me"
    AUTH_TOKEN_LIFETIME_SECONDS: int = 3600 * 24 * 7  # 7 days

    # CV storage backend: "local" (disk) or "supabase" (official REST, docs:
    # supabase.com/docs/guides/storage/uploads). Vercel Blob was rejected:
    # no officially documented REST API.
    STORAGE_BACKEND: str = "local"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "cvs"

    # Scraping
    SCRAPE_SOURCES: str = "arbeitnow,remotive,jobicy,workingnomads,jobtech,teamtailor"
    SCRAPE_TIMEOUT_SECONDS: int = 20

    # Sweden — JobTech / Platsbanken (Arbetsförmedlingen's open API).
    # Keyless works for light use; get a free key at jobtechdev.se for production.
    # Queries are PER-USER and derived from their CV during onboarding — a nurse
    # gets "undersköterska, vårdcentral", a developer gets "utvecklare". This
    # global default only applies before onboarding has run; empty = wait for
    # onboarding rather than scrape the wrong profession's jobs.
    JOBTECH_API_KEY: str = ""
    JOBTECH_QUERIES: str = ""

    # Sweden — Teamtailor career sites ({slug}.teamtailor.com/jobs.json).
    # Comma-separated slugs, e.g. "manpowerse,fortnoxab". Empty = source skipped.
    TEAMTAILOR_SITES: str = ""

    # UK — Reed.co.uk jobs-search API (key = basic-auth username, empty password).
    # Keywords/location become per-user at onboarding; these are manual overrides.
    # Empty keywords + a location = ALL professions in that area (neutral default).
    REED_API_KEY: str = ""
    REED_KEYWORDS: str = ""
    REED_LOCATION: str = ""
    REED_DISTANCE_MILES: int = 20

    # UK — Adzuna jobs API (free keys at developer.adzuna.com)
    ADZUNA_APP_ID: str = ""
    ADZUNA_APP_KEY: str = ""

    # SE + GB — Careerjet aggregator (basic-auth API key; serves both countries
    # via locale codes sv_SE / en_GB). The Referer must match the website
    # declared in the Careerjet partner portal, and user_ip must be declared
    # there too.
    CAREERJET_API_KEY: str = ""
    CAREERJET_REFERER: str = "https://github.com/AgenticTony"

    ENABLE_SCHEDULER: bool = False
    SCRAPE_INTERVAL_MINUTES: int = 60

    # CORS
    CORS_ORIGINS: str = "*"

    def get_cors_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    def get_scrape_sources(self) -> List[str]:
        """Parse enabled scrape sources from comma-separated string."""
        return [s.strip().lower() for s in self.SCRAPE_SOURCES.split(",") if s.strip()]

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
