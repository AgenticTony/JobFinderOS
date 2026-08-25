"""
Matcher service — runs AI matching of scraped jobs against the active profile.

The inverted TalentHive screening loop: TalentHive's demo.py looped candidates
against one job; JobFinderOS loops jobs against one profile.
"""

import logging
import time
from typing import Dict

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import JobPosting, MatchResult, Profile
from app.schemas.common import dump_json_list, parse_json_list
from app.services.ai_service import ai_service_available, get_ai_service
from app.services.cv_service import build_profile_context, get_active_profile
from app.services.language_filter import passes_language_filter

logger = logging.getLogger(__name__)

# Process-wide flag so the UI can poll "is a matching run active?"
_matching_in_progress = False


def is_matching_running() -> bool:
    return _matching_in_progress


def run_matching(
    db: Session,
    limit: int = None,
    profile: Profile = None,
    max_seconds: int = 300,
) -> Dict:
    """
    Match all unmatched jobs against the active profile.

    Args:
        db: database session
        limit: max jobs to process this run (default settings.MAX_JOBS_PER_MATCH_RUN)
        profile: preloaded active profile (optional)
        max_seconds: hard time budget — matching stops and returns partial
            results when exceeded, so pipeline HTTP calls always respond
            within a bounded wait (the frontend times out at 10 minutes).

    Returns:
        Summary dict {status, jobs_considered, matches_created, error}
    """
    if not ai_service_available():
        return {
            "status": "skipped",
            "jobs_considered": 0,
            "matches_created": 0,
            "error": "GLM_API_KEY not set — AI matching disabled",
        }

    if profile is None:
        profile = get_active_profile(db)
    if profile is None or not profile.cv_text:
        return {
            "status": "skipped",
            "jobs_considered": 0,
            "matches_created": 0,
            "skipped_no_profile": True,
            "error": "No active profile — upload a CV first",
        }

    limit = limit or settings.MAX_JOBS_PER_MATCH_RUN

    # Jobs that are new (never matched) — MatchResult.job_id is unique,
    # so a left-join filter gives us exactly the unmatched ones.
    unmatched = (
        db.query(JobPosting)
        .filter(JobPosting.status == "new")
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

    deadline = time.time() + max_seconds
    matches_created = 0
    try:
        for job in unmatched:
            # Cheap pre-filter: hard excludes skip the AI call entirely
            haystack = f"{job.title} {job.company or ''}".lower()
            if any(kw in haystack for kw in exclude_keywords):
                job.status = "dismissed"
                db.add(job)
                continue

            if not job.description:
                # Nothing to assess — dismiss rather than waste an AI call
                job.status = "dismissed"
                db.add(job)
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

            elapsed_ms = int((time.time() - started) * 1000)
            match = MatchResult(
                job_id=job.id,
                score=result["score"],
                tier=result["tier"],
                reasoning=result.get("reasoning"),
                matched_skills=dump_json_list(result.get("matched_skills", [])),
                missing_skills=dump_json_list(result.get("missing_skills", [])),
                transferable_skills=dump_json_list(result.get("transferable_skills", [])),
                recommendation=result.get("recommendation"),
                cover_note=result.get("cover_note"),
                confidence=result.get("confidence"),
                model_used=service.model,
                processing_time_ms=elapsed_ms,
            )
            job.status = "matched"
            db.add(job)
            db.add(match)
            matches_created += 1
            # Commit per job so the frontend's live polling sees matches
            # stream in instead of one dump when the batch ends
            db.commit()

        logger.info("Matching run: %d jobs considered, %d matches created", len(unmatched), matches_created)
        return {
            "status": "completed",
            "jobs_considered": len(unmatched),
            "matches_created": matches_created,
        }
    finally:
        _matching_in_progress = False


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
