"""
Configuration settings for JobFinderOS.
Loads from environment variables using pydantic-settings.

Reuses the TalentHive AI setup: same GLM_API_KEY and Z.ai endpoint,
so an existing TalentHive key works unchanged.
"""

from functools import lru_cache
from typing import List

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "JobFinderOS"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Database — SQLite by default for zero-config local use.
    # Set DATABASE_URL to a PostgreSQL URL for production.
    DATABASE_URL: str = "sqlite:///./jobfinderos.db"

    # AI — same Z.ai endpoint as TalentHive, model tuned for speed.
    # glm-4.6 + thinking disabled: ~5s per match vs 75-90s with reasoning on.
    # Set GLM_THINKING=enabled for deeper (much slower) reasoning.
    GLM_API_KEY: str = ""
    GLM_BASE_URL: str = "https://api.z.ai/api/coding/paas/v4"
    # Mistral — the EU-resident inference path (MIGRATION.md MIG-WO5).
    # Mistral's OWN models were rejected for matching (they keep nearly
    # everything: mistral-large scored 45-58 on jobs glm-5.1 scored 18-22).
    # This config is NOT for those models — it is pre-positioned for routing
    # GLM through Mistral's EU regional endpoint, which is verified non-proxy
    # (Z.ai is absent from Mistral's sub-processor list) but currently
    # tier-gated. Set MISTRAL_BASE_URL to https://api.eu.mistral.ai/v1 when
    # that unlocks — the default global endpoint is NOT region-pinned.
    #
    # Declared here because Settings forbids extra inputs: an undeclared key
    # in .env stops the app importing at ALL — the running process survives
    # on its old config while every new process (launchd restart, pytest,
    # scripts, deploy) fails.
    MISTRAL_API_KEY: str = ""
    MISTRAL_BASE_URL: str = "https://api.mistral.ai/v1"
    GLM_MODEL: str = "glm-5.1"
    GLM_THINKING: str = "disabled"  # disabled | enabled

    # Email applications (Resend — same provider TalentHive used)
    RESEND_API_KEY: str = ""
    APPLY_FROM_EMAIL: str = ""

    # Matching
    # Safety ceiling on AI EVALUATIONS per run — a spend guard, never a
    # throughput limit. Candidates are selected newest-first through a
    # much larger window (MATCH_CANDIDATE_WINDOW) and cheap-gated
    # (language, dedupe, exclude keywords, no-description) BEFORE this
    # cap applies, so no plausible ad is starved. History: shipped at 25
    # as a raw SQL LIMIT placed before the gates — plausible ads aged
    # out unevaluated (the dream-job starvation bug).
    MAX_JOBS_PER_MATCH_RUN: int = 200
    # Never-evaluated candidates loaded per run (newest first) for cheap
    # pre-filtering before AI evaluation.
    MATCH_CANDIDATE_WINDOW: int = 500
    # WO-02: emergency cost lever — 'off' skips the per-draft
    # fabrication judge (Layer A still guards). Default: on.
    FABRICATION_JUDGE: str = "on"
    # WO-05: empty = telemetry off (no-op init)
    SENTRY_DSN: str = ""
    ENVIRONMENT: str = "development"  # glm-4.6 no-thinking: ~5-10s per job
    MATCH_KEEP_MIN_SCORE: int = 25  # below this a match never enters the queue
    # Dead-band floor. Scores in [DEADBAND, KEEP_MIN) are re-scored once and
    # AVERAGED before the keep/dismiss call. 50 pooled samples put real noise
    # at SD 5.5, ±11 at 95% — the band must cover that below the keep line,
    # so DEADBAND = KEEP_MIN − 2×SD = 25 − 12 = 13. Below the floor a job
    # is confidently bad and never pays for a second AI call. (Averaging,
    # not "leave as new": re-queuing would re-score the same borderline
    # jobs on every run forever, unbounded.)
    MATCH_DEADBAND_MIN_SCORE: int = 13
    MATCH_STALE_DAYS: int = 30  # pending matches older than this are auto-passed
    MAX_POSTING_AGE_DAYS: int = 30  # postings older than this are never stored
    MATCH_TIME_BUDGET_SECONDS: int = 420  # hard stop; frontend pipeline timeout is 600s
    COMPOSIO_API_KEY: str = ""  # integrations layer (Settings page)

    # Auth (fastapi-users) — generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
    AUTH_SECRET: str = "dev-insecure-secret-change-me"
    AUTH_TOKEN_LIFETIME_SECONDS: int = 3600 * 24 * 7  # 7 days

    # Per-IP auth throttles (P0-3/P1-8, live-confirmed): the email/account
    # auth buckets are keyed by ATTACKER-CHOSEN strings, so distinct-email
    # signup bursts and distinct-account password sprays needed a per-IP
    # layer. Limits are settings (not constants) purely so the test suite
    # — every request from one TestClient source IP — can raise them
    # (tests/conftest.py); windows are fixed in core/ratelimit.py BUCKETS.
    AUTH_REGISTER_IP_PER_DAY: int = 10   # signups per source IP per day
    AUTH_LOGIN_IP_PER_15MIN: int = 30    # logins per source IP per 15 min
    # Honor proxy-supplied client-IP headers (True-Client-IP, then the
    # X-Forwarded-For first hop) for those per-IP buckets. Render's edge
    # proxies EVERY request, so render.yaml sets this true there; the
    # default stays FALSE because honoring these headers when the peer IS
    # the client lets attackers rotate fake IPs and bypass the throttle
    # (see app/api/deps.py _client_ip for the full decision).
    TRUST_PROXY_HEADERS: bool = False

    # CV storage backend: "local" (disk) or "supabase" (official REST, docs:
    # supabase.com/docs/guides/storage/uploads). Vercel Blob was rejected:
    # no officially documented REST API.
    STORAGE_BACKEND: str = "local"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "cvs"

    # Scraping
    SCRAPE_SOURCES: str = "arbeitnow,remotive,jobicy,workingnomads,jobtech"
    SCRAPE_TIMEOUT_SECONDS: int = 20

    # Sweden — JobTech / Platsbanken (Arbetsförmedlingen's open API).
    # Keyless works for light use; get a free key at jobtechdev.se for production.
    # Queries are PER-USER and derived from their CV during onboarding — a nurse
    # gets "undersköterska, vårdcentral", a developer gets "utvecklare". This
    # global default only applies before onboarding has run; empty = wait for
    # onboarding rather than scrape the wrong profession's jobs.
    JOBTECH_API_KEY: str = ""
    JOBTECH_QUERIES: str = ""


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
    # PIPE-18: hunt claim TTL override, minutes. Empty (default) = the
    # worker COMPUTES it from the worst case (scrape allowance + one
    # matching budget per onboarded user, floored at 45). Set only as an
    # ops escape hatch — a value smaller than a real hunt cycle invites
    # TTL-steal overlap.
    HUNT_CLAIM_TTL_MINUTES: int | None = None
    # The EXTERNAL cron's hunt times (UTC, comma list "HH:MM") — set when
    # hunts run via the render.yaml cron job instead of the dev scheduler.
    # /api/v1/pipeline/status uses it to report an honest next-run time;
    # empty = cadence unknown (dashboard shows "manual hunts only").
    HUNT_TIMES_UTC: str = ""

    # CORS
    # Explicit origins ONLY. With allow_credentials=True a wildcard origin is
    # reflected verbatim by Starlette, letting any website read the API from
    # the user's browser (CV/PII exfil). Production sets its real origin.
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    def get_cors_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    def get_scrape_sources(self) -> List[str]:
        """Parse enabled scrape sources from comma-separated string."""
        return [s.strip().lower() for s in self.SCRAPE_SOURCES.split(",") if s.strip()]

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        """Fail fast on insecure production config (never serve on the
        committed AUTH_SECRET; never reflect-echo CORS by accident). Both
        the API and the worker run this at import — BEFORE anything binds
        a port or claims a lock — so every guard here is process-wide."""
        import logging

        if not self.DEBUG:
            if self.AUTH_SECRET.startswith("dev-insecure") or len(self.AUTH_SECRET) < 32:
                raise ValueError(
                    "AUTH_SECRET must be set to a strong random value "
                    '(>=32 chars) when DEBUG=false — generate with '
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
            # r5: the cron's empty-DATABASE_URL incident, API variant —
            # WORSE. An unset/SQLite URL boots fine, /health answers
            # SELECT 1 with 200, the deploy goes healthy, and signups land
            # in an EPHEMERAL container file (gone on the next deploy). A
            # missing sync:false secret after service recreation produces
            # exactly this shape; refuse it at import in BOTH processes.
            if not self.DATABASE_URL.startswith("postgres"):
                raise ValueError(
                    "DATABASE_URL must be Postgres when DEBUG=false (got "
                    f"{self.DATABASE_URL!r}) — an unset URL silently "
                    "defaults to SQLite and accepts traffic into an "
                    "ephemeral file. This is the recreated-service/"
                    "missing-secret failure mode: fail at boot, not after "
                    "users sign up."
                )
            # OPS-7: the same recreation incident, storage variant. The
            # blueprint syncs STORAGE_BACKEND=supabase as a LITERAL (it
            # survives service recreation) while SUPABASE_* are sync:false
            # secrets (they do NOT — the runbook's documented incident).
            # Without this guard the app boots green and every CV
            # upload/delete 500s at runtime, the first time
            # SupabaseStorage needs the key. Keyed on the SELECTED backend,
            # not on production itself: local storage stays a legitimate
            # shape (storage.py: dev and single-user deploys on a real
            # disk), so DEBUG=false + STORAGE_BACKEND=local must not be
            # refused here.
            if self.STORAGE_BACKEND == "supabase" and (
                not self.SUPABASE_URL or not self.SUPABASE_SERVICE_KEY
            ):
                missing = ", ".join(
                    name
                    for name, value in (
                        ("SUPABASE_URL", self.SUPABASE_URL),
                        ("SUPABASE_SERVICE_KEY", self.SUPABASE_SERVICE_KEY),
                    )
                    if not value
                )
                raise ValueError(
                    "STORAGE_BACKEND=supabase requires SUPABASE_URL and "
                    f"SUPABASE_SERVICE_KEY to be set (missing: {missing}) "
                    "— a missing sync:false secret after service "
                    "recreation produces exactly this shape. Fail at "
                    "boot, not on the first CV upload."
                )
            if "*" in self.get_cors_origins():
                raise ValueError("CORS_ORIGINS=* is not allowed when DEBUG=false")
            # r4: an EMPTY prompt deploys fine and serves an app no origin
            # can call ("".split(",") == [""]); a scheme-less entry does
            # the same per-origin. Refuse both at boot, not in the browser.
            origins = self.get_cors_origins()
            if not origins or any(
                    not o or not o.startswith(("http://", "https://"))
                    for o in origins):
                raise ValueError(
                    "CORS_ORIGINS must be a non-empty list of http(s):// "
                    "origins when DEBUG=false (got "
                    f"{self.CORS_ORIGINS!r}) — e.g. "
                    "https://jobfinderos.pages.dev"
                )
        else:
            logging.getLogger(__name__).warning(
                "DEBUG=true — development mode (relaxed auth/CORS guards)"
            )
        return self


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
