import os

"""
Unit tests for the pure gate/parse/dedupe logic and the fixed state
machines — the cheap-to-test, expensive-to-get-wrong core.

Run: .venv/bin/python -m pytest tests/test_units.py -q
(uses a throwaway SQLite DB; no network, no keys)
"""

import json
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
        AIUsage,
        Application,
        ApplicationDraft,
        JobPosting,
        MatchResult,
        Profile,
        SystemLock,
    )
    for model in (Application, ApplicationDraft, MatchResult, Profile,
                  JobPosting, AIUsage, SystemLock):
        session.query(model).delete()
    session.commit()
    yield session
    session.rollback()
    session.close()


def _profile(db, user_id=None):
    """A profile always belongs to a user now — tests must say which.

    Postgres enforces the profiles.user_id FK; SQLite silently doesn't,
    which is how this helper shipped without a users row at all. The user
    is created here so both backends enforce the same integrity."""
    from app.models import User

    uid = user_id or uuid.uuid4()
    if db.get(User, uid) is None:
        db.add(User(id=uid, email=f"u-{uid.hex[:10]}@test.example",
                    hashed_password="test-only-not-loginable"))
        db.flush()
    p = Profile(is_active=1, user_id=uid,
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


def _bare_service(monkeypatch, raw, finish_reason=None):
    """An AIService whose model layer (_complete) returns `raw`.

    The response-handling tests below all use this: they exercise the
    REAL parse/validate code with a scripted model output."""
    from app.services.ai_service import AIService

    svc = AIService.__new__(AIService)
    svc.model = "glm-test"
    svc.thinking = "disabled"
    svc.max_tokens = 2000

    def fake_complete(*a, **k):
        svc._last_finish_reason = finish_reason
        return raw

    monkeypatch.setattr(svc, "_complete", fake_complete)
    return svc


class TestTailorRejectsEmptyPackage:
    """AI-9 (live-confirmed): a malformed tailoring response parsed to {}
    and the draft went 'ready' with a 0-char cover letter + 0-char CV —
    it passed the fabrication guard vacuously and dead-ended at submit.
    The raise IS the fix: same rule as match_job/judge_fabrication —
    malformed output is a format failure, never a package."""

    def test_empty_object_raises(self, monkeypatch):
        svc = _bare_service(monkeypatch, "{}")
        with pytest.raises(ValueError, match="(?i)tailor|cover_letter|empty"):
            svc.tailor_application(profile_context="x", cv_text="y", job_description="z")

    def test_blank_documents_raise(self, monkeypatch):
        raw = json.dumps({"cover_letter": "", "tailored_cv": "   "})
        svc = _bare_service(monkeypatch, raw)
        with pytest.raises(ValueError):
            svc.tailor_application(profile_context="x", cv_text="y", job_description="z")

    def test_missing_tailored_cv_raises(self, monkeypatch):
        raw = json.dumps({"cover_letter": "Dear Acme"})
        svc = _bare_service(monkeypatch, raw)
        with pytest.raises(ValueError):
            svc.tailor_application(profile_context="x", cv_text="y", job_description="z")

    def test_truncated_response_names_finish_reason(self, monkeypatch):
        # cut mid-JSON: _parse_json falls back to {} — and when the API
        # says the output was truncated, the error must say so
        svc = _bare_service(monkeypatch, '{"cover_letter": "Dear Acme',
                            finish_reason="length")
        with pytest.raises(ValueError, match="finish_reason=length"):
            svc.tailor_application(profile_context="x", cv_text="y", job_description="z")

    def test_valid_package_still_passes(self, monkeypatch):
        pkg = {
            "cover_letter": "Dear Acme team",
            "tailored_cv": "PROFESSIONAL SUMMARY\nBackend developer",
            "changes_summary": ["Front-loaded Python"],
        }
        svc = _bare_service(monkeypatch, json.dumps(pkg))
        out = svc.tailor_application(profile_context="x", cv_text="y", job_description="z")
        assert out["cover_letter"] == "Dear Acme team"
        assert out["tailored_cv"].startswith("PROFESSIONAL SUMMARY")

    def test_draft_fails_closed_instead_of_ready_empty(self, db, monkeypatch):
        """End-to-end: the raise lands in create_draft_for_job's except —
        the draft row is 'failed' with the AI error on it (regenerable),
        never 'ready' with empty documents that submit would reject."""
        from app.services import draft_service

        profile = _profile(db)
        job = _job_row(db, status="approved")
        job.description = "A Python role worth tailoring for."
        db.commit()

        svc = _bare_service(monkeypatch, "{}")
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: svc)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)

        draft = draft_service.create_draft_for_job(
            db, job, profile=profile, user_id=profile.user_id
        )
        assert draft.status == "failed", (
            f"draft.status is '{draft.status}' — a malformed tailoring "
            "response must fail the draft, not produce an empty package"
        )
        assert draft.error and "Tailoring failed" in draft.error
        assert not draft.cover_letter and not draft.tailored_cv


class TestScorelessMatchRejected:
    """AI-10: parsed.get('score', 0) + _clamp_score's TypeError fallback
    turned a scoreless response into a CONFIDENT 0 — one below-threshold
    sample dismissed the job forever. The comment above the parse check
    already forbids this for unparseable output; a missing/non-numeric
    score is the same format failure and must raise the same way."""

    def test_missing_score_raises(self, monkeypatch):
        raw = json.dumps({"reasoning": "great fit", "recommendation": "apply"})
        svc = _bare_service(monkeypatch, raw)
        with pytest.raises(ValueError, match="score"):
            svc.match_job(profile_context="x", cv_text="y", job_description="z")

    def test_non_numeric_score_raises(self, monkeypatch):
        for raw in ('{"score": "high", "reasoning": "r"}',
                    '{"score": null, "reasoning": "r"}'):
            svc = _bare_service(monkeypatch, raw)
            with pytest.raises(ValueError):
                svc.match_job(profile_context="x", cv_text="y", job_description="z")

    def test_nan_score_raises(self, monkeypatch):
        # json.loads accepts the bare NaN literal; _clamp_score's
        # ValueError fallback turned it into a confident 0 — the exact
        # silent-dismissal path this class exists to close
        svc = _bare_service(monkeypatch, '{"score": NaN}')
        with pytest.raises(ValueError):
            svc.match_job(profile_context="x", cv_text="y", job_description="z")

    def test_numeric_score_still_clamps(self, monkeypatch):
        svc = _bare_service(monkeypatch, '{"score": 132, "reasoning": "r"}')
        out = svc.match_job(profile_context="x", cv_text="y", job_description="z")
        assert out["score"] == 100
        assert out["tier"] == "excellent_match"

    def test_scoreless_match_never_dismisses_the_job(self, db, monkeypatch):
        """Through run_matching (the caller's error path): a scoreless
        first sample raises, the job stays 'new' with NO match row, and a
        later run with a well-formed response scores it normally."""
        from app.models import MatchResult
        from app.services import matcher_service
        from app.services.ai_service import AIService

        profile = _profile(db)
        job = _job_row(db, status="new")
        job.description = "A real description long enough to be assessed."
        db.commit()

        mode = {"broken": True}

        def fake_match(**kwargs):
            if mode["broken"]:
                # exactly what match_job now raises for a scoreless body
                raise ValueError(
                    "Malformed match response: 'score' is missing or non-numeric"
                )
            return {
                "score": 42, "tier": "stretch", "reasoning": "solid transferable core",
                "matched_skills": [], "missing_skills": [],
                "transferable_skills": [], "recommendation": "maybe",
                "confidence": "medium",
            }

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.match_job = fake_match
        monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)

        matcher_service.run_matching(db, profile=profile, user_id=profile.user_id)
        db.refresh(job)
        assert job.status == "new", (
            f"job.status is '{job.status}' — a scoreless response must leave "
            "the job re-scoreable, not dismiss or match it"
        )
        assert db.query(MatchResult).filter(MatchResult.job_id == job.id).count() == 0, (
            "a scoreless response wrote a match row — the confident-0 "
            "permanent dismissal (AI-10) is back"
        )

        mode["broken"] = False
        matcher_service.run_matching(db, profile=profile, user_id=profile.user_id)
        row = db.query(MatchResult).filter(MatchResult.job_id == job.id).one()
        assert row.score == 42
        assert row.dismissed_reason is None


