"""Account API — GDPR erasure (right to be forgotten) and export."""

import base64
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
from app.schemas.common import parse_json_list
from app.services.cv_service import get_active_profile
from app.services.storage import read_original_cv

logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/account/delete", status_code=200)
async def delete_account(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Erase the account and every personal row: the Supabase identity,
    profile (+CV file), matches, drafts, applications, then the user row.
    Job postings are shared scraped data and stay. Composio connections
    are keyed by user id and become orphaned on Composio's side —
    teardown lands with the Composio send-path work (noted, not silently
    claimed).

    ORDER: the Supabase identity dies FIRST (MIG-WO2) — email and
    password hash live there, so erasure without it is incomplete. If
    that call fails we 503 having deleted nothing, and the user retries;
    the alternative (local cascade first) leaves the strongest PII
    surviving a half-completed request. The local users row becomes a
    tombstone (see the comment at the update) — access tokens stay
    signature-valid up to Supabase's ~1h expiry, and the tombstone turns
    that window into 401s.
    """
    uid = user.id
    user_email = user.email  # capture before detaching

    from app.services import supabase_admin

    if not supabase_admin.delete_user(str(uid)):
        raise HTTPException(
            status_code=503,
            detail="Identity erasure is temporarily unavailable — no data "
            "has been deleted yet; please retry shortly",
        )

    # Same request-scoped session the dependency used — no re-fetch
    # needed since MIG-WO2 (auth no longer runs on a second session).
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

    # The users row becomes a TOMBSTONE, not a DELETE: Supabase access
    # tokens stay signature-valid up to ~1h after the identity dies, and
    # a hard-deleted row meant one post-erasure request resurrected a
    # ghost mirror — re-creating the Profile AND re-firing the onboarding
    # drip at the erased address. The tombstone (redacted email,
    # is_active=False) turns that window into 401s (get_authenticated_
    # user) and carries no personal data: email is a non-deliverable
    # sentinel, display_name nulled, every FK child already deleted.
    user.email = f"erased-{uid}@deleted.invalid"
    user.display_name = None
    user.is_active = False
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

    from app.core.ratelimit import clear_user
    clear_user(uid)
    # MIG-WO2: the email-keyed auth-bucket purge died with those buckets
    # (signup/login moved to Supabase); clear_user above covers every
    # surviving per-user spend key.
    # Beta onboarding drip (2026-09-03): the Resend contact (if one was
    # created at signup) must die with the account or the daily series
    # keeps emailing a deleted user. Best-effort — erasure completes
    # regardless; NOT gated on ONBOARDING_EMAILS_ENABLED because contacts
    # may predate a flag flip.
    try:
        from app.services import onboarding_service

        onboarding_service.remove_contact(user_email)
    except Exception:  # noqa: BLE001 — never block erasure over email
        logger.exception("onboarding: contact cleanup failed for %s", user_email)
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
        # reasoning + skill lists are an AI assessment OF THE PERSON —
        # core Art. 15 data, not internals (external verification pass 2).
        return {
            "job_id": m.job_id,
            "score": m.score,
            "tier": m.tier,
            "recommendation": m.recommendation,
            "reasoning": m.reasoning,
            "matched_skills": parse_json_list(m.matched_skills),
            "missing_skills": parse_json_list(m.missing_skills),
            "transferable_skills": parse_json_list(m.transferable_skills),
            "decision": m.decision,
            "dismissed_reason": m.dismissed_reason,
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

    # The CV is the document the user gave us — Art. 15/20 portability
    # covers it verbatim (external verification pass 2 found it absent).
    # The text is always included; the original PDF bytes are embedded
    # base64 best-effort (storage hiccup must not fail the export).
    cv_file_b64 = None
    if profile:
        try:
            cv_bytes = read_original_cv(profile)
            if cv_bytes:
                cv_file_b64 = base64.b64encode(cv_bytes).decode("ascii")
        except Exception:  # noqa: BLE001 — best-effort; text below is the data
            logger.warning("GDPR export: CV file read failed for %s", uid)

    def usage_row(u):
        # ai_usage: the user's own activity telemetry (which AI ran, when,
        # what it cost). Erasure deletes these; export shows them while live.
        return {
            "kind": u.kind,
            "model": u.model,
            "endpoint": u.endpoint,
            "prompt_tokens": u.prompt_tokens,
            "cached_tokens": u.cached_tokens,
            "completion_tokens": u.completion_tokens,
            "cost_usd": (u.cost_usd / 1_000_000) if u.cost_usd is not None else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
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
            "cv_file_name": profile.cv_file_name,
            "cv_text": profile.cv_text,
            "cv_file_b64": cv_file_b64,
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
        "ai_usage": [
            usage_row(u)
            for u in db.query(AIUsage).filter(AIUsage.user_id == uid).order_by(AIUsage.created_at).all()
        ],
    }
