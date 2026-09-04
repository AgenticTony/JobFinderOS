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

    @pytest.fixture(autouse=True)
    def _trial_caps_enforced(self, monkeypatch):
        """These tests pin the WO-14 cap behaviour itself — run them
        with the beta uncapped override OFF (it is the default)."""
        monkeypatch.setattr(settings, "BETA_UNCAPPED_HUNTS", False)

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

    @pytest.fixture(autouse=True)
    def _trial_caps_enforced(self, monkeypatch):
        """These tests pin the WO-14 cap behaviour itself — run them
        with the beta uncapped override OFF (it is the default)."""
        monkeypatch.setattr(settings, "BETA_UNCAPPED_HUNTS", False)

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


# ------------------------------------------------ review round (2026-08-31)

class TestCapCountsDecidedMatches:

    @pytest.fixture(autouse=True)
    def _trial_caps_enforced(self, monkeypatch):
        """These tests pin the WO-14 cap behaviour itself — run them
        with the beta uncapped override OFF (it is the default)."""
        monkeypatch.setattr(settings, "BETA_UNCAPPED_HUNTS", False)

    """Review finding 1 (critical): set_match_decision writes
    decision='approved'/'rejected' and leaves dismissed_reason NULL —
    a kept match the user APPROVED was AI-scored and must still count.
    The old predicate (decision IS NULL OR below_threshold) refunded
    spend slots for the reviewing action the product encourages."""

    def test_approved_kept_matches_still_count_toward_the_cap(self, db, monkeypatch):
        from datetime import timedelta

        from app.core.timeutil import utc_now
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        # age past day 1: a first-ever row two days ago
        old = _job_row(db, location="Malmö, Sweden", title="Old Dev")
        first = MatchResult(user_id=uid, job_id=old.id, score=80,
                            tier="good_match", model_used="test")
        db.add(first)
        db.flush()
        first.created_at = utc_now() - timedelta(days=2)
        db.commit()
        # 10 AI-scored rows today: 5 dismissed below threshold,
        # 5 KEPT matches the user approved (decision set, reason NULL)
        for i in range(5):
            j = _job_row(db, location="Malmö, Sweden", title=f"Below {i}")
            db.add(MatchResult(user_id=uid, job_id=j.id, score=8,
                               tier="poor_match", model_used="test",
                               decision="rejected",
                               dismissed_reason="below_threshold"))
        for i in range(5):
            j = _job_row(db, location="Malmö, Sweden", title=f"Kept {i}")
            db.add(MatchResult(user_id=uid, job_id=j.id, score=80,
                               tier="good_match", model_used="test",
                               decision="approved"))
        db.commit()
        for i in range(20):
            _job_row(db, location="Malmö, Sweden", title=f"Backlog {i}")
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid,
        )

        assert ai["jobs"] == [], (
            "approved kept matches fell out of the daily count — reviewing a "
            "match must not refund the AI slot it consumed (cap leak)"
        )
        assert summary["status"] == "daily_cap_reached"


class TestDay1BoostExpiresAtUtcMidnight:
    """Review finding 3 (medium): the boost was a rolling 24h from the
    first row while the counter resets at UTC midnight — a 22:00 start
    drew 25 + 25. Day 1 must be the CALENDAR day of the first row."""

    def test_row_from_yesterday_within_24h_gets_standard_cap(self, db):
        from datetime import timedelta

        from app.core.timeutil import utc_now
        from app.models import MatchResult
        from app.services.matcher_service import daily_score_allowance

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        j = _job_row(db, location="Malmö, Sweden", title="Late Start")
        row = MatchResult(user_id=uid, job_id=j.id, score=80,
                          tier="good_match", model_used="test")
        db.add(row)
        db.flush()
        # yesterday 23:00 UTC — inside a rolling 24h, but a DIFFERENT
        # UTC calendar day from now
        now = utc_now()
        row.created_at = (now - timedelta(hours=now.hour + 1)).replace(
            hour=23, minute=0, second=0, microsecond=0)
        db.commit()

        assert daily_score_allowance(db, user_id=uid) == settings.TRIAL_DAILY_SCORE_CAP, (
            "a first row from yesterday (even one under 24h old) must NOT "
            "re-arm the day-1 boost on today's fresh counter — the rolling "
            "window let late-day users draw the 2.5x boost twice"
        )


class TestCooldownIsPerScope:
    """Review finding 2 (high): the cooldown was global per source while
    hunts are per-user scope — A(Stockholm)'s hunt suppressed
    B(Malmö)'s entirely different fetch. Cooldown must key on the same
    fetch identity the watermarks use: (source, scope)."""

    def test_different_scope_hunt_is_not_suppressed(self, db, monkeypatch):
        from app.services.pipeline import run_pipeline
        from app.services.scrapers.jobtech import JobtechScraper

        calls = {"n": 0}

        def counting_fetch(self, ctx):
            calls["n"] += 1
            return []

        monkeypatch.setattr(JobtechScraper, "fetch", counting_fetch)

        a = _onboarded_user(db, country="SE", municipalities='["Stockholm"]',
                             queries='["utvecklare stockholm"]')
        b = _onboarded_user(db, country="SE", municipalities='["Malmö"]',
                             queries='["utvecklare"]')

        run_pipeline(sources=["jobtech"], match=False, user_id=a)
        assert calls["n"] == 1

        second = run_pipeline(sources=["jobtech"], match=False, user_id=b)
        assert calls["n"] == 2, (
            "a different user's different-scope hunt was suppressed by the "
            f"global cooldown (got {second['scrape'][0]['status']}) — B's "
            "Stockholm→Malmö queries were never issued"
        )
        assert second["scrape"][0]["status"] == "completed"


