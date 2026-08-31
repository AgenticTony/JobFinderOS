"""
Draft service — the application-preparation stage between match approval
and submission.

Flow: user approves a match -> create_draft_for_job() runs AI tailoring ->
user reviews/edits the package in the UI -> submit_draft() sends it
(email with tailored PDFs, or browser/manual queue).

INVARIANT — THE ORIGINAL CV IS NEVER TOUCHED:
profile.cv_text and the stored PDF are read-only inputs here. Tailoring
always writes to THIS draft's cover_letter / tailored_cv columns, one row
per job. The original CV stays the permanent reference for every future
match and every future draft, and is attached unmodified alongside the
tailored documents when sending — the DRAFT'S SNAPSHOT of the CV path
(P1-5b), not the profile's current path, so a re-upload mid-review can
never pair old-tailored documents with a brand-new CV.
"""

import hashlib
import logging
from typing import List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import utc_now
from app.models import Application, ApplicationDraft, JobPosting, MatchResult, Profile
from app.services import pdf_service
from app.services.ai_service import ai_service_available, get_ai_service
from app.services.cv_service import build_profile_context
from app.services.matcher_service import _job_text

logger = logging.getLogger(__name__)

# WO-01 Layer C: after this many regeneration attempts a surviving
# high-confidence finding is the model REPEATEDLY asserting something
# the CV does not support — block, don't retry again.
MAX_FABRICATION_RETRIES = 2


def get_ai_service_with_judge():
    """The AI service when the production judge is enabled; None when
    FABRICATION_JUDGE=off (emergency cost lever — Layer A still guards)."""
    if getattr(settings, "FABRICATION_JUDGE", "on") == "off":
        return None
    return get_ai_service()

DRAFT_DIR = "uploads/drafts"


class DraftError(Exception):
    """Raised when a draft cannot be created or submitted."""


class DraftConflictError(DraftError):
    """Another dispatch owns the draft's submission ('sending' claim held,
    or an application already exists). The caller's package is FINE — the
    route maps this to 409, not 400."""


def get_draft(db: Session, draft_id: int) -> Optional[ApplicationDraft]:
    return db.query(ApplicationDraft).filter(ApplicationDraft.id == draft_id).first()


def list_drafts(db: Session, limit: int = 100, *, user_id) -> List[ApplicationDraft]:
    return (
        db.query(ApplicationDraft)
        .filter(ApplicationDraft.user_id == user_id)
        .order_by(ApplicationDraft.updated_at.desc())
        .limit(limit)
        .all()
    )


