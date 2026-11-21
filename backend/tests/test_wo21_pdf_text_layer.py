"""WO-21 — outbound PDF text-layer verification.

pdf_service renders every outbound document (tailored CV, cover letter)
through a unicode-font resolution step with graceful degradation. That is
exactly the kind of path that can silently produce a visually-fine PDF
with a broken or empty text layer — and ATS parsers and employers'
search tools read the text layer, not the glyphs. Nothing asserted
extractability, neither in the suite nor at runtime in the send path.

The three acceptance criteria, plus the regression tests the review round
added:

- test_ac_1  a deliberately broken render (the non-embedded, glyph-only
             core-font path the fontless slim image used to take) FAILS
             the extraction check — the test was proven red before the
             check existed. Including through the send path's OWN wiring:
             the send passes the render's source text, and the
             glyph-survival check fails the substitution without any
             test-supplied phrase.
- test_ac_2  every PDF round-trip the suite ships (cover letter + CV,
             ascii AND non-ascii applicant names) survives reader
             extraction with a minimum character count and a known
             phrase intact — compared whitespace-collapsed, because line
             wrapping splits a source line wherever font metrics decide
             (this went red under DejaVu on the ubuntu CI runner).
- test_ac_3  the runtime check after each PDF build in the send path
             blocks the send with a user-facing error on the
             application's error column — and the send path structurally
             cannot skip it (grep-proved against the function source).
- test_send_path_blocks_substitution_but_not_the_ascii_fallback
             the fontless fallback blocks a letter it glyph-substitutes
             and ships the ascii letter it renders faithfully.
- test_floor_never_blocks_a_short_or_empty_draft
             the character floor catches STRUCTURE (empty text layer),
             never a short legitimate document.
- test_download_endpoints_verify_text_layer
             the manual-portal downloads are outbound documents too —
             verified builders only, refused with the reason on failure.
"""

import inspect
import os

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF

from app.core.database import SessionLocal, engine
from app.main import app
from app.services import draft_service, pdf_service
from app.services.pdf_service import extract_text, normalize_ws

# Built by concatenation so no single credential-shaped literal sits in
# the source (secret scanners flag fixed test passwords; the value is a
# throwaway fixture that never authenticates anything real).
_SCRATCH_DB = "test_wo21.db"


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — the
    # send-path test needs the tables even though it drives
    # submit_draft() directly, never the HTTP layer.
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

# A phrase a real letter would contain, carrying typography OUTSIDE
# latin-1 (the em-dash) — the exact class the latin-1 "replace" fallback
# destroys into "?". Extraction is the same read an ATS parser does.
KNOWN_PHRASE = (
    "I believe my five years of backend experience make me a strong "
    "fit — and I am excited about this role."
)
NON_ASCII_NAME = "Åsa Müllér-Lindqvist"

# J-1: extraction and whitespace-collapsing come from pdf_service — the
# one home for each helper — so this suite observes the SAME reader the
# runtime check uses instead of re-implementing it. (Containment stays
# wrap-insensitive for the reason the old local helper's docstring
# recorded: line wrapping splits a source line anywhere font metrics
# decide — under DejaVu metrics on the ubuntu CI runner the em-dash
# phrase once wrapped mid-phrase and the exact-substring assert went
# red.)


def _graphics_only_pdf() -> bytes:
    """A visually-valid PDF whose text layer is empty — the degenerate
    shape a broken render path can ship: pages and glyphs-as-curves, no
    extractable text at all."""
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_draw_color(0, 0, 0)
    pdf.rect(20.0, 20.0, 150.0, 80.0)
    return bytes(pdf.output())


