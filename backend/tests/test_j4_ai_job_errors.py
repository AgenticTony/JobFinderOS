"""J-4 — a failed tailoring or re-check says what failed and what to do.

Every arm of these two failures used to paste the exception's own words
into the string the user reads: an inline tailoring failure became
``Tailoring failed: ValueError: Malformed tailoring response: ...`` on
draft.error, the queued arm became ``Tailoring failed: {exc}``, and the
re-check arms became ``Re-check failed: {type}: {exc}``. None of that is
actionable — and it leaks provider internals to the browser.

The contract pins ONE exact user-facing string per failure, taken from
ONE shared definition by every arm:

test_ac_1  a failed tailoring — inline (create_draft_for_job, both the
           generic and the AIBusyError branch) and queued
           (ai_job_handlers.fail_tailor) — sets draft.error to exactly
           'Tailoring failed. Press Try again.' (retrying is right: the
           failure is the AI provider). The precondition failures (no
           CV, AI service not configured) keep their own specific
           messages. The exception's own text and type are in the
           server log, with a traceback, and in no user-facing string.
test_ac_2  a failed re-check — inline (recheck_draft and attest_claim's
           final check) and queued (ai_job_handlers.fail_recheck, whose
           string is the AIJob error GET /api/v1/ai-jobs/{id} returns
           and the draft shows via draft.error) — sets the error to
           exactly 'Re-check failed. Your text is saved. Press Check
           again.' The no-CV precondition keeps its own message.

Every expected string below is a literal from the approved contract,
never a recomputation of the services' wording.
"""

import json
import logging
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models import (
    AIJob,
    ApplicationDraft,
    JobPosting,
    MatchResult,
    Profile,
)
from app.services.ai_queue import AIDrainer
from tests.fakes.fake_ai import FakeAI, fake_service

# The contract's literals — the wording is the contract, not this
# module's opinion.
TAILOR_FAILED = "Tailoring failed. Press Try again."
RECHECK_FAILED = "Re-check failed. Your text is saved. Press Check again."

# Precondition failures that keep their own specific wording (they say
# what the USER must do, so retrying alone would not help).
NO_CV = "Upload your CV before preparing applications"
NO_KEY_INLINE = "GLM_API_KEY not configured — cannot tailor applications"
NO_KEY_QUEUED = "The AI service is not configured — nothing was generated."
NO_CV_RECHECK = "No CV on file for this account"

DRAFT_LOGGER = "app.services.draft_service"
QUEUE_LOGGER = "app.services.ai_queue"  # the drainer's own failure record

CV = ("Alice Berg. Backend Developer, Klarna, Stockholm 2019 to 2024. "
      "Built payment APIs in Python.")
# The user's edit of the blocked package: every fact in it is a CV line,
# so Layer A is clean and the re-check reaches the judge — a scripted
# judge failure is what the arms below observe.
CLEAN_LETTER = "I built payment APIs in Python at Klarna."
CLEAN_CV = "Alice Berg. Backend Developer, Klarna, Stockholm 2019 to 2024."

# The internals that must stay server-side: real provider-shaped texts,
# not placeholders, so a leak into any user-facing string is visible.
TAILOR_EXC_TEXT = "Malformed tailoring response: cover_letter/tailored_cv missing or empty"
BUSY_EXC_TEXT = ("The AI service is busy right now (rate limited). "
                 "Nothing was generated — try again in a minute.")
GENERIC_EXC_TEXT = "provider returned HTTP 500 with a truncated body"
JUDGE_EXC_TEXT = "Unparseable JSON from fabrication judge"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # lifespan -> init_db -> alembic head
        yield c
    engine.dispose()


@pytest.fixture(autouse=True)
def queue_off(monkeypatch):
    """These tests drive the drainer by hand; nothing may be left
    pending for the next test (or module) to claim."""
    monkeypatch.setattr(settings, "AI_DRAINER", "off")
    _purge_pending()
    yield
    _purge_pending()


@pytest.fixture()
def db(client):
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _purge_pending():
    db = SessionLocal()
    try:
        db.query(AIJob).filter(AIJob.status.in_(("queued", "running"))).delete(
            synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _user(db, cv=CV, *, approved_job=True):
    """A signed-in user with a CV and (optionally) an approved match:
    (uid, job, profile, auth headers)."""
    from tests.auth_helpers import get_or_create_user, mint_token

    email = f"j4-{uuid.uuid4().hex[:8]}@test.example"
    uid = get_or_create_user(email)
    job = None
    if approved_job:
        job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                         title="Developer", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:8]}", status="matched",
                         description="A Python backend role.")
        db.add(job)
        db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()
    profile = db.query(Profile).filter(Profile.user_id == uid).first()
    profile.cv_text = cv
    profile.full_name = cv.split(".")[0]
    db.commit()
    return uid, job, profile, {"Authorization": f"Bearer {mint_token(uid, email)}"}


