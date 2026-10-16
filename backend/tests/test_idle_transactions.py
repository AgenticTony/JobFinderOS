"""No database transaction may stay open while the app waits on slow work.

Production, 2026-10-16: sessions sat "idle in transaction" for minutes
(ORM SELECT profiles … then nothing), holding ACCESS SHARE locks on
users/profiles/job_postings. That blocked the WO-31 migration's DDL
(30s lock_timeout → the new build refused to boot) and will block every
future ALTER on those tables. Measured on Postgres 16 with FastAPI
0.141: a route's request session stays open through BOTH a
BackgroundTasks task and an awaited run_in_threadpool call, and the
ai_jobs drainer held its read transaction across every AI call.

The invariant tested here, engine-agnostic: at the moment of every
provider call / slow-work entry point, NO SQLAlchemy session is inside a
transaction. A lazy attribute reload during the wait (which silently
re-opens a transaction) fails it too.
"""

import weakref

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine, get_db
from app.main import app
from app.services.ai_queue import AIDrainer
from tests import test_ai_queue as _q
from tests.fakes.fake_ai import FakeAI
from tests.test_ai_queue import (
    Clock,
    _draft,
    _drain_until,
    _jobs,
    _user,
    _wire,
)

# The queue suite's fixtures, shared by binding (pytest collects module
# attributes that are fixtures; an import + same-named parameter is F811).
client = _q.client
queue_off = _q.queue_off

# Track REAL database transactions — what Postgres reports as "idle in
# transaction" — not Session.in_transaction(): SQLAlchemy 2.0 opens a
# purely logical SessionTransaction as soon as an object is modified
# (no connection, no lock), which is harmless and expected while results
# are staged in memory between AI calls.
_open_conns: set = set()
_session_conn: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


@event.listens_for(engine, "begin")
def _conn_begin(conn):  # pragma: no cover - hook
    _open_conns.add(id(conn))


@event.listens_for(engine, "commit")
def _conn_commit(conn):  # pragma: no cover - hook
    _open_conns.discard(id(conn))


@event.listens_for(engine, "rollback")
def _conn_rollback(conn):  # pragma: no cover - hook
    _open_conns.discard(id(conn))


@event.listens_for(Session, "after_begin")
def _track(session, transaction, connection):  # pragma: no cover - hook
    _session_conn[session] = id(connection)


def _open_transactions() -> list:
    """Connections currently inside a database transaction."""
    return list(_open_conns)


def _db_open(session) -> bool:
    """Does THIS session hold a connection with an open DB transaction?"""
    return _session_conn.get(session) in _open_conns


class _Recorder:
    """FakeAI on_call hook: snapshot the open-transaction count per call."""

    def __init__(self):
        self.seen = []  # (kind, open transaction count)

    def __call__(self, kind, prompt):
        self.seen.append((kind, len(_open_transactions())))

    def violations(self):
        return [(k, n) for k, n in self.seen if n]


# ---------------------------------------------------------------------
# The ai_jobs drainer: every interactive kind
# ---------------------------------------------------------------------


class TestDrainerHoldsNoTransactionDuringAICalls:
    def test_tailor_and_judge_calls_run_with_no_open_transaction(
            self, client, monkeypatch):
        uid, job_id, h = _user(client)
        rec = _Recorder()
        _wire(monkeypatch, FakeAI(on_call=rec))
        clock = Clock()
        drainer = AIDrainer(clock=clock)

        r = client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        assert r.status_code == 202, r.text
        _drain_until(drainer, clock,
                     lambda: _draft(uid, job_id)[0].status in ("ready", "failed"))

        kinds = [k for k, _ in rec.seen]
        assert "tailor" in kinds and "judge" in kinds, kinds
        assert not rec.violations(), (
            f"AI calls made while a DB transaction was open: {rec.violations()} "
            "— the drainer holds read locks on drafts/profiles for the whole "
            "provider call (minutes), blocking DDL"
        )
        # Exactly-once result still holds: staged writes land at the CAS.
        draft = _draft(uid, job_id)[0]
        assert draft.status == "ready" and draft.cover_letter

    def test_extract_and_suggest_run_with_no_open_transaction(
            self, client, monkeypatch):
        from app.core.database import SessionLocal
        from app.services import ai_queue

        uid, _job, h = _user(client, approved_job=False)
        rec = _Recorder()
        _wire(monkeypatch, FakeAI(on_call=rec))
        clock = Clock()
        drainer = AIDrainer(clock=clock)

        db = SessionLocal()
        try:
            from app.models import Profile
            pid = db.query(Profile.id).filter(Profile.user_id == uid).scalar()
            ai_queue.enqueue(db, user_id=uid, kind="extract", subject_id=pid)
            ai_queue.enqueue(db, user_id=uid, kind="suggest", subject_id=pid,
                             params={"country": "SE", "mode": "field"})
            db.commit()
        finally:
            db.close()
        _drain_until(drainer, clock,
                     lambda: all(j.status in ("done", "failed") for j in _jobs(uid)))

        kinds = {k for k, _ in rec.seen}
        assert {"extract", "suggest"} <= kinds, kinds
        assert not rec.violations(), rec.violations()
        assert all(j.status == "done" for j in _jobs(uid)), [
            (j.kind, j.status, j.error) for j in _jobs(uid)]


    def test_recheck_judge_call_runs_with_no_open_transaction(
            self, client, monkeypatch):
        import json

        from tests.test_ai_queue import TestRecheckQueued

        uid, did, h, fake = TestRecheckQueued()._blocked(client, monkeypatch)
        rec = _Recorder()
        fake.on_call = rec
        fake.judge_reply = '{"unsupported": []}'
        r = client.post(f"/api/v1/applications/draft/{did}/recheck", headers=h)
        assert r.status_code == 202, r.text
        clock = Clock()
        _drain_until(AIDrainer(clock=clock), clock,
                     lambda: _jobs(uid, "recheck")[-1].status == "done")
        assert [k for k, _ in rec.seen] == ["judge"], rec.seen
        assert not rec.violations(), rec.violations()
        d = client.get(f"/api/v1/applications/draft/{did}", headers=h).json()
        assert d["status"] == "ready", json.dumps(d)[:200]


