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

    if bound != TEST_DB:
        raise SystemExit(
            f"Refusing to run: the SQLAlchemy engine bound to {bound!r}, "
            f"not {TEST_DB!r}.\nThe suite calls drop_all() — against a real "
            "database that is unrecoverable. A test module is very likely "
            "setting DATABASE_URL at import time; conftest.py owns it."
        )
    live = (pathlib.Path(__file__).resolve().parent.parent / "jobfinderos.db").resolve()
    if pathlib.Path("./test_suite.db").resolve() == live:
        raise SystemExit("Refusing to run: the test DB path resolves to the live database.")
