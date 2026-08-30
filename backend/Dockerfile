# JobFinderOS API (web) image — python:3.12-slim (official image guidance:
# keep prod images slim, never run as root). The one-shot hunt image is
# Dockerfile.hunt (WO-07: same layers, different CMD).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# DejaVu core fonts (AI-12): python:3.12-slim ships NO fonts at all, so
# pdf_service.py's FONT_CANDIDATES resolved nothing and employer-facing
# PDFs fell back to latin-1 "replace" — every em-dash/curly quote in live
# GLM letters rendered as "?" (5 substitutions in one short paragraph).
# fonts-dejavu-core provides exactly the first Linux candidate
# (/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf) and nothing else;
# apt lists cleaned in the same layer to keep the slim image slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Locked dependencies only — reproducible builds (lockfile committed)
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Application code (alembic/ must ship for boot migrations)
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

# Non-root runtime user
RUN useradd --create-home --uid 10001 jobfinder && chown -R jobfinder /app
USER jobfinder

# Render sets PORT (default 10000) on web services and recommends binding
# to it (render.com/docs/web-services); local docker runs keep 8000.
EXPOSE 8000
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
