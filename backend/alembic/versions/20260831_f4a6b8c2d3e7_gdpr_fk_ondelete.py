"""gdpr fk ondelete actions

Revision ID: f4a6b8c2d3e7
Revises: d3e5f7a9c1b2
Create Date: 2026-08-31

P0-2 belt-and-braces (the required fix is the delete-order change in the
erasure/job-delete code paths): give the personal-data FKs explicit ON
DELETE actions so the DATABASE itself can never wedge a GDPR erasure or a
job delete again —

- applications.match_id / applications.draft_id /
  application_drafts.match_id -> ON DELETE SET NULL (all nullable)
- *.user_id FKs (profiles, match_results, application_drafts,
  applications) -> ON DELETE CASCADE: deleting the users row removes all
  personal rows even if a future code path forgets the explicit order.

PostgreSQL only. SQLite never enforces FK actions (PRAGMA foreign_keys is
off by default and the app never enables it), so there is nothing to
change on that backend — and its initial-schema FK constraints are
unnamed, which makes batch drop/recreate unreliable. A no-op is the
honest implementation there, not a shortcut.

Constraint names: the initial migration created the match/draft FKs
unnamed, which PostgreSQL auto-names <table>_<column>_fkey; the user_id
FKs were created with explicit names by revision 3061ba38c25f.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a6b8c2d3e7"
down_revision: Union[str, Sequence[str], None] = "d3e5f7a9c1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, old constraint name, referenced table)
SET_NULL_FKS = [
    ("application_drafts", "match_id",
     "application_drafts_match_id_fkey", "match_results"),
    ("applications", "match_id",
     "applications_match_id_fkey", "match_results"),
    ("applications", "draft_id",
     "applications_draft_id_fkey", "application_drafts"),
]

CASCADE_FKS = [
    ("profiles", "user_id", "fk_profiles_user_id_users"),
    ("match_results", "user_id", "fk_match_results_user_id_users"),
    ("application_drafts", "user_id", "fk_application_drafts_user_id_users"),
    ("applications", "user_id", "fk_applications_user_id_users"),
]


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # see docstring: SQLite FK actions are inert there

    for table, column, old_name, ref in SET_NULL_FKS:
        op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_{column}_{ref}", table, ref, [column], ["id"],
            ondelete="SET NULL",
        )

    for table, column, old_name in CASCADE_FKS:
        op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(
            old_name, table, "users", [column], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table, column, old_name in CASCADE_FKS:
        op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(
            old_name, table, "users", [column], ["id"]
        )

    for table, column, old_name, ref in SET_NULL_FKS:
        op.drop_constraint(f"fk_{table}_{column}_{ref}", table,
                           type_="foreignkey")
        op.create_foreign_key(old_name, table, ref, [column], ["id"])
