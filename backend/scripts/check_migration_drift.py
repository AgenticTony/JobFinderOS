"""WO-25 layer 2 — does production's migration stamp exist in this build?

Run by .github/workflows/migration-drift.yml on push to main, on PRs into
main ("would merging this strand production?") and on a schedule (drift
made outside Git — a laptop `alembic upgrade` against production — is
caught between merges).

    python scripts/check_migration_drift.py

1. Always: the checked-out alembic graph has exactly one head.
2. If DRIFT_CHECK_DATABASE_URL is set: read the target's alembic_version
   and fail when a stamped revision is unknown to this build. Behind head
   is fine (the next main deploy applies it). ZERO rows fails too: under
   RLS a reader without a policy sees an empty table, and treating that
   as "fresh database" would pass exactly when the check can't see.
   Unset (fork/dependabot PRs have no secrets) -> skipped, exit 0.

Standalone by design: no app settings import (their production guards
would demand SUPABASE_URL etc. from a CI step that only has a URL). The
URL is never printed — only its host.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402

from app.core.dburl import normalize_postgres_url  # noqa: E402
from app.core.migration_guard import (  # noqa: E402
    MigrationGuardError,
    assert_single_head,
    describe_unknown,
    revision_state,
)

URL_ENV = "DRIFT_CHECK_DATABASE_URL"


def script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def read_stamp(url: str) -> list[str]:
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    eng = create_engine(normalize_postgres_url(url), poolclass=NullPool)
    try:
        with eng.connect() as conn:
            return [r[0] for r in conn.execute(
                text("SELECT version_num FROM alembic_version"))]
    finally:
        eng.dispose()


def locate_revision(rev: str, repo: Path = BACKEND.parent) -> str | None:
    """Best effort: which remote branch introduced `rev`? None if unknown.

    Needs full history (the workflow checks out with fetch-depth 0)."""
    try:
        commits = subprocess.run(
            ["git", "log", "--all", "--format=%h", "-S", rev, "--",
             "backend/alembic/versions"],
            cwd=repo, capture_output=True, text=True, timeout=60,
        ).stdout.split()
        if not commits:
            return None
        branches = subprocess.run(
            ["git", "branch", "-r", "--contains", commits[-1]],
            cwd=repo, capture_output=True, text=True, timeout=60,
        ).stdout.split()
        return (f"introduced in {commits[-1]}; on "
                + (", ".join(branches) if branches else "no remote branch"))
    except (OSError, subprocess.SubprocessError):
        return None


def check(url: str | None, script: ScriptDirectory | None = None) -> tuple[bool, str]:
    """(ok, message). Pure enough to test against a scratch database."""
    script = script or script_directory()
    try:
        head = assert_single_head(script)
    except MigrationGuardError as e:
        return False, f"FAIL single head: {e}"
    if not url:
        return True, (f"single head {head}; drift check SKIPPED "
                      f"({URL_ENV} not set — no secret on this run)")
    host = url.split("@")[-1].split("?")[0]
    try:
        stamp = read_stamp(url)
    except Exception as e:  # noqa: BLE001 — any read failure is a failure
        return False, (f"FAIL cannot read alembic_version on {host}: "
                       f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
    if not stamp:
        return False, (
            f"FAIL alembic_version on {host} returned ZERO rows. Either the "
            "database was never migrated, or this role can't see the row "
            "(RLS is enabled on alembic_version — the reader role needs its "
            "SELECT policy; see ops/sql/ci_revision_reader.sql).")
    state = revision_state(script, stamp)
    if state.unknown:
        where = [f"  {r}: {locate_revision(r) or 'not found in any fetched ref'}"
                 for r in state.unknown]
        return False, "FAIL " + describe_unknown(state) + "\n" + "\n".join(where)
    note = (f"{len(state.pending)} pending, applied by the next main deploy: "
            f"{', '.join(state.pending)}" if state.pending else "at head")
    return True, f"OK {host} stamped {', '.join(stamp)} (this build's head {head}); {note}"


def main() -> int:
    ok, message = check(os.environ.get(URL_ENV) or None)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
