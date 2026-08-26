"""user_id NOT NULL on every tenant-owned table

Revision ID: 7c1d9e4a2b80
Revises: 3061ba38c25f
Create Date: 2026-08-26 09:58:12.004417

Layer 0 of the tenancy hardening: the database — not caller discipline —
now enforces that every tenant-owned row belongs to somebody.

Phase 1b made user_id nullable so the backfill could run. That left the
schema unable to reject an unowned row, and an unowned row is readable by
whoever asks: the IDOR guard's old `is not None` check passed NULLs to
every authenticated user. Making the column NOT NULL removes the state
that made that possible.

The upgrade REFUSES to run while orphan rows exist rather than inventing
an owner for someone's CV or sent application. Fix by running
scripts/bootstrap_user.py (claims legacy rows for an account) or by
deleting the orphans, then re-run.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1d9e4a2b80"
down_revision: Union[str, Sequence[str], None] = "3061ba38c25f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("profiles", "match_results", "application_drafts", "applications")

NAMING = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_users",
}


def _assert_no_orphans() -> None:
    """Fail loudly rather than guess who owns a row."""
    conn = op.get_bind()
    orphans = {}
    for table in TABLES:
        count = conn.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")  # noqa: S608
        ).scalar_one()
        if count:
            orphans[table] = count
    if orphans:
        detail = ", ".join(f"{t}: {n}" for t, n in orphans.items())
        raise RuntimeError(
            "Refusing to set user_id NOT NULL — unowned rows exist "
            f"({detail}). These rows predate the per-user schema and this "
            "migration will not invent an owner for them. Run "
            "`python -m scripts.bootstrap_user --email <you>` to claim them, "
            "or delete them, then re-run `alembic upgrade head`."
        )


def upgrade() -> None:
    _assert_no_orphans()
    for table in TABLES:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.alter_column(
                "user_id", existing_type=sa.Uuid(), nullable=False
            )


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch_op:
            batch_op.alter_column(
                "user_id", existing_type=sa.Uuid(), nullable=True
            )
