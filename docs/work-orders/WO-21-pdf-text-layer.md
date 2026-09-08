# WO-21 — Outbound PDF text-layer verification

> Priority: P2 (small; half-day) · Depends on: none · Status:
> designed, not started
> Origin: 2026-09-08 review of `MadsLorentzen/ai-job-search` — its
> apply flow runs an ATS-parseability check (pypdf text extraction) on
> every final PDF before it is sent. We generate PDFs that employers
> and their ATS parsers must read, and verify nothing about them.

## Why this exists

`pdf_service.py` renders every outbound document — tailored CV,
cover letter — through a unicode-font resolution step
(`_resolve_unicode_font`, :29) with graceful degradation. That is
exactly the kind of path that can silently produce a visually-fine
PDF with a broken or empty text layer; ATS parsers and employers'
search tools read the text layer, not the glyphs. No test or runtime
check anywhere asserts extractability. The generated PDF is the
product's physical artifact — it is the one thing the employer keeps.

## Design

1. **Dependency**: `pypdf` (pure-Python, BSD — the same choice the
   source repo made; no Poppler requirement). Add to the pinned
   lockfile, not just requirements.txt.
2. **Test-time**: for every PDF fixture the suite generates
   (cover letter + tailored CV paths, both ascii and
   non-ascii-name/font scenarios): extract text with pypdf, assert a
   minimum extracted-character count AND that a known phrase from the
   source text survives extraction. Assert, don't warn.
3. **Runtime (cheap)**: after each PDF build in the send path,
   extract and check the character floor; on failure, block the send
   with an explicit error (a document the employer cannot parse is a
   failed send, not a degraded one) and log to the existing
   application `error` column.

## Acceptance criteria (red-first)

1. A deliberately broken fixture (font path forced to a
   non-embedded, glyph-only render) fails the check — prove the test
   can go red before wiring it green.
2. All existing PDF round-trip tests extended with the extraction
   assertion; non-ascii name fixture included.
3. Runtime check blocks the send path on failure with a user-facing
   error; grep-prove the send path cannot skip it.
4. `pypdf` pinned in the lockfile; CI green.
