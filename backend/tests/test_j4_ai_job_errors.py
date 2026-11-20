"""J-4 — tailoring and re-check errors say what failed and what to do,
inline and queued.

Both surfaces used to paste the exception's own words into the string
the user reads: a failed tailoring became ``Tailoring failed:
ValueError: Malformed tailoring response: ...`` on the draft row (the
inline arm) or ``Tailoring failed: {exc}`` (the queued arm), and a
failed re-check became ``Re-check failed: {type}: {text}``. None of
that tells the user the one thing that helps — press Try again (a
tailoring is pure AI output; the failure is the provider's) or press
Check again (their text is already saved).

test_ac_1  a failed tailoring sets draft.error to exactly
           'Tailoring failed. Press Try again.' on the inline arm
           (create_draft_for_job — including its AIBusyError branch)
           and the queued arm (ai_job_handlers.fail_tailor through the
           real drainer; the same string is the AIJob error job_view
           serves). Both arms read ONE shared definition: re-pointing
           it moves every arm. The precondition failures (no CV, AI
           service not configured) keep their specific messages. The
           exception's own text and type are logged server-side with
           the traceback and appear in no user-facing string.
test_ac_2  a failed re-check sets the user-facing error to exactly
           'Re-check failed. Your text is saved. Press Check again.' on
           the inline arms (recheck_draft and attest_claim's final
           check) and the queued arm (fail_recheck — the same string is
           the AIJob error GET /api/v1/ai-jobs/{id} returns, and the
           draft shows it via draft.error). The no-CV precondition keeps
           its message. Same shared-definition and server-side-log
           rules.

Every expected string below is a literal from the approved contract,
never a recomputation of the services' wording.
"""

import json
import logging
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.timeutil import utc_now
from app.main import app
from app.models import AIJob, ApplicationDraft, JobPosting, MatchResult, Profile
from app.services import ai_queue
from app.services.ai_queue import AIDrainer
from tests.auth_helpers import get_or_create_user, mint_token
from tests.fakes.fake_ai import FakeAI, fake_service

# The contract's literals — the wording is the contract, not this
# module's opinion.
TAILORING_FAILED = "Tailoring failed. Press Try again."
RECHECK_FAILED = "Re-check failed. Your text is saved. Press Check again."

# The precondition messages that must SURVIVE this change (they name an
# action only the user can take; the shared string would hide it).
NO_CV = "Upload your CV before preparing applications"
NO_CV_RECHECK = "No CV on file for this account"
AI_OFF = "The AI service is not configured — nothing was generated."
AI_OFF_INLINE = "GLM_API_KEY not configured — cannot tailor applications"

CV = ("Alice Berg. Backend Developer, Klarna, Stockholm 2019 to 2024. "
      "Built payment APIs in Python.")

DRAFT_LOGGER = "app.services.draft_service"
QUEUE_LOGGER = "app.services.ai_queue"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # lifespan -> init_db -> alembic head
        yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def queue_off(monkeypatch):
    """This module drives the drainer by hand; nothing may be left
    pending for another test (or module) to claim."""
    monkeypatch.setattr(settings, "AI_DRAINER", "off")
    db = SessionLocal()
    try:
        db.query(AIJob).filter(AIJob.status.in_(("queued", "running"))).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(AIJob).filter(AIJob.status.in_(("queued", "running"))).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def db(client):
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


class Clock:
    """Virtual time for the drainer (a beat ahead: rows enqueued 'now'
    by a request are already due)."""

    def __init__(self):
        self.now = utc_now() + timedelta(seconds=1)

    def __call__(self):
        return self.now


def _wire(monkeypatch, fake: FakeAI, judge: bool = True):
    """Every AI seam the inline paths and the drained handlers use ->
    the fake (same seams the queue suite patches)."""
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


def _user(client, cv=CV):
    """A signed-in user with a CV and an approved match.
    Returns (uid, job_id, headers)."""
    email = f"j4-{uuid.uuid4().hex[:8]}@test.example"
    uid = get_or_create_user(email)
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = cv
        p.full_name = cv.split(".")[0]
        job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                         title="Developer", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:8]}", status="matched",
                         description="A Python backend role.")
        db.add(job)
        db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        job_id = job.id  # read while the session is open
        db.commit()
    finally:
        db.close()
    return uid, job_id, {"Authorization": f"Bearer {mint_token(uid, email)}"}


def _draft(db, uid, job_id):
    return (
        db.query(ApplicationDraft)
        .filter(ApplicationDraft.user_id == uid,
                ApplicationDraft.job_id == job_id)
        .first()
    )