class TestEmptyExtractionKeepsProfile:
    """AI-11 (live-confirmed): a malformed extraction response returns {}
    and _apply_extraction(profile, {}) NULLed full_name/email/phone/
    title/years on a previously-good profile when the user re-uploaded
    their CV. Raising inside the AI service does NOT fix it — cv_service
    swallows the exception and still applies — so the WRITE is guarded:
    an empty extraction is a no-op for the derived fields."""

    def _patch_upload(self, monkeypatch, service):
        from app.services import cv_service

        monkeypatch.setattr(
            cv_service.FileService, "validate_pdf", staticmethod(lambda b: True)
        )
        monkeypatch.setattr(
            cv_service.FileService,
            "extract_text_from_pdf",
            staticmethod(lambda b: "fresh cv text python"),
        )
        monkeypatch.setattr(
            cv_service, "_store_cv_file", lambda content, filename: ("cvs/new.pdf", "new.pdf")
        )
        monkeypatch.setattr(cv_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(cv_service, "get_ai_service", lambda: service)

    def _good_profile(self, db):
        profile = _profile(db)
        profile.full_name = "Jane Doe"
        profile.email = "jane@example.com"
        profile.phone = "+46700000000"
        profile.professional_title = "Backend Developer"
        profile.experience_years = 7
        db.commit()
        return profile

    class _HiccupService:
        """Stands in for get_ai_service(): malformed output, no raise."""

        def extract_profile(self, cv_text):
            return {}

    def test_empty_extraction_on_reupload_keeps_fields(self, db, monkeypatch):
        from app.services import cv_service

        profile = self._good_profile(db)
        self._patch_upload(monkeypatch, self._HiccupService())

        warnings = []
        monkeypatch.setattr(
            cv_service.logger, "warning",
            lambda msg, *a, **k: warnings.append(msg % a if a else msg),
        )

        updated = cv_service.create_or_replace_profile_from_pdf(
            db, b"%PDF-fresh", "new_cv.pdf", user_id=profile.user_id
        )

        assert updated.id == profile.id, "re-upload replaces the user's own row"
        assert updated.full_name == "Jane Doe", "AI-11 wipe: full_name nulled by empty extraction"
        assert updated.email == "jane@example.com", "AI-11 wipe: email nulled"
        assert updated.phone == "+46700000000", "AI-11 wipe: phone nulled"
        assert updated.professional_title == "Backend Developer", "AI-11 wipe: title nulled"
        assert updated.experience_years == 7, "AI-11 wipe: years nulled"
        # the new CV itself DID land — only the derived fields are guarded
        assert updated.cv_text == "fresh cv text python"
        assert updated.cv_file_name == "new_cv.pdf"
        assert any("extraction" in w.lower() for w in warnings), (
            "an empty extraction must be logged — it is a silent model "
            f"failure, not a normal empty CV (warnings: {warnings})"
        )

    def test_raised_extraction_on_reupload_keeps_fields(self, db, monkeypatch):
        """The exception path funnels into the same {} — the live incident
        was a Z.ai hiccup, which can arrive as either shape."""
        from app.services import cv_service

        profile = self._good_profile(db)

        class RaisingService:
            def extract_profile(self, cv_text):
                raise RuntimeError("simulated Z.ai 503")

        self._patch_upload(monkeypatch, RaisingService())
        updated = cv_service.create_or_replace_profile_from_pdf(
            db, b"%PDF-fresh", "new_cv.pdf", user_id=profile.user_id
        )
        assert updated.full_name == "Jane Doe"
        assert updated.email == "jane@example.com"
        assert updated.professional_title == "Backend Developer"
        assert updated.experience_years == 7

    def test_good_extraction_still_replaces_fields(self, db, monkeypatch):
        """The guard must not over-fire: a real extraction still updates
        the derived fields on re-upload."""
        from app.services import cv_service

        profile = self._good_profile(db)

        class GoodService:
            def extract_profile(self, cv_text):
                return {
                    "full_name": "New Name", "email": "new@example.com",
                    "professional_title": "Platform Engineer",
                    "experience_years": 9,
                }

        self._patch_upload(monkeypatch, GoodService())
        updated = cv_service.create_or_replace_profile_from_pdf(
            db, b"%PDF-fresh", "new_cv.pdf", user_id=profile.user_id
        )
        assert updated.full_name == "New Name"
        assert updated.email == "new@example.com"
        assert updated.professional_title == "Platform Engineer"
        assert updated.experience_years == 9


class TestPromptVersionComposition:
    """AI-13: the version hash covered only the system prompt. The
    profile-context rendering (truncations, the experience_years removal)
    changed what the model SAW with no version bump — scores silently not
    comparable. A composition constant is folded into the hash."""

    def test_format_is_major_dash_8_hex(self):
        import re

        from app.services.ai_service import AIService

        assert re.fullmatch(r"m2-[0-9a-f]{8}", AIService.matching_prompt_version())

    def test_composition_bump_changes_the_hash(self):
        from app.services.ai_service import AIService

        before = AIService.matching_prompt_version()
        original = AIService.MATCHING_INPUT_COMPOSITION_VERSION
        try:
            AIService.MATCHING_INPUT_COMPOSITION_VERSION = original + 1
            assert AIService.matching_prompt_version() != before, (
                "bumping MATCHING_INPUT_COMPOSITION_VERSION must change the "
                "version — that is the whole point of the constant"
            )
        finally:
            AIService.MATCHING_INPUT_COMPOSITION_VERSION = original
        # same constant back: the hash is stable (deterministic)
        assert AIService.matching_prompt_version() == before


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
            result = matcher_service.run_matching(db, profile=None, user_id=uid)
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

        matcher_service.run_matching(db, profile=profile, user_id=profile.user_id)
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
        # WO-08: cover_note is no longer generated or written — the legacy
        # value is frozen in place, untouched by a re-score
        assert m.cover_note == "LEGACY COVER NOTE", (
            "cover_note was written — nothing consumes it; a re-score must not refresh it"
        )
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

        matcher_service.run_matching(db, profile=a, user_id=a.user_id)

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
        assert "def needs_another_sample" not in src, (
            "needs_another_sample is defined twice — the shadow copy is back"
        )
        # Assert the CALL, not the name: a regression that leaves the import
        # in place but stops the loop from consulting the policy (inline
        # threshold instead) still contains the bare name and passed this
        # test — proven by regenerating the exact 62-row bug and watching
        # 33 tests stay green.
        assert "needs_another_sample(samples)" in src, (
            "rescore_backlog.py's sampling loop must CALL "
            "needs_another_sample(samples). An import alone satisfies a "
            "name grep; the loop re-implementing the thresholds is the "
            "regression to catch."
        )
        assert "def needs_another_sample" in inspect.getsource(matcher_service)

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


class TestTenancyLayer1:
    """TENANCY LAYER 1: services receive the profile; they never resolve it.

    The three cross-tenant P0 leaks all came from a service fetching "the"
    profile internally. Each test here uses the two-profile trap: the user
    has an ACTIVE profile (what any internal lookup returns) and the test
    explicitly passes a DIFFERENT one. If a service regresses to resolving
    identity itself — ignoring its parameter — it gets the active profile
    and the assertion fails. Revert-checked per service."""

    def _two_profiles(self, db):
        """The DB trap + the profile the test passes explicitly.

        profiles.user_id is UNIQUE (one profile per user), so the 'passed'
        profile is an UNSAVED in-memory object with distinctive content.
        The service contract only reads its attributes — if a regressed
        service re-resolves identity from the DB instead, it gets the
        active trap row and every assertion below fails."""
        active = _profile(db)  # is_active=1 — what a re-resolve would find
        active.full_name = "Active Trap"
        active.cv_text = "ACTIVE-TRAP-CV react legacy stack"
        db.commit()
        from app.models import Profile as P

        passed = P(user_id=active.user_id, is_active=0,
                   full_name="Passed Persona", cv_file_name="passed.pdf",
                   cv_text="PASSED-PROFILE-CV python fastapi specialist")
        return active, passed

    def test_create_draft_uses_the_passed_profile(self, db, monkeypatch):
        from app.services import draft_service
        from app.services.ai_service import AIService

        active, passed = self._two_profiles(db)
        job = _job_row(db, status="approved")
        job.description = "A Python role worth tailoring for."
        db.commit()

        captured = {}

        def fake_tailor(self, profile_context, cv_text, job_description):
            captured["cv_text"] = cv_text
            return {"cover_letter": "Dear Acme", "tailored_cv": "CV",
                    "changes_summary": ["n/a"]}

        fake = AIService.__new__(AIService)
        fake.model = "glm-test"
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(AIService, "tailor_application", fake_tailor)

        from app.services.draft_service import create_draft_for_job

        draft = create_draft_for_job(
            db, job, profile=passed, force=True, user_id=active.user_id
        )
        assert draft.status == "ready", draft.error
        assert "PASSED-PROFILE-CV" in captured.get("cv_text", ""), (
            f"tailoring used '{captured.get('cv_text', '')[:40]}' — the "
            "service resolved identity itself instead of using the passed "
            "profile (Layer 1 regression)"
        )

    def test_submit_draft_sends_as_the_passed_profile(self, db):
        from app.models import ApplicationDraft

        active, passed = self._two_profiles(db)
        job = _job_row(db, status="approved")
        job.application_url = "https://apply.example"
        db.commit()
        draft = ApplicationDraft(
            user_id=active.user_id, job_id=job.id, cover_letter="x",
            tailored_cv="y", changes_summary="[]", status="ready",
        )
        db.add(draft)
        db.commit()

        from app.services.draft_service import submit_draft

        application = submit_draft(
            db, draft, "browser", passed, user_id=active.user_id
        )
        assert "Passed Persona" in application.subject, (
            f"subject '{application.subject}' was built from the wrong "
            "profile — the service resolved identity itself (Layer 1 regression)"
        )
        assert "Active Trap" not in application.subject

    def test_retry_application_uses_the_passed_profile(self, db, monkeypatch):
        from app.models import Application, ApplicationDraft

        active, passed = self._two_profiles(db)
        job = _job_row(db, status="approved")
        job.application_email = "jobs@acme.example"
        db.commit()
        draft = ApplicationDraft(
            user_id=active.user_id, job_id=job.id, cover_letter="letter",
            tailored_cv="cv", changes_summary="[]", status="submitted",
        )
        db.add(draft)
        db.commit()
        application = Application(
            user_id=active.user_id, job_id=job.id, draft_id=draft.id,
            method="email", status="failed", error="boom",
        )
        db.add(application)
        db.commit()

        captured = {}
        from app.services import draft_service
        from app.services.apply_service import retry_application

        monkeypatch.setattr(
            draft_service, "_send_with_pdfs",
            lambda db_, app_, draft_, job_, profile_: captured.update(
                name=profile_.full_name, cv=profile_.cv_text
            ),
        )
        retry_application(db, application, passed)
        assert captured.get("name") == "Passed Persona", (
            f"retry emailed as '{captured.get('name')}' — the service "
            "resolved identity itself instead of using the passed profile "
            "(Layer 1 regression: this is the exact path that once emailed "
            "another user's CV)"
        )

    def test_run_matching_uses_the_passed_profile(self, db, monkeypatch):
        from app.services import matcher_service
        from app.services.ai_service import AIService

        active, passed = self._two_profiles(db)
        job = _job_row(db, status="new")
        job.description = "A Python role worth matching."
        db.commit()

        captured = {}

        def fake_match(profile_context, cv_text, job_description):
            captured["cv_text"] = cv_text
            return {"score": 80, "reasoning": "ok", "recommendation": "apply",
                    "confidence": "high", "matched_skills": ["Python"],
                    "missing_skills": [], "transferable_skills": [],
                    "cover_note": None}

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.match_job = fake_match
        monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)

        summary = matcher_service.run_matching(
            db, profile=passed, user_id=active.user_id
        )
        assert summary["matches_created"] == 1, summary
        assert "PASSED-PROFILE-CV" in captured.get("cv_text", ""), (
            f"matching scored '{captured.get('cv_text', '')[:40]}' — the "
            "service resolved identity itself instead of using the passed "
            "profile (Layer 1 regression)"
        )


class TestStripDeadSurface:
    """WO-08: cover_note was ~20% of the scoring bill and is rendered
    nowhere; Adzuna is redundant garnish in the GB pack (Reed carries the
    market — the scraper stays for the US/AU backbone); Teamtailor is dead
    without TEAMTAILOR_SITES, which was never configured."""

    def test_matching_prompt_has_no_cover_note(self):
        """The prompt's cover_note instructions (VOICE exception + JSON
        schema entry) are the ~20% of output tokens this WO removes."""
        from app.services.ai_service import AIService

        svc = AIService.__new__(AIService)
        prompt = svc._build_matching_prompt()
        assert "cover_note" not in prompt, (
            "cover_note still in the scoring prompt — the unconsumed field "
            "is still being generated (and paid for) on every match"
        )

    def test_match_response_schema_has_no_cover_note(self):
        from app.schemas.match import MatchResponse

        assert "cover_note" not in MatchResponse.model_fields, (
            "cover_note still exposed by the API — nothing renders it"
        )

    def test_gb_pack_has_no_adzuna(self):
        from app.services import source_packs

        gb = source_packs.pack_for_country("GB")
        assert "adzuna" not in gb, f"GB pack still scrapes Adzuna: {gb}"
        assert "reed" in gb, "Reed carries the UK market — it must stay"

    def test_adzuna_scraper_retained_for_expansion(self):
        """Demote, don't delete: Adzuna is the US/AU backbone."""
        from app.services.scrapers import SCRAPER_REGISTRY

        assert "adzuna" in SCRAPER_REGISTRY

    def test_teamtailor_fully_removed(self):
        from app.services.scrapers import SCRAPER_REGISTRY

        assert "teamtailor" not in SCRAPER_REGISTRY, (
            "teamtailor still registered — dead code without TEAMTAILOR_SITES"
        )
        from app.services import source_packs

        assert "teamtailor" not in source_packs.pack_for_country("SE")


class TestGlobalSourceBranchFilter:
    """WO-08 review: the global-allow-list branch (pre-onboarding users —
    the trial funnel) skipped the SCRAPER_REGISTRY filter the pack branch
    applies, so any stale name in SCRAPE_SOURCES wrote a failed ScrapeRun
    on every hunt instead of a clean skip. The test drives the PRODUCTION
    selector (_select_sources) — a test-side copy of the filter would
    only guard itself."""

    def test_global_branch_filters_stale_source_names(self, db, monkeypatch):
        from app.services import pipeline as pl

        class _StubSettings:
            @staticmethod
            def get_scrape_sources():
                return ["jobtech", "teamtailor", "bogus-future-remnant"]

        monkeypatch.setattr(pl, "settings", _StubSettings)
        # No ctx (no onboarded profile) -> the global-allow-list branch
        selected = pl._select_sources(ctx=None, sources=None)
        assert selected == ["jobtech"], (
            f"global branch selected {selected} — stale names must be "
            "filtered before scrape_source, never written as failed runs"
        )

    def test_pack_branch_still_filters_and_respects_local(self):
        from app.services import pipeline as pl

        ctx = {"country": "GB", "include_remote": False}
        selected = pl._select_sources(ctx=ctx, sources=None)
        assert "reed" in selected and "adzuna" not in selected

    def test_explicit_sources_filter_stale_names_too(self):
        """The docstring says EVERY branch filters — including explicit
        lists from internal callers (the API schema validates its own)."""
        from app.services import pipeline as pl

        assert pl._select_sources(ctx=None, sources=["jobtech"]) == ["jobtech"]
        assert pl._select_sources(ctx=None, sources=["jobtech", "teamtailor"]) == ["jobtech"]