class TestReleaseTransactionHelper:
    def test_refuses_to_publish_pending_writes(self, client):
        """Releasing must never commit a half-staged result early — the
        drainer writes results exactly once, at its lease CAS."""
        from app.core.database import SessionLocal, release_transaction
        from app.models import Profile

        db = SessionLocal()
        try:
            p = db.query(Profile).first()
            p.full_name = "Staged, not committed"
            with pytest.raises(RuntimeError, match="pending writes"):
                release_transaction(db)
        finally:
            db.rollback()
            db.close()

    def test_loaded_objects_stay_usable_without_reopening(self, client):
        """After release, reading a loaded attribute must not reload it —
        a reload would silently re-open a DB transaction during the wait."""
        from app.core.database import SessionLocal, release_transaction
        from app.models import Profile

        db = SessionLocal()
        try:
            p = db.query(Profile).first()
            release_transaction(db)
            assert not _db_open(db)
            _ = (p.id, p.cv_text, p.full_name)
            assert not _db_open(db), "attribute access re-opened a transaction"
        finally:
            db.close()


# ---------------------------------------------------------------------
# Request routes that hand slow work to their own sessions
# ---------------------------------------------------------------------


@pytest.fixture()
def request_sessions():
    """Capture the REQUEST session of every call (wraps the real get_db)."""
    captured = []

    def _capturing_get_db():
        gen = get_db()
        session = next(gen)
        captured.append(session)
        try:
            yield session
        finally:
            gen.close()

    app.dependency_overrides[get_db] = _capturing_get_db
    yield captured
    app.dependency_overrides.pop(get_db, None)


class TestRoutesReleaseBeforeSlowWork:
    def test_match_run_background_task_runs_with_request_tx_closed(
            self, client, monkeypatch, request_sessions):
        from app.services import matcher_service

        uid, _job, h = _user(client, approved_job=False)
        monkeypatch.setattr(settings, "BETA_UNCAPPED_HUNTS", True)
        seen = {}

        def fake_run_matching(db, **kw):
            seen["request_tx_open"] = [_db_open(s) for s in request_sessions]
            return {"status": "ok", "jobs_considered": 0, "matches_created": 0}

        monkeypatch.setattr(matcher_service, "run_matching", fake_run_matching)
        r = client.post("/api/v1/matches/run", headers=h)
        assert r.status_code == 200, r.text
        assert "request_tx_open" in seen, "background matching never ran"
        assert not any(seen["request_tx_open"]), (
            "the match-run request kept its transaction (SELECT profiles …) "
            "open for the whole background matching run — idle in transaction "
            "for minutes in production"
        )

    def test_hunt_route_runs_pipeline_with_request_tx_closed(
            self, client, monkeypatch, request_sessions):
        from app.api.v1 import pipeline as pipeline_route

        uid, _job, h = _user(client, approved_job=False)
        seen = {}

        def fake_run_pipeline(**kw):
            seen["request_tx_open"] = [_db_open(s) for s in request_sessions]
            return {"scrape": [], "match": None, "top_matches": [],
                    "top_match_ids": []}

        monkeypatch.setattr(pipeline_route, "run_pipeline", fake_run_pipeline)
        r = client.post("/api/v1/pipeline/run", json={"match": False}, headers=h)
        assert r.status_code == 200, r.text
        assert "request_tx_open" in seen, "the hunt never ran"
        assert not any(seen["request_tx_open"]), (
            "the hunt request kept its transaction open for the whole "
            "scrape + match (minutes) — idle in transaction in production"
        )