def _jobs(uid, kind=None):
    db = SessionLocal()
    try:
        q = db.query(AIJob).filter(AIJob.user_id == uid)
        if kind:
            q = q.filter(AIJob.kind == kind)
        return q.order_by(AIJob.id).all()
    finally:
        db.close()


def _malformed(fake: FakeAI) -> FakeAI:
    """Make the fake answer every call with unparseable text — the real
    production failure (ai_service raises ValueError('Malformed
    tailoring response: ...') after its format re-asks)."""
    fake._answer = lambda kind, prompt: "not json at all"
    return fake


def _assert_logged_with_traceback(caplog, *needles):
    """The failure's cause is diagnosable server-side: an ERROR record
    carrying a traceback (whose rendered text contains every needle —
    the exception's type and its own text)."""
    with_tb = [r for r in caplog.records
               if r.levelno >= logging.ERROR and r.exc_info]
    assert with_tb, f"no ERROR record with a traceback: {caplog.text}"
    assert all(n in caplog.text for n in needles), (
        f"missing { needles!r} in the server log:\n{caplog.text}")


# =====================================================================
# AC-1 — a failed tailoring says exactly one thing, inline and queued
# =====================================================================


def test_ac_1(client, db, monkeypatch, caplog):
    """AC-1: every failed tailoring sets draft.error to exactly
    'Tailoring failed. Press Try again.'; the cause stays in the log."""
    from app.services import draft_service
    from app.services.draft_service import create_draft_for_job, DraftError

    caplog.set_level(logging.ERROR)

    # --- inline arm: the provider's malformed output ------------------
    # The ValueError ai_service itself raises after its format re-asks —
    # the exact text that used to land in draft.error verbatim.
    uid, job_id, _h = _user(client)
    fake = _malformed(FakeAI())
    _wire(monkeypatch, fake)
    draft = create_draft_for_job(
        db, db.get(JobPosting, job_id),
        profile=db.query(Profile).filter(Profile.user_id == uid).first(),
        user_id=uid)
    assert draft.status == "failed", draft.error
    assert draft.error == TAILORING_FAILED, draft.error
    assert draft.error == draft_service.TAILORING_FAILED_MESSAGE, (
        "the inline arm must read the shared definition")
    _assert_logged_with_traceback(
        caplog, "Malformed tailoring response", "ValueError")

    # --- inline arm: the provider stayed busy (AIBusyError) -----------
    # The old branch pasted AIBusyError's own text here; the contract
    # gives it the same shared string.
    uid_b, job_b, _h_b = _user(client)
    _wire(monkeypatch, FakeAI(fail_first=99))  # every call 429s
    busy = create_draft_for_job(
        db, db.get(JobPosting, job_b),
        profile=db.query(Profile).filter(Profile.user_id == uid_b).first(),
        user_id=uid_b)
    assert busy.status == "failed", busy.error
    assert busy.error == TAILORING_FAILED, busy.error
    _assert_logged_with_traceback(
        caplog, "AIBusyError", "The AI service is busy right now")

    # --- queued arm: ai_job_handlers.fail_tailor through the drainer --
    uid_q, job_q, h_q = _user(client)
    _wire(monkeypatch, _malformed(FakeAI()))
    r = client.post(f"/api/v1/applications/draft/{job_q}", headers=h_q)
    assert r.status_code == 202, r.text
    assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
    d_q = _draft(db, uid_q, job_q)
    assert d_q.status == "failed", d_q.error
    assert d_q.error == TAILORING_FAILED, d_q.error
    (job_row,) = _jobs(uid_q, "tailor")
    assert job_row.status == "failed"
    assert job_row.error == TAILORING_FAILED, job_row.error
    view = client.get(f"/api/v1/ai-jobs/{job_row.id}", headers=h_q).json()
    assert view["status"] == "failed" and view["error"] == TAILORING_FAILED, view
    _assert_logged_with_traceback(
        caplog, "Malformed tailoring response", "ValueError")

    # --- one shared definition: re-pointing it moves every arm --------
    sentinel = "J4-TAILORING-SENTINEL"
    with monkeypatch.context() as mp:
        mp.setattr(draft_service, "TAILORING_FAILED_MESSAGE", sentinel)
        uid_s, job_s, h_s = _user(client)
        _wire(mp, _malformed(FakeAI()))
        inline_s = create_draft_for_job(
            db, db.get(JobPosting, job_s),
            profile=db.query(Profile).filter(Profile.user_id == uid_s).first(),
            user_id=uid_s)
        assert inline_s.error == sentinel, inline_s.error
        uid_q2, job_q2, h_q2 = _user(client)
        _wire(mp, _malformed(FakeAI()))
        client.post(f"/api/v1/applications/draft/{job_q2}", headers=h_q2)
        assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
        assert _draft(db, uid_q2, job_q2).error == sentinel
        assert _jobs(uid_q2, "tailor")[0].error == sentinel

    # --- the precondition failures keep their specific messages -------
    # Inline (draft_service's tailoring path): no CV / no AI service.
    uid_p, job_p, _h_p = _user(client, cv="")
    with pytest.raises(DraftError, match="Upload your CV before preparing"):
        create_draft_for_job(
            db, db.get(JobPosting, job_p),
            profile=db.query(Profile).filter(Profile.user_id == uid_p).first(),
            user_id=uid_p)
    uid_a, job_a, _h_a = _user(client)
    with monkeypatch.context() as mp:
        mp.setattr(draft_service, "ai_service_available", lambda: False)
        with pytest.raises(DraftError, match="GLM_API_KEY not configured"):
            create_draft_for_job(
                db, db.get(JobPosting, job_a),
                profile=db.query(Profile).filter(
                    Profile.user_id == uid_a).first(),
                user_id=uid_a)

    # Queued (run_tailor): no CV at drain time / AI service off. The
    # draft enqueued while the CV was there; the precondition broke
    # before the drainer ran.
    _wire(monkeypatch, FakeAI(), judge=False)
    uid_n, job_n, h_n = _user(client)
    client.post(f"/api/v1/applications/draft/{job_n}", headers=h_n)
    p_n = db.query(Profile).filter(Profile.user_id == uid_n).first()
    p_n.cv_text = ""
    db.commit()
    assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
    d_n = _draft(db, uid_n, job_n)
    assert d_n.error == NO_CV, d_n.error
    assert _jobs(uid_n, "tailor")[0].error == NO_CV

    uid_o, job_o, h_o = _user(client)
    client.post(f"/api/v1/applications/draft/{job_o}", headers=h_o)
    with monkeypatch.context() as mp:
        mp.setattr(draft_service, "ai_service_available", lambda: False)
        assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
    d_o = _draft(db, uid_o, job_o)
    assert d_o.error == AI_OFF, d_o.error
    assert _jobs(uid_o, "tailor")[0].error == AI_OFF


