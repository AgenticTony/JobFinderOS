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

# PIPE-21: a worker killed mid-run (OOM, deploy, power loss) never
# reaches the terminal-status write and its ScrapeRun stays 'running'
# on dashboards forever. A run this old can no longer be live: the
# codebase's worst-case hunt budget is the worker's hunt-lock TTL
# (CLAIM_TTL_MINUTES = 45 — matching time budget 300s + scrape +
# retries); 2h gives that ~2.7x of headroom before we declare abortion.
STALE_RUN_ABORT_HOURS = 2


def _scope_key(ctx: Dict) -> str:
    from app.services.geo import effective_municipalities, geo_plan

    key = ",".join(sorted(m.lower() for m in effective_municipalities(ctx)))
    # Key on the radius that ACTUALLY applies, not the raw preference:
    # a radius that silently falls back to municipality codes (no
    # centroid for the user's primary town) produces a byte-identical
    # request and must not invalidate the watermark (review finding:
    # spurious deep backfills that return nothing new).
    plan = geo_plan(ctx)
    if plan is not None:
        key += f"|r{plan[2]}"
    return key


def _watermark_queries(ctx: Dict) -> List[str]:
    """Every independent search unit of a fetch: free-text queries (bare,
    for watermark continuity) plus one 'name:CODE' unit per occupation
    concept. A new code has no watermark -> deep backfill for its
    history, exactly like a new query."""
    qs = [str(q).strip() for q in (ctx.get("queries") or []) if str(q).strip()]
    for c in (ctx.get("occupation_codes") or []):
        code = c.get("code") if isinstance(c, dict) else c  # defensive: dicts never again
        if code:
            qs.append(f"name:{code}")
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
    """Record a successful fetch for every (source, query, scope).

    DATA-5: this is a select-then-insert racing a concurrent run
    fetching the same key. The loser's INSERT violates
    uq_scrape_watermark and its failed transaction used to poison the
    session — the caller's next commit (the ScrapeRun's terminal-status
    write) then raised PendingRollbackError, so the hunt 500'd AFTER its
    jobs were committed and the run row stayed 'running'. The collision
    is now handled in-row: rollback, re-select (the winner's row exists
    by then), bump it. A watermark that somehow loses twice just stays
    stale — the next run re-reads with the overlap absorbing it.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import ScrapeWatermark

    scope = _scope_key(ctx)
    now = utc_now()
    queries = _watermark_queries(ctx)
    stamps = {
        r.query: r
        for r in db.query(ScrapeWatermark).filter_by(source=source, scope=scope)
    }

    # Inserts first, updates after: a lost-race rollback expires the
    # loaded rows, so no update dirt may exist yet to be discarded.
    for q in queries:
        if q in stamps:
            continue
        db.add(ScrapeWatermark(source=source, query=q, scope=scope, watermark_at=now))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = (
                db.query(ScrapeWatermark)
                .filter_by(source=source, query=q, scope=scope)
                .first()
            )
            if row is not None:
                stamps[q] = row
            else:
                logger.warning(
                    "[%s] watermark %r lost its insert race twice — next run re-reads",
                    source, q,
                )

    for q in queries:
        if q in stamps:
            stamps[q].watermark_at = now
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


def passes_radius_gate(job: NormalizedJob, ctx: Dict) -> bool:
    """The REDUCED location gate for API-side geo-filtered fetches.

    The API's position+radius filter already decided geography — but it
    says NOTHING about the rest of the location contract, which still
    applies (review finding: the original skip bypassed these too):
      - remote_only users still drop on-site jobs
      - WO-06 country routing still blocks foreign-only locations
    Only the strict municipality clause is waived (the radius exists to
    admit neighbouring kommuner the clause would reject).
    """
    if ctx.get("remote_only") and not job.remote:
        return False
    if blocked_for_user(location_countries(job.location), ctx.get("country")):
        return False
    return True


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
        # so they never consume matching budget. When the source
        # geo-filtered this fetch (jobtech position+radius — decided by
        # the SAME geo_plan the scraper used, never re-derived
        # differently), the REDUCED gate applies instead: the API's
        # distance filter replaces the municipality clause only;
        # remote_only and country routing still hold.
        if ctx:
            from app.services.geo import geo_plan

            geo_filtered = source_name == "jobtech" and geo_plan(ctx) is not None
            gate = passes_radius_gate if geo_filtered else passes_location_filter
            before = len(jobs)
            jobs = [nj for nj in jobs if gate(nj, ctx)]
            if geo_filtered:
                logger.info("[%s] API-side geo filter active (radius) — "
                            "reduced gate applied: %d -> %d jobs", source_name, before, len(jobs))
            elif before != len(jobs):
                logger.info("[%s] location filter: %d -> %d jobs", source_name, before, len(jobs))

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
            if _insert_job_posting(db, nj):
                new_count += 1

        # Terminal state lands BEFORE the watermarks: whatever happens
        # from here (a watermark race, a poisoned session), the run row
        # must already say 'completed' with its counts.
        run.jobs_new = new_count
        run.status = "completed"
        db.commit()
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
        _finalize_run(db, run)

    return run


def _finalize_run(db: Session, run: ScrapeRun) -> None:
    """Force the ScrapeRun row to a terminal, timestamped state even
    when the session is poisoned (DATA-5 belt). The pre-fix finally did
    an unwrapped db.commit(); anything earlier that left the session in
    pending-rollback (the watermark insert race did exactly that) turned
    THIS commit into PendingRollbackError — the hunt 500'd after its
    jobs were committed and the run row stayed 'running' until the 2h
    stale-run sweep. Roll back, retry the terminal write once; if even
    that fails, log it — the sweep is the last resort."""
    try:
        run.finished_at = utc_now()
        db.commit()
    except Exception:
        db.rollback()
        try:
            run.finished_at = utc_now()
            db.commit()
        except Exception as e:  # noqa: BLE001 — nothing left to try
            logger.error(
                "ScrapeRun %s: terminal-state write failed twice (%s) — "
                "the stale-run sweep will abort it", run.id, e,
            )


def _job_values(nj: NormalizedJob) -> Dict:
    """Column values for one ingested posting (shared by the ORM and
    dialect-upsert insert paths)."""
    return dict(
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


def _insert_job_posting(db: Session, nj: NormalizedJob) -> bool:
    """Insert one posting with (source, source_id) conflict-IGNORE.

    PIPE-14b: _job_exists() is the fast path, not a concurrency control
    — a manual hunt racing the cron worker has both runs pass the check
    and both INSERT. The unique index (migration b5d7f9a1c3e5) is the
    backstop; on_conflict_do_nothing turns the loser's insert into a
    no-op instead of an IntegrityError that fails the whole batch (the
    same reconcile-instead-of-abort posture as the match_results insert
    in matcher_service). Both shipped dialects support it: Postgres ON
    CONFLICT DO NOTHING, SQLite INSERT OR IGNORE. Returns True when the
    row was actually stored, False when it lost the race.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    values = _job_values(nj)
    dialect = db.get_bind().dialect.name
    if dialect in ("postgresql", "sqlite"):
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = (
            insert(JobPosting)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["source", "source_id"])
        )
        return db.execute(stmt).rowcount == 1

    # Unshipped dialect: plain ORM insert — the constraint (or the
    # pre-check) still guards, an IntegrityError just surfaces.
    db.add(JobPosting(**values))
    db.flush()
    return True


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
# NOTE: autoflush is off, so same-run adds are invisible to these queries;
# _insert_job_posting emits the INSERT immediately (and the unique
# (source, source_id) index is the cross-run backstop), so same-run and
# cross-run duplicates are both caught.


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

        # Top recommendations of this run for immediate display.
        # P0-1 (beta review): MUST scope to the caller — decision IS NULL +
        # the SHARED job.status == 'matched' flag (set by ANY user's
        # matcher) is not a user boundary. Unscoped, every hunt returned
        # the top-10 GLOBALLY-ranked pending matches: other users'
        # CV-derived reasoning and skills, serialized straight into the
        # hunt response.
        top_matches = (
            db.query(MatchResult)
            .join(JobPosting, MatchResult.job_id == JobPosting.id)
            .filter(
                MatchResult.user_id == user_id,
                MatchResult.decision.is_(None),
                JobPosting.status == "matched",
            )
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
        # PLAIN CODE STRINGS — the exact shape build_scrape_context
        # emits and the scraper/watermark consume. This shipped once
        # appending {"code","label"} dicts: the scraper stringified them
        # into occupation-name={'code': ...} which the live API answers
        # with 200 and zero hits — the recall feature silently no-op'd
        # on every SCHEDULED hunt (review finding, verified live).
        for pick in parse_json_list(getattr(p, "occupation_codes", None)) or []:
            code = pick.get("code") if isinstance(pick, dict) else None
            if code and code not in g["occupation_codes"]:
                g["occupation_codes"].append(str(code))
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
    """Queue hygiene: expire stale unmatched postings, auto-pass stale
    pending matches, and abort ScrapeRuns whose worker died mid-run.
    Runs inside every pipeline run and every scheduled hunt cycle."""
    now = utc_now()

    from sqlalchemy import or_

    # PIPE-21: ScrapeRuns stuck 'running' past the max-run budget are
    # dead workers, not live runs — mark them aborted so dashboards stop
    # showing a phantom in-flight hunt. FRESH running rows are never
    # touched: a concurrent scrape in another process is still live.
    run_cutoff = now - timedelta(hours=STALE_RUN_ABORT_HOURS)
    stale_runs = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "running", ScrapeRun.started_at < run_cutoff)
        .all()
    )
    for r in stale_runs:
        r.status = "aborted"
        r.error = f"aborted: run exceeded {STALE_RUN_ABORT_HOURS}h — worker died mid-run (stale-run sweep)"
        r.finished_at = now
    if stale_runs:
        logger.info("Sweep: aborted %d stale running ScrapeRun(s) older than %dh",
                    len(stale_runs), STALE_RUN_ABORT_HOURS)

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
