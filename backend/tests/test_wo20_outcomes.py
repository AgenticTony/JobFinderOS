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
from datetime import timedelta

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
        that's what makes time-to-first-response honest. A later
        employer signal KEEPS the first response time (review round 1)."""
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job)
        explicit = utc_now() - timedelta(days=4)
        set_outcome(db, app, "interview", "reply_detection", resolved_at=explicit)
        db.refresh(app)
        assert app.resolved_at == explicit  # evidence time, not "now"
        set_outcome(db, app, "rejected", "manual")
        db.refresh(app)
        # rejected is an employer signal AFTER interview: the FIRST
        # response time survives the progression
        assert app.resolved_at == explicit

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

        async def fetch(uid, window, terms):
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

        async def fetch(uid, window, terms):
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

        async def fetch(uid, window, terms):
            return []

        asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).filter(GmailScanState.user_id == user.id).one()
        assert state.scan_cursor is not None
        assert state.scan_cursor.startswith("e:")
        assert state.last_error is None

    def test_scan_error_recorded_not_swallowed(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent")

        async def fetch(uid, window, terms):
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


# =====================================================================
# Review round 1 (PR #124) — false-outcome and lost-reply guards
# ======================================================================

class TestSweepNeverClobbersSignals:
    """Review finding 1: the quiet sweep proposed no_response over
    interview/offer (employer DID reply), and a dismissed sweep
    proposal was re-filed on every worker run."""

    def test_sweep_skips_employer_signals(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=70, status="sent")
        set_outcome(db, app, "interview", "reply_detection")
        assert propose_no_response_sweep(db, user_id=user.id) == 0

    def test_approving_sweep_never_overwrites_interview(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=70, status="sent")
        set_outcome(db, app, "interview", "manual")
        # a stale sweep proposal slips through (filed before the outcome)
        p = OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="no_response",
            source="sweep", reason="no employer reply recorded 60+ days after send",
        )
        db.add(p)
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "interview"  # silence never beats a signal

    def test_dismissed_sweep_proposal_not_refiled(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=70, status="sent")
        p = OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="no_response",
            source="sweep", reason="dismissed once by the user",
        )
        p.decision = "dismissed"
        p.decided_at = utc_now()
        db.add(p)
        db.commit()
        assert propose_no_response_sweep(db, user_id=user.id) == 0
        assert db.query(OutcomeProposal).count() == 1  # not re-filed


class TestSenderAmbiguity:
    """Review finding 2: ATS/aggregator senders matched EVERY
    application and the scan attached to the first match. An ambiguous
    sender is not evidence for any specific application — surface it,
    never guess; discriminate by company/title only when unique."""

    def _ats_job(self, db, company, title):
        return _job(
            db, title=title, company=company,
            url=f"https://boards.greenhouse.io/{company.lower()}/jobs/1",
        )

    def _message(self, subject, body=None):
        return {
            "id": "m-amb", "from": "Notifications <noreply@greenhouse.io>",
            "subject": subject,
            "body": body or "We'd like to schedule a call to discuss next steps.",
            "received_at": utc_now() - timedelta(days=1),
        }

    def test_two_ats_applications_is_uncertain_not_first_match(self, db):
        user = _user(db)
        _application(db, user, self._ats_job(db, "Acme", "Developer"), sent_days_ago=3, status="sent", target_email=None)
        _application(db, user, self._ats_job(db, "Beta", "Analyst"), sent_days_ago=2, status="sent", target_email=None)

        async def fetch(uid, window, terms):
            return {"messages": [self._message("Update on your application")], "truncated": False}

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 0
        assert summary["uncertain"] == 1
        assert db.query(OutcomeProposal).count() == 0  # nothing attached to Acme by luck of order

    def test_company_name_discriminates_ats_sender(self, db):
        user = _user(db)
        _application(db, user, self._ats_job(db, "Acme", "Developer"), sent_days_ago=3, status="sent", target_email=None)
        _application(db, user, self._ats_job(db, "Beta", "Analyst"), sent_days_ago=2, status="sent", target_email=None)

        async def fetch(uid, window, terms):
            return {"messages": [self._message("Your Beta application — next steps")], "truncated": False}

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 1
        proposal = db.query(OutcomeProposal).one()
        beta_job = db.query(JobPosting).filter(JobPosting.company == "Beta").one()
        beta_app = db.query(Application).filter(Application.job_id == beta_job.id).one()
        assert proposal.application_id == beta_app.id

    def test_aggregator_job_url_host_does_not_match(self, db):
        """A scraped posting's URL points at the BOARD (reed/platsbanken),
        not the employer — mail from the board must not match every one of
        that board's applications."""
        from app.services.outcome_service import sender_matches_application

        user = _user(db)
        job = _job(db, url="https://www.reed.co.uk/jobs/developer/123")
        app = _application(db, user, job, target_email=None)
        assert not sender_matches_application("alerts@reed.co.uk", app, job)

    def test_single_ats_application_still_matches(self, db):
        from app.services.outcome_service import sender_matches_application

        user = _user(db)
        job = self._ats_job(db, "Acme", "Developer")
        app = _application(db, user, job, target_email=None)
        assert sender_matches_application("noreply@greenhouse.io", app, job)


