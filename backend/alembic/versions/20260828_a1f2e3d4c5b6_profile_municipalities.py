"""profiles.municipalities: strict multi-municipality location scope

User decision after the first production hunt: picking Malmö means Malmö —
the gate stops admitting the whole region implicitly. Users may pick SEVERAL
municipalities (Malmö + Lund commute belt); choosing none means explicit
whole-region. The legacy single `municipality` column stays and behaves as a
one-item list (code-level backfill — no data migration needed).

Revision ID: a1f2e3d4c5b6
Revises: e7b4c6d8f2a3
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa

revision = "a1f2e3d4c5b6"
down_revision = "e7b4c6d8f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles",
                  sa.Column("municipalities", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "municipalities")