class TestCountryRoutingGate:
    """WO-06 / D1: the location gate had no country dimension — a remote
    job located in the USA passed a Swedish include_remote user, whose
    pool became USA 73 vs Malmö 11. A job located in a FOREIGN country is
    blocked regardless of remote flag; global/unresolvable locations keep
    the remote-opt-in behaviour."""

    SE = {"country": "SE", "municipality": "Malmö", "region": "Skåne län",
          "include_remote": True, "remote_only": False}
    GB = {"country": "GB", "municipality": "Manchester", "region": "Greater Manchester",
          "include_remote": True, "remote_only": False}

    def test_foreign_remote_jobs_blocked_for_se_user(self):
        """NON-EEA foreign locations only — EEA neighbours pass for EEA
        users under free movement (see TestEEAFreeMovementBloc, which
        drove that policy decision red-first)."""
        from app.services.pipeline import passes_location_filter as gate

        for loc in ("USA", "Remote · USA", "New York, NY", "San Francisco",
                    "London, UK", "Toronto, ON"):
            job = _job(location=loc, remote=True)
            assert not gate(job, self.SE), (
                f"{loc!r} passed a Swedish user's gate — non-EEA-located jobs "
                "need work authorization the user lacks, even remote"
            )

    def test_in_country_jobs_unchanged_for_gb_user(self):
        from app.services.pipeline import passes_location_filter as gate

        assert gate(_job("London, UK", remote=True), self.GB), (
            "in-country remote job blocked for a GB user — country routing "
            "must be user-country-relative, not absolute"
        )

    def test_global_and_local_locations_keep_current_behaviour(self):
        from app.services.pipeline import passes_location_filter as gate

        # Malmö local passes as always (local terms path)
        assert gate(_job("Malmö, Skåne län", remote=False), self.SE)
        # Unresolvable/global remote: passes for include_remote users, as today
        assert gate(_job("Remote job", remote=True), self.SE)
        assert gate(_job("Remote", remote=True), self.SE)
        assert gate(_job(None, remote=True), self.SE)
        # ...and still blocked for strictly-local users, as today
        strict = dict(self.SE, include_remote=False)
        assert not gate(_job("Remote job", remote=True), strict)
        assert not gate(_job("USA", remote=True), strict)

    def test_multi_region_listings_stay_global(self):
        """"Europe, North America, Latin America" names hemispheres, not the
        US — an Europe-including remote listing is takeable for a Swede and
        must not be blocked via the bare word 'america'."""
        from app.services.country_lexicon import location_countries

        assert location_countries("Europe, North America, Latin America") == set()
        assert location_countries("Time zone: CET (+/- 3 hours)") == set()
        # ...while actual US locations still resolve
        assert location_countries("North America · USA") == {"US"}
        assert location_countries("Remote · USA") == {"US"}

    def test_multicountry_listing_admits_any_listed_country(self):
        """WO-06 review finding 1: a location enumerating several countries
        collapsed to one longest-regex winner ("Sweden, Germany" -> DE) and
        the gate blocked a job explicitly open in the user's country — the
        exact harm the product exists to prevent. Membership beats ranking:
        the gate blocks only when the matched set EXCLUDES the user's
        country."""
        from app.services.pipeline import passes_location_filter as gate

        job = _job("Sweden, Germany", remote=True)
        assert gate(job, self.SE), (
            "a remote job explicitly listing Sweden must pass a Swedish user"
        )
        assert not gate(job, self.GB), (
            "the same listing is foreign for a GB user — set excludes GB"
        )
        # The reviewer's city-vs-country case: Boston (US) AND Lincolnshire
        # (UK) in one string — GB user keeps it, SE user loses it
        both = _job("Boston, Lincolnshire, UK", remote=True)
        assert gate(both, self.GB)
        assert not gate(both, self.SE)

    def test_dotted_us_forms_resolve(self):
        """WO-06 review finding 2: 'u.s.'/'u.s.a.' ended in '.', so the
        trailing \b could never fire at end-of-string or before a space —
        "Remote, U.S." resolved to None and passed Swedish users."""
        from app.services.country_lexicon import location_countries
        from app.services.pipeline import passes_location_filter as gate

        for form in ("Remote, U.S.", "U.S. remote", "u.s.", "U.S.A.",
                     "Somewhere, U.S.A., Earth"):
            assert location_countries(form) == {"US"}, (
                f"{form!r} did not resolve to US — dotted forms must match"
            )
        assert not gate(_job("Remote, U.S.", remote=True), self.SE), (
            "'Remote, U.S.' passed a Swedish user — the D1 symptom"
        )

    def test_swedish_located_jobs_pass_for_se_user(self):
        from app.services.pipeline import passes_location_filter as gate

        for loc in ("Stockholm", "Göteborg", "Malmö", "Sverige"):
            assert gate(_job(loc, remote=True), self.SE), (
                f"{loc!r} is in-country for an SE user — must not be blocked"
            )

    def test_foreign_pool_never_stored(self, db, monkeypatch):
        """The gate runs inside scrape_source BEFORE storage — foreign rows
        must never reach job_postings."""
        from app.services import pipeline as pl
        from app.services.scrapers.base import NormalizedJob

        class _Stub:
            @staticmethod
            def is_configured(ctx):
                return True

            @staticmethod
            def fetch(ctx):
                return [
                    NormalizedJob(source="stub", source_id="1", title="US remote",
                                  company="X", url="https://x/1", remote=True,
                                  location="Remote · USA"),
                    NormalizedJob(source="stub", source_id="2", title="Malmö",
                                  company="Y", url="https://x/2", remote=False,
                                  location="Malmö, Skåne län"),
                ]

        monkeypatch.setitem(pl.SCRAPER_REGISTRY, "stub", _Stub)
        pl.scrape_source(db, "stub", ctx=self.SE)
        stored = db.query(JobPosting).filter(JobPosting.source == "stub").all()
        assert [j.title for j in stored] == ["Malmö"], (
            f"pool stored {[j.title for j in stored]} — foreign-located jobs "
            "must be gated before storage, not after"
        )


class TestJobtechPagination:
    """WO-06 / D1: the 75%-keeper-rate source fetched one page per query.
    Pagination walks offset up to a cap and stops on a short page."""

    def test_multiple_pages_fetched_until_short_page(self, monkeypatch):
        import app.services.scrapers.jobtech as jt

        pages = [
            {"hits": [{"id": str(i), "headline": f"j{i}", "removed": False} for i in range(100)]},
            {"hits": [{"id": f"b{i}", "headline": f"j{i}", "removed": False} for i in range(100)]},
            {"hits": [{"id": f"c{i}", "headline": f"j{i}", "removed": False} for i in range(37)]},
        ]
        calls = []

        def fake_get(url, params=None, **kw):
            # scraper sends list-of-tuples params (place filter support)
            p = dict(params)
            calls.append(p)
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": staticmethod(lambda s=None: pages[min(p["offset"] // 100, 2)]),
            })()

        monkeypatch.setattr(jt.httpx, "get", fake_get)
        scraper = jt.JobtechScraper()
        jobs = scraper.fetch({"queries": ["dev"], "country": "SE"})
        assert len(jobs) == 237, f"expected 100+100+37=237 unique jobs, got {len(jobs)}"
        assert [c["offset"] for c in calls] == [0, 100, 200], (
            f"pagination did not walk offset: {[c.get('offset') for c in calls]}"
        )

    def test_single_full_page_stops_at_cap(self, monkeypatch):
        import app.services.scrapers.jobtech as jt

        def fake_get(url, params=None, **kw):
            p = dict(params)
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": staticmethod(lambda s=None: {
                    "hits": [{"id": str(p["offset"] + i), "headline": "j",
                              "removed": False} for i in range(100)]}),
            })()

        monkeypatch.setattr(jt.httpx, "get", fake_get)
        # No delta_since = backfill mode: the deep one-time read
        jobs = jt.JobtechScraper().fetch({"queries": ["dev"]})
        assert len(jobs) == jt.MAX_PAGES_BACKFILL * 100, (
            f"backfill cap not respected: {len(jobs)}"
        )

    def test_delta_mode_caps_at_delta_pages(self, monkeypatch):
        """With a published-after cutoff the result set is a day of new
        ads — small, but still capped so a bad day can't page forever."""
        from datetime import datetime, timezone

        import app.services.scrapers.jobtech as jt

        def fake_get(url, params=None, **kw):
            p = dict(params)
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": staticmethod(lambda s=None: {
                    "hits": [{"id": str(p["offset"] + i), "headline": "j",
                              "removed": False} for i in range(100)]}),
            })()

        monkeypatch.setattr(jt.httpx, "get", fake_get)
        since = datetime(2026, 8, 29, tzinfo=timezone.utc)
        jobs = jt.JobtechScraper().fetch({"queries": ["dev"], "delta_since": since})
        assert len(jobs) == jt.MAX_PAGES_DELTA * 100, (
            f"delta cap not respected: {len(jobs)}"
        )


class TestEEAFreeMovementBloc:
    """WO-06 review follow-up: the gate blocked on a work-authorization
    rationale that only holds for non-EEA states. A Malmö user CAN work
    for a Danish employer (Öresund: ~20k daily commuters; EEA free
    movement), so EEA-located sets pass for EEA users. Post-Brexit GB
    has no free-movement bloc — its behaviour is unchanged."""

    SE = {"country": "SE", "municipality": "Malmö", "region": "Skåne län",
          "include_remote": True, "remote_only": False}
    GB = {"country": "GB", "municipality": "Manchester", "region": "Greater Manchester",
          "include_remote": True, "remote_only": False}

    def test_se_user_keeps_eea_neighbour_remote_jobs(self):
        from app.services.pipeline import passes_location_filter as gate

        for loc in ("Copenhagen, Denmark", "Denmark", "Berlin, Germany",
                    "Amsterdam, Netherlands", "Oslo, Norway"):
            assert gate(_job(loc, remote=True), self.SE), (
                f"{loc!r} blocked for an SE user — EEA free movement makes "
                "neighbour-located remote jobs takeable (the Öresund case)"
            )

    def test_se_user_still_loses_true_work_authorization_blocks(self):
        from app.services.pipeline import passes_location_filter as gate

        for loc in ("USA", "Remote · USA", "Toronto, Canada", "Singapore"):
            assert not gate(_job(loc, remote=True), self.SE), (
                f"{loc!r} passed an SE user — non-EEA locations need work "
                "authorization the user doesn't have"
            )

    def test_gb_user_has_no_bloc_post_brexit(self):
        from app.services.pipeline import passes_location_filter as gate

        assert not gate(_job("Berlin, Germany", remote=True), self.GB), (
            "GB users have no EEA free movement — foreign EEA locations stay "
            "blocked for them (post-Brexit right-to-work reality)"
        )
        # in-country unchanged
        assert gate(_job("London, UK", remote=True), self.GB)


