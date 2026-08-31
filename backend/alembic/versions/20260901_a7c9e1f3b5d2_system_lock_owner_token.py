"""system_locks.owner_token: owned hunt claims (PIPE-18)

Revision ID: a7c9e1f3b5d2
Revises: b5d7f9a1c3e5
Create Date: 2026-09-01

release_hunt used to clear ANY holder's claim. A hunt that overran the
fixed 45-minute TTL could have its claim stolen (correctly — the
stealer assumes the holder died) and then RELEASE the stealer's claim
on the way out, putting two hunts in flight. Claims now mint an
owner_token at claim time and release/renew are conditional UPDATEs
keyed on it. Nullable: existing rows predate ownership; a NULL token
row is either free (locked_until NULL, claimable by anyone) or a stale
held claim whose TTL will expire as before.
"""
import sqlalchemy as sa

from alembic import op

revision = "a7c9e1f3b5d2"
down_revision = "b5d7f9a1c3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("system_locks") as batch:
        batch.add_column(sa.Column("owner_token", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("system_locks") as batch:
        batch.drop_column("owner_token")
