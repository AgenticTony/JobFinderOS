"""
Country source packs — which job boards serve which country.

The onboarding country picks the pack; the global SCRAPE_SOURCES env stays
as the master allow-list (a source only runs if it is BOTH in the pack and
configured). Add a country by adding a pack here.
"""

from typing import Dict, List

# Country-agnostic boards included in every pack
SHARED_REMOTE_SOURCES = ["remotive", "jobicy", "workingnomads", "arbeitnow"]

SOURCE_PACKS: Dict[str, List[str]] = {
    "SE": ["jobtech", "careerjet", *SHARED_REMOTE_SOURCES],
    "GB": ["reed", "careerjet", *SHARED_REMOTE_SOURCES],
}


def pack_for_country(country: str) -> List[str]:
    """Sources for a country, or [] when unknown."""
    return SOURCE_PACKS.get((country or "").upper(), [])


def available_countries() -> List[Dict[str, str]]:
    """Countries with a configured pack, for the onboarding UI."""
    from app.data.geo import COUNTRIES

    return [
        {"code": code, "name": info["name"], "flag": info["flag"]}
        for code, info in COUNTRIES.items()
        if code in SOURCE_PACKS
    ]
