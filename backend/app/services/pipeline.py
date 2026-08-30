"""
Pipeline service — orchestrates the full JobFinderOS loop:

    scrape sources -> per-user location filter -> AI match vs profile -> recommend

This is the job-seeker inversion of TalentHive's demo screening orchestration.
When the user has completed onboarding, their country picks the source pack,
their CV-derived queries drive the targeted boards, and their region/city
filters what gets stored at all.
"""

import logging
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.dedupe import dedupe_key_for
from app.core.timeutil import utc_now
from app.models import JobPosting, MatchResult, Profile, ScrapeRun
from app.schemas.common import dump_json_list, parse_json_list
from app.services import matcher_service, source_packs
from app.services.country_lexicon import blocked_for_user, location_countries
from app.services.language_filter import passes_language_filter
from app.services.scrapers import SCRAPER_REGISTRY, NormalizedJob

logger = logging.getLogger(__name__)

# Sources whose fetchers honor ctx["delta_since"] (published-after
# fetching). Query-less feeds (remote boards) return date-sorted windows
# already — local dedupe makes them behave like deltas for free.
DELTA_SOURCES = {"jobtech"}

# Re-read window on top of the watermark: absorbs API clock skew and
# ads re-published with a fresh date. Cheap — dedupe eats the overlap.
DELTA_OVERLAP_HOURS = 24


def _scope_key(ctx: Dict) -> str:
    munis = list(ctx.get("municipalities") or [])
    if not munis and ctx.get("municipality"):
        munis = [str(ctx["municipality"])]
    key = ",".join(sorted(m.lower() for m in munis))
    # Radius changes the fetch scope entirely (position search replaces
    # municipality codes) — it belongs in the watermark key so a new
    # radius deep-backfills rather than delta-misses its new coverage.
    km = int(ctx.get("search_radius_km") or 0)
    if km > 0:
        key += f"|r{km}"
    return key


def _watermark_queries(ctx: Dict) -> List[str]:
    """Every independent search unit of a fetch: free-text queries (bare,
    for watermark continuity) plus one 'name:CODE' unit per occupation
    concept. A new code has no watermark -> deep backfill for its
    history, exactly like a new query."""
    qs = [str(q).strip() for q in (ctx.get("queries") or []) if str(q).strip()]
    qs += [f"name:{c}" for c in (ctx.get("occupation_codes") or []) if c]
    return qs or [""]


def delta_since_for(db: Session, source: str, ctx: Dict):
    """Cutoff for a published-after fetch, or None = full backfill.

    Keyed on (source, query, scope): ANY query or scope never fetched
    before forces a deep backfill — a new user's municipalities or a
    newly added search term automatically gets the full history read,
    not just the last day.
    """
    from datetime import timedelta

    from app.models import ScrapeWatermark

    scope = _scope_key(ctx)
    stamps = {
        r.query: r.watermark_at
        for r in db.query(ScrapeWatermark).filter_by(source=source, scope=scope)
    }
    cutoffs = []
    for q in _watermark_queries(ctx):
        if q not in stamps:
            return None  # something new under this scope -> backfill
        cutoffs.append(stamps[q])
    oldest = min(cutoffs)
    return oldest - timedelta(hours=DELTA_OVERLAP_HOURS)


def set_watermarks(db: Session, source: str, ctx: Dict) -> None:
    """Record a successful fetch for every (source, query, scope)."""
    from app.models import ScrapeWatermark

    scope = _scope_key(ctx)
    now = utc_now()
    for q in _watermark_queries(ctx):
        row = (
            db.query(ScrapeWatermark)
            .filter_by(source=source, query=q, scope=scope)
            .first()
        )
        if row is not None:
            row.watermark_at = now
        else:
            db.add(ScrapeWatermark(source=source, query=q, scope=scope, watermark_at=now))
    db.commit()


