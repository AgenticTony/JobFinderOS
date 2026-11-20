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
import unicodedata
from typing import List, Optional, Tuple

from fpdf import FPDF
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# WO-21: ATS parsers and employers' search tools read the text layer, not
# the glyphs — so every outbound PDF is re-opened with pypdf and checked
# before it leaves. The floor's ONLY job is to catch a structurally
# broken render (empty text layer -> 0 extracted characters), so it sits
# ABOVE anything broken produces and BELOW the smallest legitimate
# render: a title-only document ("Cover Letter" + no body) extracts 12-16
# characters, and a draft with no body yet is the user's content
# decision, not a broken text layer — the floor must never be the thing
# that reports "broken" for it. Character-level content loss is caught by
# the glyph-survival check in verify_text_layer (source_text), which does
# not depend on document length at all.
MIN_EXTRACTED_CHARS = 10

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

    J-1: the message is built HERE, once, and names the failure class and
    the document for the surface it blocks. Library internals (reader
    exception text) never appear in it — they go to the server log.

    J-3: per class, the message says what is wrong and what to do — only
    the unreadable class advises retrying; the three content classes end
    with their own advice, because their failure lives in the draft.
    """


# J-1: every blocked-check message begins with exactly one class marker
# (followed by the document name), so a user — or a test — can tell WHICH
# failure fired without parsing prose. The four are the contract's
# literals; nothing downstream may strip or rewrite them.
CLASS_UNREADABLE = "unreadable PDF"
CLASS_TOO_SHORT = "text layer too short"
CLASS_CHARS_LOST = "characters lost"
CLASS_PHRASE_LOST = "required phrase lost"


# The exact literal the send-context message must carry (contract AC-1):
# the user must know, in so many words, that nothing was dispatched.
NOT_SENT_OUTCOME = "the application was NOT sent."

# J-3: the only two surfaces a blocked PDF check can speak for — the
# send path (it blocks an application) and the manual-portal download
# path (it refuses a file). Anything else is a programming error and is
# refused here rather than silently answered with send wording.
VALID_CONTEXTS = ("send", "download")

# J-3: what the user should DO about the failure. Retrying only helps a
# file the reader cannot even open; the three content classes live in
# the DRAFT, so their advice points the user back into the draft and
# never says "try again". The retry literal is the contract's own.
RETRY_ADVICE = "try again; if this keeps happening, contact the operator"
ADVICE_REPLACE_CHARACTERS = "Replace those characters in the draft."
ADVICE_CHECK_CONTENT = "Check the draft's content."
ADVICE_CHECK_FORMATTING = "Check the draft's formatting."


def _check_context(context: str) -> None:
    """Refuse a context that names no real surface (J-3): the message's
    outcome wording is per-surface, so an unknown surface has no honest
    wording to get."""
    if context not in VALID_CONTEXTS:
        raise ValueError(
            f"context must be one of {VALID_CONTEXTS}, got {context!r}"
        )


def _blocked_pdf_message(
    marker: str, label: str, context: str, reason: str, advice: Optional[str]
) -> str:
    """The one builder of user-facing blocked-check messages.

    Shape (J-1, kept): the class marker first, then the document name,
    then the surface's outcome — the send path blocks an application and
    must say so exactly, so the user knows nothing was dispatched; the
    manual-portal download path refuses a file and must never claim a
    send was blocked. J-3 adds the two halves a blocked check owes the
    user: WHY it failed (`reason`) and WHAT TO DO (`advice` — the
    sentence the message ends with, per class; `None` means this class's
    remedy is the retry sentence, which only the unreadable class passes).
    """
    _check_context(context)
    if context == "download":
        outcome = "the download was stopped."
        advice = advice or f"Please {RETRY_ADVICE}."
    else:
        outcome = NOT_SENT_OUTCOME
        advice = advice or f"Please re-open the draft and {RETRY_ADVICE}."
    return f"{marker} — {label}: {outcome} {reason} {advice}"


def extract_text(pdf_bytes: bytes) -> str:
    """Extract the text layer of a rendered PDF (all pages, pypdf)."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def normalize_ws(text: str) -> str:
    """Collapse whitespace runs: line wrapping inserts breaks anywhere in
    a paragraph (font metrics decide where), so a phrase that the source
    holds on one line extracts split across two. Phrase containment is
    compared whitespace-insensitively on both sides.

    J-1: THE whitespace-collapse helper (renamed from _normalize_ws) —
    the suite imports it from here instead of re-implementing it."""
    return re.sub(r"\s+", " ", text).strip()


# The renderer strips these leading bullet glyphs (replaced by an
# indented continuation), so their absence is layout, not a broken layer.
_RENDER_STRIPPED_GLYPHS = {"•", "●"}
# Categories layout legitimately normalises or drops without the reader
# seeing different content: control, format (zero-width, bidi marks) and
# space separators (NBSP may extract as a plain space).
_INVISIBLE_CATEGORIES = ("Cc", "Cf", "Zs", "Zl", "Zp", "Cs")


def _required_glyphs(source: str) -> List[str]:
    """The visible non-ascii characters the text layer MUST preserve.

    This is the substitution detector: the latin-1 fallback render turns
    every character outside latin-1 into a literal '?' (the shipped-'?'
    production incident), and a font missing a glyph draws .notdef — in
    both cases the character silently vanishes from the extraction while
    the document still LOOKS plausible. Requiring each visible non-ascii
    source character to survive makes that failure mode a hard failure,
    independent of document length."""
    return sorted(
        ch
        for ch in set(source)
        if ord(ch) > 127
        and ch not in _RENDER_STRIPPED_GLYPHS
        and unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )


