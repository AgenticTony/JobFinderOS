"""ai_usage: per-call AI cost rows (WO-05 observability)

Revision ID: d8a2b3c4e9f5
Revises: c3f1a9d2e841
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa

revision = "d8a2b3c4e9f5"
down_revision = "c3f1a9d2e841"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=True, index=True),
        sa.Column("kind", sa.String(20), nullable=False, index=True),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Integer(), nullable=True),  # micro-dollars
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("ai_usage")