def build_scrape_context(db: Session, *, user_id) -> Optional[Dict]:
    """Per-user scrape settings from the caller's onboarded profile.

    user_id is required: the old bare call fell back to ORDER BY id DESC,
    so a forgotten argument would scrape against the newest stranger's
    country, queries and languages.
    """
    profile = (
        db.query(Profile)
        .filter(Profile.country.isnot(None), Profile.user_id == user_id)
        .first()
    )
    if not profile:
        return None
    return {
        "country": (profile.country or "").upper(),
        "region": profile.region,
        "municipality": profile.municipality,
        "municipalities": parse_json_list(getattr(profile, "municipalities", None)),
        "search_radius_km": getattr(profile, "search_radius_km", None) or 0,
        "remote_only": bool(profile.remote_only),
        "include_remote": bool(profile.include_remote),
        "queries": parse_json_list(profile.search_queries),
        # occupation-name concept CODES (strings) — the scraper turns
        # each into its own taxonomy-filtered search unit
        "occupation_codes": [
            pick["code"]
            for pick in (parse_json_list(getattr(profile, "occupation_codes", None)) or [])
            if isinstance(pick, dict) and pick.get("code")
        ],
        "languages": parse_json_list(profile.languages) or [],
    }


def passes_location_filter(job: NormalizedJob, ctx: Dict) -> bool:
    """
    Universal location gate — applied to every source identically.

    - STRICT municipality matching (user decision, post-first-hunt: picking
      Malmö means Malmö): a job passes if its location names ANY of the
      user's chosen municipalities. The legacy single `municipality` value
      behaves as a one-item list.
    - Region-wide admission ONLY when the user chose no municipality at
      all (the wizard's explicit whole-region path).
    - Remote jobs and location-less jobs only pass when the user opted
      into remote work in onboarding (include_remote) — otherwise the
      search is strictly local
    - remote_only users additionally drop non-remote jobs
    """
    if ctx.get("remote_only") and not job.remote:
        return False

    munis = [m.lower() for m in ctx.get("municipalities") or []]
    if not munis and ctx.get("municipality"):
        munis = [str(ctx["municipality"]).lower()]
    if munis and job.location and any(m in job.location.lower() for m in munis):
        return True
    if (not munis and ctx.get("region") and job.location
            and str(ctx["region"]).lower() in job.location.lower()):
        return True

    # COUNTRY ROUTING (WO-06 / D1): a job whose location names ONLY
    # foreign countries is not takeable — remote in the US still needs US
    # work authorization. MEMBERSHIP, not ranking: a listing that names
    # the user's country ("Sweden, Germany") passes no matter what else
    # it names; unresolvable locations ("Remote", empty) resolve to an
    # empty set and fall through to the remote-opt-in rule unchanged.
    if blocked_for_user(location_countries(job.location), ctx.get("country")):
        return False

    # Outside the chosen area (or no location text): only for remote-opted users
    return bool(ctx.get("include_remote")) and bool(job.remote)