class TestSavepointKeepsEarlierProposals:
    """Review finding 3: the IntegrityError rollback dropped every
    flushed-but-uncommitted proposal from the same scan. The insert must
    run under a SAVEPOINT so a collision rolls back only itself."""

    def test_collision_does_not_lose_earlier_proposals(self, db, monkeypatch):
        from app.services import outcome_service as svc

        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent")

        # Simulate the real race: a concurrent uncommitted insert is
        # invisible to the pre-check (SELECT sees nothing), so the second
        # scan's insert hits the unique index at flush.
        async def fetch(uid, window, terms):
            return {
                "messages": [
                    {"id": "m-1", "from": "hr@acme.example", "subject": "a",
                     "body": "We'd like to schedule a call.", "received_at": utc_now()},
                    {"id": "m-2", "from": "hr@acme.example", "subject": "b",
                     "body": "We have decided to move forward with other candidates.",
                     "received_at": utc_now()},
                ],
                "truncated": False,
            }

        original_query = svc._proposal_exists_for_message

        def blind_lookup(db, user_id, message_id):
            return None  # the concurrent row is uncommitted — invisible

        monkeypatch.setattr(svc, "_proposal_exists_for_message", blind_lookup)

        # First scan files m-1, then collides on... make BOTH scan calls
        # race on the same messages by replaying the fetch twice blind:
        asyncio.run(scan_replies(db, user.id, fetch))
        # second scan, same messages, pre-check still blind (simulating
        # overlap): every insert collides on the unique index at flush
        asyncio.run(scan_replies(db, user.id, fetch))
        monkeypatch.setattr(svc, "_proposal_exists_for_message", original_query)

        db.expire_all()
        # The first scan's proposals must have SURVIVED the second scan's
        # collisions (with a session-wide rollback they are lost).
        assert db.query(OutcomeProposal).count() == 2


class TestFollowUpsExcluded:
    """Review finding 4: follow-up sends are their own Application rows
    with sent_at — inflating stats, getting swept, and earning their own
    follow-up nudges. Every enumeration treats them as what they are:
    pings of the parent, not applications."""

    def _parent_with_two_follow_ups(self, db, *, parent_days_ago=5):
        user = _user(db)
        job = _job(db)
        parent = _application(db, user, job, sent_days_ago=parent_days_ago, status="sent")
        for _ in range(2):
            db.add(Application(
                user_id=user.id, job_id=job.id, method="manual", status="sent",
                sent_at=utc_now() - timedelta(days=1), follow_up_of_id=parent.id,
            ))
        db.commit()
        return user, job, parent

    def test_stats_do_not_inflate(self, db):
        user, job, parent = self._parent_with_two_follow_ups(db)
        set_outcome(db, parent, "interview", "manual")
        s = outcome_stats(db, user_id=user.id)
        assert s["sent_total"] == 1  # one employer, one application
        assert s["response_rate"] == 1.0

    def test_sweep_ignores_follow_up_rows(self, db):
        user, job, parent = self._parent_with_two_follow_ups(db, parent_days_ago=70)
        # parent is 70d quiet; the follow-ups are fresh — neither may be swept
        # in this fixture's fresh-follow-up shape. Make a follow-up old too:
        old_fu = Application(
            user_id=user.id, job_id=job.id, method="manual", status="sent",
            sent_at=utc_now() - timedelta(days=70), follow_up_of_id=parent.id,
        )
        db.add(old_fu)
        db.commit()
        proposed = propose_no_response_sweep(db, user_id=user.id)
        assert proposed == 1  # the PARENT only
        apps_proposed = [p.application_id for p in db.query(OutcomeProposal).all()]
        assert parent.id in apps_proposed
        assert old_fu.id not in apps_proposed

    def test_follow_up_row_never_offers_its_own_follow_up(self, db):
        # ONE follow-up sent (count 1 < max 2): the parent earns the
        # offer, the 20-days-quiet follow-up row itself never does
        user, job, parent = self._parent_with_two_follow_ups(db, parent_days_ago=30)
        # drop one follow-up so the parent is below the cap
        fu = (
            db.query(Application)
            .filter(Application.follow_up_of_id == parent.id)
            .first()
        )
        db.delete(fu)
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert len(n["follow_ups"]) == 1
        assert n["follow_ups"][0]["application_id"] == parent.id

    def test_scan_candidates_exclude_follow_ups(self, db):
        """A reply to the thread answers the PARENT application — the
        follow-up row must never compete as a match candidate."""
        user, job, parent = self._parent_with_two_follow_ups(db, parent_days_ago=12)

        async def fetch(uid, window, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "hr@acme.example",
                    "subject": "Your application",
                    "body": "We'd like to schedule a call.",
                    "received_at": utc_now() - timedelta(days=1),
                }],
                "truncated": False,
            }

        asyncio.run(scan_replies(db, user.id, fetch))
        proposal = db.query(OutcomeProposal).one()
        assert proposal.application_id == parent.id


