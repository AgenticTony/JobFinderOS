"""WO-31 Phase A step 1 — the durable ai_jobs queue (interactive kinds).

Owner requirement (2026-10-16): provider load must NEVER fail a user's
request. Tailor / recheck / extract / suggest enqueue, return 202 with an
honest queued state, and one drainer completes them; a 429 re-queues
with next_attempt_at (Retry-After honoured), never 'failed'.

Red-first record (each revert-checked by hand, see the PR):
  T2a  429 -> queued -> ready, never failed   (red on main: AIBusyError
       -> draft 'failed'; red here if run_job routes AILoadError to fail)
  T2b  restart mid-queue resumes             (red if the CAS ignores the
       lease token: the zombie drainer's result overwrites)
  T2c  idempotency                           (red without the partial
       unique / the active-job pre-check)
  T2   interactive before background, reserve (red if background_slots
       returns the whole limit)
  T2d  extract/suggest queued under 429       (red on main: the upload
       swallowed the 429 and the fields were silently lost)
  T3   Retry-After / AIMD / quota pause
Every fake-AI test asserts on the OUTBOUND artifact: which user's CV
reached which provider call, and whose ai_usage row it billed.
"""

import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.timeutil import utc_now
from app.main import app
from app.models import (
    AIJob,
    AIUsage,
    ApplicationDraft,
    JobPosting,
    MatchResult,
    Profile,
)
from app.models import User as UserModel
from app.services import ai_queue
from app.services.ai_queue import AIDrainer, ConcurrencyController
from tests.fakes.fake_ai import FakeAI, fake_service, rate_limit_error

ALICE_CV = ("Alice Berg. Backend Developer, Klarna, Stockholm 2019 to 2024. "
            "Built payment APIs in Python.")
BOB_CV = ("Bob Nilsson. Data Engineer, Spotify, Göteborg 2018 to 2023. "
          "Ran Spark pipelines in Scala.")


class Clock:
    """Virtual time for the drainer (claims, leases, next_attempt_at)."""

    def __init__(self):
        # A beat ahead of wall time: rows enqueued "now" by a request
        # (real utc_now) are already due on the virtual clock.
        self.now = utc_now() + timedelta(seconds=1)

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # lifespan -> init_db -> alembic head
        yield c
    engine.dispose()


def _purge_pending():
    db = SessionLocal()
    try:
        db.query(AIJob).filter(AIJob.status.in_(("queued", "running"))).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def queue_off(monkeypatch):
    """The queue's own tests drive the drainer by hand; nothing may be
    left pending for the next test (or module) to claim."""
    monkeypatch.setattr(settings, "AI_DRAINER", "off")
    _purge_pending()
    yield
    _purge_pending()
    ai_queue._HANDLERS.pop("match", None)


@pytest.fixture()
def db(client):
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _wire(monkeypatch, fake: FakeAI, judge: bool = True):
    """Every AI seam the drained handlers use -> the fake."""
    from app.api.v1 import profiles
    from app.services import cv_service, draft_service

    svc = fake_service(fake)
    monkeypatch.setattr(draft_service, "get_ai_service", lambda: svc)
    monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
    monkeypatch.setattr(cv_service, "get_ai_service", lambda: svc)
    monkeypatch.setattr(cv_service, "ai_service_available", lambda: True)
    monkeypatch.setattr(profiles, "ai_service_available", lambda: True)
    monkeypatch.setattr(settings, "FABRICATION_JUDGE", "on" if judge else "off")
    import app.services.ai_service as ai_mod

    monkeypatch.setattr(ai_mod.time, "sleep", lambda s: None)  # format re-asks
    return svc


def _user(client, cv=ALICE_CV, *, approved_job=True):
    """A signed-in user with a CV and (optionally) an approved match.
    Returns (uid, job_id, headers)."""
    from tests.auth_helpers import get_or_create_user, mint_token

    email = f"q-{uuid.uuid4().hex[:8]}@test.example"
    uid = get_or_create_user(email)
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = cv
        p.full_name = cv.split(".")[0]
        job_id = None
        if approved_job:
            job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                             title="Developer", company="Acme",
                             url=f"https://x/{uuid.uuid4().hex[:8]}", status="matched",
                             description="A Python backend role.")
            db.add(job)
            db.flush()
            db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                               tier="good_match", decision="approved"))
            job_id = job.id
        db.commit()
    finally:
        db.close()
    return uid, job_id, {"Authorization": f"Bearer {mint_token(uid, email)}"}


