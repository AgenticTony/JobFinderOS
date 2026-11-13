"""WO-31: what each ai_jobs kind does when the drainer runs it.

Every handler re-resolves its subject from the job row (never from the
request that enqueued it) and re-checks that the subject is still the
job's target — the draft still exists, belongs to the job's user and is
still waiting; the user still exists and is active (GDPR erasure
tombstones the users row). Anything else is 'dropped': no result, no
write.

Handlers STAGE writes and never commit; the drainer commits them in the
same transaction as the job's lease-token CAS (exactly-once result).
Load errors (AILoadError) propagate untouched — the drainer re-queues.

The AI service is looked up through the owning service module at call
time (draft_service.get_ai_service, cv_service.get_ai_service) — the
same seams the suite already patches.
"""

import logging

from sqlalchemy.orm import Session

from app.core.database import release_transaction
from app.models import AIJob, ApplicationDraft, JobPosting, Profile, User
from app.services import ai_queue
from app.services.ai_queue import Handler, Outcome, Requeue

logger = logging.getLogger(__name__)


def _active_user(db: Session, user_id) -> bool:
    user = db.get(User, user_id)
    return user is not None and bool(user.is_active)


def _owned_draft(db: Session, job: AIJob):
    draft = db.get(ApplicationDraft, job.subject_id)
    if draft is None or draft.user_id != job.user_id:
        return None
    return draft


def _owned_profile(db: Session, job: AIJob):
    profile = db.get(Profile, job.subject_id)
    if profile is None or profile.user_id != job.user_id:
        return None
    return profile


# ------------------------------------------------------------------
# tailor — Prepare / Try again
# ------------------------------------------------------------------


def run_tailor(db: Session, job: AIJob) -> Outcome:
    from app.services import draft_service
    from app.services.cv_service import get_active_profile

    draft = _owned_draft(db, job)
    if draft is None or draft.status not in ("queued", "drafting") or not _active_user(db, job.user_id):
        return Outcome("dropped")
    profile = get_active_profile(db, user_id=job.user_id)
    if not profile or not profile.cv_text:
        draft.status = "failed"
        draft.error = "Upload your CV before preparing applications"
        db.add(draft)
        return Outcome("failed", error=draft.error)
    if not draft_service.ai_service_available():
        draft.status = "failed"
        draft.error = "The AI service is not configured — nothing was generated."
        db.add(draft)
        return Outcome("failed", error=draft.error)
    posting = draft.job or db.get(JobPosting, draft.job_id)
    # Every read is done: end the read transaction before the provider
    # calls (tailor + judge + regenerations take minutes). Results are
    # staged in memory (autoflush off) and written once, at the lease CAS.
    release_transaction(db)
    # The package is built against the CV on file NOW — snapshot it in the
    # result transaction (P1-5b), not at enqueue time.
    draft_service._snapshot_cv(draft, profile)
    draft_service._tailor_into(db, draft, posting, profile)
    # A guard block is a completed job (its verdict is the result); the
    # job fails only for non-load errors below.
    return Outcome("done")


def fail_tailor(db: Session, job: AIJob, exc: Exception) -> Outcome:
    error = f"Tailoring failed: {exc}"
    draft = _owned_draft(db, job)
    if draft is not None and draft.status in ("queued", "drafting"):
        draft.status = "failed"
        draft.error = error
        db.add(draft)
    return Outcome("failed", error=error)


# ------------------------------------------------------------------
# recheck — Check again, and attest's final check
# ------------------------------------------------------------------


def run_recheck(db: Session, job: AIJob) -> Outcome:
    from app.services import draft_service
    from app.services.cv_service import get_active_profile

    draft = _owned_draft(db, job)
    if draft is None or not _active_user(db, job.user_id):
        return Outcome("dropped")
    try:
        draft_service._validate_recheck(draft)
    except draft_service.DraftError:
        # Attested to ready, regenerated or submitted while it waited.
        return Outcome("dropped")
    profile = get_active_profile(db, user_id=job.user_id)
    if not profile or not profile.cv_text:
        return Outcome("failed", error="No CV on file for this account")
    posting = draft.job or db.get(JobPosting, draft.job_id)
    release_transaction(db)  # no transaction open across the judge call
    draft_service._recheck_into(db, draft, profile, job=posting)
    return Outcome("done")


