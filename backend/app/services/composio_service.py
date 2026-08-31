"""
Composio integration — connected accounts for third-party apps (Gmail etc).

Official SDK integration (2026-08-31), per docs.composio.dev: sessions are
the standard Platform path — `composio.sessions.create(user_id=...)`,
authorize via hosted Connect Links, execute through the session. The
original v1 REST client is gone (v1 returns 410 Gone); this module now
wraps the vendor SDK so wire-format evolution is the SDK's problem, not
ours. Dependency: composio + 10 transitive pins in requirements.lock.

The SDK is synchronous; every call is wrapped in asyncio.to_thread so
the async contract the Settings routes await stays unchanged.

Multi-user model (Composio Platform): ONE project API key brokers every
connection; end users never need Composio accounts. Each connection is
filed under the `user_id` WE pass — always users.id (the UUID;
Composio's documented best-practice identifier), so connections stay
private per user. OAuth tokens are stored and refreshed at Composio and
NEVER pass through this backend — connections add nothing to the GDPR
export/erasure surface.

Sessions: one per operation (create -> use). Session ids persist
server-side; storing them locally for resume is the follow-up once the
connect UI ships in earnest.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class ComposioError(Exception):
    """A Composio call failed — callers decide between degrade and 502."""


def is_configured() -> bool:
    """Config gate — the kill-switch pattern (see FABRICATION_JUDGE)."""
    return bool(settings.COMPOSIO_API_KEY)


_client = None


def get_composio():
    """Lazy singleton. The key is injected from settings, never
    os.environ (pydantic-settings does not export to the process env)."""
    global _client
    if _client is None:
        if not is_configured():
            raise ComposioError("COMPOSIO_API_KEY not set — integrations disabled")
        from composio import Composio

        _client = Composio(api_key=settings.COMPOSIO_API_KEY)
    return _client


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    """Tolerant field read across SDK model objects and plain dicts."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return default


def _jsonable(obj: Any) -> Any:
    """Best-effort SDK-response -> JSON-compatible conversion."""
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:  # noqa: BLE001 — fall through to defaults
            pass
    if isinstance(obj, (list, tuple)):
        return [_jsonable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "__dict__") and vars(obj):
        return {k: _jsonable(v) for k, v in vars(obj).items()}
    return obj


async def list_connections(user_id: str) -> List[Dict[str, Any]]:
    """This user's connected accounts, or [] when unconfigured/unreachable
    (integration status must never 500 the Settings page)."""
    if not is_configured():
        return []
    try:

        def _list():
            return get_composio().connected_accounts.list(user_ids=[user_id])

        response = await asyncio.to_thread(_list)
        items = _field(response, "items", default=[]) or []
        out = []
        for it in items:
            toolkit = _field(it, "toolkit", default={}) or {}
            out.append(
                {
                    "id": _field(it, "id"),
                    "app_name": str(
                        _field(toolkit, "slug", default="")
                        or _field(it, "toolkit_slug", default="")
                    ).lower(),
                    "status": _field(it, "status", default="UNKNOWN"),
                    "created_at": str(_field(it, "created_at", default="")),
                }
            )
        return out
    except Exception as e:  # noqa: BLE001 — status page degrades, never 500s
        logger.error("Composio list_connections failed: %s", e)
        return []


async def initiate_connection(
    app_name: str, redirect_uri: str, user_id: str
) -> Optional[str]:
    """Start this user's OAuth flow for one app (toolkit slug, e.g.
    'gmail'); returns the hosted Connect Link the frontend opens.

    The user signs in with THEIR Google (etc.) account on Composio's
    hosted page — the project key only brokers the handshake and files
    the connection under `user_id`. `redirect_uri` becomes the
    post-connect callback back into the app.
    """
    if not is_configured():
        return None

    def _authorize():
        session = get_composio().sessions.create(user_id=user_id)
        request = session.authorize(toolkit=app_name, callback_url=redirect_uri)
        return getattr(request, "redirect_url", None)

    try:
        return await asyncio.to_thread(_authorize)
    except Exception as e:  # noqa: BLE001 — the route 502s on None
        logger.error("Composio initiate_connection failed: %s", e)
        return None


async def search_tools(user_id: str, use_case: str) -> Any:
    """Runtime tool discovery — tool slugs are never hardcoded (vendor
    rule); search by use case inside the user's session. Returns the
    SDK response as a JSON-compatible structure (Composio returns a
    recommended plan that names tool slugs in its guidance text)."""
    if not is_configured():
        return []

    def _search():
        session = get_composio().sessions.create(user_id=user_id)
        return session.search(query=use_case)

    return _jsonable(await asyncio.to_thread(_search))


async def execute_tool(
    user_id: str, tool_slug: str, arguments: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Execute ONE tool as this user, through a session scoped to them.

    Product rule (the approval invariant): nothing here sends anything on
    its own — outbound actions must be routed by callers through the same
    explicit-user-approval gates as every other dispatch path. Raises
    ComposioError on failure; never degrades silently.
    """
    if not is_configured():
        raise ComposioError("COMPOSIO_API_KEY not set — integrations disabled")

    def _execute():
        session = get_composio().sessions.create(user_id=user_id)
        return session.execute(tool_slug, arguments=arguments or {})

    response = await asyncio.to_thread(_execute)
    data = _jsonable(response)
    if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
        raise ComposioError(f"Composio execute {tool_slug} failed: {str(data)[:300]}")
    return data if isinstance(data, dict) else {"data": data}
