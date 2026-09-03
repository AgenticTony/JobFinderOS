"""
CV service — orchestrates CV upload: extract text, store file, run AI profile extraction.

INVARIANT — THE ORIGINAL CV IS IMMUTABLE:
The uploaded PDF and its extracted text (Profile.cv_text / cv_file_path) are the
permanent reference point for the whole system. They are written exactly once,
at upload time, and are never modified by any later pipeline stage. Every job
specific version lives in its own ApplicationDraft row (see draft_service).

Re-uploading replaces THIS user's profile row in place and stores a NEW file;
the object it replaces is deleted at re-upload (P1-5a) unless a still-open
draft snapshotted it (its package needs its original CV until sent — GDPR
erasure sweeps snapshot paths when the account dies). The previous claim that
"a new profile row is created and old files are deactivated" was false: the
row was overwritten and the old file orphaned on disk, surviving erasure.
"""

import logging
import os
import uuid
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models import Profile
from app.schemas.common import dump_json_list, parse_json_list
from app.services.ai_service import ai_service_available, get_ai_service
from app.services.file_service import FileService

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.getenv("CV_UPLOAD_DIR", "uploads/cvs")


def _store_cv_file(content: bytes, filename: str) -> Tuple[str, str]:
    """Persist the CV PDF via the storage backend (local disk by default,
    Supabase Storage when STORAGE_BACKEND=supabase) so it can be attached
    to applications. Returns (storage key, original safe filename)."""
    from app.services.storage import get_storage

    safe = filename.rsplit("/", 1)[-1].replace(" ", "_") or "cv.pdf"
    stored_name = f"{uuid.uuid4().hex[:8]}_{safe}"
    mime = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if safe.lower().endswith(".docx")
        else "application/pdf"
    )
    key = get_storage().save(stored_name, content, mime)
    return key, safe


def build_profile_context(profile: Profile, include_derived: bool = True) -> str:
    """Compact text summary of the profile + preferences fed to the matcher."""
    skills = parse_json_list(profile.skills)
    preferred = parse_json_list(profile.preferred_roles)
    excluded = parse_json_list(profile.exclude_keywords)
    roles = parse_json_list(profile.recent_roles)

    skill_names = [s.get("name", "") if isinstance(s, dict) else str(s) for s in skills]
    role_lines = [
        f"- {r.get('title','')} at {r.get('company','')} ({r.get('period','')})"
        if isinstance(r, dict) else str(r)
        for r in roles
    ]

    # include_derived=False renders ONLY user-entered fields: the
    # fabrication guard verifies against this (WO-01 review r5). Derived
    # fields (title, skills) are extraction-model OUTPUT — 'REST API
    # Design' absent from the CV was silently blessed when the whole
    # context was guard truth. The model still sees them (its prompt
    # uses the default); the guard must not trust them.
    lines = []
    if include_derived and profile.professional_title:
        lines.append(f"Professional title: {profile.professional_title}")
    # NOTE: experience_years is DELIBERATELY not rendered. The CV says
    # '20 years in regulated operations'; a bare 'Years of experience: 20'
    # next to an aspirational junior title strips the domain qualifier and
    # hands the model '20 years of development' — the root cause of the
    # judge's competence-inflation findings (WO-01 review, 2026-08-28).
    # The full CV text is included in every prompt and states it truthfully.
    if include_derived and skill_names:
        lines.append(f"Skills: {', '.join(skill_names[:40])}")
    if profile.location:
        lines.append(f"Location: {profile.location}")
    lines.append(f"Open to remote: {'yes' if profile.remote_ok else 'no'}")
    languages = parse_json_list(profile.languages)
    if languages:
        lines.append(
            f"Working languages (jobs in other languages are a poor fit): {', '.join(languages)}"
        )
    if preferred:
        lines.append(f"Preferred roles: {', '.join(preferred)}")
    if profile.preferred_locations:
        lines.append(f"Preferred locations: {profile.preferred_locations}")
    if profile.min_salary:
        lines.append(f"Minimum salary: {profile.min_salary}")
    if excluded:
        lines.append(f"Hard excludes (skip jobs containing): {', '.join(excluded)}")

    context = "\n".join(lines)
    if role_lines:
        context += "\nRecent roles:\n" + "\n".join(role_lines)
    return context


