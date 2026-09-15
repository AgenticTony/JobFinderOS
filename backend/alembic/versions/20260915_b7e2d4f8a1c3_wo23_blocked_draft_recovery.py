"""WO-23 blocked-draft recovery: vouched facts + attestation columns

Revision ID: b7e2d4f8a1c3
Revises: a9c2e4f6b8d0
Create Date: 2026-09-15

A draft the fabrication guard blocks was a dead end — the only way
forward was re-uploading the whole CV. Recovery needs three columns:

1. profiles.vouched_facts — JSON list of strings. Skills/facts that are
   true but aren't on the CV ("Things I can vouch for"). User-entered,
   rendered by build_profile_context OUTSIDE the include_derived gate so
   they reach BOTH the generator and the guard. The addendum to the CV,
   never an edit (invariant #1).

2. application_drafts.fabrication_attested — JSON list of
   {claim, at, saved_to_profile}. Per-claim "This is true — keep it"
   confirmations on a blocked draft. A vouched/attested fact is a fact,
   not a verdict: each line supports only what it states, so a blanket
   entry can never clear an unrelated claim the AI invented.

3. application_drafts.fabrication_resolved_at — set when a blocked draft
   recovers to 'ready' (re-check clean, or all high claims attested).
   fabrication_blocked itself is NEVER cleared on recovery — it is the
   raw fabrication-rate data.

All three are additive, nullable, no backfill.
"""
import sqlalchemy as sa

from alembic import op

revision = "b7e2d4f8a1c3"
down_revision = "a9c2e4f6b8d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("vouched_facts", sa.Text(), nullable=True))
    with op.batch_alter_table("application_drafts") as batch:
        batch.add_column(sa.Column("fabrication_attested", sa.Text(), nullable=True))
        batch.add_column(sa.Column("fabrication_resolved_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("application_drafts") as batch:
        batch.drop_column("fabrication_resolved_at")
        batch.drop_column("fabrication_attested")
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("vouched_facts")
