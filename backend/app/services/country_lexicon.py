"""Best-effort location → country-set resolution for the location gate.

WO-06 / D1: the gate had no country dimension, so remote jobs located in
the USA passed Swedish include_remote users — a pool of USA 73 vs Malmö
11 for a Malmö user. A job whose location resolves ONLY to foreign
countries is not takeable (US remote jobs need US work authorization),
regardless of the remote flag.

Design constraints, deliberately:
- MEMBERSHIP, not ranking: a multi-country location ("Sweden, Germany",
  "Boston, Lincolnshire, UK") resolves to the SET of every country
  named, and the gate blocks only when that set excludes the user's
  country. The first version returned one longest-regex winner, which
  blocked jobs explicitly open in the user's country ("Sweden, Germany"
  -> DE) — the exact harm the product exists to prevent — and would have
  degraded further as the lexicon grew (review finding, 2026-08-27).
- Lookaround boundaries `(?<!\w)term(?!\w)`, not `\b..\b`: terms ending
  in a non-word char ("u.s.", "u.s.a.") can never satisfy a trailing
  `\b` at end-of-string or before a space — they were dead entries, and
  "Remote, U.S." resolved to nothing (review finding 2).
- Unresolvable locations ("Remote", "Anywhere", empty) resolve to an
  EMPTY SET and keep the existing remote-opt-in behaviour — this lexicon
  can only ever BLOCK, never admit.
- Coverage is intentionally partial: the markets served (SE, GB) plus
  the countries that dominate the shared remote feeds. An unmapped
  foreign location simply isn't blocked yet — same failure mode as
  today, never worse.
"""

import re
from typing import Set

# term (lowercase) -> ISO country code
_TERMS = {
    # Nordics / served markets
    "sweden": "SE", "sverige": "SE", "stockholm": "SE", "gothenburg": "SE",
    "göteborg": "SE", "malmö": "SE", "lund": "SE", "uppsala": "SE",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "london": "GB", "manchester": "GB", "birmingham": "GB",
    "leeds": "GB", "glasgow": "GB", "edinburgh": "GB", "bristol": "GB",
    "brighton": "GB", "sheffield": "GB", "lincolnshire": "GB",
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
    "austria": "AT", "belgium": "BE", "czechia": "CZ", "denmark": "DK",
    "copenhagen": "DK", "norway": "NO", "oslo": "NO", "finland": "FI",
    "singapore": "SG", "dubai": "AE", "tokyo": "JP", "sydney": "AU",
    "australia": "AU",
}

# Lookaround boundaries work for BOTH word-ending ("sweden") and
# dot-ending ("u.s.") terms; \b..\b only works for the former.
_COMPILED = [
    (re.compile(rf"(?<!\w){re.escape(term)}(?!\w)"), country)
    for term, country in _TERMS.items()
]

# Multi-region qualifiers: "North/South/Latin/Central America" names a
# hemisphere, not the US — a listing spanning "Europe, North America" is
# still takeable for a European. Blank these spans before matching the
# bare 'america' term.
_MULTI_REGION = re.compile(r"\b(north|south|latin|central)\s+america\b")


def location_countries(location: str) -> Set[str]:
    """Resolve a free-text location to the SET of ISO country codes named.

    An empty set means 'unresolvable / global' — the caller keeps its
    existing behaviour for those. Every country mentioned counts: the
    gate blocks only when the set is non-empty AND excludes the user's
    country, so a listing that names the user's country passes no matter
    what else it names.
    """
    if not location:
        return set()
    hay = _MULTI_REGION.sub(" ", location.lower())
    return {country for pattern, country in _COMPILED if pattern.search(hay)}