# =====================================================================
# AC-2 — a failed re-check says the text is saved and to press Check
# again, inline and queued
# =====================================================================


def _judge_boom(kind, prompt):
    """The fake provider's judge call dies — the transport failure whose
    type and text must stay server-side."""
    if kind == "judge":
        raise RuntimeError("judge transport exploded (J-4 fixture)")


def _blocked_draft(client, monkeypatch):
    """A fabrication-blocked draft — exactly what Check again takes as
    input. Returns (uid, job_id, headers, draft_id, fake)."""
    uid, job_id, h = _user(client)
    fake = FakeAI(judge_reply=json.dumps(
        {"unsupported": [{"claim": "AWS Certified", "why": "not in CV"}]}))
    _wire(monkeypatch, fake)
    r = client.post(f"/api/v1/applications/draft/{job_id}", headers=h)
    assert r.status_code == 202, r.text
    assert AIDrainer(clock=Clock()).drain_once() == ["done"]
    sdb = SessionLocal()
    try:
        d = (sdb.query(ApplicationDraft)
             .filter(ApplicationDraft.user_id == uid,
                     ApplicationDraft.job_id == job_id).first())
        assert d is not None, "fixture: the Prepare must open a draft"
        assert d.status == "failed" and d.fabrication_blocked, (
            "fixture: the judge must block this draft")
        did = d.id
    finally:
        sdb.close()
    return uid, job_id, h, did, fake


def _profile_in(db, uid):
    return db.query(Profile).filter(Profile.user_id == uid).first()


