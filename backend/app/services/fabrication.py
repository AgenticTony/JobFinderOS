"""Fabrication guard — Layer A: the deterministic checker.

WO-01 / Invariant #4: every fact in a tailored CV or cover letter must
trace to the source CV. Until this module, the invariant was enforced by
prompt text alone; every test touching tailoring mocked the tailor, so
nothing ever verified WHAT was written.

DESIGN CONSTRAINTS (from the WO, hard-won):

1. Translation invariance. The tailored document may be in a different
   language than the CV (an English CV tailored to a Swedish posting
   produces Swedish). Only atoms that survive translation are checked
   deterministically: employers, dates, numbers, credentials and
   technologies. Job titles and prose are NOT matched ("Developer" ->
   "Utvecklare") — semantic fidelity is Layer B's (the LLM judge) job.
   A claim absent here is not proof of honesty, only of the absence of a
   mechanically detectable invention.

2. Diacritics are letters. Swedish å ä ö are distinct letters, not
   accented a/o — normalisation is casefold + whitespace collapse ONLY.

3. Tiered confidence. credential/organisation/year/metric findings are
   high-confidence (near-exact, translation-invariant) and drive
   regeneration→block. technology findings are advisory ("Azure" vs
   "Microsoft Azure") and drive review-UI flags only.
"""

import re
from dataclasses import dataclass, field
from typing import List

# --------------------------------------------------------------------------
# Claim extraction
# --------------------------------------------------------------------------

@dataclass
class Claim:
    """An atom asserted in the tailored document. `context` is the
    surrounding sentence so the review UI can show WHERE the unverified
    claim appears."""
    kind: str          # year | organisation | credential | metric | technology
    value: str
    context: str = ""
    tier: str = field(default="advisory")  # high | advisory


HIGH_CONFIDENCE = {"year", "organisation", "credential", "metric"}
ADVISORY = {"technology"}

# Capitalised-run tokens that are never organisation names: section
# headers, salutations/sign-offs, sentence-start words, months, languages,
# countries we serve. Breaking a run on any of these prevents
# "Dear Hiring Manager" and "Professional Summary" registering as employers.
_ORG_STOPWORDS = {
    # salutations / sign-offs
    "dear", "hello", "hi", "hey", "sincerely", "regards", "yours", "truly",
    "best", "kind", "wishes", "thanks", "thank", "you",
    # section headers (EN + SV)
    "summary", "professional", "profile", "experience", "work", "education",
    "skills", "skill", "certifications", "certificates", "projects", "career",
    "about", "references", "languages", "technologies", "technical",
    "interests", "volunteer", "volunteering", "awards", "honors", "publications",
    "sammanfattning", "profil", "erfarenhet", "arbetslivserfarenhet", "utbildning",
    "kunskaper", "sprak", "språk", "projekt", "referenser", "intressen",
    "uppdragsHistorik", "certifieringar", "kompetenser", "körmål", "körmål",
    # common sentence-start / structural words
    "i", "my", "me", "the", "a", "an", "in", "at", "for", "with", "as", "and",
    "but", "or", "we", "your", "their", "this", "that", "these", "those", "it",
    "its", "our", "his", "her", "he", "she", "they", "from", "to", "of", "on",
    "by", "was", "were", "is", "are", "have", "has", "had", "be", "been",
    "am", "will", "would", "can", "could", "led", "worked", "built",
    "developed", "designed", "managed", "created", "drove", "owned",
    "delivered", "improved", "reduced", "increased", "grew", "started",
    "joined", "left", "since", "until", "during", "while", "when", "where",
    "what", "who", "how", "why", "not", "also", "more", "most", "over",
    "under", "between", "after", "before", "about", "into", "across",
    "arbetade", "utvecklade", "byggde", "ledde", "skapade", "hanterade",
    "forbattrade", "förbättrade", "minskade", "okade", "ökade", "var", "nu",
    "underhöll", "underhall", "ansvarade", "driv", "drev", "startade",
    "juniorutvecklare", "mjukvaruingenjör", "seniorutvecklare",
    # live-judge FP classes (2026-08-27): Swedish salutations/connectors,
    # skill-list glue words, title prefixes
    "hej", "med", "och", "implemented", "gdpr", "product", "databases",
    "apis", "ai", "programmerare", "fullstack", "frontend", "backend",
    "använder", "anvander",
    # country/region names — location enumerations glue into runs
    # ("Sweden, Germany" is a remote listing's reach, not an employer)
    "sweden", "germany", "sverige", "tyskland", "europe", "europa",
    "france", "frankrike", "spain", "spanien", "netherlands", "nederländerna",
    "norway", "norge", "denmark", "danmark", "finland", "uk", "usa",
    "united", "kingdom", "states", "canada", "australia", "schweiz",
    # months / weekdays / languages / misc calendar (EN + SV)
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
    "september", "oktober", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "english", "swedish", "german", "french", "spanish", "engelska", "svenska",
    "tyska", "franska", "spanska",
    "curriculum", "vitae", "resume", "cv",
}

