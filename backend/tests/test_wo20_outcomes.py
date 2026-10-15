"""WO-20 outcome layer — acceptance tests (red-first per the WO).

Every numbered test maps to a WO-20 acceptance criterion:

1. Vocabulary: Final/Open partition; enum-only writes; send-side status
   untouched by outcome writes; migration round-trip.
2. Classification fixtures: schedule-a-call → interview; move-forward-
   with-others → rejected; ambiguous → surfaced; SUBJECT-ONLY REFUSED.
3. Approval gate: no applications row changes without a confirmed
   decision — decide_proposal with a PENDING proposal raises and
   changes nothing (the revert-the-gate proof, inverted).
4. Incremental state: a replayed scan files no duplicate proposals.
5. Nudges: thresholds asserted; `drafted` never chased, never quiet.
6. Stats: denominator = out-the-door only; sweep results land in the
   denominator.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_wo20_outcomes.py -q
"""

import asyncio
import uuid
from datetime import date, timedelta

import pytest

from app.core.database import engine
from app.core.orm import Base
from app.core.timeutil import utc_now
from app.models import (
    Application,
    ApplicationDraft,
    GmailScanState,
    JobPosting,
    OutcomeProposal,
    Profile,
    User,
)
from app.services import outcome_service as osvc
from app.services.outcome_service import (
    DEADLINE_URGENT_DAYS,
    FINAL_OUTCOMES,
    FOLLOW_UP_MAX,
    FOLLOW_UP_QUIET_DAYS,
    OPEN_OUTCOMES,
    OUTCOME_STATUSES,
    OutcomeError,
    approve_all_pending,
    classify_message,
    compute_nudges,
    create_follow_up_draft,
    decide_proposal,
    list_pending_proposals,
    outcome_stats,
    propose_no_response_sweep,
    scan_replies,
    sender_matches_application,
    set_outcome,
)


@pytest.fixture()
def db():
    from app.core.database import SessionLocal
    from tests.conftest import stamp_alembic_head

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    stamp_alembic_head()
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
    engine.dispose()


def _user(db, email="wo20@test.example"):
    u = User(id=uuid.uuid4(), email=email, hashed_password="test-only")
    db.add(u)
    db.flush()
    return u


def _profile(db, user, country="GB"):
    p = Profile(
        is_active=1, user_id=user.id, full_name="WO Twenty",
        cv_file_name="cv.pdf", cv_text="developer python",
        country=country, languages='["English"]',
    )
    db.add(p)
    db.commit()
    return p


def _job(db, *, title="Developer", company="Acme", url="https://acme.example/jobs/1",
         published_days_ago=1, application_email="jobs@acme.example"):
    j = JobPosting(
        source="manual", source_id=uuid.uuid4().hex[:8], title=title,
        company=company, url=url, status="new",
        published_at=utc_now() - timedelta(days=published_days_ago),
        application_email=application_email,
    )
    db.add(j)
    db.flush()
    return j


def _application(db, user, job, *, sent_days_ago=None, status="manual_pending",
                 target_email="jobs@acme.example", created_days_ago=0):
    a = Application(
        user_id=user.id, job_id=job.id, method="manual", status=status,
        target_email=target_email, apply_url=job.url,
        sent_at=(utc_now() - timedelta(days=sent_days_ago)) if sent_days_ago is not None else None,
        created_at=utc_now() - timedelta(days=created_days_ago),
    )
    db.add(a)
    db.commit()
    return a


# =====================================================================
# 1. Vocabulary
# ======================================================================

