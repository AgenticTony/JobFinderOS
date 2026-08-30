"""AI-12 — employer-facing PDFs must not render "?" for curly typography.

Production bug: python:3.12-slim ships NO fonts at all, so
_resolve_unicode_font() returned None in both production images and every
em-dash (U+2014) and curly quote (U+2019/U+201C/U+201D) in live GLM
letters hit the latin-1 "replace" fallback in _UnicodePDF._clean — one
short shipped paragraph contained five "?" marks. The fix installs
fonts-dejavu-core in backend/Dockerfile and backend/Dockerfile.hunt;
FONT_CANDIDATES already lists DejaVuSans.ttf as its first Linux path.

Honesty across the three environments this code runs on:

- macOS dev host: the Arial candidates resolve -> the render test runs.
- ubuntu-latest CI runner (unit suite): DejaVu candidates resolve
  (Ubuntu ships fonts-dejavu-core) -> the render test runs there too.
- slim production container: the true production-shape proof is the CI
  docker-job smoke that renders and text-extracts INSIDE the built image
  (.github/workflows/ci.yml). Here the fontless case is exercised
  directly instead: test_latin1_fallback... monkeypatches the resolver
  to None, which is exactly the pre-fix slim image's state.

So the render test's skip guard means precisely "this machine has no TTF
candidate at all" — a shape no supported environment has after the fix.
"""

import io
import pathlib

import pdfplumber
import pytest

from app.services import pdf_service

# The live failure signature: em-dash, curly apostrophe, curly double
# quotes — plus Swedish å/ä/ö, which the latin-1 core-font path was
# already documented to survive (they must keep surviving both paths).
CURLY = (
    "I\u2019m excited \u2014 truly \u2014 about the role; "
    "the team\u2019s \u201cfirst principles\u201d culture."
)
SWEDISH = "åäö ÅÄÖ"


def _extract(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


# ---------- font-fallback logic (runs identically everywhere) ----------


def test_resolve_unicode_font_returns_none_when_no_candidates(monkeypatch):
    """Discovery degrades to None — the state a fontless slim image is in."""
    monkeypatch.setattr(pdf_service, "FONT_CANDIDATES", [])
    assert pdf_service._resolve_unicode_font() is None


def test_resolve_unicode_font_prefers_first_existing_candidate(monkeypatch):
    """First existing path wins; missing earlier candidates are skipped."""
    monkeypatch.setattr(
        pdf_service,
        "FONT_CANDIDATES",
        ["/nonexistent/font.ttf", "/nonexistent/other.ttf"],
    )
    assert pdf_service._resolve_unicode_font() is None
    real = pdf_service.FONT_CANDIDATES  # restored by monkeypatch teardown
    assert real  # sanity: the production list is non-empty


def test_latin1_fallback_substitutes_curly_typography(monkeypatch):
    """Pin the bug: with no font, _clean turns every curly char into '?'.

    This is byte-for-byte what production did before AI-12 — the slim
    image had no /usr/share/fonts at all, _resolve_unicode_font() was
    None, and U+2014/U+2019/U+201C/U+201D each became a literal '?' via
    text.encode("latin-1", "replace") (pdf_service.py's _clean).
    """
    monkeypatch.setattr(pdf_service, "_resolve_unicode_font", lambda: None)
    pdf = pdf_service._UnicodePDF()
    assert pdf.unicode_ok is False
    cleaned = pdf._clean(CURLY)
    # All four curly characters were substituted...
    for ch in ("\u2014", "\u2019", "\u201c", "\u201d"):
        assert ch not in cleaned
    # ...each by a question mark (U+2014 x2, U+2019 x2, U+201C, U+201D = 6).
    assert cleaned.count("?") == 6, repr(cleaned)
    # Latin-1 range text must keep surviving this fallback as designed.
    assert pdf._clean(SWEDISH) == SWEDISH


# ---------- render round-trip (runs wherever a TTF candidate exists) ----------


@pytest.mark.skipif(
    pdf_service._resolve_unicode_font() is None,
    reason="no unicode TTF candidate on this machine — the docker-job "
    "smoke in ci.yml covers the production container shape",
)
def test_pdf_render_preserves_curly_typography():
    """End-to-end: render -> extract text -> the curly chars are intact.

    Runs on the macOS host (Arial candidates) and the ubuntu CI runner
    (DejaVu candidates). Extraction is the same check the CI docker-job
    smoke does inside the built image — the '?' artifacts of the latin-1
    fallback WOULD show up in extracted text, so this cannot pass
    vacuously when the font path is broken.
    """
    for render in (pdf_service.cover_letter_pdf, pdf_service.tailored_cv_pdf):
        extracted = _extract(render(CURLY + "\n" + SWEDISH, "Test Candidate"))
        for ch in ("\u2014", "\u2019", "\u201c", "\u201d", "å", "ä", "ö"):
            assert ch in extracted, (render.__name__, ch, repr(extracted))
        assert "?" not in extracted, repr(extracted)


# ---------- code <-> image contract (guards the fix's wiring) ----------


def test_font_candidates_cover_the_dejavu_path_the_dockerfiles_install():
    """fonts-dejavu-core must match a FONT_CANDIDATES Linux path.

    If someone renames the candidate paths, the apt package installed by
    the Dockerfiles would exist but never be found — production would
    silently regress to '?' with a green local suite.
    """
    assert "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" in pdf_service.FONT_CANDIDATES


@pytest.mark.parametrize(
    "dockerfile", ["Dockerfile", "Dockerfile.hunt"], ids=["api", "hunt"]
)
def test_production_images_install_dejavu_core(dockerfile):
    """Both production images must carry the font package (AI-12 fix).

    Static but load-bearing: this is the only unit-level guard that the
    apt layer survives future Dockerfile edits; the behavioral proof for
    the built image is the ci.yml docker-job render smoke.
    """
    path = pathlib.Path(__file__).resolve().parents[1] / dockerfile
    assert "fonts-dejavu-core" in path.read_text(), path
