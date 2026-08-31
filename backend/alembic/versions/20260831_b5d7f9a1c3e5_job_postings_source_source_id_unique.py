"""job_postings unique(source, source_id) with dedupe-first migration

Revision ID: b5d7f9a1c3e5
Revises: f4a6b8c2d3e7
Create Date: 2026-08-31

PIPE-14b. The ingest path's dedupe (_job_exists) is a pre-check, not a
concurrency control: a manual hunt racing the cron worker had both runs
SELECT "not present" and both INSERT the same (source, source_id) — the
shared pool grew duplicate postings, and both runs then AI-scored the
same listings (the uq_match_results_user_job reconcile discarded the
loser's rows only AFTER the spend). This revision makes the database
itself refuse duplicates.

Creating the unique index on live data requires deduplicating first —
existing duplicate (source, source_id) rows would make CREATE UNIQUE
INDEX fail. Dedupe rules:

- keep the LOWEST id (oldest) per (source, source_id) where source_id
  IS NOT NULL; rows with NULL source_id (manual jobs) never conflict
  because NULLs are distinct in unique indexes on both backends;
- resolve the uq_match_results_user_job collisions the re-point is
  about to create (a user had matched BOTH copies of the posting — that
  is exactly how the doubles were being scored). For every (user_id,
  effective job) group that contains at least one loser-referencing
  row, keep ONLY the group's single lowest match id — that can mean
  deleting a HIGHER-id match that sits on the KEEPER job itself: the
  matcher scans candidates NEWEST-first (order_by scraped_at.desc), and
  a race-born duplicate's loser copy is the newer posting, so the lower
  match id usually lands on the LOSER. Groups with no loser-referencing
  row cannot newly collide and are untouched. Clear
  application_drafts.match_id / applications.match_id references to the
  dropped rows, THEN re-point the surviving children
  (match_results / application_drafts / applications .job_id) from each
  loser to its keeper and delete the loser postings — no child is ever
  orphaned and the UPDATE itself can never violate the constraint.

Everything is plain SQL that runs identically on SQLite and Postgres —
no dialect split needed. Downgrade only drops the index: merged rows
cannot be un-merged, and the application-level dedupe (_job_exists)
keeps working without it.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5d7f9a1c3e5"
down_revision: Union[str, Sequence[str], None] = "f4a6b8c2d3e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Scratch tables (dropped at the end of both upgrade and downgrade runs).
_MAP = "_pipe14_keeper"        # loser id -> keeper id
_EFF = "_pipe14_eff"           # match id -> (effective job id, loser-ref?)
_DUP_MATCHES = "_pipe14_dup_matches"  # match_results ids to drop

_CHILDREN = ("match_results", "application_drafts", "applications")


def upgrade() -> None:
    bind = op.get_bind()

    # 0. scratch tables (idempotent guards: a failed earlier attempt
    #    must not wedge the migration on re-run)
    for ddl in (
        f"DROP TABLE IF EXISTS {_MAP}",
        f"DROP TABLE IF EXISTS {_EFF}",
        f"DROP TABLE IF EXISTS {_DUP_MATCHES}",
        f"CREATE TABLE {_MAP} (loser INTEGER PRIMARY KEY, keeper INTEGER NOT NULL)",
        f"CREATE TABLE {_EFF} (id INTEGER PRIMARY KEY,"
        f" eff INTEGER NOT NULL, loser_ref INTEGER NOT NULL)",
        f"CREATE TABLE {_DUP_MATCHES} (id INTEGER PRIMARY KEY)",
    ):
        bind.execute(sa.text(ddl))

    # 1. loser -> keeper map: every row that is NOT the lowest id of its
    #    (source, source_id) group. NULL source_id never enters (NULL
    #    comparisons drop those rows out of the correlated subquery).
    bind.execute(sa.text(f"""
        INSERT INTO {_MAP} (loser, keeper)
        SELECT j.id,
               (SELECT MIN(k.id) FROM job_postings k
                 WHERE k.source = j.source AND k.source_id = j.source_id)
          FROM job_postings j
         WHERE j.source_id IS NOT NULL
           AND j.id <> (SELECT MIN(k.id) FROM job_postings k
                         WHERE k.source = j.source
                           AND k.source_id = j.source_id)
    """))

    # 2. uq_match_results_user_job collisions that the re-point is
    #    ABOUT to create (a user had matched BOTH copies of the
    #    posting): resolve them BEFORE the UPDATE — the UPDATE itself
    #    would violate the constraint the moment two rows of the same
    #    group point at the keeper. Flatten every match to its
    #    effective job (keeper for loser-referencing rows, the job
    #    itself otherwise); a group that contains ANY loser-referencing
    #    row keeps ONLY its single lowest match id — and the row deleted
    #    for that may be the one sitting on the KEEPER job: the matcher
    #    scans newest-first, so the lower match id usually lands on the
    #    (newer, race-born) LOSER copy (review finding: the first
    #    version only examined loser-side rows and blew up on exactly
    #    that shape). Groups with no loser-referencing row cannot newly
    #    collide and stay untouched.
    bind.execute(sa.text(f"""
        INSERT INTO {_EFF} (id, eff, loser_ref)
        SELECT m.id,
               COALESCE(k.keeper, m.job_id),
               CASE WHEN k.loser IS NULL THEN 0 ELSE 1 END
          FROM match_results m
          LEFT JOIN {_MAP} k ON k.loser = m.job_id
    """))
    bind.execute(sa.text(f"""
        INSERT INTO {_DUP_MATCHES} (id)
        SELECT e.id
          FROM {_EFF} e
          JOIN match_results m ON m.id = e.id
         WHERE EXISTS (SELECT 1
                         FROM {_EFF} e2
                         JOIN match_results m2 ON m2.id = e2.id
                        WHERE m2.user_id = m.user_id
                          AND e2.eff = e.eff
                          AND e2.loser_ref = 1)
           AND e.id > (SELECT MIN(e3.id)
                         FROM {_EFF} e3
                         JOIN match_results m3 ON m3.id = e3.id
                        WHERE m3.user_id = m.user_id
                          AND e3.eff = e.eff)
    """))

    # 3. clear references to the dropped matches, then drop them.
    bind.execute(sa.text(f"""
        UPDATE application_drafts
           SET match_id = NULL
         WHERE match_id IN (SELECT id FROM {_DUP_MATCHES})
    """))
    bind.execute(sa.text(f"""
        UPDATE applications
           SET match_id = NULL
         WHERE match_id IN (SELECT id FROM {_DUP_MATCHES})
    """))
    bind.execute(sa.text(f"DELETE FROM match_results WHERE id IN (SELECT id FROM {_DUP_MATCHES})"))

    # 4. re-point every child's job_id at the keeper — now collision-free.
    for table in _CHILDREN:
        bind.execute(sa.text(f"""
            UPDATE {table}
               SET job_id = (SELECT keeper FROM {_MAP}
                              WHERE loser = {table}.job_id)
             WHERE job_id IN (SELECT loser FROM {_MAP})
        """))

    bind.execute(sa.text(f"DELETE FROM job_postings WHERE id IN (SELECT loser FROM {_MAP})"))

    for ddl in (f"DROP TABLE {_DUP_MATCHES}", f"DROP TABLE {_EFF}", f"DROP TABLE {_MAP}"):
        bind.execute(sa.text(ddl))

    # 5. the constraint itself.
    op.create_index(
        "uq_job_postings_source_source_id", "job_postings",
        ["source", "source_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_job_postings_source_source_id", table_name="job_postings")
    # Merged rows are not restored — see the docstring.
    bind = op.get_bind()
    for scratch in (_MAP, _EFF, _DUP_MATCHES):
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {scratch}"))
