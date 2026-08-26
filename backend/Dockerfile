# JobFinderOS backend worker — python:3.12-slim (official image guidance:
# keep prod images slim, never run as root)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

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

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
