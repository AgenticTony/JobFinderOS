"""J-2 — send, retry, CV-upload and reply-scan errors say what failed
and what to do, never SDK or library text.

Every one of these surfaces used to paste the vendor's or the parser's
own words into the string the user reads: a failed Resend dispatch
became ``Email send failed: {the SDK's exception}`` on the application
row; an unextractable CV upload became ``Failed to extract text from
PDF: {pdfminer's message}`` in the 400 detail; a failed inbox scan
became the raw ComposioError (Composio response body included) in the
502 detail. The user cannot act on any of those — and they leak
internals to the browser.

test_ac_1  a failed email send sets application.error to a message
           carrying 'the email was not sent' + 'You can retry it from
           this application.' on all three dispatch paths (draft send,
           draft-bearing retry, legacy no-draft retry). The original-
           CV-read failure keeps its own advice ('Please re-upload your
           CV, then retry it from this application.'). The
           not-configured branches keep their 'contact the operator'
           messages and the PdfTextLayerError branch keeps its J-1/J-3
           wording — neither gains the retry literal. The exception's
           own text and type reach the server log (with traceback) and
           none of the user-facing strings.
test_ac_2  a CV upload whose text cannot be extracted returns 400 with
           'could not read the text of this file' + 'Please upload a
           text-based PDF or a DOCX instead.', never advises retrying
           the same file, and keeps the reader's exception text/type
           server-side (logged with traceback). The DOCX branch's
           existing errors are unchanged.
test_ac_3  a failed reply scan returns 502 with 'could not reach your
           mailbox' + 'Please try again in a little while.' (retrying
           is right here: the failure is the mail service); the
           Composio response body and exception text are logged
           server-side only.

Every expected string below is a literal from the approved contract,
never a recomputation of the services' wording.
"""

import logging
import sys
import uuid

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models import Application, ApplicationDraft, JobPosting, Profile
from app.models import User as UserModel
from app.services import pdf_service
from app.services.draft_service import submit_draft
from tests.auth_helpers import auth_client, register

# Built by concatenation so no single credential-shaped literal sits in
# the source (the value is a throwaway fixture that never authenticates
# anything real).
_SCRATCH_DB = "test_j2.db"

# The contract's literals — the wording is the contract, not this
# module's opinion.
NOT_SENT = "the email was not sent"
RETRY_IT = "You can retry it from this application."
REUPLOAD = "Please re-upload your CV, then retry it from this application."
UNREADABLE_TEXT = "could not read the text of this file"
UPLOAD_ADVICE = "Please upload a text-based PDF or a DOCX instead."
MAILBOX = "could not reach your mailbox"
SCAN_ADVICE = "Please try again in a little while."

# A body every render path draws faithfully (pure ascii: survives the
# unicode font AND the latin-1 core-font fallback alike — the J-1
# fixture).
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

# Bytes no PDF reader can open (the J-1 'unreadable PDF' class forcing
# input — used here to fire the PdfTextLayerError branch unchanged).
GARBAGE_PDF = b"this is not a pdf; the reader must raise on it"

# The vendor-shaped transport failure the fake resend raises: its type
# name and text are the internals that must stay server-side.
SDK_EXC_TEXT = (
    'resend sdk transport failure: HTTP 429 — {"message":"Too many '
    'requests","name":"rate_limit_exceeded"}'
)

DRAFT_LOGGER = "app.services.draft_service"
APPLY_LOGGER = "app.services.apply_service"


# The fake resend SDK's own error type — the class name is one of the
# internals that must never reach a user-facing string. J-6 split the
# dispatch failure by OUTCOME (a rejection Resend answered vs a transport
# failure), so the double raises the SDK's REAL rejection type, carrying
# the same vendor-shaped text: an exception of any other type now means
# 'outcome unknown' and gets J-6's message, not J-2's.
from resend.exceptions import ResendError


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — the
    # endpoint and send-flow tests need the tables.
    import os

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
# AC-1 helpers — the three dispatch paths and their forcing inputs
# ---------------------------------------------------------------------