class TestOneDriverEverywhere:
    """WO-11 / ARCHITECTURE F2: asyncpg is documented to fail on BOTH
    Supabase poolers (prepared statements). SQLAlchemy 2.0's psycopg
    dialect serves create_engine AND create_async_engine, so the async
    auth layer runs on psycopg too — one driver, no asyncpg."""

    def test_async_url_never_uses_asyncpg(self):
        from app.core.database import async_database_url

        cases = {
            "postgresql+psycopg://u:p@h:5432/db": "postgresql+psycopg://u:p@h:5432/db",
            "postgresql://u:p@h:5432/db": "postgresql+psycopg://u:p@h:5432/db",
        }
        for url, want in cases.items():
            got = async_database_url(url)
            assert got == want, f"{url} -> {got}, want {want}"
            assert "asyncpg" not in got, (
                "asyncpg in the async engine URL — it fails on both "
                "Supabase poolers (F2); both engines run on psycopg"
            )

    def test_sqlite_async_translation_unchanged(self):
        from app.core.database import async_database_url

        assert async_database_url("sqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"

    def test_asyncpg_absent_from_the_lockfile(self):
        from pathlib import Path

        lock = Path(__file__).resolve().parent.parent / "requirements.lock"
        txt = Path(__file__).resolve().parent.parent / "requirements.txt"
        for f in (lock, txt):
            assert "asyncpg" not in f.read_text(), (
                f"{f.name} still pins asyncpg — the driver that fails on "
                "both Supabase poolers must not ship"
            )


class TestSyncEngineDriverSafety:
    """WO-11 review: bare postgresql:// resolves to the psycopg2 dialect
    in SQLAlchemy 2.0 — psycopg2 is NOT installed. The async engine was
    protected by async_database_url; the sync engine took DATABASE_URL
    verbatim, so a Render/Heroku-style URL crashed at first connection
    with ModuleNotFoundError: psycopg2. Normalization now lives in one
    pure function used at config time."""

    def test_every_postgres_shape_normalizes_to_psycopg(self):
        from app.core.database import normalize_postgres_url

        cases = {
            "postgres://u:p@h:5432/db": "postgresql+psycopg://u:p@h:5432/db",
            "postgresql://u:p@h:5432/db": "postgresql+psycopg://u:p@h:5432/db",
            "postgresql+psycopg://u:p@h:5432/db": "postgresql+psycopg://u:p@h:5432/db",
            "sqlite:///./x.db": "sqlite:///./x.db",
        }
        for url, want in cases.items():
            assert normalize_postgres_url(url) == want, f"{url} -> {normalize_postgres_url(url)!r}"

    def test_normalized_urls_resolve_to_the_installed_driver(self):
        """Dialect-level proof, no connection: every accepted Postgres
        shape must build an engine whose driver is psycopg — never the
        uninstalled psycopg2."""
        from sqlalchemy import create_engine

        from app.core.database import normalize_postgres_url

        for url in ("postgres://u:p@h:5432/db",
                    "postgresql://u:p@h:5432/db",
                    "postgresql+psycopg://u:p@h:5432/db"):
            eng = create_engine(normalize_postgres_url(url))
            assert eng.dialect.driver == "psycopg", (
                f"{url} resolved to driver {eng.dialect.driver!r} — only "
                "psycopg (3) is installed"
            )


class TestDependencyFreeMigrations:
    """WO-11 review round 2: alembic's env.py imported app.core.database,
    which instantiates Settings() at module scope — so 'alembic upgrade
    head' with only DATABASE_URL (the migration-container shape: Render
    pre-deploy, k8s init container, one-off docker run) died on the
    AUTH_SECRET production guard before touching the database. CI could
    never see it: the alembic step sets DEBUG=true by design. The fix:
    the URL normalizer and ORM Base live in dependency-free modules the
    migration runner can import without any app config."""

    def test_asyncpg_stepdown_covered_by_the_one_normalizer(self):
        """A pre-WO-11 DATABASE_URL still carrying +asyncpg must step
        down to psycopg — the defence env.py used to have locally and
        my refactor deleted while the comment claimed it stayed."""
        from app.core.dburl import normalize_postgres_url

        assert normalize_postgres_url(
            "postgresql+asyncpg://u:p@h:5432/db"
        ) == "postgresql+psycopg://u:p@h:5432/db"

    def test_dburl_and_orm_import_no_app_config(self):
        """The property CI can't exercise in-process: importing the
        migration-runner modules must not construct Settings or engines.
        Checked in a fresh subprocess where sys.modules starts clean."""
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; import app.core.dburl, app.core.orm; "
             "assert 'app.core.config' not in sys.modules, 'config loaded'; "
             "assert 'app.core.database' not in sys.modules, 'database loaded'"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"dependency-free modules pull app config:\n{r.stderr[-400:]}"
        )


class TestFabricationGuard:
    """WO-01 Layer A: the deterministic checker, driven from the
    PRODUCTION module (a shadow copy is how rescore_backlog diverged
    from the matcher three times). Fixtures live in
    tests/fixtures/fabrication/."""

    @staticmethod
    def _fixture(name):
        import json
        from pathlib import Path

        path = (Path(__file__).resolve().parent / "fixtures" / "fabrication"
                / f"{name}.json")
        return json.loads(path.read_text())

    def test_clean_tailoring_has_zero_findings(self):
        """The false-positive guard, and the harder direction: a checker
        that flags everything trivially passes the fabricated case."""
        from app.services.fabrication import unsupported_claims

        fx = self._fixture("clean")
        findings = unsupported_claims(fx["source_cv"], fx["tailored"])
        assert findings == [], (
            f"clean tailored output flagged {[f.value for f in findings]} — "
            "false positives on a faithful document are the failure mode "
            "that would block legitimate drafts"
        )

    def test_each_planted_defect_is_named(self):
        """All five planted strings must be NAMED — five unrelated false
        positives must fail."""
        from app.services.fabrication import unsupported_claims

        fx = self._fixture("fabricated")
        findings = unsupported_claims(fx["source_cv"], fx["tailored"])
        by_kind = {}
        for f in findings:
            by_kind.setdefault(f.kind, []).append(f.value.lower())

        for expected in fx["expected_findings"]:
            kind = expected["kind"]
            assert kind in by_kind, (
                f"planted {kind} defect not detected at all; got kinds {sorted(by_kind)}"
            )
            if "value" in expected:
                assert any(expected["value"] in v for v in by_kind[kind]), (
                    f"planted {kind} {expected['value']!r} not named; "
                    f"got {by_kind[kind]}"
                )
            else:
                assert any(expected["value_contains"] in v for v in by_kind[kind]), (
                    f"planted {kind} containing {expected['value_contains']!r} "
                    f"not named; got {by_kind[kind]}"
                )

    def test_swedish_translation_round_trip(self):
        """The translation trap: Swedish prose, English CV. Org/tech atoms
        survive translation and must NOT false-positive; diacritics
        (Malmö) preserved through normalisation."""
        from app.services.fabrication import unsupported_claims

        fx = self._fixture("swedish_roundtrip")
        findings = unsupported_claims(fx["source_cv"], fx["tailored"])
        org_tech = [f for f in findings
                    if f.kind in ("organisation", "technology")]
        assert org_tech == [], (
            f"translation-invariant atoms flagged across languages: "
            f"{[(f.kind, f.value) for f in org_tech]}"
        )
        assert findings == [], (
            f"unexpected findings on faithful translation: "
            f"{[(f.kind, f.value) for f in findings]}"
        )

    def test_shifted_year_and_inflated_metric_detection(self):
        """The two subtle defects: a shifted year closes an employment
        gap; an inflated metric inflates an achievement. Numeric-core
        matching must catch 40% vs 12% (not just string absence)."""
        from app.services.fabrication import unsupported_claims

        src = "Worked at Svenska Spel 2019 to 2023, improved conversion by 12%."
        tailored = "Worked at Svenska Spel 2017 to 2023, improved conversion by 40%."
        findings = unsupported_claims(src, tailored)
        values = " ".join(f.value.lower() for f in findings)
        assert "2017" in values, "shifted year not caught"
        assert "40" in values, "inflated percentage not caught (12% -> 40%)"
        assert "2019" not in values and "12" not in values.split("40")[0], (
            "supported atoms falsely flagged"
        )

    def test_claim_carries_sentence_context(self):
        """The review UI shows WHERE the unverified claim appears."""
        from app.services.fabrication import unsupported_claims

        src = "Skills: Python"
        tailored = "Certified Kubernetes administrator with Python skills."
        findings = unsupported_claims(src, tailored)
        with_ctx = [f for f in findings if f.kind in ("credential", "technology")
                    and f.context]
        assert with_ctx, "high-confidence findings must carry sentence context"


class TestFabricationLiveFPClasses:
    """Regression fixtures from the first LIVE judge run (2026-08-27,
    3/5 docs flagged): the real false-positive classes the checker
    shipped with, each now named."""

    def test_addressee_company_is_context_not_fabrication(self):
        from app.services.fabrication import unsupported_claims

        src = "Erik Lindberg. Skills: Python."
        tailored = "Hej Birger AB,\n\nJag har arbetat med Python."
        findings = unsupported_claims(src, tailored, allowed_names=["Birger AB"])
        assert not any("birger" in f.value.lower() for f in findings), (
            "the employer being applied to is legitimate context, not a "
            "career claim"
        )

    def test_glued_skill_runs_survive_connector_differences(self):
        """Swedish CV: 'TypeScript och React'; tailored: 'TypeScript React'.
        Both glued-pair orders and the connector-free form must pass."""
        from app.services.fabrication import unsupported_claims

        src = ("Skills: TypeScript och React, REST API:er och PostgreSQL, "
               "SQL databases.")
        tailored = "TypeScript React and REST APIs PostgreSQL. Databases SQL."
        org_findings = [f for f in unsupported_claims(src, tailored)
                         if f.kind == "organisation"]
        assert org_findings == [], (
            f"glued skill runs flagged: {[(f.value) for f in org_findings]}"
        )

    def test_swedish_salutation_breaks_organisation_runs(self):
        from app.services.fabrication import unsupported_claims

        src = "Erik. Skills: Python."
        tailored = "Hej Sogeti,\n\nMed vänliga hälsningar, Erik"
        org = [f for f in unsupported_claims(src, tailored)
               if f.kind == "organisation"]
        assert org == [], [f.value for f in org]


class TestAllowedNamesSubtraction:
    """WO-01 review: allowed_names matched as SUBSTRINGS, so with
    job.title in the list the standard CV line 'Title, Company — years'
    glued into one run and the invented employer rode through on the
    allowed title prefix (the exact inverse _org_supported guards
    against — the allowed path bypassed it). Subtraction must be
    token-wise: strip the allowed name's tokens, judge the REMAINDER."""

    def test_glued_title_plus_fabricated_employer_is_caught(self):
        from app.services.fabrication import split_tiers, unsupported_claims

        src = "Casino Cosmopol, Malmö — Gaming Operations Manager."
        tailored = "Software Engineer, Acme Global Ltd — 2019-2022."
        findings = unsupported_claims(
            src, tailored,
            allowed_names=["Birger AB", "Software Engineer"])
        high, _ = split_tiers(findings)
        assert any("acme" in f.value.lower() for f in high), (
            f"invented employer rode through on the allowed job title: "
            f"{[(f.kind, f.value, f.tier) for f in findings]}"
        )

    def test_pure_title_run_still_allowed(self):
        """The FP the follow-up fixed must stay fixed: a run that IS the
        job title (plus generic glue) is application context."""
        from app.services.fabrication import unsupported_claims

        src = "Skills: Python."
        tailored = "Applying for the Software Engineer role."
        findings = unsupported_claims(
            src, tailored, allowed_names=["Software Engineer"])
        assert not any("software engineer" in f.value.lower()
                       for f in findings)

    def test_dead_technology_entries_now_match(self):
        """WO-01 review: 7 vocabulary entries (.net, node.js, c#, ...)
        could never match — _normalise strips their punctuation. Fixed by
        normalising the vocabulary at compile time."""
        from app.services.fabrication import extract_claims

        text = "I have used .NET, Node.js, C#, C++, Next.js in production."
        techs = {c.value for c in extract_claims(text) if c.kind == "technology"}
        for expected in (".net", "node.js", "c#", "c++", "next.js"):
            assert expected in techs, f"{expected} dead — punct stripped before match"


