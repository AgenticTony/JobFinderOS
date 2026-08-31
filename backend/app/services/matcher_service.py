"""
Matcher service — runs AI matching of scraped jobs against the active profile.

The inverted TalentHive screening loop: TalentHive's demo.py looped candidates
against one job; JobFinderOS loops jobs against one profile.
"""

import logging
import statistics
import threading
import time
from typing import Dict

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import JobPosting, MatchResult, Profile
from app.schemas.common import dump_json_list, parse_json_list
from app.services.ai_service import AIService, ai_service_available, get_ai_service
from app.services.cv_service import build_profile_context
from app.services.language_filter import passes_language_filter

logger = logging.getLogger(__name__)

# AI-14: per-USER running state. The old single process-global flag told
# EVERY user's dashboard "matching in progress" whenever ANY user
# matched. Readers scope the query: the status route asks about ITS
# caller; a user_id=None ask is the deliberately-global view (worker
# introspection), never a dashboard's.
_matching_users: set = set()
_matching_users_guard = threading.Lock()
# Per-USER locks: one user's 7-minute hunt must never block another's.
# Keyed by user_id; a global lock refused every other caller.
_user_locks: dict = {}
_user_locks_guard = threading.Lock()

# PIPE-19: how many AI evaluations may pass between mid-run checks that
# the user still exists. A GDPR erase (account.py) deletes the user row
# while a scheduled hunt is matching them; without this the matcher fed
# a ghost's CV to GLM for up to MAX_JOBS_PER_MATCH_RUN evaluations, every
# INSERT failing the user_id FK on Postgres. Cheap query, generous
# interval: an erase is rare, an evaluation is ~5-10s.
USER_LIVENESS_CHECK_EVERY = 25


def _mark_matching_started(user_id) -> None:
    with _matching_users_guard:
        _matching_users.add(user_id)


def _mark_matching_done(user_id) -> None:
    with _matching_users_guard:
        _matching_users.discard(user_id)


def _user_exists(db: Session, user_id) -> bool:
    from app.models import User

    return db.query(User.id).filter(User.id == user_id).first() is not None


def _user_gone_summary(user_id, jobs_considered: int, matches_created: int) -> Dict:
    """PIPE-19 abort summary: the account was erased mid-run."""
    logger.warning(
        "Matching aborted: user %s no longer exists (erased mid-run) after "
        "%d match(es) — no further evaluations",
        user_id, matches_created,
    )
    return {
        "status": "aborted",
        "jobs_considered": jobs_considered,
        "matches_created": matches_created,
        "error": "User deleted mid-run — matching aborted",
    }


def _get_user_lock(user_id):
    with _user_locks_guard:
        if user_id not in _user_locks:
            _user_locks[user_id] = threading.Lock()
        return _user_locks[user_id]


def resolve_samples(samples):
    """The scoring protocol's resolution step — SINGLE source of truth.

    Average the sample scores once; select the payload (reasoning,
    recommendation, confidence, skills) from the sample
    CLOSEST to the final mean, because the prose must agree with the
    number the user sees.

    Both this service and scripts/rescore_backlog.py call this function.
    The script previously ran its own copy that kept only the scores and
    discarded the payloads — 241 rows got a current-prompt score next to
    legacy-prompt prose, the F1 defect reproduced at full scale by the
    shadow copy of the protocol.

    Returns (final_score, best_payload_sample).
    """
    final_score = round(statistics.mean(s["score"] for s in samples))
    best_payload = min(samples, key=lambda s: abs(s["score"] - final_score))
    return final_score, best_payload


