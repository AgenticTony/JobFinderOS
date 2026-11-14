"""
PDF service — renders the tailored application package (cover letter + CV)
into clean PDFs for sending. Used by the draft submit flow.

fpdf2 with unicode font fallback: macOS Arial Unicode, Linux DejaVu, else
core-font latin-1 transliteration (Swedish å/ä/ö survive all paths).
"""

import io
import logging
import os
import re
from typing import List, Optional, Tuple

from fpdf import FPDF
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# WO-21: ATS parsers and employers' search tools read the text layer, not
# the glyphs — so every outbound PDF is re-opened with pypdf and checked
# before it leaves. The floor sits BELOW the smallest real document the
# render can produce (the fixed "Curriculum Vitae" title alone extracts
# 16 chars; the smallest test fixture with a body extracts 35+) and ABOVE
# anything a structurally broken render produces (empty text layer -> 0,
# title-only degenerate render -> 12-16).
MIN_EXTRACTED_CHARS = 25

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS (no full unicode but wide)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",  # Linux alt
]

_MARKDOWN_RE = re.compile(r"(\*\*|\*|__|##|#)")
_BULLET_RE = re.compile(r"^[\-\u2022\u25cf]\s+")


def _resolve_unicode_font() -> Optional[str]:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


class _UnicodePDF(FPDF):
    """FPDF that degrades gracefully when no unicode TTF is available."""

    unicode_ok = False

    def __init__(self):
        super().__init__(format="A4")
        font_path = _resolve_unicode_font()
        if font_path:
            try:
                self.add_font("AppFont", "", font_path)
                self.add_font("AppFont", "B", font_path)
                self.unicode_ok = True
                return
            except Exception as e:  # fall through to core font
                logger.warning("Unicode font load failed (%s), using Helvetica", e)
        self.set_font("Helvetica", "", 11)

    def _clean(self, text: str) -> str:
        text = _MARKDOWN_RE.sub("", text)
        if not self.unicode_ok:
            text = text.encode("latin-1", "replace").decode("latin-1")
        return text

    def body(self, title: str, text: str, subtitle: Optional[str] = None) -> None:
        self.add_page()
        self.set_font("AppFont" if self.unicode_ok else "Helvetica", "B", 16)
        self.cell(0, 10, self._clean(title), new_x="LMARGIN", new_y="NEXT")
        if subtitle:
            self.set_font("AppFont" if self.unicode_ok else "Helvetica", "", 10)
            self.set_text_color(110, 110, 110)
            self.cell(0, 6, self._clean(subtitle), new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
        self.ln(4)

        self.set_font("AppFont" if self.unicode_ok else "Helvetica", "", 11)
        for line in text.splitlines():
            line = line.rstrip()
            if not line:
                self.ln(3)
                continue
            # Section headers (short ALL-CAPS or former markdown headings)
            if len(line) <= 60 and line.isupper():
                self.ln(2)
                self.set_font("AppFont" if self.unicode_ok else "Helvetica", "B", 12)
                self.multi_cell(0, 6, self._clean(line), new_x="LMARGIN", new_y="NEXT")
                self.set_font("AppFont" if self.unicode_ok else "Helvetica", "", 11)
            elif _BULLET_RE.match(line):
                self.multi_cell(0, 6, "  " + self._clean(_BULLET_RE.sub("", line)), new_x="LMARGIN", new_y="NEXT")
            else:
                self.multi_cell(0, 6, self._clean(line), new_x="LMARGIN", new_y="NEXT")


class PdfTextLayerError(Exception):
    """WO-21: a rendered PDF's text layer failed verification.

    A document the employer's parser cannot read is a FAILED send, not a
    degraded one — the message is written to the application's user-facing
    `error` column verbatim.
    """


def extract_text(pdf_bytes: bytes) -> str:
    """Extract the text layer of a rendered PDF (all pages, pypdf)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def verify_text_layer(
    pdf_bytes: bytes,
    *,
    min_chars: int = MIN_EXTRACTED_CHARS,
    must_contain: Optional[str] = None,
    label: str = "document",
) -> str:
    """Assert the PDF is machine-readable: a minimum character count and,
    when given, a known phrase that must survive extraction. Raises
    PdfTextLayerError — assert, never warn (WO-21 design point 2)."""
    try:
        text = extract_text(pdf_bytes)
    except Exception as e:  # unreadable structure is a broken layer too
        raise PdfTextLayerError(
            f"The {label} could not be opened for text extraction ({e}) — "
            "the application was NOT sent. Please re-open the draft and try "
            "again; if this keeps happening, contact the operator."
        ) from e
    extracted = text.strip()
    if len(extracted) < min_chars:
        raise PdfTextLayerError(
            f"The {label}'s text layer looks broken — text extraction "
            f"returned {len(extracted)} characters (expected at least "
            f"{min_chars}). ATS parsers and search tools read this layer, "
            "so the application was NOT sent. Please re-open the draft and "
            "try again; if this keeps happening, contact the operator."
        )
    if must_contain is not None and must_contain not in extracted:
        raise PdfTextLayerError(
            f"The {label}'s text layer lost part of its content during "
            "rendering — the phrase a parser must be able to read did not "
            "survive extraction, so the application was NOT sent. Please "
            "re-open the draft and try again; if this keeps happening, "
            "contact the operator."
        )
    return extracted


def cover_letter_pdf(cover_letter: str, applicant_name: Optional[str]) -> bytes:
    """Render the cover letter as a one-page-ish PDF."""
    pdf = _UnicodePDF()
    pdf.body(
        title="Cover Letter",
        subtitle=applicant_name,
        text=cover_letter,
    )
    return bytes(pdf.output())


def tailored_cv_pdf(tailored_cv: str, applicant_name: Optional[str]) -> bytes:
    """Render the tailored CV text as a clean PDF."""
    pdf = _UnicodePDF()
    pdf.body(
        title="Curriculum Vitae",
        subtitle=applicant_name,
        text=tailored_cv,
    )
    return bytes(pdf.output())


def send_attachment_pdfs(
    cover_letter: str, tailored_cv: str, applicant_name: Optional[str]
) -> List[Tuple[str, bytes]]:
    """WO-21: build BOTH tailored documents for the send path, verifying
    each one's text layer.

    This is the ONLY build entry the send path may use — every PDF it
    attaches has already been through verify_text_layer here, so a broken
    text layer raises PdfTextLayerError before anything is dispatched.
    (tests/test_wo21_pdf_text_layer.py::test_ac_3 greps the send function
    to keep it that way.)
    """
    letter = cover_letter_pdf(cover_letter, applicant_name)
    verify_text_layer(letter, label="cover letter")
    cv = tailored_cv_pdf(tailored_cv, applicant_name)
    verify_text_layer(cv, label="tailored CV")
    return [
        (f"Cover Letter - {applicant_name or 'Applicant'}.pdf", letter),
        (f"CV - {applicant_name or 'Applicant'} (tailored).pdf", cv),
    ]