class TestTechPatternProseSafety:
    """WO-01 review round 2: normalising the vocabulary made c#/c++/.net
    reachable but ALSO made them match bare 'c' and 'net' in ordinary
    prose ("Grades: A, B, C" flagged C++). Punctuated entries must match
    against the RAW casefolded text, where their punctuation still
    exists — the banner's credibility depends on not crying wolf."""

    def test_ordinary_prose_produces_no_tech_claims(self):
        from app.services.fabrication import extract_claims

        for text in ("Grades: A, B, C in my final year.",
                     "I improved net revenue and reduced churn.",
                     "Vitamin C and a safety net were mentioned.",
                     "The CI pipeline rests until Friday."):
            techs = [c.value for c in extract_claims(text)
                     if c.kind == "technology"]
            assert techs == [], f"{text!r} flagged {techs} — prose noise"

    def test_genuine_punctuated_mentions_still_match_without_phantoms(self):
        from app.services.fabrication import extract_claims

        techs = {c.value for c in
                 extract_claims("Used C# daily, built .NET and Node.js, "
                                "CI/CD, scikit-learn, C++ services.")
                 if c.kind == "technology"}
        assert {"c#", ".net", "node.js", "ci/cd", "scikit-learn", "c++"} <= techs
        # no phantom: a C# mention must not also emit c++
        assert extract_claims("Used C# daily.")[0:1] == [] or not any(
            c.value == "c++" for c in extract_claims("Used C# daily."))


class TestTechSupportGround:
    """WO-01 review r3: extraction moved punctuated entries to the raw
    haystack, but the SUPPORT check still normalises the claim — 
    _normalise('c#') = 'c', a substring of nearly any CV, so invented
    C#/.NET/C++ claims were never reported (false-negative side of the
    prose fix). The check needs the same per-entry ground."""

    def test_invented_punctuated_tech_is_flagged(self):
        from app.services.fabrication import unsupported_claims

        cv = ("Anthony Foran. Casino Cosmopol, Malmö. Skills: Python, "
              "SQL, network administration.")
        for mention in ("I have 5 years of C# experience.",
                        "I built services in .NET Core.",
                        "I use C++ daily."):
            flagged = [c.value for c in unsupported_claims(cv, mention)
                       if c.kind == "technology"]
            assert flagged, f"{mention!r} invented tech not reported"

    def test_genuine_punctuated_tech_in_cv_not_flagged(self):
        from app.services.fabrication import unsupported_claims

        cv = "Skills: C#, .NET, Node.js, Python."
        tailored = "Daily driver: C# and .NET services in Node.js."
        flagged = [c.value for c in unsupported_claims(cv, tailored)
                   if c.kind == "technology"]
        assert flagged == [], f"raw-ground support false positive: {flagged}"


class TestTechVerificationTolerance:
    """WO-01 review r4: extraction and verification have OPPOSITE
    tolerance requirements — extraction strict (bare 'c' in prose must
    not match), verification lenient (a human CV's formatting is
    arbitrary: 'Node JS' must support a tailored 'Node.js'). One ground
    oscillated for four rounds; separator-tolerant verification ends it."""

    def test_cv_spelling_variance_supports_tailored_form(self):
        from app.services.fabrication import unsupported_claims

        cases = [
            ("Skills: CI / CD, Jenkins.", "I run CI/CD pipelines."),
            ("CI-CD automation experience.", "I run CI/CD pipelines."),
            ("Skills: Node JS, React.", "I build with Node.js."),
            ("scikit learn, pandas.", "I use scikit-learn."),
            ("Google-Cloud, Docker.", "I deploy on Google Cloud."),
        ]
        for cv, tailored in cases:
            flagged = [c.value for c in unsupported_claims(cv, tailored)
                       if c.kind == "technology"]
            assert flagged == [], (
                f"CV {cv!r} failed to support tailored {tailored!r}: {flagged}"
            )

    def test_invented_punctuated_tech_still_flagged(self):
        from app.services.fabrication import unsupported_claims

        cv = "Skills: Python, SQL, network administration."
        for mention in ("C# experience", ".NET Core services", "C++ daily"):
            flagged = [c.value for c in unsupported_claims(cv, mention)
                       if c.kind == "technology"]
            assert flagged, f"invented {mention!r} no longer reported"

    def test_asp_net_extraction(self):
        """r4 carry-over: leading (?<!\\w) blocked '.net' inside 'ASP.NET'
        — an invented ASP.NET claim extracted nothing at all."""
        from app.services.fabrication import extract_claims, unsupported_claims

        techs = [c.value for c in
                 extract_claims("Built on ASP.NET Core and ASP.NET MVC")
                 if c.kind == "technology"]
        assert ".net" in techs, f"ASP.NET invisible: {techs}"
        # invented ASP.NET against a CV without it -> flagged
        flagged = [c.value for c in
                   unsupported_claims("Skills: Python.",
                                      "Built on ASP.NET Core")
                   if c.kind == "technology"]
        assert ".net" in flagged
        # genuine ASP.NET in the CV -> supported
        assert not [c for c in unsupported_claims("Skills: ASP.NET MVC.",
                                                  "Built on ASP.NET Core")
                    if c.kind == "technology"]


class TestLossyContextRootCause:
    """WO-01 review final round, root cause: the CV says '20 års
    erfarenhet från reglerad verksamhet' (20 years in REGULATED
    OPERATIONS); build_profile_context rendered 'Professional title:
    Junior Fullstack Developer' + 'Years of experience: 20' — the
    domain qualifier stripped, so the model received 'junior developer
    with 20 years of experience', whose only coherent reading is 20
    years of DEVELOPMENT. The judge's competence-inflation findings
    were the model using a line WE handed it. The CV text (included in
    full in every prompt) states it correctly — the bare number line is
    redundant AND lossy, so it goes."""

    def test_context_never_renders_bare_years_line(self):
        from app.models import Profile as P
        from app.services.cv_service import build_profile_context

        profile = P(is_active=1, user_id=uuid.uuid4(),
                    professional_title="Junior Fullstack Developer",
                    experience_years=20, cv_file_name="cv.pdf",
                    cv_text="20 års erfarenhet från reglerad verksamhet")
        ctx = build_profile_context(profile)
        assert "years of experience" not in ctx.lower(), (
            f"lossy bare-years line still rendered: {ctx!r} — the domain "
            "qualifier must live in the CV text, not be flattened here"
        )
        # the truthful line stays available: the CV itself is in every prompt
        assert "reglerad" in profile.cv_text


class TestLiveCatchFixturesLoadBearing:
    """WO-02 review: live_catch snapshots were write-only — the suite was
    green with two physically absent. WO-01 specified them as PERMANENT
    regression fixtures: every snapshot on disk must load, carry recorded
    catches, and its Layer-A findings must still be reproducible by the
    production checker."""

    def test_every_snapshot_loads_and_layer_a_reproduces(self):
        import json
        from pathlib import Path

        from app.services.fabrication import unsupported_claims

        d = Path(__file__).parent / "fixtures" / "fabrication"
        snapshots = sorted(d.glob("live_catch_*.json"))
        assert snapshots, "no live_catch fixtures on disk — the catches are gone"
        # EACH catch is individually load-bearing: deleting one snapshot
        # (as happened during the baseline rerun) must fail here, not just
        # emptying the directory. New catches append to this set.
        expected = {"live_catch_580.json", "live_catch_583.json"}
        present = {s_.name for s_ in snapshots}
        assert expected <= present, (
            f"recorded catches missing from disk: {expected - present} — "
            "a production catch was dropped without replacing its fixture"
        )
        for snap in snapshots:
            data = json.loads(snap.read_text())
            assert data.get("judge"), f"{snap.name}: no judge catches recorded"
            for c in data["judge"]:
                assert c.get("claim"), f"{snap.name}: judge entry without claim"
            # Layer A findings recorded in the snapshot must still be
            # produced by the CURRENT checker (deterministic half)
            claims = {c["value"].split("|")[0].lower()
                      for c in data.get("layer_a", [])}
            if claims:
                reproduced = unsupported_claims(
                    data["source_cv"], data["tailored"])
                now = {c.value.split("|")[0].lower() for c in reproduced}
                assert claims <= now, (
                    f"{snap.name}: recorded Layer-A catches {claims - now} "
                    "no longer reproduce — a checker change lost a real catch"
                )


class TestAICostRecording:
    """WO-05: per-call AI usage rows — cost accounting, price-drift
    detection, and the residency audit trail (endpoint+model+timestamp).
    Recorded from _complete, the ONE call site every AI operation uses."""

    def test_usage_row_recorded_per_call(self, db):
        import app.services.ai_service as ai_mod
        from app.models import AIUsage

        class _Usage:
            prompt_tokens = 1000
            completion_tokens = 200
            model_dump = lambda self: {
                "prompt_tokens": 1000, "completion_tokens": 200}

        class _Resp:
            usage = _Usage()
            choices = [type("C", (), {"message": type("M", (), {
                "content": '{"score": 50}', "reasoning_content": None})()})()]
            id = "req_test_1"

        svc = ai_mod.AIService.__new__(ai_mod.AIService)
        svc.model = "glm-5.1"
        svc.max_tokens = 2000
        svc.thinking = {"type": "disabled"}
        class _Completions:
            @staticmethod
            def create(**kw):
                return _Resp()

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        svc.client = _Client()

        from app.core.database import SessionLocal
        before = SessionLocal().query(AIUsage).count()
        svc._complete("sys", "user", kind="match")
        after = SessionLocal().query(AIUsage).count()
        row = SessionLocal().query(AIUsage).order_by(AIUsage.id.desc()).first()
        assert after == before + 1, "no usage row recorded per call"
        assert row.kind == "match" and row.model == "glm-5.1"
        assert row.prompt_tokens == 1000 and row.completion_tokens == 200
        assert row.request_id == "req_test_1"
        assert row.endpoint and "z.ai" in row.endpoint, (
            f"residency-audit field empty: {row.endpoint!r}"
        )
        # cost math: GLM-5.1 verified prices 1.40/M in, 4.40/M out
        expected_usd = (1000 * 1.40e-6) + (200 * 4.40e-6)
        assert abs(row.cost_usd / 1e6 - expected_usd) < 1e-9, (
            f"cost {row.cost_usd}u$ != expected {expected_usd:.9f}$"
        )

    def test_cached_tokens_priced_at_cached_rate(self, db):
        import app.services.ai_service as ai_mod

        class _Details:
            cached_tokens = 800

        class _Usage:
            prompt_tokens = 1000
            completion_tokens = 0
            prompt_tokens_details = _Details()  # attribute, as the SDK provides

        class _Resp:
            usage = _Usage()
            choices = [type("C", (), {"message": type("M", (), {
                "content": "ok", "reasoning_content": None})()})()]
            id = "req_test_2"

        svc = ai_mod.AIService.__new__(ai_mod.AIService)
        svc.model = "glm-5.1"
        svc.max_tokens = 2000
        svc.thinking = {"type": "disabled"}
        class _Completions2:
            @staticmethod
            def create(**kw):
                return _Resp()

        class _Chat2:
            completions = _Completions2()

        class _Client2:
            chat = _Chat2()

        svc.client = _Client2()
        svc._complete("s", "u", kind="judge")
        from app.core.database import SessionLocal
        from app.models import AIUsage
        row = SessionLocal().query(AIUsage).order_by(AIUsage.id.desc()).first()
        assert row.cached_tokens == 800
        expected = (800 * 0.26e-6) + (200 * 1.40e-6)  # cached + uncached input
        assert abs(row.cost_usd / 1e6 - expected) < 1e-9


