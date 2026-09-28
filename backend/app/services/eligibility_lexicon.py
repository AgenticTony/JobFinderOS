r"""WO-19 part B: the deterministic work-rights eligibility lexicon.

Verdicts over posting text + the user's work-rights answer, BEFORE any
AI spend. Adapted from the source repo's Eligibility Gate (v1.2.6),
hardened by the round-1 review (2026-09-15):

- Round-1 rule: a job is hidden ONLY on an explicit, affirmative
  requirement ("must hold", "…required", "kräver") of citizenship /
  PR / security clearance, in a sentence with NO negation, AND only
  when the user's rights for THAT JOB'S COUNTRIES are not established
  (per-jurisdiction: work rights are answered for the user's onboarded
  country; eu_right covers the EEA bloc; post-Brexit GB is outside it).
  Everything short of that is flagged, never dropped — the WO's own
  "ambiguous cases FLAG" rule. Bare keywords ("citizenship", "cleared")
  match nothing: "corporate citizenship" and "we cleared a backlog"
  are not requirements.
- Welcome wording (sponsorship ONLY — "we sponsor", "visa sponsorship
  available", "international applicants welcome") verifies, and only
  for a user who needs sponsorship. Relocation packages are NOT visa
  sponsorship — they routinely assume an existing right to work.
- High-risk sector (framed: "a leading bank", "banking sector",
  defence, government agency…) + silent text flags unverified with a
  note, only for sponsorship seekers. "bank holidays" is UK benefits
  boilerplate, not a sector.
- prefer_not_say / unknown / unresolvable job country → UNVERIFIED,
  never verification, never a drop.

Pure functions, no DB, no AI — testable in isolation (test_units.py).
"""

import re
from typing import Optional, Set, Tuple

WORK_RIGHTS_VALUES = (
    "citizen_or_pr",
    "permanent_resident",
    "eu_right",
    "needs_sponsorship",
    "prefer_not_say",
)

# The EEA free-movement bloc (mirrors country_lexicon._EEA — import
# avoided to keep this module dependency-free for prompt-side use).
_EEA = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
    "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

_COUNTRY_NAMES = {"SE": "Sweden", "GB": "the United Kingdom"}

# Affirmative REQUIREMENT framing — the only shapes that may hide a
# job. Anchored phrases, not keywords.
_REQUIREMENT_RES = [
    re.compile(p, re.I) for p in (
        r"must\s+(?:hold|have|possess)[^.]{0,50}citizenship",
        # (?![\w-]) — round-2 finding 2: "must be Swedish-speaking" and
        # "must be British-based" are language/location requirements,
        # not citizenship; the hyphen boundary is the difference (bare
        # "must be British." stays a hard stop).
        r"must\s+be\s+(?:a\s+)?(?:british|swedish|danish|norwegian"
        r"|finnish|citizen)(?![\w-])",
        r"citizenship\s+(?:of\s+[a-z\s]+?\s+)?(?:is\s+)?required",
        r"citizenship\s+required",
        r"security\s+clearance[^.]{0,40}(?:is\s+)?required",
        r"(?:must\s+(?:hold|have)|required|eligible\s+for|eligibility"
        r"\s+for)[^.]{0,50}security\s+clearance",
        r"(?:svenskt|danskt|norskt|finskt|brittiskt)\s+medborgarskap",
        r"kräver\s+(?:svenskt\s+)?medborgarskap",
        r"(?:krav|kräver|krävs)[^.]{0,40}säkerhetsprövning",
        r"säkerhetsprövning\s+(?:krävs|godkänd)",
        r"right\s+to\s+work[^.]{0,30}(?:is\s+)?required",
    )
]

# A sentence carrying one of these does NOT state a requirement.
# Round-2 finding 3: the Swedish idioms "är inte ett krav" / "inget krav
# på" need krav as a negatable word and ingen/inget/inga as negators.
_NEGATION_RE = re.compile(
    r"(?i)\b(?:not|no|without|inte|ej|utan|ingen|inget|inga)\b[^.\n]{0,40}"
    r"(?:required|needed|necessary|citizenship|clearance|medborgarskap"
    r"|säkerhetsprövning|krav)"
    r"|required[^.\n]{0,20}\bnot\b"
    r"|kräver\s+inte|krävs\s+inte",
)

