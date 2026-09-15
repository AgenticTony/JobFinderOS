"""WO-19 part B: work rights + eligibility verdict columns

Revision ID: d94f2a6c8e1b
Revises: b7e2d4f8a1c3
Create Date: 2026-09-15

The eligibility gate (deterministic, pre-AI):

1. profiles.work_rights — enum-ish string:
   citizen_or_pr | permanent_resident | eu_right | needs_sponsorship |
   prefer_not_say. Collected at onboarding, editable in Profile
   preferences. 'prefer_not_say' (the default) behaves as
   unverified-everywhere — never verification.

2. match_results.eligibility / eligibility_note — the verdict computed
   at gate time for every SHOWN match ('verified' | 'unverified';
   'ineligible' posts are hard-stopped before scoring and never get a
   row — re-evaluated free each run, like the scope gate, because the
   user's answer can change). The note carries WHY (welcoming wording
   quoted / high-risk-sector warning); NULL = silent-plain, stored for
   stats only.

All additive, nullable, no backfill (existing rows read as
prefer_not_say / no verdict — recomputed on the next hunt).
"""
import sqlalchemy as sa

from alembic import op

revision = "d94f2a6c8e1b"
down_revision = "b7e2d4f8a1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column(
            "work_rights", sa.String(length=30), nullable=True,
            server_default="prefer_not_say"))
    with op.batch_alter_table("match_results") as batch:
        batch.add_column(sa.Column("eligibility", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("eligibility_note", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("match_results") as batch:
        batch.drop_column("eligibility_note")
        batch.drop_column("eligibility")
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("work_rights")