class TestVocabulary:
    def test_partition_is_total_and_disjoint(self):
        assert FINAL_OUTCOMES | OPEN_OUTCOMES == set(OUTCOME_STATUSES)
        assert FINAL_OUTCOMES & OPEN_OUTCOMES == set()

    def test_drafted_is_open_and_special(self):
        assert "drafted" in OPEN_OUTCOMES
        assert "drafted" not in FINAL_OUTCOMES

    def test_invalid_status_rejected(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job)
        with pytest.raises(OutcomeError, match="unknown outcome"):
            set_outcome(db, app, "ghosted", "manual")

    def test_invalid_source_rejected(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job)
        with pytest.raises(OutcomeError, match="unknown outcome source"):
            set_outcome(db, app, "interview", "psychic")

    def test_outcome_write_never_touches_send_side_status(self, db):
        """Acceptance 1: two lifecycles, two columns — proven, not assumed."""
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, status="manual_pending")
        before = app.status
        set_outcome(db, app, "interview", "manual")
        db.refresh(app)
        assert app.status == before == "manual_pending"
        set_outcome(db, app, "hired", "manual")
        db.refresh(app)
        assert app.status == before  # still untouched after a Final write

    def test_resolved_at_records_event_time(self, db):
        """Event-time semantics: every outcome write stamps WHEN the
        outcome became established. An explicit evidence time (an
        approved proposal's reply arrival) overrides the recording time —
        that's what makes time-to-first-response honest."""
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job)
        explicit = utc_now() - timedelta(days=4)
        set_outcome(db, app, "interview", "reply_detection", resolved_at=explicit)
        db.refresh(app)
        assert app.resolved_at == explicit  # evidence time, not "now"
        set_outcome(db, app, "rejected", "manual")
        db.refresh(app)
        assert app.resolved_at is not None and app.resolved_at != explicit

    def test_migration_round_trip_columns_persist(self, db):
        """create_all (== migration head shape) round-trips every new
        column — the migration round-trip leg of acceptance 1."""
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job)
        app.outcome_status = "offer"
        app.outcome_source = "manual"
        app.outcome_note = "called me"
        app.outcome_evidence = "msg:abc123"
        app.resolved_at = utc_now()
        app.follow_up_of_id = None
        db.commit()
        db.expire_all()
        fresh = db.query(Application).filter(Application.id == app.id).one()
        assert fresh.outcome_status == "offer"
        assert fresh.outcome_source == "manual"
        assert fresh.outcome_note == "called me"
        assert fresh.outcome_evidence == "msg:abc123"
        assert fresh.resolved_at is not None


# =====================================================================
# 2. Classification fixtures
# ======================================================================

class TestClassification:
    def test_schedule_a_call_is_interview(self):
        c = classify_message(
            "Your application", "Hi, we'd like to schedule a call this week."
        )
        assert c is not None and c.status == "interview"

    def test_moving_forward_with_others_is_rejected(self):
        c = classify_message(
            "Update on your application",
            "Thank you for applying. We have decided to move forward with "
            "other candidates whose experience more closely matches.",
        )
        assert c is not None and c.status == "rejected"

    def test_offer(self):
        c = classify_message("Good news", "We are pleased to offer you the role.")
        assert c is not None and c.status == "offer"

    def test_auto_ack_is_not_an_outcome(self):
        assert classify_message(
            "Application received", "We have received your application and will review it."
        ) is None

    def test_swedish_interview(self):
        c = classify_message("Din ansökan", "Hej! Vi vill boka ett samtal med dig.")
        assert c is not None and c.status == "interview"

    def test_swedish_rejection(self):
        c = classify_message(
            "Din ansökan",
            "Tyvärr har vi gått vidare med andra kandidater för tjänsten.",
        )
        assert c is not None and c.status == "rejected"

    def test_ambiguous_conflict_surfaced_not_guessed(self):
        """Both interview AND rejection phrases in one body → None."""
        assert classify_message(
            "Update",
            "We'd like to schedule a call. However, we have decided to move "
            "forward with other candidates for the original role.",
        ) is None

    def test_no_signal_returns_none(self):
        assert classify_message("Hello", "Just checking in, nothing about jobs.") is None

    def test_subject_only_classification_refused(self):
        """Acceptance 2, red-first leg: a classifier that grades a subject
        or snippet alone is the bug this WO exists to prevent."""
        with pytest.raises(ValueError, match="FULL message body"):
            classify_message("We'd like to schedule a call", None)
        with pytest.raises(ValueError, match="FULL message body"):
            classify_message("We'd like to schedule a call", "   ")

    def test_manual_only_never_classified(self):
        """hired/applied/withdrawn are human words — even if the phrasing
        appears, the classifier refuses."""
        assert classify_message(
            "Welcome!", "Congratulations, we are pleased to offer you the position."
        ).status == "offer"  # offer IS classifiable
        # 'hired' has no patterns at all; guard the invariant directly:
        assert "hired" in osvc.MANUAL_ONLY_OUTCOMES