def create_draft_for_job(
    db: Session,
    job: JobPosting,
    *,
    profile: Profile,
    force: bool = False,
    user_id,
) -> ApplicationDraft:
    """
    Generate (or regenerate) the tailored application package for an approved job.

    TENANCY LAYER 1: the profile arrives as a required parameter — this
    function never resolves identity itself. The three cross-tenant P0
    leaks all came from services fetching "the" profile internally; the
    route resolves the caller's profile and hands it in.

    The AI tailoring call is synchronous here — the API endpoint wraps this in
    a threadpool. Typical latency ~5-20s on glm-4.6 with thinking disabled.
    """
    existing = (
        db.query(ApplicationDraft)
        .filter(
            ApplicationDraft.job_id == job.id,
            ApplicationDraft.user_id == user_id,
            ApplicationDraft.status != "submitted",
        )
        .first()
    )
    if existing and existing.status == "ready" and not force:
        return existing  # already prepared — user should review, not regenerate

    if not profile or not profile.cv_text:
        raise DraftError("Upload your CV before preparing applications")

    if not ai_service_available():
        raise DraftError("GLM_API_KEY not configured — cannot tailor applications")

    match: Optional[MatchResult] = (
        db.query(MatchResult)
        .filter(MatchResult.job_id == job.id, MatchResult.user_id == user_id)
        .first()
    )

    # Reuse the existing row when regenerating a failed draft
    draft = existing or ApplicationDraft(
        user_id=user_id, job_id=job.id, match_id=match.id if match else None
    )
    draft.status = "drafting"
    draft.error = None
    # P1-5b: snapshot the CV reference this tailoring runs against. The
    # profile's path moves on re-upload; without the snapshot the send
    # path cannot know which CV the package was built from, and a
    # CV-old-tailored package emails CV-new as its "original CV".
    # Refreshed on every (re)generation — the package now being built IS
    # against the current CV. The early-return reuse of an untouched
    # 'ready' draft above keeps its original snapshot (package unchanged).
    draft.cv_file_path = profile.cv_file_path
    draft.cv_hash = hashlib.sha256(
        (profile.cv_text or "").encode("utf-8")
    ).hexdigest()
    db.add(draft)
    db.commit()

    try:
        # FABRICATION GUARD (WO-01 Layer C): every tailored output is
        # checked against the source CV before anything reaches the
        # review screen. High-confidence findings REGENERATE (never strip
        # — a silently mutilated document is worse than a blocked one),
        # up to MAX_FABRICATION_RETRIES; a survivor BLOCKS the draft and
        # names the untraceable claim. Advisory findings persist for the
        # review UI and never auto-act.
        from app.services.fabrication import (
            findings_as_json,
            split_tiers,
            unsupported_claims,
        )

        correction: Optional[str] = None
        retries = 0
        while True:
            kwargs = {}
            if correction:
                kwargs["correction"] = correction
            result = get_ai_service().tailor_application(
                profile_context=build_profile_context(profile),
                cv_text=profile.cv_text,
                job_description=_job_text(job),
                **kwargs,
            )
            draft.cover_letter = result["cover_letter"]
            draft.tailored_cv = result["tailored_cv"]
            from app.schemas.common import dump_json_list

            draft.changes_summary = dump_json_list(result["changes_summary"])

            # The guard's source of truth must match the MODEL'S input
            # (WO-01 review): the model saw cv_text + profile_context, so
            # verifying against the CV alone flags facts we ourselves fed
            # it via the summary. After the lossy-years fix, the summary
            # is safe to include — aligning before that fix would have
            # laundered the bad summary into 'supported'.
            model_input = (f"{profile.cv_text}\n"
                           f"{build_profile_context(profile, include_derived=False)}")
            findings = unsupported_claims(
                model_input,
                f"{result.get('cover_letter', '')}\n{result.get('tailored_cv', '')}",
                allowed_names=[n for n in (job.company, job.title) if n],
            )
            high, advisory = split_tiers(findings)

            # WO-02: the production judge — semantic evidence Layer A
            # cannot see. Runs after Layer A is clean (no point judging a
            # document Layer A already rejected); a finding joins the
            # high-confidence path: regenerate with the claim named, block
            # after MAX retries. Kill switch: FABRICATION_JUDGE=off.
            if not high and get_ai_service_with_judge():
                judge_claims = get_ai_service_with_judge().judge_fabrication(
                    model_input,
                    f"{result.get('cover_letter', '')}\n{result.get('tailored_cv', '')}",
                )
                if judge_claims:
                    high = [
                        type("JudgeClaim", (), {
                            "value": c.get("claim", "?"),
                            "kind": "judge", "tier": "high",
                            "context": c.get("why", ""),
                        })() for c in judge_claims
                    ]
                    advisory = advisory  # unchanged

            if not high:
                from app.schemas.common import dump_json_list as _dumps

                draft.fabrication_findings = _dumps(findings_as_json(advisory))
                draft.fabrication_retries = retries
                draft.fabrication_blocked = False
                draft.status = "ready"
                db.add(draft)
                db.commit()
                db.refresh(draft)
                logger.info(
                    "Draft %s prepared for job %s (fabrication retries=%d, "
                    "advisory=%d)", draft.id, job.id, retries, len(advisory),
                )
                return draft

            if retries >= MAX_FABRICATION_RETRIES:
                draft.fabrication_findings = None  # blocked — nothing to review
                draft.fabrication_retries = retries
                draft.fabrication_blocked = True
                draft.status = "failed"
                named = ", ".join(
                    sorted({c.value.split("|")[0] for c in high})
                )
                draft.error = (
                    "Blocked by the fabrication guard: the tailored document "
                    f"repeatedly asserts claims your CV does not support "
                    f"({named}). Edit your CV to include them, or regenerate."
                )
                db.add(draft)
                db.commit()
                logger.warning(
                    "Draft %s BLOCKED after %d retries: %s",
                    draft.id, retries, named,
                )
                return draft

            retries += 1
            correction = (
                "Your previous output contained claims that cannot be traced "
                "to your CV and may be fabrications: "
                + ", ".join(sorted({c.value.split("|")[0] for c in high}))
                + ". Regenerate the documents WITHOUT these claims — every "
                "employer, date, credential and metric must exist in the "
                "original CV."
            )
            logger.info(
                "Draft %s: %d high-confidence fabrication findings — "
                "regenerating (attempt %d)", draft.id, len(high), retries,
            )
    except Exception as e:  # noqa: BLE001 — surface the failure on the draft row
        db.rollback()
        draft.status = "failed"
        draft.error = f"Tailoring failed: {type(e).__name__}: {e}"
        db.add(draft)
        db.commit()
        db.refresh(draft)
        logger.error("Draft tailoring failed for job %s: %s", job.id, e)
        return draft