class TestSentryPIIScrub:
    """F7: Sentry captures request bodies — on this API that means CV
    text. scrub_pii must drop bodies whole and redact every known
    PII-carrying field, recursively; init must be a no-op without a DSN."""

    def test_scrub_drops_bodies_and_redacts_fields(self):
        from app.core.telemetry import scrub_pii

        event = {
            "request": {"data": {"cv_text": "SECRET CV"}},
            "extra": {"cv_text": "SECRET", "nested": {"cover_letter": "SECRET",
                     "safe": "ok"}, "tailored_cv": "SECRET"},
        }
        out = scrub_pii(event)
        assert out["request"]["data"] == "[redacted]"
        assert out["extra"]["cv_text"] == "[redacted]"
        assert out["extra"]["nested"]["cover_letter"] == "[redacted]"
        assert out["extra"]["nested"]["safe"] == "ok"
        assert "SECRET" not in str(out)

    def test_scrub_truncates_cv_sized_strings(self):
        from app.core.telemetry import scrub_pii

        out = scrub_pii({"extra": {"some_blob": "x" * 9000}})
        assert len(out["extra"]["some_blob"]) < 2100

    def test_init_noop_without_dsn(self):
        from app.core import telemetry

        telemetry.init_sentry()  # must not raise with no DSN configured
        # and must not have installed the client
        try:
            import sentry_sdk
            assert sentry_sdk.Hub.current.client is None or \
                not sentry_sdk.Hub.current.client.is_active()
        except Exception:
            pass


class TestScrubRealisticEvent:
    """Reviewer's realistic-event probe: the original tests checked extra/
    request in isolation. A REAL Sentry event also carries breadcrumbs
    (log messages — which include request payloads) and frame locals with
    IDENTITY fields (full_name is personal data and was not in the set)."""

    def test_breadcrumbs_are_scrubbed(self):
        from app.core.telemetry import scrub_pii

        event = {"breadcrumbs": [
            {"message": '{"cv_text": "SECRET CV", "full_name": "Anthony Foran"}'}],
            "extra": {}}
        out = scrub_pii(event)
        assert "SECRET" not in str(out) and "Anthony Foran" not in str(out), (
            "breadcrumb messages carry request payloads — they must be "
            "scrubbed like every other pocket"
        )

    def test_identity_fields_are_redacted(self):
        from app.core.telemetry import scrub_pii

        event = {"exception": {"values": [{"type": "E", "stacktrace": {"frames": [
            {"vars": {"profile": {"full_name": "Anthony Foran",
                                  "email": "x@y.se",
                                  "phone": "+4670000000",
                                  "location": "Malmö"}}}]}}]}}
        out = scrub_pii(event)
        flat = str(out)
        for leaked in ("Anthony Foran", "x@y.se", "+4670000000"):
            assert leaked not in flat, f"identity field {leaked!r} left the machine"


class TestScrubCollectionPoint:
    """Reviewer round 2: allow-listing key names cannot cover frame
    locals (arbitrary, unbounded names: user_message, raw, result_text…)
    and breadcrumb DATA is the field integrations actually populate
    (logging.py:378). The fix is at the collection point."""

    def test_frame_locals_never_collected(self):
        """include_local_variables=False must be set — the SDK default
        (True) ships 2KB CV fragments under arbitrary variable names,
        on the error path (the judge's fail-closed ValueError fires on
        exactly the documents most worth protecting)."""
        import inspect

        import app.core.telemetry as telemetry

        src = inspect.getsource(telemetry.init_sentry)
        assert "include_local_variables=False" in src, (
            "frame locals still collected — name-level redaction cannot "
            "cover arbitrary variable names; close it at collection"
        )

    def test_breadcrumb_data_scrubbed(self):
        from app.core.telemetry import scrub_pii

        event = {"breadcrumbs": [
            {"category": "httpx",
             "message": "POST /api",
             "data": {"payload": "CV: " + "x" * 5000,
                      "cv_text": "SECRET",
                      "safe": "ok"}}],
            "extra": {}}
        out = scrub_pii(event)
        crumb = out["breadcrumbs"][0]
        assert crumb["message"] == "[redacted message]"
        assert crumb["data"]["cv_text"] == "[redacted]"
        assert crumb["data"]["safe"] == "ok"
        assert len(str(crumb["data"]["payload"])) < 2100, (
            f"breadcrumb data bypassed even the truncation cap: "
            f"{len(str(crumb['data']['payload']))} chars"
        )


class TestHuntClaimLock:
    """WO-04 / D3: two processes running the scheduler must not
    double-fire a hunt. DB claim lock — portable, stale-TTL stealable,
    always released."""

    def test_exactly_one_claimant_wins(self, db):
        from app.services.worker import claim_hunt, release_hunt

        assert claim_hunt(db) is True, "first claimant must win"
        assert claim_hunt(db) is False, "second concurrent claimant must skip"
        release_hunt(db)
        assert claim_hunt(db) is True, "released claim is claimable again"

    def test_stale_claim_is_stealable(self, db):
        """A crashed holder self-heals: once the TTL passes, the claim
        is stealable — the lock must never deadlock the hunt forever."""
        import datetime

        from app.core.timeutil import utc_now
        from app.models import SystemLock

        db.add(SystemLock(name="hunt", locked_until=utc_now() - datetime.timedelta(minutes=30)))
        db.commit()
        from app.services.worker import claim_hunt

        assert claim_hunt(db) is True, "stale claim must be stealable"

    def test_release_is_idempotent(self, db):
        from app.services.worker import claim_hunt, release_hunt

        claim_hunt(db)
        release_hunt(db)
        release_hunt(db)  # crashed-after-release path must not raise
        assert claim_hunt(db) is True


class TestWorkerEntrypoint:
    """WO-04: the worker owns the hunt, claim-locked. The API lifespan
    keeps ENABLE_SCHEDULER (default false = production shape: no
    scheduler in API replicas; dev may opt into single-process)."""

    def test_api_default_has_no_scheduler(self):
        """The CLASS DEFAULT (an env override is legitimate local-dev
        convenience; the shipped default is the production shape)."""
        from app.core.config import Settings

        assert Settings.model_fields["ENABLE_SCHEDULER"].default is False, (
            "default must be false — an API replica with a live scheduler "
            "is the D3 defect"
        )

    def test_worker_module_has_entrypoint(self):
        from app.services import worker

        assert callable(worker.main)
        assert callable(worker.claim_hunt)
        assert callable(worker.release_hunt)

    def test_run_scheduled_hunt_releases_on_no_users(self, db):
        """The claim is ALWAYS released — even the nothing-to-do path."""
        from unittest.mock import patch

        from app.services import worker

        with patch.object(worker, "claim_hunt", return_value=True), \
             patch.object(worker.SessionLocal, "__call__", side_effect=[db, db]):
            summary = worker.run_scheduled_hunt()
        assert summary["status"] == "ran"
        # claim released: next claim succeeds
        assert worker.claim_hunt(db) is True


class TestClaimInsertRace:
    """RC1 on the claim tests stayed green when the PK-collision guard
    was removed — those tests never race. This one forces the collision
    deterministically at the CURRENT race point: with the atomic UPDATE
    claim, a zero-rowcount UPDATE falls through to INSERT — inject the
    competing row between the UPDATE and the INSERT."""

    def test_pk_collision_loses_cleanly(self, db):
        import datetime

        from app.core.timeutil import utc_now
        from app.models import SystemLock
        from app.services import worker

        original_execute = db.execute
        state = {"injected": False}

        def sneaky_execute(*a, **kw):
            result = original_execute(*a, **kw)
            if (not state["injected"] and a
                    and "system_locks" in str(a[0])):
                state["injected"] = True
                db.add(SystemLock(name="hunt", locked_until=utc_now()
                                  + datetime.timedelta(minutes=45)))
                db.flush()  # the other process's row lands mid-window
            return result

        db.execute = sneaky_execute
        try:
            won = worker.claim_hunt(db)
        finally:
            db.execute = original_execute
        assert won is False, (
            "the losing inserter must return False, not raise — a PK "
            "collision is another process winning the claim"
        )

    

