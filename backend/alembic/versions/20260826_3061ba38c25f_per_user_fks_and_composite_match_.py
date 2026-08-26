"""per-user FKs and composite match uniqueness

Revision ID: 3061ba38c25f
Revises: ab219adaba28
Create Date: 2026-08-26 08:56:54.752716

Phase 1b: user_id FKs on profiles/match_results/application_drafts/
applications; match_results uniqueness moves from (job_id) to
(user_id, job_id) — two users can now score the same job. Batch ops keep
it portable to SQLite (alembic docs: alembic.sqlalchemy.org/en/latest/batch.html).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3061ba38c25f'
down_revision: Union[str, Sequence[str], None] = 'ab219adaba28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAMING = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_users",
}


def upgrade() -> None:
    for table in ("application_drafts", "applications", "match_results"):
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
            batch_op.create_index(
                op.f(f"ix_{table}_user_id"), ["user_id"], unique=False
            )
            batch_op.create_foreign_key(
                f"fk_{table}_user_id_users", "users", ["user_id"], ["id"]
            )

    # match_results: job_id was UNIQUE (single-user world) — drop it and
    # move uniqueness to (user_id, job_id)
    with op.batch_alter_table("match_results", naming_convention=NAMING) as batch_op:
        # original uniqueness was Column(unique=True, index=True) — a UNIQUE
        # INDEX, not a named constraint: replace the unique index with a plain
        # one, then add the composite unique constraint
        batch_op.drop_index("ix_match_results_job_id")
        batch_op.create_index("ix_match_results_job_id", ["job_id"], unique=False)
        batch_op.create_unique_constraint(
            "uq_match_results_user_job", ["user_id", "job_id"]
        )

    with op.batch_alter_table("profiles", naming_convention=NAMING) as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch_op.create_index(op.f("ix_profiles_user_id"), ["user_id"], unique=True)
        batch_op.create_foreign_key(
            "fk_profiles_user_id_users", "users", ["user_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("profiles", naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint("fk_profiles_user_id_users", type_="foreignkey")
        batch_op.drop_index(op.f("ix_profiles_user_id"), table_name="profiles")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("match_results", naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint("uq_match_results_user_job", type_="unique")
        batch_op.drop_index("ix_match_results_job_id")
        batch_op.create_index("ix_match_results_job_id", ["job_id"], unique=True)
        batch_op.drop_index(op.f("ix_match_results_user_id"), table_name="match_results")
        batch_op.drop_column("user_id")

    for table in ("applications", "application_drafts"):
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_user_id_users", type_="foreignkey")
            batch_op.drop_index(op.f(f"ix_{table}_user_id"), table_name=table)
            batch_op.drop_column("user_id")