def _drain_until(drainer: AIDrainer, clock: Clock, done, *, step_s=3.0, max_steps=40,
                 every_step=None):
    """Drain, advance virtual time, repeat until done() (or give up)."""
    for _ in range(max_steps):
        drainer.drain_once()
        if every_step is not None:
            every_step()
        if done():
            return
        clock.advance(step_s)
    raise AssertionError("queue never drained")


def _draft(uid, job_id):
    db = SessionLocal()
    try:
        rows = db.query(ApplicationDraft).filter(
            ApplicationDraft.user_id == uid, ApplicationDraft.job_id == job_id).all()
        return rows
    finally:
        db.close()


def _jobs(uid, kind=None):
    db = SessionLocal()
    try:
        q = db.query(AIJob).filter(AIJob.user_id == uid)
        if kind:
            q = q.filter(AIJob.kind == kind)
        return q.order_by(AIJob.id).all()
    finally:
        db.close()


# =====================================================================
# T2a — 429 -> queued -> ready, never failed
# =====================================================================


class TestLoadNeverFailsTheDraft:
    def test_429_then_ready_never_failed(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        fake = FakeAI(fail_first=6, code="1302", retry_after=2)
        _wire(monkeypatch, fake)
        clock = Clock()
        drainer = AIDrainer(clock=clock)

        r = client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "queued"
        assert body["queue"]["status"] == "queued" and body["queue"]["kind"] == "tailor"
        assert body["queue"]["eta_minutes"] >= 1

        seen = []

        def observe():
            d = client.get(f"/api/v1/applications/draft/{body['id']}", headers=h).json()
            seen.append(d["status"])
            assert d["status"] != "failed", (
                f"provider load failed the user's draft: {d['error']!r} — "
                "the owner requirement is queue, never fail")

        _drain_until(drainer, clock, lambda: seen and seen[-1] == "ready",
                     every_step=observe)

        rows = _draft(uid, job_id)
        assert len(rows) == 1, f"one Prepare made {len(rows)} drafts"
        d = rows[0]
        assert d.status == "ready" and d.cover_letter
        jobs = _jobs(uid, "tailor")
        assert [j.status for j in jobs] == ["done"], "exactly one completed tailor job"
        assert jobs[0].attempts == 7, "6 rejected claims + the one that landed"
        # The guard ran ONCE, on the package that was stored
        judges = [p for k, p in fake.answered if k == "judge"]
        assert len(judges) == 1, f"guard ran {len(judges)}x on the final package"
        assert d.cover_letter in judges[0] and d.tailored_cv in judges[0]
        # Outbound identity + attribution: every call carried Alice's CV
        # and every ai_usage row (429 ones included) is hers.
        assert all("Alice Berg" in p for k, p in fake.calls if k == "tailor")
        db = SessionLocal()
        try:
            usage = db.query(AIUsage).filter(AIUsage.user_id == uid).all()
        finally:
            db.close()
        assert sum(1 for u in usage if u.detail == "rate_limited (429)") == 6
        assert {u.kind for u in usage if u.detail is None} == {"tailor", "judge"}

    def test_retry_after_is_the_next_attempt(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(fail_first=1, retry_after=3))
        clock = Clock()
        drainer = AIDrainer(clock=clock)
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        t0 = clock()
        assert drainer.drain_once() == ["requeued"]
        job = _jobs(uid, "tailor")[0]
        assert job.status == "queued" and job.last_error_code == "1302"
        assert job.next_attempt_at >= t0 + timedelta(seconds=3), (
            "Retry-After: 3 must set next_attempt_at >= now + 3s")
        clock.advance(2)
        assert drainer.drain_once() == [], "claimed before Retry-After elapsed"
        clock.advance(1.5)
        assert drainer.drain_once() == ["done"]

    def test_non_load_failure_still_fails_with_an_action(self, client, monkeypatch):
        """'failed' stays for non-load causes — malformed output after the
        format re-asks — with a Try-again message."""
        uid, job_id, h = _user(client)
        fake = FakeAI()
        _wire(monkeypatch, fake)
        monkeypatch.setattr(fake, "_answer", lambda kind, prompt: "not json at all")
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
        d = _draft(uid, job_id)[0]
        assert d.status == "failed", "malformed output is a non-load failure"
        assert "Malformed tailoring response" in (d.error or ""), d.error
        assert _jobs(uid, "tailor")[0].status == "failed"

    def test_fabrication_block_is_a_finished_job_not_a_failure(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(judge_reply=json.dumps(
            {"unsupported": [{"claim": "AWS Certified", "why": "not in CV"}]})))
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        assert AIDrainer(clock=Clock()).drain_once() == ["done"]
        d = _draft(uid, job_id)[0]
        assert d.status == "failed" and d.fabrication_blocked, d.error
        assert _jobs(uid, "tailor")[0].status == "done"


# =====================================================================
# T2b — restart mid-queue: every job completes, each result once
# =====================================================================


class _Killed(BaseException):
    """A process death: BaseException skips every `except Exception`."""


class TestRestartMidQueue:
    def test_killed_drainer_resumes_after_lease_expiry(self, client, monkeypatch):
        users = [_user(client, cv) for cv in (ALICE_CV, BOB_CV, ALICE_CV)]
        state = {"kill": True}

        def kill_once(kind, prompt):
            if state["kill"] and kind == "tailor":
                state["kill"] = False
                raise _Killed()

        fake = FakeAI(on_call=kill_once)
        _wire(monkeypatch, fake, judge=False)
        for _uid, job_id, h in users:
            assert client.post(f"/api/v1/applications/draft/{job_id}",
                               headers=h).status_code == 202
        clock = Clock()
        a = AIDrainer(clock=clock)
        claimed = a.claim()
        assert claimed is not None
        job_id_a, token_a, _ = claimed
        with pytest.raises(_Killed):
            a.run_job(job_id_a, token_a)  # dies mid-call, lease held

        b = AIDrainer(clock=clock)
        assert b.drain_once() == ["done", "done"], "the live jobs run; the leased one waits"
        clock.advance(settings.AI_JOB_LEASE_SECONDS + 1)
        assert b.drain_once() == ["done"], "the dead drainer's job is reclaimed"

        for uid, job_id, _h in users:
            rows = _draft(uid, job_id)
            assert len(rows) == 1 and rows[0].status == "ready"
            (job,) = _jobs(uid, "tailor")
            assert job.status == "done"
        # the zombie wakes up: its token is stale, nothing is written
        assert a.run_job(job_id_a, token_a) == "lost"

    def test_lost_lease_drops_the_result(self, client, monkeypatch):
        """Exactly-once: a drainer whose lease was taken over commits
        nothing — the CAS on lease_token is the only result writer."""
        uid, job_id, h = _user(client)
        clock = Clock()
        b = AIDrainer(clock=clock)
        holder = {}

        def take_over(kind, prompt):
            if kind == "tailor" and "b_done" not in holder:
                holder["b_done"] = True
                clock.advance(settings.AI_JOB_LEASE_SECONDS + 1)
                # B reclaims the expired lease and finishes first
                assert b.drain_once() == ["done"]

        fake = FakeAI(on_call=take_over)
        _wire(monkeypatch, fake, judge=False)
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        a = AIDrainer(clock=clock)
        jid, token, _ = a.claim()
        assert a.run_job(jid, token) == "lost"
        (job,) = _jobs(uid, "tailor")
        assert job.status == "done" and job.attempts == 2
        rows = _draft(uid, job_id)
        assert len(rows) == 1 and rows[0].status == "ready"
        tailors = [p for k, p in fake.answered if k == "tailor"]
        assert len(tailors) == 2, "at-least-once call (the honest boundary)"


# =====================================================================
# T2c — idempotency
# =====================================================================


class TestIdempotency:
    def test_prepare_twice_one_job_one_draft(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(), judge=False)
        r1 = client.post(f"/api/v1/applications/draft/{job_id}", headers=h).json()
        r2 = client.post(f"/api/v1/applications/draft/{job_id}", headers=h).json()
        assert r1["id"] == r2["id"] and r1["queue"]["id"] == r2["queue"]["id"]
        assert len(_jobs(uid, "tailor")) == 1
        # force (Try again) on a still-queued draft is the same job too
        r3 = client.post(f"/api/v1/applications/draft/{job_id}?force=true", headers=h).json()
        assert r3["queue"]["id"] == r1["queue"]["id"]
        assert AIDrainer(clock=Clock()).drain_once() == ["done"]
        assert len(_draft(uid, job_id)) == 1

    def test_partial_unique_is_the_backstop(self, client):
        uid, _job_id, _h = _user(client, approved_job=False)
        db = SessionLocal()
        try:
            now = utc_now()
            for _ in range(2):
                db.add(AIJob(user_id=uid, kind="tailor", subject_id=999_001, priority_class=0,
                             status="queued", attempts=0, next_attempt_at=now, enqueued_at=now))
            from sqlalchemy.exc import IntegrityError

            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            # a FINISHED row does not block a new active one
            db.add(AIJob(user_id=uid, kind="tailor", subject_id=999_002, priority_class=0,
                         status="done", attempts=1, next_attempt_at=now, enqueued_at=now))
            db.add(AIJob(user_id=uid, kind="tailor", subject_id=999_002, priority_class=0,
                         status="queued", attempts=0, next_attempt_at=now, enqueued_at=now))
            db.commit()
        finally:
            db.close()

    def test_result_for_deleted_draft_is_dropped(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        fake = FakeAI()
        _wire(monkeypatch, fake, judge=False)
        draft_id = client.post(f"/api/v1/applications/draft/{job_id}", headers=h).json()["id"]
        db = SessionLocal()
        try:
            db.query(ApplicationDraft).filter(ApplicationDraft.id == draft_id).delete()
            db.commit()
        finally:
            db.close()
        assert AIDrainer(clock=Clock()).drain_once() == ["dropped"]
        assert fake.calls == [], "a dropped job must not spend a provider call"
        assert _draft(uid, job_id) == []

    def test_result_for_erased_user_is_dropped(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        fake = FakeAI()
        _wire(monkeypatch, fake, judge=False)
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        db = SessionLocal()
        try:
            db.query(UserModel).filter(UserModel.id == uid).update({"is_active": False})
            db.commit()
        finally:
            db.close()
        assert AIDrainer(clock=Clock()).drain_once() == ["dropped"]
        assert fake.calls == []

    def test_account_deletion_takes_queued_jobs(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(), judge=False)
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        assert _jobs(uid)
        r = client.request("DELETE", "/api/v1/account/delete", headers=h)
        assert r.status_code == 200, r.text
        assert _jobs(uid) == [], "erasure left queued AI work behind"


# =====================================================================
# T2 — interactive before background; the reserve
# =====================================================================


class _RecordingExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append(args)


def _enqueue_matches(uid, n):
    db = SessionLocal()
    try:
        now = utc_now() - timedelta(minutes=5)  # older than the tailor
        for i in range(n):
            db.add(AIJob(user_id=uid, kind="match", subject_id=800_000 + i,
                         priority_class=ai_queue.PRIORITY_BACKLOG, status="queued",
                         attempts=0, next_attempt_at=now, enqueued_at=now))
        db.commit()
    finally:
        db.close()


class TestPriority:
    def test_tailor_behind_50_matches_is_claimed_next(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(), judge=False)
        _enqueue_matches(uid, 50)
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        jid, _tok, background = AIDrainer(clock=Clock()).claim()
        db = SessionLocal()
        try:
            assert db.get(AIJob, jid).kind == "tailor" and background is False
        finally:
            db.close()

    def test_full_background_leaves_the_reserved_slots(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(), judge=False)
        _enqueue_matches(uid, 50)
        controller = ConcurrencyController(start=4, maximum=8, reserve=2, clock=Clock())
        d = AIDrainer(controller=controller, clock=Clock())
        d._executor = _RecordingExecutor()
        assert d.fill() == 2, "matches may hold at most C - reserve = 2 of 4 slots"
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        assert d.fill() == 1, "a user's tailor must get a reserved slot at once"
        db = SessionLocal()
        try:
            kinds = [db.get(AIJob, args[0]).kind for args in d._executor.submitted]
        finally:
            db.close()
        assert kinds == ["match", "match", "tailor"]


# =====================================================================
# T2d — extract + suggest queued under 429
# =====================================================================


def _pdf_bytes(text: str) -> bytes:
    return b"%PDF-1.4 " + text.encode("utf-8")


@pytest.fixture()
def plain_pdf(monkeypatch):
    """Parsing is not under test here: the 'PDF' carries its text."""
    from app.services.file_service import FileService

    monkeypatch.setattr(FileService, "validate_pdf", staticmethod(lambda c: None))
    monkeypatch.setattr(FileService, "extract_text_from_pdf",
                        staticmethod(lambda c: c[len(b"%PDF-1.4 "):].decode("utf-8")))


class TestExtractAndSuggestQueued:
    def test_upload_under_429_saves_cv_and_fills_later(self, client, monkeypatch, plain_pdf):
        uid, _job, h = _user(client, cv="", approved_job=False)
        fake = FakeAI(fail_first=3, retry_after=2)
        _wire(monkeypatch, fake)
        cv = "Carla Holm. QA Engineer, Ericsson, Lund 2017 to 2024. Tested 5G software."
        r = client.post("/api/v1/profile/upload", headers=h,
                        files={"file": ("carla.pdf", _pdf_bytes(cv), "application/pdf")})
        assert r.status_code == 202, r.text
        assert r.json()["extraction"]["status"] == "queued"
        clock = Clock()
        drainer = AIDrainer(clock=clock)
        drainer.drain_once()
        me = client.get("/api/v1/profile/me", headers=h).json()
        assert me["extraction"]["status"] == "queued", "429 must keep it queued"
        assert me["cv_file_name"] == "carla.pdf", "the CV itself is saved at once"
        _drain_until(drainer, clock, lambda: _jobs(uid, "extract")[-1].status == "done")
        me = client.get("/api/v1/profile/me", headers=h).json()
        assert me["extraction"] is None
        assert me["full_name"] == "Carla Holm" and me["professional_title"] == "QA Engineer"
        assert all("Carla Holm" in p for k, p in fake.calls if k == "extract")

    def test_reupload_while_running_reruns_on_the_new_cv(self, client, monkeypatch, plain_pdf):
        uid, _job, h = _user(client, cv="", approved_job=False)
        state = {}

        def reupload_mid_call(kind, prompt):
            if kind == "extract" and "done" not in state:
                state["done"] = True
                db = SessionLocal()
                try:
                    p = db.query(Profile).filter(Profile.user_id == uid).first()
                    p.cv_text = "Dana Ek. Designer, IKEA, Malmö 2020 to 2024."
                    db.commit()
                finally:
                    db.close()

        fake = FakeAI(on_call=reupload_mid_call)
        _wire(monkeypatch, fake)
        client.post("/api/v1/profile/upload", headers=h, files={
            "file": ("x.pdf", _pdf_bytes("Old Name. Clerk, Shop, Lund 2010."), "application/pdf")})
        assert AIDrainer(clock=Clock()).drain_once() == ["requeued", "done"]
        me = client.get("/api/v1/profile/me", headers=h).json()
        assert me["full_name"] == "Dana Ek", "fields from the replaced CV were applied"

    def test_suggest_queued_then_result(self, client, monkeypatch):
        uid, _job, h = _user(client, approved_job=False)
        fake = FakeAI(fail_first=2, retry_after=1)
        _wire(monkeypatch, fake)
        r = client.post("/api/v1/profile/suggest-queries?country=SE&mode=field", headers=h)
        assert r.status_code == 202, r.text
        job = r.json()["job"]
        assert job["status"] == "queued" and job["kind"] == "suggest"
        clock = Clock()
        drainer = AIDrainer(clock=clock)
        drainer.drain_once()
        assert client.get(f"/api/v1/ai-jobs/{job['id']}", headers=h).json()["status"] == "queued"
        _drain_until(drainer, clock, lambda: _jobs(uid, "suggest")[-1].status == "done")
        view = client.get(f"/api/v1/ai-jobs/{job['id']}", headers=h).json()
        assert view["status"] == "done"
        assert view["result"]["from_your_experience"] == ["Backend developer", "Python developer"]
        assert view["result"]["mode"] == "field"
        assert all("Alice Berg" in p for k, p in fake.calls if k == "suggest")

    def test_ai_job_status_is_owner_only(self, client, monkeypatch):
        _uid, _job, h = _user(client, approved_job=False)
        _wire(monkeypatch, FakeAI())
        job = client.post("/api/v1/profile/suggest-queries?country=SE", headers=h).json()["job"]
        _uid2, _job2, h2 = _user(client, cv=BOB_CV, approved_job=False)
        assert client.get(f"/api/v1/ai-jobs/{job['id']}", headers=h2).status_code == 404


# =====================================================================
# Recheck + attest's final check ride the queue too
# =====================================================================


class TestRecheckQueued:
    def _blocked(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        fake = FakeAI(judge_reply=json.dumps(
            {"unsupported": [{"claim": "AWS Certified", "why": "not in CV"}]}))
        _wire(monkeypatch, fake)
        did = client.post(f"/api/v1/applications/draft/{job_id}", headers=h).json()["id"]
        assert AIDrainer(clock=Clock()).drain_once() == ["done"]
        return uid, did, h, fake

    def test_recheck_under_429_stays_queued_then_recovers(self, client, monkeypatch):
        uid, did, h, fake = self._blocked(client, monkeypatch)
        fake.judge_reply = '{"unsupported": []}'
        fake.fail_first = len(fake.calls) + 2  # the next two calls 429
        r = client.post(f"/api/v1/applications/draft/{did}/recheck", headers=h)
        assert r.status_code == 202, r.text
        assert r.json()["queue"]["kind"] == "recheck"
        assert r.json()["status"] == "failed", "the recovery panel stays while it waits"
        clock = Clock()
        _drain_until(AIDrainer(clock=clock), clock,
                     lambda: _jobs(uid, "recheck")[-1].status == "done")
        d = client.get(f"/api/v1/applications/draft/{did}", headers=h).json()
        assert d["status"] == "ready" and d["fabrication_blocked"] is True
        assert d["queue"] is None

    def test_attest_last_claim_queues_the_final_check(self, client, monkeypatch):
        uid, did, h, fake = self._blocked(client, monkeypatch)
        fake.judge_reply = '{"unsupported": []}'
        before = len(fake.calls)
        r = client.post(f"/api/v1/applications/draft/{did}/attest", headers=h,
                        json={"claim": "AWS Certified", "save_to_profile": False})
        assert r.status_code == 202, r.text
        assert len(fake.calls) == before, "the confirm made an inline judge call"
        assert AIDrainer(clock=Clock()).drain_once() == ["done"]
        d = client.get(f"/api/v1/applications/draft/{did}", headers=h).json()
        assert d["status"] == "ready"


# =====================================================================
# Outbound identity under the queue (TestOutboundIdentity pattern)
# =====================================================================


class TestOutboundIdentityQueued:
    def test_each_users_cv_reaches_only_their_call(self, client, monkeypatch):
        a_uid, a_job, a_h = _user(client, ALICE_CV)
        b_uid, b_job, b_h = _user(client, BOB_CV)
        fake = FakeAI()
        _wire(monkeypatch, fake, judge=False)
        client.post(f"/api/v1/applications/draft/{a_job}", headers=a_h)
        client.post(f"/api/v1/applications/draft/{b_job}", headers=b_h)
        assert AIDrainer(clock=Clock()).drain_once() == ["done", "done"]
        (a_draft,) = _draft(a_uid, a_job)
        (b_draft,) = _draft(b_uid, b_job)
        assert "Alice Berg" in a_draft.cover_letter and "Bob" not in a_draft.cover_letter
        assert "Bob Nilsson" in b_draft.cover_letter and "Alice" not in b_draft.cover_letter
        prompts = [p for k, p in fake.calls if k == "tailor"]
        assert sum("Alice Berg" in p and "Bob Nilsson" not in p for p in prompts) == 1
        assert sum("Bob Nilsson" in p and "Alice Berg" not in p for p in prompts) == 1
        db = SessionLocal()
        try:
            for uid in (a_uid, b_uid):
                rows = db.query(AIUsage).filter(AIUsage.user_id == uid,
                                                AIUsage.kind == "tailor").all()
                assert len(rows) == 1, "each tailor call billed to its own user"
        finally:
            db.close()


# =====================================================================
# T3 — Retry-After / AIMD / quota pause
# =====================================================================


class TestLoadSignals:
    def test_classify_retry_after_and_codes(self):
        from app.services.ai_service import classify_load_error

        e = classify_load_error(rate_limit_error("1302", 3))
        assert e.code == "1302" and e.delay_s == 3 and not e.quota
        q = classify_load_error(rate_limit_error(
            "1316", None, "Usage limit reached, will reset at 2099-01-01 08:00:00"))
        assert q.quota and q.delay_s == 6 * 3600, "reset far away clamps to 6h"
        a = classify_load_error(rate_limit_error("1313", None, "Fair Usage Policy"))
        assert a.account and not a.quota
        assert classify_load_error(ValueError("x")) is None

    def test_aimd_halves_on_429_and_grows_back(self):
        clock = Clock()
        c = ConcurrencyController(start=4, maximum=8, reserve=2, clock=clock)
        from app.services.ai_service import classify_load_error

        c.on_load(classify_load_error(rate_limit_error("1302", 1)))
        assert c.limit == 2
        c.tick()
        assert c.limit == 2, "no increase inside 60s of a 429"
        clock.advance(61)
        c.tick()
        assert c.limit == 3
        clock.advance(61)
        c.tick()
        assert c.limit == 4

    def test_quota_window_pauses_background_and_shows_paused_copy(self, client, monkeypatch):
        uid, job_id, h = _user(client)
        clock = Clock()
        fake = FakeAI(quota_until=clock() + timedelta(minutes=30), clock=clock)
        _wire(monkeypatch, fake, judge=False)
        client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
        controller = ConcurrencyController(start=4, maximum=8, reserve=2, clock=clock)
        d = AIDrainer(controller=controller, clock=clock)
        assert d.drain_once() == ["requeued"]
        assert not controller.background_allowed(), "a quota window pauses match claims"
        job = _jobs(uid, "tailor")[0]
        assert job.status == "queued" and job.last_error_code == "quota:1316"
        view = client.get("/api/v1/applications/drafts", headers=h).json()[0]["queue"]
        assert view["paused_until"] is not None, "interactive copy says 'paused until'"
        clock.advance(31 * 60)
        assert controller.background_allowed()
        assert d.drain_once() == ["done"]


# =====================================================================
# Production wiring: AI_DRAINER=api runs a real thread, woken on enqueue
# =====================================================================


class TestDrainerThread:
    def test_thread_drains_without_a_poll(self, client, monkeypatch):
        import time

        uid, job_id, h = _user(client)
        _wire(monkeypatch, FakeAI(), judge=False)
        monkeypatch.setattr(settings, "AI_DRAINER", "api")
        ai_queue.start_drainer("api")
        try:
            client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
            deadline = time.time() + 10
            while time.time() < deadline and _draft(uid, job_id)[0].status != "ready":
                time.sleep(0.05)
        finally:
            ai_queue.stop_drainer()
        assert _draft(uid, job_id)[0].status == "ready"