def test_ac_1(monkeypatch):
    """AC-1: a deliberately broken fixture fails the extraction check.

    The broken fixture is the render itself, not the checker: the font
    resolver is forced to None — precisely the state the fontless
    python:3.12-slim image was in (see tests/test_pdf_fonts.py) — so the
    render falls back to the NON-EMBEDDED core font, and every character
    outside latin-1 is substituted glyph-by-glyph. The known phrase does
    not survive extraction: the check must fail it, not warn.
    """
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
    assert pdf_service._UnicodePDF().unicode_ok is False

    broken = pdf_service.cover_letter_pdf(KNOWN_PHRASE, NON_ASCII_NAME)
    extracted = extract_text(broken)
    # Fixture sanity — the render really is broken the way production was:
    assert KNOWN_PHRASE not in extracted, repr(extracted)
    assert "?" in extracted, repr(extracted)
    assert "—" not in extracted, repr(extracted)

    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verify_text_layer(broken, must_contain=KNOWN_PHRASE, label="cover letter")
    assert "cover letter" in str(excinfo.value).lower()

    # The production wiring must catch this WITHOUT any test-supplied
    # phrase: the send path passes the render's own source text, and the
    # glyph-survival check fails the substitution on its own.
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.send_attachment_pdfs(KNOWN_PHRASE, KNOWN_PHRASE, NON_ASCII_NAME)
    assert "cover letter" in str(excinfo.value).lower()

    # And the degenerate shape — a PDF with no text layer at all — fails
    # on the character floor before any phrase is even consulted.
    with pytest.raises(pdf_service.PdfTextLayerError):
        pdf_service.verify_text_layer(_graphics_only_pdf())


ROUND_TRIP_BODY = (
    "Dear Hiring Manager,\n"
    "\n"
    "I am applying for the backend developer role at Acme.\n"
    + KNOWN_PHRASE
    + "\n"
    "\n"
    "PROFESSIONAL SUMMARY\n"
    "- Python and FastAPI services in daily production use\n"
    "- PostgreSQL schemas designed for multi-tenant row-level security\n"
    "\n"
    "I currently live in Malmö and work in both Swedish and English.\n"
    "\n"
    "Best regards,\n"
)
# 200 is an independent literal floor for these fixtures: the body above is
# ~500 characters, so any render that loses real content to a broken text
# layer lands far below it.
ROUND_TRIP_MIN_CHARS = 200


def test_ac_2():
    """AC-2: every PDF round-trip survives reader extraction.

    Cover letter AND tailored CV paths, ascii AND non-ascii applicant
    names: a minimum extracted-character count AND the known phrase (and
    the applicant's own name — the thing an ATS matches on) survive.
    """
    for render, title in (
        (pdf_service.cover_letter_pdf, "Cover Letter"),
        (pdf_service.tailored_cv_pdf, "Curriculum Vitae"),
    ):
        for applicant in ("Test Candidate", NON_ASCII_NAME):
            blob = render(ROUND_TRIP_BODY, applicant)
            # Through the production check (the render's own source text
            # is what production passes — this is the wiring, not a
            # test-supplied phrase)...
            text = pdf_service.verify_text_layer(
                blob, source_text=f"{applicant}\n{ROUND_TRIP_BODY}", label=title
            )
            # ...and independently at the seam the ATS sees.
            extracted = extract_text(blob)
            hay = normalize_ws(extracted)
            assert extracted.strip() == text
            assert len(extracted) >= ROUND_TRIP_MIN_CHARS, (
                render.__name__, applicant, len(extracted), repr(extracted)
            )
            # Whitespace-collapsed containment: wrapping splits the phrase
            # across lines wherever the font's metrics decide to break.
            assert normalize_ws(KNOWN_PHRASE) in hay, (render.__name__, applicant)
            assert title in hay, (render.__name__, applicant)
            for part in applicant.split():
                assert part in hay, (render.__name__, applicant, part)

    # The ascii-name CV fixture (the classic ATS input) keeps its section
    # headers extractable — the structure an ATS parses, not just prose.
    extracted = extract_text(pdf_service.tailored_cv_pdf(ROUND_TRIP_BODY, "Test Candidate"))
    assert "PROFESSIONAL SUMMARY" in extracted, repr(extracted)


def _seed_ready_email_draft(db):
    """A 'ready' draft on a job that publishes an application email —
    exactly the package submit_draft(method='email') dispatches."""
    import uuid

    from app.models import ApplicationDraft, JobPosting, Profile
    from app.models import User as UserModel

    uid = uuid.uuid4()
    db.add(UserModel(id=uid, email=f"wo21-{uid.hex[:6]}@test.example",
                     hashed_password="test-only"))
    db.flush()
    profile = Profile(user_id=uid, is_active=1, full_name="WO21 Tester",
                      cv_text="WO21 CV", cv_file_name="cv.pdf")
    job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                     title="Dev", company="Acme",
                     url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                     description="A Python role.",
                     application_email="jobs@acme.example")
    db.add_all([profile, job])
    db.flush()
    draft = ApplicationDraft(user_id=uid, job_id=job.id, status="ready",
                             cover_letter=ROUND_TRIP_BODY,
                             tailored_cv=ROUND_TRIP_BODY,
                             changes_summary="[]")
    db.add(draft)
    db.commit()
    return uid, profile, draft