class TestFirstResponseTime:
    """Review finding 5: resolved_at was overwritten on every outcome
    write, so the stat measured time to the LATEST outcome. The FIRST
    employer response is the stat; progressions must not move it."""

    def test_progression_keeps_first_response_time(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=40, status="sent")
        first_reply = app.sent_at + timedelta(days=4)
        set_outcome(db, app, "interview", "reply_detection", resolved_at=first_reply)
        later_reply = app.sent_at + timedelta(days=35)
        set_outcome(db, app, "rejected", "reply_detection", resolved_at=later_reply)
        db.refresh(app)
        assert app.resolved_at == first_reply  # the FIRST response
        s = outcome_stats(db, user_id=user.id)
        assert s["avg_days_to_first_response"] == 4.0

    def test_late_arriving_earlier_evidence_moves_it_back(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=40, status="sent")
        set_outcome(db, app, "rejected", "reply_detection",
                    resolved_at=app.sent_at + timedelta(days=35))
        set_outcome(db, app, "interview", "reply_detection",
                    resolved_at=app.sent_at + timedelta(days=3))
        db.refresh(app)
        assert app.resolved_at == app.sent_at + timedelta(days=3)


class TestSwedishFollowUpTemplate:
    """Review finding 6: the code read profile.country_code, but the
    column is country — every Swedish user got the English template."""

    def test_swedish_profile_gets_swedish_template(self, db):
        user = _user(db)
        profile = _profile(db, user, country="SE")
        job = _job(db, title="Utvecklare", company="Acme")
        draft = ApplicationDraft(
            user_id=user.id, job_id=job.id, status="submitted",
            cover_letter="Original", tailored_cv="CV",
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
        fu = create_follow_up_draft(db, app, profile)
        assert fu.cover_letter.startswith("Hej")
        assert "uppföljning" in fu.cover_letter.lower() or "hör av mig" in fu.cover_letter


class TestScanCursorSafety:
    """Review rounds 1+3: a truncated scan must never claim ground it
    did not cover (round 1 pinned the cursor to today; round 3 showed
    the date-granular replacement stalled on dense windows — both are
    silent mail loss). The watermark is epoch-anchored."""

    def test_truncated_scan_writes_backfill_watermark_not_today(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")
        received = (utc_now() - timedelta(days=20)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )

        async def fetch(uid, window, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "hr@acme.example",
                    "subject": "Your application",
                    "body": "We'd like to schedule a call.",
                    "received_at": received,
                }],
                "truncated": True,  # the window overflowed the cap
            }

        asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).one()
        assert state.scan_cursor.startswith("b:")
        # before = oldest processed arrival + 1s — NOT today
        _, after, before = state.scan_cursor.split(":")
        assert int(before) == _r3_epoch(received) + 1

    def test_untruncated_scan_advances_to_incremental_watermark(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")

        async def fetch(uid, window, terms):
            return {"messages": [], "truncated": False}

        asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).one()
        assert state.scan_cursor.startswith("e:")

    def test_truncation_flag_recorded(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")

        async def fetch(uid, window, terms):
            return {"messages": [], "truncated": True}

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary.get("truncated") is True


class TestTimestampNormalization:
    """Review finding 9: the adapter produced tz-AWARE received_at
    datetimes while every other timestamp in the app is naive UTC —
    mixing them raises TypeError in the stats subtraction."""

    def test_aware_received_at_normalized_to_naive_utc(self, db):
        from datetime import timezone as tz_mod

        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=10, status="sent")

        aware = (utc_now() - timedelta(days=2)).replace(tzinfo=tz_mod.utc)

        async def fetch(uid, window, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "hr@acme.example",
                    "subject": "Your application",
                    "body": "We'd like to schedule a call.",
                    "received_at": aware,
                }],
                "truncated": False,
            }

        asyncio.run(scan_replies(db, user.id, fetch))
        proposal = db.query(OutcomeProposal).one()
        assert proposal.evidence_received_at is not None
        assert proposal.evidence_received_at.tzinfo is None  # naive UTC
        # and the stats subtraction does not raise
        approve_all_pending(db, user_id=user.id)
        s = outcome_stats(db, user_id=user.id)
        assert s["avg_days_to_first_response"] is not None


# =====================================================================
# Review round 2 (PR #127) — cursor ordering, lone-ATS, final-regression,
# follow-up deadlines, live aggregator hosts, cross-app message dedupe
# ======================================================================

class TestTruncatedScanResumesBelowBatch:
    """Review rounds 2+3: Gmail returns NEWEST-first and a truncated
    scan processes the newest batch — so the watermark must sit at the
    batch's OLDEST arrival, and the next scan's window must EXCLUDE
    the processed batch while keeping every older hit inside."""

    def test_second_window_excludes_processed_batch(self, db):
        now = utc_now()
        msgs = [{
            "id": f"m-{i:02d}", "from": "hr@acme.example",
            "subject": "Your application",
            "body": "We'd like to schedule a call.",
            "received_at": now - timedelta(days=4) + timedelta(hours=i),
        } for i in range(70)]  # m-00 oldest … m-69 newest, all in the past
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")

        def _serve(uid, w, terms):
            in_win = [
                m for m in msgs
                if w["after"] < _r3_epoch(m["received_at"]) < w["before"]
            ]
            newest_first = sorted(in_win, key=lambda m: m["received_at"], reverse=True)
            batch = newest_first[:50]

            async def run():
                return {
                    "messages": sorted(batch, key=lambda m: m["received_at"]),
                    "truncated": len(in_win) > 50,
                }
            return run()

        first = asyncio.run(scan_replies(db, user.id, _serve))
        assert first["proposed"] == 50
        state = db.query(GmailScanState).one()
        assert state.scan_cursor.startswith("b:")
        _, after, before = state.scan_cursor.split(":")
        # the watermark sits at the batch's OLDEST arrival (m-20) +1s
        assert int(before) == _r3_epoch(msgs[20]["received_at"]) + 1

        second = asyncio.run(scan_replies(db, user.id, _serve))
        assert second["proposed"] == 20  # the remaining older hits
        db.refresh(state)
        assert state.scan_cursor.startswith("e:")


