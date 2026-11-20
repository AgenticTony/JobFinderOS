"""J-3 — J-1 follow-up: the blocked-PDF-check messages say what is wrong
and what to DO, per failure class, on both surfaces.

J-1 gave every blocked-check message exactly one class marker, the
document name and the surface's outcome sentence ('the application was
NOT sent.' on send, never a send claim on download). What the message
never told the user is what to DO: every class ended with 'try again' —
including the three content classes a retry cannot fix — and the
content classes never named the lost characters or said what to change
in the draft.

test_ac_1 holds the message verify_text_layer builds (the one home of
blocked-check wording, per J-1) to the J-3 rules, for each failure class
on BOTH surfaces (context 'send' and 'download'):

- 'characters lost' names each lost character literally followed by its
  code point in parentheses — '— (U+2014)' — up to six, then
  'and N more', and says to replace those characters in the draft;
- 'text layer too short' keeps 'text extraction returned N characters'
  and says the document has too little readable text and to check the
  draft's content;
- 'required phrase lost' says part of the text did not survive and to
  check the draft's formatting;
- 'characters lost' and 'required phrase lost' keep the words
  'text layer';
- only 'unreadable PDF' advises retrying — with the literal 'try again;
  if this keeps happening, contact the operator' — and the three
  content classes never say 'try again';
- every message keeps J-1's shape: one class marker first, the document
  name, 'the application was NOT sent.' on send, never 'NOT sent' on
  download;
- verify_text_layer and the verified builders refuse any context other
  than 'send' or 'download' with ValueError.

Every expected string below is a literal from the approved contract (or
a deterministic property of the fixture — the 0 a PDF with no text layer
at all extracts), never a recomputation of the service's wording.
"""

import pytest
from fpdf import FPDF

from app.services import pdf_service

# The four class markers and the outcome literals, verbatim from the
# approved contracts (J-1's shape, kept by J-3).
MARKERS = (
    "unreadable PDF",
    "text layer too short",
    "characters lost",
    "required phrase lost",
)
NOT_SENT = "the application was NOT sent."
RETRY = "try again; if this keeps happening, contact the operator"
DOCUMENT = "cover letter"

# A body every render path draws faithfully (pure ascii) — the healthy
# document the class-forcing inputs below start from.
HEALTHY_BODY = (
    "Dear Hiring Manager,\n\nI am applying for the backend developer "
    "role at Acme. My five years of experience make me a strong fit, "
    "and I am excited about this role.\n\nBest regards,\n"
)

# Bytes no PDF reader can open: extraction itself raises on them (the
# 'unreadable PDF' class).
GARBAGE_PDF = b"this is not a pdf; the reader must raise on it"

# Two visible non-ascii characters (em-dash, a-ring) the healthy render
# never drew — the 'characters lost' class with few enough lost
# characters that the message must name every one.
TWO_LOST = HEALTHY_BODY + "\n— å"
# Eight distinct visible non-ascii characters — more than the six the
# message names, so it must finish with the overflow count.
EIGHT_LOST = HEALTHY_BODY + "\n— å ä ö ç ñ ü ï"

# A phrase the healthy document does not contain — the 'required phrase
# lost' class.
ABSENT_PHRASE = "a phrase that appears nowhere in this document"


def _no_text_pdf() -> bytes:
    """A visually-valid PDF whose text layer is empty — a page with a
    drawn rectangle and nothing extractable: the 'text layer too short'
    class, with the deterministic count 0."""
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_draw_color(0, 0, 0)
    pdf.rect(20.0, 20.0, 150.0, 80.0)
    return bytes(pdf.output())


def _blocked(pdf_bytes, context, **kwargs) -> str:
    """The message verify_text_layer itself builds for this input — the
    single place blocked-check wording is decided (J-1), observed here
    on one surface at a time."""
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verify_text_layer(
            pdf_bytes, context=context, label=DOCUMENT, **kwargs
        )
    return str(excinfo.value)


