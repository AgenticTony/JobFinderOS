"""scrape_runs.scope: cooldown keyed on the fetch identity (WO-14 review fix)

Revision ID: c3d5e7f9a1b3
Revises: f9b3d5e7a2c8
Create Date: 2026-09-01

The manual-hunt cooldown looked up the last completed ScrapeRun by
source alone, so one user's Stockholm hunt suppressed a different
user's Malmö hunt of the same source — the cooldown must key on the
same fetch identity the watermarks use: (source, scope). scrape_source
now stamps pipeline._scope_key(ctx) on every run row. The column is
nullable because legacy rows have no scope; NULL never equals a scope
key, so pre-migration rows simply stop suppressing anything (one extra
scrape per (source, scope) after deploy, once). The composite index
serves the cooldown lookup.
"""
import sqlalchemy as sa

from alembic import op

revision = "c3d5e7f9a1b3"
down_revision = "f9b3d5e7a2c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scrape_runs") as batch:
        batch.add_column(sa.Column("scope", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_scrape_runs_source_scope", "scrape_runs", ["source", "scope"]
    )


def downgrade() -> None:
    op.drop_index("ix_scrape_runs_source_scope", table_name="scrape_runs")
    with op.batch_alter_table("scrape_runs") as batch:
        batch.drop_column("scope")
