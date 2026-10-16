"""Profile API — CV upload and the single active job-seeker profile."""

import logging
from functools import partial

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.config import settings
from app.core.database import get_db
from app.core.ratelimit import enforce
from app.crud import get_stats
from app.models import User
from app.schemas.profile import (
    OnboardingRequest,
    ProfilePreferencesUpdate,
    ProfileResponse,
)
from app.services import ai_queue
from app.services.ai_service import ai_service_available
from app.services.cv_service import (
    create_or_replace_profile_from_cv,
    get_active_profile,
)
from app.services.source_packs import available_countries

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/upload", response_model=ProfileResponse)
async def upload_cv(
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    Upload a CV (PDF or Word .docx): extracts the text, stores the file
    and replaces the active profile — then QUEUES the AI profile
    extraction (WO-31).

    The CV is saved before any AI runs; an unreadable file is a 400 here.
    The extraction is an ai_job: 202 with `extraction` (queued/running,
    position, ETA) while it waits, and provider load delays it instead of
    silently losing the fields. 200 when it has already landed.
    """
    from app.services import cv_service

    enforce(user.id, "cv_upload")
    lower = (file.filename or "").lower()
    if not file.filename or not lower.endswith((".pdf", ".docx")):
        raise HTTPException(
            status_code=400,
            detail=(
                "Please upload a PDF or Word (.docx) file — older .doc "
                "files need to be saved as .docx first"
            ),
        )

    content = await file.read()
    try:
        # run_in_threadpool: parsing + storage are blocking IO — running
        # them inline would block the event loop.
        profile = await run_in_threadpool(
            partial(
                create_or_replace_profile_from_cv,
                db,
                content,
                file.filename,
                user_id=user.id,
                extract=False,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if cv_service.ai_service_available():
        await run_in_threadpool(
            partial(ai_queue.enqueue, db, user_id=user.id, kind="extract",
                    subject_id=profile.id)
        )
        await run_in_threadpool(ai_queue.drain_if_inline)
        db.expire_all()
    else:
        logger.warning("GLM_API_KEY not set — profile stored with CV text only; AI matching disabled")
    extraction = ai_queue.extraction_view(db, profile)
    if extraction is not None and extraction["status"] in ("queued", "running"):
        response.status_code = 202
    return ProfileResponse.from_orm_profile(profile, extraction=extraction)


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Get the active profile (404 until a CV is uploaded)."""
    profile = get_active_profile(db, user_id=user.id)
    if not profile:
        raise HTTPException(status_code=404, detail="No profile yet — upload a CV first")
    return ProfileResponse.from_orm_profile(
        profile, extraction=ai_queue.extraction_view(db, profile))


@router.put("/me", response_model=ProfileResponse)
async def update_preferences(
    prefs: ProfilePreferencesUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """Update contact info and job-search preferences."""
    from app.schemas.common import dump_json_list

    profile = get_active_profile(db, user_id=user.id)
    if not profile:
        raise HTTPException(status_code=404, detail="No profile yet — upload a CV first")

    data = prefs.model_dump(exclude_unset=True)
    # Round-2 finding 7: the Profile editor sends work_rights on EVERY
    # save — re-evaluation is owed only when the ANSWER changed, not
    # when a phone number was edited beside it.
    prior_work_rights = profile.work_rights
    for field, value in data.items():
        if field in ("preferred_roles", "exclude_keywords"):
            setattr(profile, field, dump_json_list(value))
        elif field == "work_rights":
            from app.services.eligibility_lexicon import WORK_RIGHTS_VALUES

            if value is not None and value not in WORK_RIGHTS_VALUES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"work_rights must be one of "
                        f"{list(WORK_RIGHTS_VALUES)}"
                    ),
                )
            setattr(profile, field, value)
        elif field == "vouched_facts":
            # WO-23: strict 30x200 bound — a 400 the user can act on,
            # not a silent trim of facts they believe are saved.
            from app.services.cv_service import normalize_vouched_facts

            try:
                setattr(profile, field, dump_json_list(
                    normalize_vouched_facts(value)))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif field == "remote_ok":
            setattr(profile, field, 1 if value else 0)
        else:
            setattr(profile, field, value)

    db.add(profile)
    db.commit()
    db.refresh(profile)
    if "work_rights" in data and data.get("work_rights") != prior_work_rights:
        # Round-1 finding 5: the answer decides which postings are hidden
        # — existing undecided matches must follow it immediately.
        # (Round-2 finding 7: only on an actual CHANGE.)
        from app.services.matcher_service import reevaluate_eligibility

        reevaluate_eligibility(db, user_id=user.id, profile=profile)
    return ProfileResponse.from_orm_profile(profile)