# Sponsorship-welcoming wording — the ONLY verification path.
_SPONSORSHIP_WELCOME_RES = [
    re.compile(p, re.I) for p in (
        r"(?<!\w)we\s+sponsor(?!\w)",
        r"visa\s+sponsorship\s+(?:available|offered|provided|provided)",
        r"(?<!\w)sponsor(?:ship)?\s+(?:is\s+)?available",
        r"international\s+applicants\s+welcome",
        r"vi\s+(?:erbjuder\s+)?arbetstillstånd",
    )
]

# Sponsorship REFUSALS (round-2 finding 1): "No visa sponsorship
# available" is standard UK boilerplate, and the welcome patterns match
# inside it — a refusal clause must never verify. Per-clause, like the
# welcome check: the refusal kills its own clause only.
_SPONSORSHIP_REFUSAL_RES = [
    re.compile(p, re.I) for p in (
        r"(?<!\w)no\s+(?:visa\s+)?sponsorship",
        r"(?<!\w)(?:visa\s+)?sponsorship[^.;\n]{0,40}\bnot\s+"
        r"(?:available|offered|provided)",
        r"(?<!\w)(?:do(?:es)?\s+not|don't|doesn't|cannot|can't|won't"
        r"|will\s+not|unable\s+to)\s+(?:offer|provide|sponsor)",
        r"vi\s+erbjuder\s+inte",
        r"(?<!\w)(?:utan|ingen|inget|inga)\s+(?:arbetstillstånd|visum)",
    )
]