def _live_email_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "apply@jobfinderos.test")
    monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)


def _fake_resend(monkeypatch, sink):
    import sys as _sys

    class _Emails:
        @staticmethod
        def send(params, options=None):
            sink.append(params)
            return {"id": f"msg_{len(sink)}"}

    monkeypatch.setitem(_sys.modules, "resend",
                        type("R", (), {"Emails": _Emails, "api_key": None}))


def test_ac_3(client, db, monkeypatch):
    """AC-3: the runtime check after each PDF build BLOCKS the send.

    The builder is forced to produce the broken fixture from AC-1 (a PDF
    with no text layer) — the send must fail with a user-facing error on
    the application's error column, and NO email may be dispatched: a
    document the employer's parser cannot read is a failed send, not a
    degraded one. Then the structural half: grep the send function's
    source and prove it builds PDFs ONLY through the verifying wrapper.
    """
    uid, profile, draft = _seed_ready_email_draft(db)
    _live_email_settings(monkeypatch)
    sent = []
    _fake_resend(monkeypatch, sent)

    broken = _graphics_only_pdf()
    monkeypatch.setattr(pdf_service, "cover_letter_pdf", lambda text, name: broken)

    from app.services.draft_service import submit_draft

    application = submit_draft(db, draft, "email", profile, user_id=uid)
    assert application.status == "failed", (
        f"a broken text layer must fail the send, got {application.status}"
    )
    error = application.error or ""
    assert "cover letter" in error.lower(), error
    assert "NOT sent" in error, error
    assert not sent, f"the email was dispatched anyway: {[a['filename'] for a in sent[0]['attachments']]}"

    db.refresh(draft)
    assert draft.status == "ready", (
        "a send blocked on its text layer must leave the draft editable"
    )

    # --- grep-proof: the send path cannot skip the check ---
    send_source = inspect.getsource(draft_service._send_with_pdfs)
    assert "pdf_service.send_attachment_pdfs(" in send_source
    # No raw builder call inside the send function: every tailored PDF it
    # attaches has already been through verify_text_layer inside the
    # wrapper — building unverified is not possible from here.
    assert "cover_letter_pdf(" not in send_source, (
        "the send path calls a raw PDF builder — it can skip the check"
    )
    assert "tailored_cv_pdf(" not in send_source, (
        "the send path calls a raw PDF builder — it can skip the check"
    )
    assert "PdfTextLayerError" in send_source
    assert "application.error" in send_source
    # And the wrapper builds BOTH documents through the verified builders,
    # each of which runs verify_text_layer on what it built.
    wrapper_source = inspect.getsource(pdf_service.send_attachment_pdfs)
    assert "verified_cover_letter_pdf(" in wrapper_source
    assert "verified_tailored_cv_pdf(" in wrapper_source
    for builder in (
        pdf_service.verified_cover_letter_pdf,
        pdf_service.verified_tailored_cv_pdf,
    ):
        builder_source = inspect.getsource(builder)
        assert "verify_text_layer(" in builder_source, builder.__name__
        assert "source_text=" in builder_source, (
            f"{builder.__name__} verifies without the render's source "
            "text — the glyph-substitution failure mode would slip through"
        )


ASCII_ONLY_LETTER = (
    "Dear Hiring Manager,\n"
    "\n"
    "I am applying for the backend developer role at Acme. My five years "
    "of backend experience make me a strong fit, and I am excited about "
    "this role.\n"
    "\n"
    "Best regards,\n"
)