def fail_recheck(db: Session, job: AIJob, exc: Exception) -> Outcome:
    error = (f"Re-check failed: {exc}. Your text is saved — press "
             "Check again.")
    draft = _owned_draft(db, job)
    if draft is not None and draft.status == "failed":
        draft.error = error
        db.add(draft)
    return Outcome("failed", error=error)


# ------------------------------------------------------------------
# extract — CV upload's profile fields
# ------------------------------------------------------------------

EXTRACT_EMPTY_ERROR = (
    "We couldn't read your details from this CV automatically. Upload a "
    "text-based PDF or DOCX, or fill in your profile by hand."
)


def run_extract(db: Session, job: AIJob) -> Outcome:
    from app.services import cv_service

    profile = _owned_profile(db, job)
    if profile is None or not _active_user(db, job.user_id):
        return Outcome("dropped")
    text = profile.cv_text or ""
    if not text.strip():
        return Outcome("failed", error=EXTRACT_EMPTY_ERROR)
    before = cv_service.cv_sha256(text)
    release_transaction(db)  # no transaction open across the provider call
    extracted = cv_service.get_ai_service().extract_profile(text)
    db.refresh(profile)
    if cv_service.cv_sha256(profile.cv_text) != before:
        # Re-uploaded while the call ran: these fields describe the OLD
        # CV. Run again on the new text (no load penalty).
        raise Requeue()
    if not extracted or not cv_service._extraction_complete(extracted):
        # AI-11: an empty extraction never wipes existing fields. The
        # 2026-10-30 audit extends it: a reply that parses but omits keys
        # is the same format failure — applying it would null every
        # omitted field on a previously-good profile.
        return Outcome("failed", error=EXTRACT_EMPTY_ERROR)
    cv_service._apply_extraction(profile, extracted)
    db.add(profile)
    user_id = job.user_id

    def sync_name() -> None:
        from app.core.database import SessionLocal

        sdb = SessionLocal()
        try:
            p = sdb.query(Profile).filter(Profile.user_id == user_id).first()
            if p is not None:
                cv_service._sync_contact_first_name(sdb, p, user_id=user_id)
        finally:
            sdb.close()

    return Outcome("done", after_commit=sync_name)


def fail_extract(db: Session, job: AIJob, exc: Exception) -> Outcome:
    # A ValueError names the real cause (truncation, 2026-10-30 audit) —
    # surface it instead of the generic "upload it again" advice that
    # misdirects when the file was fine.
    detail = f" ({exc})" if isinstance(exc, ValueError) else ""
    return Outcome(
        "failed",
        error=(f"Reading your CV failed{detail}. Your CV is saved — upload "
               "it again to retry, or fill in your profile by hand."),
    )


# ------------------------------------------------------------------
# suggest — the wizard's job-titles step
# ------------------------------------------------------------------


def run_suggest(db: Session, job: AIJob) -> Outcome:
    import json

    from app.services import cv_service

    profile = _owned_profile(db, job)
    if profile is None or not _active_user(db, job.user_id):
        return Outcome("dropped")
    if not profile.cv_text:
        return Outcome("failed", error="Upload a CV first")
    params = json.loads(job.params or "{}")
    country = params.get("country", "")
    mode = params.get("mode", "field")
    cv_text = profile.cv_text
    release_transaction(db)  # no transaction open across the provider call
    result = cv_service.get_ai_service().suggest_search_queries(cv_text, country, mode)
    return Outcome("done", result={"country": country, "mode": mode, **result})


def fail_suggest(db: Session, job: AIJob, exc: Exception) -> Outcome:
    return Outcome(
        "failed",
        error="We couldn't suggest job titles this time — try again, or add your own.",
    )


ai_queue.register("tailor", Handler(run=run_tailor, fail=fail_tailor))
ai_queue.register("recheck", Handler(run=run_recheck, fail=fail_recheck))
ai_queue.register("extract", Handler(run=run_extract, fail=fail_extract))
ai_queue.register("suggest", Handler(run=run_suggest, fail=fail_suggest))