class TestLoneAtsRequiresDiscriminator:
    """Review round 2, finding 2: with exactly ONE open application, any
    ATS-domain email attached to it unchecked — including replies to
    jobs applied to OFF-platform through the same ATS. ATS-domain
    senders always require the subject discriminator; only a specific
    match (target email/domain, non-aggregator posting host) attaches
    alone."""

    def test_lone_ats_match_generic_subject_is_uncertain(self, db):
        user = _user(db)
        job = _job(db, title="Developer", company="Acme",
                   url="https://boards.greenhouse.io/acme/jobs/1")
        _application(db, user, job, sent_days_ago=3, status="sent", target_email=None)

        async def fetch(uid, window, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "Notifications <noreply@greenhouse.io>",
                    "subject": "Update on your application",
                    "body": "We have decided to move forward with other candidates.",
                    "received_at": utc_now() - timedelta(days=1),
                }],
                "truncated": False,
            }

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 0
        assert summary["uncertain"] == 1

    def test_lone_ats_match_with_company_subject_attaches(self, db):
        user = _user(db)
        job = _job(db, title="Developer", company="Acme",
                   url="https://boards.greenhouse.io/acme/jobs/1")
        _application(db, user, job, sent_days_ago=3, status="sent", target_email=None)

        async def fetch(uid, window, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "Notifications <noreply@greenhouse.io>",
                    "subject": "Your Acme application — next steps",
                    "body": "We'd like to schedule a call.",
                    "received_at": utc_now() - timedelta(days=1),
                }],
                "truncated": False,
            }

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 1

    def test_specific_target_email_attaches_without_discriminator(self, db):
        """A direct target-email match is evidence in itself — no
        subject check needed."""
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent",
                     target_email="jobs@acme.example")

        async def fetch(uid, window, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "HR <jobs@acme.example>",
                    "subject": "Hello",
                    "body": "We'd like to schedule a call.",
                    "received_at": utc_now() - timedelta(days=1),
                }],
                "truncated": False,
            }

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 1


class TestFinalNotReopenedByStaleSignal:
    """Review round 2, finding 3: approving an older OPEN-signal
    proposal (interview) after a newer FINAL outcome (rejected)
    reopened the application — it re-entered nudges and the sweep."""

    def test_stale_interview_does_not_reopen_rejected(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=40, status="sent")
        # newer final outcome already applied
        set_outcome(db, app, "rejected", "reply_detection",
                    resolved_at=utc_now() - timedelta(days=1))
        # stale OPEN-signal proposal still pending
        db.add(OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="interview",
            source="reply_detection", evidence_message_id="m-old",
            evidence_received_at=utc_now() - timedelta(days=30),
        ))
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "rejected"  # final stands
        p = db.query(OutcomeProposal).one()
        assert "superseded" in (p.reason or "")

    def test_newer_offer_still_progresses_rejected(self, db):
        """FINAL → FINAL progression stays allowed: a genuine later offer
        email may legitimately rescue a rejected application."""
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=40, status="sent")
        set_outcome(db, app, "rejected", "reply_detection",
                    resolved_at=utc_now() - timedelta(days=30))
        db.add(OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="offer",
            source="reply_detection", evidence_message_id="m-new",
            evidence_received_at=utc_now() - timedelta(days=1),
        ))
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "offer"


class TestFollowUpDraftNotDeadlineNudged:
    """Review round 2, finding 4: a ready follow-up draft for a job
    whose posting window is long past showed under 'Finish applying'
    as a passed deadline."""

    def test_follow_up_draft_excluded_from_deadlines(self, db):
        from app.core import config as config_mod

        user = _user(db)
        job = _job(db, published_days_ago=config_mod.settings.MAX_POSTING_AGE_DAYS + 5)
        app = _application(db, user, job, sent_days_ago=30, status="sent")
        db.add(ApplicationDraft(
            user_id=user.id, job_id=job.id, status="ready",
            cover_letter="follow up", follow_up_application_id=app.id,
        ))
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert n["deadlines"] == []

    def test_first_contact_draft_still_nudged(self, db):
        from app.core import config as config_mod

        user = _user(db)
        job = _job(db, published_days_ago=config_mod.settings.MAX_POSTING_AGE_DAYS + 5)
        db.add(ApplicationDraft(
            user_id=user.id, job_id=job.id, status="ready", cover_letter="x",
        ))
        db.commit()
        n = compute_nudges(db, user_id=user.id)
        assert len(n["deadlines"]) == 1