def _source_for_verification(text: str, applicant_name: Optional[str]) -> str:
    """Everything the render was given to draw — the applicant name
    (subtitle) and the body — as one string for the glyph-survival check."""
    return "\n".join(part for part in (applicant_name, text) if part)


def verify_text_layer(
    pdf_bytes: bytes,
    *,
    min_chars: int = MIN_EXTRACTED_CHARS,
    must_contain: Optional[str] = None,
    source_text: Optional[str] = None,
    label: str = "document",
    context: str = "send",
) -> str:
    """Assert the PDF is machine-readable: a minimum character count and,
    when the render's source text is given, that every visible non-ascii
    source character survived extraction (glyph substitution detector).
    Raises PdfTextLayerError — assert, never warn (WO-21 design point 2).

    `context` ('send', the default, or 'download'; anything else raises
    ValueError) picks which surface's outcome the blocked-check message
    carries — see _blocked_pdf_message. The message is built here and
    nowhere else: callers pass it through verbatim (application.error /
    HTTP detail).
    """
    _check_context(context)
    try:
        text = extract_text(pdf_bytes)
    except Exception as e:  # unreadable structure is a broken layer too
        # J-1: the ORIGINAL exception (message, with traceback) is
        # server-side knowledge — the operator debugs from the log; the
        # user-facing message below names the class and the document and
        # carries none of the library's text. Both outbound surfaces
        # (send, download) funnel through here, so both get this record.
        logger.error(
            "pypdf text extraction failed on the %s (%s context): %s",
            label,
            context,
            e,
            exc_info=True,
        )
        # J-3: the one class whose remedy is a retry — the file could not
        # even be opened, so another attempt (and then the operator) is
        # the honest advice.
        raise PdfTextLayerError(
            _blocked_pdf_message(
                CLASS_UNREADABLE,
                label,
                context,
                "Its PDF structure could not be opened for text extraction.",
                None,
            )
        ) from e
    extracted = text.strip()
    if len(extracted) < min_chars:
        raise PdfTextLayerError(
            _blocked_pdf_message(
                CLASS_TOO_SHORT,
                label,
                context,
                "The document has too little readable text: "
                f"text extraction returned {len(extracted)} characters, "
                f"fewer than the {min_chars} a machine-readable document "
                "carries.",
                ADVICE_CHECK_CONTENT,
            )
        )
    if source_text is not None:
        missing = [ch for ch in _required_glyphs(source_text) if ch not in text]
        if missing:
            # J-3: each lost character is named literally, then its code
            # point — '— (U+2014)' — so the user can find it in the draft;
            # six are named, the rest are counted.
            shown = ", ".join(f"{ch} (U+{ord(ch):04X})" for ch in missing[:6])
            if len(missing) > 6:
                shown += f" and {len(missing) - 6} more"
            raise PdfTextLayerError(
                _blocked_pdf_message(
                    CLASS_CHARS_LOST,
                    label,
                    context,
                    "Its text layer lost characters during rendering: "
                    f"{shown} did not survive extraction.",
                    ADVICE_REPLACE_CHARACTERS,
                )
            )
    if must_contain is not None and normalize_ws(must_contain) not in normalize_ws(text):
        raise PdfTextLayerError(
            _blocked_pdf_message(
                CLASS_PHRASE_LOST,
                label,
                context,
                "Its text layer lost part of the text — a phrase the "
                "source document contains did not survive extraction.",
                ADVICE_CHECK_FORMATTING,
            )
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


def verified_cover_letter_pdf(
    cover_letter: str, applicant_name: Optional[str], *, context: str = "send"
) -> bytes:
    """Build AND verify: the cover-letter entry BOTH outbound paths use —
    the email send and the manual-portal download (the same employer ATS
    reads both). A broken text layer raises PdfTextLayerError instead of
    leaving the service; `context` ('send'/'download', anything else
    ValueError) picks the blocked-check message's outcome wording for
    the surface doing the building."""
    _check_context(context)
    blob = cover_letter_pdf(cover_letter, applicant_name)
    verify_text_layer(
        blob,
        source_text=_source_for_verification(cover_letter, applicant_name),
        label="cover letter",
        context=context,
    )
    return blob


def verified_tailored_cv_pdf(
    tailored_cv: str, applicant_name: Optional[str], *, context: str = "send"
) -> bytes:
    """Build AND verify the tailored CV (see verified_cover_letter_pdf)."""
    _check_context(context)
    blob = tailored_cv_pdf(tailored_cv, applicant_name)
    verify_text_layer(
        blob,
        source_text=_source_for_verification(tailored_cv, applicant_name),
        label="tailored CV",
        context=context,
    )
    return blob


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
    letter = verified_cover_letter_pdf(cover_letter, applicant_name)
    cv = verified_tailored_cv_pdf(tailored_cv, applicant_name)
    return [
        (f"Cover Letter - {applicant_name or 'Applicant'}.pdf", letter),
        (f"CV - {applicant_name or 'Applicant'} (tailored).pdf", cv),
    ]
