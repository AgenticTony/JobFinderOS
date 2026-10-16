"""WO-31 Phase A step 1: the durable ai_jobs queue

Revision ID: a31e5c7d9f20
Revises: c3d8e2f4a6b8
Create Date: 2026-10-16

Owner requirement (2026-10-16): provider load must never fail a user's
request. Interactive AI work (tailor, recheck, extract, suggest) is
enqueued here and drained by one loop in the API process
(AI_DRAINER=api); a 429 re-queues the row with next_attempt_at instead
of failing it.

- uq_ai_jobs_active_subject: partial UNIQUE (kind, user_id, subject_id)
  WHERE status IN ('queued','running') — the idempotency key.
- ix_ai_jobs_claim: the drainer's claim scan.
- user_id FK ON DELETE CASCADE. Erasure tombstones the users row (never
  deletes it), so account deletion also deletes these rows explicitly.

Additive, no backfill. RLS: user-owned (rls_sql.USER_TABLES gains
ai_jobs; ensure_rls is idempotent and re-applied per boot by init_db).
"""

import sqlalchemy as sa

from alembic import op

from app.core.rls_sql import ensure_rls, remove_rls

revision = "a31e5c7d9f20"
down_revision = "c3d8e2f4a6b8"
branch_labels = None
depends_on = None

_ACTIVE = sa.text("status IN ('queued', 'running')")


def upgrade() -> None:
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("priority_class", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("last_error_code", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("params", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_jobs_user_id", "ai_jobs", ["user_id"])
    op.create_index(
        "ix_ai_jobs_claim", "ai_jobs", ["status", "priority_class", "next_attempt_at"]
    )
    op.create_index(
        "uq_ai_jobs_active_subject",
        "ai_jobs",
        ["kind", "user_id", "subject_id"],
        unique=True,
        sqlite_where=_ACTIVE,
        postgresql_where=_ACTIVE,
    )
    ensure_rls(op.get_bind())


def downgrade() -> None:
    remove_rls(op.get_bind())
    op.drop_index("uq_ai_jobs_active_subject", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_claim", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_user_id", table_name="ai_jobs")
    op.drop_table("ai_jobs")
    # remove_rls stripped EVERY table's policies; the tables that remain
    # still need theirs (ensure_rls skips tables that no longer exist).
    ensure_rls(op.get_bind())