def _seed_email_fixture(db, *, with_draft_cv_snapshot=None):
    """user + profile + email-publishing job + 'ready' draft, committed —
    exactly the package submit_draft(method='email') dispatches. The
    profile stores NO CV file (cv_file_path unset): the original-CV read
    fails naturally unless a test monkeypatch serves bytes."""
    uid = uuid.uuid4()
    db.add(
        UserModel(
            id=uid,
            email=f"j2-{uid.hex[:6]}@test.example",
            hashed_password="test-only",
        )
    )
    db.flush()
    profile = Profile(
        user_id=uid,
        is_active=1,
        full_name="J2 Tester",
        cv_text="J2 CV",
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
    draft = ApplicationDraft(
        user_id=uid,
        job_id=job.id,
        status="ready",
        cover_letter=HEALTHY_BODY,
        tailored_cv=HEALTHY_BODY,
        changes_summary="[]",
        cv_file_path=with_draft_cv_snapshot,
    )
    db.add(draft)
    db.commit()
    return uid, profile, job, draft


def _live_email_settings(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "apply@jobfinderos.test")
    monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)


def _raising_resend(monkeypatch, exc):
    """A resend module whose dispatch raises `exc` — the SDK failure."""
    class _Emails:
        @staticmethod
        def send(params):
            raise exc

    monkeypatch.setitem(
        sys.modules, "resend", type("R", (), {"Emails": _Emails, "api_key": None})
    )


def _capturing_resend(monkeypatch, sink):
    class _Emails:
        @staticmethod
        def send(params):
            sink.append(params)
            return {"id": f"msg_{len(sink)}"}

    monkeypatch.setitem(
        sys.modules, "resend", type("R", (), {"Emails": _Emails, "api_key": None})
    )


def _serve_original_cv(monkeypatch, content=b"%PDF-1.4 ORIGINAL CV BYTES"):
    """Make the send paths' original-CV read succeed (storage boundary),
    so a test observes the DISPATCH failure, not the CV-read one."""
    from app.services import storage

    monkeypatch.setattr(storage, "read_cv_at_path", lambda key: content)
    monkeypatch.setattr(storage, "read_original_cv", lambda profile: content)


def _failed_application(db, uid, job, *, draft=None):
    """A 'failed' email application row — what retry_application takes."""
    application = Application(
        user_id=uid,
        job_id=job.id,
        draft_id=draft.id if draft else None,
        method="email",
        status="failed",
        error="old failure",
        target_email="jobs@acme.example",
        subject="Application: Dev",
        body=HEALTHY_BODY,
    )
    db.add(application)
    db.commit()
    return application


def _assert_clean_failure(application, advice):
    """A failed dispatch whose user-facing error says what happened and
    what to do — and none of the SDK's own words."""
    assert application.status == "failed", application.error
    error = application.error or ""
    assert NOT_SENT in error, error
    assert advice in error, error
    assert error.index(NOT_SENT) < error.index(advice), error
    assert SDK_EXC_TEXT not in error, error
    assert "ResendError" not in error, error
    return error


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