# ------------------------------------------- beta uncapped (2026-09-04)

class TestBetaUncappedHunts:
    """Owner decision 2026-09-04: during beta (BETA_UNCAPPED_HUNTS is the
    default) a hunt drains the WHOLE in-scope backlog in one run — no
    daily allowance, no batch-of-25, no second Hunt press needed."""

    def _seed_backlog(self, db, n, *, location="Malmö, Sweden"):
        for i in range(n):
            _job_row(db, remote=0, location=location,
                     title=f"Beta Dev {i}",
                     description="Python role with a real description.")

    def test_whole_backlog_drains_in_one_run(self, db, monkeypatch):
        from app.models import Profile
        from app.services import matcher_service

        assert settings.BETA_UNCAPPED_HUNTS is True, "uncapped must be the beta default"
        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]')
        self._seed_backlog(db, 40)  # 15 above the old day-1 allowance of 25
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid,
        )

        assert len(ai["jobs"]) == 40, (
            f"beta run scored {len(ai['jobs'])} of 40 — the backlog must "
            "drain in one run (owner decision 2026-09-04)"
        )
        assert summary["status"] != "daily_cap_reached", summary


# --------------------------------------- review round (2026-09-04)

class TestBetaPlumbing:
    """The uncapped plumbing asserted DIRECTLY (repo rule 2: red on
    revert). A fast fake AI never approaches the 420s deadline, so
    behaviour tests alone stay green with the time budget still in
    place — these capture what _run_matching_loop actually receives."""

    def _capture_loop(self, monkeypatch):
        from app.services import matcher_service

        captured = {}
        real = matcher_service._run_matching_loop

        def spy(db, unmatched, profile, user_id, **kwargs):
            captured.update(kwargs)
            return real(db, unmatched, profile, user_id, **kwargs)

        monkeypatch.setattr(matcher_service, "_run_matching_loop", spy)
        return captured

    def _run(self, db, monkeypatch, **kw):
        import uuid as _uuid

        from app.models import Profile
        from app.services import matcher_service

        uid = _onboarded_user(
            db, country="SE", municipalities=f'["Malmö"]',
        )
        for i in range(3):
            _job_row(db, location="Malmö, Sweden", title=f"Plumb Dev {i}")
        _fake_ai(monkeypatch)
        summary = matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid, **kw,
        )
        return summary, uid

    def test_beta_default_forwards_no_deadline_and_window_limit(self, db, monkeypatch):
        captured = self._capture_loop(monkeypatch)
        assert settings.BETA_UNCAPPED_HUNTS is True  # the beta default
        self._run(db, monkeypatch)
        # the time budget must be REMOVED in beta (None -> no deadline)
        assert captured["max_seconds"] is None, captured
        # no explicit limit -> the candidate window is the drain ceiling
        assert captured["limit"] == settings.MATCH_CANDIDATE_WINDOW, captured

    def test_explicit_limit_is_honoured_in_beta(self, db, monkeypatch):
        captured = self._capture_loop(monkeypatch)
        summary, _ = self._run(db, monkeypatch, limit=2)
        assert captured["limit"] == 2, captured

    def test_caps_on_forwards_the_budget_and_clamp(self, db, monkeypatch):
        monkeypatch.setattr(settings, "BETA_UNCAPPED_HUNTS", False)
        captured = self._capture_loop(monkeypatch)
        self._run(db, monkeypatch)  # no explicit limit, no max_seconds
        assert captured["max_seconds"] == 300, captured  # forwarded, not None
        # caps on: the day-1 allowance binds below the 200 clamp (scored 0)
        assert captured["limit"] == settings.TRIAL_DAY1_SCORE_CAP, captured

    def test_heartbeat_fires_inside_the_loop(self, db, monkeypatch):
        """PIPE-18b: a live uncapped run renews the hunt claim from
        inside the evaluation loop — without this, a 40-80 min backfill
        outlives the 45-min claim TTL and gets stolen mid-flight."""
        beats = []
        uid = None

        from app.models import Profile
        from app.services import matcher_service
        import uuid as _uuid

        uid = _onboarded_user(
            db, country="SE", municipalities='["Malmö"]',
        )
        for i in range(matcher_service.HEARTBEAT_EVERY + 2):
            _job_row(db, location="Malmö, Sweden", title=f"Beat Dev {i}")
        _fake_ai(monkeypatch)

        matcher_service.run_matching(
            db, profile=db.query(Profile).filter(Profile.user_id == uid).one(),
            user_id=uid, heartbeat=lambda: beats.append(1),
        )
        assert len(beats) >= 1, (
            f"{matcher_service.HEARTBEAT_EVERY + 2} evaluations must fire at "
            "least one heartbeat — the claim TTL cannot cover an uncapped run"
        )
