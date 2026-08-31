"""
JobFinderOS — FastAPI Backend Entry Point

An operating system for job hunting: scrape job sites, match against the
CV on file, recommend, and (after approval) apply.

Built on the TalentHive foundation (github.com/AgenticTony/TalentHiv).
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text as sa_text

from app import users
from app.api import deps
from app.api.deps import set_user_context_middleware
from app.api.v1 import account, applications, jobs, matches, pipeline, profiles
from app.api.v1 import settings as settings_api
from app.core.config import settings
from app.core.database import init_db
from app.core.telemetry import init_sentry
from app.services.scheduler import start_scheduler, stop_scheduler

# Configure logging (TalentHive pattern)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

# Lifespan replaces the deprecated @app.on_event (FastAPI docs:
# fastapi.tiangolo.com/advanced/events/). init_db failure RAISES — a
# half-migrated schema must never serve traffic while /health says "up".
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    init_db()  # raises on failure — boot stops, deploy fails loudly
    logger.info("Database initialized successfully")
    # WO-04: the scheduler runs in the WORKER process (python -m
    # app.worker), never the API — two API replicas used to mean two
    # racing hunt cycles (D3). ENABLE_SCHEDULER stays as the dev-mode
    # single-process convenience (default false = production shape).
    start_scheduler()
    # Warm the occupation taxonomy table in the BACKGROUND — awaited
    # on the boot path it added up to SCRAPE_TIMEOUT_SECONDS (20s) to
    # every cold start during a taxonomy outage (review note). Failure
    # is tolerated: endpoints retry via threadpool on use.
    import asyncio

    from fastapi.concurrency import run_in_threadpool

    async def _warm_taxonomy() -> None:
        try:
            from app.services import occupation_taxonomy

            await run_in_threadpool(occupation_taxonomy._names)
        except Exception as e:  # noqa: BLE001 — warming is best-effort
            logger.warning("taxonomy warm-up failed (will retry on use): %s", e)

    _warm_task = asyncio.create_task(_warm_taxonomy())
    yield
    _warm_task.cancel()
    stop_scheduler()

init_sentry()  # no-op without SENTRY_DSN (WO-05 / F7)

app = FastAPI(lifespan=lifespan,
    title=settings.APP_NAME,
    description=(
        "Job hunting OS: scrape job sites -> AI-match against your CV -> "
        "recommend -> approve -> auto-apply. Built on the TalentHive engine."
    ),
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(profiles.router, prefix="/api/v1/profile", tags=["Profile"])
app.include_router(pipeline.router, prefix="/api/v1/pipeline", tags=["Pipeline"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["Jobs"])
app.include_router(matches.router, prefix="/api/v1/matches", tags=["Matches"])
app.include_router(applications.router, prefix="/api/v1/applications", tags=["Applications"])
app.include_router(settings_api.router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(account.router, prefix="/api/v1", tags=["Account"])

# Auth (fastapi-users v15 — see app/users.py). Register + JWT login + /users/me.
# The two pre-authentication endpoints carry rate-limit dependencies — the
# only routes an attacker can reach without an account.
app.include_router(
    users.fastapi_users.get_auth_router(users.auth_backend),
    prefix="/api/v1/auth/jwt",
    tags=["Auth"],
    dependencies=[Depends(deps.login_rate_limit)],
)
app.include_router(
    users.fastapi_users.get_register_router(users.UserRead, users.UserCreate),
    prefix="/api/v1/auth",
    tags=["Auth"],
    dependencies=[Depends(deps.register_rate_limit)],
)
app.include_router(
    users.fastapi_users.get_users_router(users.UserRead, users.UserUpdate),
    prefix="/api/v1/users",
    tags=["Auth"],
)


app.middleware("http")(set_user_context_middleware)


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    """Baseline security headers on every response (external verification
    pass 2: the API shipped none). The frontend is a static export on a
    different origin, so its headers live in frontend/public/_headers —
    Next cannot add them at runtime under output:'export'."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Ignored by browsers over plain HTTP (local dev); enforced on the
    # Render HTTPS origin. Long max-age is safe: the API is API-only.
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/health", tags=["Ops"])
def health():
    """Liveness + database readiness for uptime monitors and deploy checks."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(sa_text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    finally:
        db.close()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "version": settings.APP_VERSION,
    }




@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "healthy",
        "docs": "/docs",
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors (TalentHive pattern)."""
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
