"""MIG-WO3: row-level security — policies, grants, request-JWT context

Revision ID: e8f1a3c5d7b9
Revises: cdc76cd3ae26
Create Date: 2026-09-08

The two-session story becomes database-enforced:

  - INTERACTIVE sessions (core.database.RequestSessionLocal — the only
    factory get_db serves) run every transaction as role `authenticated`
    with `request.jwt.claim.sub` set from the verified token
    (app/core/database.py _propagate_request_jwt). Policies on the seven
    identity tables key on auth.uid(); a session with NO sub sees
    NOTHING (auth.uid() IS NOT NULL guard — the documented fail-closed
    stance for NULL identities).
  - SERVICE sessions (SessionLocal — hunt/scheduler/pipeline/worker,
    scripts, test seeding) run as postgres, the table owner: RLS is not
    enforced for the owner, so shared-pool work (job_postings,
    scrape_runs, system_locks) keeps running untouched.

The SQL lives in app/core/rls_sql.py (single source — tests/conftest's
stamp_alembic_head applies the same layer to create_all-built schemas,
which no migration ever touches). Vanilla-Postgres portability: the
layer provisions the Supabase-shaped context (authenticated role +
auth.uid() shim) when missing. SQLite: no roles, no RLS — no-op.

Rollback: policies are additive; downgrade drops them and disables RLS
(grants and the shim stay — inert without RLS).
"""

from alembic import op

from app.core.rls_sql import ensure_rls, remove_rls

revision = "e8f1a3c5d7b9"
down_revision = "cdc76cd3ae26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ensure_rls(op.get_bind())


def downgrade() -> None:
    remove_rls(op.get_bind())
