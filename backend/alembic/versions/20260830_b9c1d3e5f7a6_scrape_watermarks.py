"""scrape_watermarks: delta-scrape watermarks (published-after fetches)

Revision ID: b9c1d3e5f7a6
Revises: e7b4c6d8f2a3
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa

revision = "b9c1d3e5f7a6"
down_revision = "a1f2e3d4c5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scrape_watermarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False, index=True),
        sa.Column("query", sa.String(500), nullable=False, server_default=""),
        sa.Column("scope", sa.String(500), nullable=False, server_default=""),
        sa.Column("watermark_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "query", "scope", name="uq_scrape_watermark"),
    )


def downgrade() -> None:
    op.drop_table("scrape_watermarks")
