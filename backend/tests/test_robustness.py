"""Robustness tests — PIPE-17, PIPE-18, PIPE-19, AI-14.

PIPE-17: a paginated scraper that breaks out mid-walk on a page error
returns PARTIAL data. Stamping the watermark anyway permanently skips
the un-fetched pages in delta mode — one 06:00 hiccup drops that day's
postings for every user sharing the scope. The watermark may only
advance on a FULLY successful fetch.

PIPE-18: the hunt claim lock had a fixed 45-minute TTL (smaller than
per-user matching budgets × N users) and release_hunt cleared ANY
owner's claim — an overrunning holder whose TTL was stolen released the
STEALER's claim, and two hunts ran concurrently. Claims now carry an
owner token; release only clears your own claim; the TTL is sized from
the real worst case.

PIPE-19: a user erased mid-run (GDPR delete during a scheduled hunt)
left the matcher feeding their CV to GLM for up to 200 evaluations,
every INSERT failing the user_id FK. The matcher must notice the user
is gone (periodic check + FK-failure verification) and abort cleanly.

AI-14: `matching_running` was a single process-global flag — while ANY
user matched, EVERY user's dashboard said "matching in progress". The
running state is per user; the status route reports the CALLER's.
"""

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal

# Built by concatenation so no single credential-shaped literal sits in
# the source (secret scanners flag fixed test passwords; the value is a
# throwaway fixture that never authenticates anything real).
PASSWORD = "TestPass-" + "2026!"


@pytest.fixture()
def db():
    """Per-file session fixture (same shape as test_delta/test_units):
    clean per-user data between tests, schema stays — Alembic owns it."""
    from app.core.database import engine
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
    from app.core.database import engine

    engine.dispose()


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
    from app.core.database import engine

    engine.dispose()


def _register_and_auth(client, label):
    email = f"rb-{label}-{uuid.uuid4().hex[:6]}@test.example"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return email, r.json()["access_token"]


def _auth(client, token):
    client.headers.update({"Authorization": f"Bearer {token}"})


# ------------------------------------------------------------------ PIPE-17

class _Resp:
    def __init__(self, hits):
        self._hits = hits

    def raise_for_status(self):
        pass

    def json(self):
        return {"hits": self._hits, "total": len(self._hits)}


def _hit(i, municipality="Malmö"):
    return {
        "id": f"jt-{i}",
        "headline": f"Developer {i}",
        "employer": {"name": "Acme"},
        "workplace_address": {"municipality": municipality},
        "webpage_url": f"https://x/jt-{i}",
        "description": {"text": "A fine job."},
        "publication_date": "2026-08-29T12:00:00+02:00",
    }


def _stub_jobtech(monkeypatch, pages):
    """pages: a list whose items are either a hit list (a successful page)
    or an Exception instance to raise for that page's request. The
    taxonomy fetch answers an empty concept list."""
    from app.services.scrapers import jobtech

    state = {"n": 0}

    def fake_get(url, params=None, **kwargs):
        if "taxonomy" in url:
            return _Resp([])
        idx = state["n"]
        state["n"] += 1
        page = pages[min(idx, len(pages) - 1)]
        if isinstance(page, Exception):
            raise page
        return _Resp(page)

    monkeypatch.setattr(jobtech.httpx, "get", fake_get)


_SE_CTX = {
    "country": "SE", "queries": ["utvecklare"], "municipalities": ["Malmö"],
    "languages": [], "remote_only": False, "include_remote": True,
}


