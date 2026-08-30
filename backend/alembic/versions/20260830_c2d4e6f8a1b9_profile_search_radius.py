"""profiles.search_radius_km: commute-zone radius search

Revision ID: c2d4e6f8a1b9
Revises: b9c1d3e5f7a6
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d4e6f8a1b9"
down_revision = "b9c1d3e5f7a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("search_radius_km", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "search_radius_km")
