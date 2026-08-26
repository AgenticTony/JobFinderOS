"""match_results: prompt_version + per-user dismissed_reason

Revision ID: 9f4b2c81de07
Revises: 7c1d9e4a2b80
Create Date: 2026-08-26 11:12:40.881204

Two columns, both driven by measurement:

prompt_version — scores from different scoring prompts are not comparable.
Re-running the SAME model on the SAME job across the rubric-anchor change
moved scores by up to 26 points (job 665: 58 -> 32). The existing backlog
was scored before that change, so it is stamped 'legacy-unversioned' rather
than left indistinguishable from current scores.

dismissed_reason — dismissal is per-user state. It previously lived on
job_postings.status, which is a SHARED row, so one user's exclude keyword
or cross-board duplicate removed the posting from every other user's queue.
Recording it here keeps it per-user, stops re-evaluation, and preserves the
audit trail of why a job was dropped.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f4b2c81de07"
down_revision: Union[str, Sequence[str], None] = "7c1d9e4a2b80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY = "legacy-unversioned"


def upgrade() -> None:
    with op.batch_alter_table("match_results") as batch_op:
        batch_op.add_column(sa.Column("prompt_version", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("dismissed_reason", sa.String(30), nullable=True))
        batch_op.create_index(
            op.f("ix_match_results_prompt_version"), ["prompt_version"], unique=False
        )
        batch_op.create_index(
            op.f("ix_match_results_dismissed_reason"), ["dismissed_reason"], unique=False
        )

    # Every pre-existing score predates versioning. Stamping them makes the
    # stale backlog queryable ("re-score everything not on the current
    # version") instead of silently ranked against fresh scores.
    op.execute(
        sa.text(
            "UPDATE match_results SET prompt_version = :v WHERE prompt_version IS NULL"
        ).bindparams(v=LEGACY)
    )


def downgrade() -> None:
    with op.batch_alter_table("match_results") as batch_op:
        batch_op.drop_index(op.f("ix_match_results_dismissed_reason"))
        batch_op.drop_index(op.f("ix_match_results_prompt_version"))
        batch_op.drop_column("dismissed_reason")
        batch_op.drop_column("prompt_version")