def scrape_source(db: Session, source_name: str, ctx: Optional[Dict] = None) -> ScrapeRun:
    """Run one scraper, upsert new jobs, record a ScrapeRun audit row."""
    run = ScrapeRun(source=source_name, status="running")
    db.add(run)
    db.commit()

    # Delta mode: the fetcher gets a published-after cutoff derived from
    # the last successful fetch of this exact (source, query, scope).
    # ctx["backfill"] (onboarding, explicit) forces the deep read.
    ctx = dict(ctx or {})
    if source_name in DELTA_SOURCES:
        ctx["delta_since"] = (
            None if ctx.get("backfill") else delta_since_for(db, source_name, ctx)
        )
    else:
        ctx.pop("delta_since", None)

    scraper_cls = SCRAPER_REGISTRY.get(source_name)
    if scraper_cls is None:
        run.status = "failed"
        run.error = f"Unknown source: {source_name}"
        run.finished_at = utc_now()
        db.commit()
        return run

    if not scraper_cls.is_configured(ctx):
        run.status = "skipped"
        run.error = f"{source_name} not configured (see backend/.env.example)"
        run.finished_at = utc_now()
        db.commit()
        return run

    try:
        jobs: List[NormalizedJob] = scraper_cls().fetch(ctx)
        run.jobs_found = len(jobs)

        # Universal location gate — out-of-area jobs are never stored,
        # so they never consume matching budget. EXCEPT when the source
        # already geo-filtered this fetch (jobtech position+radius): the
        # API's distance filter IS the location gate for those jobs, and
        # the strict local municipality check would wrongly reject the
        # neighbouring-kommun ads the radius exists to catch.
        if ctx:
            from app.services.geo import radius_geo_active

            geo_filtered = source_name == "jobtech" and radius_geo_active(ctx)
            if not geo_filtered:
                before = len(jobs)
                jobs = [nj for nj in jobs if passes_location_filter(nj, ctx)]
                if before != len(jobs):
                    logger.info("[%s] location filter: %d -> %d jobs", source_name, before, len(jobs))
            else:
                logger.info("[%s] API-side geo filter active (radius) — local gate skipped", source_name)

            # Freshness gate — postings older than MAX_POSTING_AGE_DAYS
            # are almost certainly closed; never store them
            max_age = timedelta(days=settings.MAX_POSTING_AGE_DAYS)
            fresh = len(jobs)
            jobs = [
                nj
                for nj in jobs
                if nj.published_at is None or nj.published_at >= utc_now() - max_age
            ]
            if fresh != len(jobs):
                logger.info("[%s] freshness gate: %d -> %d jobs", source_name, fresh, len(jobs))

            # Language gate — postings in languages the user doesn't speak
            # are dropped before storing (English always passes)
            before = len(jobs)
            jobs = [
                nj
                for nj in jobs
                if passes_language_filter(nj.title, nj.description, ctx.get("languages", []))
            ]
            if before != len(jobs):
                logger.info(
                    "[%s] language filter: %d -> %d jobs", source_name, before, len(jobs)
                )

        new_count = 0
        for nj in jobs:
            if _job_exists(db, nj):
                continue
            posting = JobPosting(
                source=nj.source,
                source_id=nj.source_id,
                dedupe_key=dedupe_key_for(nj.title, nj.company, nj.location),
                title=nj.title[:500],
                company=nj.company,
                location=nj.location,
                remote=1 if nj.remote else 0,
                url=nj.url[:1000],
                description=nj.description,
                employment_type=nj.employment_type,
                salary=nj.salary,
                tags=dump_json_list(nj.tags),
                category=nj.category,
                application_email=nj.application_email,
                application_url=nj.application_url,
                published_at=nj.published_at,
            )
            db.add(posting)
            db.flush()  # make the row visible so same-run duplicates are caught
            new_count += 1

        db.commit()
        run.jobs_new = new_count
        run.status = "completed"
        if source_name in DELTA_SOURCES:
            try:
                set_watermarks(db, source_name, ctx)
            except Exception as e:  # noqa: BLE001 — a watermark miss degrades
                # to a re-read next run (overlap absorbs it); never fail the hunt
                logger.warning("[%s] watermark update failed: %s", source_name, e)
        logger.info(
            "[%s] %d found, %d new (delta_since=%s)",
            source_name, len(jobs), new_count, ctx.get("delta_since"),
        )
    except Exception as e:
        db.rollback()
        run.status = "failed"
        run.error = str(e)[:2000]
        logger.error("[%s] scrape failed: %s", source_name, e)
    finally:
        run.finished_at = utc_now()
        db.commit()

    return run


def _job_exists(db: Session, nj: NormalizedJob) -> bool:
    """Dedupe by (source, source_id) then by URL."""
    if nj.source_id:
        exists = (
            db.query(JobPosting.id)
            .filter(JobPosting.source == nj.source, JobPosting.source_id == nj.source_id)
            .first()
        )
        if exists:
            return True
    if nj.url:
        exists = db.query(JobPosting.id).filter(JobPosting.url == nj.url[:1000]).first()
        if exists:
            return True
    # Cross-board duplicate: same normalized title+company already stored
    key = dedupe_key_for(nj.title, nj.company, nj.location)
    exists = db.query(JobPosting.id).filter(JobPosting.dedupe_key == key).first()
    return bool(exists)
# NOTE: autoflush is off, so same-run adds are invisible to these queries.
# We flush each posting immediately to make same-run dedup work.