def save_draft_edits(
    db: Session,
    draft: ApplicationDraft,
    cover_letter: Optional[str] = None,
    tailored_cv: Optional[str] = None,
) -> ApplicationDraft:
    """Persist the user's manual edits to the package."""
    if draft.status == "submitted":
        raise DraftError("This application was already submitted")
    if cover_letter is not None:
        draft.cover_letter = cover_letter
    if tailored_cv is not None:
        draft.tailored_cv = tailored_cv
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def submit_draft(
    db: Session,
    draft: ApplicationDraft,
    method: str,
    profile: Profile,
    *,
    user_id,
) -> Application:
    """
    Submit the reviewed package.

    - email:   sends cover letter + tailored CV PDFs (plus the original CV) via
               Resend when the job published an application email
    - browser / manual: queues as manual_pending with the apply URL; the UI
               opens the posting with the package ready to paste

    SUBMIT (double-send window): the ready->sending claim below is a
    conditional UPDATE — the rowcount is the verdict, the same atomic
    pattern as claim_hunt. Two rapid submits both read 'ready', but only
    ONE transition wins; the loser gets DraftConflictError (409 at the
    route) BEFORE any email is dispatched. The partial unique index on
    applications(draft_id) is the DB backstop for anything the claim
    cannot see.
    """
    if draft.status == "submitted":
        raise DraftConflictError("This application was already submitted")
    if draft.status == "sending":
        raise DraftConflictError("This application is already being submitted")
    if draft.status != "ready" or not draft.cover_letter:
        raise DraftError("Draft is not ready — prepare or fix it first")

    # Atomic claim: exactly one caller moves this row ready -> sending.
    # (In-memory status above is only a fast path — it can be stale in the
    # racing session; the UPDATE's WHERE clause cannot.)
    from sqlalchemy import update

    claimed = db.execute(
        update(ApplicationDraft)
        .where(ApplicationDraft.id == draft.id, ApplicationDraft.status == "ready")
        .values(status="sending")
    )
    db.commit()
    if claimed.rowcount != 1:
        db.rollback()
        db.refresh(draft)
        if draft.status == "submitted":
            # the other dispatch already finished
            raise DraftConflictError("This application was already submitted")
        raise DraftConflictError("This application is already being submitted")

    job: JobPosting = draft.job
    if job is None:
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_id).first()
    # TENANCY LAYER 1: profile is a required parameter — the outbound
    # identity (sender name, attached CVs) comes from exactly the profile
    # the route resolved for the caller. This function never looks one up;
    # the optional-lookup version here is where a wrong user's CV got
    # emailed to an employer.

    target_email = job.application_email
    apply_url = job.application_url or job.url
    if method == "email" and not target_email:
        # No published email — degrade to the browser flow
        method = "browser"

    applicant = profile.full_name if profile else None
    subject = f"Application: {job.title}" + (f" — {applicant}" if applicant else "")

    # One application row per draft (unique index). A FAILED application
    # leaves the draft 'ready'; resubmitting REUSES that row — a fresh
    # insert would trip the constraint, and a duplicate history for one
    # reviewed package is a lie anyway.
    application = (
        db.query(Application).filter(Application.draft_id == draft.id).first()
    )
    if application is not None and application.status != "failed":
        _release_send_claim(db, draft, status="submitted")
        raise DraftConflictError("This application was already submitted")
    if application is None:
        application = Application(
            user_id=user_id if user_id is not None else draft.user_id,
            job_id=job.id,
            match_id=draft.match_id,
            draft_id=draft.id,
        )
        db.add(application)
    application.method = method
    application.status = "queued"
    application.error = None
    application.subject = subject
    application.body = draft.cover_letter
    application.target_email = target_email
    application.apply_url = apply_url
    try:
        db.flush()
    except IntegrityError:
        # The unique(draft_id) backstop fired: another dispatch won the
        # race (its claim preceded ours in a path the conditional UPDATE
        # could not observe). Nothing of ours was sent — the INSERT is the
        # first write of this attempt.
        db.rollback()
        db.refresh(draft)
        raise DraftConflictError("This application was already submitted")

    try:
        if method == "email":
            _send_with_pdfs(db, application, draft, job, profile)
        else:
            application.status = "manual_pending"
    except Exception:
        # _send_with_pdfs swallows its own failures onto the application
        # row; anything raised PAST it must not strand the 'sending'
        # claim (the sweep is the last resort, not the plan).
        db.rollback()
        draft.status = "ready"
        db.add(draft)
        db.commit()
        raise

    # State follows the outcome: a FAILED email send leaves the draft
    # editable, so "Finish applying" still shows it. Applied-ness is DERIVED
    # from the applications table per user — job.status is never written
    # here (it's shared across users).
    if application.status != "failed":
        draft.status = "submitted"
    else:
        draft.status = "ready"
    db.add(draft)
    db.commit()
    db.refresh(application)
    return application


