"""Account API — GDPR erasure (right to be forgotten)."""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.database import get_db
from app.models import Application, ApplicationDraft, MatchResult, Profile, User
from app.services.cv_service import get_active_profile

logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/account/delete", status_code=200)
async def delete_account(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Erase the account and every personal row: profile (+CV file),
    matches, drafts, applications, then the user itself. Job postings are
    shared scraped data and stay. Composio connections are keyed by user id
    and become orphaned on Composio's side — teardown lands with the
    Composio send-path work (noted, not silently claimed)."""
    uid = user.id
    user_email = user.email  # capture before detaching
    # The injected user object belongs to the async auth session; re-fetch
    # in this session so the delete cascade works on one session's objects
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found")
    profile = get_active_profile(db, user_id=uid)

    # Best-effort CV file removal (storage-backend aware)
    cv_path = profile.cv_file_path if profile else None
    deleted_files = 0
    if cv_path and os.path.exists(cv_path):
        try:
            os.remove(cv_path)
            deleted_files = 1
        except OSError:
            logger.warning("GDPR delete: CV file removal failed for %s", cv_path)

    matches = db.query(MatchResult).filter(MatchResult.user_id == uid).delete()
    drafts = db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid).delete()
    applications = db.query(Application).filter(Application.user_id == uid).delete()
    profiles = db.query(Profile).filter(Profile.user_id == uid).delete()

    db.delete(user)
    db.commit()
    logger.info(
        "GDPR erasure: user=%s (%s) — %d matches, %d drafts, %d applications, "
        "%d profiles, %d CV file(s)",
        uid,
        user_email,
        matches,
        drafts,
        applications,
        profiles,
        deleted_files,
    )
    return {
        "status": "erased",
        "detail": "Your account and all personal data have been deleted.",
    }


@router.get("/account/export")
async def export_account(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """GDPR data portability: everything we hold about the caller."""
    uid = user.id
    profile = get_active_profile(db, user_id=uid)

    def match_row(m):
        return {
            "job_id": m.job_id,
            "score": m.score,
            "tier": m.tier,
            "decision": m.decision,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }

    def app_row(a):
        return {
            "job_id": a.job_id,
            "method": a.method,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }

    return {
        "account": {"id": str(uid), "email": user.email, "created_at": user.created_at.isoformat() if user.created_at else None},
        "profile": {
            "full_name": profile.full_name,
            "email": profile.email,
            "phone": profile.phone,
            "location": profile.location,
            "country": profile.country,
            "region": profile.region,
            "municipality": profile.municipality,
            "languages": profile.languages,
            "search_queries": profile.search_queries,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        }
        if profile
        else None,
        "matches": [
            match_row(m)
            for m in db.query(MatchResult).filter(MatchResult.user_id == uid).all()
        ],
        "applications": [
            app_row(a)
            for a in db.query(Application).filter(Application.user_id == uid).all()
        ],
    }
