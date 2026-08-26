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

# Process-wide flag so the UI can poll "is a matching run active?"
_matching_in_progress = False
# Per-USER locks: one user's 7-minute hunt must never block another's.
# Keyed by user_id; a global lock refused every other caller.
_user_locks: dict = {}
_user_locks_guard = threading.Lock()


def _get_user_lock(user_id):
    with _user_locks_guard:
        if user_id not in _user_locks:
            _user_locks[user_id] = threading.Lock()
        return _user_locks[user_id]


def resolve_samples(samples):
    """The scoring protocol's resolution step — SINGLE source of truth.

    Average the sample scores once; select the payload (reasoning,
    recommendation, confidence, skills, cover_note) from the sample
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


def is_matching_running() -> bool:
    return _matching_in_progress


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
        limit: max jobs to process this run (default settings.MAX_JOBS_PER_MATCH_RUN)
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

    limit = limit or settings.MAX_JOBS_PER_MATCH_RUN

    # Per-user: jobs THIS user has never evaluated (no match row for
    # (user, job)), freshest first. Postings globally dismissed as junk
    # (stale sweep) stay excluded. job.status 'matched' is bookkeeping for
    # "someone evaluated this" — every user still gets their own evaluation.
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
        .limit(limit)
        .all()
    )

    if not unmatched:
        return {"status": "completed", "jobs_considered": 0, "matches_created": 0}

    global _matching_in_progress
    _matching_in_progress = True
    service = get_ai_service()
    profile_context = build_profile_context(profile)
    exclude_keywords = [k.lower() for k in parse_json_list(profile.exclude_keywords)]
    languages = parse_json_list(profile.languages) or []

    # Language gate on the backlog: previously-stored jobs written in a
    # language the user doesn't speak never consume matching budget
    if languages:
        unmatched = [
            j for j in unmatched if passes_language_filter(j.title, j.description, languages)
        ]

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

    deadline = time.time() + max_seconds
    matches_created = 0
    try:
        for job in unmatched:
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

            if time.time() > deadline:
                logger.info(
                    "Matching time budget (%ss) reached after %d matches — remaining jobs stay 'new'",
                    max_seconds,
                    matches_created,
                )
                break

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
            #   skills, cover_note) from the sample CLOSEST to the final
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
            # confidence, skills and cover_note must agree with the
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
                cover_note=best_payload.get("cover_note"),
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
                # Duplicate MatchResult (job reset to 'new', manual job, race):
                # reconcile instead of aborting the whole batch
                db.rollback()
                job.status = "matched"
                db.add(job)
                db.commit()
                logger.warning(
                    "Job %s already had a match — reconciled status, batch continues", job.id
                )
                continue

        logger.info("Matching run: %d jobs considered, %d matches created", len(unmatched), matches_created)
        return {
            "status": "completed",
            "jobs_considered": len(unmatched),
            "matches_created": matches_created,
        }
    finally:
        _matching_in_progress = False


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