_CREDENTIAL_PATTERNS = [
    r"\bb\.?sc\.?\b", r"\bm\.?sc\.?\b", r"\bb\.?eng\.?\b", r"\bm\.?eng\.?\b",
    r"\bph\.?d\.?\b", r"\bmba\b", r"\bbachelor(?:'s)?\b", r"\bmaster(?:'s)?\b",
    r"\bdoctorate\b", r"\bcertified\s+\w+(?:\s+\w+)?",
    r"\baz-?\d{3}\b", r"\bcka\d{2,}\b", r"\baws\s+certified\b",
    r"\bcissp\b", r"\bpmp\b", r"\bcsm\b",
    # Swedish degrees
    r"\bcivilingenjör\b", r"\bhögskoleingenjör\b", r"\bhogskoleingenjor\b",
    r"\bkandidatexamen\b", r"\bmasterexamen\b", r"\bdoktorsexamen\b",
]

# Degree equivalence groups: abbreviations are translation-invariant but
# degree NAMES translate ("MSc" <-> "Masterexamen"). A credential claim is
# supported when any member of its group appears in the source.
_CREDENTIAL_GROUPS = [
    {"master", "msc", "m.sc", "masterexamen", "magisterexamen",
     "master s", "msc ", "m sc"},
    {"bachelor", "bsc", "b.sc", "kandidatexamen"},
    {"phd", "doctorate", "doktorsexamen", "ph.d"},
    {"civilingenjör", "civilingenjor"},
    {"högskoleingenjör", "hogskoleingenjor"},
]

