"""J-7 — PDFs draw Chinese, Japanese and Korean names; a name the fonts
cannot show gets honest advice.

Before J-7 the render had ONE unicode font: whatever FONT_CANDIDATES
resolved first. DejaVu (the production images' font) has no CJK glyphs
at all, so an applicant named 王伟 could not be sent anything — the
render drew .notdef, extraction lost the characters, and every outbound
document for that applicant was blocked with 'characters lost'.

test_ac_1  the renderer gains a fallback chain (fpdf2's
           set_fallback_fonts): a glyph the primary font lacks is drawn
           from the first fallback candidate that has it. Every
           candidate is optional — a missing file is skipped silently,
           and a candidate without TrueType (glyf) outlines is skipped
           with a warning, because with the pinned fpdf2 2.8.8 a
           CFF-outline font's runs extract as nothing at all for
           pdfplumber and as NUL-padded text for pypdf. A .ttc
           collection is loaded as its FIRST font. When the primary
           resolves to nothing the render is exactly the old latin-1
           core-font path, with no fallback fonts consulted.
test_ac_2  both production images install fonts-wqy-microhei (WenQuanYi
           Micro Hei — TrueType outlines covering Chinese, Japanese kana
           and Korean Hangul) beside fonts-dejavu-core, in the same
           layer with its apt-lists cleanup, and pdf_service's fallback
           candidate list names the file that package installs.
test_ac_3  the name group's advice becomes 'Your name contains
           characters this PDF font cannot show. Please contact the
           operator.' — with fallback fonts installed, a character lost
           from the NAME can only be a gap in the fonts the operator
           installed (a typo is drawn faithfully and never lands in the
           lost list), so there is nothing in the profile to check.
           Everything else in J-5's message is unchanged.

Every font below is BUILT at test time with fontTools.fontBuilder (glyf
outlines except the one deliberately-CFF candidate): no font file is
committed, and nothing depends on the host's fonts — the test runs
identically on the macOS host and the CI Linux host, with no skips.
"""

import logging
import pathlib

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont

from app.services import pdf_service

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]

# The name at the centre of the contract, plus a Japanese and a Korean
# name drawn by the same mechanism (a glyph the Latin primary lacks).
CJK_NAME = "王伟"
JP_NAME = "山田太郎"
KR_NAME = "김민수"

# What the built fonts cover. The primary is Latin-only (like DejaVu in
# the production images): everything the letter body needs, and NOT ONE
# CJK character. The fallback carries ONLY the CJK names above — nothing
# the primary already has — so any CJK glyph in a passing render's text
# layer can only have been drawn from the fallback font.
LATIN_CHARSET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 .,;:!?'\"()-—’éèçüåäöô"
)
FALLBACK_CHARSET = CJK_NAME + JP_NAME + KR_NAME

# A body whose every character the Latin primary covers — including the
# 'é' the contract names, drawn from the PRIMARY, not the fallback.
BODY = (
    "Dear Hiring Manager,\n"
    "\n"
    "I am applying for the backend developer role at Acme. My five "
    "years of experience make me a strong fit — and I am excited about "
    "this rôle in your géniale équipe.\n"
    "\n"
    "Best regards,\n"
)

# A body the fontless core-font path draws faithfully (pure ascii) —
# the healthy document the J-5 message fixtures below start from, so a
# lost character can only come from the NAME.
ASCII_BODY = (
    "Dear Hiring Manager,\n"
    "\n"
    "I am applying for the backend developer role at Acme. My five "
    "years of experience make me a strong fit, and I am excited about "
    "this role.\n"
    "\n"
    "Best regards,\n"
)

# A profile name whose first character sits outside latin-1 — the shape
# the fontless core-font path substitutes with '?' (J-5's fixture).
NAME_LOST = "Łukasz Nowak"

# J-7's name advice, verbatim from the approved contract — and the J-5
# sentence it replaces (asserted ABSENT, so the old wording cannot
# survive the change unnoticed).
NEW_NAME_ADVICE = (
    "Your name contains characters this PDF font cannot show. "
    "Please contact the operator."
)
OLD_NAME_ADVICE = "Please check your name in your profile."
DRAFT_ADVICE = "Please replace those characters in the draft."
FROM_NAME = "from your profile name: "
FROM_DRAFT = "; from the draft: "
NOT_SENT = "the application was NOT sent."
# J-1's four class markers — a blocked-check message carries exactly one.
MARKERS = (
    "unreadable PDF",
    "text layer too short",
    "characters lost",
    "required phrase lost",
)