def needs_another_sample(samples: list) -> bool:
    """The scoring protocol's SAMPLING POLICY — single source of truth.

    How many AI calls a job earns, given what we have so far:

    - nothing yet                -> take the triage sample
    - triage below the dead-band -> stop. Confidently bad; a second
      opinion cannot rescue a 5, and dismissal is the right answer.
    - triage inside [DEADBAND, KEEP_MIN) -> take one more. The outcome is
      uncertain (+/-11 noise on a single sample) and dismissal is
      PERMANENT, so the keep/dismiss call is never made on one sample.
    - running mean >= KEEP_MIN and fewer than 3 -> top up to 3, so a row
      the user will actually see is a 3-sample mean (+/-6, not +/-11).
    - otherwise -> stop.

    This lives here, not in each caller, because it has now diverged three
    times: the re-score script has separately shipped a one-directional
    dismissal derivation (176 rows), a score-without-payload write (241
    rows), and a triage break on KEEP_MIN instead of DEADBAND (62 rows
    dismissed on a single sample — the exact outcome the dead-band exists
    to prevent). Callers own their error handling; the policy is here.
    """
    if not samples:
        return True
    if len(samples) >= 3:
        return False

    triage = samples[0]["score"]
    if len(samples) == 1:
        # Confidently bad never pays for a second call; everything else
        # earns one — the dead-band because the outcome is uncertain, the
        # keeper because a row the user sees must be a 3-sample mean.
        return triage >= settings.MATCH_DEADBAND_MIN_SCORE

    # Two samples so far. If triage already cleared keep-min we are on the
    # keeper path and COMMIT to the full 3 — stopping early here would
    # decide a permanent dismissal on a 2-sample (+/-8) mean, which is the
    # thin evidence the dead-band exists to refuse. If triage was inside
    # the dead-band, a third sample is only worth buying when the pair
    # actually clears the line.
    if triage >= settings.MATCH_KEEP_MIN_SCORE:
        return True
    return statistics.mean(s["score"] for s in samples) >= settings.MATCH_KEEP_MIN_SCORE


def is_matching_running(user_id=None) -> bool:
    """Is a matching run active? user_id scopes the question to ONE user
    (the dashboard contract — AI-14: the caller must not be told another
    user's run is theirs). user_id=None asks globally, for the worker's
    own introspection only."""
    with _matching_users_guard:
        if user_id is not None:
            return user_id in _matching_users
        return bool(_matching_users)


def run_matching(
    db: Session,
    limit: int = None,
    profile: Profile = None,
    max_seconds: int = 300,
    *,
    user_id,
) -> Dict:
    """
    Match all unmatched jobs against the given profile.

    TENANCY LAYER 1: `profile` is the caller-resolved profile for `user_id`.
    Deliberately NOT resolved here — every service that fetched "the"
    profile internally eventually fetched the wrong one (three P0 leaks).
    A missing profile returns the no-profile skip; the caller (route,
    scheduler, pipeline) resolves and passes it.

    Args:
        db: database session
        limit: max AI evaluations this run, applied AFTER the cheap gates
            (default settings.MAX_JOBS_PER_MATCH_RUN — a spend guard, never
            a candidate-selection limit; selection is the oldest-first
            MATCH_CANDIDATE_WINDOW fetch)
        profile: the caller's profile (None -> skipped, never re-resolved)
        max_seconds: hard time budget — matching stops and returns partial
            results when exceeded, so pipeline HTTP calls always respond
            within a bounded wait (the frontend times out at 10 minutes).

    Returns:
        Summary dict {status, jobs_considered, matches_created, error}
    """
    lock = _get_user_lock(user_id)
    if not lock.acquire(blocking=False):
        return {
            "status": "skipped",
            "jobs_considered": 0,
            "matches_created": 0,
            "error": "Your matching run is already in progress",
        }
    try:
        return _run_matching_inner(
            db, limit=limit, profile=profile, max_seconds=max_seconds, user_id=user_id
        )
    finally:
        lock.release()


def ai_scored_today(db: Session, *, user_id) -> int:
    """WO-14 D3: this user's AI EVALUATIONS today (UTC). Cheap-gate
    dismissals (duplicate / excluded_keyword / no_description, and the
    legacy out_of_scope) write match rows but spend no AI — only real
    evaluations count, which is exactly what the cap bounds.

    Review fix (2026-08-31): an AI-scored row is exactly one whose
    dismissed_reason is NULL or 'below_threshold'. The old predicate
    keyed on decision IS NULL, which UNcounted a kept match the moment
    the user approved/rejected it (set_match_decision writes decision,
    never dismissed_reason) — reviewing matches refunded spend slots and
    the cap leaked in proportion to engagement."""

    from sqlalchemy import or_

    from app.core.timeutil import utc_now

    day_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(MatchResult)
        .filter(
            MatchResult.user_id == user_id,
            MatchResult.created_at >= day_start,
            or_(
                MatchResult.dismissed_reason.is_(None),
                MatchResult.dismissed_reason == "below_threshold",
            ),
        )
        .count()
    )


