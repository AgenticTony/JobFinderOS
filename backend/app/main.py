"""
JobFinderOS — FastAPI Backend Entry Point

An operating system for job hunting: scrape job sites, match against the
CV on file, recommend, and (after approval) apply.

Built on the TalentHive foundation (github.com/AgenticTony/TalentHiv).
"""

import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text as sa_text

from app import users
from app.api.v1 import applications, jobs, matches, pipeline, profiles
from app.api.v1 import settings as settings_api
from app.core.config import settings
from app.core.database import init_db
from app.services.scheduler import start_scheduler, stop_scheduler

# Configure logging (TalentHive pattern)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

app = FastAPI(
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

# Auth (fastapi-users v15 — see app/users.py). Register + JWT login + /users/me.
app.include_router(
    users.fastapi_users.get_auth_router(users.auth_backend),
    prefix="/api/v1/auth/jwt",
    tags=["Auth"],
)
app.include_router(
    users.fastapi_users.get_register_router(users.UserRead, users.UserCreate),
    prefix="/api/v1/auth",
    tags=["Auth"],
)
app.include_router(
    users.fastapi_users.get_users_router(users.UserRead, users.UserUpdate),
    prefix="/api/v1/users",
    tags=["Auth"],
)


@app.get("/health", tags=["Ops"])
async def health():
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


@app.on_event("startup")
async def startup_event():
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database: %s", e)
    try:
        start_scheduler()
    except Exception as e:
        logger.error("Failed to start scheduler: %s", e)


@app.on_event("shutdown")
async def shutdown_event():
    stop_scheduler()


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