def _box(pen, width=500, height=700):
    """A closed rectangle outline — a real drawn glyph, however plain."""
    pen.moveTo((0, 0))
    pen.lineTo((0, height))
    pen.lineTo((width, height))
    pen.lineTo((width, 0))
    pen.closePath()
    return pen


def _build_font(path, charset, family, *, cff=False):
    """Build a minimal font file covering exactly `charset` (plus space).

    glyf outlines by default (TrueType) — the outline kind the renderer
    accepts; with cff=True the very same coverage is laid out as CFF
    charstrings in an OTF, the outline kind it must refuse.
    """
    charset = set(charset) | {" "}
    cmap = {0x20: "space"}
    names = ["space"]
    for index, char in enumerate(sorted(c for c in charset if c != " ")):
        name = f"g{index}"
        cmap[ord(char)] = name
        names.append(name)
    glyph_order = [".notdef"] + names
    builder = FontBuilder(1000, isTTF=not cff)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(cmap)
    widths = {name: (300 if name == "space" else 500, 0) for name in glyph_order}
    if cff:
        char_strings = {}
        for name in glyph_order:
            pen = T2CharStringPen(widths[name][0], None)
            if name != ".notdef":
                _box(pen)
            char_strings[name] = pen.getCharString()
        builder.setupCFF(family, {"FullName": family}, char_strings, {})
    else:
        glyphs = {".notdef": _box(TTGlyphPen(None), 300, 300).glyph()}
        for name in names:
            glyphs[name] = _box(TTGlyphPen(None)).glyph()
        builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(widths)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(str(path))
    return str(path)


def _build_collection(path, faces):
    """A .ttc collection from the given TTFont faces, face 0 first."""
    collection = TTCollection()
    collection.fonts = list(faces)
    collection.save(str(path))
    return str(path)


def _font_object(path, charset, family):
    """A built font as a TTFont object, for collecting into a .ttc."""
    _build_font(path, charset, family)
    return TTFont(path)


@pytest.fixture()
def built_fonts(tmp_path):
    """The contract's fixture pair: a Latin-only primary and a fallback
    carrying the CJK names, both with TrueType (glyf) outlines."""
    return (
        _build_font(tmp_path / "primary.ttf", LATIN_CHARSET, "J7Primary"),
        _build_font(tmp_path / "fallback.ttf", FALLBACK_CHARSET, "J7Fallback"),
    )


def _use_fonts(monkeypatch, primary, fallbacks):
    """Point the renderer at a built primary and a fallback candidate
    list (empty = no fallback font available)."""
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: primary)
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", list(fallbacks))