_TECHNOLOGY_VOCABULARY = [
    "kubernetes", "docker", "terraform", "ansible", "jenkins", "github",
    "gitlab", "bitbucket", "elk", "prometheus", "grafana", "datadog",
    "aws", "azure", "gcp", "google cloud", "heroku", "vercel", "netlify",
    "react", "vue", "angular", "svelte", "next.js", "nuxt", "ember",
    "node.js", "express", "nestjs", "django", "flask", "fastapi", "spring",
    "laravel", "rails", ".net", "symfony",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "cassandra",
    "dynamodb", "sqlite", "kafka", "rabbitmq", "graphql", "grpc", "rest",
    "python", "javascript", "typescript", "java", "c#", "c++", "ruby",
    "golang", "rust", "kotlin", "swift", "scala", "php", "perl", "haskell",
    "pytorch", "tensorflow", "scikit-learn", "keras", "xgboost", "spark",
    "hadoop", "airflow", "dbt", "snowflake", "bigquery", "databricks",
    "machine learning", "deep learning", "nlp", "computer vision",
    "microservices", "ci/cd", "devops", "linux", "bash", "powershell",
]

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# Percentages, currency, "N years/persons", bare large numbers attached to %
_METRIC_RES = [
    # (?<!\w)...(?!\w): a trailing \b after '%' can never fire before a
    # space (same dead-boundary class as the u.s. lexicon bug)
    re.compile(r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:%|percent|procent)(?!\w)", re.I),
    re.compile(r"[$€£]\s*\d+(?:[.,]\d+)?(?:\s*[mk])?\b", re.I),
    re.compile(r"\b\d+\s*(?:years?|år)\b", re.I),
    re.compile(r"\b\d+\s*(?:people|personer|team\s*members?|medarbetare)\b", re.I),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _normalise(text: str) -> str:
    """Casefold, strip punctuation, collapse whitespace. Diacritics are
    preserved (å ä ö are letters, not accents — constraint 2); punctuation
    is stripped because commas separate title/company/city in both CVs and
    tailored output ("Svenska Spel, Stockholm" must support the run
    "Svenska Spel Stockholm")."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _extract_years(text: str) -> List[Claim]:
    claims = []
    for sentence in _sentences(text):
        for match in _YEAR_RE.finditer(sentence):
            claims.append(Claim(kind="year", value=match.group(0),
                                context=sentence, tier="high"))
    return claims


def _org_tier(tokens) -> str:
    if len(tokens) >= 3 or any(t.casefold() in _CORPORATE_MARKERS
                               for t in tokens):
        return "high"
    return "advisory"


def _extract_organisations(text: str) -> List[Claim]:
    """Runs of >=2 consecutive capitalised tokens, with stoplist tokens
    breaking runs. Single proper nouns are skipped — they are mostly
    sentence starts and cities, and the false-positive cost outweighs
    the coverage."""
    claims = []
    for sentence in _sentences(text):
        tokens = [t.rstrip(".") for t in re.findall(r"[\w'’.-]+", sentence)]
        run: List[str] = []
        for tok in tokens:
            if (len(tok) >= 2 and tok[0].isupper()
                    and tok.casefold() not in _ORG_STOPWORDS
                    and any(c.isalpha() for c in tok)):
                run.append(tok)
            else:
                if len(run) >= 2 and not all(
                        t.casefold() in _GENERIC_TITLE_WORDS for t in run):
                    claims.append(Claim(
                        kind="organisation", value=" ".join(run),
                        context=sentence, tier=_org_tier(run)))
                run = []
        if len(run) >= 2 and not all(
                t.casefold() in _GENERIC_TITLE_WORDS for t in run):
            claims.append(Claim(kind="organisation", value=" ".join(run),
                                context=sentence, tier=_org_tier(run)))
    return claims


def _extract_credentials(text: str) -> List[Claim]:
    claims = []
    lowered = _normalise(text)
    # map each pattern match back to a sentence for context
    for sentence in _sentences(text):
        s_low = _normalise(sentence)
        for pattern in _CREDENTIAL_PATTERNS:
            for match in re.finditer(pattern, s_low):
                claims.append(Claim(kind="credential",
                                    value=match.group(0).strip(),
                                    context=sentence, tier="high"))
    return _dedupe(claims, lowered)


def _extract_metrics(text: str) -> List[Claim]:
    claims = []
    for sentence in _sentences(text):
        for pattern in _METRIC_RES:
            for match in pattern.finditer(sentence):
                value = match.group(0).strip()
                # Normalise the numeric core: "40%" -> "40", "$3M" -> "3"
                num = re.search(r"\d+(?:[.,]\d+)?", value)
                if num:
                    claims.append(Claim(
                        kind="metric",
                        value=f"{value}|{num.group(0).replace(',', '.')}",
                        context=sentence, tier="high"))
    return claims


def _extract_technologies(text: str) -> List[Claim]:
    claims = []
    lowered = _normalise(text)
    for tech in _TECHNOLOGY_VOCABULARY:
        if re.search(rf"(?<!\w){re.escape(tech)}(?!\w)", lowered):
            claims.append(Claim(kind="technology", value=tech,
                                context="", tier="advisory"))
    return claims


def _dedupe(claims: List[Claim], _) -> List[Claim]:
    seen = set()
    out = []
    for c in claims:
        key = (c.kind, _normalise(c.value))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def extract_claims(tailored_text: str) -> List[Claim]:
    """Every translation-invariant atom asserted in the tailored text."""
    claims = (
        _extract_years(tailored_text)
        + _extract_organisations(tailored_text)
        + _extract_credentials(tailored_text)
        + _extract_metrics(tailored_text)
        + _extract_technologies(tailored_text)
    )
    return _dedupe(claims, tailored_text)


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def _metric_supported(claim: Claim, source_norm: str) -> bool:
    """A metric is supported when its numeric core appears in the source
    within the same metric kind (a '40%' needs a '40%' — a bare '40'
    elsewhere is not support for a percentage)."""
    value, num = claim.value.split("|", 1)
    kind_hint = "%" if ("%" in value or "percent" in value.lower()
                        or "procent" in value.lower()) else None
    if kind_hint:
        # every percentage in the source
        src_nums = {
            m.group(1).replace(",", ".")
            for m in re.finditer(
                r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:%|percent|procent)(?!\w)",
                source_norm, re.I)
        }
        return num in src_nums
    # currency / durations / team sizes: numeric core anywhere in source
    return re.search(rf"(?<!\d){re.escape(num)}(?!\d)", source_norm) is not None


_CONNECTORS = {"and", "och", "med", "i", "at", "på", "pa", "of", "in", "to"}


def _strip_connectors(tokens):
    return [t for t in tokens if t not in _CONNECTORS]


def unsupported_claims(source_cv: str, tailored_text: str,
                       allowed_names: List[str] = None) -> List[Claim]:
    """Atoms asserted in tailored_text that do not appear in source_cv.

    Checks ONLY translation-invariant atoms; matching is casefold +
    whitespace-collapsed substring against the source, diacritics
    preserved. `allowed_names` are legitimate non-CV entities — the
    addressee company being applied to is context, not a career claim
    (a live-judge FP class: 'Hej Birger AB' is the employer, not a
    fabricated history). A claim absent here is not proof of honesty,
    only of the absence of a mechanically detectable invention (Layer B
    judges semantics; Layer C acts on these findings).
    """
    src = _normalise(source_cv or "")
    src_tokens = set(_strip_connectors(src.split()))
    src_noc = " ".join(_strip_connectors(src.split()))
    # Metrics need the % signs intact — _normalise strips punctuation,
    # which would delete the very atom being matched
    src_raw = " ".join((source_cv or "").casefold().split())
    allowed = [_normalise(n) for n in (allowed_names or []) if n]
    findings: List[Claim] = []

    for claim in extract_claims(tailored_text or ""):
        value = _normalise(claim.value)
        if any(a and a in value for a in allowed):
            continue  # the addressee/company being applied to
        if claim.kind == "metric":
            if not _metric_supported(claim, src_raw):
                findings.append(claim)
            continue
        if claim.kind == "technology":
            if claim.value not in src:
                findings.append(claim)
            continue
        if claim.kind == "organisation":
            if _org_supported(claim, src) or _org_supported(claim, src_noc):
                continue
            tokens = _strip_connectors(value.split())
            # Glued skill/title runs: every token present in the source
            # somewhere ("Databases SQL" over a CV listing SQL and
            # databases) — order-free containment kills the FP class
            if tokens and all(t in src_tokens for t in tokens):
                continue
            findings.append(claim)
            continue
        if claim.kind == "credential":
            if not _credential_supported(claim, src):
                findings.append(claim)
            continue
        if value not in src:
            findings.append(claim)

    return findings


def _credential_supported(claim: Claim, src: str) -> bool:
    """Direct substring, or any member of the claim's degree-equivalence
    group present in the source (degree names translate; abbreviations
    and certifications like 'AWS Certified' do not and stay strict)."""
    value = _normalise(claim.value)
    if value in src:
        return True
    for group in _CREDENTIAL_GROUPS:
        if any(member in value for member in group if len(member) > 3):
            if any(member in src for member in group):
                return True
    return False


# Title/structural words that may appear inside an organisation run but
# cannot ESTABLISH its identity — a window consisting only of these
# ("Software Engineer") must not validate a run whose employer part
# ("Acme Global Ltd") is fabricated.
# Corporate markers: a run carrying one of these (or 3+ tokens) is a
# plausible employer name and stays HIGH-confidence. Bare 2-token runs
# without markers ("Hiring Team", "Berlin EU", "Practices Agile") demote
# to ADVISORY — the live judge showed capitalised-run extraction has an
# irreducible phrase-glue FP tail on real prose, and regeneration is too
# costly a response for it.
_CORPORATE_MARKERS = {"ab", "ab", "ltd", "llc", "inc", "plc", "gmbh",
                      "ag", "group", "holding", "holdings", "solutions",
                      "consulting", "technologies", "systems", "labs",
                      "studio", "studios", "ventures", "capital"}

_GENERIC_TITLE_WORDS = {
    "software", "engineer", "developer", "senior", "junior", "manager",
    "director", "consultant", "analyst", "designer", "architect",
    "specialist", "coordinator", "assistant", "lead", "head", "chief",
    "officer", "administrator", "technician", "tester", "intern",
    "ab", "ltd", "llc", "inc", "plc", "limited", "solutions",
    "utvecklare", "ingenjör", "ingenjor", "konsult", "analytiker",
    "koordinator", "assistent", "tekniker", "praktikant", "chef",
    "certified",
}


def _org_supported(claim: Claim, src: str) -> bool:
    """An organisation run is supported when the full run — or any
    contiguous >=2-token window CONTAINING a non-generic token — appears
    in the source. The window rule handles title words gluing onto
    employer names across translation ("Juniorutvecklare Tietoevry
    Malmö" is supported by "Tietoevry Malmö" even though the title did
    not translate); the non-generic requirement stops the inverse — a
    fabricated employer riding through on a supported job title."""
    tokens = _normalise(claim.value).split()
    if _normalise(claim.value) in src:
        return True
    for width in range(len(tokens) - 1, 1, -1):
        for start in range(len(tokens) - width + 1):
            window_tokens = tokens[start:start + width]
            if all(t in _GENERIC_TITLE_WORDS for t in window_tokens):
                continue
            if " ".join(window_tokens) in src:
                return True
    return False


def split_tiers(findings: List[Claim]):
    """Layer C's tier split: high-confidence drives regeneration/block,
    advisory drives review-UI flags only. The CLAIM's own tier wins over
    its kind — 2-token markerless organisation runs are advisory (the
    phrase-glue FP class), marker-bearing and 3+ token runs are high."""
    high = [c for c in findings if c.tier == "high"]
    advisory = [c for c in findings if c.tier == "advisory"]
    return high, advisory


def findings_as_json(findings: List[Claim]) -> list:
    """Serialize findings for the ApplicationDraft column / API response."""
    return [{"kind": c.kind, "value": c.value.split("|")[0],
             "context": c.context, "tier": c.tier} for c in findings]