def _release_send_claim(db: Session, draft: ApplicationDraft, *, status: str) -> None:
    """Leave the draft in a truthful state when a submit aborts after the
    claim (never leave 'sending' behind on a path we control)."""
    draft.status = status
    db.add(draft)
    db.commit()


def _send_with_pdfs(
    db: Session,
    application: Application,
    draft: ApplicationDraft,
    job: JobPosting,
    profile: Optional[Profile],
) -> None:
    """Attach tailored cover letter + CV PDFs (and the original CV file) and send."""
    from app.core.config import settings

    if not settings.RESEND_API_KEY or not settings.APPLY_FROM_EMAIL:
        application.status = "failed"
        # P0-6: environment-neutral — "edit backend/.env" was a dead end
        # in the Render container, exactly where this error fires.
        application.error = (
            "Email apply is not configured on this deployment "
            "(RESEND_API_KEY / APPLY_FROM_EMAIL missing) — contact the "
            "operator, or use 'Apply in browser'"
        )
        return

    # P1-3(c): the per-account daily ceiling on employer emails — checked
    # BEFORE any attachment work or dispatch. The shared APPLY_FROM_EMAIL
    # domain carries every user's deliverability; one runaway account
    # must not burn it.
    from app.core.ratelimit import enforce

    enforce(application.user_id, "send_daily")

    import base64
    import os

    try:
        import resend

        applicant = profile.full_name if profile else None
        attachments = []
        os.makedirs(DRAFT_DIR, exist_ok=True)
        for filename, blob in (
            (f"Cover Letter - {applicant or 'Applicant'}.pdf",
             pdf_service.cover_letter_pdf(draft.cover_letter or "", applicant)),
            (f"CV - {applicant or 'Applicant'} (tailored).pdf",
             pdf_service.tailored_cv_pdf(draft.tailored_cv or "", applicant)),
        ):
            attachments.append(
                {"filename": filename, "content": base64.b64encode(blob).decode("utf-8")}
            )

        # Also attach the original CV PDF when available (storage-aware).
        # P1-5b: the DRAFT's snapshot decides which CV is "original" — this
        # package was tailored against THAT file. Reading the profile's
        # CURRENT path here is how a re-upload mid-review produced
        # CV-old-tailored documents mailed with CV-new attached. Legacy
        # drafts (NULL snapshot) fall back to the current path.
        from app.services.storage import read_cv_at_path

        original_cv_path = draft.cv_file_path or (
            profile.cv_file_path if profile else None
        )
        original_cv = read_cv_at_path(original_cv_path)
        if original_cv:
            attachments.append(
                {
                    "filename": (profile.cv_file_name if profile else None) or "CV.pdf",
                    "content": base64.b64encode(original_cv).decode("utf-8"),
                }
            )

        resend.api_key = settings.RESEND_API_KEY
        from_email = (
            f"{applicant} <{settings.APPLY_FROM_EMAIL}>" if applicant else settings.APPLY_FROM_EMAIL
        )
        params: dict = {
            "from": from_email,
            "to": [application.target_email],
            "subject": application.subject,
            "text": draft.cover_letter or "",
            "attachments": attachments,
        }
        # P1-1: applications go out from the SHARED APPLY_FROM_EMAIL —
        # without reply_to an employer's answer (the interview invitation)
        # lands in the operator's inbox and never reaches the applicant.
        if profile is not None and getattr(profile, "email", None):
            params["reply_to"] = profile.email
        email = resend.Emails.send(params)
        application.status = "sent"
        application.sent_at = utc_now()
        logger.info("Tailored application sent to %s (id=%s)", application.target_email, email.get("id"))
    except Exception as e:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Email send failed: {e}"
        logger.error("Tailored application send failed: %s", e)
