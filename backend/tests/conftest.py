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

TEST_DB = "sqlite:///./test_suite.db"

# Set BEFORE app.core.config / app.core.database are imported anywhere.
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("GLM_API_KEY", "")
os.environ["DEBUG"] = "true"  # tests run with production guards relaxed


def pytest_configure(config):
    """Refuse to run if anything re-pointed the suite at a real database.

    drop_all() against a developer's or production database is
    unrecoverable without a backup. This is cheap insurance.
    """
    url = os.environ.get("DATABASE_URL", "")
    if url != TEST_DB:
        raise SystemExit(
            f"Refusing to run tests against DATABASE_URL={url!r}.\n"
            f"The suite drops and recreates tables; it must use {TEST_DB}."
        )
    live = pathlib.Path(__file__).resolve().parent.parent / "jobfinderos.db"
    if live.exists() and live.samefile(live):  # path exists; ensure we are not it
        target = pathlib.Path("./test_suite.db").resolve()
        if target == live.resolve():
            raise SystemExit("Refusing to run: test DB resolves to the live database.")
