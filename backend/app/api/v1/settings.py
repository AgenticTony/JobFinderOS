"""Settings API — integrations (Composio connected accounts)."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_authenticated_user
from app.models import User
from app.services import composio_service
from app.services.cv_service import get_active_profile

logger = logging.getLogger(__name__)
router = APIRouter()


class ComposioConnectRequest(BaseModel):
    app_name: str = "gmail"
    redirect_uri: str = "http://localhost:3000/"


def _entity_id() -> str:
    """Stable per-user entity for Composio: the profile's email when set,
    else the profile id. Becomes the logged-in account id once auth lands."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        profile = get_active_profile(db)
        if not profile:
            return "anonymous"
        return (profile.email or f"profile-{profile.id}").strip().lower()
    finally:
        db.close()


@router.get("/integrations")
async def integrations(user: User = Depends(get_authenticated_user)):
    """Integration statuses for the Settings page (scoped to this user)."""
    accounts = await composio_service.list_connections(str(user.id))
    return {
        "composio": {
            "configured": composio_service.is_configured(),
            "accounts": accounts,
        }
    }


@router.post("/integrations/composio/connect")
async def composio_connect(
    payload: ComposioConnectRequest, user: User = Depends(get_authenticated_user)
):
    """Initiate the Composio OAuth flow; the frontend opens the redirect URL."""
    if not composio_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="COMPOSIO_API_KEY not set — add it to backend/.env (from composio.dev)",
        )
    url = await composio_service.initiate_connection(
        payload.app_name, payload.redirect_uri, str(user.id)
    )
    if not url:
        raise HTTPException(status_code=502, detail="Composio did not return a connection URL")
    return {"redirect_url": url}
