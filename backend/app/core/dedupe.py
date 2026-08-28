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
      2. title token overlap >= 0.6 after stripping the employer
         'till/at/for X' suffix, AND
      3. EMPLOYER LINK — companies equal, OR one company is named in the
         other posting's title (the agency pattern), AND
      4. no seniority divergence (Senior vs plain = different roles).
    """
    def muni(loc: str | None) -> str:
        return (loc or "").split(",")[0].strip().lower()

    if muni(location_a) != muni(location_b) or not muni(location_a):
        return False

    def core_title(t: str | None) -> set[str]:
        stripped = _TILL_SUFFIX.sub("", t or "")
        toks = _tokens(stripped) - _SENIORITY_TOKENS
        return toks - {"developer", "utvecklare"} if not toks else toks

    ta, tb = _tokens(_TILL_SUFFIX.sub("", title_a or "")), _tokens(_TILL_SUFFIX.sub("", title_b or ""))
    if not ta or not tb:
        return False
    union = ta | tb
    overlap = len(ta & tb) / len(union) if union else 0.0
    if overlap < 0.6:
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