def _raising_ai(monkeypatch, *, tailor=None, judge=None, available=True):
    """Point draft_service's AI seams at a service whose calls raise —
    the seams the suite already patches (no network, no key)."""
    from app.services import draft_service

    svc = SimpleNamespace(tailor_application=tailor, judge_fabrication=judge)
    monkeypatch.setattr(draft_service, "get_ai_service", lambda: svc)
    monkeypatch.setattr(draft_service, "ai_service_available", lambda: available)
    return svc


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


def _draft(uid, job_id=None):
    db = SessionLocal()
    try:
        q = db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid)
        if job_id is not None:
            q = q.filter(ApplicationDraft.job_id == job_id)
        return q.all()
    finally:
        db.close()


def _jobs(uid, kind):
    db = SessionLocal()
    try:
        return (db.query(AIJob)
                .filter(AIJob.user_id == uid, AIJob.kind == kind)
                .order_by(AIJob.id).all())
    finally:
        db.close()


def _set_cv(uid, cv_text):
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = cv_text
        db.commit()
    finally:
        db.close()


def _assert_logged(caplog, logger_name, *needles):
    """The exception's own text (and type) landed in the server log with
    a traceback attached — the operator's half of the contract."""
    records = [
        r
        for r in caplog.records
        if r.name == logger_name and r.levelno >= logging.ERROR and r.exc_info
    ]
    assert records, f"no ERROR record with a traceback on {logger_name}: {caplog.text}"
    assert any(
        all(n in r.getMessage() for n in needles) for r in records
    ), [r.getMessage() for r in records]


def _assert_traceback_names(caplog, *type_names):
    """The exception's TYPE is in the logged traceback (the queued arms
    log it via the drainer's record, whose message carries only the
    text)."""
    import traceback as tb

    for name in type_names:
        assert any(
            r.exc_info and name in "".join(tb.format_exception(*r.exc_info))
            for r in caplog.records
            if r.levelno >= logging.ERROR
        ), f"{name} appears in no logged traceback: {caplog.text}"


# =====================================================================
# AC-1 — a failed tailoring says what failed and what to do
# =====================================================================