def test_ac_1(client, db, monkeypatch, caplog):
    """AC-1: every failed email send says what failed and what to do,
    on all three dispatch paths — SDK text stays in the log."""
    from app.services.apply_service import retry_application

    caplog.set_level(logging.ERROR)

    # --- the three dispatch paths, generic SDK failure ----------------
    # All three need the original-CV read to SUCCEED (a served storage
    # read) so the failure observed is the dispatch itself; the context
    # manager restores the natural read for the CV-failure part below.
    uid, profile, job, draft = _seed_email_fixture(db)
    uid2, profile2, job2, draft2 = _seed_email_fixture(db)
    uid3, profile3, job3, _draft3 = _seed_email_fixture(db)
    application2 = _failed_application(db, uid2, job2, draft=draft2)
    application3 = _failed_application(db, uid3, job3)
    _live_email_settings(monkeypatch)
    sdk = ResendError(
        code=429,
        error_type="rate_limit_exceeded",
        message=SDK_EXC_TEXT,
        suggested_action="Reduce your request rate.",
    )
    _raising_resend(monkeypatch, sdk)
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        # 1. the draft send path (submit_draft -> _send_with_pdfs)
        application = submit_draft(db, draft, "email", profile, user_id=uid)
        # 2. the retry of a draft-bearing application
        #    (retry_application -> _send_with_pdfs)
        retry_application(db, application2, profile2)
        # 3. the legacy no-draft retry (retry_application ->
        #    apply_service._send_email_application)
        retry_application(db, application3, profile3)
    _assert_clean_failure(application, RETRY_IT)
    _assert_clean_failure(application2, RETRY_IT)
    _assert_clean_failure(application3, RETRY_IT)
    _assert_logged(caplog, DRAFT_LOGGER, "ResendError", SDK_EXC_TEXT)
    _assert_logged(caplog, APPLY_LOGGER, "ResendError", SDK_EXC_TEXT)

    # --- the original-CV-read failure keeps its own advice ------------
    # The fixture stores no CV file, so the storage read returns None:
    # nothing is dispatched and the message must tell the user to
    # re-upload — the retry literal belongs to the dispatch failure.
    uid4, profile4, job4, draft4 = _seed_email_fixture(db)
    sent: list = []
    _capturing_resend(monkeypatch, sent)
    application4 = submit_draft(db, draft4, "email", profile4, user_id=uid4)
    error = _assert_clean_failure(application4, REUPLOAD)
    assert RETRY_IT not in error, error
    assert "Try again" not in error, error
    assert not sent, "the email was dispatched anyway"
    _assert_logged(caplog, DRAFT_LOGGER, "Original CV file could not be read")

    # ...and on the legacy path (apply_service's own CV read)
    uid5, profile5, job5, _draft5 = _seed_email_fixture(db)
    application5 = _failed_application(db, uid5, job5)
    retry_application(db, application5, profile5)
    error = _assert_clean_failure(application5, REUPLOAD)
    assert RETRY_IT not in error, error
    _assert_logged(caplog, APPLY_LOGGER, "Original CV file could not be read")

    # --- the not-configured branches keep their operator messages -----
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    uid6, profile6, job6, draft6 = _seed_email_fixture(db)
    application6 = submit_draft(db, draft6, "email", profile6, user_id=uid6)
    assert application6.status == "failed"
    assert "contact the operator" in application6.error, application6.error
    assert RETRY_IT not in application6.error, application6.error
    assert NOT_SENT not in application6.error, application6.error

    uid7, profile7, job7, _draft7 = _seed_email_fixture(db)
    application7 = _failed_application(db, uid7, job7)
    retry_application(db, application7, profile7)
    assert application7.status == "failed"
    assert "contact the operator" in application7.error, application7.error
    assert RETRY_IT not in application7.error, application7.error
    assert NOT_SENT not in application7.error, application7.error
    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")

    # --- the PdfTextLayerError branch keeps J-1/J-3 wording -----------
    uid8, profile8, job8, draft8 = _seed_email_fixture(db)
    blocked: list = []
    _capturing_resend(monkeypatch, blocked)
    with monkeypatch.context() as mp:
        _serve_original_cv(mp)
        mp.setattr(pdf_service, "cover_letter_pdf", lambda text, name: GARBAGE_PDF)
        application8 = submit_draft(db, draft8, "email", profile8, user_id=uid8)
    assert application8.status == "failed", application8.error
    # J-1's shape: the class marker leads and the send outcome is J-1's
    # own literal — none of J-2's.
    assert application8.error.startswith("unreadable PDF"), application8.error
    assert "the application was NOT sent." in application8.error
    assert RETRY_IT not in application8.error, application8.error
    assert NOT_SENT not in application8.error, application8.error
    assert not blocked, "the email was dispatched anyway"


# ---------------------------------------------------------------------
# AC-2 — the CV upload whose text cannot be extracted
# ---------------------------------------------------------------------

# Bytes with a PDF header that no reader can parse — the reader raises
# on them, and whatever it says is an internal that must stay
# server-side.
CORRUPT_PDF = b"%PDF-1.4 this is not really a pdf; the parser must raise on it"


def _no_text_pdf() -> bytes:
    """A visually-valid PDF whose text layer is empty — the scanned
    document shape: opens fine, extracts nothing."""
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_draw_color(0, 0, 0)
    pdf.rect(20.0, 20.0, 150.0, 80.0)
    return bytes(pdf.output())


def _reader_raises_on(content: bytes) -> tuple:
    """(type name, text) of the exception the PDF reader itself raises
    on these bytes — captured from the real raise, so the assertions
    check THIS text, not a guess. Empty when the reader stays silent."""
    import io

    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page.extract_text()
    except Exception as e:  # any reader error is exactly what we capture
        return type(e).__name__, str(e)
    return "", ""


