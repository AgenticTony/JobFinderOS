"""fabrication guard columns on application_drafts

WO-01 Layer C: persist Layer-A findings at draft creation plus retry and
block counts, so the fabrication rate is a measured number with a
denominator rather than an aspiration. Column addition only — nullable /
defaulted, no data migration (per the pattern in the repo's existing
chain; op.add_column per Alembic's official migration reference).

Revision ID: c3f1a9d2e841
Revises: 9f4b2c81de07
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f1a9d2e841"
down_revision = "9f4b2c81de07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("application_drafts",
                  sa.Column("fabrication_findings", sa.Text(), nullable=True))
    op.add_column("application_drafts",
                  sa.Column("fabrication_retries", sa.Integer(),
                            nullable=False, server_default="0"))
    op.add_column("application_drafts",
                  sa.Column("fabrication_blocked", sa.Boolean(),
                            nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("application_drafts", "fabrication_blocked")
    op.drop_column("application_drafts", "fabrication_retries")
    op.drop_column("application_drafts", "fabrication_findings")