def daily_score_allowance(db: Session, *, user_id) -> int:
    """WO-14 D2: the day-1 allowance is boosted (~2.5×) so the first
    session proves the product; later days settle to the standard cap.

    Review fix (2026-08-31): 'day 1' is the UTC CALENDAR DAY of the
    user's first-ever match row — not a rolling 24h window. The rolling
    window ran on a different clock than ai_scored_today's UTC-midnight
    reset, so a first hunt at 22:00 drew the boosted allowance before
    midnight AND again after it (50 evaluations, not 25)."""
    from sqlalchemy import func

    from app.core.timeutil import utc_now

    first = (
        db.query(func.min(MatchResult.created_at))
        .filter(MatchResult.user_id == user_id)
        .scalar()
    )
    if first is None or first.date() == utc_now().date():
        return settings.TRIAL_DAY1_SCORE_CAP
    return settings.TRIAL_DAILY_SCORE_CAP


def daily_scoring_state(db: Session, *, user_id):
    """Public pair for the route-level synchronous pre-check: the user's
    (scored_today, allowance) so a capped manual run gets an immediate
    clear message instead of a silent background no-op."""
    return ai_scored_today(db, user_id=user_id), daily_score_allowance(db, user_id=user_id)


def daily_cap_message(scored: int, allowance: int) -> str:
    """The user-facing capped sentence — public: the /matches/run route
    surfaces it synchronously, and _daily_cap_summary embeds it in the
    run summary's error field. Kept separate so the route never depends
    on the run-summary shape."""
    return (
        f"Daily scoring limit reached — {scored} of {allowance} jobs "
        f"scored today (trial cap). New jobs score on tomorrow's hunt."
    )


def _daily_cap_summary(scored, allowance, jobs_considered=0, matches_created=0) -> Dict:
    return {
        "status": "daily_cap_reached",
        "jobs_considered": jobs_considered,
        "matches_created": matches_created,
        "error": daily_cap_message(scored, allowance),
    }


