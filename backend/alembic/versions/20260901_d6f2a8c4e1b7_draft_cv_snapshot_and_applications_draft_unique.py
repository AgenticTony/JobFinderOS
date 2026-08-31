"""application_drafts CV snapshot + applications unique(draft_id) (P1-5, SUBMIT)

Revision ID: d6f2a8c4e1b7
Revises: a7c9e1f3b5d2
Create Date: 2026-09-01

Two data-integrity fixes:

1. application_drafts gains cv_file_path + cv_hash — the CV reference
   snapshotted at draft creation. draft_service previously read the
   CURRENT profile path at send time, so a draft guarded against CV-old
   emailed CV-new as its "original CV" after a re-upload.

2. applications gets a PARTIAL unique index on draft_id. The submit path
   was check-then-act with no row lock: two rapid clicks both saw
   'ready' and sent two employer emails. The index is the DB backstop
   (draft_id is nullable — browser/manual applies carry no draft — so it
   is partial WHERE draft_id IS NOT NULL; multiple NULLs stay allowed on
   both Postgres and SQLite).

   Any duplicate rows the race already produced are deduplicated first:
   keep the "best" row per draft (a delivered one, else the earliest).
"""
import sqlalchemy as sa

from alembic import op

revision = "d6f2a8c4e1b7"
down_revision = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None

_DEDUPE_SQL = sa.text(
    """
    DELETE FROM applications
    WHERE draft_id IS NOT NULL
      AND id NOT IN (
          SELECT id FROM (
              SELECT id, ROW_NUMBER() OVER (
                  PARTITION BY draft_id
                  ORDER BY (status IN ('sent', 'manual_pending')) DESC, id ASC
              ) AS rn
              FROM applications
              WHERE draft_id IS NOT NULL
          ) ranked
          WHERE rn = 1
      )
    """
)


def upgrade() -> None:
    with op.batch_alter_table("application_drafts") as batch:
        batch.add_column(sa.Column("cv_file_path", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("cv_hash", sa.String(length=64), nullable=True))

    # Dedupe BEFORE the index: keep one row per draft — a delivered
    # application if any, else the earliest. Later duplicates are
    # artifacts of the double-send window (each meant the employer got
    # the same email twice; the kept row is the honest record).
    op.execute(_DEDUPE_SQL)
    op.create_index(
        "uq_applications_draft_id",
        "applications",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("draft_id IS NOT NULL"),
        sqlite_where=sa.text("draft_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_applications_draft_id", table_name="applications")
    with op.batch_alter_table("application_drafts") as batch:
        batch.drop_column("cv_hash")
        batch.drop_column("cv_file_path")
