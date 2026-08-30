"""
Cross-board dedupe keys — the same job posted on two boards arrives with
different IDs and URLs, so we normalize title + company (or location) into
a collision-resistant key. Stored on job_postings.dedupe_key and checked
at store time and match time.
"""

import hashlib
import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Swedish/English agency markers: a copy whose COMPANY names recruiting is
# the agency re-post; prefer the direct employer's copy when collapsing.
_AGENCY_MARKERS = ("rekryter", "konsult", "staffing", "recruit", "bemanning")
_SENIORITY_TOKENS = {"senior", "junior", "lead", "sr", "jr", "chef", "head"}
# Employer-suffix pattern: "Integration Developer till Pågen" — the client
# is named in the title (agency re-posts do this constantly on Platsbanken).
_TILL_SUFFIX = re.compile(r"\s+(?:till|at|for)\s+.+$", re.IGNORECASE)
# DEDUPE-FP: title tokens that annotate LOGISTICS, not the role. Titles
# that differ ONLY by these are the same job ('Python Developer' vs
# 'Python Developer (Remote)'); ANY other differing token is role
# identity and blocks the collapse. Deliberately tiny — every word here
# is a word we accept as meaningless for job identity, which is why e.g.
# 'office' and 'contract' are NOT listed ('Office Manager' is not
# 'Manager', 'Contract Manager' is not 'Manager').
_TITLE_NOISE_TOKENS = frozenset(
    {
        # work-mode suffixes boards append to titles
        "remote", "hybrid", "onsite", "distans", "heltid", "deltid",
        # grammatical filler
        "the", "a", "an", "and", "of", "to", "for", "with", "at", "och",
    }
) | _SENIORITY_TOKENS  # seniority markers — also gated independently below


def _norm(value: str | None) -> str:
    return _NON_ALNUM.sub("", (value or "").lower())


def _tokens(value: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9åäö]+", (value or "").lower()) if t}


def dedupe_key_for(title: str | None, company: str | None, location: str | None = None) -> str:
    t = _norm(title)
    c = _norm(company) or _norm(location)
    return hashlib.md5(f"{t}|{c}".encode()).hexdigest()[:16]


def likely_same_job(
    *, title_a: str | None, company_a: str | None, location_a: str | None,
    title_b: str | None, company_b: str | None, location_b: str | None,
) -> bool:
    """Fuzzy second gate for the exact key's blind spot: the same job
    posted DIRECTLY by the employer and AGAIN by an agency — every exact
    component differs ('Integration Developer till Pågen' via Cabeza vs
    'Integration Developer' at PÅGEN AKTIEBOLAG).

    Rule (precision over recall — a wrongly collapsed job is invisible
    forever, a missed duplicate wastes one queue slot):
      1. same municipality (first location segment), AND
      2. titles differ ONLY by NOISE tokens (_TITLE_NOISE_TOKENS:
         work-mode suffixes, seniority markers, filler words) after
         stripping the employer 'till/at/for X' suffix, AND the
         noise-stripped cores are near-identical (Jaccard >= 0.75).
         DEDUPE-FP: the old >= 0.6 raw overlap was EXACTLY the Jaccard
         of a 4-token title differing by one word (3/5), so it silently
         collapsed live pairs like 'Senior Data Engineer (Python)' into
         'Senior Data Scientist (Python)' — Engineer vs Scientist is
         role identity, not noise; AND
      3. EMPLOYER LINK — companies equal, OR one company is named in the
         other posting's title (the agency pattern), AND
      4. no seniority divergence (Senior vs plain = different roles).
    """
    def muni(loc: str | None) -> str:
        return (loc or "").split(",")[0].strip().lower()

    if muni(location_a) != muni(location_b) or not muni(location_a):
        return False

    ta, tb = _tokens(_TILL_SUFFIX.sub("", title_a or "")), _tokens(_TILL_SUFFIX.sub("", title_b or ""))
    if not ta or not tb:
        return False

    # ROLE-IDENTITY GUARD: any differing token outside the noise list is
    # the role itself ('engineer' vs 'scientist', 'flask' vs 'fastapi',
    # 'assoc' vs nothing) — never the same job, whatever the ratio.
    differing = (ta | tb) - (ta & tb)
    if any(t not in _TITLE_NOISE_TOKENS for t in differing):
        return False

    # Near-identity threshold on the noise-stripped cores. Belt to the
    # guard above: the guard alone decides today (passing it implies the
    # cores are equal), but the 0.75 ratio keeps the collapse decision
    # inside the near-identity envelope as an explicit, testable
    # invariant — if the guard is ever widened (noise-list growth,
    # stemming), one-word-off shapes (3/5, 2/4) still refuse.
    core_a, core_b = ta - _TITLE_NOISE_TOKENS, tb - _TITLE_NOISE_TOKENS
    if not core_a or not core_b:
        return False
    union = core_a | core_b
    overlap = len(core_a & core_b) / len(union)
    if overlap < 0.75:
        return False

    # Seniority divergence: {senior, junior, lead...} present on one side
    # only = genuinely different roles, never collapse.
    sa, sb = _tokens(title_a) & _SENIORITY_TOKENS, _tokens(title_b) & _SENIORITY_TOKENS
    if sa != sb:
        return False

    # Employer link
    ca, cb = _norm(company_a), _norm(company_b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True

    def company_tokens(company: str | None) -> set[str]:
        # legal suffixes don't distinguish employers
        drop = {"ab", "aktiebolag", "hb", "inc", "ltd", "llc", "gmbh",
                "co", "company", "publ"}
        return {t for t in _tokens(company) if t not in drop}

    cta, ctb = company_tokens(company_a), company_tokens(company_b)
    if cta and ctb and (cta <= ctb or ctb <= cta):
        return True  # 'Axis Communications' == 'Axis Communications AB'
    # one company named in the OTHER posting's title (agency pattern:
    # 'till Pågen' names the client PÅGEN AKTIEBOLAG)
    def company_in(company: str | None, title: str | None) -> bool:
        ctoks = company_tokens(company)
        ttoks = _tokens(title)
        return any(len(t) >= 4 and t in ttoks for t in ctoks)

    return company_in(company_a, title_b) or company_in(company_b, title_a)
