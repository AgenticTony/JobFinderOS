"""WO-20 review round 2: outcome_proposals dedupe moves to (user, message)

Revision ID: b7c9d1f3a5e7
Revises: f1a3c5e7b9d1
Create Date: 2026-10-15

One email files at most ONE proposal across ALL the user's
applications. The round-1 index keyed (application_id,
evidence_message_id): after ambiguity resolution moved a message's
target (the first application closed), a later overlapping scan could
file the SAME email against a second application — two proposals, one
email, a wrong outcome waiting for approve-all.

This drops the per-application unique index and creates (user_id,
evidence_message_id) unique where evidence_message_id IS NOT NULL.
Sweep proposals (NULL message id) are unaffected — multiple NULLs stay
distinct on both backends.

No data migration: at most one row per (application, message) exists,
so the widened key is trivially satisfied by existing rows.
"""

from alembic import op
import sqlalchemy as sa

revision = "b7c9d1f3a5e7"
down_revision = "f1a3c5e7b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_outcome_proposals_app_message", table_name="outcome_proposals")
    op.create_index(
        "uq_outcome_proposals_user_message",
        "outcome_proposals",
        ["user_id", "evidence_message_id"],
        unique=True,
        sqlite_where=sa.text("evidence_message_id IS NOT NULL"),
        postgresql_where=sa.text("evidence_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_outcome_proposals_user_message", table_name="outcome_proposals")
    op.create_index(
        "uq_outcome_proposals_app_message",
        "outcome_proposals",
        ["application_id", "evidence_message_id"],
        unique=True,
        sqlite_where=sa.text("evidence_message_id IS NOT NULL"),
        postgresql_where=sa.text("evidence_message_id IS NOT NULL"),
    )
