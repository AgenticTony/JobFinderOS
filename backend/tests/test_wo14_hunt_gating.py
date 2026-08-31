"""WO-14 — hunt cadence + trial gating.

Deliverables under test:

- D1: a repeat Hunt within the scrape cooldown performs NO scrape (the
  button stays, board quota is saved, matching still runs). Onboarding
  backfill bypasses the cooldown — the first hunt must read full history
  for its NEW scope keys or the watermark backfill never fires.
- D2: the first day's scoring allowance is LARGER than later days —
  day 1 must prove the product, then settle into cadence.
- D3: the trial cap is enforced on AI EVALUATIONS, not rendered cards,
  and lives in run_matching itself so BOTH manual and scheduled hunts
  inherit it (the 7-day × 10/day economics count cron runs too). A
  capped user gets a clear message, not a silent empty queue.
- Gap: run_matching clamps its own limit structurally — the next caller
  inherits the bound (the Layer-0 principle applied to spend) — and the
  two route ceilings agree at MAX_JOBS_PER_MATCH_RUN.
"""


import pytest  # noqa: E402

from app.core.config import settings  # noqa: E402
from tests.test_scope_gates import (  # noqa: E402
    _fake_ai,
    _job_row,
    _onboarded_user,
)


@pytest.fixture()
def db():
    """Per-file session fixture (same shape as test_scope_gates)."""
    from app.core.database import SessionLocal, engine
    from app.core.orm import Base
    from app.models import (
        AIUsage,
        Application,
        ApplicationDraft,
        JobPosting,
        MatchResult,
        Profile,
        ScrapeRun,
        ScrapeWatermark,
        SystemLock,
        User,
    )
    from tests.conftest import stamp_alembic_head

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    stamp_alembic_head()
    session = SessionLocal()
    for model in (Application, ApplicationDraft, MatchResult, Profile,
                  JobPosting, AIUsage, ScrapeRun, ScrapeWatermark, SystemLock,
                  User):
        session.query(model).delete()
    session.commit()
    yield session
    session.rollback()
    session.close()
    engine.dispose()


# ------------------------------------------------------------------ D1

class TestRepeatHuntCooldown:
    def _patch_jobtech_fetch(self, monkeypatch):
        from app.services.scrapers.jobtech import JobtechScraper

        calls = {"n": 0}

        def counting_fetch(self, ctx):
            calls["n"] += 1
            return []

        monkeypatch.setattr(JobtechScraper, "fetch", counting_fetch)
        return calls

    def test_second_press_within_cooldown_skips_scrape(self, db, monkeypatch):
        from app.services.pipeline import run_pipeline

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        calls = self._patch_jobtech_fetch(monkeypatch)

        first = run_pipeline(sources=["jobtech"], match=False, user_id=uid)
        assert calls["n"] == 1
        assert first["scrape"][0]["status"] == "completed"

        second = run_pipeline(sources=["jobtech"], match=False, user_id=uid)
        assert calls["n"] == 1, (
            "a repeat press inside the cooldown scraped again — the button "
            "must be a free no-op for board quota"
        )
        entry = second["scrape"][0]
        assert entry["status"] == "skipped_cooldown", (
            f"the skip must be reported, not silent: {entry}"
        )
        assert entry["error"] and "UTC" in entry["error"], (
            f"the notice should say when the last real scrape ran: {entry}"
        )

    def test_backfill_bypasses_cooldown(self, db, monkeypatch):
        """Onboarding's first hunt MUST scrape even inside the cooldown —
        its scope keys are new, so the watermark backfill depends on it."""
        from app.services.pipeline import run_pipeline

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        calls = self._patch_jobtech_fetch(monkeypatch)

        run_pipeline(sources=["jobtech"], match=False, user_id=uid)
        assert calls["n"] == 1

        deep = run_pipeline(sources=["jobtech"], match=False, backfill=True,
                            user_id=uid)
        assert calls["n"] == 2, "backfill was blocked by the cooldown"
        assert deep["scrape"][0]["status"] == "completed"


# ------------------------------------------------------------ D2 + D3