def test_send_path_blocks_substitution_but_not_the_ascii_fallback(monkeypatch):
    """The fontless fallback is caught by CONTENT, not by existence.

    With no unicode font at all (the fontless slim image), a letter whose
    typography sits outside latin-1 is glyph-substituted into '?' — that
    send must BLOCK. The very same fallback path rendering a pure-ascii
    letter produces a perfectly readable document — that send must PASS.
    The check separates the two by what survived extraction, not by
    whether the fallback happened.
    """
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
    assert pdf_service._UnicodePDF().unicode_ok is False

    # Curly typography in the fallback: blocked, with the lost characters
    # named in the user-facing error.
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.send_attachment_pdfs(KNOWN_PHRASE, ROUND_TRIP_BODY, NON_ASCII_NAME)
    assert "cover letter" in str(excinfo.value).lower()
    assert "U+2014" in str(excinfo.value)  # the em-dash it destroyed

    # The same fallback, ascii-only content: the render is faithful, the
    # attachments go out.
    attachments = pdf_service.send_attachment_pdfs(
        ASCII_ONLY_LETTER, ASCII_ONLY_LETTER, "Test Candidate"
    )
    assert [name for name, _ in attachments] == [
        "Cover Letter - Test Candidate.pdf",
        "CV - Test Candidate (tailored).pdf",
    ]
    extracted = extract_text(attachments[0][1])
    assert normalize_ws(ASCII_ONLY_LETTER) in normalize_ws(extracted)


def test_floor_never_blocks_a_short_or_empty_draft():
    """The character floor catches STRUCTURE (empty text layer -> 0), not
    short documents: a title-only render — what an empty draft body
    produces — has an intact text layer and must pass, so the runtime
    error never tells a user with an empty draft that their text layer is
    'broken'."""
    for render, title in (
        (pdf_service.cover_letter_pdf, "Cover Letter"),
        (pdf_service.tailored_cv_pdf, "Curriculum Vitae"),
    ):
        # Empty body — the smallest legitimate render the send path sees.
        extracted = pdf_service.verify_text_layer(render("", "A"), label=title.lower())
        assert title in extracted
    # And through the send path, exactly as production calls it: an empty
    # draft still ships its title-only documents.
    assert len(pdf_service.send_attachment_pdfs("", "", "A")) == 2

    # The broken shape is still caught: zero extractable characters.
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verify_text_layer(_graphics_only_pdf(), label="cover letter")
    message = str(excinfo.value)
    assert "text extraction returned 0" in message
    assert "NOT sent" in message


def _seed_download_draft(db):
    """A draft whose cover letter carries the em-dash — the manual-portal
    download path's outbound content."""
    import uuid

    from app.models import ApplicationDraft, JobPosting
    from tests.auth_helpers import get_or_create_user

    uid = get_or_create_user(f"wo21-dl-{uuid.uuid4().hex[:6]}@test.example")
    job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                     title="Dev", company="Acme",
                     url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                     description="A Python role.")
    draft = ApplicationDraft(user_id=uid, job=job, status="ready",
                             cover_letter=ROUND_TRIP_BODY,
                             tailored_cv=ROUND_TRIP_BODY,
                             changes_summary="[]")
    db.add(draft)
    db.commit()
    return uid, draft.id


def test_download_endpoints_verify_text_layer(client, db, monkeypatch):
    """The manual-portal downloads are outbound documents too — the same
    employer ATS reads them — so a broken text layer must be refused with
    the reason, never served as a PDF (reviewer finding U-2)."""
    from tests.auth_helpers import mint_token

    uid, draft_id = _seed_download_draft(db)
    headers = {"Authorization": f"Bearer {mint_token(uid, 'wo21-dl@test.example')}"}

    for path in (
        f"/api/v1/applications/draft/{draft_id}/download/cover-letter",
        f"/api/v1/applications/draft/{draft_id}/download/cv",
    ):
        # Healthy render: served, and the em-dash phrase is readable.
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
        assert resp.headers["content-type"] == "application/pdf"
        extracted = extract_text(resp.content)
        assert normalize_ws(KNOWN_PHRASE) in normalize_ws(extracted), path

        # Fontless render (the AC-1 failure class): refused with the
        # verification reason, not served as a glyph-substituted PDF.
        monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
        resp = client.get(path, headers=headers)
        assert resp.status_code == 500, (path, resp.status_code, resp.text)
        assert "text layer" in resp.json()["detail"].lower(), resp.text
        monkeypatch.undo()
