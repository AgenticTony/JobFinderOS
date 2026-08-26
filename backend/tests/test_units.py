import os

"""
Unit tests for the pure gate/parse/dedupe logic and the fixed state
machines — the cheap-to-test, expensive-to-get-wrong core.

Run: .venv/bin/python -m pytest tests/test_units.py -q
(uses a throwaway SQLite DB; no network, no keys)
"""

import uuid
from datetime import timedelta

import pytest  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
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

# Module-scoped: schema created once via the production boot path
# (TestClient lifespan -> init_db -> alembic). Tests clean DATA not schema.
@pytest.fixture(scope="module")
def _client():
    from fastapi.testclient import TestClient

    from app.main import app as _app

    db_file = "test_suite.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    with TestClient(_app) as c:
        yield c
    engine.dispose()


@pytest.fixture()
def db(_client):
    session = SessionLocal()
    # Clean per-user data between tests (schema stays — Alembic owns it)
    from app.models import (
        Application,
        ApplicationDraft,
        JobPosting,
        MatchResult,
        Profile,
    )
    for model in (Application, ApplicationDraft, MatchResult, Profile, JobPosting):
        session.query(model).delete()
    session.commit()
    yield session
    session.rollback()
    session.close()


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


def _rescore_module():
    """Load scripts/rescore_backlog.py (the PRODUCTION module), by path.

    scripts/ isn't a package and pytest doesn't put backend/ on sys.path,
    hence importlib. The point: tests must run the script's own code —
    apply_rescore and derive_dismissal. A reimplementation in this file
    only guards itself (regressing the script to the one-directional
    176-row bug left 26 tests passing when the test ran its own copy).
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "rescore_backlog.py"
    spec = importlib.util.spec_from_file_location("rescore_backlog_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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

    def _run_with_scores(self, db, monkeypatch, scores, recommendations=None, fail_on_call=None):
        """fail_on_call: index of a call that should raise (simulating 429/timeout).
        recommendations: optional list of rec values per sample, for payload tests."""
        from app.services import matcher_service
        from app.services.ai_service import AIService

        profile = _profile(db)
        job = _job_row(db, status="new")
        job.description = "A real description long enough to be assessed."
        db.commit()

        calls = {"n": 0}

        def fake_match(**kwargs):
            i = calls["n"]
            calls["n"] += 1
            if fail_on_call is not None and i == fail_on_call:
                raise ConnectionError("simulated 429")
            i = min(i, len(scores) - 1)
            rec = recommendations[i] if recommendations else "maybe"
            return {
                "score": scores[i], "tier": AIService._tier_for_score(scores[i]),
                "reasoning": f"reasoning for score {scores[i]}",
                "matched_skills": [], "missing_skills": [],
                "transferable_skills": [], "recommendation": rec,
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
        """22 in dead-band → re-score gets 30, preliminary mean 26 ≥ keep-min
        → keeper adds 1 more (30). 3 calls, samples [22,30,30], mean 27."""
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(db, monkeypatch, [22, 30])
        assert n == 3, "1 triage + 1 dead-band + 1 keeper = 3 calls (3 total samples)"
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score >= 25, "averaged above keep-min must stay in the queue"
        assert row.dismissed_reason is None

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

    def test_clear_pass_gets_three_samples_and_stores_the_mean(self, db, monkeypatch):
        """Keepers (>=25) get 3 samples; the MEAN is the stored score.
        3 calls total: 1 triage + 2 keeper re-samples."""
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(db, monkeypatch, [70, 10])
        assert n == 3, "keeper = 1 triage + 2 re-samples = 3 calls"
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 30, "mean of [70, 10, 10] = 30 — stored single value"
        assert row.dismissed_reason is None

    def test_subthreshold_after_averaging_is_dismissed_not_queued(self, db, monkeypatch):
        """WO2 defect 3: first sample clears keep-min, but the 3-sample mean
        falls below it — the result must be DISMISSED, not queued. Samples
        [26, 20, 18] average to 21, which is below MATCH_KEEP_MIN_SCORE=25."""
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(db, monkeypatch, [26, 20, 18])
        assert n == 3, "triage >=25 triggers the keeper path (1 + 2 calls)"
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 21, "mean of [26, 20, 18] = 21"
        assert row.dismissed_reason == "below_threshold", (
            f"score {row.score} < keep-min {25} but dismissed_reason is "
            f"{row.dismissed_reason} — sub-threshold scores must never enter "
            "the queue as live matches"
        )
        assert row.decision == "rejected"

    def test_deadband_keeper_produces_clean_unweighted_mean(self, db, monkeypatch):
        """WO2 defect 2: a dead-band score that averages up enters the keeper
        path; the final stored value must be the mean of ALL raw samples
        equally, not mean(mean(s1,s2), s3, s4). Samples [20, 30, 40]:
        dead-band 20+30 preliminary=25 clears keep-min, keeper adds 40.
        Correct: mean(20, 30, 40) = 30.
        Old buggy path: mean(mean(20,30), 40, 40) = mean(25, 40, 40) = 35."""
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(db, monkeypatch, [20, 30, 40])
        assert n == 3, "1 triage + 1 dead-band + 1 keeper = 3 calls"
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 30, (
            f"mean(20,30,40) = 30. Got {row.score} — if this is 35, "
            "the dead-band mean was weighted into the keeper average (defect 2)"
        )
        assert row.dismissed_reason is None

    def test_every_match_row_is_stamped_with_the_prompt_version(self, db, monkeypatch):
        from app.models import MatchResult
        from app.services.ai_service import AIService

        n, job, profile = self._run_with_scores(db, monkeypatch, [70])
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.prompt_version == AIService.matching_prompt_version()

    def test_f1_payload_comes_from_sample_closest_to_mean(self, db, monkeypatch):
        """F1 regression: score is a 3-sample mean but the prose/recommendation/
        confidence must come from the sample CLOSEST to that mean — not always
        from sample 1. Samples [26, 45, 49] mean 40; the sample scoring 45 is
        closest, so recommendation and reasoning must be from that sample.
        Old buggy behavior: payload always from sample 1 (score 26, rec=skip,
        reasoning='barely match') paired with a displayed score of 40."""
        from app.models import MatchResult

        n, job, profile = self._run_with_scores(
            db, monkeypatch,
            scores=[26, 45, 49],
            recommendations=["skip", "apply", "apply"],
        )
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 40, "mean(26,45,49) = 40"
        assert row.recommendation == "apply", (
            f"recommendation is '{row.recommendation}' — should be 'apply' "
            "(from the 45-sample closest to the mean). If 'skip', the payload "
            "came from sample 1 (F1 regression: prose contradicts the score)"
        )
        assert "45" in (row.reasoning or ""), (
            f"reasoning is '{row.reasoning}' — must reference the score-45 "
            "sample (closest to mean 40). If it references 26, F1 regressed."
        )

    def test_f3_deadband_failure_leaves_job_new_for_retry(self, db, monkeypatch):
        """F3 regression: a transient API failure during dead-band sampling
        (429, timeout) must leave the job as 'new' for retry on the next
        run — NOT permanently dismiss it on one ±11 sample. The old buggy
        behavior continued to the keep-min check with a single uncertain
        sample and dismissed the job forever."""
        from app.models import MatchResult

        # Score 20 (dead-band range [13,25)); the re-score call fails
        n, job, profile = self._run_with_scores(
            db, monkeypatch, scores=[20], fail_on_call=1,
        )
        db.refresh(job)
        assert job.status == "new", (
            f"job.status is '{job.status}' — must be 'new' for retry. "
            "If 'dismissed' or 'matched', F3 regressed: a single uncertain "
            "sample was used for a permanent decision"
        )
        rows = db.query(MatchResult).filter(MatchResult.job_id == job.id).all()
        assert len(rows) == 0, (
            f"{len(rows)} match rows written — must be 0. A dead-band "
            "sampling failure writes nothing and retries next run."
        )

    def test_keep_min_invariant_bidirectional_dismissal(self, db):
        """INVARIANT: the re-score script's dismissal derivation keeps all
        four queue invariants intact. Seeds all three violation types (the
        176-row bug and both of its mirrors), then runs the PRODUCTION
        derivation imported from scripts/rescore_backlog.py — never a copy:
        regressing the script to the one-directional bug left 26 tests
        passing when this test ran its own inline loop."""
        from app.core.config import settings
        from app.models import MatchResult

        derive_dismissal = _rescore_module().derive_dismissal
        keep = settings.MATCH_KEEP_MIN_SCORE

        profile = _profile(db)
        job_low = _job_row(db, status="matched")
        job_high = _job_row(db, status="matched")
        job_rose = _job_row(db, status="matched")

        # VIOLATION 1: sub-threshold row WITHOUT dismissal (the 176-row bug)
        db.add(MatchResult(
            user_id=profile.user_id, job_id=job_low.id, score=18,
            tier="poor_match", recommendation="maybe", decision=None,
            dismissed_reason=None, prompt_version="m2-62c2452b",
        ))
        # VIOLATION 2: strong row with a stale auto-pass dismissal (score
        # rose above keep-min after a re-score)
        db.add(MatchResult(
            user_id=profile.user_id, job_id=job_high.id, score=72,
            tier="good_match", recommendation="maybe", decision="rejected",
            dismissed_reason="below_threshold", prompt_version="m2-62c2452b",
        ))
        # VIOLATION 3: strong row still carrying the FULL auto-pass stamp.
        # The fall-below branch stamps recommendation='skip'; a row that
        # later rises keeps that stamp unless the derivation clears it —
        # a strong row recommending 'skip' hides a keeper from review.
        db.add(MatchResult(
            user_id=profile.user_id, job_id=job_rose.id, score=72,
            tier="good_match", recommendation="skip", decision="rejected",
            dismissed_reason="below_threshold", prompt_version="m2-62c2452b",
        ))
        db.commit()

        # Verify the violations EXIST before the derivation — an assertion
        # over an empty set is decoration, not a test
        v1_before = db.query(MatchResult).filter(
            MatchResult.score < keep, MatchResult.dismissed_reason.is_(None)
        ).count()
        v2_before = db.query(MatchResult).filter(
            MatchResult.score < keep, MatchResult.decision.is_(None)
        ).count()
        v3_before = db.query(MatchResult).filter(
            MatchResult.score >= keep,
            MatchResult.dismissed_reason == "below_threshold",
        ).count()
        v4_before = db.query(MatchResult).filter(
            MatchResult.score >= 50, MatchResult.recommendation == "skip"
        ).count()
        assert v1_before == 1, f"seed failed: {v1_before} sub-threshold rows without dismissal"
        assert v2_before == 1, f"seed failed: {v2_before} sub-threshold rows with decision NULL"
        assert v3_before == 2, f"seed failed: {v3_before} strong rows with stale dismissal"
        assert v4_before == 1, f"seed failed: {v4_before} strong rows with skip stamp"

        # Run the PRODUCTION derivation over every row, exactly as main() does
        for m in db.query(MatchResult).all():
            derive_dismissal(m, keep)
        db.commit()

        # Assert ALL FOUR invariants at zero
        v1 = db.query(MatchResult).filter(
            MatchResult.score < keep, MatchResult.dismissed_reason.is_(None)
        ).count()
        v2 = db.query(MatchResult).filter(
            MatchResult.score < keep, MatchResult.decision.is_(None)
        ).count()
        v3 = db.query(MatchResult).filter(
            MatchResult.score >= keep,
            MatchResult.dismissed_reason == "below_threshold",
        ).count()
        v4 = db.query(MatchResult).filter(
            MatchResult.score >= 50, MatchResult.recommendation == "skip"
        ).count()

        assert v1 == 0, f"{v1} sub-threshold rows without dismissal — invariant violated"
        assert v2 == 0, f"{v2} sub-threshold rows with decision=NULL — invariant violated"
        assert v3 == 0, f"{v3} strong rows wrongly dismissed — invariant violated"
        assert v4 == 0, f"{v4} strong rows with skip recommendation — invariant violated"


class TestRescorePayload:
    """F1 at full scale: the re-score script must refresh the PROSE with
    the score. The previous run kept only result['score'] and discarded
    the payloads — 241 rows ended up with a current-prompt score next to
    legacy-prompt prose (0 cover_note changes vs the pre-run snapshot
    proved no fresh payload was ever written). All tests run the
    PRODUCTION apply_rescore / derive_dismissal imported from the script."""

    def test_apply_rescore_refreshes_payload_not_just_score(self, db):
        from app.models import MatchResult
        from app.schemas.common import parse_json_list

        apply_rescore = _rescore_module().apply_rescore

        profile = _profile(db)
        job = _job_row(db, status="matched")
        m = MatchResult(
            user_id=profile.user_id, job_id=job.id, score=45, tier="stretch",
            reasoning="LEGACY PROSE from the old prompt",
            recommendation="maybe", cover_note="LEGACY COVER NOTE",
            confidence="low", prompt_version="legacy-unversioned",
        )
        db.add(m)
        db.commit()

        # mean(72, 45, 70) = 62.33 -> 62; closest sample is the 70
        samples = [
            {"score": 72, "reasoning": "prose from 72", "recommendation": "apply",
             "cover_note": "note from 72", "confidence": "high",
             "matched_skills": ["Python"], "missing_skills": ["Kafka"],
             "transferable_skills": ["Go"]},
            {"score": 45, "reasoning": "prose from 45", "recommendation": "skip",
             "cover_note": "note from 45", "confidence": "low",
             "matched_skills": [], "missing_skills": [],
             "transferable_skills": []},
            {"score": 70, "reasoning": "prose from 70", "recommendation": "apply",
             "cover_note": "note from 70", "confidence": "high",
             "matched_skills": ["Python", "SQL"], "missing_skills": [],
             "transferable_skills": []},
        ]
        final = apply_rescore(m, samples, model="glm-5.1")
        db.commit()

        assert final == 62
        assert m.score == 62
        # Payload from the sample CLOSEST to the mean — never the legacy row
        assert m.reasoning == "prose from 70", "stale prose survived a re-score"
        assert m.cover_note == "note from 70", "stale cover_note survived a re-score"
        assert m.recommendation == "apply"
        assert m.confidence == "high"
        assert parse_json_list(m.matched_skills) == ["Python", "SQL"]
        assert parse_json_list(m.missing_skills) == []
        assert parse_json_list(m.transferable_skills) == []
        assert m.prompt_version != "legacy-unversioned"
        assert m.model_used == "glm-5.1"
        # Keeper above keep-min: no dismissal
        assert m.decision is None and m.dismissed_reason is None

    def test_apply_rescore_subthreshold_stamps_autopass(self, db):
        from app.core.config import settings
        from app.models import MatchResult

        apply_rescore = _rescore_module().apply_rescore
        stamp = _rescore_module().AUTOPASS_REASONING

        profile = _profile(db)
        job = _job_row(db, status="matched")
        m = MatchResult(
            user_id=profile.user_id, job_id=job.id, score=41, tier="stretch",
            reasoning="old keeper prose", recommendation="apply",
            decision=None, dismissed_reason=None,
            prompt_version="legacy-unversioned",
        )
        db.add(m)
        db.commit()

        # mean(20, 18) = 19 — below keep-min, single triage semantics
        samples = [
            {"score": 20, "reasoning": "weak", "recommendation": "skip",
             "confidence": "high", "matched_skills": [], "missing_skills": [],
             "transferable_skills": []},
            {"score": 18, "reasoning": "weak too", "recommendation": "skip",
             "confidence": "high", "matched_skills": [], "missing_skills": [],
             "transferable_skills": []},
        ]
        final = apply_rescore(m, samples, model="glm-5.1")
        db.commit()

        assert final == 19
        assert final < settings.MATCH_KEEP_MIN_SCORE
        assert m.decision == "rejected"
        assert m.dismissed_reason == "below_threshold"
        assert m.recommendation == "skip"
        assert m.reasoning == stamp, "sub-threshold row must carry the auto-pass stamp"

    def test_rise_branch_clears_the_full_autopass_stamp(self, db):
        """A row that dips below keep-min and recovers must shed ALL FOUR
        stamp fields. The rise-branch used to leave reasoning='Auto-passed…'
        on a strong score — MatchCard renders reasoning as the primary
        explanation, so a keeper told the user it was auto-passed for
        being too weak."""
        from app.core.config import settings
        from app.models import MatchResult

        derive_dismissal = _rescore_module().derive_dismissal
        keep = settings.MATCH_KEEP_MIN_SCORE
        stamp = _rescore_module().AUTOPASS_REASONING

        profile = _profile(db)
        job = _job_row(db, status="matched")
        m = MatchResult(
            user_id=profile.user_id, job_id=job.id, score=72,
            tier="good_match",
            reasoning="Your Python and FastAPI experience matches what they ask for.",
            recommendation="apply", decision=None, dismissed_reason=None,
        )
        db.add(m)
        db.commit()

        # Fall below keep-min: the stamp goes on
        m.score = 18
        derive_dismissal(m, keep)
        db.commit()
        assert m.decision == "rejected"
        assert m.reasoning == stamp, "fall-branch must stamp reasoning"

        # Recover above keep-min: the ENTIRE stamp comes off
        m.score = 72
        derive_dismissal(m, keep)
        db.commit()
        assert m.decision is None
        assert m.dismissed_reason is None
        assert m.recommendation is None
        assert m.reasoning is None, (
            "stale auto-pass prose survived on a strong row — the rise-branch "
            "must shed all four stamp fields"
        )


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


class TestSharedSamplingPolicy:
    """The sampling policy must live in ONE place. It has now diverged
    three times between the matcher and the re-score script — one-directional
    dismissal (176 rows), score-without-payload (241 rows), and a triage
    break on KEEP_MIN instead of the dead-band floor (62 rows permanently
    dismissed on a single +/-11 sample)."""

    def test_script_uses_the_shared_policy_not_its_own_thresholds(self):
        """The script must call needs_another_sample, not re-implement it.

        A copy in the script only guards itself: the last three regressions
        all shipped because the script had its own version of a rule the
        matcher had already fixed.
        """
        import inspect

        from app.services import matcher_service

        src = _rescore_module_source()
        assert "needs_another_sample" in src, (
            "rescore_backlog.py must import and call needs_another_sample. "
            "If it re-implements the sampling thresholds, a matcher fix will "
            "silently not reach it — that has happened three times."
        )
        assert "MATCH_KEEP_MIN_SCORE:" not in src.replace(" ", ""), "sanity"
        # The policy itself is defined exactly once, in the matcher
        assert "def needs_another_sample" in inspect.getsource(matcher_service)
        assert "def needs_another_sample" not in src, (
            "needs_another_sample is defined twice — the shadow copy is back"
        )

    def test_deadband_score_earns_a_second_sample(self):
        """A 22 must never be decided on one sample: dismissal is permanent
        and single-sample noise is +/-11. This is the exact rule the script
        skipped when it broke on KEEP_MIN, dismissing 62 rows."""
        from app.services.matcher_service import needs_another_sample

        for score in (13, 18, 22, 24):
            assert needs_another_sample([{"score": score}]), (
                f"score {score} is inside the dead-band [13,25) and must earn "
                "a second sample before a permanent dismissal"
            )

    def test_confidently_bad_stops_at_one_sample(self):
        from app.services.matcher_service import needs_another_sample

        for score in (0, 5, 12):
            assert not needs_another_sample([{"score": score}]), (
                f"score {score} is below the dead-band — a second opinion "
                "cannot rescue it and must not be paid for"
            )

    def test_keeper_path_commits_to_three_samples(self):
        """Once triage clears keep-min the row is heading for the queue;
        stopping at 2 would decide a dismissal on a +/-8 mean."""
        from app.services.matcher_service import needs_another_sample

        assert needs_another_sample([{"score": 26}, {"score": 20}])
        assert not needs_another_sample(
            [{"score": 26}, {"score": 20}, {"score": 18}]
        )


def _rescore_module_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "scripts" / "rescore_backlog.py"
    ).read_text()