# High-risk sectors, FRAMED — "a leading bank", "banking sector". Bare
# "bank" is UK benefits boilerplate ("plus bank holidays").
_HIGH_RISK_RES = [
    re.compile(p, re.I) for p in (
        r"(?<!\w)(?:a|the|an)\s+(?:leading\s+|major\s+|top\s+)?"
        r"(?:investment\s+|retail\s+|commercial\s+|private\s+)?bank(?!\s+holiday)",
        r"(?<!\w)banking\s+(?:sector|industry|background)(?!\w)",
        r"(?<!\w)(?:ministry|government\s+agency|public\s+sector|myndighet)"
        r"(?!\w)",
        r"(?<!\w)(?:defence|defense|military|armed\s+forces|totalförsvaret)"
        r"(?!\w)",
        r"(?<!\w)(?:försvar|krigsmakt)(?!\w)",
        r"(?<!\w)(?:telecom|telco|telekom)(?!\w)",
        r"(?<!\w)critical\s+infrastructure(?!\w)",
    )
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def effective_rights(work_rights: Optional[str], home_country: Optional[str],
                     job_countries: Set[str]) -> str:
    """Is the user's right to hold THIS job established?

    'established' | 'not_established' | 'unknown'. Work rights are
    answered for the user's ONBOARDED country (round-1 finding 2: one
    profile-wide claim must never license a tailor statement or a gate
    pass for another jurisdiction — post-Brexit GB is outside the EEA).
    Unresolvable job countries (remote/global) are 'unknown' — flag,
    never drop.
    """
    work_rights = (work_rights or "").strip() or "prefer_not_say"
    if work_rights == "prefer_not_say":
        return "unknown"
    if not job_countries:
        return "unknown"
    home = (home_country or "").upper()
    if work_rights in ("citizen_or_pr", "permanent_resident"):
        return "established" if home in job_countries else "not_established"
    if work_rights == "eu_right":
        return ("established" if all(c in _EEA for c in job_countries)
                else "not_established")
    return "not_established"  # needs_sponsorship


def work_rights_line(work_rights: Optional[str],
                     home_country: Optional[str]) -> str:
    """The scoped prompt line for the tailor AND the guard source.

    Scoped to the answered country with an explicit never-elsewhere
    instruction: a UK letter must not claim a right the user's Swedish
    answer doesn't give (round-1 finding 2 — the guard sees this same
    line, so an out-of-scope claim is unsupported by the source).
    """
    work_rights = (work_rights or "").strip() or "prefer_not_say"
    home = (home_country or "").upper()
    where = _COUNTRY_NAMES.get(home, home or "your country")
    if work_rights == "prefer_not_say":
        return ("Work rights: not stated. Never assert citizenship, work "
                "rights, or visa status for any country.")
    bodies = {
        "citizen_or_pr": f"citizen or permanent resident of {where} — can "
                         f"work in {where} without sponsorship",
        "permanent_resident": f"permanent resident of {where} — can work "
                              f"in {where} without sponsorship",
        "eu_right": "EU/EEA work right — can work in any EU/EEA country "
                    "without sponsorship",
        "needs_sponsorship": f"needs visa sponsorship to work (including "
                             f"in {where})",
    }
    body = bodies.get(work_rights, "not stated")
    return (
        f"Work rights (answered for {where}): {body}. "
        "This answer covers ONLY the countries stated — never claim work "
        "rights, citizenship or visa status for any other country."
    )


def evaluate_eligibility(text: str, work_rights: Optional[str],
                         home_country: Optional[str] = None,
                         job_countries: Optional[Set[str]] = None
                         ) -> Tuple[str, Optional[str]]:
    """(verdict, note) for one posting against one user's work rights.

    verdict: 'ineligible' (hard stop — explicit affirmative requirement
    + rights not established for the job's countries), 'verified'
    (sponsorship-welcoming wording, sponsorship seeker), or
    'unverified'. Fails closed: prefer_not_say and unknown values never
    verify; unresolvable countries never drop.
    """
    work_rights = (work_rights or "").strip() or "prefer_not_say"
    text = text or ""
    job_countries = job_countries or set()

    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]

    def _first(patterns):
        for sentence in sentences:
            if _NEGATION_RE.search(sentence):
                continue
            for pattern in patterns:
                m = pattern.search(sentence)
                if m:
                    return m, sentence
        return None, None

    def _first_welcome(patterns):
        # Welcome wording is checked per CLAUSE: "Citizenship is not
        # required - we sponsor visas" must verify — the negation kills
        # the requirement reading, not the welcome clause beside it.
        # A sponsorship REFUSAL kills its own clause the same way
        # (round-2 finding 1: "no visa sponsorship available" is a
        # refusal, not a welcome).
        for sentence in sentences:
            for clause in re.split(r"[;–—]| - ", sentence):
                if _NEGATION_RE.search(clause):
                    continue
                if any(r.search(clause) for r in _SPONSORSHIP_REFUSAL_RES):
                    continue
                for pattern in patterns:
                    m = pattern.search(clause)
                    if m:
                        return m, clause
        return None, None

    rights = effective_rights(work_rights, home_country, job_countries)

    # Hard stop: explicit affirmative requirement, rights not
    # established for THIS job. Unknown rights / unknown country flag —
    # a DETECTED requirement is never silently discarded (round-2
    # finding 4): the note is the flag the card renders.
    m, _sentence = _first(_REQUIREMENT_RES)
    if m and rights == "not_established":
        return (
            "ineligible",
            f"Posting requires citizenship/clearance "
            f"(“{m.group(0).strip()[:60]}”) — your work-rights answer "
            f"does not cover this job's country.",
        )
    if m and rights == "unknown":
        return (
            "unverified",
            f"Posting states a citizenship/clearance requirement "
            f"(“{m.group(0).strip()[:60]}”) — your work-rights answer "
            f"doesn't cover this job's country; check before applying.",
        )

    # Verification: sponsorship wording only, and only for the user who
    # needs it. (Relocation packages are NOT visa sponsorship.)
    if work_rights == "needs_sponsorship":
        # Round-3: a DETECTED refusal is the most actionable signal
        # this gate has for a sponsorship seeker — it must carry its
        # note, not fall through to a chip-less plain card (MatchCard
        # renders the chip only when a note exists; same shape as
        # round-2 finding 4 for requirements). Scanned BEFORE the
        # welcome: when an ad carries both, the refusal is the
        # safety-relevant half.
        for sentence in sentences:
            for clause in re.split(r"[;–—]| - ", sentence):
                for pattern in _SPONSORSHIP_REFUSAL_RES:
                    m = pattern.search(clause)
                    if m:
                        return (
                            "unverified",
                            f"Posting states it does not offer visa "
                            f"sponsorship "
                            f"(“{m.group(0).strip()[:60]}”).",
                        )
        m, _sentence = _first_welcome(_SPONSORSHIP_WELCOME_RES)
        if m:
            return (
                "verified",
                f"Posting welcomes international applicants "
                f"(“{m.group(0).strip()[:60]}”).",
            )

    if work_rights == "needs_sponsorship":
        m, _sentence = _first(_HIGH_RISK_RES)
        if m:
            return (
                "unverified",
                f"High-risk sector for work-rights requirements "
                f"(“{m.group(0).strip()[:60]}”) and the posting is silent — "
                f"check before applying.",
            )

    return ("unverified", None)