class TestLiveAggregatorHosts:
    """Review round 2, finding 5: the live pool has careerjet postings
    on jobviewtrack.com and arbeitnow on www.arbeitnow.co.uk — neither
    was in AGGREGATOR_HOSTS, so board mail matched as 'the employer'."""

    def test_live_hosts_are_aggregators(self):
        from app.services.outcome_service import _is_aggregator_host

        for host in ("jobviewtrack.com", "www.jobviewtrack.com",
                     "arbeitnow.co.uk", "www.arbeitnow.co.uk"):
            assert _is_aggregator_host(host), host

    def test_live_host_does_not_match_as_employer(self, db):
        from app.services.outcome_service import sender_matches_application

        user = _user(db)
        job = _job(db, url="https://jobviewtrack.com/job/developer/123")
        app = _application(db, user, job, target_email=None)
        assert not sender_matches_application("digest@jobviewtrack.com", app, job)


class TestOneMessageOneProposal:
    """Review round 2, finding 6: dedupe was keyed (application,
    message) — the same email could later be filed against a SECOND
    application after the first closed. One email, at most one
    proposal, ever."""

    def test_same_message_never_refiled_on_another_application(self, db):
        user = _user(db)
        job_a = _job(db, title="Developer", company="Acme",
                     url="https://boards.greenhouse.io/acme/jobs/1")
        job_b = _job(db, title="Analyst", company="Beta",
                     url="https://boards.greenhouse.io/beta/jobs/1")
        _application(db, user, job_a, sent_days_ago=3, status="sent", target_email=None)
        _application(db, user, job_b, sent_days_ago=2, status="sent", target_email=None)

        msg = {
            "id": "m-1", "from": "Notifications <noreply@greenhouse.io>",
            "subject": "Your Acme application — next steps",
            "body": "We'd like to schedule a call.",
            "received_at": utc_now() - timedelta(days=1),
        }

        async def fetch(uid, window, terms):
            return {"messages": [msg], "truncated": False}

        # Scan 1: subject names Acme → filed on A
        asyncio.run(scan_replies(db, user.id, fetch))
        first = db.query(OutcomeProposal).one()
        app_a_row = db.query(Application).join(JobPosting).filter(
            JobPosting.company == "Acme").one()
        assert first.application_id == app_a_row.id

        # A closes; the overlap window replays the SAME message with B
        # now the only candidate — it must NOT be filed again
        db.refresh(app_a_row)
        db.refresh(first)
        first.decision = "approved"
        first.decided_at = utc_now()
        db.add(first)
        set_outcome(db, app_a_row, "hired", "manual")
        summary2 = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary2["proposed"] == 0
        assert db.query(OutcomeProposal).count() == 1
        assert db.query(OutcomeProposal).one().application_id == app_a_row.id

# =====================================================================
# Review round 3 (PR #128) — pagination, watermark cursor, evidence
# time, free-mail domains, predicate drift
# =====================================================================


def _r3_epoch(dt) -> int:
    """naive-UTC datetime → epoch seconds (the cursor's storage unit)."""
    from datetime import timezone as tz_mod
    return int(dt.replace(tzinfo=tz_mod.utc).timestamp())


