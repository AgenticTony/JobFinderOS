"""WO-20 outcome layer: outcome columns, proposals, scan state

Revision ID: f1a3c5e7b9d1
Revises: d94f2a6c8e1b
Create Date: 2026-10-15

What the employer did, recorded on the applications that were sent:

1. applications.outcome_* — the canonical outcome vocabulary
   (drafted/applied/interview/offer/hired/rejected/no_response/
   offer_declined/withdrawn), its source (manual | reply_detection |
   sweep), a note, an evidence pointer (message id — never a body),
   and resolved_at for final states. The send-side `status` column
   (queued/sent/failed/manual_pending) is NOT touched by any outcome
   write — two lifecycles, two columns, tested separately.

2. applications.follow_up_of_id — self-FK chaining follow-up sends to
   the application they follow up on. Follow-ups are counted for the
   10-day nudge; the first application is never its own follow-up.

3. application_drafts.follow_up_application_id — marks a draft as a
   follow-up (seeded from the original package, routed through the
   SAME review-approve gates; submit links the new application row
   back via follow_up_of_id).

4. outcome_proposals — propose-then-confirm. Reply detection and the
   60-day quiet sweep NEVER write applications directly; they file a
   proposal citing its evidence. Only an explicit user approval
   (decision='approved') applies it. Dismissed proposals stay as
   audit + re-scan dedupe.

5. gmail_scan_state — per-user incremental scan cursor (Gmail after:
   date, overlapped one day for safety; dedupe is the unique proposal
   index) so a scan never rescans; last_error for observability.

Deviation from the WO sketch, recorded here: proposals also carry
evidence_received_at (when the employer's message actually arrived).
resolved_at is set from it on approval — time-to-first-response must
measure the REPLY, not the moment we scanned it.

All additive, nullable, no backfill. RLS: both new tables are
user-owned — rls_sql.USER_TABLES gains them and this migration runs
ensure_rls (idempotent; re-applied per boot by init_db).
"""

import sqlalchemy as sa

from alembic import op

from app.core.rls_sql import ensure_rls, remove_rls

revision = "f1a3c5e7b9d1"
down_revision = "d94f2a6c8e1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("outcome_status", sa.String(20), nullable=True))
        batch.add_column(sa.Column("outcome_source", sa.String(20), nullable=True))
        batch.add_column(sa.Column("outcome_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("outcome_evidence", sa.String(500), nullable=True))
        batch.add_column(sa.Column("resolved_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("follow_up_of_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_applications_follow_up_of",
            "applications",
            ["follow_up_of_id"],
            ["id"],
        )

    with op.batch_alter_table("application_drafts") as batch:
        batch.add_column(
            sa.Column("follow_up_application_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_drafts_follow_up_application",
            "applications",
            ["follow_up_application_id"],
            ["id"],
        )

    op.create_table(
        "outcome_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, index=True
        ),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("proposed_status", sa.String(20), nullable=False),
        # reply_detection | sweep — manual markers never route through here
        sa.Column("source", sa.String(20), nullable=False),
        # Evidence pointer, never a message body (PII rule: store the
        # classification + citation, not the inbox content)
        sa.Column("evidence_message_id", sa.String(255), nullable=True),
        sa.Column("evidence_from", sa.String(255), nullable=True),
        sa.Column("evidence_subject", sa.String(500), nullable=True),
        # When the employer's message actually arrived — resolved_at is
        # set from this on approval so time-to-first-response measures
        # the reply, not the scan.
        sa.Column("evidence_received_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # NULL = pending | approved | dismissed — only approved applies
        sa.Column("decision", sa.String(20), nullable=True, index=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "uq_outcome_proposals_app_message",
        "outcome_proposals",
        ["application_id", "evidence_message_id"],
        unique=True,
        sqlite_where=sa.text("evidence_message_id IS NOT NULL"),
        postgresql_where=sa.text("evidence_message_id IS NOT NULL"),
    )

    op.create_table(
        "gmail_scan_state",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        # Gmail `after:` query cursor (yyyy-mm-dd). Overlapped one day
        # each scan; the unique proposal index is the dedupe backstop.
        sa.Column("scan_cursor", sa.String(64), nullable=True),
        sa.Column("last_scan_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )

    ensure_rls(op.get_bind())


def downgrade() -> None:
    remove_rls(op.get_bind())
    op.drop_table("gmail_scan_state")
    op.drop_index("uq_outcome_proposals_app_message", table_name="outcome_proposals")
    op.drop_table("outcome_proposals")
    with op.batch_alter_table("application_drafts") as batch:
        batch.drop_constraint("fk_drafts_follow_up_application", type_="foreignkey")
        batch.drop_column("follow_up_application_id")
    with op.batch_alter_table("applications") as batch:
        batch.drop_constraint("fk_applications_follow_up_of", type_="foreignkey")
        batch.drop_column("follow_up_of_id")
        batch.drop_column("resolved_at")
        batch.drop_column("outcome_evidence")
        batch.drop_column("outcome_note")
        batch.drop_column("outcome_source")
        batch.drop_column("outcome_status")
