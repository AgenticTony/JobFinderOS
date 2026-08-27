"""Best-effort location → country resolution for the location gate.

WO-06 / D1: the gate had no country dimension, so remote jobs located in
the USA passed Swedish include_remote users — a pool of USA 73 vs Malmö
11 for a Malmö user. A job whose location resolves to a foreign country
is not takeable (US remote jobs need US work authorization), regardless
of the remote flag.

Design constraints, deliberately:
- WORD-BOUNDARY matching only — substring hits would turn "us" (an
  English word) and city-name fragments into false blocks.
- Unresolvable locations ("Remote", "Anywhere", empty, unknown city)
  resolve to None and keep the existing remote-opt-in behaviour — this
  lexicon can only ever BLOCK, never admit.
- Coverage is intentionally partial: the markets served (SE, GB) plus
  the countries that dominate the shared remote feeds (US, DE, NL, FR,
  CA, IN, ...). An unmapped foreign location simply isn't blocked yet —
  same failure mode as today, never worse.
"""

import re
from typing import Optional

# term (lowercase, word-boundary matched) -> ISO country code
_TERMS = {
    # Nordics / served markets
    "sweden": "SE", "sverige": "SE", "stockholm": "SE", "gothenburg": "SE",
    "göteborg": "SE", "malmö": "SE", "lund": "SE", "uppsala": "SE",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "london": "GB", "manchester": "GB", "birmingham": "GB",
    "leeds": "GB", "glasgow": "GB", "edinburgh": "GB", "bristol": "GB",
    "brighton": "GB", "sheffield": "GB",
    # Countries that dominate the shared remote feeds
    "usa": "US", "united states": "US", "u.s.": "US", "u.s.a.": "US",
    "america": "US", "new york": "US", "san francisco": "US", "boston": "US",
    "austin": "US", "seattle": "US", "chicago": "US", "denver": "US",
    "washington dc": "US", "los angeles": "US", "portland": "US",
    "germany": "DE", "deutschland": "DE", "berlin": "DE", "munich": "DE",
    "münchen": "DE", "hamburg": "DE",
    "netherlands": "NL", "amsterdam": "NL", "rotterdam": "NL",
    "france": "FR", "paris": "FR",
    "canada": "CA", "toronto": "CA", "vancouver": "CA", "montreal": "CA",
    "india": "IN", "bangalore": "IN", "mumbai": "IN",
    "poland": "PL", "warsaw": "PL", "krakow": "PL", "kraków": "PL",
    "spain": "ES", "madrid": "ES", "barcelona": "ES",
    "italy": "IT", "milan": "IT", "rome": "IT",
    "singapore": "SG", "dubai": "AE", "tokyo": "JP", "sydney": "AU",
    "australia": "AU",
}

_COMPILED = [
    (re.compile(rf"\b{re.escape(term)}\b"), country)
    for term, country in _TERMS.items()
]


# Multi-region qualifiers: "North/South/Latin America" names a hemisphere,
# not the US — a listing spanning "Europe, North America" is still takeable
# for a European. Blank these spans before matching the bare 'america' term.
_MULTI_REGION = re.compile(r"\b(north|south|latin|central)\s+america\b")


def location_country(location: Optional[str]) -> Optional[str]:
    """Resolve a free-text location to an ISO country code, or None.

    None means 'unresolvable / global' — the caller keeps its existing
    behaviour for those. Longest-match wins so 'united kingdom' beats
    any single-word coincidence.
    """
    if not location:
        return None
    hay = _MULTI_REGION.sub(" ", location.lower())
    best: Optional[str] = None
    best_len = -1
    for pattern, country in _COMPILED:
        if pattern.search(hay) and len(pattern.pattern) > best_len:
            best, best_len = country, len(pattern.pattern)
    return best