def _select_sources(ctx: Optional[Dict], sources: Optional[List[str]]) -> List[str]:
    """Which scrapers a hunt runs — the SINGLE source of truth.

    Every branch filters through SCRAPER_REGISTRY so a stale config name
    (a scraper removed while .env still lists it) is a clean skip, not a
    failed ScrapeRun on every hunt. The global-allow-list branch — the
    one every pre-onboarding user (the trial funnel) takes — originally
    skipped this filter; found in review 2026-08-27.
    """
    if sources:
        # Belt to the schema's boundary validation: internal callers
        # (scheduler, tests) aren't schema-checked, so explicit lists
        # filter too — the docstring's 'every branch' is literally true
        return [s for s in sources if s in SCRAPER_REGISTRY]
    if ctx:
        requested = [
            s for s in source_packs.pack_for_country(ctx["country"])
            if s in SCRAPER_REGISTRY
        ]
        # Worldwide remote boards are pointless for a strictly-local
        # user — don't even spend the requests
        if not ctx.get("include_remote"):
            requested = [s for s in requested if s not in source_packs.SHARED_REMOTE_SOURCES]
            if not requested:
                logger.info("Strictly-local user — remote boards skipped")
        return requested
    return [s for s in settings.get_scrape_sources() if s in SCRAPER_REGISTRY]


def run_pipeline(
    sources: Optional[List[str]] = None,
    match: bool = True,
    max_matches: Optional[int] = None,
    backfill: bool = False,
    *,
    user_id,
) -> Dict:
    """
    Run the full pipeline (used by the API and the scheduler).

    backfill=True forces a deep fetch (no published-after cutoff) — the
    onboarding flow uses it so a brand-new user's first hunt reads the
    full history for their queries and municipalities.
    """
    db = SessionLocal()
    try:
        ctx = build_scrape_context(db, user_id=user_id)
        if ctx and backfill:
            ctx["backfill"] = True
        # Per-user pack when onboarded; explicit request or global allow-list otherwise
        requested = _select_sources(ctx, sources)
        scrape_summaries = []
        for source in requested:
            run = scrape_source(db, source, ctx)
            scrape_summaries.append(
                {
                    "source": run.source,
                    "status": run.status,
                    "jobs_found": run.jobs_found,
                    "jobs_new": run.jobs_new,
                    "error": run.error,
                }
            )

        _maintenance_sweeps(db)

        match_summary = None
        if match:
            try:
                # TENANCY LAYER 1: resolve the caller's profile here and
                # inject it — run_matching never resolves identity itself.
                from app.services.cv_service import get_active_profile

                run_profile = get_active_profile(db, user_id=user_id)
                if not run_profile:
                    match_summary = {
                        "status": "skipped",
                        "jobs_considered": 0,
                        "matches_created": 0,
                        "error": "No active profile — upload a CV first",
                    }
                else:
                    match_summary = matcher_service.run_matching(
                        db,
                        limit=max_matches,
                        profile=run_profile,
                        max_seconds=settings.MATCH_TIME_BUDGET_SECONDS,
                        user_id=user_id,
                    )
            except Exception as e:  # noqa: BLE001 — report in summary, never 500 the endpoint
                db.rollback()
                match_summary = {
                    "status": "failed",
                    "jobs_considered": 0,
                    "matches_created": 0,
                    "error": f"{type(e).__name__}: {e}",
                }

        # Top recommendations of this run for immediate display
        top_matches = (
            db.query(MatchResult)
            .join(JobPosting, MatchResult.job_id == JobPosting.id)
            .filter(MatchResult.decision.is_(None), JobPosting.status == "matched")
            .order_by(MatchResult.score.desc())
            .limit(10)
            .all()
        )

        return {
            "scrape": scrape_summaries,
            "match": match_summary,
            "top_matches": [m.id for m in top_matches],
        }
    finally:
        db.close()