def test_ac_1(client, db, monkeypatch, caplog):
    """AC-1: every arm of a failed tailoring — inline and queued — says
    exactly 'Tailoring failed. Press Try again.'; the exception's own
    words stay in the server log."""
    from app.services import ai_job_handlers, draft_service
    from app.services.ai_service import AIBusyError

    caplog.set_level(logging.ERROR)

    # --- one shared definition, and it IS the contract's string -------
    assert draft_service.TAILORING_FAILED_MESSAGE == TAILOR_FAILED
    assert draft_service.TAILORING_FAILED_MESSAGE is ai_job_handlers.TAILORING_FAILED_MESSAGE, (
        "the inline and queued arms must take the string from ONE shared "
        "definition, not two copies that can drift")

    # --- inline, generic failure --------------------------------------
    uid1, job1, profile1, _h = _user(db)
    _raising_ai(monkeypatch, tailor=lambda **kw: (_ for _ in ()).throw(
        ValueError(GENERIC_EXC_TEXT)))
    d1 = draft_service.create_draft_for_job(db, job1, profile=profile1, user_id=uid1)
    assert d1.status == "failed", "the tailoring failure must fail the draft"
    assert d1.error == TAILOR_FAILED, d1.error
    assert GENERIC_EXC_TEXT not in d1.error and "ValueError" not in d1.error
    _assert_logged(caplog, DRAFT_LOGGER, "ValueError", GENERIC_EXC_TEXT)

    # --- inline, the AIBusyError branch (persistent 429) --------------
    # Same string: retrying is the right advice, and the raw 429 payload
    # stays server-side.
    uid2, job2, profile2, _h = _user(db)
    _raising_ai(monkeypatch, tailor=lambda **kw: (_ for _ in ()).throw(
        AIBusyError(BUSY_EXC_TEXT)))
    d2 = draft_service.create_draft_for_job(db, job2, profile=profile2, user_id=uid2)
    assert d2.status == "failed", d2.error
    assert d2.error == TAILOR_FAILED, d2.error
    assert "busy" not in d2.error.lower() and "1302" not in d2.error
    _assert_logged(caplog, DRAFT_LOGGER, "AIBusyError", BUSY_EXC_TEXT)

    # --- queued: the drainer's fail_tailor ----------------------------
    # A malformed tailoring response (the non-load failure the queue
    # still fails for) after the format re-asks.
    uid3, job3, _p3, h3 = _user(db)
    fake = FakeAI()
    _wire(monkeypatch, fake)
    monkeypatch.setattr(fake, "_answer", lambda kind, prompt: "not json at all")
    r = client.post(f"/api/v1/applications/draft/{job3.id}", headers=h3)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    assert AIDrainer().drain_once() == ["failed"]
    d3 = _draft(uid3, job3.id)[0]
    assert d3.status == "failed", d3.error
    assert d3.error == TAILOR_FAILED, d3.error
    assert TAILOR_EXC_TEXT not in d3.error and "Malformed" not in d3.error
    # The cause is the operator's to read: the drainer logs the exception
    # with its traceback before it hands the job to fail_tailor.
    _assert_logged(caplog, QUEUE_LOGGER, TAILOR_EXC_TEXT)
    _assert_traceback_names(caplog, "ValueError")

    # --- the precondition failures keep their own messages ------------
    # Inline: no CV, and the AI service not configured — both name what
    # the USER must change, so 'Try again' alone would misdirect.
    uid4, job4, profile4, _h = _user(db, cv="")
    with pytest.raises(draft_service.DraftError, match=re.escape(NO_CV)):
        draft_service.create_draft_for_job(db, job4, profile=profile4, user_id=uid4)
    uid4b, job4b, profile4b, _h = _user(db)
    _raising_ai(monkeypatch, tailor=lambda **kw: {}, available=False)
    with pytest.raises(draft_service.DraftError, match=re.escape(NO_KEY_INLINE)):
        draft_service.create_draft_for_job(db, job4b, profile=profile4b, user_id=uid4b)

    # Queued: the same two preconditions, re-checked by the drainer
    # after the request was accepted (the CV was removed / the service
    # reconfigured while the job waited).
    uid5, job5, _p5, h5 = _user(db)
    _wire(monkeypatch, FakeAI())
    assert client.post(f"/api/v1/applications/draft/{job5.id}", headers=h5).status_code == 202
    _set_cv(uid5, "")
    assert AIDrainer().drain_once() == ["failed"]
    d5 = _draft(uid5, job5.id)[0]
    assert d5.status == "failed" and d5.error == NO_CV, d5.error
    assert _jobs(uid5, "tailor")[-1].error == NO_CV

    uid6, job6, _p6, h6 = _user(db)
    _wire(monkeypatch, FakeAI())
    assert client.post(f"/api/v1/applications/draft/{job6.id}", headers=h6).status_code == 202
    monkeypatch.setattr(draft_service, "ai_service_available", lambda: False)
    assert AIDrainer().drain_once() == ["failed"]
    d6 = _draft(uid6, job6.id)[0]
    assert d6.status == "failed" and d6.error == NO_KEY_QUEUED, d6.error
    assert _jobs(uid6, "tailor")[-1].error == NO_KEY_QUEUED


# =====================================================================
# AC-2 — a failed re-check says what failed and what to do
# =====================================================================


def _blocked_draft(client, db, monkeypatch):
    """A guard-blocked draft via the queued path (one judge finding), so
    re-check/attest are valid actions on it: (uid, draft_id, headers, fake)."""
    uid, job, _p, h = _user(db)
    fake = FakeAI(judge_reply=json.dumps(
        {"unsupported": [{"claim": "AWS Certified", "why": "not in CV"}]}))
    _wire(monkeypatch, fake)
    did = client.post(f"/api/v1/applications/draft/{job.id}", headers=h).json()["id"]
    assert AIDrainer().drain_once() == ["done"]
    d = _draft(uid, job.id)[0]
    assert d.status == "failed" and d.fabrication_blocked, d.error
    return uid, job.id, did, h, fake


def _judge_raises(monkeypatch):
    """A guard service whose judge call fails — the transport failure
    both re-check arms must report without leaking its words."""
    from app.services import draft_service

    def _judge(source, document):
        raise ValueError(JUDGE_EXC_TEXT)

    _raising_ai(monkeypatch, judge=_judge)
    monkeypatch.setattr(draft_service.settings, "FABRICATION_JUDGE", "on",
                        raising=False)