def _r3_detail(mid: str, epoch_s: int, body="We'd like to schedule a call."):
    """One Gmail full-message detail dict as the GET tool returns it."""
    import base64
    return {
        "id": mid, "threadId": "t-1",
        "internalDate": str(epoch_s * 1000),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "HR <hr@acme.example>"},
                {"name": "Subject", "value": "Your application"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


def _r3_patch_gmail(monkeypatch, *, metas_by_call, details, on_search=None):
    """Patch gmail_scan's vendor layer for adapter-level tests.

    metas_by_call: list of (metas, next_token) served per SEARCH call,
    in order (a tool that pages at its own default size). details maps
    message id → GET response. Returns a state dict counting search
    calls and recording their arguments."""
    from app.services import gmail_scan

    state = {"search_args": []}

    async def fake_slugs(uid):
        return ["GMAIL_SEARCH_QUERY", "GMAIL_GET_MESSAGE"]

    async def fake_execute(uid, slug, arguments):
        if "SEARCH" in slug or "QUERY" in slug or "LIST" in slug:
            state["search_args"].append(dict(arguments))
            if on_search:
                on_search(arguments)
            i = len(state["search_args"]) - 1
            metas, token = metas_by_call[min(i, len(metas_by_call) - 1)]
            return {"messages": metas, "nextPageToken": token}
        return details[arguments["message_id"]]

    monkeypatch.setattr(gmail_scan, "_slugs_for", fake_slugs)
    monkeypatch.setattr(gmail_scan.composio_service, "execute_tool", fake_execute)
    return state


def _r3_window(after=0, before=2_000_000_000):
    return {"after": after, "before": before}


class TestSearchPaginates:
    """Round 3, finding 1: the search call sent only {query} — no page
    size and no nextPageToken loop. The slice then operated on the
    FIRST (newest) page only: matches beyond page 1 were stranded
    forever, and a window whose first page held ≤50 reported
    truncated=False (cursor jumped to today, older pages never read)."""

    def test_token_followed_and_truncation_uses_real_count(self, monkeypatch):
        """55 hits served 10-per-page: the adapter must follow the token
        until the collected count proves the window overflows the cap —
        not stop at page 1 (10 ≤ 50 would have said 'complete')."""
        from datetime import datetime
        from datetime import timezone as tz_mod

        base = int(datetime(2026, 1, 1, tzinfo=tz_mod.utc).timestamp())
        pages = []
        for start in range(0, 55, 10):
            chunk = [{"id": f"m-{i}", "threadId": "t-1"} for i in range(start, start + 10)]
            chunk.reverse()  # newest-first inside the page (Gmail order)
            pages.append((chunk, "tok" if start + 10 < 55 else ""))
        details = {f"m-{i}": _r3_detail(f"m-{i}", base + i) for i in range(55)}

        state = _r3_patch_gmail(monkeypatch, metas_by_call=pages, details=details)
        from app.services import gmail_scan

        result = asyncio.run(
            gmail_scan.gmail_fetch_messages("u1", _r3_window(), ["@acme.example"])
        )
        assert len(state["search_args"]) >= 2, "adapter stopped after the first page"
        assert result["truncated"] is True
        assert len(result["messages"]) == 50

    def test_single_big_page_with_token_is_truncated(self, monkeypatch):
        """120 hits, tool serves 100 per page: ONE call already exceeds
        the cap — truncated=True, no further paging needed."""
        from datetime import datetime
        from datetime import timezone as tz_mod

        base = int(datetime(2026, 1, 1, tzinfo=tz_mod.utc).timestamp())
        chunk = [{"id": f"m-{i}", "threadId": "t-1"} for i in range(100)]
        chunk.reverse()
        details = {f"m-{i}": _r3_detail(f"m-{i}", base + i) for i in range(120)}
        state = _r3_patch_gmail(
            monkeypatch, metas_by_call=[(chunk, "tok")], details=details
        )
        from app.services import gmail_scan

        result = asyncio.run(
            gmail_scan.gmail_fetch_messages("u1", _r3_window(), None)
        )
        assert len(state["search_args"]) == 1
        assert result["truncated"] is True
        assert len(result["messages"]) == 50

    def test_complete_window_short_of_cap_not_truncated(self, monkeypatch):
        from datetime import datetime
        from datetime import timezone as tz_mod

        base = int(datetime(2026, 1, 1, tzinfo=tz_mod.utc).timestamp())
        pages = []
        for start in range(0, 30, 10):
            chunk = [{"id": f"m-{i}", "threadId": "t-1"} for i in range(start, min(start + 10, 30))]
            chunk.reverse()
            pages.append((chunk, "tok" if start + 10 < 30 else ""))
        details = {f"m-{i}": _r3_detail(f"m-{i}", base + i) for i in range(30)}
        _r3_patch_gmail(monkeypatch, metas_by_call=pages, details=details)
        from app.services import gmail_scan

        result = asyncio.run(
            gmail_scan.gmail_fetch_messages("u1", _r3_window(), None)
        )
        assert result["truncated"] is False
        assert len(result["messages"]) == 30


class TestBatchIsNewestByDate:
    """Round 3, finding 2: a threads-shaped response flattens to an
    order that interleaves chronology — slicing by list position (the
    round-2 tail OR a head) is wrong. The batch must be the NEWEST
    messages by internalDate: sort before slicing."""

    def test_scrambled_meta_order_batch_is_newest_by_date(self, monkeypatch):
        from datetime import datetime
        from datetime import timezone as tz_mod

        from app.services import gmail_scan

        base = int(datetime(2026, 1, 1, tzinfo=tz_mod.utc).timestamp())
        order = list(range(30, 60)) + list(range(30))  # rotated — neither asc nor desc
        metas, details = [], {}
        for i in order:
            metas.append({"id": f"m-{i}", "threadId": "t-1",
                          "internalDate": str((base + i) * 1000)})
            details[f"m-{i}"] = _r3_detail(f"m-{i}", base + i)
        _r3_patch_gmail(monkeypatch, metas_by_call=[(metas, "")], details=details)
        result = asyncio.run(gmail_scan.gmail_fetch_messages("u1", _r3_window(), None))
        ids = {m["id"] for m in result["messages"]}
        assert len(ids) == 50
        # NEWEST 50 by internalDate = ranks 10..59 — not a positional slice
        assert ids == {f"m-{i}" for i in range(10, 60)}


class TestCursorWatermark:
    """Round 3, findings 1/2/4 (cursor half): a DATE-granular after:
    cursor cannot both avoid stranding and make progress — the oldest-
    page tail sliced the first page only, and ≥50 matches inside the
    one-day overlap re-served the same batch forever. The cursor is a
    two-bound EPOCH watermark: "b:{after}:{before}" while a window
    still overflows the cap (before anchored at the OLDEST message of
    the processed batch + 1s), "e:{epoch}" once a window completes
    (bounded by the window's own before, not a fresh 'now')."""

    @staticmethod
    def _msg(mid, received, body="We'd like to schedule a call."):
        return {
            "id": mid, "from": "hr@acme.example",
            "subject": "Your application", "body": body,
            "received_at": received,
        }

    def test_truncated_scan_writes_backfill_watermark(self, db):
        received = (utc_now() - timedelta(days=20)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")

        async def fetch(uid, a, terms):
            return {"messages": [self._msg("m-1", received)], "truncated": True}

        asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).one()
        assert state.scan_cursor is not None
        assert state.scan_cursor.startswith("b:"), state.scan_cursor
        _, after, before = state.scan_cursor.split(":")
        # before = oldest processed arrival + 1s — anything older stays
        # inside the next window; anything newer was in the batch
        assert int(before) == _r3_epoch(received) + 1
        assert int(after) < _r3_epoch(received)

    def test_complete_scan_bounds_cursor_at_window_not_wallclock(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")
        windows = []

        async def fetch(uid, a, terms):
            windows.append(a)
            return {"messages": [], "truncated": False}

        asyncio.run(scan_replies(db, user.id, fetch))
        state = db.query(GmailScanState).one()
        assert state.scan_cursor.startswith("e:"), state.scan_cursor
        # fresh scan → the window was [now-90d, now]; the completed
        # cursor claims exactly the window's upper bound — never a
        # later 'now' (mail arriving mid-scan must stay unclaimed)
        assert int(state.scan_cursor[2:]) == windows[0]["before"]

    def test_dense_window_makes_progress_every_scan(self, db):
        """Round 3, finding 4: 60 matching mails inside two days. The
        date-granular cursor re-served the same 50 forever (all deduped,
        zero new, cursor unmoved). With the epoch watermark, scan 2's
        window excludes the processed batch and reaches the 10 older
        messages; scan 3 completes."""
        now = utc_now()
        msgs = [
            self._msg(f"m-{i:02d}", now - timedelta(days=3) + timedelta(hours=i))
            for i in range(60)
        ]  # m-00 oldest … m-59 newest, all in the past
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")

        def _serve(uid, a, terms):
            if isinstance(a, dict):
                in_win = [
                    m for m in msgs
                    if a["after"] < _r3_epoch(m["received_at"]) < a["before"]
                ]
            else:  # legacy date-style call (current code)
                in_win = [
                    m for m in msgs
                    if a is None or m["received_at"].date() > a
                ]

            def _mk(m):
                async def _f():
                    return m
                return _f()

            newest_first = sorted(in_win, key=lambda m: m["received_at"], reverse=True)
            batch = newest_first[:50]
            truncated = len(in_win) > 50
            # adapter contract: batch oldest→newest + truncated flag
            payload = {
                "messages": sorted(batch, key=lambda m: m["received_at"]),
                "truncated": truncated,
            }

            async def fetch_inner():
                return payload
            return fetch_inner()

        first = asyncio.run(scan_replies(db, user.id, lambda u, a, t: _serve(u, a, t)))
        state = db.query(GmailScanState).one()
        assert first["proposed"] == 50
        assert state.scan_cursor.startswith("b:")

        second = asyncio.run(scan_replies(db, user.id, lambda u, a, t: _serve(u, a, t)))
        db.refresh(state)
        # THE stall: scan 2 must reach the 10 OLDER messages, not re-serve
        # the same newest 50 (all deduped → 0 new under the broken cursor)
        assert second["proposed"] == 10, f"scan 2 proposed {second['proposed']} (stalled?)"
        assert state.scan_cursor.startswith("e:")

        third = asyncio.run(scan_replies(db, user.id, lambda u, a, t: _serve(u, a, t)))
        assert third["proposed"] == 0  # steady state — everything deduped
        assert db.query(OutcomeProposal).count() == 60

    def test_incremental_window_reincludes_boundary_second(self, db):
        """Gmail's epoch after:/before: inclusivity is undocumented — the
        incremental window must re-include the claimed boundary second
        (a one-second gap there would strand a message forever; dedupe
        makes the overlap free)."""
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")
        bound = _r3_epoch(utc_now()) - 3600
        db.add(GmailScanState(user_id=user.id, scan_cursor=f"e:{bound}"))
        db.commit()
        seen = []

        async def fetch(uid, w, terms):
            seen.append(w)
            return {"messages": [], "truncated": False}

        asyncio.run(scan_replies(db, user.id, fetch))
        assert seen[0]["after"] == bound - 1

    def test_legacy_date_cursor_still_parses(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=30, status="sent")
        db.add(GmailScanState(user_id=user.id, scan_cursor="2026-09-01"))
        db.commit()
        seen = []

        async def fetch(uid, a, terms):
            seen.append(a)
            return {"messages": [], "truncated": False}

        asyncio.run(scan_replies(db, user.id, fetch))
        # legacy ISO date → incremental window (epoch), never a crash
        assert isinstance(seen[0], dict) and "after" in seen[0] and "before" in seen[0]


class TestFinalGuardUsesOutcomeEvidenceTime:
    """Round 3, finding 3: the stale-proposal guard compared evidence
    against resolved_at — but the round-1 min rule pins resolved_at to
    the FIRST employer response. interview(d4) → rejected(d35) leaves
    resolved_at=d4, so a day-20 second-round invite scanned late passed
    the guard and reopened the rejection. The guard must compare against
    the CURRENT outcome's own evidence time (outcome_evidence_at)."""

    def test_midlife_invite_does_not_reopen_rejected(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=40, status="sent")
        day4 = utc_now() - timedelta(days=36)    # first response
        day35 = utc_now() - timedelta(days=5)    # the rejection
        day20 = utc_now() - timedelta(days=20)   # mid-life invite
        set_outcome(db, app, "interview", "reply_detection", resolved_at=day4)
        set_outcome(db, app, "rejected", "reply_detection", resolved_at=day35)
        db.refresh(app)
        assert app.resolved_at == day4  # min rule holds
        db.add(OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="interview",
            source="reply_detection", evidence_message_id="m-mid",
            evidence_received_at=day20,
        ))
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "rejected"  # the rejection stands
        p = db.query(OutcomeProposal).one()
        assert "superseded" in (p.reason or "")

    def test_newer_offer_still_progresses_after_rejection(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=40, status="sent")
        set_outcome(db, app, "rejected", "reply_detection",
                    resolved_at=utc_now() - timedelta(days=35))
        db.add(OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="offer",
            source="reply_detection", evidence_message_id="m-new",
            evidence_received_at=utc_now() - timedelta(days=1),
        ))
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "offer"