def _run_matching_inner(
    db: Session,
    limit: int = None,
    profile: Profile = None,
    max_seconds: int = 300,
    *,
    user_id,
) -> Dict:
    if not ai_service_available():
        return {
            "status": "skipped",
            "jobs_considered": 0,
            "matches_created": 0,
            "error": "GLM_API_KEY not set — AI matching disabled",
        }

    if profile is None or not profile.cv_text:
        return {
            "status": "skipped",
            "jobs_considered": 0,
            "matches_created": 0,
            "skipped_no_profile": True,
            "error": "No profile passed — the caller must resolve and provide it",
        }

    # `limit` caps AI EVALUATIONS per run (spend guard). It must never cap
    # candidate SELECTION: the fetch below is newest-first through a much
    # larger window, and the cheap gates (language, dedupe, exclude
    # keywords, no-description) trim it BEFORE any AI slot is spent — the
    # original design applied this limit to the raw SQL, so plausible ads
    # starved behind junk until the 30-day sweep dismissed them
    # unevaluated (the dream-job starvation bug).
    # WO-14 gap fix: the clamp lives IN the service, so every caller —
    # route, worker, script, the next one anyone writes — inherits the
    # bound. Route schemas stay as defence-in-depth, not the only
    # defence (the Layer-0 principle this codebase applies to tenancy,
    # applied to spend).
    limit = min(
        limit or settings.MAX_JOBS_PER_MATCH_RUN,
        settings.MAX_JOBS_PER_MATCH_RUN,
    )

    # WO-14 D3: the daily trial cap binds on AI EVALUATIONS, here in the
    # service so manual AND scheduled hunts inherit it. Checked before
    # the candidate query: a capped user costs one COUNT, nothing else,
    # and gets a clear message instead of a silent empty queue.
    scored_today = ai_scored_today(db, user_id=user_id)
    allowance = daily_score_allowance(db, user_id=user_id)
    remaining = allowance - scored_today
    if remaining <= 0:
        logger.info(
            "Daily scoring cap: user %s at %d/%d — run returned without "
            "spending", user_id, scored_today, allowance,
        )
        return _daily_cap_summary(scored_today, allowance)
    spend_limit = min(limit, remaining)
    # Whether the DAILY cap (not the per-run cap) is the binding
    # constraint — decides the final summary status so a capped user is
    # told, not left with a silently short run.
    daily_binding = remaining <= limit

    # Per-user: jobs THIS user has never evaluated (no match row for
    # (user, job)). NEWEST FIRST (user decision, 2026-08-30): continuous
    # recruiting means the first strong applicant often wins, so fresh
    # ads get the evaluation slots. Starvation safety does not depend on
    # this order — the post-gate evaluation ceiling plus the time budget
    # drain a scoped user's daily inflow within a run or two, so backlog
    # jobs are delayed, not starved; ads that still expire unevaluated at
    # MAX_POSTING_AGE_DAYS were a month old and past their best
    # application window anyway. Postings globally dismissed as junk stay
    # excluded; job.status 'matched' is bookkeeping for "someone
    # evaluated this" — every user still gets their own evaluation.
    from sqlalchemy import and_

    unmatched = (
        db.query(JobPosting)
        .outerjoin(
            MatchResult,
            and_(MatchResult.job_id == JobPosting.id, MatchResult.user_id == user_id),
        )
        .filter(
            MatchResult.id.is_(None),
            JobPosting.status != "dismissed",
        )
        .order_by(JobPosting.scraped_at.desc())
        .limit(settings.MATCH_CANDIDATE_WINDOW)
        .all()
    )

    if not unmatched:
        return {"status": "completed", "jobs_considered": 0, "matches_created": 0}

    # AI-14: the running mark spans EVERYTHING that can fail from here —
    # the cheap gates below used to sit outside the old flag's
    # try/finally, so a gate error leaked the state (with the global
    # flag that was cosmetic; per-user it would pin the caller's
    # dashboard at "matching" forever).
    _mark_matching_started(user_id)
    try:
        return _run_matching_loop(
            db, unmatched, profile, user_id,
            limit=spend_limit, max_seconds=max_seconds,
            daily_binding=daily_binding,
            scored_today=scored_today, allowance=allowance,
        )
    finally:
        _mark_matching_done(user_id)


