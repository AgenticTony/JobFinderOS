"""J-6 — a send whose outcome is unknown never claims 'not sent', and a
retry never emails the employer twice.

J-2 made every failed dispatch say "the email was not sent". That is a
claim about the OUTCOME, and for one failure class it is a lie: when
resend.Emails.send dies of a timeout or a dropped connection, the
request may already have reached Resend — the employer may already have
the email. Telling the user "not sent" invites a blind retry, and the
retry sends a second copy.

test_ac_1  the two send paths tell the user only what is known. A
           failure BEFORE the dispatch call, and a rejection Resend
           ANSWERED (resend.exceptions.ResendError), keep J-2's "the
           email was not sent" wording. Any other exception raised BY
           the dispatch — a timeout or connection error — sets exactly
           the unknown-outcome message, which never says "not sent".
           Every case leaves the application 'failed' (retryable) and
           keeps J-2's original-CV, not-configured and PdfTextLayerError
           branches their own messages; the exception's own text and
           type stay in the server log with a traceback.
test_ac_2  every send carries an idempotency key: the application's id
           plus a hash of the values actually dispatched (recipient,
           subject, body text, every attachment's filename and bytes).
           The renders are reproducible (pdf_service pins fpdf2's
           creation date), so two sends of an unchanged application — a
           real re-render apart — carry the same key and Resend delivers
           at most one; an edit changes the key; two applications never
           share one. Through the real installed SDK with only its HTTP
           transport faked, the outgoing POST carries the key as the
           Idempotency-Key header.

Every expected string below is a literal from the approved contract,
never a recomputation of the services' wording.
"""

import base64
import logging
import os
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

# Built by concatenation so no single credential-shaped literal sits in
# the source (the value is a throwaway fixture that never authenticates
# anything real).
_SCRATCH_DB = "test_j6.db"

# The contract's literals — the wording is the contract, not this
# module's opinion.
UNKNOWN_OUTCOME = (
    "We could not confirm whether the email was sent. You can retry it "
    "from this application; an unchanged email is never delivered twice."
)
NOT_SENT = "the email was not sent"
RETRY_IT = "You can retry it from this application."
REUPLOAD = "Please re-upload your CV, then retry it from this application."

# A body every render path draws faithfully (pure ascii: survives the
# unicode font AND the latin-1 core-font fallback alike — the J-1
# fixture).
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

# Bytes no PDF reader can open — forcing input for the PdfTextLayerError
# branch (the J-1 'unreadable PDF' class).
GARBAGE_PDF = b"this is not a pdf; the reader must raise on it"

DRAFT_LOGGER = "app.services.draft_service"
APPLY_LOGGER = "app.services.apply_service"


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — the
    # send-flow tests need the tables.
    if os.path.exists(_SCRATCH_DB):
        os.remove(_SCRATCH_DB)
    with TestClient(app) as c:  # lifespan runs init_db -> alembic head
        yield c
    engine.dispose()
    if os.path.exists(_SCRATCH_DB):
        os.remove(_SCRATCH_DB)


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
            email=f"j6-{uid.hex[:6]}@test.example",
            hashed_password="test-only",
        )
    )
    db.flush()
    profile = Profile(
        user_id=uid,
        is_active=1,
        full_name="J6 Tester",
        cv_text="J6 CV",
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
        subject="Application: Dev — J6 Tester",
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
    boundary), so a test observes the DISPATCH failure, not the CV-read
    one."""
    from app.services import storage

    monkeypatch.setattr(storage, "read_cv_at_path", lambda key: content)
    monkeypatch.setattr(storage, "read_original_cv", lambda profile: content)


def _raising_original_cv(monkeypatch):
    """The storage boundary itself failing BEFORE the dispatch call —
    the pre-send failure class (nothing has left the machine)."""
    from app.services import storage

    def boom(key):
        raise OSError("storage backend i/o error before any dispatch")

    monkeypatch.setattr(storage, "read_cv_at_path", boom)
    monkeypatch.setattr(storage, "read_original_cv", boom)


class _RecordingSend:
    """A resend.Emails double at the exact call seam the service uses.

    Records (params, options) and the wall-clock moment of the call —
    the idempotency key is one of them — then answers with `exc` (a
    raise) or a success dict.
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