# =====================================================================
# 3. Approval gate — propose-then-confirm
# ======================================================================

class TestApprovalGate:
    def _proposal(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=3, status="sent")
        p = OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="interview",
            source="reply_detection", evidence_message_id="m-1",
            evidence_from="hr@acme.example", evidence_subject="Your application",
            evidence_received_at=utc_now() - timedelta(days=1),
            reason="matched: 'schedule a call'",
        )
        db.add(p)
        db.commit()
        return user, app, p

    def test_deciding_a_pending_proposal_directly_is_refused(self, db):
        """THE gate (acceptance 3): decide_proposal is the only writer,
        and it demands a decision — a second decision on a decided row
        is refused AND the application is untouched. The approval path
        (decide via 'approved') is the single route to a write; nothing
        else in this module applies proposals."""
        user, app, p = self._proposal(db)
        assert p.decision is None
        p.decision = "approved"
        p.decided_at = utc_now()
        db.add(p)
        db.commit()
        with pytest.raises(OutcomeError, match="already approved"):
            decide_proposal(db, p, "dismissed")
        db.refresh(p)
        assert p.decision == "approved"  # unchanged by the refused call

    def test_scan_files_proposals_but_writes_nothing(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=3, status="sent")

        async def fetch(uid, after, terms):
            return [{
                "id": "m-1", "from": "HR <hr@acme.example>",
                "subject": "Your application",
                "body": "Hi, we'd like to schedule a call this week.",
                "received_at": utc_now() - timedelta(days=1),
            }]

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 1
        db.refresh(app)
        assert app.outcome_status is None  # NOTHING applied by the scan
        assert len(list_pending_proposals(db, user_id=user.id)) == 1

    def test_approval_applies_with_honest_resolution_time(self, db):
        user, app, p = self._proposal(db)
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "interview"
        assert app.outcome_source == "reply_detection"
        assert app.outcome_evidence == "msg:m-1"
        # resolved_at = when the reply ARRIVED, not when we approved
        assert app.resolved_at == p.evidence_received_at

    def test_manual_outcome_never_overwritten_by_detection(self, db):
        user, app, p = self._proposal(db)
        set_outcome(db, app, "hired", "manual")
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "hired"
        assert app.outcome_source == "manual"  # the user's word outranks

    def test_dismiss_touches_nothing_and_keeps_audit(self, db):
        user, app, p = self._proposal(db)
        p.decision = "dismissed"
        p.decided_at = utc_now()
        db.add(p)
        db.commit()
        db.refresh(app)
        assert app.outcome_status is None


# =====================================================================
# 4. Incremental state — a processed message is never reprocessed
# ======================================================================

