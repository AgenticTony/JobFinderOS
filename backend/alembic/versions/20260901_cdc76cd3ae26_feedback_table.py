"""feedback table: the beta testers' one-box feedback (owner decision 2026-09-01)

Revision ID: cdc76cd3ae26
Revises: c3d5e7f9a1b3
Create Date: 2026-09-01

Console sidebar gets a "Beta feedback" page: category chips + one text
box, no fields to fill. Rows are account-linked BY DESIGN (disclosed on
the page) — "scores are wrong" is only actionable when the owner can
open that user's matches. Rate-limited at the endpoint (5/hour) so a
stuck submit button can't flood the notification inbox.
"""
import sqlalchemy as sa

from alembic import op

revision = "cdc76cd3ae26"
down_revision = "c3d5e7f9a1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_user_id", table_name="feedback")
    op.drop_table("feedback")