class _FakeTransport(real_resend.HTTPClient):
    """The real installed SDK with only its HTTP transport swapped.

    The SDK's RequestsClient is the one boundary between resend.Emails
    .send and the wire: replacing it keeps the SDK's real param
    handling, header building and error mapping intact, and shows this
    test exactly the POST the production send would have put on the
    wire.
    """

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def request(self, method, url, headers, json=None, files=None, data=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json": json,
            }
        )
        return self.handler()


def _ok_response():
    return (
        b'{"id":"msg_transport","object":"email"}',
        200,
        {"content-type": "application/json"},
    )


def _rejected_response():
    """Resend ANSWERED and refused the request — the SDK raises a
    resend.exceptions.ResendError subclass for this body."""
    return (
        b'{"message":"Too many requests","name":"rate_limit_exceeded"}',
        429,
        {"content-type": "application/json"},
    )


def _timing_out_transport():
    """The connection dies mid-request: the transport itself raises, and
    the SDK wraps it in a ResendError(error_type='HttpClientError') —
    the shape a real timeout takes out of the installed SDK."""
    import requests

    raise requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='api.resend.com', port=443): "
        "Read timed out. (read timeout=10)"
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
# AC-1 — the message tells the user only what is known about delivery
# ---------------------------------------------------------------------