def test_ac_2(client, db, monkeypatch, caplog):
    """AC-2: every failed re-check says exactly 'Re-check failed. Your
    text is saved. Press Check again.'; the cause stays in the log."""
    from app.services import draft_service
    from app.services.draft_service import attest_claim, recheck_draft, DraftError

    caplog.set_level(logging.ERROR)

    # --- inline arm: recheck_draft ------------------------------------
    uid, _job, h, did, fake = _blocked_draft(client, monkeypatch)
    fake.on_call = _judge_boom
    d1 = db.get(ApplicationDraft, did)
    with pytest.raises(DraftError) as ei:
        recheck_draft(db, d1, profile=_profile_in(db, uid))
    assert str(ei.value) == RECHECK_FAILED, str(ei.value)
    assert d1.error == RECHECK_FAILED, d1.error
    assert d1.status == "failed", "the user's text stays saved, not wiped"
    _assert_logged_with_traceback(
        caplog, "RuntimeError", "judge transport exploded")

    # --- inline arm: attest_claim's final check ------------------------
    uid2, _job2, h2, did2, fake2 = _blocked_draft(client, monkeypatch)
    fake2.on_call = _judge_boom
    d2 = db.get(ApplicationDraft, did2)
    with pytest.raises(DraftError) as ei2:
        attest_claim(db, d2, "AWS Certified", False,
                     profile=_profile_in(db, uid2), user_id=uid2)
    assert str(ei2.value) == RECHECK_FAILED, str(ei2.value)
    assert d2.error == RECHECK_FAILED, d2.error
    assert d2.status == "failed", "the attestation is kept, only the check failed"
    _assert_logged_with_traceback(
        caplog, "RuntimeError", "judge transport exploded")

    # --- queued arm: fail_recheck through the drainer ------------------
    uid3, _job3, h3, did3, fake3 = _blocked_draft(client, monkeypatch)
    fake3.on_call = _judge_boom
    r = client.post(f"/api/v1/applications/draft/{did3}/recheck", headers=h3)
    assert r.status_code == 202, r.text
    qid = r.json()["queue"]["id"]
    assert r.json()["status"] == "failed", "the recovery panel stays while it waits"
    assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
    # the draft shows it via draft.error...
    d_view = client.get(f"/api/v1/applications/draft/{did3}", headers=h3).json()
    assert d_view["status"] == "failed"
    assert d_view["error"] == RECHECK_FAILED, d_view["error"]
    # ...and the same string is the AIJob error the job view returns
    view = client.get(f"/api/v1/ai-jobs/{qid}", headers=h3).json()
    assert view["status"] == "failed", view
    assert view["error"] == RECHECK_FAILED, view["error"]
    assert draft_service.RECHECK_FAILED_MESSAGE == RECHECK_FAILED
    _assert_logged_with_traceback(
        caplog, "RuntimeError", "judge transport exploded")

    # --- one shared definition: re-pointing it moves every arm ---------
    sentinel = "J4-RECHECK-SENTINEL"
    with monkeypatch.context() as mp:
        mp.setattr(draft_service, "RECHECK_FAILED_MESSAGE", sentinel)
        uid_i, _job_i, _h_i, did_i, fake_i = _blocked_draft(client, mp)
        fake_i.on_call = _judge_boom
        d_i = db.get(ApplicationDraft, did_i)
        with pytest.raises(DraftError) as ei_i:
            recheck_draft(db, d_i, profile=_profile_in(db, uid_i))
        assert str(ei_i.value) == sentinel, str(ei_i.value)
        assert d_i.error == sentinel, d_i.error
        uid_q, _job_q, h_q, did_q, fake_q = _blocked_draft(client, mp)
        fake_q.on_call = _judge_boom
        client.post(f"/api/v1/applications/draft/{did_q}/recheck", headers=h_q)
        assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
        q_view = client.get(
            f"/api/v1/ai-jobs/{_jobs(uid_q, 'recheck')[0].id}",
            headers=h_q).json()
        assert q_view["error"] == sentinel, q_view

    # --- the precondition failure (no CV) keeps its message ------------
    uid_n, _job_n, h_n, did_n, _fake_n = _blocked_draft(client, monkeypatch)
    sdb = SessionLocal()
    try:
        p = sdb.query(Profile).filter(Profile.user_id == uid_n).first()
        p.cv_text = ""  # the CV disappears before the check runs
        sdb.commit()
    finally:
        sdb.close()
    # at the route: the 400 says what is missing...
    r = client.post(f"/api/v1/applications/draft/{did_n}/recheck", headers=h_n)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == NO_CV_RECHECK, r.json()["detail"]
    # ...and in the drainer: the job fails with that same message, not
    # the shared retry string (retrying could not help — there is no CV)
    uid_m, _job_m, h_m, did_m, _fake_m = _blocked_draft(client, monkeypatch)
    r = client.post(f"/api/v1/applications/draft/{did_m}/recheck", headers=h_m)
    qid_m = r.json()["queue"]["id"]
    sdb = SessionLocal()
    try:
        p = sdb.query(Profile).filter(Profile.user_id == uid_m).first()
        p.cv_text = ""
        sdb.commit()
    finally:
        sdb.close()
    assert AIDrainer(clock=Clock()).drain_once() == ["failed"]
    view_m = client.get(f"/api/v1/ai-jobs/{qid_m}", headers=h_m).json()
    assert view_m["error"] == NO_CV_RECHECK, view_m
    d_m = client.get(f"/api/v1/applications/draft/{did_m}", headers=h_m).json()
    assert "Blocked by the fabrication guard" in (d_m["error"] or ""), (
        "the precondition failure must not overwrite the block message" )