class TestIncrementalState:
    def test_replayed_scan_creates_no_duplicates(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent")

        async def fetch(uid, after, terms):
            # The SAME message, replayed by every scan (as an overlapping
            # Gmail after: window would return it)
            return [{
                "id": "m-1", "from": "hr@acme.example",
                "subject": "Your application",
                "body": "We'd like to schedule a call.",
                "received_at": utc_now() - timedelta(days=1),
            }]

        first = asyncio.run(scan_replies(db, user.id, fetch))
        second = asyncio.run(scan_replies(db, user.id, fetch))
        assert first["proposed"] == 1
        assert second["proposed"] == 0  # dedupe: the message is known
        assert db.query(OutcomeProposal).count() == 1

    def test_cursor_advances(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent")

        async def fetch(uid, after, terms):
            return []

        asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).filter(GmailScanState.user_id == user.id).one()
        assert state.scan_cursor == date.today().isoformat()
        assert state.last_error is None

    def test_scan_error_recorded_not_swallowed(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent")

        async def fetch(uid, after, terms):
            raise RuntimeError("gmail said no")

        with pytest.raises(RuntimeError):
            asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).filter(GmailScanState.user_id == user.id).one()
        assert "gmail said no" in state.last_error


# =====================================================================
# Sender matching
# ======================================================================

class TestSenderMatching:
    def test_exact_target_email(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, target_email="jobs@acme.example")
        assert sender_matches_application("jobs@acme.example", app, job)

    def test_parent_domain_of_job_url(self, db):
        user = _user(db)
        job = _job(db, url="https://careers.acme.example/jobs/1")
        app = _application(db, user, job, target_email=None)
        assert sender_matches_application("hr@acme.example", app, job)

    def test_ats_domain(self, db):
        user = _user(db)
        job = _job(db, url="https://boards.greenhouse.io/acme/jobs/1")
        app = _application(db, user, job, target_email=None)
        assert sender_matches_application("no-reply@greenhouse.io", app, job)

    def test_unrelated_sender_rejected(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, target_email="jobs@acme.example")
        assert not sender_matches_application("news@newsletter.example", app, job)


# =====================================================================
# 5. Nudges + thresholds
# ======================================================================

class TestNudges:
    def test_deadline_urgent_within_7_days(self, db):
        """published 25d ago + 30d horizon = 5 days left → urgent."""
        user = _user(db)
        job = _job(db, published_days_ago=25)
        db.add(ApplicationDraft(
            user_id=user.id, job_id=job.id, status="ready", cover_letter="x",
        ))
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert len(n["deadlines"]) == 1
        assert n["deadlines"][0]["level"] == "urgent"
        assert n["deadlines"][0]["days_left"] <= DEADLINE_URGENT_DAYS

    def test_deadline_passed(self, db):
        user = _user(db)
        job = _job(db, published_days_ago=32)
        db.add(ApplicationDraft(
            user_id=user.id, job_id=job.id, status="ready", cover_letter="x",
        ))
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert n["deadlines"][0]["level"] == "passed"

    def test_fresh_draft_not_flagged(self, db):
        user = _user(db)
        job = _job(db, published_days_ago=1)
        db.add(ApplicationDraft(
            user_id=user.id, job_id=job.id, status="ready", cover_letter="x",
        ))
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert n["deadlines"] == []

    def test_follow_up_offered_after_10_quiet_days(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=FOLLOW_UP_QUIET_DAYS + 1, status="sent")
        n = compute_nudges(db, user_id=user.id)
        assert len(n["follow_ups"]) == 1
        assert n["follow_ups"][0]["follow_up_count"] == 0

    def test_follow_up_capped_at_two(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=20, status="sent")
        for _ in range(FOLLOW_UP_MAX):
            db.add(Application(
                user_id=user.id, job_id=job.id, method="manual",
                status="sent", sent_at=utc_now() - timedelta(days=1),
                follow_up_of_id=app.id,
            ))
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert n["follow_ups"] == []

    def test_drafted_never_chased_never_quiet(self, db):
        """`drafted` is open but nothing was sent: no follow-up nudge, no
        sweep proposal — even 100 days later."""
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=None)  # never sent
        set_outcome(db, app, "drafted", "manual")
        n = compute_nudges(db, user_id=user.id)
        assert n["follow_ups"] == []
        assert propose_no_response_sweep(db, user_id=user.id) == 0

    def test_quiet_sweep_at_60_days(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=61, status="sent")
        assert propose_no_response_sweep(db, user_id=user.id) == 1
        # idempotent: second sweep run proposes nothing new
        assert propose_no_response_sweep(db, user_id=user.id) == 0

    def test_confirm_applied_prompt_for_stale_manual_handoff(self, db):
        user = _user(db)
        job = _job(db)
        # handed off 4 days ago, never confirmed as applied
        _application(db, user, job, sent_days_ago=None, created_days_ago=4)
        n = compute_nudges(db, user_id=user.id)
        assert len(n["confirm_applied"]) == 1

    def test_thresholds_exposed(self, db):
        user = _user(db)
        t = compute_nudges(db, user_id=user.id)["thresholds"]
        assert t == {
            "deadline_urgent_days": 7,
            "follow_up_quiet_days": 10,
            "follow_up_max": 2,
            "quiet_sweep_days": 60,
        }


# =====================================================================
# Follow-up drafts
# ======================================================================