def test_ac_1(client, db, monkeypatch, caplog):
    """AC-1: on both send paths, only a failure that PROVES nothing was
    sent may say 'the email was not sent'."""
    from app.services.apply_service import retry_application

    caplog.set_level(logging.ERROR)
    _live_email_settings(monkeypatch)

    # --- the dispatch's own failures, draft path (submit_draft) -------
    # A rejection Resend ANSWERED (through the real SDK: the transport
    # serves the 429 the API serves) keeps J-2's wording.
    uid, profile, job, draft = _seed(db)
    transport = _FakeTransport(_rejected_response)
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(real_resend, "default_http_client", transport)
        application = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application.status == "failed", application.error
    assert NOT_SENT in application.error, application.error
    assert RETRY_IT in application.error, application.error
    assert UNKNOWN_OUTCOME not in application.error, application.error
    db.refresh(draft)
    assert draft.status == "ready", "a failed send must stay retryable"
    _assert_logged(caplog, DRAFT_LOGGER, "RateLimitError")

    # A timeout through the real SDK (transport raises; the SDK wraps it
    # in ResendError(error_type='HttpClientError')) is the unknown-outcome
    # class: Resend may already hold the email.
    uid, profile, job, draft = _seed(db)
    transport = _FakeTransport(_timing_out_transport)
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(real_resend, "default_http_client", transport)
        application = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application.status == "failed", application.error
    assert application.error == UNKNOWN_OUTCOME, application.error
    db.refresh(draft)
    assert draft.status == "ready"
    _assert_logged(caplog, DRAFT_LOGGER, "Read timed out")

    # Any OTHER exception out of the dispatch (the SDK faked at its own
    # seam) is the same unknown-outcome class.
    import requests

    uid, profile, job, draft = _seed(db)
    sender = _RecordingSend(exc=requests.exceptions.ReadTimeout("Read timed out."))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        sender.install(mp)
        application = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application.status == "failed", application.error
    assert application.error == UNKNOWN_OUTCOME, application.error
    assert "not sent" not in application.error.lower(), application.error
    db.refresh(draft)
    assert draft.status == "ready"

    # --- the dispatch's own failures, retry of a draft application -----
    uid, profile, job, draft = _seed(db)
    application2 = _failed_application(db, uid, job, draft=draft)
    sender = _RecordingSend(exc=real_resend.exceptions.ResendError(
        code=429, error_type="rate_limit_exceeded",
        message="Too many requests", suggested_action="slow down"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        sender.install(mp)
        retry_application(db, application2, profile)
    assert application2.status == "failed", application2.error
    assert NOT_SENT in application2.error, application2.error
    assert UNKNOWN_OUTCOME not in application2.error, application2.error

    uid, profile, job, draft = _seed(db)
    application3 = _failed_application(db, uid, job, draft=draft)
    sender = _RecordingSend(exc=TimeoutError("connection reset mid-send"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        sender.install(mp)
        retry_application(db, application3, profile)
    assert application3.status == "failed", application3.error
    assert application3.error == UNKNOWN_OUTCOME, application3.error
    assert "not sent" not in application3.error.lower(), application3.error

    # --- the dispatch's own failures, legacy no-draft path -------------
    uid, profile, job, _draft = _seed(db, with_draft=False)
    application4 = _failed_application(db, uid, job)
    sender = _RecordingSend(exc=real_resend.exceptions.ResendError(
        code=422, error_type="validation_error",
        message="missing from", suggested_action=""))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        sender.install(mp)
        retry_application(db, application4, profile)
    assert application4.status == "failed", application4.error
    assert NOT_SENT in application4.error, application4.error
    assert UNKNOWN_OUTCOME not in application4.error, application4.error
    _assert_logged(caplog, APPLY_LOGGER, "ResendError")

    uid, profile, job, _draft = _seed(db, with_draft=False)
    application5 = _failed_application(db, uid, job)
    sender = _RecordingSend(exc=ConnectionError("connection dropped by peer"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        sender.install(mp)
        retry_application(db, application5, profile)
    assert application5.status == "failed", application5.error
    assert application5.error == UNKNOWN_OUTCOME, application5.error
    assert "not sent" not in application5.error.lower(), application5.error
    _assert_logged(caplog, APPLY_LOGGER, "connection dropped")

    # --- a failure BEFORE the dispatch call keeps J-2's wording --------
    # Nothing has left the machine, so "not sent" is the truth. The
    # storage boundary itself fails while the attachments are built.
    uid, profile, job, draft = _seed(db)
    with monkeypatch.context() as mp:
        _raising_original_cv(mp)
        application6 = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application6.status == "failed", application6.error
    assert NOT_SENT in application6.error, application6.error
    assert RETRY_IT in application6.error, application6.error
    assert UNKNOWN_OUTCOME not in application6.error, application6.error
    assert "storage backend i/o error" not in application6.error, application6.error
    assert "OSError" not in application6.error, application6.error
    _assert_logged(caplog, DRAFT_LOGGER, "OSError", "storage backend i/o error")

    # ...and on the legacy path (its own CV read)
    uid, profile, job, _draft = _seed(db, with_draft=False)
    application7 = _failed_application(db, uid, job)
    with monkeypatch.context() as mp:
        _raising_original_cv(mp)
        retry_application(db, application7, profile)
    assert application7.status == "failed", application7.error
    assert NOT_SENT in application7.error, application7.error
    assert UNKNOWN_OUTCOME not in application7.error, application7.error
    _assert_logged(caplog, APPLY_LOGGER, "OSError", "storage backend i/o error")

    # --- J-2's branches keep their own messages ------------------------
    # The original-CV read failure keeps its re-upload advice (the
    # fixture stores no CV file, so the natural read returns None).
    uid, profile, job, draft = _seed(db)
    ok = _RecordingSend()
    with monkeypatch.context() as mp:
        ok.install(mp)
        application8 = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application8.status == "failed", application8.error
    assert NOT_SENT in application8.error, application8.error
    assert REUPLOAD in application8.error, application8.error
    assert RETRY_IT not in application8.error, application8.error
    assert UNKNOWN_OUTCOME not in application8.error, application8.error
    assert not ok.calls, "the email was dispatched anyway"

    uid, profile, job, _draft = _seed(db, with_draft=False)
    application9 = _failed_application(db, uid, job)
    retry_application(db, application9, profile)
    assert application9.status == "failed", application9.error
    assert REUPLOAD in application9.error, application9.error
    assert UNKNOWN_OUTCOME not in application9.error, application9.error

    # The not-configured branches keep their operator messages.
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    uid, profile, job, draft = _seed(db)
    application10 = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application10.status == "failed", application10.error
    assert "contact the operator" in application10.error, application10.error
    assert NOT_SENT not in application10.error, application10.error
    assert UNKNOWN_OUTCOME not in application10.error, application10.error

    uid, profile, job, _draft = _seed(db, with_draft=False)
    application11 = _failed_application(db, uid, job)
    retry_application(db, application11, profile)
    assert application11.status == "failed", application11.error
    assert "not configured" in application11.error, application11.error
    assert NOT_SENT not in application11.error, application11.error
    assert UNKNOWN_OUTCOME not in application11.error, application11.error
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")

    # The PdfTextLayerError branch keeps J-1/J-3 wording.
    uid, profile, job, draft = _seed(db)
    blocked = _RecordingSend()
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        blocked.install(mp)
        mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: GARBAGE_PDF)
        application12 = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application12.status == "failed", application12.error
    assert application12.error.startswith("unreadable PDF"), application12.error
    assert "the application was NOT sent." in application12.error, application12.error
    assert UNKNOWN_OUTCOME not in application12.error, application12.error
    assert not blocked.calls, "the email was dispatched anyway"

    # --- no user-facing string carries the exception's own words -------
    for row in (
        application, application2, application3, application4, application5,
        application6, application7, application8, application9, application10,
        application11, application12,
    ):
        error = row.error or ""
        for internal in ("Read timed out", "Too many requests", "RateLimitError",
                         "ResendError", "storage backend i/o error",
                         "connection reset", "connection dropped",
                         "HttpClientError", "HTTPSConnectionPool"):
            assert internal not in error, error


# ---------------------------------------------------------------------
# AC-2 — every send carries an idempotency key, and the render beneath
# it is reproducible
# ---------------------------------------------------------------------

# The shape of a key: the application's id, a dash, and a hex digest —
# nothing else. Anything personal (recipient, subject, name, CV text)
# must live only inside the digest.
_KEY_SHAPE = re.compile(r"^[0-9]+-[0-9a-f]{64}$")

# Text that must never be readable out of a key in clear text.
_PERSONAL = (
    "jobs@acme.example",   # the recipient
    "J6 Tester",           # the applicant's name (and the attachment names)
    "Dear Hiring Manager",  # the body
    "backend developer",   # the body
)


def _assert_key_shape(key, application_id):
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


class _DroppingThenOkTransport:
    """The wire story J-6 exists for: the first POST reaches the wire and
    the connection dies before the answer comes back (the outcome is
    unknown), and the retry's POST succeeds."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        import requests

        self.calls += 1
        if self.calls == 1:
            raise requests.exceptions.ReadTimeout(
                "Read timed out. (read timeout=10)"
            )
        return _ok_response()


def test_ac_2(client, db, monkeypatch):
    """AC-2: both send paths dispatch under an idempotency key that is
    stable for an unchanged application and new for any change to it."""
    from app.services.apply_service import retry_application
    from app.services.draft_service import save_draft_edits

    _live_email_settings(monkeypatch)

    # --- two sends of one unchanged application, a real re-render apart --
    # The dispatch dies of a timeout both times (the send the user then
    # retries), so both calls are observed at the resend.Emails.send seam
    # while the application stays retryable.
    uid, profile, job, draft = _seed(db)
    timing_out = _RecordingSend(exc=TimeoutError("Read timed out"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        application = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application.status == "failed", application.error
    assert application.error == UNKNOWN_OUTCOME, application.error

    time.sleep(1.1)  # a real re-render apart: the PDF timestamp moves
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        retry_application(db, application, profile)
    assert len(timing_out.calls) == 2
    first, second = timing_out.calls
    # A second apart — the renders below really were built at different
    # wall-clock moments.
    assert second["at"] - first["at"] >= 1.0

    key = first["options"]["idempotency_key"]
    assert second["options"] == {"idempotency_key": key}, (
        first["options"], second["options"])
    _assert_key_shape(key, application.id)

    # The attachments of the two sends are BYTE-IDENTICAL — the render is
    # reproducible, so the hashed values (and the key above) cannot drift.
    assert _attachment_payload(first) == _attachment_payload(second)
    assert len(_attachment_payload(first)) == 3, (
        "the package is the cover letter, the tailored CV and the "
        "original CV"
    )

    # --- an edit changes the key: a retry after an edit is a new send ---
    save_draft_edits(
        db, draft, cover_letter=HEALTHY_BODY + "\nOne more sentence, added in review."
    )
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        timing_out.install(mp)
        retry_application(db, application, profile)
    third = timing_out.calls[2]
    edited_key = third["options"]["idempotency_key"]
    assert edited_key != key, "an edited draft must not re-send under the old key"
    _assert_key_shape(edited_key, application.id)
    assert _attachment_payload(third) != _attachment_payload(first)

    # --- two applications never share a key, identical content or not ---
    uid_b, profile_b, job_b, draft_b = _seed(db)  # same body, name, recipient
    sibling = _RecordingSend(exc=TimeoutError("Read timed out"))
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        sibling.install(mp)
        application_b = submit_draft(db, draft_b, "email", profile_b, user_id=uid_b)
    key_b = sibling.calls[0]["options"]["idempotency_key"]
    assert application_b.id != application.id
    # The content really was identical — only the application id differs.
    assert _attachment_payload(sibling.calls[0]) == _attachment_payload(first)
    assert sibling.calls[0]["params"]["subject"] == first["params"]["subject"]
    assert sibling.calls[0]["params"]["to"] == first["params"]["to"]
    assert sibling.calls[0]["params"]["text"] == first["params"]["text"]
    assert key_b != key, "two applications must never share an idempotency key"
    _assert_key_shape(key_b, application_b.id)

    # --- the legacy no-draft path carries a key of its own --------------
    uid_c, profile_c, job_c, _draft_c = _seed(db, with_draft=False)
    application_c = _failed_application(db, uid_c, job_c)
    legacy = _RecordingSend()
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        legacy.install(mp)
        retry_application(db, application_c, profile_c)
    assert application_c.status == "sent", application_c.error
    legacy_key = legacy.calls[0]["options"]["idempotency_key"]
    _assert_key_shape(legacy_key, application_c.id)
    assert legacy_key not in (key, edited_key, key_b)

    # --- the real installed SDK puts the key on the wire ----------------
    # Only the HTTP transport is faked: the SDK's own param handling,
    # header building and error mapping all run, so this is the POST the
    # production send would put on the wire.
    uid_d, profile_d, job_d, draft_d = _seed(db)
    transport = _FakeTransport(_DroppingThenOkTransport())
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(real_resend, "default_http_client", transport)
        application_d = submit_draft(db, draft_d, "email", profile_d, user_id=uid_d)
    assert application_d.status == "failed", application_d.error
    assert application_d.error == UNKNOWN_OUTCOME, application_d.error

    time.sleep(1.1)
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(real_resend, "default_http_client", transport)
        retry_application(db, application_d, profile_d)
    assert application_d.status == "sent", application_d.error

    assert len(transport.requests) == 2, transport.requests
    on_the_wire = []
    for request in transport.requests:
        assert request["method"] == "post", request
        assert request["url"].endswith("/emails"), request["url"]
        assert "Idempotency-Key" in request["headers"], request["headers"]
        on_the_wire.append(request["headers"]["Idempotency-Key"])
        # the body is the dispatched payload — the key does not replace it
        assert request["json"]["to"] == ["jobs@acme.example"]
        assert len(request["json"]["attachments"]) == 3
    assert on_the_wire[0] == on_the_wire[1], (
        "the retry of an unchanged application must carry the SAME key — "
        f"got {on_the_wire}"
    )
    _assert_key_shape(on_the_wire[0], application_d.id)