class TestDailyScoringCap:
    def _seed_backlog(self, db, n, *, location="Malmö, Sweden"):
        for i in range(n):
            _job_row(db, remote=0, location=location,
                     title=f"Cap Dev {i}",
                     description="Python role with a real description.")

    def test_day1_allowance_is_larger_and_binds(self, db, monkeypatch):
        """Fresh user, 40 in-scope candidates: day 1 scores the boosted
        allowance and STOPS — the backlog drips over days, freshest first
        (the candidate query is newest-first)."""
        from app.models import Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        self._seed_backlog(db, 40)
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid,
        )

        day1 = settings.TRIAL_DAY1_SCORE_CAP
        assert day1 > settings.TRIAL_DAILY_SCORE_CAP, (
            "day 1 must carry the 2–3× boost or the first impression starves"
        )
        assert len(ai["jobs"]) == day1, (
            f"day-1 run scored {len(ai['jobs'])}, expected exactly {day1}"
        )
        assert summary["status"] == "daily_cap_reached", summary
        assert "limit" in summary["error"].lower(), (
            f"capped user needs a clear message, got: {summary.get('error')!r}"
        )

    def test_day2_user_capped_at_standard_cap(self, db, monkeypatch):
        """A user past day 1 with 5 scored today gets the remaining 5 of
        the standard allowance, then stops."""
        from app.core.timeutil import utc_now
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        self._seed_backlog(db, 30)
        # age the user past day 1: an AI-scored row 2 days ago
        old_job = _job_row(db, location="Malmö, Sweden", title="Old Dev")
        row = MatchResult(user_id=uid, job_id=old_job.id, score=80,
                          tier="good_match", recommendation="apply",
                          model_used="test")
        db.add(row)
        db.commit()
        db.execute(
            MatchResult.__table__.update()
            .where(MatchResult.id == row.id)
            .values(created_at=utc_now().replace(hour=0, minute=0, second=0) -
                    __import__("datetime").timedelta(days=2))
        )
        db.commit()
        # 5 already scored today
        for i in range(5):
            j = _job_row(db, location="Malmö, Sweden", title=f"Today Dev {i}")
            db.add(MatchResult(user_id=uid, job_id=j.id, score=80,
                               tier="good_match", recommendation="apply",
                               model_used="test"))
        db.commit()
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid,
        )

        expected = settings.TRIAL_DAILY_SCORE_CAP - 5
        assert len(ai["jobs"]) == expected, (
            f"day-2 user with 5/{settings.TRIAL_DAILY_SCORE_CAP} spent scored "
            f"{len(ai['jobs'])} more; the allowance must bind at the cap"
        )
        assert summary["status"] == "daily_cap_reached"

    def test_user_at_cap_gets_message_not_silent_empty(self, db, monkeypatch):
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        self._seed_backlog(db, 10)
        old_job = _job_row(db, location="Malmö, Sweden", title="Old Dev")
        db.add(MatchResult(user_id=uid, job_id=old_job.id, score=80,
                           tier="good_match", model_used="test",
                           created_at=__import__("datetime").datetime(2020, 1, 1)))
        for i in range(settings.TRIAL_DAILY_SCORE_CAP):
            j = _job_row(db, location="Malmö, Sweden", title=f"Spent {i}")
            db.add(MatchResult(user_id=uid, job_id=j.id, score=80,
                               tier="good_match", model_used="test"))
        db.commit()
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid,
        )

        assert ai["jobs"] == [], "a capped user must spend zero AI calls"
        assert summary["status"] == "daily_cap_reached"
        assert summary["error"] and "daily" in summary["error"].lower()


# ------------------------------------------------------------ the gap

class TestRunMatchingClamp:
    def test_absurd_limit_is_clamped_structurally(self, db, monkeypatch):
        """Call run_matching DIRECTLY with an absurd limit: the service
        clamps to MAX_JOBS_PER_MATCH_RUN so every future caller inherits
        the bound — route schemas become defence-in-depth, not the only
        defence."""
        from app.models import Profile
        from app.services import matcher_service

        monkeypatch.setattr(settings, "MAX_JOBS_PER_MATCH_RUN", 3)
        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        for i in range(10):
            _job_row(db, location="Malmö, Sweden", title=f"Clamp Dev {i}")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(
            db, limit=99999,
            profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid,
        )

        assert len(ai["jobs"]) <= 3, (
            f"a direct call with limit=99999 scored {len(ai['jobs'])} — the "
            "clamp must live in run_matching, not only in route schemas"
        )
