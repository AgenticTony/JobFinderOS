"""Account API — GDPR erasure (right to be forgotten)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.database import get_db
from app.models import (
    AIUsage,
    Application,
    ApplicationDraft,
    MatchResult,
    Profile,
    User,
)
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

    # P1-5a: EVERY CV object of this user must go — the profile's current
    # path AND every distinct draft snapshot path. Drafts keep their
    # snapshot file alive past a re-upload (their package still needs its
    # original CV); collecting them BEFORE the rows are deleted is what
    # makes erasure complete instead of orphan-permitting.
    cv_paths = set()
    if profile and profile.cv_file_path:
        cv_paths.add(profile.cv_file_path)
    for (snap,) in (
        db.query(ApplicationDraft.cv_file_path)
        .filter(
            ApplicationDraft.user_id == uid,
            ApplicationDraft.cv_file_path.isnot(None),
        )
        .distinct()
        .all()
    ):
        cv_paths.add(snap)

    # DELETE ORDER MATTERS (P0-2, live-confirmed): applications reference
    # drafts AND matches (draft_id, match_id), drafts reference matches
    # (match_id). Those FKs are NOT DEFERRABLE with no ON DELETE action,
    # so deleting parents first raises IntegrityError -> 500 -> rollback
    # that keeps EVERY personal row. Children first, always:
    # applications -> drafts -> matches -> profiles -> user.
    applications = db.query(Application).filter(Application.user_id == uid).delete()
    drafts = db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid).delete()
    matches = db.query(MatchResult).filter(MatchResult.user_id == uid).delete()
    profiles = db.query(Profile).filter(Profile.user_id == uid).delete()

    # ai_usage rows are user-linked telemetry (no FK — a plain user_id
    # column), which is why erasure missed them. Retention decision for a
    # pre-beta product: DELETE them with the account. The table exists for
    # cost accounting and residency audits of LIVE accounts; once the
    # account is erased there is no lawful basis to keep per-user call
    # history, and aggregate cost trends survive via every other user's
    # rows. Revisit only if a retention obligation (e.g. invoicing law)
    # appears — until then, account death takes its telemetry.
    ai_usage = db.query(AIUsage).filter(AIUsage.user_id == uid).delete()

    db.delete(user)
    db.commit()

    # CV file removal AFTER the commit: doing it before meant a failed
    # transaction (the IntegrityError above, in production) destroyed the
    # user's only CV while every PII row survived — the exact live repro.
    # Goes through the storage backend, so it works for local paths AND
    # remote object keys (the os.path.exists version silently skipped
    # Supabase keys, leaving the CV in the bucket after "erasure").
    # Every collected path is attempted — one failure must not skip the
    # rest of the user's PII files.
    from app.services.storage import get_storage

    deleted_files = 0
    for cv_path in sorted(p for p in cv_paths if p):
        try:
            if get_storage().delete(cv_path):
                deleted_files += 1
        except Exception:
            logger.warning("GDPR delete: CV removal failed for %s", cv_path)

    from app.core.ratelimit import clear_email, clear_user
    clear_user(uid)
    # P1-8: the auth throttles are keyed by EMAIL (reg:{email},
    # login:{email}), not user id — those entries outlived the account for
    # up to an hour, keeping live in-memory state for the erased address
    # and 429ing its same-address re-signup. The per-IP buckets
    # (regip:/loginip:) cannot be keyed to a user and expire with their
    # window.
    clear_email(user_email)
    logger.info(
        "GDPR erasure: user=%s (%s) — %d matches, %d drafts, %d applications, "
        "%d profiles, %d ai_usage rows, %d CV file(s)",
        uid,
        user_email,
        matches,
        drafts,
        applications,
        profiles,
        ai_usage,
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

    def draft_row(d):
        # The user's own content: the tailored package they reviewed and
        # (usually) edited. Portability covers it verbatim.
        return {
            "job_id": d.job_id,
            "status": d.status,
            "cover_letter": d.cover_letter,
            "tailored_cv": d.tailored_cv,
            "changes_summary": d.changes_summary,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }

    def app_row(a):
        # subject/body/target_email are the user's outbound content and the
        # address they sent it to — core portability data, not internals.
        return {
            "job_id": a.job_id,
            "method": a.method,
            "status": a.status,
            "subject": a.subject,
            "body": a.body,
            "target_email": a.target_email,
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
        "drafts": [
            draft_row(d)
            for d in db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid).all()
        ],
        "applications": [
            app_row(a)
            for a in db.query(Application).filter(Application.user_id == uid).all()
        ],
    }
