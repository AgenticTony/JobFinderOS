"""users.token_version: dropped — P1-7 scheme retired with MIG-WO2

Revision ID: a9c2e4f6b8d0
Revises: e8f1a3c5d7b9
Create Date: 2026-09-09

The column carried the JWT-revocation generation for the P1-7 scheme
(password changes bumped it; version-pinned tokens died on mismatch).
MIG-WO2 deleted the scheme — Supabase Auth terminates sessions on
password change natively — and the column was retained solely as
rollback material for the fastapi-users→Supabase cutover. The cutover
was executed and verified 2026-09-09 (4 accounts remapped, end-to-end
login proven, verify_deployment 6/6); this drops the dead weight.

Nothing in the app reads or writes the column (grep-clean across
app/, tests/, ops/), so the drop is behaviorally invisible.
"""

from alembic import op
import sqlalchemy as sa

revision = "a9c2e4f6b8d0"
down_revision = "e8f1a3c5d7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False,
                      server_default="0")
        )