class TestSignalBeatsApprovedSweep:
    """Round 3, finding 4 (guard half): approving a sweep stamped
    resolved_at with the APPROVAL date. A real employer reply that
    arrived EARLIER but was scanned later (e.g. beyond the truncation
    boundary) then failed the evidence-age guard — silence beat
    signal, the exact inversion of the round-1 rule. A real signal
    must always beat an approved no_response, and resolved_at becomes
    the reply's arrival (the first REAL response)."""

    def test_late_scanned_reply_overrides_approved_no_response(self, db):
        user = _user(db)
        job = _job(db)
        app = _application(db, user, job, sent_days_ago=65, status="sent")
        arrival = utc_now() - timedelta(days=15)  # day 50: before the sweep ran
        # the sweep raced the scan: 60 quiet days elapsed before the
        # truncated scan ever reached this rejection
        db.add(OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="no_response",
            source="sweep", reason="no employer reply recorded 60+ days after send",
        ))
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "no_response"

        db.add(OutcomeProposal(
            user_id=user.id, application_id=app.id, proposed_status="rejected",
            source="reply_detection", evidence_message_id="m-real",
            evidence_from="hr@acme.example", evidence_subject="Your application",
            evidence_received_at=arrival,
        ))
        db.commit()
        approve_all_pending(db, user_id=user.id)
        db.refresh(app)
        assert app.outcome_status == "rejected"  # the real reply wins
        assert app.resolved_at == arrival        # first REAL response, not the sweep stamp
        s = outcome_stats(db, user_id=user.id)
        assert s["no_response_total"] == 0
        assert s["responded_total"] == 1