class TestClaimAtomicUpdatePath:
    """Reviewer round 2: the UPDATE path was SELECT->check->UPDATE->COMMIT
    with no predicate — my 8-thread probe showed 1/8 only because the
    table was FRESH (INSERT path, PK picks a winner). Seeded — steady
    state, cycle two onward — 8 threads ALL won. The claim must be one
    conditional UPDATE whose rowcount is the verdict."""

    def test_seeded_concurrent_race_yields_one_winner(self, db):
        import threading

        from app.services.worker import claim_hunt, release_hunt

        # SEED: row exists and is released — the steady state
        assert claim_hunt(db) is True
        release_hunt(db)

        barrier = threading.Barrier(8)
        wins = []

        def race():
            barrier.wait()
            s = db.__class__.__mro__ and None  # noqa: F841 — placeholder
            from app.core.database import SessionLocal
            s = SessionLocal()
            try:
                if claim_hunt(s):
                    wins.append(1)
            finally:
                s.close()

        ts = [threading.Thread(target=race) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(wins) == 1, (
            f"{len(wins)}/8 threads won the SAME released claim — the "
            "UPDATE path does not exclude; the claim must be a single "
            "conditional UPDATE"
        )

    def test_claim_released_on_enumeration_failure(self, db, monkeypatch):
        """Reviewer: an exception between claim and release leaks the
        claim — a 45-minute silent outage from a transient DB error."""
        from app.services import worker

        monkeypatch.setattr(worker, "claim_hunt", lambda db_: True)
        # user enumeration explodes (transient DB error shape)
        import sqlalchemy as sa

        def boom(*a, **kw):
            raise sa.OperationalError("stmt", {}, RuntimeError("transient"))
        monkeypatch.setattr(
            "app.services.pipeline.run_pipeline", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
        # make Profile query itself raise: patch SessionLocal used inside
        class _BoomSession:
            def __init__(self, *a, **kw):
                raise RuntimeError("transient DB error")
        monkeypatch.setattr(worker, "SessionLocal", _BoomSession)
        try:
            worker.run_scheduled_hunt()
        except Exception:
            pass  # the failure itself is expected to propagate or log
        # the claim must NOT be leaked: a fresh session can claim
        from app.core.database import SessionLocal
        s = SessionLocal()
        try:
            import datetime

            from app.core.timeutil import utc_now
            from app.models import SystemLock
            s.add(SystemLock(name="hunt", locked_until=utc_now()
                             + datetime.timedelta(minutes=45)))
            s.commit()
        except Exception:
            raise AssertionError(
                "run_scheduled_hunt leaked the claim — enumeration "
                "failure must release (try/finally)"
            )
        finally:
            s.close()


class TestMigrationAdvisoryLock:
    """WO-07: on a Render blueprint deploy the web service and the hunt
    cron boot near-simultaneously against the same Postgres — and
    alembic's `upgrade head` takes no lock of its own (two concurrent
    runs both apply a pending migration; one crashes on duplicate DDL
    and fails its deploy). init_db must serialize boots with a Postgres
    advisory lock: lock BEFORE upgrade, unlock AFTER."""

    def test_postgres_upgrade_wrapped_in_advisory_lock(self, monkeypatch):
        from unittest.mock import MagicMock

        import app.core.database as dbmod
        from alembic import command

        calls = []

        lock_conn = MagicMock()
        lock_conn.execute.side_effect = (
            lambda sql, *a, **k: calls.append(("conn", str(sql)))
        )
        lock_ctx = MagicMock()
        lock_ctx.__enter__.return_value = lock_conn

        fake_engine = MagicMock()
        fake_engine.connect.return_value = lock_ctx

        def fake_upgrade(cfg, rev):
            calls.append(("upgrade", rev))

        monkeypatch.setattr(dbmod, "DATABASE_URL",
                            "postgresql+psycopg://u:p@host:5432/db")
        monkeypatch.setattr(dbmod, "engine", fake_engine)
        monkeypatch.setattr(command, "upgrade", fake_upgrade)

        dbmod.init_db()

        assert len(calls) == 4, f"expected timeout/lock/upgrade/unlock, got {calls}"
        assert "lock_timeout" in calls[0][1], calls[0]
        assert "pg_advisory_lock" in calls[1][1], calls[1]
        assert calls[2] == ("upgrade", "head"), calls[2]
        assert "pg_advisory_unlock" in calls[3][1], calls[3]

    def test_unlock_runs_even_when_upgrade_fails(self, monkeypatch):
        """A crashed migration must not leak the lock (it would block the
        retrying deploy for the session's lifetime)."""
        from unittest.mock import MagicMock

        import app.core.database as dbmod
        from alembic import command

        calls = []

        lock_conn = MagicMock()
        lock_conn.execute.side_effect = (
            lambda sql, *a, **k: calls.append(("conn", str(sql)))
        )
        lock_ctx = MagicMock()
        lock_ctx.__enter__.return_value = lock_conn

        fake_engine = MagicMock()
        fake_engine.connect.return_value = lock_ctx

        def boom(cfg, rev):
            calls.append(("upgrade", rev))
            raise RuntimeError("migration DDL failed")

        monkeypatch.setattr(dbmod, "DATABASE_URL",
                            "postgresql+psycopg://u:p@host:5432/db")
        monkeypatch.setattr(dbmod, "engine", fake_engine)
        monkeypatch.setattr(command, "upgrade", boom)

        import pytest
        with pytest.raises(RuntimeError):
            dbmod.init_db()

        assert any("pg_advisory_unlock" in c[1] for c in calls), (
            f"unlock missing after failure: {calls}"
        )


class TestWorkerOnceMode:
    """WO-07: the Render cron job runs ONE claim-hunt-release cycle and
    exits (metered per-second billing — a scheduler loop would bill 12h
    runs and hit Render's cron cap)."""

    def test_once_runs_single_cycle_without_scheduler(self, monkeypatch):
        from app.services import worker

        ran = []
        monkeypatch.setattr(worker, "run_scheduled_hunt",
                            lambda: ran.append(1) or {"status": "ran"})

        def no_scheduler(*a, **k):
            raise AssertionError("--once must never start the scheduler loop")

        import apscheduler.schedulers.blocking as apsb
        monkeypatch.setattr(apsb, "BlockingScheduler", no_scheduler)

        rc = worker.main(["--once"])

        assert rc == 0
        assert ran == [1]

    def test_default_mode_still_schedules(self, monkeypatch):
        from unittest.mock import MagicMock

        from app.core.config import settings
        from app.services import worker

        sched = MagicMock()
        monkeypatch.setattr(
            "apscheduler.schedulers.blocking.BlockingScheduler",
            lambda: sched,
        )
        rc = worker.main([])

        assert rc == 0
        sched.add_job.assert_called_once()
        kwargs = sched.add_job.call_args.kwargs
        assert kwargs["id"] == "jobfinder_hunt"
        assert kwargs["minutes"] == settings.SCRAPE_INTERVAL_MINUTES
        sched.start.assert_called_once()


class TestWorkerProductionPostgresGuard:
    """WO-07 live incident: the recreated frankfurt cron ran 'successfully'
    in 13s against container-local SQLite. The guard now lives in
    Settings._production_guards (see TestProductionPostgresGuard — keyed on
    DEBUG=false, run at import in BOTH processes). r5 removed this
    worker-local ENVIRONMENT-keyed version: a second, independently-toggled
    switch for the same property is a gap, not defense in depth. These
    tests pin that the worker itself stays switch-free: --once with sqlite
    runs (dev shape) because Settings already refused the bad config."""

    def test_once_runs_on_sqlite_in_dev(self, monkeypatch):
        from app.services import worker

        called = []
        monkeypatch.setattr(worker, "run_scheduled_hunt",
                            lambda: called.append(1) or {"status": "ran"})
        monkeypatch.setattr(worker, "init_db", lambda: None)

        assert worker.main(["--once"]) == 0
        assert called == [1]


class TestCORSProductionGuard:
    """Review r4 (WO-07 round): an EMPTY CORS_ORIGINS at blueprint-prompt
    time deploys successfully and serves an app no origin can call —
    "".split(",") == [""] allows nothing, and the wildcard guard never
    fires. Production must refuse empty or malformed origins."""

    def _settings_kwargs(self):
        return {"DEBUG": False,
                "AUTH_SECRET": "x" * 48,
                "DATABASE_URL": "postgresql+psycopg://u:p@h/db",
                "CORS_ORIGINS": ""}

    def test_empty_cors_rejected_in_production(self):
        import pytest

        from app.core.config import Settings
        with pytest.raises(ValueError):
            Settings(**self._settings_kwargs())

    def test_malformed_origin_rejected_in_production(self):
        import pytest

        from app.core.config import Settings
        with pytest.raises(ValueError):
            Settings(**{**self._settings_kwargs(),
                        "CORS_ORIGINS": "https://good.example,jobfinderos.pages.dev"})

    def test_valid_origins_pass(self):
        from app.core.config import Settings
        s = Settings(**{**self._settings_kwargs(),
                        "CORS_ORIGINS": "https://jobfinderos.pages.dev,http://localhost:3000"})
        assert s.get_cors_origins() == ["https://jobfinderos.pages.dev",
                                        "http://localhost:3000"]


class TestProductionPostgresGuard:
    """Review r5: the cron's empty-DATABASE_URL incident, API variant —
    WORSE. Boot creates ephemeral SQLite, /health answers SELECT 1 with
    200, Render marks the deploy healthy, users sign up into a file that
    vanishes on the next deploy. The guard belongs in _production_guards
    (both processes run it at import, before anything binds a port), not
    on the worker where the failure was merely cheapest."""

    def test_sqlite_rejected_when_debug_false(self):
        import pytest

        from app.core.config import Settings
        with pytest.raises(ValueError):
            Settings(DEBUG=False, AUTH_SECRET="x" * 48,
                     CORS_ORIGINS="https://jobfinderos.pages.dev",
                     DATABASE_URL="sqlite:///./jobfinderos.db")

    def test_default_sqlite_rejected_when_debug_false(self, monkeypatch):
        """The dangerous default: DATABASE_URL left UNSET resolves to the
        sqlite default — the exact recreation-incident shape. Hermetic:
        the env var AND the .env file must both be neutralized (pydantic
        reads .env too, and conftest exports DATABASE_URL on the
        postgres CI job)."""
        import pytest

        from app.core.config import Settings
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(ValueError):
            Settings(_env_file=None, DEBUG=False, AUTH_SECRET="x" * 48,
                     CORS_ORIGINS="https://jobfinderos.pages.dev")

    def test_postgres_passes_when_debug_false(self):
        from app.core.config import Settings
        s = Settings(DEBUG=False, AUTH_SECRET="x" * 48,
                     CORS_ORIGINS="https://jobfinderos.pages.dev",
                     DATABASE_URL="postgresql+psycopg://u:p@h/db")
        assert s.DATABASE_URL.startswith("postgresql")

    def test_sqlite_still_fine_in_dev(self):
        from app.core.config import Settings
        s = Settings(DEBUG=True, DATABASE_URL="sqlite:///./jobfinderos.db")
        assert s.DATABASE_URL.startswith("sqlite")


class TestProductionStorageGuard:
    """OPS-7: the runbook's sync:false recreation incident, storage
    variant. The blueprint syncs STORAGE_BACKEND=supabase as a LITERAL
    (survives service recreation) while SUPABASE_* are prompted secrets
    (they do NOT) — boot went green and every CV upload 500'd at runtime,
    the first time SupabaseStorage needed the key. The guard keys on the
    SELECTED backend, not on production itself: local storage stays
    legitimate (storage.py: dev and single-user deploys on a real disk),
    which is why the DEBUG=false tests above (default local backend)
    keep passing untouched."""

    def _kwargs(self):
        return {"DEBUG": False,
                "AUTH_SECRET": "x" * 48,
                "DATABASE_URL": "postgresql+psycopg://u:p@h/db",
                "CORS_ORIGINS": "https://jobfinderos.pages.dev"}

    def test_supabase_missing_secrets_rejected_in_production(self, monkeypatch):
        """The recreation-incident shape exactly: backend still supabase
        (synced literal), both secrets gone. Hermetic — the developer's
        backend/.env carries real SUPABASE_* values (runbook Step 1) that
        pydantic would otherwise pick up and make this PASS wrongly."""
        import pytest

        from app.core.config import Settings
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        with pytest.raises(ValueError):
            Settings(_env_file=None, **self._kwargs(), STORAGE_BACKEND="supabase")

    def test_supabase_half_configured_rejected_in_production(self, monkeypatch):
        import pytest

        from app.core.config import Settings
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        with pytest.raises(ValueError):
            Settings(_env_file=None, **self._kwargs(), STORAGE_BACKEND="supabase",
                     SUPABASE_URL="https://example.supabase.co")

    def test_supabase_fully_configured_passes(self):
        from app.core.config import Settings
        s = Settings(_env_file=None, **self._kwargs(), STORAGE_BACKEND="supabase",
                     SUPABASE_URL="https://example.supabase.co",
                     SUPABASE_SERVICE_KEY="service-key")
        assert s.STORAGE_BACKEND == "supabase"

    def test_local_backend_needs_no_supabase_in_production(self):
        """Local is a legitimate production shape (persistent-disk
        single-user deploy) — the guard must key on the backend choice,
        not forbid local outright."""
        from app.core.config import Settings
        s = Settings(_env_file=None, **self._kwargs(), STORAGE_BACKEND="local")
        assert s.STORAGE_BACKEND == "local"

    def test_supabase_missing_secrets_fine_in_dev(self):
        from app.core.config import Settings
        s = Settings(DEBUG=True, STORAGE_BACKEND="supabase")
        assert s.STORAGE_BACKEND == "supabase"


class TestEmailApplyErrorsAreEnvironmentNeutral:
    """P0-6: the unconfigured-email-apply errors told users to edit
    backend/.env — a file that does not exist in the Render container,
    i.e. a dead end in exactly the deployment where the error fires.
    The strings must name no path. Both senders check config BEFORE
    importing resend, so the early-return branch is unit-testable; the
    send itself needs a live Resend key and stays untested."""

    def test_pdf_sender_error_names_no_env_file(self, monkeypatch):
        from types import SimpleNamespace

        from app.core.config import settings
        from app.services import draft_service
        monkeypatch.setattr(settings, "RESEND_API_KEY", "")
        monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "")

        application = SimpleNamespace(status=None, error=None)
        draft_service._send_with_pdfs(None, application, None, None, None)
        assert application.status == "failed"
        assert ".env" not in application.error
        assert "deployment" in application.error

    def test_plain_email_sender_error_names_no_env_file(self, monkeypatch):
        from types import SimpleNamespace

        from app.core.config import settings
        from app.services import apply_service
        monkeypatch.setattr(settings, "RESEND_API_KEY", "")
        monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "")

        application = SimpleNamespace(status=None, error=None)
        apply_service._send_email_application(None, application, None, None)
        assert application.status == "failed"
        assert ".env" not in application.error
        assert "deployment" in application.error