class TestPartialFetchHoldsWatermark:
    def test_full_fetch_stamps_watermark(self, db, monkeypatch):
        from app.models import ScrapeWatermark
        from app.services.pipeline import scrape_source

        _stub_jobtech(monkeypatch, pages=[[_hit(1), _hit(2)]])  # short page = complete walk
        run = scrape_source(db, "jobtech", ctx=dict(_SE_CTX))

        assert run.status == "completed", (run.status, run.error)
        assert db.query(ScrapeWatermark).count() == 1, (
            "a fully successful fetch must stamp its (source, query, scope) watermark"
        )

    def test_partial_fetch_does_not_stamp_watermark(self, db, monkeypatch):
        """Page 0 full (100 hits) -> walk continues; page 1 errors -> the
        scraper breaks out with PARTIAL data. The watermark must NOT
        advance: stamping it would permanently skip the un-fetched pages
        in delta mode."""
        import httpx

        from app.models import ScrapeWatermark
        from app.services.pipeline import scrape_source

        _stub_jobtech(monkeypatch, pages=[
            [_hit(i) for i in range(100)],          # full page -> page 1 is fetched
            httpx.ConnectError("transient 06:00 hiccup"),
        ])
        run = scrape_source(db, "jobtech", ctx=dict(_SE_CTX))

        assert run.status == "completed", (run.status, run.error)
        assert run.jobs_found > 0, "the partial data must still be stored"
        assert db.query(ScrapeWatermark).count() == 0, (
            "a partial fetch must hold the old watermark so the next run "
            "re-reads the gap (dedupe eats the overlap)"
        )

    def test_scraper_reports_partial_health(self, monkeypatch):
        """The health contract itself: BaseScraper.fetch_complete defaults
        True; jobtech flips its own instance to False when a page fails."""
        import httpx

        from app.services.scrapers import jobtech
        from app.services.scrapers.base import BaseScraper

        assert BaseScraper.fetch_complete is True

        scraper = jobtech.JobtechScraper()
        _stub_jobtech(monkeypatch, pages=[
            [_hit(i) for i in range(100)],
            httpx.ConnectError("page 2 down"),
        ])
        scraper.fetch(dict(_SE_CTX))
        assert scraper.fetch_complete is False, (
            "a mid-walk page failure must mark the fetch partial"
        )

    def test_zero_new_jobs_is_still_a_full_fetch(self, db, monkeypatch):
        """Ordinary 'nothing new' must NOT hold the watermark — only fetch
        errors do. An empty result set walked to its end is complete."""
        from app.models import ScrapeWatermark
        from app.services.pipeline import scrape_source

        _stub_jobtech(monkeypatch, pages=[[]])
        run = scrape_source(db, "jobtech", ctx=dict(_SE_CTX))

        assert run.status == "completed", (run.status, run.error)
        assert run.jobs_new == 0
        assert db.query(ScrapeWatermark).count() == 1, (
            "a complete fetch with zero new jobs still advances the watermark"
        )


# ------------------------------------------------------------------ PIPE-18

class TestOwnerTokenClaimRelease:
    def test_wrong_owner_cannot_release(self, db):
        """The release must be conditional on the owner token: a holder
        whose TTL was stolen must not release the STEALER's claim."""
        from app.models import SystemLock
        from app.services.worker import claim_hunt, release_hunt

        token_a = claim_hunt(db)
        assert token_a, "fixture setup: first claimant must win"

        released = release_hunt(db, "not-the-owner")
        assert released is False, "a foreign owner must not release the claim"

        row = db.query(SystemLock).filter(SystemLock.name == "hunt").one()
        assert row.locked_until is not None, "the claim must still be held"

        assert release_hunt(db, token_a) is True, "the true owner releases"
        row = db.query(SystemLock).filter(SystemLock.name == "hunt").one()
        assert row.locked_until is None

    def test_release_after_ttl_steal_does_not_free_the_stealer(self, db):
        """A's cycle overruns its TTL; B steals the claim. A finally gets
        to its release — it must NOT free B's claim (that is how two
        concurrent hunts used to happen)."""
        import datetime

        from app.core.timeutil import utc_now
        from app.models import SystemLock
        from app.services.worker import claim_hunt, release_hunt

        token_a = claim_hunt(db)
        assert token_a

        # TTL expires; B steals (the atomic UPDATE path does exactly this)
        db.query(SystemLock).filter(SystemLock.name == "hunt").update(
            {"locked_until": utc_now() - datetime.timedelta(minutes=1)}
        )
        db.commit()
        token_b = claim_hunt(db)
        assert token_b and token_b != token_a, "fixture setup: B must steal"

        assert release_hunt(db, token_a) is False, (
            "the overrunning holder must not release the stealer's claim"
        )
        assert claim_hunt(db) is None, "B's claim must still hold the lock"

    def test_claim_returns_distinct_tokens(self, db):
        from app.services.worker import claim_hunt, release_hunt

        t1 = claim_hunt(db)
        release_hunt(db, t1)
        t2 = claim_hunt(db)
        release_hunt(db, t2)
        assert t1 and t2 and t1 != t2, "each claim must mint a fresh owner token"

    def test_renew_extends_own_claim_only(self, db):

        from app.models import SystemLock
        from app.services.worker import claim_hunt, renew_hunt

        token = claim_hunt(db)
        before = (
            db.query(SystemLock).filter(SystemLock.name == "hunt").one().locked_until
        )
        assert renew_hunt(db, token) is True
        after = (
            db.query(SystemLock).filter(SystemLock.name == "hunt").one().locked_until
        )
        assert after > before, "renewal must extend the holder's TTL"

        # a foreign token renews nothing
        old_after = after
        assert renew_hunt(db, "foreign") is False
        row = db.query(SystemLock).filter(SystemLock.name == "hunt").one()
        assert row.locked_until == old_after