def test_ac_1(built_fonts, monkeypatch, caplog):
    """AC-1: with the primary font resolved, glyphs it lacks are drawn
    from fallback fonts registered via set_fallback_fonts — so a cover
    letter and a tailored CV for 王伟 (body carrying 'é') pass
    verify_text_layer; with the fallback gone the same render is blocked
    as 'characters lost'. Missing and non-TrueType candidates are
    skipped, a .ttc collection is loaded as its first font, and a
    fontless render never consults a fallback at all."""
    primary, fallback = built_fonts

    # --- the contract's render: 王伟 drawn from the fallback, é from the
    # primary, through BOTH production builders (each runs
    # verify_text_layer on what it built — a lost glyph raises) ---
    _use_fonts(monkeypatch, primary, [fallback])
    for build in (
        pdf_service.verified_cover_letter_pdf,
        pdf_service.verified_tailored_cv_pdf,
    ):
        blob = build(BODY, CJK_NAME)
        extracted = pdf_service.extract_text(blob)
        assert "王" in extracted and "伟" in extracted, (build.__name__, extracted)
        assert "é" in extracted, (build.__name__, extracted)
        assert "rôle" in pdf_service.normalize_ws(extracted), (
            build.__name__, extracted
        )

    # The same mechanism draws a Japanese and a Korean name.
    for name in (JP_NAME, KR_NAME):
        extracted = pdf_service.extract_text(
            pdf_service.verified_cover_letter_pdf(BODY, name)
        )
        for part in name.split():
            assert part in extracted, (name, extracted)

    # --- the fallback removed: the very same render loses the name and
    # is blocked — which also proves the primary itself covers none of
    # 王伟 (the render above can only have drawn them from the fallback) ---
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", [])
    for build in (
        pdf_service.verified_cover_letter_pdf,
        pdf_service.verified_tailored_cv_pdf,
    ):
        with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
            build(BODY, CJK_NAME)
        message = str(excinfo.value)
        assert "characters lost" in message, (build.__name__, message)
        assert "王 (U+738B)" in message and "伟 (U+4F1F)" in message, message

    # --- a missing fallback file is skipped, never an error: it neither
    # breaks the render nor stops a later candidate from being used ---
    missing = "/nonexistent/j7/font.ttf"
    assert not pathlib.Path(missing).exists()
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", [missing, fallback])
    extracted = pdf_service.extract_text(
        pdf_service.verified_cover_letter_pdf(BODY, CJK_NAME)
    )
    assert "王" in extracted and "伟" in extracted, extracted
    # And with nothing but missing candidates the render still happens
    # (the primary alone draws the Latin document it covers).
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", [missing])
    latin_only = pdf_service.verified_cover_letter_pdf(BODY, "Test Candidate")
    assert "rôle" in pdf_service.normalize_ws(pdf_service.extract_text(latin_only))

    # --- a candidate without TrueType (glyf) outlines is skipped ---
    font_dir = pathlib.Path(fallback).parent
    cff = _build_font(
        font_dir / "cff-fallback.otf", FALLBACK_CHARSET, "J7Cff", cff=True
    )
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", [cff])
    with caplog.at_level(logging.WARNING, logger="app.services.pdf_service"):
        pdf = pdf_service._UnicodePDF()
    assert pdf.unicode_ok is True  # the primary still loaded
    assert any(cff in record.getMessage() for record in caplog.records), (
        "a fallback without TrueType (glyf) outlines must be skipped "
        "WITH a warning"
    )
    # Behaviourally: it contributed nothing, so the name is still lost —
    # exactly as with no fallback at all.
    with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
        pdf_service.verified_cover_letter_pdf(BODY, CJK_NAME)
    assert "characters lost" in str(excinfo.value)

    # --- a .ttc collection is loaded as its FIRST font: face 0 carries
    # the CJK names, face 1 does not, and the render draws them ---
    face0 = _font_object(font_dir / "face0.ttf", FALLBACK_CHARSET, "J7Face0")
    face1 = _font_object(font_dir / "face1.ttf", LATIN_CHARSET, "J7Face1")
    ttc = _build_collection(font_dir / "pair.ttc", [face0, face1])
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", [ttc])
    extracted = pdf_service.extract_text(
        pdf_service.verified_cover_letter_pdf(BODY, CJK_NAME)
    )
    assert "王" in extracted and "伟" in extracted, extracted

    # --- when the primary resolves to NOTHING the render is exactly the
    # old latin-1 core-font path: no fallback is consulted even though a
    # perfectly good one is installed, and the name is substituted with
    # '?' glyph by glyph, as before J-7 ---
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
    monkeypatch.setattr(pdf_service, "FALLBACK_FONT_CANDIDATES", [fallback])
    pdf = pdf_service._UnicodePDF()
    assert pdf.unicode_ok is False
    extracted = pdf_service.extract_text(pdf_service.cover_letter_pdf(BODY, CJK_NAME))
    assert "王" not in extracted and "伟" not in extracted, extracted
    assert "?" in extracted, extracted  # the latin-1 'replace' fallback


def test_ac_2():
    """AC-2: both production images install fonts-wqy-microhei beside
    fonts-dejavu-core, in the same layer as the apt-lists cleanup, and
    that package's font file is in the fallback candidate list the
    renderer reads."""
    for dockerfile in ("Dockerfile", "Dockerfile.hunt"):
        text = (BACKEND_DIR / dockerfile).read_text()
        layer = _apt_font_layer(text, dockerfile)
        assert "fonts-wqy-microhei" in layer, (dockerfile, layer)
        assert "fonts-dejavu-core" in layer, (dockerfile, layer)
        assert "rm -rf /var/lib/apt/lists/*" in layer, (
            dockerfile, "the font package must share the apt layer that "
            "cleans the lists, or the slim image stops being slim"
        )
    # ...and the code actually finds the file that package installs:
    # WenQuanYi Micro Hei is a TrueType collection at this path.
    assert "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc" in (
        pdf_service.FALLBACK_FONT_CANDIDATES
    )


def _apt_font_layer(text, dockerfile):
    """The RUN layer that installs the font packages (Dockerfile lines
    continue while indented)."""
    blocks, current = [], []
    for line in text.splitlines():
        if line.startswith("RUN "):
            current = [line]
        elif current and (line.startswith(" ") or line.startswith("\t")):
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    installing = [b for b in blocks if "apt-get install" in b]
    assert installing, f"{dockerfile} installs no apt packages?"
    assert len(installing) == 1, (dockerfile, installing)
    return installing[0]


