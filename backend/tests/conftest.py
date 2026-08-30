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
# P0-3/P1-8: raise the per-IP auth-throttle limits for the suite. The
# whole suite drives the app from ONE TestClient source IP ("testclient")
# — at the shipped 10 signups/IP/day the ~60 registrations in
# test_multiuser.py would 429 after the first ten. The per-IP tests
# restore the shipped values per-test by monkeypatching BUCKETS (see
# TestPerIpAuthThrottles). TRUST_PROXY_HEADERS stays at its False default
# so the header-spoofing gate is tested in its safe configuration.
os.environ.setdefault("AUTH_REGISTER_IP_PER_DAY", "1000")
os.environ.setdefault("AUTH_LOGIN_IP_PER_15MIN", "1000")


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
    """
    from alembic.config import Config

    from alembic import command

    ini = pathlib.Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.stamp(cfg, "head")


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
