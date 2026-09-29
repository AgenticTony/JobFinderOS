"""WO-25 — migration drift guard.

The 2026-09-28 outage: a build of an unmerged branch ran `upgrade head`
against production, stamping `d94f2a6c8e1b` — a revision main did not
have. Every main-built process then crashed at boot on alembic's
"Can't locate revision" until the branch merged.

Two checks, run by init_db() before every `upgrade head`:

1. **Explain** — a database stamped at a revision this build does not
   know fails with a diagnosis (both revisions, the cause, the fix)
   instead of a bare hash. Still fatal: never boot on an unknown schema.
2. **Prevent** — on Render, pending migrations apply only when the build
   came from MIGRATION_BRANCH (default main). A branch build with NO
   pending revisions boots normally. An unknown branch fails CLOSED.
   Off Render (local dev, CI) the branch rule does not apply — the
   scheduled CI drift check (scripts/check_migration_drift.py) is what
   detects a laptop `alembic upgrade` against production.

Deliberately free of app settings: the environment is passed in, so the
same functions serve init_db, the CI drift script and the tests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)


class MigrationGuardError(RuntimeError):
    """Boot must stop: migrating (or running) would be unsafe."""


@dataclass(frozen=True)
class RevisionState:
    current: tuple[str, ...]  # what the database is stamped at
    heads: tuple[str, ...]  # this build's script heads
    unknown: tuple[str, ...]  # stamped revisions this build does not have
    pending: tuple[str, ...]  # revisions `upgrade head` would apply


def _lineage(script: ScriptDirectory, revs: Iterable[str]) -> set[str]:
    """Every revision in `revs` plus all of their ancestors."""
    out: set[str] = set()
    for rev in revs:
        out.update(s.revision for s in script.iterate_revisions(rev, "base"))
    return out


def revision_state(script: ScriptDirectory, current: Iterable[str]) -> RevisionState:
    """Compare the database's stamped revisions against this build."""
    current = tuple(current)
    heads = tuple(script.get_heads())
    known = {s.revision for s in script.walk_revisions()}
    unknown = tuple(r for r in current if r not in known)
    if unknown:
        return RevisionState(current, heads, unknown, ())
    pending = _lineage(script, heads) - _lineage(script, current)
    return RevisionState(current, heads, (), tuple(sorted(pending)))


def describe_unknown(state: RevisionState) -> str:
    return (
        f"Database is stamped at {', '.join(state.unknown)}, which this build "
        f"does not contain (this build's head: {', '.join(state.heads) or 'none'}). "
        "The database is AHEAD of this build: a migration from another "
        "branch was applied to it. Merge the branch that owns that revision "
        "(or deploy it) — do not downgrade or re-stamp the database, that "
        "drops columns or makes it lie about its schema. See "
        "docs/work-orders/WO-25-migration-drift-guard.md."
    )


def deploy_identity(env: Mapping[str, str]) -> str | None:
    """'branch@shortsha' on Render, None elsewhere (for the boot log)."""
    if not env.get("RENDER"):
        return None
    branch = env.get("RENDER_GIT_BRANCH") or "<unknown branch>"
    commit = (env.get("RENDER_GIT_COMMIT") or "")[:7] or "<unknown commit>"
    return f"{branch}@{commit}"


def assert_may_migrate(
    state: RevisionState, env: Mapping[str, str], migration_branch: str
) -> None:
    """Raise unless this process may run `upgrade head` right now."""
    if state.unknown:
        raise MigrationGuardError(describe_unknown(state))
    if not state.pending or not env.get("RENDER"):
        return
    branch = env.get("RENDER_GIT_BRANCH")
    if branch == migration_branch:
        return
    who = (
        f"branch {branch!r}" if branch
        else "an unknown branch (RENDER_GIT_BRANCH is not set — failing closed)"
    )
    raise MigrationGuardError(
        f"Refusing to migrate: this build is from {who}, and only "
        f"{migration_branch!r} may apply migrations to this database. "
        f"Pending: {', '.join(state.pending)}. Merge the branch and deploy "
        f"{migration_branch!r} (a branch build with no new migrations boots "
        "normally). WO-25."
    )


def assert_single_head(script: ScriptDirectory) -> str:
    """Return the one head; raise if the graph has forked."""
    heads = script.get_heads()
    if len(heads) != 1:
        raise MigrationGuardError(
            f"Alembic graph has {len(heads)} heads ({', '.join(sorted(heads))}); "
            "`upgrade head` would fail at boot. Add a merge migration "
            "(alembic merge heads) or re-parent one branch's migration."
        )
    return heads[0]
