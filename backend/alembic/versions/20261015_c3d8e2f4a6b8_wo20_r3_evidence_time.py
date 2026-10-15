"""WO-20 review round 3: applications.outcome_evidence_at

Revision ID: c3d8e2f4a6b8
Revises: b7c9d1f3a5e7
Create Date: 2026-10-15

The final-regression guard in decide_proposal compared a new
proposal's evidence against resolved_at — but the round-1 min rule
pins resolved_at to the FIRST employer response, so the guard could
not order outcomes: interview(day 4) → rejected(day 35) left
resolved_at=day 4, and a day-20 second-round invite scanned late
passed the guard and reopened the rejection.

outcome_evidence_at records when the CURRENT outcome's own evidence
arrived (approved reply proposals) or was recorded (manual markers,
sweeps); the guard now compares against it. Backfilled from
resolved_at for rows that already carry an outcome — the best
available proxy for pre-column writes.
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d8e2f4a6b8"
down_revision = "b7c9d1f3a5e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("outcome_evidence_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE applications SET outcome_evidence_at = resolved_at "
        "WHERE outcome_status IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("applications", "outcome_evidence_at")