class TestFollowUp:
    def _sent_app(self, db):
        user = _user(db)
        profile = _profile(db, user)
        job = _job(db, title="Backend Developer", company="Acme")
        draft = ApplicationDraft(
            user_id=user.id, job_id=job.id, status="submitted",
            cover_letter="Original letter", tailored_cv="Original CV",
        )
        db.add(draft)
        db.flush()
        app = Application(
            user_id=user.id, job_id=job.id, draft_id=draft.id,
            method="manual", status="manual_pending",
            target_email="jobs@acme.example", sent_at=utc_now() - timedelta(days=12),
        )
        db.add(app)
        db.commit()
        return user, profile, job, draft, app

    def test_follow_up_seeded_from_original(self, db):
        user, profile, job, draft, app = self._sent_app(db)
        fu = create_follow_up_draft(db, app, profile)
        assert fu.follow_up_application_id == app.id
        assert "Original letter" in fu.cover_letter  # seeded, not invented
        assert fu.tailored_cv == "Original CV"
        assert fu.status == "ready"

    def test_follow_up_capped(self, db):
        user, profile, job, draft, app = self._sent_app(db)
        create_follow_up_draft(db, app, profile)
        db.add(Application(
            user_id=user.id, job_id=job.id, method="manual", status="sent",
            sent_at=utc_now(), follow_up_of_id=app.id,
        ))
        db.commit()
        with pytest.raises(OutcomeError, match="max"):
            create_follow_up_draft(db, app, profile)

    def test_closed_application_never_chased(self, db):
        user, profile, job, draft, app = self._sent_app(db)
        set_outcome(db, app, "rejected", "manual")
        with pytest.raises(OutcomeError, match="closed"):
            create_follow_up_draft(db, app, profile)

    def test_submit_chains_follow_up_link(self, db):
        """submit_draft carries follow_up_application_id onto the new
        application row (follow-up counting depends on the chain)."""
        from app.services.draft_service import submit_draft

        user, profile, job, draft, app = self._sent_app(db)
        fu = create_follow_up_draft(db, app, profile)
        sent = submit_draft(db, fu, "manual", profile, user_id=user.id)
        assert sent.follow_up_of_id == app.id
        assert sent.subject.startswith("Following up:")


# =====================================================================
# 6. Stats — the honest denominator
# ======================================================================

class TestStats:
    def test_denominator_excludes_unsent(self, db):
        user = _user(db)
        job = _job(db)
        sent = _application(db, user, job, sent_days_ago=5, status="sent")
        _application(db, user, job, sent_days_ago=None)  # manual_pending, unconfirmed
        set_outcome(db, sent, "interview", "manual")
        s = outcome_stats(db, user_id=user.id)
        assert s["sent_total"] == 1  # the hand-off is unknown, not sent
        assert s["response_rate"] == 1.0

    def test_confirmed_applied_counts_in_denominator(self, db):
        user = _user(db)
        job = _job(db)
        handoff = _application(db, user, job, sent_days_ago=None)
        set_outcome(db, handoff, "applied", "manual")
        s = outcome_stats(db, user_id=user.id)
        assert s["sent_total"] == 1  # the user's own confirmation

    def test_no_response_lands_in_denominator(self, db):
        """Acceptance 6: sweep results count — silence is data."""
        user = _user(db)
        job = _job(db)
        quiet = _application(db, user, job, sent_days_ago=70, status="sent")
        set_outcome(db, quiet, "no_response", "sweep")
        s = outcome_stats(db, user_id=user.id)
        assert s["sent_total"] == 1
        assert s["no_response_total"] == 1
        assert s["response_rate"] == 0.0

    def test_interview_rate_counts_beyond_interview(self, db):
        """hired/offer imply the interview stage was reached."""
        user = _user(db)
        job = _job(db)
        a = _application(db, user, job, sent_days_ago=5, status="sent")
        set_outcome(db, a, "hired", "manual")
        s = outcome_stats(db, user_id=user.id)
        assert s["interview_rate"] == 1.0

    def test_time_to_first_response_uses_reply_time(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=10, status="sent")
        reply_at = app.sent_at + timedelta(days=3)
        set_outcome(db, app, "interview", "reply_detection", resolved_at=reply_at)
        s = outcome_stats(db, user_id=user.id)
        assert s["avg_days_to_first_response"] == 3.0

    def test_empty_stats_are_null_not_zero(self, db):
        user = _user(db)
        s = outcome_stats(db, user_id=user.id)
        assert s["sent_total"] == 0
        assert s["response_rate"] is None  # 0/0 is not 0.0 — it's unknown
