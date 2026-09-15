r"""WO-19 part B: the deterministic work-rights eligibility lexicon.

Verdicts over posting text + the user's work-rights answer, BEFORE any
AI spend. Adapted from the source repo's Eligibility Gate (v1.2.6):

- citizenship / PR / security-clearance requirement AND the user needs
  sponsorship → INELIGIBLE (hard stop: never scored, never shown).
  Re-evaluated each run at zero cost, like the PIPE-16 scope gate —
  the user's answer can change.
- sponsorship-welcoming wording ("we sponsor", "international
  applicants welcome") → VERIFIED, note recorded.
- high-risk sector (government/defence, banking, telcos, professional
  services, critical infrastructure) + silent text → UNVERIFIED + note
  ("silence is not permission" — shown, flagged, never dropped).
- otherwise silent → UNVERIFIED without a note (stored for stats; the
  card renders no chip — silent-plain on every card is noise).
- prefer_not_say / unknown → UNVERIFIED everywhere: not having answered
  must never read as verification.

Deliberately country-agnostic v1: SE and GB phrasings both match. A
Swedish citizenship phrase in a GB posting is rare and flagging it is
correct anyway. Coverage is intentionally partial — an unmatched
foreign requirement is simply not caught yet, same failure mode as the
location lexicon, never worse.

Pure functions, no DB, no AI — testable in isolation (test_units.py).
"""

import re
from typing import Optional, Tuple

WORK_RIGHTS_VALUES = (
    "citizen_or_pr",
    "permanent_resident",
    "eu_right",
    "needs_sponsorship",
    "prefer_not_say",
)

#: Human phrasing per value — the line the tailor prompt, the guard
#: source, and the UI labels all share (one rendering, three consumers;
#: the guard source must state what the generator sees).
WORK_RIGHTS_LINES = {
    "citizen_or_pr": "citizen or permanent resident of the work country — "
                     "works without sponsorship",
    "permanent_resident": "permanent resident — works without sponsorship",
    "eu_right": "EU/EEA work right — works in the EU without sponsorship",
    "needs_sponsorship": "needs visa sponsorship to work",
    "prefer_not_say": "work rights not stated",
}

# Hard requirements: wording that makes a role categorically
# unavailable to someone needing sponsorship. Word-boundary lookarounds
# per the country-lexicon lesson (terms ending in non-word chars).
_CITIZENSHIP_RES = [
    re.compile(p, re.I) for p in (
        r"(?<!\w)citizenship(?!\w)",
        r"(?<!\w)medborgarskap(?!\w)",
        r"(?<!\w)security\s+clearance(?!\w)",
        r"(?<!\w)säkerhetsprövning(?!\w)",
        r"(?<!\w)sakerhetsprovning(?!\w)",
        r"(?<!\w)cleared(?!\w)",
        r"(?<!\w)[^.\n]{0,40}right\s+to\s+work[^.\n]{0,40}required",
        r"(?<!\w)must\s+(?:be\s+)?(?:a\s+)?(?:citizen| british)",
        r"(?<!\w)kräver\s+(?:svenskt\s+)?medborgarskap",
        r"(?<!\w)eligible\s+for\s+\w*\s*clearance",
    )
]

_SPONSORSHIP_WELCOME_RES = [
    re.compile(p, re.I) for p in (
        r"(?<!\w)we\s+sponsor(?!\w)",
        r"(?<!\w)visa\s+sponsorship\s+(?:available|offered|provided)",
        r"(?<!\w)international\s+applicants\s+welcome",
        r"(?<!\w)relocation(?:\s+support|\s+package)?\s+(?:offered|available)",
        r"(?<!\w)vi\s+(?:erbjuder\s+)?arbetstillstånd",
    )
]

# The source framework's named high-risk sectors: postings here carry a
# raised prior of citizenship/clearance requirements, so silence flags.
_HIGH_RISK_RES = [
    re.compile(p, re.I) for p in (
        r"(?<!\w)(?:ministry|government\s+agency|public\s+sector|myndighet)"
        r"(?!\w)",
        r"(?<!\w)(?:defence|defense|military|armed\s+forces|totalförsvaret)"
        r"(?!\w)",
        r"(?<!\w)(?:försvar|krigsmakt)(?!\w)",
        r"(?<!\w)(?:bank|banking|nordic\s+bank)(?!\w)",
        r"(?<!\w)(?:telecom|telco|telekom)(?!\w)",
        r"(?<!\w)(?:critical\s+infrastructure|kritical\s+infrastruktur)"
        r"(?!\w)",
    )
]


def _matches(patterns, text: str) -> Optional[re.Match]:
    for pattern in patterns:
        m = pattern.search(text or "")
        if m:
            return m
    return None


def evaluate_eligibility(text: str, work_rights: Optional[str]) -> Tuple[str, Optional[str]]:
    """(verdict, note) for one posting against one user's work rights.

    verdict: 'ineligible' (hard stop), 'verified', or 'unverified'.
    Fails closed: prefer_not_say and unknown values never verify.
    """
    work_rights = (work_rights or "").strip() or "prefer_not_say"
    text = text or ""

    if work_rights == "needs_sponsorship":
        m = _matches(_CITIZENSHIP_RES, text)
        if m:
            return (
                "ineligible",
                f"Posting appears to require citizenship/clearance "
                f"(“{m.group(0).strip()[:60]}”) — unavailable without "
                f"sponsorship.",
            )

    # prefer_not_say never counts as verification — even welcoming
    # wording only verifies a stated answer.
    if work_rights in ("prefer_not_say",):
        return ("unverified", None)

    m = _matches(_SPONSORSHIP_WELCOME_RES, text)
    if m:
        return (
            "verified",
            f"Posting welcomes international applicants "
            f"(“{m.group(0).strip()[:60]}”).",
        )

    m = _matches(_HIGH_RISK_RES, text)
    if m:
        return (
            "unverified",
            f"High-risk sector for work-rights requirements "
            f"(“{m.group(0).strip()[:60]}”) and the posting is silent — "
            f"check before applying.",
        )

    return ("unverified", None)