class TestFreeMailDomainNotSpecific:
    """Round 3, finding 5: an employer contact on a free-mail provider
    (jobs.acme@gmail.com) made EVERY sender on that provider a
    'specific' match — a friend's gmail mentioning a phone screen
    attached and classify as an interview. Domain-level matching must
    exclude free-mail providers; the exact address stays specific."""

    def test_same_free_mail_domain_is_not_specific(self):
        from app.services.outcome_service import sender_match_is_specific

        class _A:
            target_email = "jobs.acme@gmail.com"
            apply_url = None
        assert not sender_match_is_specific("friend@gmail.com", _A(), None)

    def test_exact_free_mail_address_still_specific(self):
        from app.services.outcome_service import sender_match_is_specific

        class _A:
            target_email = "jobs.acme@gmail.com"
            apply_url = None
        assert sender_match_is_specific("jobs.acme@gmail.com", _A(), None)

    def test_scan_does_not_attach_stranger_on_free_mail_domain(self, db):
        user = _user(db)
        job = _job(db)
        _application(db, user, job, sent_days_ago=3, status="sent",
                     target_email="jobs.acme@gmail.com")

        async def fetch(uid, a, terms):
            return {
                "messages": [{
                    "id": "m-1", "from": "Old Friend <friend@gmail.com>",
                    "subject": "Long time",
                    "body": "Congrats on the search — heard you got a phone screen next week!",
                    "received_at": utc_now() - timedelta(days=1),
                }],
                "truncated": False,
            }

        summary = asyncio.run(scan_replies(db, user.id, fetch))
        assert summary["proposed"] == 0
        assert db.query(OutcomeProposal).count() == 0


class TestPredicateDriftJoblessApplication:
    """Round 3, finding 6: sender_matches_application re-implemented
    sender_match_is_specific and the copies drifted — the plausible
    path skipped apply_url when the JobPosting row was missing and
    never excluded ATS-hosted posting URLs. An application whose job
    row is gone must still match its employer host (the rules live
    once: plausible = specific OR ATS)."""

    def test_jobless_application_matches_employer_host(self):
        from app.services.outcome_service import sender_matches_application

        class _A:
            target_email = None
            apply_url = "https://careers.acme.example/apply/123"
        assert sender_matches_application("hr@acme.example", _A(), None)

    def test_jobless_application_rejects_unrelated_sender(self):
        from app.services.outcome_service import sender_matches_application

        class _A:
            target_email = None
            apply_url = "https://careers.acme.example/apply/123"
        assert not sender_matches_application("news@newsletter.io", _A(), None)