def test_ac_3(built_fonts, monkeypatch):
    """AC-3: the name group's advice is exactly the operator sentence —
    a character lost from the applicant's name can only be a font gap,
    since a typo is not lost — and everything else in J-5's message
    survives: the group labels and order, the six-per-group listing with
    its own overflow count, the draft group's advice, and the
    send/download outcome shape."""
    primary, fallback = built_fonts

    # The whole message, verbatim, on both surfaces — the name group's
    # advice is the ONLY thing that changed from J-5. The lost character
    # comes from the fontless core-font path, the one render that still
    # loses a name character today.
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
    healthy = pdf_service.cover_letter_pdf(ASCII_BODY, "Test Candidate")
    expected = {
        "send": (
            "characters lost — cover letter: its text layer lost these "
            "characters during rendering: from your profile name: Ł (U+0141). "
            "the application was NOT sent. " + NEW_NAME_ADVICE
        ),
        "download": (
            "characters lost — cover letter: its text layer lost these "
            "characters during rendering: from your profile name: Ł (U+0141). "
            "the download was stopped. " + NEW_NAME_ADVICE
        ),
    }
    for context in ("send", "download"):
        with pytest.raises(pdf_service.PdfTextLayerError) as excinfo:
            pdf_service.verify_text_layer(
                healthy,
                context=context,
                label="cover letter",
                source_text=ASCII_BODY,
                applicant_name=NAME_LOST,
            )
        assert str(excinfo.value) == expected[context], (context, excinfo.value)

    # Both groups: name group first, and each group keeps its own advice
    # — the draft's sentence is untouched.
    with pytest.raises(pdf_service.PdfTextLayerError) as both:
        pdf_service.verify_text_layer(
            healthy,
            context="send",
            label="cover letter",
            source_text=ASCII_BODY + "\n— å",
            applicant_name=NAME_LOST,
        )
    message = str(both.value)
    assert message.endswith(
        FROM_NAME + "Ł (U+0141)" + FROM_DRAFT + "å (U+00E5), — (U+2014). "
        + NOT_SENT + " " + NEW_NAME_ADVICE + " " + DRAFT_ADVICE
    ), message
    assert message.index(FROM_NAME) < message.index("from the draft:"), message

    # The old advice is gone, and the shape J-5 kept is still kept: one
    # class marker, the document named, the surface's outcome, never a
    # 'try again' (a retry cannot restore a glyph no font has).
    assert OLD_NAME_ADVICE not in message, message
    assert message.startswith("characters lost"), message
    assert sum(message.count(marker) for marker in MARKERS) == 1, message
    assert "cover letter" in message, message
    assert NOT_SENT in message, message
    assert "try again" not in message, message

    # J-3's single-list form (no name group involved) is untouched.
    with pytest.raises(pdf_service.PdfTextLayerError) as draft_only:
        pdf_service.verify_text_layer(
            healthy,
            context="send",
            label="cover letter",
            source_text=ASCII_BODY + "\n— å",
        )
    message = str(draft_only.value)
    assert NEW_NAME_ADVICE not in message, message
    assert "profile name" not in message, message
    assert message.endswith(NOT_SENT + " " + DRAFT_ADVICE), message

    # Six per group, each group counting its own overflow — a name with
    # eight characters no font can draw.
    eight = "Ľuboš Čekor Ňora Ďula Ťava Őrs Žeder"
    with pytest.raises(pdf_service.PdfTextLayerError) as overflowing:
        pdf_service.verify_text_layer(
            healthy,
            context="send",
            label="cover letter",
            source_text=ASCII_BODY,
            applicant_name=eight,
        )
    message = str(overflowing.value)
    assert message.count(" (U+") == 6, message
    assert ", and 2 more" in message, message
    assert "from the draft:" not in message, message
    assert NEW_NAME_ADVICE in message, message

    # And the advice means a FONT GAP, not a name the user mistyped: the
    # same render draws every name character the installed fonts cover
    # and reports only the one no font has (漢 is in neither built font;
    # 王 and 伟 are in the fallback) — a typo would simply be drawn.
    _use_fonts(monkeypatch, primary, [fallback])
    pdf_service.verified_cover_letter_pdf(BODY, CJK_NAME)  # covered: no error
    with pytest.raises(pdf_service.PdfTextLayerError) as font_gap:
        pdf_service.verified_cover_letter_pdf(BODY, CJK_NAME + "漢")
    message = str(font_gap.value)
    assert NEW_NAME_ADVICE in message, message
    assert "from the draft:" not in message, message  # the draft is clean
    assert "漢 (U+6F22)" in message, message
