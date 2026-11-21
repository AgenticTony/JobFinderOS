"""J-8 — the send key covers the whole email, a 409 is never 'not sent',
and no send goes out keyless.

J-6 gave every application email an idempotency key, but the key hashed
only the recipient, subject, body and attachments — the `from` and
`reply_to` the dispatch actually carries were outside it, and the
attachments were hashed in list order. Resend therefore promised
"one key, one payload" over a payload it never saw whole: two emails
that differ only in the sender's name or the reply-to address share a
key, and Resend answers the second one with the first one's verdict.
The same fix closes J-6's last mislabelling: a 409 (Resend's own
idempotency conflict) and every 5xx answer were classified as "the
email was not sent" — a 409 means the request under this key was
ALREADY processed, which is the opposite of not sent.

test_ac_1  the key covers EVERY field of the params dict actually
           passed to resend.Emails.send — from and reply_to included —
           plus every attachment's filename and bytes, hashed in a
           canonical order: changing any one field yields a new key,
           permuting the attachments does not, and an unchanged
           application keeps its key across a real re-render (J-6's
           proof stays green). The key is still the application id plus
           a one-way digest with no personal data in clear text, and
           Resend never sees one key with two different payloads. The
           outcome classification is exact: a ResendError Resend
           ANSWERED with an HTTP 4xx other than 409 is a rejection and
           keeps J-2's "the email was not sent" message; a 409, any
           5xx, the SDK's transport wrapper and any non-ResendError
           leave the outcome unknown with J-6's message, which never
           says "not sent". Both send paths.
test_ac_2  no email the backend sends through resend.Emails.send goes
           out without an idempotency key — both application send paths
           and feedback_service.notify_owner. The owner notification's
           key is a one-way digest of its full params, so the same
           notification retried is delivered once. A resend.Emails.send
           that cannot carry the key (no `options` parameter) is
           detected BEFORE anything is dispatched: the send fails with
           J-2's "the email was not sent" message, the application is
           'failed', and the cause is logged.

Every expected string below is a literal from the approved contract,
never a recomputation of the services' wording.
"""

import base64
import logging
import re
import sys
import time
import uuid

import pytest
import resend as real_resend
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.services import pdf_service
from app.services.draft_service import submit_draft

# The contract's literals — the wording is the contract, not this
# module's opinion.
UNKNOWN_OUTCOME = (
    "We could not confirm whether the email was sent. You can retry it "
    "from this application; an unchanged email is never delivered twice."
)
NOT_SENT = "the email was not sent"
RETRY_IT = "You can retry it from this application."

# A body every render path draws faithfully (pure ascii — the J-1
# fixture's rule, so the PDFs underneath are reproducible).
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

DRAFT_LOGGER = "app.services.draft_service"
APPLY_LOGGER = "app.services.apply_service"


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — the
    # send-flow tests need the tables.
    with TestClient(app) as c:  # lifespan runs init_db -> alembic head
        yield c
    engine.dispose()


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


# ---------------------------------------------------------------------
# fixtures and doubles
# ---------------------------------------------------------------------


def _seed(db, *, body=HEALTHY_BODY, with_draft=True):
    """user + profile + email-publishing job (+ a 'ready' draft), all
    committed — exactly the package the email send paths dispatch. The
    profile stores NO CV file: the original-CV read fails naturally
    unless a test monkeypatch serves bytes."""
    from app.models import ApplicationDraft, JobPosting, Profile
    from app.models import User as UserModel

    uid = uuid.uuid4()
    db.add(
        UserModel(
            id=uid,
            email=f"j8-{uid.hex[:6]}@test.example",
            hashed_password="test-only",
        )
    )
    db.flush()
    profile = Profile(
        user_id=uid,
        is_active=1,
        full_name="J8 Tester",
        cv_text="J8 CV",
        cv_file_name="cv.pdf",
        email="applicant@personal.example",
    )
    job = JobPosting(
        source="manual",
        source_id=uuid.uuid4().hex[:8],
        title="Dev",
        company="Acme",
        url=f"https://x/{uuid.uuid4().hex[:6]}",
        status="matched",
        description="A Python role.",
        application_email="jobs@acme.example",
    )
    db.add_all([profile, job])
    db.flush()
    draft = None
    if with_draft:
        draft = ApplicationDraft(
            user_id=uid,
            job_id=job.id,
            status="ready",
            cover_letter=body,
            tailored_cv=body,
            changes_summary="[]",
        )
        db.add(draft)
    db.commit()
    return uid, profile, job, draft