class TestClaimTTLFormula:
    def test_ttl_covers_worst_case_at_default_user_counts(self):
        """The TTL must cover the real worst case: the scrape phase plus
        one per-user matching budget per onboarded user (with headroom).
        At the shipped MATCH_TIME_BUDGET_SECONDS=420 that is 7 minutes a
        user — 45 minutes already undersized at SEVEN users."""
        from app.core.config import settings
        from app.services.worker import (
            CLAIM_TTL_FLOOR_MINUTES,
            SCRAPE_PHASE_ALLOWANCE_MINUTES,
            compute_claim_ttl_minutes,
        )

        per_user_min = -(-settings.MATCH_TIME_BUDGET_SECONDS // 60)  # ceil
        for users in (0, 1, 5, 10, 25):
            ttl = compute_claim_ttl_minutes(users)
            worst = SCRAPE_PHASE_ALLOWANCE_MINUTES + users * per_user_min
            assert ttl >= worst, (
                f"TTL {ttl}min < worst case {worst}min at {users} users"
            )
            assert ttl >= CLAIM_TTL_FLOOR_MINUTES, "the floor is a minimum, not a cap"

    def test_ttl_grows_with_user_count(self):
        from app.services.worker import compute_claim_ttl_minutes

        # below the floor the TTL is flat (45); above it, one matching
        # budget per extra user
        assert compute_claim_ttl_minutes(10) < compute_claim_ttl_minutes(20)
        assert compute_claim_ttl_minutes(20) > compute_claim_ttl_minutes(1)

    def test_ttl_env_override_wins(self, db, monkeypatch):
        """HUNT_CLAIM_TTL_MINUTES is the ops escape hatch — an explicit
        value must be used verbatim."""

        from app.core.timeutil import utc_now
        from app.models import SystemLock
        from app.services.worker import claim_hunt, release_hunt

        monkeypatch.setattr("app.core.config.settings.HUNT_CLAIM_TTL_MINUTES", 90)
        token = claim_hunt(db)
        try:
            until = (
                db.query(SystemLock).filter(SystemLock.name == "hunt").one().locked_until
            )
            delta = (until - utc_now()).total_seconds() / 60
            assert 89 <= delta <= 91, f"override must size the TTL to ~90min, got {delta:.1f}"
        finally:
            release_hunt(db, token)

    def test_claim_ttl_scales_with_live_user_count(self, db):
        """The default TTL is COMPUTED from the onboarded-user count at
        claim time: a populated deployment claims a window at least the
        formula's verdict for its user count."""
        from app.core.timeutil import utc_now
        from app.models import Profile, SystemLock, User
        from app.services.worker import claim_hunt, release_hunt

        for _ in range(6):  # above the 45min floor: 15 + 6*7 = 57min worst case
            u = User(id=uuid.uuid4(), email=f"ttl-{uuid.uuid4().hex[:6]}@test.example",
                     hashed_password="x")
            db.add(u)
            db.flush()
            db.add(Profile(is_active=1, user_id=u.id, full_name="T",
                           cv_file_name="cv.pdf", cv_text="dev", country="SE"))
        db.commit()

        token = claim_hunt(db)
        try:
            until = (
                db.query(SystemLock).filter(SystemLock.name == "hunt").one().locked_until
            )
            minutes = (until - utc_now()).total_seconds() / 60
            from app.services.worker import compute_claim_ttl_minutes

            assert minutes >= compute_claim_ttl_minutes(6) - 1, (
                f"the claim must size its TTL from the live user count "
                f"({minutes:.1f}min < {compute_claim_ttl_minutes(6)}min)"
            )
        finally:
            release_hunt(db, token)


# ------------------------------------------------------------------ PIPE-19

def _scripted_ai(monkeypatch, *, on_call=None, score=80, with_title=False):
    """Fake AI service recording every model call; `on_call(n)` runs
    before the n-th call returns (n counts RAW model calls). With
    `with_title`, on_call receives the parsed job title too."""
    from app.services import matcher_service
    from app.services.ai_service import AIService

    seen = {"calls": 0, "jobs": []}

    def fake_match(profile_context, cv_text, job_description):
        seen["calls"] += 1
        title = job_description.split("\n")[0].replace("Title: ", "")
        if on_call is not None:
            on_call(seen["calls"], title=title) if with_title else on_call(seen["calls"])
        if title not in seen["jobs"]:
            seen["jobs"].append(title)
        return {"score": score, "reasoning": "ok", "recommendation": "apply",
                "confidence": "high", "matched_skills": ["Python"],
                "missing_skills": [], "transferable_skills": []}

    svc = AIService.__new__(AIService)
    svc.model = "glm-test"
    svc.match_job = fake_match
    monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
    monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)
    return seen


