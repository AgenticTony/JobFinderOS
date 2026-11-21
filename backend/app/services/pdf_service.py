"""
PDF service — renders the tailored application package (cover letter + CV)
into clean PDFs for sending. Used by the draft submit flow.

fpdf2 with unicode font fallback: macOS Arial Unicode, Linux DejaVu, else
core-font latin-1 transliteration (Swedish å/ä/ö survive all paths). When
a unicode font DOES resolve, fpdf2's set_fallback_fonts draws any glyph
the primary lacks (Chinese/Japanese/Korean names) from the first fallback
candidate that has it (J-7).
"""

import io
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fontTools.ttLib import TTFont
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

# J-7: fallback fonts fpdf2 draws from when the PRIMARY font lacks a
# glyph — the gap that blocked every applicant whose name the primary
# font cannot draw (Chinese/Japanese/Korean names under DejaVu, which
# has no CJK glyphs at all). A glyph missing from every font stays a
# blocked send ('characters lost'): the fallback widens what can be
# drawn, it never hides a character nobody could draw.
# Both production images install fonts-wqy-microhei (WenQuanYi Micro
# Hei), a TrueType collection whose glyf outlines cover Chinese,
# Japanese kana and Korean Hangul.
FALLBACK_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # fonts-wqy-microhei
]

# A .ttc (TrueType collection) file holds several faces; both the
# outline probe below and fpdf2's add_font load the collection's FIRST
# font, so a collection candidate means exactly "face 0".
_TTC_FACE_INDEX = 0

_MARKDOWN_RE = re.compile(r"(\*\*|\*|__|##|#)")
_BULLET_RE = re.compile(r"^[\-\u2022\u25cf]\s+")

# J-6: the fixed render date (see _UnicodePDF.__init__).
RENDER_CREATION_DATE = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _resolve_unicode_font() -> Optional[str]:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _has_true_type_outlines(path: str) -> bool:
    """True when the font file's outlines are TrueType (a `glyf` table).

    A .ttc collection is judged on its first face, the one fpdf2 loads.
    A candidate without TrueType outlines is NOT usable as a fallback:
    with the pinned fpdf2 2.8.8 a CFF-outline font's runs extract as
    nothing at all for pdfplumber and as NUL-padded text for pypdf — a
    fallback that breaks the text layer it was adding glyphs to is worse
    than no fallback, so the caller skips it (with a warning)."""
    try:
        font = TTFont(path, fontNumber=_TTC_FACE_INDEX, lazy=True)
    except Exception as e:
        logger.warning("Fallback font %s could not be opened (%s); skipped", path, e)
        return False
    try:
        return "glyf" in font
    finally:
        font.close()


