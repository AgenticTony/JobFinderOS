"""
Language gate — jobs written in a language the user doesn't speak never
enter the DB (scrape gate) or the matching queue (matcher gate).

Detection is a cheap marker heuristic over title + description snippet.
English always passes: it's the job-board lingua franca and tech titles
are English worldwide, so we only gate languages the user did NOT select
in onboarding.
"""

# Strong markers are decisive alone in a title; weak markers add up.
# Danish and Norwegian share markers — one bucket, one chip.
_MARKERS: dict[str, tuple[set[str], set[str]]] = {
    "German": (
        {"entwickler", "(m/w/d)", "(w/m/d)", "(f/m/d)", "mitarbeiter", "fachinformatiker"},
        {" und ", " für ", " mit ", " ihrer ", "sie werden", "ihre aufgaben", "das sind ihre"},
    ),
    "French": (
        {"développeur", "(h/f)", "expériencé", "ingénieur logiciel", "cdi –", "alternance"},
        {" nous ", " vous ", " et les ", " expérience ", "au sein de", "poste à"},
    ),
    "Spanish": (
        {"desarrollador", "experiencia demostrable", "oferta de empleo"},
        {" y con ", " para el ", "trabajo en", "vacante ", " que se "},
    ),
    "Italian": (
        {"sviluppatore", "esperienza maturata", "offerta di lavoro"},
        {" e con ", " per la ", "lavoro in", "candidatura ", " che si "},
    ),
    "Dutch": (
        {"ervaring op", "vacature:", "ontwikkelaar"},
        {" en voor ", " voor een ", "met een ", "binnen de ", "werken bij"},
    ),
    "Swedish": (
        {"utvecklare", "söker vi", "välmotiverad", "arbetsgivare"},
        {" och ", " att du ", " erfarenhet ", " för att ", " hos oss ", " kommer att "},
    ),
    "Danish/Norwegian": (
        {"udvikler", "utvikler", "vi søger", "vi söker"},
        {" og ", " erfaring ", " for at ", " du vil ", " hos oss ", " arbeid "},
    ),
    "Finnish": (
        {"kehittäjä", "kokemus ohjelmistokehityksestä"},
        {" ja ", " sinun ", " työpaikka", " haku "},
    ),
}

_STRONG_WEIGHT = 5
_WEAK_WEIGHT = 1
_DETECT_THRESHOLD = 6


def detect_language(title: str | None, description: str | None = "") -> str | None:
    """Best-effort language of a posting, or None if none clearly dominates."""
    title_l = (title or "").lower()
    body_l = (title_l + "\n" + (description or "")[:2500].lower())

    best_lang, best_score = None, 0
    for lang, (strong, weak) in _MARKERS.items():
        score = 0
        for m in strong:
            if m in title_l:
                score += _STRONG_WEIGHT * 2  # a strong marker in the title is decisive
            score += body_l.count(m) * _STRONG_WEIGHT
        for m in weak:
            score += body_l.count(m) * _WEAK_WEIGHT
        if score > best_score:
            best_lang, best_score = lang, score

    return best_lang if best_score >= _DETECT_THRESHOLD else None


def passes_language_filter(
    title: str | None,
    description: str | None,
    languages: list[str],
) -> bool:
    """True when the posting's language is one the user speaks (or English/unknown)."""
    if not languages:
        return True  # gate disabled until onboarding captures languages
    spoken = {lang.strip().lower() for lang in languages}
    lang = detect_language(title, description)
    if lang is None:
        return True  # undetectable (usually English) — let the AI judge
    return lang.lower() in spoken or lang.startswith(tuple(spoken))