class TestMunicipalLocationFilter:
    """Product decision (user, post-first-hunt): picking Malmö means Malmö.
    The gate becomes STRICT multi-municipality; region-wide only when the
    user chose NO municipalities (explicit whole-region). Red-first: the
    old gate admitted anything in the user's REGION ('Lund, Skåne län'
    passed for a Malmö user)."""

    def _job(self, location, remote=False):
        from types import SimpleNamespace
        return SimpleNamespace(location=location, remote=remote)

    def test_region_no_longer_passes_when_municipalities_set(self):
        from app.services.pipeline import passes_location_filter
        ctx = {"country": "SE", "region": "Skåne län",
               "municipalities": ["Malmö"], "remote_only": False,
               "include_remote": False}
        assert passes_location_filter(self._job("Lund, Skåne län"), ctx) is False

    def test_multi_municipality_membership(self):
        from app.services.pipeline import passes_location_filter
        ctx = {"country": "SE", "region": "Skåne län",
               "municipalities": ["Malmö", "Lund"], "remote_only": False,
               "include_remote": False}
        assert passes_location_filter(self._job("Lund, Skåne län"), ctx) is True
        assert passes_location_filter(self._job("Malmö, Skåne län"), ctx) is True
        assert passes_location_filter(self._job("Ängelholm, Skåne län"), ctx) is False

    def test_legacy_single_municipality_is_strict(self):
        """Tony's current profile: municipality='Malmö', no list — must
        behave as ['Malmö'] (strict), not region-wide."""
        from app.services.pipeline import passes_location_filter
        ctx = {"country": "SE", "region": "Skåne län", "municipality": "Malmö",
               "remote_only": False, "include_remote": False}
        assert passes_location_filter(self._job("Malmö, Skåne län"), ctx) is True
        assert passes_location_filter(self._job("Lund, Skåne län"), ctx) is False

    def test_region_wide_only_when_no_municipality_chosen(self):
        from app.services.pipeline import passes_location_filter
        ctx = {"country": "SE", "region": "Skåne län",
               "remote_only": False, "include_remote": False}
        assert passes_location_filter(self._job("Ängelholm, Skåne län"), ctx) is True

    def test_remote_rules_unchanged(self):
        from app.services.pipeline import passes_location_filter
        ctx = {"country": "SE", "municipalities": ["Malmö"],
               "remote_only": False, "include_remote": True}
        assert passes_location_filter(self._job("Remote — worldwide", remote=True), ctx) is True
        assert passes_location_filter(self._job("Remote — worldwide", remote=False), ctx) is False


class TestJobtechPlaceFilter:
    """The official API's `municipality` param takes taxonomy CODES — when
    the user chose municipalities, the scraper fetches ONLY those kommuner
    instead of all of Sweden; unresolved names fall back to the local gate."""

    def test_municipality_codes_sent_when_chosen(self, monkeypatch):
        import app.services.scrapers.jobtech as jt

        monkeypatch.setattr(jt, "_MUNICIPALITY_CODES",
                            {"malmö": "O5cp", "lund": "LuNd"})

        captured = []

        def fake_get(url, params=None, **kw):
            captured.append(params)
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": staticmethod(lambda s=None: {"hits": []}),
            })()

        monkeypatch.setattr(jt.httpx, "get", fake_get)
        jt.JobtechScraper().fetch({"queries": ["dev"],
                                   "municipalities": ["Malmö", "Lund"]})
        sent = captured[0]
        assert ("municipality", "O5cp") in sent and ("municipality", "LuNd") in sent

    def test_no_place_params_without_municipalities(self, monkeypatch):
        import app.services.scrapers.jobtech as jt

        captured = []

        def fake_get(url, params=None, **kw):
            captured.append(params)
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": staticmethod(lambda s=None: {"hits": []}),
            })()

        monkeypatch.setattr(jt.httpx, "get", fake_get)
        jt.JobtechScraper().fetch({"queries": ["dev"]})
        assert all("municipality" not in dict(p) for p in captured)


class TestFuzzyDuplicateGate:
    """The Pågen incident: the SAME job as an agency ad ('Integration
    Developer till Pågen' via Cabeza rekrytering) AND a direct ad
    ('Integration Developer' at PÅGEN AKTIEBOLAG). The exact dedupe key
    differs in every component — title, company — so only a fuzzy gate
    with an EMPLOYER LINK catches it without collapsing generic titles."""

    def test_pagen_agency_vs_direct_is_duplicate(self):
        from app.core.dedupe import likely_same_job
        assert likely_same_job(
            title_a="Integration Developer till Pågen",
            company_a="Cabeza rekrytering och konsulting AB",
            location_a="Malmö, Skåne län",
            title_b="Integration Developer",
            company_b="PÅGEN AKTIEBOLAG",
            location_b="Malmö, Skåne län",
        ) is True

    def test_generic_titles_unrelated_companies_not_duplicate(self):
        from app.core.dedupe import likely_same_job
        assert likely_same_job(
            title_a="Software Developer",
            company_a="Knowit Aktiebolag",
            location_a="Malmö, Skåne län",
            title_b="Software Developer",
            company_b="Edument AB",
            location_b="Malmö, Skåne län",
        ) is False

    def test_seniority_variant_same_company_not_duplicate(self):
        from app.core.dedupe import likely_same_job
        assert likely_same_job(
            title_a="Integration Developer",
            company_a="PÅGEN AKTIEBOLAG",
            location_a="Malmö, Skåne län",
            title_b="Senior Integration Developer",
            company_b="PÅGEN AKTIEBOLAG",
            location_b="Malmö, Skåne län",
        ) is False

    def test_identical_title_and_company_is_duplicate(self):
        from app.core.dedupe import likely_same_job
        assert likely_same_job(
            title_a="QA Engineer",
            company_a="Axis Communications",
            location_a="Lund, Skåne län",
            title_b="QA Engineer",
            company_b="Axis Communications AB",
            location_b="Lund, Skåne län",
        ) is True

    def test_different_cities_not_duplicate(self):
        from app.core.dedupe import likely_same_job
        assert likely_same_job(
            title_a="Integration Developer till Pågen",
            company_a="Cabeza rekrytering AB",
            location_a="Malmö, Skåne län",
            title_b="Integration Developer",
            company_b="PÅGEN AKTIEBOLAG",
            location_b="Göteborg, Västra Götalands län",
        ) is False


class TestFuzzyDedupeWiring:
    """The gate wired into the matcher: agency re-posts collapse against
    both this batch and the user's existing undecided matches — and a
    stored AGENCY copy is dismissed in favor of a NEWER direct ad."""

    U1 = None

    def _job(self, db, title, company, location="Malmö, Skåne län"):
        import uuid as _uuid

        from app.models import JobPosting
        j = JobPosting(
            source="jobtech", source_id=_uuid.uuid4().hex[:10], title=title,
            company=company, location=location,
            url=f"https://x/{_uuid.uuid4().hex[:8]}", status="new",
        )
        db.add(j)
        db.commit()
        return j

    def test_batch_pair_collapses_agency_copy(self, db):
        from app.services.matcher_service import _dismiss_fuzzy_duplicates
        owner = _profile(db).user_id  # real users row (PG enforces the FK)
        direct = self._job(db, "Integration Developer", "PÅGEN AKTIEBOLAG")
        agency = self._job(db, "Integration Developer till Pågen",
                           "Cabeza rekrytering och konsulting AB")
        dropped = _dismiss_fuzzy_duplicates(db, owner, [direct, agency], "test")
        assert [j.id for j in dropped] == [agency.id]

    def test_stored_agency_copy_flipped_for_new_direct_ad(self, db):
        from app.core.timeutil import utc_now
        from app.models import MatchResult
        from app.services.matcher_service import _dismiss_fuzzy_duplicates
        owner = _profile(db).user_id  # real users row (PG enforces the FK)
        stored_job = self._job(db, "Integration Developer till Pågen",
                               "Cabeza rekrytering och konsulting AB")
        m = MatchResult(user_id=owner, job_id=stored_job.id, score=53,
                        tier="good_match", recommendation="apply",
                        reasoning="r", decision=None)
        m.created_at = utc_now()  # production rows are tz-aware (timeutil)
        db.add(m)
        db.commit()
        fresh_direct = self._job(db, "Integration Developer", "PÅGEN AKTIEBOLAG")
        dropped = _dismiss_fuzzy_duplicates(db, m.user_id, [fresh_direct], "test")
        assert dropped == []  # the direct ad is KEPT
        db.refresh(m)
        assert m.dismissed_reason == "duplicate"  # stored agency copy flipped

    def test_unrelated_generic_titles_pass_through(self, db):
        from app.services.matcher_service import _dismiss_fuzzy_duplicates
        owner = _profile(db).user_id
        a = self._job(db, "Software Developer", "Knowit Aktiebolag")
        b = self._job(db, "Software Developer", "Edument AB")
        assert _dismiss_fuzzy_duplicates(db, owner, [a, b], "test") == []


class TestStarvationFix:
    """The evaluation cap shipped as a raw SQL LIMIT on the candidate
    query — plausible ads starved behind junk until the stale sweep
    dismissed them unevaluated (the dream-job starvation bug). Selection
    is newest-first (continuous recruiting: first applicant wins) through
    a window, and the cap counts AI evaluations AFTER the free gates —
    starvation safety comes from throughput, not ordering."""

    def _fake_service(self, monkeypatch, score=60):
        from app.services import matcher_service
        from app.services.ai_service import AIService

        calls = {"n": 0}

        def fake_match(**kwargs):
            calls["n"] += 1
            return {
                "score": score, "tier": AIService._tier_for_score(score),
                "reasoning": "r", "matched_skills": [], "missing_skills": [],
                "transferable_skills": [], "recommendation": "apply",
                "cover_note": "c", "confidence": "medium",
            }

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.match_job = fake_match
        monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)
        return calls

    def test_freshest_job_gets_the_slot_under_a_tight_cap(self, db, monkeypatch):
        """Newest-first (user decision, 2026-08-30): continuous recruiting
        rewards the first strong applicant, so the fresh ad outranks the
        old one for the evaluation slot. The old ad is queued, not lost."""
        from datetime import timedelta

        from app.core.timeutil import utc_now
        from app.models import MatchResult
        from app.services import matcher_service

        profile = _profile(db)
        old = _job_row(db, status="new")
        old.description = "An old but plausible description worth assessing."
        old.scraped_at = utc_now() - timedelta(days=29)
        fresh = _job_row(db, status="new")
        fresh.title = "Backend developer"
        fresh.company = "Globex"
        fresh.description = "A fresh description worth assessing."
        db.commit()

        calls = self._fake_service(monkeypatch)
        matcher_service.run_matching(
            db, limit=1, profile=profile, user_id=profile.user_id
        )

        assert calls["n"] == 3, "one keeper job = triage + 2 keeper samples"
        assert db.query(MatchResult).filter(MatchResult.job_id == fresh.id).count() == 1, (
            "the fresh ad must be evaluated first — first applicant wins"
        )
        assert db.query(MatchResult).filter(MatchResult.job_id == old.id).count() == 0, (
            "cap=1 queues the old ad for the next run — delayed, never lost"
        )

    def test_cheap_gates_dont_burn_evaluation_slots(self, db, monkeypatch):
        from datetime import timedelta

        from app.core.timeutil import utc_now
        from app.models import MatchResult
        from app.services import matcher_service

        profile = _profile(db)
        profile.exclude_keywords = '["ninja"]'
        db.commit()

        excluded = _job_row(db, status="new")
        excluded.title = "Ninja rockstar developer"
        excluded.description = "Dropped by the keyword gate, costing no AI call."
        excluded.scraped_at = utc_now() - timedelta(days=29)
        plausible = _job_row(db, status="new")
        plausible.title = "Python developer"
        plausible.description = "A real description long enough to be assessed."
        plausible.scraped_at = utc_now() - timedelta(days=28)
        db.commit()

        self._fake_service(monkeypatch)
        matcher_service.run_matching(
            db, limit=1, profile=profile, user_id=profile.user_id
        )

        ex = db.query(MatchResult).filter(MatchResult.job_id == excluded.id).one()
        assert ex.dismissed_reason == "excluded_keyword", "keyword gate fired, no AI"
        assert db.query(MatchResult).filter(
            MatchResult.job_id == plausible.id, MatchResult.dismissed_reason.is_(None)
        ).count() == 1, "an excluded job must not consume the plausible job's slot"