def create_or_replace_profile_from_cv(
    db: Session,
    file_content: bytes,
    filename: str,
    *,
    user_id,
) -> Profile:
    """
    Upload flow: validate + extract text (PDF or Word .docx), store the
    file, run AI profile extraction, and save as THE USER'S profile
    (one each — re-upload replaces that user's own profile, never
    anyone else's). Legacy binary .doc is deliberately NOT accepted:
    no pure-Python parser exists; the error tells the user to re-save.
    """
    if (filename or "").lower().endswith(".docx"):
        FileService.validate_size(file_content)
        if not FileService.is_docx(file_content):
            raise ValueError("File is not a valid Word document (.docx)")
        cv_text = FileService.extract_text_from_docx(file_content)
    else:
        FileService.validate_pdf(file_content)
        cv_text = FileService.extract_text_from_pdf(file_content)
    path, safe_name = _store_cv_file(file_content, filename)

    extracted: Optional[dict] = None
    if ai_service_available():
        try:
            extracted = get_ai_service().extract_profile(cv_text)
        except Exception as e:  # noqa: BLE001 — never fail the upload over AI hiccups
            logger.warning("AI profile extraction failed (continuing with raw CV): %s", e)
    else:
        logger.warning("GLM_API_KEY not set — profile stored with CV text only; AI matching disabled")

    # Per-user: replace THIS user's profile if it exists (the singleton
    # takeover — second upload stealing the whole app — died with this)
    profile = db.query(Profile).filter(Profile.user_id == user_id).first()
    replaced_path = profile.cv_file_path if profile is not None else None
    if profile is None:
        profile = Profile(user_id=user_id, is_active=1)
    profile.cv_text = cv_text
    profile.cv_file_path = path
    profile.cv_file_name = filename.rsplit("/", 1)[-1]
    profile.cv_file_size = len(file_content)
    if extracted:
        _apply_extraction(profile, extracted)
    else:
        # AI-11 (live-confirmed): a malformed extraction parses to {} (and
        # a raised one lands here too — the except above leaves it None).
        # _apply_extraction(profile, {}) NULLs full_name/email/phone/title/
        # years, so a re-upload during a Z.ai hiccup wiped a previously-good
        # profile. The write is guarded HERE because raising inside the AI
        # service does not help — this function swallows it. An empty
        # extraction is a no-op for the derived fields: the new CV text and
        # file metadata above still land, and the existing fields survive.
        logger.warning(
            "AI profile extraction produced no fields for user %s — keeping "
            "existing profile fields (extracted=%r)",
            user_id, extracted,
        )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    # P1-5a: the NEW object is safely committed — retire the one it
    # replaced. This runs AFTER the commit so a failed delete can never
    # roll back the upload itself.
    if replaced_path and replaced_path != path:
        _retire_replaced_cv(db, user_id, replaced_path)
    # Beta onboarding (2026-09-03): the CV parse is the first moment we
    # know the user's name — sync it onto the Resend contact so drip
    # emails 2-6 greet personally (the contact starts as "there").
    # Post-commit and best-effort by contract: a Resend hiccup must
    # never fail the upload.
    if profile.full_name:
        from app.models import User
        from app.services.onboarding_service import update_contact_first_name

        account = db.query(User).filter(User.id == user_id).first()
        if account and account.email:
            update_contact_first_name(
                str(account.email), profile.full_name.split()[0]
            )

    logger.info("Saved profile id=%s (user=%s) from %s", profile.id, user_id, safe_name)
    return profile


def _retire_replaced_cv(db: Session, user_id, replaced_path: str) -> None:
    """Delete the CV storage object a re-upload replaced (P1-5a).

    The replaced object is otherwise referenced by NOTHING while erasure
    only deletes the profile's CURRENT path — the live-confirmed orphan
    that outlived GDPR deletion. Kept alive ONLY when a still-open draft
    snapshotted it: that draft's package must be able to attach the CV it
    was tailored from (erasure sweeps snapshot paths when the account
    dies). Best-effort by design — a storage hiccup logs a warning and
    never fails the upload (the new object is already stored).
    """
    from app.models import ApplicationDraft
    from app.services.storage import get_storage

    still_needed = (
        db.query(ApplicationDraft.id)
        .filter(
            ApplicationDraft.user_id == user_id,
            ApplicationDraft.cv_file_path == replaced_path,
            ApplicationDraft.status != "submitted",
        )
        .first()
    )
    if still_needed:
        logger.info(
            "Keeping replaced CV object %s — still snapshotted by an open "
            "draft (erasure will sweep it when the account is deleted)",
            replaced_path,
        )
        return
    try:
        if get_storage().delete(replaced_path):
            logger.info("Deleted replaced CV object %s (user=%s)", replaced_path, user_id)
    except Exception as e:  # noqa: BLE001 — never fail the upload over cleanup
        logger.warning(
            "Could not delete replaced CV object %s (user=%s) — the new CV "
            "is stored; orphan cleanup did not complete: %s",
            replaced_path, user_id, e,
        )


def _apply_extraction(profile: Profile, extracted: dict) -> None:
    """Write AI-extracted fields onto the profile."""
    profile.full_name = extracted.get("full_name") or None
    profile.email = extracted.get("email") or None
    profile.phone = extracted.get("phone") or None
    profile.location = extracted.get("location") or None
    profile.professional_title = extracted.get("professional_title") or None
    profile.experience_years = _safe_int(extracted.get("experience_years"))
    profile.skills = dump_json_list(extracted.get("skills") or [])
    profile.recent_roles = dump_json_list(extracted.get("recent_roles") or [])
    profile.education = dump_json_list(extracted.get("education") or [])
    profile.certifications = dump_json_list(extracted.get("certifications") or [])
    profile.keywords = dump_json_list(extracted.get("keywords") or [])
    profile.ai_summary = extracted.get("summary") or None


def get_active_profile(db: Session, *, user_id) -> Optional[Profile]:
    """The caller's profile — never anyone else's.

    user_id is REQUIRED and keyword-only. There is deliberately no
    unscoped fallback: every previous version of this function resolved a
    bare call to *some* profile (newest, then oldest), and that convenience
    is exactly what produced the three cross-tenant P0 leaks — the tailoring
    input, the outbound sender identity, and the attached original CV all
    silently belonged to another account.

    A forgotten user_id is now a TypeError at the call site instead of a
    stranger's CV in an employer's inbox. The scheduler already resolves a
    per-user id before every run (scheduler.py), so no system-context path
    needs an unscoped lookup.
    """
    return db.query(Profile).filter(Profile.user_id == user_id).first()


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