def _assert_shape(message, marker, context) -> None:
    """J-1's shape, which J-3 keeps: one class marker first (and exactly
    one anywhere in the message), the document named, and the surface's
    outcome — send says the application was NOT sent, download never
    says 'NOT sent'."""
    assert message.startswith(marker), (marker, context, message)
    assert sum(message.count(m) for m in MARKERS) == 1, message
    assert DOCUMENT in message, message
    if context == "send":
        assert NOT_SENT in message, message
    else:
        assert "NOT sent" not in message, message


SURFACES = ("send", "download")


def test_ac_1():
    """AC-1: each blocked-check message says what is wrong and what to
    do, per class, on both surfaces — and only the unreadable class
    advises retrying."""
    healthy = pdf_service.cover_letter_pdf(HEALTHY_BODY, "Test Candidate")

    # --- per class, per surface: the shape holds, and the retry advice
    # belongs to 'unreadable PDF' alone ---
    per_class = [
        ("unreadable PDF", lambda ctx: _blocked(GARBAGE_PDF, ctx)),
        ("text layer too short", lambda ctx: _blocked(_no_text_pdf(), ctx)),
        ("characters lost", lambda ctx: _blocked(healthy, ctx, source_text=TWO_LOST)),
        ("required phrase lost", lambda ctx: _blocked(healthy, ctx, must_contain=ABSENT_PHRASE)),
    ]
    for marker, message_for in per_class:
        for context in SURFACES:
            message = message_for(context)
            _assert_shape(message, marker, context)
            if marker == "unreadable PDF":
                assert RETRY in message, (context, message)
            else:
                # the three content classes never say 'try again' — a
                # retry cannot restore what the render lost
                assert "try again" not in message, (marker, context, message)

    # --- 'characters lost' names the lost characters literally, then
    # the overflow past six ---
    for context in SURFACES:
        message = _blocked(healthy, context, source_text=TWO_LOST)
        assert "— (U+2014)" in message, (context, message)  # the contract's example
        assert "å (U+00E5)" in message, (context, message)
        assert message.count(" (U+") == 2, (context, message)
        assert "more" not in message, (context, message)  # nothing was held back
        assert "replace those characters in the draft" in message, (context, message)
        assert "text layer" in message, (context, message)

        message = _blocked(healthy, context, source_text=EIGHT_LOST)
        assert message.count(" (U+") == 6, (context, message)  # names at most six
        assert "and 2 more" in message, (context, message)

    # --- 'text layer too short' keeps the count and says what to do ---
    for context in SURFACES:
        message = _blocked(_no_text_pdf(), context)
        assert "text extraction returned 0 characters" in message, (context, message)
        assert "too little readable text" in message, (context, message)
        assert "check the draft's content" in message, (context, message)

    # --- 'required phrase lost' says what to do ---
    for context in SURFACES:
        message = _blocked(healthy, context, must_contain=ABSENT_PHRASE)
        assert "part of the text did not survive" in message, (context, message)
        assert "check the draft's formatting" in message, (context, message)
        assert "text layer" in message, (context, message)

    # --- 'send' and 'download' are the only contexts; every other is
    # a caller bug and must be refused, at every entry point that takes
    # a context ---
    for bad in ("preview", "", "SEND", None):
        with pytest.raises(ValueError):
            pdf_service.verify_text_layer(healthy, context=bad, label=DOCUMENT)
        with pytest.raises(ValueError):
            pdf_service.verified_cover_letter_pdf(HEALTHY_BODY, "Test Candidate", context=bad)
        with pytest.raises(ValueError):
            pdf_service.verified_tailored_cv_pdf(HEALTHY_BODY, "Test Candidate", context=bad)

    # The two surfaces themselves still verify through every entry point.
    assert pdf_service.verify_text_layer(healthy, context="send", label=DOCUMENT)
    assert pdf_service.verify_text_layer(healthy, context="download", label=DOCUMENT)
    assert pdf_service.verified_cover_letter_pdf(
        HEALTHY_BODY, "Test Candidate", context="download"
    )
    assert pdf_service.verified_tailored_cv_pdf(
        HEALTHY_BODY, "Test Candidate", context="send"
    )