class _UnicodePDF(FPDF):
    """FPDF that degrades gracefully when no unicode TTF is available."""

    unicode_ok = False

    def __init__(self):
        super().__init__(format="A4")
        # J-6: pin the creation date — fpdf2's default is the wall clock,
        # and it lands in BOTH the document (/CreationDate) and the file
        # identifier fpdf2 hashes, so two renders of the same text a
        # second apart came out byte-different. The send path hashes the
        # attachment bytes into its idempotency key, so the render must
        # be a pure function of its input: same text in, same bytes out.
        # The email carrying the PDF is the timestamp the employer sees.
        self.set_creation_date(RENDER_CREATION_DATE)
        font_path = _resolve_unicode_font()
        if font_path:
            try:
                self.add_font("AppFont", "", font_path)
                self.add_font("AppFont", "B", font_path)
                self.unicode_ok = True
                # J-7: only a render that HAS a primary unicode font can
                # fall back — the fontless core-font path below is
                # byte-for-byte today's latin-1 transliteration, with no
                # fallback fonts registered.
                self._register_fallback_fonts()
                return
            except Exception as e:  # fall through to core font
                logger.warning("Unicode font load failed (%s), using Helvetica", e)
        self.set_font("Helvetica", "", 11)

    def _register_fallback_fonts(self) -> None:
        """J-7: register the fallback fonts fpdf2 draws a missing glyph
        from (set_fallback_fonts). Every candidate is optional — a
        missing file, an unreadable one, or one without TrueType
        outlines is skipped, never an error: a candidate that cannot be
        used only costs the glyphs it would have added, while an
        exception raised here would be caught by __init__ and drop the
        WHOLE render to the latin-1 core font."""
        families: List[str] = []
        for index, path in enumerate(FALLBACK_FONT_CANDIDATES):
            if not os.path.exists(path):
                continue
            if not _has_true_type_outlines(path):
                logger.warning(
                    "Fallback font %s has no TrueType (glyf) outlines; "
                    "skipped (fpdf2 2.8.8 renders a CFF-outline font's "
                    "text layer unreadably)",
                    path,
                )
                continue
            family = f"AppFontFallback{index}"
            try:
                # Both styles, so a glyph missing from a BOLD run falls
                # back too (set_fallback_fonts only falls back on an
                # exact emphasis match by default).
                self.add_font(
                    family, "", path, collection_font_number=_TTC_FACE_INDEX
                )
                self.add_font(
                    family, "B", path, collection_font_number=_TTC_FACE_INDEX
                )
            except Exception as e:
                logger.warning(
                    "Fallback font %s failed to load (%s); skipped", path, e
                )
                continue
            families.append(family)
        if not families:
            return
        try:
            self.set_fallback_fonts(families)
        except Exception as e:  # never let a fallback break the render
            logger.warning("Registering fallback fonts failed (%s); ignored", e)

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
    """


# J-1: every blocked-check message begins with exactly one class marker
# (followed by the document name), so a user — or a test — can tell WHICH
# failure fired without parsing prose. The four are the contract's
# literals; nothing downstream may strip or rewrite them.
CLASS_UNREADABLE = "unreadable PDF"
CLASS_TOO_SHORT = "text layer too short"
CLASS_CHARS_LOST = "characters lost"
CLASS_PHRASE_LOST = "required phrase lost"


# The exact literal the send-context message must carry (J-1 contract).
NOT_SENT_OUTCOME = "the application was NOT sent."

# J-3: every class ends with its own what-to-do advice. Only the
# unreadable class advises retrying — a retry can fix a transient read,
# but it cannot restore characters a render lost or text a draft never
# had, so the three content classes each say what to CHANGE instead and
# never say 'try again'.
RETRY_ADVICE = "Please try again; if this keeps happening, contact the operator."
REPLACE_CHARACTERS_ADVICE = "Please replace those characters in the draft."
CHECK_CONTENT_ADVICE = "Please check the draft's content."
CHECK_FORMATTING_ADVICE = "Please check the draft's formatting."
# J-5: the advice the profile name gets. The render draws the applicant's
# name as the document subtitle, so a character lost from the name is not
# in the draft at all — telling the user to "replace those characters in
# the draft" sends them hunting through a document that does not contain
# the character. J-5 therefore sent the user to the profile, where the
# name is edited.
# J-7: the advice is no longer "check your name in your profile" — the
# fallback fonts now draw every name the installed fonts cover, so a
# character the render lost from the NAME can only be a gap in the fonts
# the operator installed, not something the user mistyped (a typo is
# drawn faithfully and never lands in this list). There is nothing the
# user can change; only the operator can add the font.
CHECK_NAME_ADVICE = (
    "Your name contains characters this PDF font cannot show. "
    "Please contact the operator."
)

# The only surfaces a blocked-check message exists for (J-3: anything
# else is a caller bug, refused loudly by every entry point that takes
# a context instead of being silently answered with send wording).
_CONTEXTS = ("send", "download")


def _checked_context(context: str) -> str:
    if context not in _CONTEXTS:
        raise ValueError(f"context must be one of {_CONTEXTS}, got {context!r}")
    return context


def _blocked_pdf_message(
    marker: str, label: str, context: str, reason: str, advice: str
) -> str:
    """The one builder of user-facing blocked-check messages.

    `context` picks the surface's outcome sentence: the send path blocks
    an application (and must say so, exactly, so the user knows nothing
    was dispatched); the manual-portal download path refuses a file and
    must never claim a send was blocked. `advice` is the class's own
    what-to-do sentence (J-3) — only the unreadable class may tell the
    user to try again.
    """
    if context == "send":
        outcome = NOT_SENT_OUTCOME
    elif context == "download":
        outcome = "the download was stopped."
    else:
        raise ValueError(f"context must be one of {_CONTEXTS}, got {context!r}")
    return f"{marker} — {label}: {reason} {outcome} {advice}"


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


# J-5: the two places a lost character can be the user's to fix. The name
# group is the render's subtitle (the profile name); the draft group is
# everything else, including a name character the draft ALSO carries.
NAME_GROUP_LABEL = "from your profile name: "
DRAFT_GROUP_LABEL = "from the draft: "


def _lost_character_groups(
    text: str, applicant_name: Optional[str], extracted: str
) -> Tuple[List[str], List[str]]:
    """Split the characters the render lost into the two groups the
    user can act on (J-5): those lost only from the applicant's name —
    the profile name the render draws as the subtitle — and not present
    in the draft text, and every other lost character (the draft's own,
    including a name character the draft also carries)."""
    name_glyphs = set(_required_glyphs(applicant_name or ""))
    draft_chars = set(text)
    name_group: List[str] = []
    draft_group: List[str] = []
    for ch in _required_glyphs(_source_for_verification(text, applicant_name)):
        if ch in extracted:
            continue
        if ch in name_glyphs and ch not in draft_chars:
            name_group.append(ch)
        else:
            draft_group.append(ch)
    return name_group, draft_group


def _named_characters(chars: List[str]) -> str:
    """J-3's listing, unchanged: each character literally followed by its
    code point in parentheses, up to six, then the overflow count."""
    shown = ", ".join(f"{ch} (U+{ord(ch):04X})" for ch in chars[:6])
    if len(chars) > 6:
        shown += f", and {len(chars) - 6} more"
    return shown


def _chars_lost_reason(
    text: str, applicant_name: Optional[str], extracted: str
) -> Optional[Tuple[str, str]]:
    """The 'characters lost' reason and its advice, pointing each group of
    lost characters at the place the user can fix it (J-5) — or None when
    nothing was lost.

    With no name group the reason and advice are exactly J-3's. With one,
    the characters are listed by group, name group first, and the advice
    covers each group present, name group first."""
    name_group, draft_group = _lost_character_groups(text, applicant_name, extracted)
    if not name_group and not draft_group:
        return None
    if not name_group:
        return (
            "its text layer lost these characters during "
            f"rendering: {_named_characters(draft_group)}.",
            REPLACE_CHARACTERS_ADVICE,
        )
    groups = [f"{NAME_GROUP_LABEL}{_named_characters(name_group)}"]
    advice = [CHECK_NAME_ADVICE]
    if draft_group:
        groups.append(f"{DRAFT_GROUP_LABEL}{_named_characters(draft_group)}")
        advice.append(REPLACE_CHARACTERS_ADVICE)
    return (
        "its text layer lost these characters during "
        f"rendering: {'; '.join(groups)}.",
        " ".join(advice),
    )


def verify_text_layer(
    pdf_bytes: bytes,
    *,
    min_chars: int = MIN_EXTRACTED_CHARS,
    must_contain: Optional[str] = None,
    source_text: Optional[str] = None,
    applicant_name: Optional[str] = None,
    label: str = "document",
    context: str = "send",
) -> str:
    """Assert the PDF is machine-readable: a minimum character count and,
    when the render's source is given (draft text and/or applicant name),
    that every visible non-ascii source character survived extraction
    (glyph substitution detector). Raises PdfTextLayerError — assert,
    never warn (WO-21 design point 2).

    `source_text` is the render's draft/body text; `applicant_name`, when
    given, is the profile name the render draws as the subtitle. Both are
    checked together (every visible non-ascii character in either must
    survive), and J-5 splits what was lost by where the user fixes it: a
    character lost only from the name and not present in the draft text
    is reported 'from your profile name: ' with the profile advice, every
    other lost character as J-3 reported it.

    `context` ('send', the default, or 'download') picks which surface's
    outcome the blocked-check message carries — see
    _blocked_pdf_message; any other context is refused with ValueError.
    The message is built here and nowhere else: callers pass it through
    verbatim (application.error / HTTP detail).
    """
    _checked_context(context)
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
        raise PdfTextLayerError(
            _blocked_pdf_message(
                CLASS_UNREADABLE,
                label,
                context,
                "its PDF structure could not be opened for text "
                "extraction.",
                RETRY_ADVICE,
            )
        ) from e
    extracted = text.strip()
    if len(extracted) < min_chars:
        raise PdfTextLayerError(
            _blocked_pdf_message(
                CLASS_TOO_SHORT,
                label,
                context,
                f"text extraction returned {len(extracted)} characters — "
                "the document has too little readable text.",
                CHECK_CONTENT_ADVICE,
            )
        )
    if source_text is not None or applicant_name is not None:
        # J-3: name each lost character literally followed by its code
        # point — the user can find '—' in their draft, but not 'U+2014'.
        # Up to six per group, then that group's overflow count. J-5:
        # report the name's characters against the profile, where the
        # name is actually edited.
        chars_lost = _chars_lost_reason(source_text or "", applicant_name, text)
        if chars_lost is not None:
            reason, advice = chars_lost
            raise PdfTextLayerError(
                _blocked_pdf_message(CLASS_CHARS_LOST, label, context, reason, advice)
            )
    if must_contain is not None and normalize_ws(must_contain) not in normalize_ws(text):
        raise PdfTextLayerError(
            _blocked_pdf_message(
                CLASS_PHRASE_LOST,
                label,
                context,
                "part of the text did not survive in the document's "
                "text layer.",
                CHECK_FORMATTING_ADVICE,
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
    leaving the service; `context` picks the blocked-check message's
    outcome wording for the surface doing the building ('send' or
    'download' — anything else is refused with ValueError, J-3)."""
    _checked_context(context)
    blob = cover_letter_pdf(cover_letter, applicant_name)
    verify_text_layer(
        blob,
        source_text=cover_letter,
        applicant_name=applicant_name,
        label="cover letter",
        context=context,
    )
    return blob


def verified_tailored_cv_pdf(
    tailored_cv: str, applicant_name: Optional[str], *, context: str = "send"
) -> bytes:
    """Build AND verify the tailored CV (see verified_cover_letter_pdf)."""
    _checked_context(context)
    blob = tailored_cv_pdf(tailored_cv, applicant_name)
    verify_text_layer(
        blob,
        source_text=tailored_cv,
        applicant_name=applicant_name,
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