@router.post("/onboarding", response_model=ProfileResponse)
async def save_onboarding(
    payload: OnboardingRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    Save the onboarding wizard result: country, region, municipality,
    remote-only preference, and the approved search queries. This is what
    makes the pipeline targeted (source pack + queries + location filter).
    """
    from app.data.geo import COUNTRIES

    country = (payload.country or "").upper()
    if country not in COUNTRIES:
        raise HTTPException(status_code=400, detail=f"Unsupported country: {payload.country}")

    profile = get_active_profile(db, user_id=user.id)
    if not profile:
        raise HTTPException(status_code=404, detail="No profile yet — upload a CV first")

    from app.schemas.common import dump_json_list

    profile.country = country
    profile.region = payload.region
    # Strict multi-municipality scope (user decision: picking Malmö means
    # Malmö; add Lund for the commute belt; none = explicit whole-region).
    # Legacy single field kept in sync (first item) for older consumers.
    municipalities = payload.municipalities or []
    profile.municipalities = dump_json_list(municipalities)
    profile.municipality = municipalities[0] if municipalities else payload.municipality
    # Commute-zone radius (km) around the first chosen municipality;
    # 0/None = exact match. GEO sources only — falls back silently to
    # municipality codes where no centroid resolves.
    profile.search_radius_km = payload.search_radius_km if payload.search_radius_km else None
    profile.remote_only = 1 if payload.remote_only else 0
    profile.include_remote = 1 if (payload.include_remote or payload.remote_only) else 0
    profile.search_queries = dump_json_list(payload.search_queries)
    # WO-19 part B: the eligibility gate's answer — enum-validated, and
    # prefer_not_say (or omitted) keeps unverified-everywhere behaviour.
    from app.services.eligibility_lexicon import WORK_RIGHTS_VALUES

    if payload.work_rights is not None:
        if payload.work_rights not in WORK_RIGHTS_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"work_rights must be one of {list(WORK_RIGHTS_VALUES)}",
            )
        profile.work_rights = payload.work_rights
    # Occupation taxonomy codes: the server is the authority — client
    # codes are validated against the official concepts feed, unknown
    # codes dropped (logged), labels rehydrated. ENFORCED SE-only
    # (review finding: the comment used to claim GB passes through an
    # empty list, but nothing stopped Swedish codes from storing on a
    # GB profile after an edit-mode country switch).
    from app.services import occupation_taxonomy

    valid_picks = []
    if (country or "").upper() == "SE" and payload.occupation_codes:
        # The taxonomy table is a ~515 KB sync fetch on first use —
        # never on the event loop; startup warms it, this covers cold
        # and failed boots.
        valid_picks = await run_in_threadpool(
            occupation_taxonomy.validate_codes, payload.occupation_codes
        )
    elif payload.occupation_codes:
        logger.info(
            "onboarding: %d occupation code(s) ignored — country %s is not SE",
            len(payload.occupation_codes), country,
        )
    if payload.occupation_codes and not valid_picks:
        logger.info(
            "onboarding: %d occupation code(s) submitted, none valid — dropped",
            len(payload.occupation_codes),
        )
    profile.occupation_codes = dump_json_list(valid_picks)
    profile.languages = dump_json_list(payload.languages or ["English"])
    profile.onboarded = 1
    # WO-19 B: the onboarding answer (and edit-setup reflows) revise
    # existing undecided matches the same way the Profile editor does.
    from app.services.matcher_service import reevaluate_eligibility

    reevaluate_eligibility(db, user_id=user.id, profile=profile)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return ProfileResponse.from_orm_profile(profile)


@router.post("/suggest-queries")
async def suggest_queries(
    country: str,
    response: Response,
    mode: str = "field",
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    AI suggests job-search queries for the active profile's CV in the given
    country, shaped by the user's chosen search strategy:
    field (default) | adjacent | widen. The mode is the user's own choice —
    never inferred from age or any protected characteristic.

    WO-31: queued — 202 with {"job": {...queued/running, position, ETA}};
    poll GET /api/v1/ai-jobs/{id} for the result. 200 with the
    suggestions (and the finished job) when already done. Provider load
    keeps it queued; it never errors on load.
    """
    from app.data.geo import COUNTRIES

    country = (country or "").upper()
    if country not in COUNTRIES:
        raise HTTPException(status_code=400, detail=f"Unsupported country: {country}")
    if mode not in ("field", "adjacent", "widen"):
        raise HTTPException(status_code=400, detail="mode must be field, adjacent or widen")

    enforce(user.id, "ai_suggest")
    profile = get_active_profile(db, user_id=user.id)
    if not profile or not profile.cv_text:
        raise HTTPException(status_code=400, detail="Upload a CV first")

    if not ai_service_available():
        raise HTTPException(status_code=400, detail="GLM_API_KEY not configured")

    job = await run_in_threadpool(
        partial(ai_queue.enqueue, db, user_id=user.id, kind="suggest",
                subject_id=profile.id, params={"country": country, "mode": mode})
    )
    await run_in_threadpool(ai_queue.drain_if_inline)
    db.expire_all()
    view = ai_queue.job_view(db.get(type(job), job.id), include_result=True)
    if view["status"] == "done" and view.get("result"):
        return {**view["result"], "job": view}
    response.status_code = 202
    return {"country": country, "mode": mode, "job": view}


@router.get("/geo")
async def get_geo():
    """Region → city/municipality data for the onboarding wizard dropdowns."""
    from app.data.geo import GEO
    from app.services.geo import RADIUS_SUPPORTED_MUNICIPALITIES

    return {
        "countries": available_countries(),
        "geo": GEO,
        # Where the commute-radius control can honestly anchor (a
        # centroid exists for the user's PRIMARY town)
        "radius_supported": RADIUS_SUPPORTED_MUNICIPALITIES,
    }


@router.get("/status")
async def profile_status(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Quick readiness check used by the dashboard."""
    profile = get_active_profile(db, user_id=user.id)
    return {
        "has_profile": profile is not None,
        "has_cv_text": bool(profile and profile.cv_text),
        "ai_enabled": ai_service_available(),
        "email_apply_enabled": settings.EMAIL_APPLY_ENABLED,
        "stats": get_stats(db, user_id=user.id),
    }