def _apply_cheap_gates(db, user_id, unmatched, service, languages):
    """The pre-AI filters: language, PIPE-16 scope, cross-board dedupe,
    fuzzy agency/direct dedupe. Pure trimming + per-user dismissals —
    no evaluation slots spent here."""

    # Language gate on the backlog: previously-stored jobs written in a
    # language the user doesn't speak never consume matching budget
    if languages:
        unmatched = [
            j for j in unmatched if passes_language_filter(j.title, j.description, languages)
        ]

    # PIPE-16 scope gate: the shared pool stores a job when it fits ANY
    # user's scope, so it always held rows this user's own fetch would
    # never have kept — and matching had no location dimension, so they
    # entered EVERY strictly-local user's window and burned evaluation
    # slots before auto-dismissal (live 2026-08-30: a London lead backend
    # engineer's entire first hunt went to remote marketing/intern ads).
    # SKIPPED per-run, for free, BEFORE any AI slot is spent. The
    # predicate is the ingest gate mirrored exactly — same functions,
    # same jobtech/radius selection (app.services.pipeline) — so a job
    # the ingest gate stored FOR this user's scope always still matches.
    # Runs before the dedupe gates so out-of-scope rows never enter the
    # batch's dedupe-key bookkeeping.
    #
    # REG1 (2026-08-31): this used to write TERMINAL out_of_scope
    # dismissal rows — and the candidate query excludes any (user, job)
    # with a match row, so a user who later widened their preferences
    # (re-enabled remote, added a municipality) could NEVER re-see those
    # jobs: preference edits silently shrank the pool forever (live
    # proven: include_remote=False run dismissed a remote job; flipping
    # to True never resurfaced it). Out-of-scope is a property of the
    # CURRENT preferences, not a verdict on the job — so it is a skip,
    # re-evaluated each run at zero cost (the gate is a Python filter;
    # no AI, no DB write). A job re-enters the moment the scope admits it.
    from app.services.pipeline import build_scrape_context, stored_job_in_user_scope

    scope_ctx = build_scrape_context(db, user_id=user_id)
    if scope_ctx:
        out_of_scope = [
            j for j in unmatched if not stored_job_in_user_scope(j, scope_ctx)
        ]
        if out_of_scope:
            logger.info(
                "Scope gate: skipping %d out-of-scope jobs before any AI "
                "spend (re-evaluated next run — no dismissal rows written)",
                len(out_of_scope),
            )
        unmatched = [j for j in unmatched if j not in out_of_scope]

    # Cross-board duplicate gate: if another posting with the same
    # title+company key already has a match, dismiss this copy instead of
    # paying for the same job twice
    from app.core.dedupe import dedupe_key_for

    matched_keys = {
        row[0]
        for row in db.query(JobPosting.dedupe_key)
        .join(MatchResult, MatchResult.job_id == JobPosting.id)
        .filter(JobPosting.dedupe_key.isnot(None), MatchResult.user_id == user_id)
        .all()
    }
    deduped = []
    for j in unmatched:
        key = j.dedupe_key or dedupe_key_for(j.title, j.company, j.location)
        if key in matched_keys:
            _dismiss_for_user(db, user_id, j, "duplicate", service.model)
            deduped.append(j)
        else:
            matched_keys.add(key)  # also guards duplicates within this batch
    if deduped:
        db.commit()
        logger.info("Dedupe gate: dismissed %d cross-board duplicates", len(deduped))
    unmatched = [j for j in unmatched if j not in deduped]

    # Fuzzy second gate (the Pågen incident): the same job as an agency
    # ad ('... till Pågen' via Cabeza) AND a direct ad (PÅGEN AKTIEBOLAG)
    # differs in every exact component. High-precision pair rule from
    # app.core.dedupe.likely_same_job — same municipality + titles
    # differing only by noise tokens (near-identical cores; a one-word
    # role difference like Engineer/Scientist never collapses —
    # DEDUPE-FP) + an employer link + no seniority split. The AGENCY
    # copy is the one dismissed.
    fuzzy_duped = _dismiss_fuzzy_duplicates(db, user_id, unmatched, service.model)
    if fuzzy_duped:
        db.commit()
        logger.info("Fuzzy dedupe gate: dismissed %d agency/direct re-posts",
                    len(fuzzy_duped))
    return [j for j in unmatched if j not in fuzzy_duped]


