"""
Unit tests for the pure gate/parse/dedupe logic and the fixed state
machines — the cheap-to-test, expensive-to-get-wrong core.

Run: .venv/bin/python -m pytest tests/test_units.py -q
(uses a throwaway SQLite DB; no network, no keys)
"""

import os
import uuid
from datetime import timedelta


import pytest  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.dedupe import dedupe_key_for  # noqa: E402
from app.models import JobPosting, Profile  # noqa: E402
from app.services.language_filter import (  # noqa: E402
    detect_language,
    passes_language_filter,
)
from app.services.pipeline import passes_location_filter  # noqa: E402
from app.services.scrapers.base import NormalizedJob  # noqa: E402

# ---------- pure gates ----------

def _job(location=None, remote=False, title="Developer", company="Acme"):
    return NormalizedJob(
        source="t", source_id="1", title=title, company=company,
        url="https://x", remote=remote, location=location,
    )


CTX = {"municipality": "Malmö", "region": "Skåne län",
       "include_remote": False, "remote_only": False}


class TestLocationGate:
    def test_local_job_passes(self):
        assert passes_location_filter(_job("Malmö, Skåne län"), CTX)

    def test_outside_area_onsite_dropped(self):
        assert not passes_location_filter(_job("Stockholm"), CTX)

    def test_outside_remote_needs_opt_in(self):
        remote = dict(CTX, include_remote=True)
        assert passes_location_filter(_job("Berlin", remote=True), remote)
        assert not passes_location_filter(_job("Berlin", remote=True), CTX)

    def test_dateless_remote_strict_local_dropped(self):
        assert not passes_location_filter(_job(None, remote=True), CTX)


class TestDedupeKeys:
    def test_formatting_collapses(self):
        assert dedupe_key_for("Junior Full-stack Developer", "Acme AB") == \
               dedupe_key_for("junior fullstack developer!", "acme ab")

    def test_different_companies_differ(self):
        assert dedupe_key_for("Developer", "Acme") != dedupe_key_for("Developer", "Beta")


class TestLanguageFilter:
    def test_german_detected_and_gated(self):
        title = "Senior-Entwickler:in (m/w/d)"
        assert detect_language(title, "Sie unterstützen das Team und übernehmen Aufgaben") == "German"
        assert not passes_language_filter(title, "mit und für", ["English", "Swedish"])

    def test_german_passes_for_german_speaker(self):
        assert passes_language_filter("Entwickler (m/w/d)", "mit und für", ["German"])

    def test_english_always_passes(self):
        assert passes_language_filter("Developer", "Join our team and build", ["Swedish"])

    def test_no_languages_configured_passes_all(self):
        assert passes_language_filter("Entwickler", "mit und für", [])


# ---------- state machines (fixed bugs) ----------

@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


def _profile(db, user_id=None):
    """A profile always belongs to a user now — tests must say which."""
    p = Profile(is_active=1, user_id=user_id or uuid.uuid4(),
                full_name="Test", cv_file_name="cv.pdf",
                cv_text="developer python")
    db.add(p)
    db.commit()
    return p


def _job_row(db, status="approved"):
    j = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                   title="Dev", company="Acme", url=f"https://x/{uuid.uuid4().hex[:6]}",
                   status=status)
    db.add(j)
    db.commit()
    return j


class TestSubmitStateMachine:
    """B6: a FAILED email send must not mark the job applied / lock the draft."""

    def test_failed_send_keeps_draft_ready(self, db, monkeypatch):
        from app.services import draft_service

        def boom(*a, **k):
            raise RuntimeError("resend down")

        monkeypatch.setattr(draft_service, "_send_with_pdfs", boom)
        profile = _profile(db)
        job = _job_row(db)
        job.application_email = "jobs@acme.example"
        db.commit()
        from app.models import ApplicationDraft
        draft = ApplicationDraft(job_id=job.id, user_id=profile.user_id,
                                 cover_letter="x", tailored_cv="y",
                                 changes_summary="[]", status="ready")
        db.add(draft)
        db.commit()

        with pytest.raises(RuntimeError):
            draft_service.submit_draft(
                db, draft, "email", profile=profile, user_id=profile.user_id
            )

        db.rollback()
        db.refresh(draft)
        db.refresh(job)
        assert draft.status == "ready", "failed send must leave the draft editable"
        assert job.status == "approved", "failed send must not mark the job applied"

    def test_manual_pending_marks_submitted(self, db, monkeypatch):

        profile = _profile(db)
        job = _job_row(db)
        job.application_url = "https://apply.example"
        db.commit()
        from app.models import ApplicationDraft
        draft = ApplicationDraft(job_id=job.id, user_id=profile.user_id,
                                 cover_letter="x", tailored_cv="y",
                                 changes_summary="[]", status="ready")
        db.add(draft)
        db.commit()

        from app.services.draft_service import submit_draft

        app_row = submit_draft(
            db, draft, "browser", profile=profile, user_id=profile.user_id
        )
        assert app_row.status == "manual_pending"
        db.refresh(draft)
        assert draft.status == "submitted"
        # job.status is NEVER user-mutated now — applied-ness derives from
        # the applications table per user
        db.refresh(job)
        assert job.status == "approved"


