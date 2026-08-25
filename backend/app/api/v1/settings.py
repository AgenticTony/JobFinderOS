"""Settings API — integrations (Composio connected accounts)."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import composio_service

logger = logging.getLogger(__name__)
router = APIRouter()


class ComposioConnectRequest(BaseModel):
    app_name: str = "gmail"
    redirect_uri: str = "http://localhost:3000/"


@router.get("/integrations")
async def integrations():
    """Integration statuses for the Settings page."""
    accounts = await composio_service.list_connections()
    return {
        "composio": {
            "configured": composio_service.is_configured(),
            "accounts": accounts,
        }
    }


@router.post("/integrations/composio/connect")
async def composio_connect(payload: ComposioConnectRequest):
    """Initiate the Composio OAuth flow; the frontend opens the redirect URL."""
    if not composio_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="COMPOSIO_API_KEY not set — add it to backend/.env (from composio.dev)",
        )
    url = await composio_service.initiate_connection(
        payload.app_name, payload.redirect_uri
    )
    if not url:
        raise HTTPException(status_code=502, detail="Composio did not return a connection URL")
    return {"redirect_url": url}