def _run_matching_loop(
    db, unmatched, profile, user_id, *, limit, max_seconds,
    daily_binding=False, scored_today=0, allowance=0,
) -> Dict:
    """The evaluation loop proper. Called with the per-user running mark
    already set (AI-14) — returns the summary dict, aborts early when
    the user is erased mid-run (PIPE-19)."""
    service = get_ai_service()
    profile_context = build_profile_context(profile)
    exclude_keywords = [k.lower() for k in parse_json_list(profile.exclude_keywords)]
    languages = parse_json_list(profile.languages) or []

    unmatched = _apply_cheap_gates(db, user_id, unmatched, service, languages)

    deadline = time.time() + max_seconds
    matches_created = 0
    evaluated = 0
    for job in unmatched:
        # PIPE-19 liveness check: a GDPR erase mid-run deletes the
        # user row; every later INSERT would fail its FK (and on
        # SQLite, where FKs are off, silently write ghost rows).
        # Checked every USER_LIVENESS_CHECK_EVERY evaluations so a
        # 200-evaluation run stops within ~25 slots of the erase.
        if (
            evaluated
            and evaluated % USER_LIVENESS_CHECK_EVERY == 0
            and not _user_exists(db, user_id)
        ):
            return _user_gone_summary(user_id, len(unmatched), matches_created)

        # Cheap pre-filter: hard excludes skip the AI call entirely
        haystack = f"{job.title} {job.company or ''}".lower()
        if any(kw in haystack for kw in exclude_keywords):
            # THIS user's exclude list — never the shared job row, or one
            # user's "senior" filter hides senior roles from everyone
            _dismiss_for_user(db, user_id, job, "excluded_keyword", service.model)
            db.commit()
            continue

        if not job.description:
            # Nothing to assess — dismiss rather than waste an AI call
            _dismiss_for_user(db, user_id, job, "no_description", service.model)
            db.commit()
            continue

        # The evaluation cap counts AI SPEND, not candidates: the
        # free gates above must never consume a slot.
        if evaluated >= limit:
            logger.info(
                "Evaluation cap (%d) reached after %d matches — "
                "remaining candidates stay queued for the next run",
                limit,
                matches_created,
            )
            break

        if time.time() > deadline:
            logger.info(
                "Matching time budget (%ss) reached after %d matches — remaining jobs stay 'new'",
                max_seconds,
                matches_created,
            )
            break

        evaluated += 1
        started = time.time()
        try:
            result = service.match_job(
                profile_context=profile_context,
                cv_text=profile.cv_text,
                job_description=_job_text(job),
            )
        except Exception as e:  # noqa: BLE001 — any AI failure skips the job, never kills the run
            logger.error("Match failed for job %s (%s): %s", job.id, type(e).__name__, e)
            continue  # leave as 'new' for the next run

        # SCORING PROTOCOL (review-hardened):
        # - Collect full result dicts (not just scores) from each sample
        # - Average scores once
        # - Select the PAYLOAD (reasoning, recommendation, confidence,
        #   skills) from the sample CLOSEST to the final
        #   mean — prose must agree with the number the user sees
        # - Check keep-min on the final averaged value
        # - A dead-band sampling failure leaves the job 'new' for retry
        #   (one ±11 sample is never enough for permanent dismissal)
        #
        # Cost: 41% of backlog rows clear keep-min → ~2.06× the single-
        # sample cost. The embeddings prefilter (ROADMAP) is the lever.
        samples = [result]  # full result dicts, not just scores

        # How many samples this job earns comes from the SHARED policy
        # (needs_another_sample) — dead-band second opinion, then top-up
        # to 3 for anything heading into the queue. The re-score script
        # calls the same function; duplicating the thresholds is what
        # dismissed 62 rows on a single sample.
        sampling_failed = False
        while needs_another_sample(samples):
            try:
                samples.append(
                    service.match_job(
                        profile_context=profile_context,
                        cv_text=profile.cv_text,
                        job_description=_job_text(job),
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Re-sample failed for job %s: %s", job.id, e)
                # A failure while still inside the dead-band leaves one
                # ±11 sample in the uncertain zone — NOT enough for a
                # permanent dismissal. Leave the job 'new' for retry,
                # matching the convention for unparseable responses.
                # Above the band we already have enough to store.
                sampling_failed = len(samples) < 2 and (
                    samples[0]["score"] < settings.MATCH_KEEP_MIN_SCORE
                )
                break
        if sampling_failed:
            continue

        # Average once; F1: the payload comes from the sample closest to
        # the mean — via resolve_samples, the shared protocol the
        # re-score script also calls. The prose, recommendation,
        # confidence and skills must agree with the
        # displayed number — a score of 40 paired with
        # recommendation='skip' and reasoning='barely match' (from a
        # sample that scored 26) is incoherent and breaks MatchCard's
        # 'AI says: apply' chip and the recommendation filter.
        final_score, best_payload = resolve_samples(samples)
        final_tier = AIService._tier_for_score(final_score)
        if len(samples) > 1:
            logger.info(
                "Scored job %s: scores=%s -> %d (%s), payload from sample scoring %d",
                job.id,
                sorted(s["score"] for s in samples),
                final_score, final_tier, best_payload["score"],
            )

        elapsed_ms = int((time.time() - started) * 1000)

        # Keep-min check on the FINAL averaged value
        if final_score < settings.MATCH_KEEP_MIN_SCORE:
            auto_pass = MatchResult(
                user_id=user_id,
                job_id=job.id,
                score=final_score,
                tier=final_tier,
                reasoning="Auto-passed: below the score threshold for your CV.",
                matched_skills=dump_json_list(best_payload.get("matched_skills", [])),
                missing_skills=dump_json_list(best_payload.get("missing_skills", [])),
                transferable_skills=dump_json_list(best_payload.get("transferable_skills", [])),
                recommendation="skip",
                confidence=best_payload.get("confidence"),
                model_used=service.model,
                processing_time_ms=elapsed_ms,
                decision="rejected",
                dismissed_reason="below_threshold",
                prompt_version=AIService.matching_prompt_version(),
            )
            db.add(auto_pass)
            try:
                db.commit()
            except Exception:
                db.rollback()
                # PIPE-19 belt: an erase inside the check window shows
                # up here as the user_id FK failing on Postgres.
                if not _user_exists(db, user_id):
                    return _user_gone_summary(
                        user_id, len(unmatched), matches_created
                    )
            continue

        match = MatchResult(
            user_id=user_id,
            job_id=job.id,
            score=final_score,
            tier=final_tier,
            reasoning=best_payload.get("reasoning"),
            matched_skills=dump_json_list(best_payload.get("matched_skills", [])),
            missing_skills=dump_json_list(best_payload.get("missing_skills", [])),
            transferable_skills=dump_json_list(best_payload.get("transferable_skills", [])),
            recommendation=best_payload.get("recommendation"),
            confidence=best_payload.get("confidence"),
            model_used=service.model,
            processing_time_ms=elapsed_ms,
            prompt_version=AIService.matching_prompt_version(),
        )
        job.status = "matched"
        db.add(job)
        db.add(match)
        from sqlalchemy.exc import IntegrityError

        try:
            db.commit()  # per-job commit, contained
            matches_created += 1
        except IntegrityError:
            db.rollback()
            # PIPE-19: an insert failing because the USER row is gone
            # (a GDPR erase inside the liveness-check window) must
            # abort — every further evaluation would fail the same
            # FK after spending the GLM call. Verified against the
            # live row, not the exception text: an IntegrityError
            # pointing at anything else (a duplicate MatchResult, a
            # job deleted mid-run) takes the reconcile path below.
            if not _user_exists(db, user_id):
                return _user_gone_summary(user_id, len(unmatched), matches_created)
            # Duplicate MatchResult (job reset to 'new', manual job, race):
            # reconcile instead of aborting the whole batch
            job.status = "matched"
            db.add(job)
            db.commit()
            logger.warning(
                "Job %s already had a match — reconciled status, batch continues", job.id
            )
            continue

    logger.info("Matching run: %d jobs considered, %d matches created", len(unmatched), matches_created)
    if daily_binding and evaluated >= limit:
        # The DAILY trial cap was the binding constraint — say so, with
        # this run's real counts (jobs may still have matched today).
        return _daily_cap_summary(
            scored_today + evaluated, allowance,
            jobs_considered=len(unmatched), matches_created=matches_created,
        )
    return {
        "status": "completed",
        "jobs_considered": len(unmatched),
        "matches_created": matches_created,
    }



def _dismiss_for_user(db, user_id, job: JobPosting, reason: str, model: str) -> None:
    """Record that THIS user's pipeline dropped this job.

    Dismissal is per-user state and must never touch job_postings.status:
    the job row is shared, so writing one user's exclude-keyword or
    duplicate decision onto it removed the posting from every other user's
    queue. The row also stops re-evaluation (the candidate query joins on
    (user_id, job_id)) and keeps an audit trail of why.
    """
    db.add(
        MatchResult(
            user_id=user_id,
            job_id=job.id,
            score=0,
            tier="poor_match",
            recommendation="skip",
            reasoning=f"Not shown: {reason.replace('_', ' ')}.",
            dismissed_reason=reason,
            decision="rejected",
            model_used=model,
            prompt_version=AIService.matching_prompt_version(),
        )
    )


_AGENCY_MARKERS = ("rekryter", "konsult", "staffing", "recruit", "bemanning")


def _is_agency_posting(job) -> bool:
    return any(m in (job.company or "").lower() for m in _AGENCY_MARKERS)


def _dismiss_fuzzy_duplicates(db, user_id, unmatched, model: str):
    """Pågen-pattern gate: collapse the same job posted directly AND via an
    agency. Compares each candidate against (a) the user's undecided
    matches from the last 14 days and (b) earlier candidates in this
    batch; the AGENCY copy is dismissed; if both sides look direct (or
    both agency), the LATER one (the candidate) is dismissed.
    """
    from datetime import timedelta

    from app.core.dedupe import likely_same_job
    from app.core.timeutil import utc_now

    cutoff = utc_now() - timedelta(days=14)
    existing = (
        db.query(MatchResult, JobPosting)
        .join(JobPosting, MatchResult.job_id == JobPosting.id)
        .filter(
            MatchResult.user_id == user_id,
            MatchResult.decision.is_(None),
            MatchResult.dismissed_reason.is_(None),
            MatchResult.created_at >= cutoff,
        )
        .all()
    )

    def same(a, b) -> bool:
        return likely_same_job(
            title_a=a.title, company_a=a.company, location_a=a.location,
            title_b=b.title, company_b=b.company, location_b=b.location,
        )

    dismissed = []
    flipped = 0
    kept_batch = []
    for job in unmatched:
        # against existing undecided matches
        flip = None
        drop = False
        for match_row, other in existing:
            if other.id == job.id:
                continue
            if same(job, other):
                if _is_agency_posting(other) and not _is_agency_posting(job):
                    # the stored copy is the agency re-post: dismiss IT,
                    # keep this direct copy
                    flip = match_row
                else:
                    drop = True
                break
        if flip is not None:
            flip.dismissed_reason = "duplicate"
            flip.decision = "rejected"
            flip.reasoning = "Not shown: duplicate (agency re-post of a newer direct ad)."
            flipped += 1
            kept_batch.append(job)
            continue
        if drop:
            _dismiss_for_user(db, user_id, job, "duplicate", model)
            dismissed.append(job)
            continue
        # against earlier candidates in this batch
        dupe_of = next((k for k in kept_batch if same(job, k)), None)
        if dupe_of is not None:
            if _is_agency_posting(job) and not _is_agency_posting(dupe_of):
                _dismiss_for_user(db, user_id, job, "duplicate", model)
                dismissed.append(job)
            else:
                _dismiss_for_user(db, user_id, dupe_of, "duplicate", model)
                dismissed.append(dupe_of)
                kept_batch = [k for k in kept_batch if k.id != dupe_of.id]
                kept_batch.append(job)
            continue
        kept_batch.append(job)

    # Commit OUR OWN changes (review catch): the caller commits only when
    # new dismissals are returned, so a FLIP-ONLY outcome (stored agency
    # copy dismissed for a newer direct ad, nothing new dropped) would
    # never commit — the queue kept both copies.
    if dismissed or flipped:
        db.commit()
    return dismissed


def _job_text(job: JobPosting) -> str:
    """Compose the job posting text sent to the AI."""
    parts = [f"Title: {job.title}"]
    if job.company:
        parts.append(f"Company: {job.company}")
    if job.location:
        parts.append(f"Location: {job.location}")
    if job.remote:
        parts.append("Remote: yes")
    if job.employment_type:
        parts.append(f"Employment type: {job.employment_type}")
    if job.salary:
        parts.append(f"Salary: {job.salary}")
    tags = parse_json_list(job.tags)
    if tags:
        parts.append(f"Tags: {', '.join(tags[:20])}")
    parts.append(f"\nDescription:\n{job.description}")
    return "\n".join(parts)