class TestParseFailureRetry:
    """B8: unparseable model output must RAISE (retry), never score 0."""

    def test_match_job_raises_on_garbage(self, monkeypatch):
        from app.services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.thinking = "disabled"
        svc.max_tokens = 2000
        monkeypatch.setattr(svc, "_complete", lambda *a, **k: "<<not json>>")
        with pytest.raises(ValueError, match="Unparseable"):
            svc.match_job(profile_context="x", cv_text="y", job_description="z")


class TestDuplicateMatchContainment:
    """B9: a pre-matched job requeued must not abort the whole batch."""

    def test_lock_second_run_skips(self, db):
        """Per-user lock: same user blocked, different user proceeds."""
        from app.services import matcher_service

        uid = uuid.uuid4()
        lock = matcher_service._get_user_lock(uid)
        acquired = lock.acquire(blocking=False)
        assert acquired
        try:
            result = matcher_service.run_matching(db, user_id=uid)
            assert result["status"] == "skipped"
            assert "already in progress" in result["error"]
        finally:
            lock.release()


class TestStaleSweep:
    """B12: date-less postings expire by scraped_at."""

    def test_datless_old_swept(self, db):
        from app.core.timeutil import utc_now
        from app.services.pipeline import _maintenance_sweeps

        old = JobPosting(source="t", source_id="o1", title="Old", url="https://o",
                         status="new", published_at=None,
                         scraped_at=utc_now() - timedelta(days=45))
        db.add(old)
        db.commit()
        _maintenance_sweeps(db)
        db.refresh(old)
        assert old.status == "dismissed"


class TestDeadBand:
    """Scores move +/-7 between runs at temp 0 and dismissal is permanent,
    so the keep/dismiss call near the line is re-scored and averaged."""

    def _run_with_scores(self, db, monkeypatch, scores):
        from app.services import matcher_service
        from app.services.ai_service import AIService

        profile = _profile(db)
        job = _job_row(db, status="new")
        job.description = "A real description long enough to be assessed."
        db.commit()

        calls = {"n": 0}

        def fake_match(**kwargs):
            i = min(calls["n"], len(scores) - 1)
            calls["n"] += 1
            return {
                "score": scores[i], "tier": AIService._tier_for_score(scores[i]),
                "reasoning": "r", "matched_skills": [], "missing_skills": [],
                "transferable_skills": [], "recommendation": "maybe",
                "cover_note": "c", "confidence": "medium",
            }

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.match_job = fake_match
        monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)

        matcher_service.run_matching(db, user_id=profile.user_id)
        return calls["n"], job, profile

    def test_borderline_is_rescored_and_averaged_up(self, db, monkeypatch):
        """22 then 30 averages to 26 — above the keep line, so it survives."""
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(db, monkeypatch, [22, 30])
        assert n == 2, "a dead-band score must trigger exactly one re-score"
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 26
        assert row.dismissed_reason is None, "averaged above keep-min must stay in the queue"

    def test_borderline_averaging_down_is_dismissed(self, db, monkeypatch):
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(db, monkeypatch, [24, 18])
        assert n == 2
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 21
        assert row.dismissed_reason == "below_threshold"

    def test_confidently_bad_never_pays_for_a_second_call(self, db, monkeypatch):
        n, job, profile = self._run_with_scores(db, monkeypatch, [8, 90])
        assert n == 1, "below the dead-band floor must not re-score"

    def test_clear_pass_never_pays_for_a_second_call(self, db, monkeypatch):
        n, job, profile = self._run_with_scores(db, monkeypatch, [70, 10])
        assert n == 1, "comfortably above keep-min must not re-score"

    def test_every_match_row_is_stamped_with_the_prompt_version(self, db, monkeypatch):
        from app.models import MatchResult
        from app.services.ai_service import AIService

        n, job, profile = self._run_with_scores(db, monkeypatch, [70])
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.prompt_version == AIService.matching_prompt_version()


class TestDismissalIsPerUser:
    """One user's exclude keyword must not hide a shared job from others."""

    def test_exclude_keyword_does_not_touch_the_shared_job(self, db, monkeypatch):
        import uuid as _uuid

        from sqlalchemy import and_

        from app.models import JobPosting as JP
        from app.models import MatchResult
        from app.services import matcher_service
        from app.services.ai_service import AIService

        a = _profile(db, user_id=_uuid.uuid4())
        a.exclude_keywords = '["senior"]'
        db.commit()
        job = _job_row(db, status="new")
        job.title = "Senior Developer"
        job.description = "Long enough description to be assessed properly."
        db.commit()

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.match_job = lambda **k: pytest.fail("excluded job must not reach the AI")
        monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)

        matcher_service.run_matching(db, user_id=a.user_id)

        db.refresh(job)
        assert job.status != "dismissed", (
            "CROSS-TENANT: one user's exclude keyword dismissed the SHARED job row"
        )
        row = db.query(MatchResult).filter(MatchResult.user_id == a.user_id).one()
        assert row.dismissed_reason == "excluded_keyword"

        # User B, with no exclude list, still sees the job as a candidate
        b = _profile(db, user_id=_uuid.uuid4())
        candidates = (
            db.query(JP)
            .outerjoin(MatchResult, and_(MatchResult.job_id == JP.id,
                                         MatchResult.user_id == b.user_id))
            .filter(MatchResult.id.is_(None), JP.status != "dismissed")
            .all()
        )
        assert job.id in [j.id for j in candidates], (
            "user B lost a job because user A excluded it"
        )
