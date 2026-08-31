"""users.token_version: JWT revocation generation (P1-7)

Revision ID: f9b3d5e7a2c8
Revises: d6f2a8c4e1b7
Create Date: 2026-09-01

fastapi-users' stock JWT is unrevokable until expiry — a 7-day token
kept working after a password change. Every token now embeds the user's
token_version as a 'ver' claim; auth rejects tokens whose claim no
longer matches the row, and password changes bump the version. Existing
rows default to 0, which is also how claim-less legacy tokens read, so
the rollout does not force a mass logout.
"""
import sqlalchemy as sa

from alembic import op

revision = "f9b3d5e7a2c8"
down_revision = "d6f2a8c4e1b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
