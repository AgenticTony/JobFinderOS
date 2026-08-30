"""profiles.occupation_codes: taxonomy-validated profession search units

Revision ID: d3e5f7a9c1b2
Revises: c2d4e6f8a1b9
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "d3e5f7a9c1b2"
down_revision = "c2d4e6f8a1b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("occupation_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "occupation_codes")