def test_ac_2(client, caplog):
    """AC-2: an unextractable CV upload is a 400 that says what to
    upload instead — the reader's own words stay in the log."""
    caplog.set_level(logging.ERROR)

    email = f"j2-cv-{uuid.uuid4().hex[:6]}@test.example"
    register(client, email)
    auth_client(client, email)

    # What the reader itself says on the corrupt bytes (the internal
    # text this criterion keeps server-side).
    reader_type, reader_text = _reader_raises_on(CORRUPT_PDF)
    assert reader_text, "the fixture must make the reader raise"

    with caplog.at_level(logging.ERROR, logger="app.services.file_service"):
        # 1. the scanned shape: a valid PDF with no text layer at all
        resp = client.post(
            "/api/v1/profile/upload",
            files={"file": ("cv.pdf", _no_text_pdf(), "application/pdf")},
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert UNREADABLE_TEXT in detail, detail
        assert UPLOAD_ADVICE in detail, detail
        assert detail.index(UNREADABLE_TEXT) < detail.index(UPLOAD_ADVICE), detail

        # 2. the corrupt shape: the reader raises, the 400 stays the same
        resp = client.post(
            "/api/v1/profile/upload",
            files={"file": ("cv.pdf", CORRUPT_PDF, "application/pdf")},
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert UNREADABLE_TEXT in detail, detail
        assert UPLOAD_ADVICE in detail, detail

    # Never advises retrying the same file — a retry cannot make a
    # scan readable.
    assert "try again" not in detail.lower(), detail
    assert "retry" not in detail.lower(), detail

    # The reader's own text and type appear NOWHERE in the 400 detail...
    assert reader_text not in detail, detail
    assert reader_type not in detail, detail

    # ...but ARE in the server log, attached to a traceback.
    records = [
        r
        for r in caplog.records
        if r.name == "app.services.file_service"
        and r.levelno >= logging.ERROR
        and r.exc_info
    ]
    assert records, f"no ERROR record with a traceback: {caplog.text}"
    assert any(
        reader_text in r.getMessage() and reader_type in r.getMessage()
        for r in records
    ), [r.getMessage() for r in records]

    # --- the DOCX branch's existing errors are unchanged --------------
    resp = client.post(
        "/api/v1/profile/upload",
        files={
            "file": (
                "cv.docx",
                b"not a zip: renamed junk with a .docx name",
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "File is not a valid Word document (.docx)"


# ---------------------------------------------------------------------
# AC-3 — the failed reply scan
# ---------------------------------------------------------------------

# What the vendor's response body looks like inside the exception the
# scan path raises — the internal that must stay server-side.
COMPOSIO_BODY = (
    '{"error":{"code":"internal_server_error","message":"upstream '
    'connect error or disconnect"}}'
)


def test_ac_3(client, db, monkeypatch, caplog):
    """AC-3: a failed reply scan is a 502 that names the mail service
    and says to try again soon — Composio's response stays in the log."""
    from app.services import composio_service, gmail_scan

    caplog.set_level(logging.ERROR)

    email = f"j2-scan-{uuid.uuid4().hex[:6]}@test.example"
    register(client, email)
    auth_client(client, email)

    # The scan's Composio preconditions hold (configured, Gmail
    # connected) — the failure being tested is the mailbox FETCH.
    monkeypatch.setattr(composio_service, "is_configured", lambda: True)

    async def _gmail_connected(user_id):
        return [{"id": "c1", "app_name": "gmail", "status": "ACTIVE",
                 "created_at": ""}]

    monkeypatch.setattr(composio_service, "list_connections", _gmail_connected)

    exc_text = f"Composio execute GMAIL_SEARCH failed: {COMPOSIO_BODY}"

    async def _failing_fetch(user_id, window, sender_terms=None):
        raise composio_service.ComposioError(exc_text)

    monkeypatch.setattr(gmail_scan, "gmail_fetch_messages", _failing_fetch)

    with caplog.at_level(logging.ERROR, logger="app.api.v1.outcomes"):
        resp = client.post("/api/v1/outcomes/scan")
    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert MAILBOX in detail, detail
    assert SCAN_ADVICE in detail, detail
    assert detail.index(MAILBOX) < detail.index(SCAN_ADVICE), detail

    # The Composio response body and the exception text appear nowhere
    # in the 502...
    assert COMPOSIO_BODY not in detail, detail
    assert exc_text not in detail, detail
    assert "Composio" not in detail, detail
    assert "GMAIL_SEARCH" not in detail, detail

    # ...but ARE in the server log, attached to a traceback.
    records = [
        r
        for r in caplog.records
        if r.name == "app.api.v1.outcomes"
        and r.levelno >= logging.ERROR
        and r.exc_info
    ]
    assert records, f"no ERROR record with a traceback: {caplog.text}"
    assert any(exc_text in r.getMessage() for r in records), [
        r.getMessage() for r in records
    ]