def build_union_contexts(db: Session) -> List[Dict]:
    """One scrape context per country, unioned across EVERY onboarded
    user — the scheduled hunt fetches the union of everyone's queries
    and municipalities, so the shared pool stops being shaped by whoever
    triggered the last hunt, and a new user's municipalities join the
    union (forcing a backfill for the new scope key automatically).

    Union semantics: a job is stored if it fits ANY user's scope;
    per-user relevance is (still) decided at matching time.
    """
    profiles = (
        db.query(Profile)
        .filter(Profile.country.isnot(None), Profile.user_id.isnot(None))
        .all()
    )
    by_country: Dict[str, Dict] = {}
    for p in profiles:
        c = (p.country or "").upper()
        g = by_country.setdefault(
            c,
            {
                "country": c,
                "region": None,
                "municipality": None,
                "municipalities": [],
                "queries": [],
                "occupation_codes": [],
                "languages": [],
                "remote_only": False,
                "include_remote": False,
            },
        )
        munis = parse_json_list(getattr(p, "municipalities", None))
        if not munis and p.municipality:
            munis = [p.municipality]
        for m in munis or []:
            if m and m not in g["municipalities"]:
                g["municipalities"].append(m)
        for q in parse_json_list(p.search_queries) or []:
            if q and q not in g["queries"]:
                g["queries"].append(q)
        for pick in parse_json_list(getattr(p, "occupation_codes", None)) or []:
            if isinstance(pick, dict) and pick.get("code"):
                g.setdefault("occupation_codes", [])
                if pick["code"] not in [x["code"] for x in g["occupation_codes"]]:
                    g["occupation_codes"].append(pick)
        for lang in parse_json_list(p.languages) or []:
            if lang and lang not in g["languages"]:
                g["languages"].append(lang)
        if p.include_remote:
            g["include_remote"] = True
    return list(by_country.values())


def scrape_for_context(db: Session, ctx: Dict) -> List[Dict]:
    """Scrape every source in ctx's country pack. The scheduled union
    hunt calls this once per country instead of per user."""
    summaries = []
    for source in _select_sources(ctx, None):
        run = scrape_source(db, source, ctx)
        summaries.append(
            {
                "source": run.source,
                "status": run.status,
                "jobs_found": run.jobs_found,
                "jobs_new": run.jobs_new,
                "error": run.error,
            }
        )
    return summaries


def match_for_user(db: Session, user_id) -> Dict:
    """One user's matching pass (tenancy layer 1: profile resolved by the
    caller-side helper and injected — same rule as run_pipeline)."""
    from app.services.cv_service import get_active_profile

    profile = get_active_profile(db, user_id=user_id)
    if not profile or not profile.cv_text:
        return {"status": "skipped", "error": "No active profile with a CV"}
    try:
        return matcher_service.run_matching(
            db,
            profile=profile,
            max_seconds=settings.MATCH_TIME_BUDGET_SECONDS,
            user_id=user_id,
        )
    except Exception as e:  # noqa: BLE001 — report, never kill the hunt cycle
        db.rollback()
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def _maintenance_sweeps(db: Session) -> None:
    """Queue hygiene: expire stale unmatched postings and auto-pass stale
    pending matches. Runs inside every pipeline run."""
    now = utc_now()

    from sqlalchemy import or_

    stale_cutoff = now - timedelta(days=settings.MAX_POSTING_AGE_DAYS)
    # Postings with a publication date expire by it; date-less postings
    # (NULL published_at never satisfies `<`) expire by when we scraped them.
    stale_new = (
        db.query(JobPosting)
        .filter(
            JobPosting.status == "new",
            or_(
                JobPosting.published_at < stale_cutoff,
                (JobPosting.published_at.is_(None)) & (JobPosting.scraped_at < stale_cutoff),
            ),
        )
        .all()
    )
    for job in stale_new:
        job.status = "dismissed"
    if stale_new:
        logger.info("Sweep: dismissed %d stale unmatched postings", len(stale_new))

    old_cutoff = now - timedelta(days=settings.MATCH_STALE_DAYS)
    old_pending = (
        db.query(MatchResult)
        .filter(MatchResult.decision.is_(None))
        .filter(MatchResult.created_at < old_cutoff)
        .all()
    )
    for m in old_pending:
        m.decision = "rejected"
        m.decided_at = now
        job = db.get(JobPosting, m.job_id)
        if job and job.status == "matched":
            job.status = "rejected"
    if old_pending:
        logger.info("Sweep: auto-passed %d pending matches older than %dd", len(old_pending), settings.MATCH_STALE_DAYS)
    db.commit()
