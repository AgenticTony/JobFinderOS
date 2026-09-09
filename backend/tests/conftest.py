"""Test session bootstrap — MUST be imported before any app module.

Why this file exists
--------------------
Test DB selection used to depend on which test module pytest imported
first: each module called os.environ.setdefault("DATABASE_URL", ...), so
only the first one to run actually took effect, and every other module
silently inherited it. That is fragile in a way with real consequences —
tests/test_units.py's fixture calls Base.metadata.drop_all(), so whichever
database the session happens to bind to gets DROPPED.

During this change the ordering shifted and the session bound to the real
backend/jobfinderos.db. drop_all() then deleted the live development
data (243 matches, 399 postings — restored from backup).

conftest.py is imported by pytest before any test module, so setting the
URL here makes the choice deterministic instead of collection-order
dependent. The guard below then makes the dangerous case impossible
rather than merely unlikely.
"""

import os
import pathlib

# CI's Postgres leg injects TEST_DATABASE_URL (a THROWAWAY database — the
# suite drop_all()s it). Locally, unset -> the SQLite scratch file. Never
# point this at anything you want to keep; the guard below still refuses
# to run if the engine binds to anything other than exactly this URL.
TEST_DB = os.environ.get("TEST_DATABASE_URL") or "sqlite:///./test_suite.db"

# Set BEFORE app.core.config / app.core.database are imported anywhere.
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("GLM_API_KEY", "")
os.environ["DEBUG"] = "true"  # tests run with production guards relaxed
# WO-02: the per-draft fabrication judge is OFF for the suite by
# default — draft tests script Layer A and must not spend judge calls.
# TestProductionJudge opts in per-test.
os.environ.setdefault("FABRICATION_JUDGE", "off")
# MIG-WO2: deterministic Supabase URL for auth verification — the JWKS
# fetch is monkeypatched (never leaves the machine), but app.users bakes
# the token ISSUER from this at import; a fixed value keeps the suite
# independent of whatever backend/.env carries on a dev machine.
# (The old per-IP auth-throttle env lifts died with the settings they
# raised — signup/login moved to Supabase Auth.)
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")


import pytest


@pytest.fixture(autouse=True)
def _supabase_admin_stub(monkeypatch):
    """MIG-WO2: GDPR erasure calls the Supabase admin API — the suite
    never leaves the machine. Erasure-ordering tests override this with
    tests.auth_helpers.stub_supabase_delete(monkeypatch, result=False)."""
    from app.services import supabase_admin

    monkeypatch.setattr(supabase_admin, "delete_user", lambda uid: True)


def stamp_alembic_head() -> None:
    """Record the current ORM metadata shape as alembic head.

    Modules that rebuild the schema with Base.metadata.create_all (the
    per-file db fixtures in test_delta/test_radius/test_taxonomy) leave a
    HEAD-shaped schema with NO alembic_version row. The next app boot in
    the same session (a TestClient lifespan -> init_db) then misreads it:

    - sqlite: init_db stamps the table-shape at the INITIAL revision and
      the per-user-FK batch migration chokes on the already-new column
      order (sqlalchemy CircularDependencyError) — 33 setup errors when
      the full suite runs against a fresh scratch file.
    - postgres: init_db replays every migration from scratch against the
      existing tables (DuplicateTable) — 71 errors.

    create_all builds the CURRENT metadata, which is the head shape, so
    stamping head after it is the truthful record. Alembic owns the
    schema on both backends; this keeps its version table honest when a
    test fixture rebuilds what alembic would have built.

    MIG-WO3: create_all reproduces every TABLE but no migration ever
    runs — the RLS layer (policies/grants) would silently vanish for
    the rest of the suite, and request sessions would run as
    `authenticated` against ungranted tables (permission denied). The
    same single-source layer the migration runs is applied right here.
    """
    from alembic.config import Config

    from alembic import command

    ini = pathlib.Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.stamp(cfg, "head")

    from sqlalchemy import create_engine

    from app.core.rls_sql import ensure_rls

    eng = create_engine(TEST_DB)
    try:
        with eng.begin() as conn:
            ensure_rls(conn)
    finally:
        eng.dispose()


def pytest_collection_modifyitems(session, config, items):
    """Refuse to run if the ENGINE bound to anything but the test database.

    Checking os.environ here would be theatre — this module already
    overwrote it above, so the variable always matches. The invariant that
    actually matters is what app.core.database resolved at import time: a
    module that hard-sets DATABASE_URL before conftest, or an engine
    created against a different URL, is exactly how the live database got
    dropped. Import is deferred to this hook so the check runs AFTER every
    test module has been imported and had its chance to interfere.
    """
    from app.core.database import DATABASE_URL as bound
    from app.core.database import normalize_postgres_url

    # Compare normalized-to-normalized: app.core.database normalizes its URL
    # at import, so a bare-postgresql TEST_DATABASE_URL would otherwise
    # false-refuse (bound +psycopg != TEST_DB bare) and block the suite.
    if bound != normalize_postgres_url(TEST_DB):
        raise SystemExit(
            f"Refusing to run: the SQLAlchemy engine bound to {bound!r}, "
            f"not {TEST_DB!r}.\nThe suite calls drop_all() — against a real "
            "database that is unrecoverable. A test module is very likely "
            "setting DATABASE_URL at import time; conftest.py owns it."
        )
    live = (pathlib.Path(__file__).resolve().parent.parent / "jobfinderos.db").resolve()
    if pathlib.Path("./test_suite.db").resolve() == live:
        raise SystemExit("Refusing to run: the test DB path resolves to the live database.")