def _failed_application(db, uid, job, *, draft=None, body=HEALTHY_BODY):
    """A 'failed' email application row — what retry_application takes."""
    from app.models import Application

    application = Application(
        user_id=uid,
        job_id=job.id,
        draft_id=draft.id if draft else None,
        method="email",
        status="failed",
        error="old failure",
        target_email="jobs@acme.example",
        subject="Application: Dev — J8 Tester",
        body=body,
    )
    db.add(application)
    db.commit()
    return application


def _live_email_settings(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "apply@jobfinderos.test")
    monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)


def _serve_original_cv(monkeypatch, content=b"%PDF-1.4 ORIGINAL CV BYTES"):
    """Make the send paths' original-CV read succeed (the storage
    boundary), so a test observes the DISPATCH, not the CV-read."""
    from app.services import storage

    monkeypatch.setattr(storage, "read_cv_at_path", lambda key: content)
    monkeypatch.setattr(storage, "read_original_cv", lambda profile: content)


class _RecordingSend:
    """A resend.Emails double at the exact call seam the service uses.

    Records (params, options) of every call — the idempotency key rides
    `options` — then answers with `exc` (a raise) or a success dict.
    """

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def install(self, monkeypatch):
        outer = self

        class _Emails:
            @staticmethod
            def send(params, options=None):
                outer.calls.append(
                    {"params": params, "options": options, "at": time.time()}
                )
                if outer.exc is not None:
                    raise outer.exc
                return {"id": f"msg_{len(outer.calls)}"}

        monkeypatch.setitem(
            sys.modules,
            "resend",
            type("R", (), {"Emails": _Emails, "api_key": None}),
        )


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


# ---------------------------------------------------------------------
# AC-1 — the key covers every dispatched field; the outcome
#        classification is exactly the contract's
# ---------------------------------------------------------------------

# The shape of a key: the application's id, a dash, and a hex digest —
# nothing else. Anything personal must live only inside the digest.
_KEY_SHAPE = re.compile(r"^[0-9]+-[0-9a-f]{64}$")

# Text that must never be readable out of a key in clear text.
_PERSONAL = (
    "jobs@acme.example",    # the recipient
    "J8 Tester",            # the applicant's name (from, attachment names)
    "applicant@personal.example",  # reply_to
    "apply@jobfinderos.test",      # the operator sender
    "Dear Hiring Manager",  # the body
    "backend developer",    # the body
)


def _assert_key_shape(key, application_id):
    assert isinstance(key, str) and key, key
    assert _KEY_SHAPE.match(key), key
    assert key.startswith(f"{application_id}-"), key
    for personal in _PERSONAL:
        assert personal not in key, (key, personal)


def _attachment_payload(call):
    """The attachments of one recorded send, decoded to bytes — what the
    employer would receive."""
    return [
        (a["filename"], base64.b64decode(a["content"]))
        for a in call["params"]["attachments"]
    ]


class _KeyLedger:
    """J-8's invariant across a whole test: Resend never sees one key
    with two different payloads."""

    def __init__(self):
        self.by_key = {}

    def record(self, call, application_id):
        key = (call["options"] or {}).get("idempotency_key")
        assert key, f"dispatched without an idempotency key: {call['params']}"
        payload = (
            sorted((f, c) for f, c in _attachment_payload(call)),
            tuple(sorted(
                (k, tuple(v) if isinstance(v, list) else v)
                for k, v in call["params"].items() if k != "attachments"
            )),
        )
        if key in self.by_key:
            assert self.by_key[key] == payload, (
                f"one key, two payloads ({key}): {self.by_key[key]} vs {payload}"
            )
        else:
            self.by_key[key] = payload
        return key


def _assert_only_field_changed(call, baseline, field):
    """The two dispatched payloads differ in exactly `field` — so a key
    change can only mean the key covers THAT field."""
    before = dict(baseline["params"])
    after = dict(call["params"])
    old, new = before.pop(field), after.pop(field)
    assert old != new, f"{field} did not actually change — the test proves nothing"
    assert after == before, (
        f"expected only {field} to change; also changed: "
        f"{[k for k in set(after) | set(before) if after.get(k) != before.get(k)]}"
    )


