"""
Composio integration — connected accounts for third-party apps (Gmail etc).

Composio is the decided connected-email/integration layer (see CLAUDE.md:
future multi-user sends applications through the user's own email via
Composio tools). This service talks to the Composio REST API with the
COMPOSIO_API_KEY from backend/.env — no SDK dependency, httpx only.

Multi-user model (Composio's SaaS pattern): ONE platform API key brokers
every connection; end users never need Composio accounts. Each OAuth
connection is filed under an entity `user_id` we choose (today: the
active profile's email/id; once auth lands: the logged-in account).
Connect + list are entity-scoped so connections stay private per user.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

COMPOSIO_BASE = "https://backend.composio.dev/api/v1"
TIMEOUT = httpx.Timeout(10, connect=5, read=15)


def is_configured() -> bool:
    return bool(settings.COMPOSIO_API_KEY)


def _headers() -> Dict[str, str]:
    return {"X-API-Key": settings.COMPOSIO_API_KEY, "Content-Type": "application/json"}


async def list_connections(user_id: str) -> List[Dict[str, Any]]:
    """This user's connected accounts (entity-scoped), or [] when unconfigured."""
    if not is_configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{COMPOSIO_BASE}/connectedAccounts",
                headers=_headers(),
                params={"user_id": user_id},
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                {
                    "id": it.get("id"),
                    "app_name": (it.get("appUniqueId") or it.get("appName") or "").lower(),
                    "status": it.get("status", "UNKNOWN"),
                    "created_at": it.get("createdAt"),
                }
                for it in items
            ]
    except Exception as e:  # noqa: BLE001 — integration status must never 500 the app
        logger.error("Composio list_connections failed: %s", e)
        return []


async def initiate_connection(app_name: str, redirect_uri: str, user_id: str) -> Optional[str]:
    """Start this user's OAuth flow for one app; returns the redirect URL.

    The end user signs in with THEIR Google (etc.) account on the provider's
    own consent page — the platform key only brokers the handshake and files
    the connection under `user_id`.
    """
    if not is_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{COMPOSIO_BASE}/connectedAccounts",
                headers=_headers(),
                json={
                    "appName": app_name,
                    "redirectUri": redirect_uri,
                    "user_id": user_id,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("redirectUrl") or data.get("connectionUrl")
    except Exception as e:  # noqa: BLE001
        logger.error("Composio initiate_connection failed: %s", e)
        return None