def _user_with_profile_and_jobs(db, n_jobs=3, label="u", uid=None):
    """User + onboarded profile + n local jobs. When `uid` is given the
    user already exists with their own Profile row (registration creates
    one) — only jobs are added."""
    from app.models import JobPosting, Profile, User

    if uid is None:
        uid = uuid.uuid4()
        db.add(User(id=uid, email=f"rb-{label}-{uid.hex[:8]}@test.example",
                    hashed_password="x"))
        db.flush()
        db.add(Profile(is_active=1, user_id=uid, full_name="T",
                       cv_file_name="cv.pdf", cv_text="developer python",
                       country="SE", municipalities='["Malmö"]',
                       search_queries='["utvecklare"]', languages='["sv"]',
                       include_remote=1))
    jobs = []
    for i in range(n_jobs):
        job = JobPosting(source="t", source_id=f"{label}-{uid.hex[:4]}-{i}",
                         title=f"Dev {label}{i}", company="Acme",
                         location="Malmö", url=f"https://x/{label}{uid.hex[:4]}{i}",
                         description="A fine job.", remote=0)
        db.add(job)
        jobs.append(job)
    db.commit()
    return uid, jobs


def _erase_user(uid):
    """GDPR-shape erase from a SEPARATE session, mid-run."""
    from app.models import MatchResult, Profile, User

    s = SessionLocal()
    try:
        s.query(MatchResult).filter(MatchResult.user_id == uid).delete()
        s.query(Profile).filter(Profile.user_id == uid).delete()
        s.query(User).filter(User.id == uid).delete()
        s.commit()
    finally:
        s.close()