def test_ac_1(client, db, monkeypatch):
    """AC-1: the key covers every field actually dispatched, and only a
    4xx-answered rejection may say 'the email was not sent'."""
    from app.services.apply_service import retry_application

    _live_email_settings(monkeypatch)
    ledger = _KeyLedger()

    # =================================================================
    # Part 1 — the draft path: from, reply_to and the attachment ORDER
    # =================================================================
    uid, profile, job, draft = _seed(db)
    timing_out = _RecordingSend(exc=TimeoutError("Read timed out"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        application = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application.status == "failed", application.error
    assert len(timing_out.calls) == 1, "the dispatch must actually run"
    first = timing_out.calls[0]
    key = ledger.record(first, application.id)
    _assert_key_shape(key, application.id)

    # The dispatched payload really carries the fields the key must
    # cover — a key that omits them is the bug this contract closes.
    assert first["params"]["from"] == f"J8 Tester <{settings.APPLY_FROM_EMAIL}>"
    assert first["params"]["reply_to"] == "applicant@personal.example"
    assert first["params"]["to"] == ["jobs@acme.example"]
    assert first["params"]["text"] == HEALTHY_BODY
    assert len(first["params"]["attachments"]) == 3

    # --- unchanged application, a real re-render apart: SAME key -------
    # (J-6's proof stays green under the widened digest.)
    time.sleep(1.1)  # the PDF timestamp moves between renders
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        retry_application(db, application, profile)
    second = timing_out.calls[1]
    assert second["at"] - first["at"] >= 1.0
    assert _attachment_payload(second) == _attachment_payload(first), (
        "the renders beneath are reproducible — a key change here would "
        "come from the digest, not the email"
    )
    assert ledger.record(second, application.id) == key

    # --- `from` alone changes the key ----------------------------------
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(settings, "APPLY_FROM_EMAIL", "other-sender@jobfinderos.test")
        timing_out.install(mp)
        retry_application(db, application, profile)
    sender_changed = timing_out.calls[2]
    _assert_only_field_changed(sender_changed, first, "from")
    sender_key = ledger.record(sender_changed, application.id)
    assert sender_key != key, "the key must cover the `from` the dispatch carries"

    # --- `reply_to` alone changes the key ------------------------------
    profile.email = "replies-go-here@personal.example"
    db.add(profile)
    db.commit()
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        retry_application(db, application, profile)
    reply_changed = timing_out.calls[3]
    _assert_only_field_changed(reply_changed, first, "reply_to")
    reply_key = ledger.record(reply_changed, application.id)
    assert reply_key != key, "the key must cover `reply_to`"

    # --- an attachment's BYTES alone change the key --------------------
    profile.email = "applicant@personal.example"  # back to the baseline email
    db.add(profile)
    db.commit()
    with monkeypatch.context() as mp:
        _serve_original_cv(mp, content=b"%PDF-1.4 A DIFFERENT ORIGINAL CV")
        timing_out.install(mp)
        retry_application(db, application, profile)
    bytes_changed = timing_out.calls[4]
    _assert_only_field_changed(bytes_changed, first, "attachments")
    assert ledger.record(bytes_changed, application.id) != key

    # --- an attachment's FILENAME alone changes the key ----------------
    profile.cv_file_name = "renamed-cv.pdf"
    db.add(profile)
    db.commit()
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        retry_application(db, application, profile)
    name_changed = timing_out.calls[5]
    _assert_only_field_changed(name_changed, first, "attachments")
    name_key = ledger.record(name_changed, application.id)
    assert name_key != key, "the key must cover every attachment's filename"

    # --- the attachment ORDER does not change the key ------------------
    # The same email assembled in a different order is the same email:
    # the attachments are hashed in a canonical order, not list order.
    profile.cv_file_name = "cv.pdf"  # back to the baseline package
    db.add(profile)
    db.commit()
    real_pdfs = pdf_service.send_attachment_pdfs

    def _reordered(*args, **kwargs):
        return list(real_pdfs(*args, **kwargs))[::-1]

    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(pdf_service, "send_attachment_pdfs", _reordered)
        timing_out.install(mp)
        retry_application(db, application, profile)
    permuted = timing_out.calls[6]
    assert _attachment_payload(permuted) != _attachment_payload(first), (
        "the attachments were not actually permuted"
    )
    assert sorted(_attachment_payload(permuted)) == sorted(_attachment_payload(first))
    assert ledger.record(permuted, application.id) == key, (
        "the same email with its attachments assembled in another order "
        "must keep the key — the hash is canonical, not list-ordered"
    )

    # =================================================================
    # Part 2 — the legacy path: to, subject and text, one field at a
    #          time (this path attaches only the original CV, so a
    #          body edit cannot move the PDFs beneath the assertion)
    # =================================================================
    uid_l, profile_l, job_l, _draft_l = _seed(db, with_draft=False)
    application_l = _failed_application(db, uid_l, job_l)
    legacy = _RecordingSend(exc=TimeoutError("Read timed out"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        legacy.install(mp)
        retry_application(db, application_l, profile_l)
    legacy_first = legacy.calls[0]
    legacy_key = ledger.record(legacy_first, application_l.id)
    _assert_key_shape(legacy_key, application_l.id)

    for field, mutate, restore in (
        ("to",
         lambda: setattr(application_l, "target_email", "hr@acme.example"),
         lambda: setattr(application_l, "target_email", "jobs@acme.example")),
        ("subject",
         lambda: setattr(application_l, "subject", "Following up: Dev"),
         lambda: setattr(application_l, "subject", "Application: Dev — J8 Tester")),
        ("text",
         lambda: setattr(application_l, "body", HEALTHY_BODY + "Added line."),
         lambda: setattr(application_l, "body", HEALTHY_BODY)),
        ("from",
         lambda: setattr(profile_l, "full_name", "J8 Renamed"),
         lambda: setattr(profile_l, "full_name", "J8 Tester")),
        ("reply_to",
         lambda: setattr(profile_l, "email", "legacy-reply@personal.example"),
         lambda: setattr(profile_l, "email", "applicant@personal.example")),
        ("attachments",
         lambda: setattr(profile_l, "cv_file_name", "legacy-cv.pdf"),
         lambda: setattr(profile_l, "cv_file_name", "cv.pdf")),
    ):
        mutate()
        db.add_all([application_l, profile_l])
        db.commit()
        with monkeypatch.context() as mp:
            _serve_original_cv(mp)
            legacy.install(mp)
            retry_application(db, application_l, profile_l)
        call = legacy.calls[-1]
        _assert_only_field_changed(call, legacy_first, field)
        changed_key = ledger.record(call, application_l.id)
        assert changed_key != legacy_key, f"the key must cover `{field}`"
        restore()  # one field at a time: every call is comparable to the baseline
        db.add_all([application_l, profile_l])
        db.commit()

    # =================================================================
    # Part 3 — the outcome classification, both send paths
    # =================================================================
    # (label, exception, is a rejection Resend ANSWERED with 4xx != 409)
    cases = [
        ("422 validation_error",
         real_resend.exceptions.ResendError(
             code=422, error_type="validation_error",
             message="missing from", suggested_action=""), True),
        ("429 rate_limit_exceeded",
         real_resend.exceptions.ResendError(
             code=429, error_type="rate_limit_exceeded",
             message="Too many requests", suggested_action="slow down"), True),
        ("409 idempotency conflict",
         real_resend.exceptions.ResendError(
             code=409, error_type="idempotency_conflict",
             message="Idempotency-Key is already processed with a "
                     "different payload", suggested_action=""), False),
        ("500 application_error",
         real_resend.exceptions.ResendError(
             code=500, error_type="application_error",
             message="Something went wrong", suggested_action=""), False),
        ("503 service_unavailable",
         real_resend.exceptions.ResendError(
             code=503, error_type="service_unavailable",
             message="upstream unavailable", suggested_action=""), False),
        ("transport wrapper (no HTTP answer)",
         real_resend.exceptions.ResendError(
             code=500, error_type="HttpClientError",
             message="HTTPSConnectionPool: Read timed out.",
             suggested_action="Request failed, please try again."), False),
        ("an exception that is not a ResendError",
         TimeoutError("connection reset mid-send"), False),
    ]

    for label, exc, rejected in cases:
        # --- the draft path (submit_draft -> _send_with_pdfs) ----------
        uid_c, profile_c, job_c, draft_c = _seed(db)
        sender = _RecordingSend(exc=exc)
        with monkeypatch.context() as mp:
            _serve_original_cv(mp)
            sender.install(mp)
            dispatched = submit_draft(db, draft_c, "email", profile_c, user_id=uid_c)
        assert dispatched.status == "failed", (label, dispatched.error)
        assert len(sender.calls) == 1, (label, "the dispatch must actually run")
        if rejected:
            assert NOT_SENT in dispatched.error, (label, dispatched.error)
            assert RETRY_IT in dispatched.error, (label, dispatched.error)
            assert UNKNOWN_OUTCOME not in dispatched.error, (label, dispatched.error)
        else:
            assert dispatched.error == UNKNOWN_OUTCOME, (label, dispatched.error)
            assert "not sent" not in dispatched.error.lower(), (label, dispatched.error)
        db.refresh(draft_c)
        assert draft_c.status == "ready", (label, "a failed send must stay retryable")

        # --- the legacy path (retry_application -> _send_email_application)
        uid_d, profile_d, job_d, _draft_d = _seed(db, with_draft=False)
        application_d = _failed_application(db, uid_d, job_d)
        sender_l = _RecordingSend(exc=exc)
        with monkeypatch.context() as mp:
            _serve_original_cv(mp)
            sender_l.install(mp)
            retry_application(db, application_d, profile_d)
        assert application_d.status == "failed", (label, application_d.error)
        assert len(sender_l.calls) == 1, (label, "the dispatch must actually run")
        if rejected:
            assert NOT_SENT in application_d.error, (label, application_d.error)
            assert UNKNOWN_OUTCOME not in application_d.error, (label, application_d.error)
        else:
            assert application_d.error == UNKNOWN_OUTCOME, (label, application_d.error)
            assert "not sent" not in application_d.error.lower(), (
                label, application_d.error)
