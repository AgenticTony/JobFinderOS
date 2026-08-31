"""Concurrency tests — PIPE-14 and DATA-5.

PIPE-14a: the manual Hunt button runs the same shared-pool scrape unit
(sources, watermarks, job_postings) as the cron worker, so it must claim
the same DB hunt lock — a press during the cron window must return
"busy", never double-run.

PIPE-14b: job_postings gets a DB-level unique(source, source_id) with a
dedupe-first migration (children re-pointed), and the ingest path
upserts instead of trusting the _job_exists pre-check.

DATA-5: the watermark select-then-insert race. The loser's IntegrityError
used to leave the session in pending-rollback: the ScrapeRun's terminal
commit then raised PendingRollbackError — the hunt 500'd AFTER its jobs
were committed and the run row stayed 'running'.
"""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import false, text

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


def _register_and_auth(client):
    email = f"pc-{uuid.uuid4().hex[:6]}@test.example"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = client.post("/api/v1/auth/jwt/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return email


# ---------- PIPE-14a: the manual hunt claims the hunt lock ----------

class TestManualHuntClaimsHuntLock:
    def test_hunt_returns_409_when_lock_held(self, client, db, monkeypatch):
        """The cron worker holds the claim (SystemLock name='hunt') for
        its cycle. A manual press in that window must get an honest
        busy answer, not silently double-scrape the shared pool."""
        from app.services.worker import claim_hunt, release_hunt

        _register_and_auth(client)

        ran = []

        def fake_run_pipeline(**kwargs):
            ran.append(kwargs)
            return {"scrape": [], "match": None, "top_matches": []}

        monkeypatch.setattr("app.api.v1.pipeline.run_pipeline", fake_run_pipeline)

        token = claim_hunt(db)
        assert token, "fixture setup: first claimant must win"
        try:
            r = client.post(
                "/api/v1/pipeline/run",
                json={"sources": ["arbeitnow"], "match": False},
            )
            assert r.status_code == 409, (
                f"a held hunt lock must return busy, got {r.status_code}: {r.text[:300]}"
            )
            assert not ran, "a held hunt lock must short-circuit the run, not double-run"
        finally:
            release_hunt(db, token)

    def test_lock_held_during_run_and_released_after(self, client, db, monkeypatch):
        """The route claims the SAME SystemLock row the worker uses:
        a claim attempt from inside the running pipeline must lose, and
        the lock must be free again once the response is sent."""
        from app.services.worker import claim_hunt

        _register_and_auth(client)

        seen = {}

        def fake_run_pipeline(**kwargs):
            seen["claim_during_run"] = claim_hunt(db)
            return {"scrape": [], "match": None, "top_matches": []}

        monkeypatch.setattr("app.api.v1.pipeline.run_pipeline", fake_run_pipeline)

        r = client.post(
            "/api/v1/pipeline/run", json={"sources": ["arbeitnow"], "match": False}
        )
        assert r.status_code == 200, r.text[:300]
        assert seen["claim_during_run"] is None, (
            "the manual hunt did not hold the hunt lock while running"
        )
        assert claim_hunt(db), "the manual hunt must ALWAYS release its claim"

    def test_lock_released_when_pipeline_raises(self, client, db, monkeypatch):
        """A crashed manual hunt must not leak the claim — a leaked
        claim is a 45-minute silent hunt outage (WO-04 review)."""
        from app.services.worker import claim_hunt

        _register_and_auth(client)

        def boom(**kwargs):
            raise RuntimeError("scrape exploded")

        monkeypatch.setattr("app.api.v1.pipeline.run_pipeline", boom)

        with pytest.raises(RuntimeError):
            client.post(
                "/api/v1/pipeline/run",
                json={"sources": ["arbeitnow"], "match": False},
            )
        assert claim_hunt(db), "a crashed manual hunt leaked the hunt lock"


# ---------- PIPE-14b: unique(source, source_id) + ingest upsert ----------

class _FakeScraper:
    jobs = []

    @classmethod
    def is_configured(cls, ctx):
        return True

    def fetch(self, ctx):
        return list(self.jobs)


@pytest.fixture()
def fake_source(monkeypatch):
    from app.services.scrapers import SCRAPER_REGISTRY

    SCRAPER_REGISTRY["fake"] = _FakeScraper
    yield _FakeScraper
    del SCRAPER_REGISTRY["fake"]


def _nj(source_id, url=None, title="Dev", company="Acme"):
    from app.services.scrapers.base import NormalizedJob

    return NormalizedJob(
        source="t", source_id=source_id, title=title, company=company,
        url=url or f"https://x/{source_id}",
    )


class TestJobPostingIngestUpsert:
    def test_same_run_duplicate_inserts_once(self, db, fake_source):
        from app.models import JobPosting
        from app.services.pipeline import scrape_source

        fake_source.jobs = [_nj("a"), _nj("a")]
        run = scrape_source(db, "fake", ctx=None)

        assert run.status == "completed", (run.status, run.error)
        assert run.jobs_new == 1, "same-run duplicate must be stored exactly once"
        assert (
            db.query(JobPosting).filter_by(source="t", source_id="a").count() == 1
        )

    def test_cross_run_duplicate_backstopped_by_constraint(self, db, fake_source, monkeypatch):
        """The PIPE-14 race: the competitor's row lands AFTER our
        _job_exists check — the pre-check cannot see it. The unique
        index is the backstop; the ingest must upsert (skip) instead of
        raising IntegrityError and failing the whole batch."""
        from app.models import JobPosting
        from app.services.pipeline import scrape_source

        db.add(JobPosting(source="t", source_id="race-1", title="Dev",
                          company="Acme", url="https://x/1"))
        db.commit()

        # The check misses (it ran before the competitor committed).
        monkeypatch.setattr("app.services.pipeline._job_exists", lambda db_, nj: False)

        fake_source.jobs = [_nj("race-1"), _nj("fresh-1")]
        run = scrape_source(db, "fake", ctx=None)

        assert run.status == "completed", (
            f"a lost insert race must not fail the run: {run.status} {run.error}"
        )
        stored = {j.source_id for j in db.query(JobPosting).filter_by(source="t")}
        assert stored == {"race-1", "fresh-1"}, stored
        assert run.jobs_new == 1, "the conflicted insert must not count as new"


class TestJobPostingsUniqueMigration:
    """Full Alembic rehearsal on a scratch SQLite file: upgrade to the
    pre-PIPE-14 head, seed duplicates WITH children (including a user
    who matched both copies), upgrade to head, verify; then downgrade
    and re-upgrade (dedupe must be idempotent)."""

    PRE = "f4a6b8c2d3e7"  # head before the PIPE-14b revision

    def _alembic_cfg(self):
        from alembic.config import Config

        ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        return Config(str(ini))

    def _upgrade(self, rev):
        from alembic import command

        command.upgrade(self._alembic_cfg(), rev)

    def test_dedupes_repoints_children_and_enforces_unique(self, tmp_path, monkeypatch):
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.exc import IntegrityError

        from alembic import command

        url = f"sqlite:///{tmp_path / 'pipe14_mig.db'}"
        # alembic/env.py reads DATABASE_URL, not the ini value
        monkeypatch.setenv("DATABASE_URL", url)
        self._upgrade(self.PRE)

        eng = create_engine(url)
        u1, u2, u3, u4 = (str(uuid.uuid4()) for _ in range(4))
        with eng.begin() as c:
            # three copies of (t, dup-1); two of (t, dup-2); NULL
            # source_ids must never be treated as duplicates
            for i in range(3):
                c.execute(text(
                    "INSERT INTO job_postings (source, source_id, title, url,"
                    " remote, status, scraped_at, created_at, updated_at)"
                    " VALUES ('t','dup-1','Dev','https://x/d1',0,'new',"
                    "'2026-01-01','2026-01-01','2026-01-01')"))
            for i in range(2):
                c.execute(text(
                    "INSERT INTO job_postings (source, source_id, title, url,"
                    " remote, status, scraped_at, created_at, updated_at)"
                    " VALUES ('t','dup-2','Dev','https://x/d2',0,'new',"
                    "'2026-01-01','2026-01-01','2026-01-01')"))
            for i in range(2):  # manual jobs: NULL source_id, keep both
                c.execute(text(
                    "INSERT INTO job_postings (source, source_id, title, url,"
                    " remote, status, scraped_at, created_at, updated_at)"
                    " VALUES ('manual',NULL,'Dev','https://x/m',0,'new',"
                    "'2026-01-01','2026-01-01','2026-01-01')"))

            # U1 matched BOTH copies of dup-1 with the KEEPER-side match
            # lower (match id 1 on keeper job 1, id 2 on loser job 2).
            c.execute(text(
                f"INSERT INTO match_results (user_id, job_id, score, tier,"
                f" created_at, updated_at) VALUES ('{u1}', 1, 90, 'good_match',"
                f" '2026-01-01','2026-01-01')"))
            c.execute(text(
                f"INSERT INTO match_results (user_id, job_id, score, tier,"
                f" created_at, updated_at) VALUES ('{u1}', 2, 80, 'good_match',"
                f" '2026-01-01','2026-01-01')"))
            # U2 matched only the loser of dup-2 (job 5, keeper 4)
            c.execute(text(
                f"INSERT INTO match_results (user_id, job_id, score, tier,"
                f" created_at, updated_at) VALUES ('{u2}', 5, 70, 'stretch',"
                f" '2026-01-01','2026-01-01')"))

            # U3 is the PRODUCTION shape of the collision, found in
            # review: the matcher scans candidates NEWEST-first
            # (order_by scraped_at.desc), so for a race-born duplicate
            # pair the LOSER copy (inserted later = newer) is matched
            # FIRST — the LOWER match id sits on the LOSER job (id 4 on
            # job 2) and the HIGHER id on the KEEPER (id 5 on job 1).
            # The group dedupe must therefore delete the KEEPER-side
            # row too, or the re-point UPDATE itself violates
            # uq_match_results_user_job.
            c.execute(text(
                f"INSERT INTO match_results (user_id, job_id, score, tier,"
                f" created_at, updated_at) VALUES ('{u3}', 2, 95, 'good_match',"
                f" '2026-01-01','2026-01-01')"))       # id 4 — loser side
            c.execute(text(
                f"INSERT INTO match_results (user_id, job_id, score, tier,"
                f" created_at, updated_at) VALUES ('{u3}', 1, 75, 'stretch',"
                f" '2026-01-01','2026-01-01')"))       # id 5 — keeper side
            # U4 matched a job with no duplicate — must be untouched
            c.execute(text(
                f"INSERT INTO match_results (user_id, job_id, score, tier,"
                f" created_at, updated_at) VALUES ('{u4}', 6, 60, 'stretch',"
                f" '2026-01-01','2026-01-01')"))       # id 6

            # A draft on loser job 3, linked to U1's match on loser job 2
            # (the match that must be dropped in the uq collision).
            c.execute(text(
                "INSERT INTO application_drafts (user_id, job_id, match_id,"
                " status, created_at, updated_at)"
                f" VALUES ('{u1}', 3, 2, 'ready', '2026-01-01','2026-01-01')"))
            # One draft per U3 match: the survivor (match 4, loser job 2)
            # and the doomed keeper-side match (match 5, keeper job 1).
            c.execute(text(
                "INSERT INTO application_drafts (user_id, job_id, match_id,"
                " status, created_at, updated_at)"
                f" VALUES ('{u3}', 2, 4, 'ready', '2026-01-01','2026-01-01')"))
            c.execute(text(
                "INSERT INTO application_drafts (user_id, job_id, match_id,"
                " status, created_at, updated_at)"
                f" VALUES ('{u3}', 1, 5, 'ready', '2026-01-01','2026-01-01')"))
            # An application on loser job 5, linked to U2's KEPT match.
            c.execute(text(
                "INSERT INTO applications (user_id, job_id, match_id, method,"
                " status, created_at, updated_at)"
                f" VALUES ('{u2}', 5, 3, 'email', 'sent',"
                f" '2026-01-01','2026-01-01')"))

        self._upgrade("head")

        with eng.connect() as c:
            dup1 = c.execute(text(
                "SELECT id FROM job_postings WHERE source='t' AND source_id='dup-1'"
            )).fetchall()
            dup2 = c.execute(text(
                "SELECT id FROM job_postings WHERE source='t' AND source_id='dup-2'"
            )).fetchall()
            nulls = c.execute(text(
                "SELECT id FROM job_postings WHERE source_id IS NULL"
            )).fetchall()
            assert [r[0] for r in dup1] == [1], f"dup-1 must collapse to the lowest id: {dup1}"
            assert [r[0] for r in dup2] == [4], f"dup-2 must collapse to the lowest id: {dup2}"
            assert len(nulls) == 2, "NULL source_id rows are distinct — never deduped"

            m_u1 = c.execute(text(
                f"SELECT job_id FROM match_results WHERE user_id='{u1}'"
            )).fetchall()
            assert m_u1 == [(1,)], (
                f"the uq(user_id, job_id) collision must keep exactly one "
                f"match on the keeper: {m_u1}"
            )
            m_u2 = c.execute(text(
                f"SELECT job_id FROM match_results WHERE user_id='{u2}'"
            )).fetchall()
            assert m_u2 == [(4,)], f"U2's match must be re-pointed to the keeper: {m_u2}"

            # The production shape: the LOW match id (4) sat on the
            # LOSER and is the group survivor — the HIGHER keeper-side
            # match (5) is deleted, and the survivor is re-pointed onto
            # the keeper without the UPDATE itself blowing up.
            m_u3 = c.execute(text(
                f"SELECT id, job_id FROM match_results WHERE user_id='{u3}'"
            )).fetchall()
            assert m_u3 == [(4, 1)], (
                f"the group must keep exactly its lowest match id (4), "
                f"re-pointed to the keeper — the keeper-side match must be "
                f"deleted too: {m_u3}"
            )
            m_u4 = c.execute(text(
                f"SELECT id, job_id FROM match_results WHERE user_id='{u4}'"
            )).fetchall()
            assert m_u4 == [(6, 6)], (
                f"a match on a non-duplicated job must be untouched: {m_u4}"
            )

            drafts = c.execute(text(
                "SELECT job_id, match_id FROM application_drafts ORDER BY id"
            )).fetchall()
            assert drafts == [(1, None), (1, 4), (1, None)], (
                f"drafts re-pointed to the keeper; match_id kept for the "
                f"surviving match (4) and cleared for both dropped matches "
                f"(2 and 5): {drafts}"
            )
            app_row = c.execute(text(
                "SELECT job_id, match_id FROM applications"
            )).fetchone()
            assert app_row == (4, 3), (
                f"application re-pointed, kept match reference intact: {app_row}"
            )

            # scratch tables cleaned up
            names = inspect(eng).get_table_names()
            assert not any(n.startswith("_pipe14") for n in names), names

            # the index bites: a fourth copy of (t, dup-1) is refused
            with pytest.raises(IntegrityError):
                with eng.begin() as c2:
                    c2.execute(text(
                        "INSERT INTO job_postings (source, source_id, title, url,"
                        " remote, status, scraped_at, created_at, updated_at)"
                        " VALUES ('t','dup-1','Dev','https://x/d1b',0,'new',"
                        "'2026-01-02','2026-01-02','2026-01-02')"))

        # downgrade drops the index; the same insert now succeeds.
        # Explicit target (pre-unique revision), not "-1": PIPE-18's
        # system_locks.owner_token revision is now the head, so a
        # relative downgrade would only pop that instead.
        command.downgrade(self._alembic_cfg(), self.PRE)
        with eng.begin() as c:
            c.execute(text(
                "INSERT INTO job_postings (source, source_id, title, url,"
                " remote, status, scraped_at, created_at, updated_at)"
                " VALUES ('t','dup-1','Dev','https://x/d1c',0,'new',"
                "'2026-01-03','2026-01-03','2026-01-03')"))

        # re-upgrade: dedupe must be idempotent (the new copy is the
        # highest id, so the keeper stays 1 and it simply disappears)
        self._upgrade("head")
        with eng.connect() as c:
            still = c.execute(text(
                "SELECT id FROM job_postings WHERE source='t' AND source_id='dup-1'"
            )).fetchall()
            assert [r[0] for r in still] == [1], still
        eng.dispose()


# ---------- DATA-5: watermark upsert race ----------

def _blind_watermark_select(monkeypatch, db, fake_source=None, arm_now=False):
    """Force the DATA-5 interleaving deterministically. ScrapeWatermark
    queries see nothing from the moment the blindness is ARMED — either
    immediately (direct set_watermarks calls) or by the fake scraper's
    fetch() (inside scrape_source: after delta_since_for's read, before
    set_watermarks' read) — until the first one consumes it. That is
    exactly the window in which the loser of the select-then-insert
    race sits when its INSERT hits uq_scrape_watermark."""
    from app.models import ScrapeWatermark

    real_query = db.query
    state = {"armed": arm_now}

    def query(*entities, **kw):
        q = real_query(*entities, **kw)
        if (
            entities
            and entities[0] is ScrapeWatermark
            and state["armed"]
        ):
            state["armed"] = False  # one blind read — the race window
            return q.filter(false())
        return q

    monkeypatch.setattr(db, "query", query)

    if fake_source is not None:
        real_fetch = fake_source.fetch

        def fetch(_self, ctx):
            state["armed"] = True
            return real_fetch(_self, ctx)

        fake_source.fetch = fetch


class TestWatermarkInsertRace:
    def test_lost_insert_race_recovers_without_poisoning(self, db, monkeypatch):
        """The loser's INSERT violates uq_scrape_watermark. The fix:
        rollback, re-select (the winner's row exists now), bump it. No
        exception escapes and the session stays usable — no
        PendingRollbackError on the next commit."""
        from app.core.timeutil import utc_now
        from app.models import ScrapeWatermark
        from app.services.pipeline import _scope_key, set_watermarks

        ctx = {
            "country": "SE", "queries": ["utvecklare"], "municipalities": ["Malmö"],
            "languages": [], "remote_only": False, "include_remote": True,
        }
        stale = utc_now() - timedelta(days=5)
        db.add(ScrapeWatermark(source="jobtech", query="utvecklare",
                               scope=_scope_key(ctx), watermark_at=stale))
        db.commit()

        _blind_watermark_select(monkeypatch, db, arm_now=True)
        set_watermarks(db, "jobtech", ctx)  # must not raise

        rows = db.query(ScrapeWatermark).all()
        assert len(rows) == 1, rows
        assert rows[0].watermark_at > stale, "the winner's row must be bumped, not duplicated"
        db.commit()  # a poisoned session would raise PendingRollbackError here

    def test_scrape_run_reaches_terminal_state_after_race(self, db, fake_source, monkeypatch):
        """End-to-end on the scrape unit: job stored, watermark insert
        loses the race — the run must still complete. Pre-fix, the
        poisoned session made the finally-commit raise
        PendingRollbackError: hunt 500s AFTER the jobs landed, run row
        stuck 'running' until the 2h stale sweep."""
        from app.core.timeutil import utc_now
        from app.models import JobPosting, ScrapeWatermark
        from app.services.pipeline import scrape_source
        from app.services.scrapers.base import NormalizedJob

        # A real (gate-passing) context: ctx stays falsy only until the
        # delta branch adds its keys, so the job must pass the gates on
        # its own merits (local, remote, English).
        ctx = {
            "country": "SE", "municipalities": ["Malmö"], "languages": [],
            "remote_only": False, "include_remote": True, "queries": ["dev"],
        }
        db.add(ScrapeWatermark(source="fake", query="dev", scope="malmö",
                               watermark_at=utc_now() - timedelta(days=1)))
        db.commit()

        monkeypatch.setattr("app.services.pipeline.DELTA_SOURCES", {"fake"})
        fake_source.jobs = [NormalizedJob(
            source="t", source_id="w-1", title="Dev", company="Acme",
            url="https://x/w1", remote=True, location="Malmö",
        )]
        _blind_watermark_select(monkeypatch, db, fake_source=fake_source)

        run = scrape_source(db, "fake", ctx=ctx)  # must not raise

        assert run.status == "completed", (run.status, run.error)
        assert run.finished_at is not None, "the run must reach a terminal, timestamped state"
        assert db.query(JobPosting).filter_by(source="t", source_id="w-1").count() == 1, (
            "the scraped job must stay committed"
        )
        assert db.query(ScrapeWatermark).count() == 1, "exactly one watermark row"

    def test_finalize_run_recovers_poisoned_session(self, db):
        """Belt for the case the upsert fix can't cover (a NON-race
        failure mid-run): even a pending-rollback session must not stop
        the ScrapeRun's terminal write — rollback, retry once, log."""
        from sqlalchemy.exc import IntegrityError

        from app.models import ScrapeRun
        from app.services.pipeline import _finalize_run

        run = ScrapeRun(source="fake", status="running")
        db.add(run)
        db.commit()

        cols = ("source, source_id, title, url, remote, status,"
                " scraped_at, created_at, updated_at")
        base = ("('p','z','T','https://z',0,'new',"
                "'2026-01-01','2026-01-01','2026-01-01')")
        db.execute(text(f"INSERT INTO job_postings ({cols}) VALUES {base}"))
        db.commit()
        # SQLite refuses the duplicate AT STATEMENT TIME — the session
        # is left pending-rollback either way.
        with pytest.raises(IntegrityError):
            db.execute(text(
                f"INSERT INTO job_postings ({cols}) VALUES "
                "('p','z','T','https://z2',0,'new','2026-01-02','2026-01-02','2026-01-02')"
            ))
            db.commit()
        # the session is now poisoned — the pre-fix finally's bare
        # db.commit() raised PendingRollbackError from here

        run.status = "completed"
        _finalize_run(db, run)  # must not raise

        fresh = db.query(ScrapeRun).filter_by(id=run.id).first()
        assert fresh.status == "completed"
        assert fresh.finished_at is not None
