"""Profile API — CV upload and the single active job-seeker profile."""

import logging
from functools import partial

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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
from app.services.ai_service import ai_service_available, get_ai_service
from app.services.cv_service import (
    create_or_replace_profile_from_pdf,
    get_active_profile,
)
from app.services.source_packs import available_countries

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/upload", response_model=ProfileResponse)
async def upload_cv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    Upload a CV PDF: extracts text, stores the file, runs AI profile
    extraction, and replaces the active profile.

    The heavy work (PDF parsing + GLM call, up to ~2 min when Z.ai is slow)
    runs in a threadpool so the rest of the API stays responsive.
    """
    enforce(user.id, "cv_upload")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file")

    content = await file.read()
    try:
        # run_in_threadpool: create_or_replace_profile_from_pdf does blocking
        # IO (pdfplumber, sync httpx) — running it inline would block the
        # event loop and freeze every other request during the GLM call.
        profile = await run_in_threadpool(
            partial(
                create_or_replace_profile_from_pdf,
                db,
                content,
                file.filename,
                user_id=user.id,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ProfileResponse.from_orm_profile(profile)


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Get the active profile (404 until a CV is uploaded)."""
    profile = get_active_profile(db, user_id=user.id)
    if not profile:
        raise HTTPException(status_code=404, detail="No profile yet — upload a CV first")
    return ProfileResponse.from_orm_profile(profile)


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
    for field, value in data.items():
        if field in ("preferred_roles", "exclude_keywords"):
            setattr(profile, field, dump_json_list(value))
        elif field == "remote_ok":
            setattr(profile, field, 1 if value else 0)
        else:
            setattr(profile, field, value)

    db.add(profile)
    db.commit()
    db.refresh(profile)
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
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return ProfileResponse.from_orm_profile(profile)


@router.post("/suggest-queries")
async def suggest_queries(
    country: str,
    mode: str = "field",
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    AI suggests job-search queries for the active profile's CV in the given
    country, shaped by the user's chosen search strategy:
    field (default) | adjacent | widen. The mode is the user's own choice —
    never inferred from age or any protected characteristic.
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

    try:
        result = await run_in_threadpool(
            get_ai_service().suggest_search_queries, profile.cv_text, country, mode
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI suggestion failed: {e}")

    return {"country": country, "mode": mode, **result}


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