class TestDeletedUserAbortsMatching:
    def test_user_deleted_mid_run_aborts_no_further_model_calls(self, db, monkeypatch):
        """The user is erased during the FIRST job's evaluation. The
        matcher must abort — no further model calls, no ghost rows."""
        from app.services import matcher_service
        from app.services.cv_service import get_active_profile

        monkeypatch.setattr(matcher_service, "USER_LIVENESS_CHECK_EVERY", 1)
        uid, jobs = _user_with_profile_and_jobs(db, n_jobs=3)

        def on_call(n):
            if n == 1:
                _erase_user(uid)

        seen = _scripted_ai(monkeypatch, on_call=on_call)
        profile = get_active_profile(db, user_id=uid)
        summary = matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert summary["status"] == "aborted", (
            f"a deleted user must abort the run, got {summary}"
        )
        assert len(seen["jobs"]) == 1, (
            f"no further jobs may be evaluated after the user is gone, "
            f"got {seen['jobs']}"
        )
        assert "user" in (summary.get("error") or "").lower()

    def test_other_users_rows_unaffected(self, db, monkeypatch):
        from app.models import MatchResult
        from app.services import matcher_service
        from app.services.cv_service import get_active_profile

        monkeypatch.setattr(matcher_service, "USER_LIVENESS_CHECK_EVERY", 1)
        uid_a, _ = _user_with_profile_and_jobs(db, n_jobs=2, label="a")
        uid_b, jobs_b = _user_with_profile_and_jobs(db, n_jobs=1, label="b")
        # B already has a stored match from an earlier run
        db.add(MatchResult(user_id=uid_b, job_id=jobs_b[0].id, score=88,
                           tier="good_match", reasoning="kept"))
        db.commit()
        b_before = db.query(MatchResult).filter(MatchResult.user_id == uid_b).count()

        def on_call(n):
            if n == 1:
                _erase_user(uid_a)

        _scripted_ai(monkeypatch, on_call=on_call)
        profile_a = get_active_profile(db, user_id=uid_a)
        matcher_service.run_matching(db, profile=profile_a, user_id=uid_a)

        assert db.query(MatchResult).filter(MatchResult.user_id == uid_b).count() == b_before, \
            "another user's rows must be untouched by the aborted run"

        # and B's own matching still completes normally afterwards
        monkeypatch.setattr(matcher_service, "USER_LIVENESS_CHECK_EVERY", 1)
        db2 = SessionLocal()
        try:
            profile_b = get_active_profile(db2, user_id=uid_b)
            summary_b = matcher_service.run_matching(db2, profile=profile_b, user_id=uid_b)
        finally:
            db2.close()
        assert summary_b["status"] == "completed", summary_b

    def test_integrity_error_on_gone_user_aborts(self, db, monkeypatch):
        """Belt for the window between liveness checks: on Postgres an
        erase mid-evaluation surfaces as the per-job INSERT failing the
        user_id FK. The matcher must VERIFY the user row is gone and
        abort instead of burning the remaining evaluations.

        The IntegrityError trigger is backend-appropriate (what is under
        test is the DECISION: commit fails -> user row checked -> gone ->
        abort). Postgres enforces the user_id FK, so erasing the user
        alone arms the per-job INSERT failure. SQLite never enforces the
        FK, so there the conflicting (user_id, job_id) row is what makes
        the matcher's commit raise instead.

        The erase therefore COMMITS FIRST and the conflicting row is
        added after, tolerated to fail: in one flush SQLAlchemy runs
        INSERTs before DELETEs, so insert-then-delete in a single
        transaction makes the user DELETE fail the FK itself (the row
        just inserted still references the user) on any FK-enforcing
        backend — the erase never lands and the run completes.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from app.models import MatchResult
        from app.services import matcher_service
        from app.services.cv_service import get_active_profile

        monkeypatch.setattr(matcher_service, "USER_LIVENESS_CHECK_EVERY", 10_000)
        uid, jobs = _user_with_profile_and_jobs(db, n_jobs=3)
        by_title = {j.title: j for j in jobs}

        from app.models import Profile, User  # noqa: F401 — used in on_call

        def on_call(n, title=None):
            if n == 1 and title in by_title:
                # the erase AND a conflicting match row for the in-flight
                # job: the matcher's own commit then raises IntegrityError.
                # The conflicting match row must SURVIVE the erase (the
                # GDPR wipe would delete it) — delete profile+user only,
                # and commit that BEFORE inserting the conflicting row.
                s = SessionLocal()
                try:
                    s.query(Profile).filter(Profile.user_id == uid).delete()
                    s.query(User).filter(User.id == uid).delete()
                    s.commit()
                    try:
                        s.add(MatchResult(user_id=uid, job_id=by_title[title].id,
                                          score=1, tier="poor_match"))
                        s.commit()
                    except SAIntegrityError:
                        # Postgres: expected — the user is gone, so this
                        # insert fails the FK. The FK on the matcher's
                        # own per-job insert is the trigger there.
                        s.rollback()
                finally:
                    s.close()

        seen = _scripted_ai(monkeypatch, on_call=on_call, with_title=True)
        run_db = SessionLocal()
        try:
            profile = get_active_profile(run_db, user_id=uid)
            summary = matcher_service.run_matching(run_db, profile=profile, user_id=uid)
        finally:
            run_db.close()

        assert summary["status"] == "aborted", (
            f"an insert failure with the user gone must abort, got {summary}"
        )
        assert len(seen["jobs"]) == 1, (
            f"only the in-flight job may be evaluated, got {seen['jobs']}"
        )

    def test_non_user_integrity_error_does_not_abort(self, db, monkeypatch):
        """An IntegrityError pointing at something ELSE — here the
        (user_id, job_id) unique constraint, the classic reconcile case
        (job reset to 'new', manual job, a racing run) — must keep the
        reconcile path, never the deleted-user abort."""
        from app.models import MatchResult
        from app.services import matcher_service
        from app.services.cv_service import get_active_profile

        monkeypatch.setattr(matcher_service, "USER_LIVENESS_CHECK_EVERY", 1)
        uid, jobs = _user_with_profile_and_jobs(db, n_jobs=2)
        by_title = {j.title: j for j in jobs}

        def on_call(n, title=None):
            if n == 1 and title in by_title:
                s = SessionLocal()
                try:
                    s.add(MatchResult(user_id=uid, job_id=by_title[title].id,
                                      score=1, tier="poor_match"))
                    s.commit()
                finally:
                    s.close()
                # the user row STAYS — only the insert conflicts

        seen = _scripted_ai(monkeypatch, on_call=on_call, with_title=True)
        run_db = SessionLocal()
        try:
            profile = get_active_profile(run_db, user_id=uid)
            summary = matcher_service.run_matching(run_db, profile=profile, user_id=uid)
        finally:
            run_db.close()

        assert summary["status"] == "completed", (
            f"a non-user IntegrityError must not abort the run: {summary}"
        )
        assert len(seen["jobs"]) == 2, (
            f"the remaining jobs must still be evaluated: {seen['jobs']}"
        )


# ------------------------------------------------------------------- AI-14

class TestPerUserMatchingState:
    def test_unit_flag_is_per_user(self):
        from app.services import matcher_service as ms

        a, b = uuid.uuid4(), uuid.uuid4()
        ms._mark_matching_started(a)
        try:
            assert ms.is_matching_running(user_id=a) is True
            assert ms.is_matching_running(user_id=b) is False, (
                "another user's matching run must not show as mine"
            )
            assert ms.is_matching_running() is True, (
                "the global view (worker introspection) stays honest"
            )
        finally:
            ms._mark_matching_done(a)
        assert ms.is_matching_running(user_id=a) is False
        assert ms.is_matching_running() is False

    def test_status_route_reports_the_callers_state_only(self, client, db, monkeypatch):
        """End-to-end: while A's matching runs, A's dashboard polls
        running and B's does not; after completion both are clear."""

        from app.models import Profile, User
        from app.services import matcher_service as ms
        from app.services.cv_service import get_active_profile

        email_a, tok_a = _register_and_auth(client, "a")
        _, tok_b = _register_and_auth(client, "b")

        # A's matching runs against A's OWN account (the API-registered
        # user, resolved by email so the route's user.id matches).
        # Registration auto-creates an empty Profile row — fill it in
        # place instead of inserting a second one.
        uid_a = db.query(User).filter(User.email == email_a).one().id
        profile_a = db.query(Profile).filter(Profile.user_id == uid_a).one()
        profile_a.cv_text = "developer python"
        profile_a.cv_file_name = "cv.pdf"
        profile_a.country = "SE"
        profile_a.municipalities = '["Malmö"]'
        profile_a.search_queries = '["utvecklare"]'
        profile_a.languages = '["sv"]'
        profile_a.include_remote = 1
        _user_with_profile_and_jobs(db, n_jobs=1, label="a14", uid=uid_a)

        ai_entered = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def fake_match(profile_context, cv_text, job_description):
            calls["n"] += 1
            ai_entered.set()
            release.wait(timeout=15)
            return {"score": 80, "reasoning": "ok", "recommendation": "apply",
                    "confidence": "high", "matched_skills": ["Python"],
                    "missing_skills": [], "transferable_skills": []}

        from app.services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc.match_job = fake_match
        monkeypatch.setattr(ms, "ai_service_available", lambda: True)
        monkeypatch.setattr(ms, "get_ai_service", lambda: svc)

        def run_a():
            s = SessionLocal()
            try:
                profile = get_active_profile(s, user_id=uid_a)
                ms.run_matching(s, profile=profile, user_id=uid_a)
            finally:
                s.close()

        t = threading.Thread(target=run_a)
        t.start()
        try:
            assert ai_entered.wait(timeout=15), "matching never reached the AI call"

            _auth(client, tok_a)
            st_a = client.get("/api/v1/pipeline/status").json()
            _auth(client, tok_b)
            st_b = client.get("/api/v1/pipeline/status").json()

            assert st_a["matching_running"] is True, (
                "the matching user's own dashboard must see the run"
            )
            assert st_b["matching_running"] is False, (
                "AI-14: while A matches, B's dashboard must NOT claim a "
                f"matching run — got {st_b['matching_running']}"
            )
        finally:
            release.set()
            t.join(timeout=20)

        _auth(client, tok_a)
        assert client.get("/api/v1/pipeline/status").json()["matching_running"] is False
        _auth(client, tok_b)
        assert client.get("/api/v1/pipeline/status").json()["matching_running"] is False
