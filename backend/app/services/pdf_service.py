"""
PDF service — renders the tailored application package (cover letter + CV)
into clean PDFs for sending. Used by the draft submit flow.

fpdf2 with unicode font fallback: macOS Arial Unicode, Linux DejaVu, else
core-font latin-1 transliteration (Swedish å/ä/ö survive all paths).
"""

import logging
import os
import re
from typing import Optional

from fpdf import FPDF

logger = logging.getLogger(__name__)

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