def test_ac_2(client, db, monkeypatch, caplog):
    """AC-2: every arm of a failed re-check — inline recheck_draft,
    attest_claim's final check, and the queued fail_recheck (whose
    string is what GET /api/v1/ai-jobs/{id} returns) — says exactly
    'Re-check failed. Your text is saved. Press Check again.'"""
    from app.services import ai_job_handlers, draft_service
    from app.services.draft_service import (
        attest_claim,
        recheck_draft,
        save_draft_edits,
    )

    caplog.set_level(logging.ERROR)

    # --- one shared definition, and it IS the contract's string -------
    assert draft_service.RECHECK_FAILED_MESSAGE == RECHECK_FAILED
    assert draft_service.RECHECK_FAILED_MESSAGE is ai_job_handlers.RECHECK_FAILED_MESSAGE, (
        "the inline and queued arms must take the string from ONE shared "
        "definition, not two copies that can drift")

    # --- inline: recheck_draft on guard-clean text, judge fails --------
    uid1, job1, did1, _h1, _f1 = _blocked_draft(client, db, monkeypatch)
    d1 = db.get(ApplicationDraft, did1)
    save_draft_edits(db, d1, cover_letter=CLEAN_LETTER, tailored_cv=CLEAN_CV)
    _judge_raises(monkeypatch)
    with pytest.raises(draft_service.DraftError) as ei:
        recheck_draft(db, d1, profile=db.query(Profile).filter(
            Profile.user_id == uid1).first())
    assert d1.status == "failed", "a failed check is never a verdict"
    assert d1.error == RECHECK_FAILED, d1.error
    # The 400 the user reads carries the same string, and the user's text
    # survived it.
    assert str(ei.value) == RECHECK_FAILED
    assert d1.cover_letter == CLEAN_LETTER and d1.tailored_cv == CLEAN_CV
    assert "judge" not in d1.error.lower() and JUDGE_EXC_TEXT not in d1.error
    _assert_logged(caplog, DRAFT_LOGGER, "ValueError", JUDGE_EXC_TEXT)

    # --- inline: attest_claim's final check, judge fails ---------------
    uid2, job2, did2, _h2, _f2 = _blocked_draft(client, db, monkeypatch)
    d2 = db.get(ApplicationDraft, did2)
    _judge_raises(monkeypatch)
    with pytest.raises(draft_service.DraftError) as ei2:
        attest_claim(db, d2, "AWS Certified", save_to_profile=False,
                     profile=db.query(Profile).filter(
                         Profile.user_id == uid2).first())
    assert d2.status == "failed", "a failed check is never a verdict"
    assert d2.error == RECHECK_FAILED, d2.error
    assert str(ei2.value) == RECHECK_FAILED
    assert "Unparseable" not in d2.error and "ValueError" not in d2.error
    _assert_logged(caplog, DRAFT_LOGGER, "ValueError", JUDGE_EXC_TEXT)

    # --- queued: fail_recheck, read back through the job endpoint ------
    uid3, job3, did3, h3, fake3 = _blocked_draft(client, db, monkeypatch)
    d3 = db.get(ApplicationDraft, did3)
    save_draft_edits(db, d3, cover_letter=CLEAN_LETTER, tailored_cv=CLEAN_CV)
    r = client.post(f"/api/v1/applications/draft/{did3}/recheck", headers=h3)
    assert r.status_code == 202, r.text
    assert r.json()["queue"]["kind"] == "recheck"
    job_id = r.json()["queue"]["id"]
    # The judge fails while the job runs (the fake answers garbage to the
    # judge call — an unparseable verdict is a transport failure).
    fake3.judge_reply = "not json at all"
    assert AIDrainer().drain_once() == ["failed"]
    d3b = _draft(uid3, job3)[0]
    assert d3b.error == RECHECK_FAILED, d3b.error
    assert JUDGE_EXC_TEXT not in (d3b.error or "")
    view = client.get(f"/api/v1/ai-jobs/{job_id}", headers=h3).json()
    assert view["status"] == "failed"
    assert view["error"] == RECHECK_FAILED, view
    assert _jobs(uid3, "recheck")[-1].error == RECHECK_FAILED
    _assert_logged(caplog, QUEUE_LOGGER, "Unparseable")
    _assert_traceback_names(caplog, "ValueError")

    # --- the precondition failure keeps its own message ----------------
    # An account with no CV cannot re-check at all (the advice is 'upload
    # a CV', not 'press Check again'), so it keeps its own wording — at
    # the request seam (400) and in the drainer's own re-check of it.
    uid4, job4, did4, h4, _f4 = _blocked_draft(client, db, monkeypatch)
    _set_cv(uid4, "")
    r4 = client.post(f"/api/v1/applications/draft/{did4}/recheck", headers=h4)
    assert r4.status_code == 400, r4.text
    assert r4.json()["detail"] == NO_CV_RECHECK, r4.text

    uid5, job5, did5, h5, _f5 = _blocked_draft(client, db, monkeypatch)
    assert client.post(
        f"/api/v1/applications/draft/{did5}/recheck", headers=h5).status_code == 202
    _set_cv(uid5, "")  # the CV is removed while the job waits
    assert AIDrainer().drain_once() == ["failed"]
    view5 = client.get(
        f"/api/v1/ai-jobs/{_jobs(uid5, 'recheck')[-1].id}", headers=h5).json()
    assert view5["error"] == NO_CV_RECHECK, view5
    assert view5["error"] != RECHECK_FAILED
