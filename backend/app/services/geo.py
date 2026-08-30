"""Municipality centroids for radius search.

JobTech's search API accepts position + position.radius (km) — a true
commute-zone fetch that catches neighbouring municipalities without the
user naming them. The API needs a coordinate, the user picks a
municipality name: this table bridges them.

Coordinates are city-centre approximations (±~2 km — irrelevant at the
15-50 km radii on offer). Coverage: the majors plus the distinct
regions, so a stranger in a small kommun keeps the exact municipality
match (resolve returns None -> the fetch falls back to municipality
codes, never breaks). Extend from SCB's open municipality geometries
when coverage matters.
"""

from typing import List, Optional, Tuple

MUNICIPALITY_CENTROIDS: dict[str, Tuple[float, float]] = {
    # Skåne / south
    "malmö": (55.605, 13.000),
    "lund": (55.704, 13.191),
    "helsingborg": (56.043, 12.694),
    "kristianstad": (56.029, 14.157),
    "landskrona": (55.871, 12.833),
    "trelleborg": (55.373, 13.157),
    "ystad": (55.429, 13.820),
    "karlskrona": (56.161, 15.586),
    "växjö": (56.877, 14.810),
    "kalmar": (56.661, 16.361),
    "halmstad": (56.671, 12.857),
    "varberg": (57.106, 12.246),
    # West
    "göteborg": (57.707, 11.967),
    "goteborg": (57.707, 11.967),
    "borås": (57.721, 12.934),
    "boras": (57.721, 12.934),
    "trollhättan": (58.283, 12.292),
    "trollhattan": (58.283, 12.292),
    "uddevalla": (58.350, 11.913),
    # Mid
    "jönköping": (57.783, 14.161),
    "jonkoping": (57.783, 14.161),
    "örebro": (59.274, 15.207),
    "orebro": (59.274, 15.207),
    "karlstad": (59.379, 13.504),
    "västerås": (59.609, 16.548),
    "vasteras": (59.609, 16.548),
    "eskilstuna": (59.371, 16.504),
    "linköping": (58.411, 15.621),
    "linkoping": (58.411, 15.621),
    "norrköping": (58.588, 16.192),
    "norrkoping": (58.588, 16.192),
    # Stockholm / east
    "stockholm": (59.329, 18.069),
    "södertälje": (59.196, 17.626),
    "sodertalje": (59.196, 17.626),
    "uppsala": (59.859, 17.639),
    "nyköping": (58.753, 17.009),
    "nykoping": (58.753, 17.009),
    # North
    "gävle": (60.674, 17.143),
    "gavle": (60.674, 17.143),
    "sundsvall": (62.391, 17.308),
    "östersund": (63.179, 14.636),
    "ostersund": (63.179, 14.636),
    "umeå": (63.826, 20.263),
    "umea": (63.826, 20.263),
    "skellefteå": (64.750, 20.950),
    "skelleftea": (64.750, 20.950),
    "luleå": (65.584, 22.155),
    "lulea": (65.584, 22.155),
    "kiruna": (67.856, 20.225),
}


def resolve_position(municipalities: List[str]) -> Optional[Tuple[float, float]]:
    """Centroid of the user's PRIMARY municipality — municipalities[0]
    — or None. Strictly the first pick, not 'the first one that
    resolves': the UI names chosen[0] as the anchor, so silently
    substituting a resolvable later town would centre the commute zone
    somewhere the user did not choose and (with the API-side geo filter)
    exclude their own town entirely. Unresolvable primary -> None ->
    the fetch falls back to municipality codes for every chosen town.
    """
    if not municipalities:
        return None
    return MUNICIPALITY_CENTROIDS.get(str(municipalities[0]).strip().lower())


def effective_municipalities(ctx: dict) -> List[str]:
    """The ctx's municipality list with the legacy single-field
    fallback. SINGLE source for both the scraper's place params and
    the store gate's geo decision — the two must never read different
    fields (review finding: municipalities=[] + legacy municipality=
    used to skip the gate for an unfiltered, whole-of-Sweden fetch).
    """
    munis = list(ctx.get("municipalities") or [])
    if not munis and ctx.get("municipality"):
        munis = [str(ctx["municipality"])]
    return munis


def geo_plan(ctx: dict) -> Optional[Tuple[float, float, int]]:
    """The fetch's geo decision, computed ONCE and shared: (lat, lon,
    km) when API-side radius filtering applies, else None.

    None whenever: no radius, no chosen municipality, or the user's
    PRIMARY town has no centroid (strict anchoring). The scraper builds
    its position params from this and the store gate asks the same
    function — they cannot diverge because neither re-derives anything.
    """
    km = int(ctx.get("search_radius_km") or 0)
    if km <= 0:
        return None
    munis = effective_municipalities(ctx)
    if not munis:
        return None
    pos = resolve_position(munis)
    if pos is None:
        return None
    return (pos[0], pos[1], km)


def radius_geo_active(ctx: dict) -> bool:
    """True when this scrape context uses API-side geo filtering.
    Kept as the boolean convenience over geo_plan — same single source."""
    return geo_plan(ctx) is not None


# Canonical spellings (the keys minus the diacritic-free lookup
# variants) — the /geo endpoint exposes these so the wizard only
# offers the radius where a centroid actually anchors it.
_RADIUS_LOOKUP_VARIANTS = {
    "goteborg", "boras", "trollhattan", "jonkoping", "orebro",
    "vasteras", "linkoping", "norrkoping", "sodertalje", "nykoping",
    "gavle", "ostersund", "umea", "skelleftea", "lulea",
}
RADIUS_SUPPORTED_MUNICIPALITIES = sorted(
    k.capitalize() for k in MUNICIPALITY_CENTROIDS
    if k not in _RADIUS_LOOKUP_VARIANTS
)
