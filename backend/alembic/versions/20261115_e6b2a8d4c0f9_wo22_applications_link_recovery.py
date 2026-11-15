"""WO-22 slice s1: applications.link_recovery — the substitution audit

Revision ID: e6b2a8d4c0f9
Revises: a31e5c7d9f20
Create Date: 2026-11-15

Apply-time link recovery: when the hand-off probe finds a DEFINITELY
dead apply URL (HTTP 404/410/5xx — never 401/403/timeout), the pool is
searched for a live twin and the twin's link is handed off instead.
Every substitution is audited ON the application row itself — JSON
{"dead_url", "twin_url", "source", "recovered_at", "note"} in
applications.link_recovery — deliberately NOT a separate log table.

Additive, nullable, no backfill: rows that never had a substitution
keep NULL, so the no-twin path writes nothing. No RLS change (no new
table; the column rides applications' existing user-owned policy).
"""

import sqlalchemy as sa

from alembic import op

revision = "e6b2a8d4c0f9"
down_revision = "a31e5c7d9f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("link_recovery", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.drop_column("link_recovery")
