"""
CV service — orchestrates CV upload: extract text, store file, run AI profile extraction.

INVARIANT — THE ORIGINAL CV IS IMMUTABLE:
The uploaded PDF and its extracted text (Profile.cv_text / cv_file_path) are the
permanent reference point for the whole system. They are written exactly once,
at upload time, and are never modified by any later pipeline stage. Every job
specific version lives in its own ApplicationDraft row (see draft_service).
Re-uploading a CV creates a NEW profile row + NEW file (old ones are kept on
disk, just deactivated) — nothing is ever overwritten in place.
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
    """Persist the CV PDF locally so it can be attached to applications."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = filename.rsplit("/", 1)[-1].replace(" ", "_") or "cv.pdf"
    stored_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    path = os.path.join(UPLOAD_DIR, stored_name)
    with open(path, "wb") as fh:
        fh.write(content)
    return path, safe_name


def build_profile_context(profile: Profile) -> str:
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

    lines = []
    if profile.professional_title:
        lines.append(f"Professional title: {profile.professional_title}")
    if profile.experience_years is not None:
        lines.append(f"Years of experience: {profile.experience_years}")
    if skill_names:
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


def create_or_replace_profile_from_pdf(
    db: Session,
    file_content: bytes,
    filename: str,
) -> Profile:
    """
    Upload flow: validate + extract PDF text, store the file,
    run AI profile extraction, and save as the single active profile.
    """
    FileService.validate_pdf(file_content)
    cv_text = FileService.extract_text_from_pdf(file_content)
    path, safe_name = _store_cv_file(file_content, filename)

    extracted: dict = {}
    if ai_service_available():
        try:
            extracted = get_ai_service().extract_profile(cv_text)
        except Exception as e:  # noqa: BLE001 — never fail the upload over AI hiccups
            logger.warning("AI profile extraction failed (continuing with raw CV): %s", e)
    else:
        logger.warning("GLM_API_KEY not set — profile stored with CV text only; AI matching disabled")

    # Deactivate previous profiles (single active profile model)
    db.query(Profile).filter(Profile.is_active == 1).update({"is_active": 0})

    profile = Profile(
        is_active=1,
        cv_text=cv_text,
        cv_file_path=path,
        cv_file_name=filename.rsplit("/", 1)[-1],
        cv_file_size=len(file_content),
        full_name=extracted.get("full_name") or None,
        email=extracted.get("email") or None,
        phone=extracted.get("phone") or None,
        location=extracted.get("location") or None,
        professional_title=extracted.get("professional_title") or None,
        experience_years=_safe_int(extracted.get("experience_years")),
        skills=dump_json_list(extracted.get("skills") or []),
        recent_roles=dump_json_list(extracted.get("recent_roles") or []),
        education=dump_json_list(extracted.get("education") or []),
        certifications=dump_json_list(extracted.get("certifications") or []),
        keywords=dump_json_list(extracted.get("keywords") or []),
        ai_summary=extracted.get("summary") or None,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    logger.info("Created active profile id=%s from %s", profile.id, safe_name)
    return profile


def get_active_profile(db: Session) -> Optional[Profile]:
    return db.query(Profile).filter(Profile.is_active == 1).first()


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
