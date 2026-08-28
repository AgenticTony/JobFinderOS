"""system_locks: portable hunt claim lock (WO-04 worker split)

Revision ID: e7b4c6d8f2a3
Revises: d8a2b3c4e9f5
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa

revision = "e7b4c6d8f2a3"
down_revision = "d8a2b3c4e9f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_locks",
        sa.Column("name", sa.String(50), primary_key=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_locks")
