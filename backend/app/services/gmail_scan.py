"""WO-20 Gmail read adapter + the scheduled outcome maintenance entry.

The ONLY module in the outcome layer that touches Composio —
outcome_service stays importable (and unit-testable) with no vendor SDK
in the picture: it consumes an injected FetchMessages callable, and this
module is the production implementation of that callable.

Vendor rule (composio_service): tool slugs are never hardcoded — slugs
are discovered per user session via search_tools and matched by shape.
Live verification path: POST /outcomes/scan from the UI (the same
first-call discipline as scripts/composio_first_call.py).
"""

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from typing import Any, List, Optional

from app.services import composio_service

logger = logging.getLogger(__name__)

# Default lookback when no scan cursor exists yet — bounded, not "all mail"
DEFAULT_LOOKBACK_DAYS = 90

# Per-scan message cap. NOT a silent drop: past the cap the fetch reports
# truncated=True and the scan holds its cursor behind the last processed
# message, so the next scan resumes where this one stopped.
SCAN_PAGE_CAP = 50

_SLUG_RE = re.compile(r"\bGMAIL_[A-Z0-9_]+\b")


def _discover_slugs(plan: Any) -> List[str]:
    """Pull GMAIL_* tool slugs out of a search_tools response (slugs ride
    the guidance text — the vendor's documented shape)."""
    found = _SLUG_RE.findall(json.dumps(plan, default=str))
    seen: set = set()
    return [s for s in found if not (s in seen or seen.add(s))]


def _pick(slugs: List[str], *needles: str, exclude: str = "") -> Optional[str]:
    for slug in slugs:
        if exclude and exclude in slug:
            continue
        if all(n in slug for n in needles):
            return slug
    return None


async def _slugs_for(user_id: str) -> List[str]:
    plan = await composio_service.search_tools(
        user_id, "search gmail messages by query and read a message body"
    )
    return _discover_slugs(plan)


def _header(headers: Any, name: str) -> Optional[str]:
    """Gmail payload headers arrive as [{'name','value'},…] — tolerantly."""
    try:
        for h in headers or []:
            key = h.get("name", "") if isinstance(h, dict) else getattr(h, "name", "")
            if str(key).lower() == name:
                return (h.get("value") if isinstance(h, dict) else getattr(h, "value", None))
    except Exception:  # noqa: BLE001 — headers are best-effort
        return None
    return None


def _body_text(payload: Any) -> Optional[str]:
    """Full plain-text body from a Gmail payload: prefers text/plain
    parts (decoded); NEVER falls back to the snippet — a missing body is
    surfaced upstream, not classified (the WO-20 full-body rule)."""
    try:
        import base64

        def walk(node) -> Optional[str]:
            mime = str(node.get("mimeType", "") if isinstance(node, dict) else getattr(node, "mimeType", ""))
            data = node.get("body", {}) if isinstance(node, dict) else getattr(node, "body", None)
            data = data or {}
            blob = data.get("data") if isinstance(data, dict) else None
            if mime == "text/plain" and blob:
                return base64.urlsafe_b64decode(blob).decode("utf-8", errors="replace")
            for part in (node.get("parts", []) if isinstance(node, dict) else getattr(node, "parts", None)) or []:
                got = walk(part)
                if got:
                    return got
            return None

        return walk(payload) if payload else None
    except Exception:  # noqa: BLE001 — one malformed message, not a scan failure
        return None


async def gmail_fetch_messages(
    user_id: str,
    after_date: Optional[date],
    sender_terms: Optional[List[str]] = None,
) -> List[dict]:
    """Fetch sender-constrained inbox messages since `after_date` (plus a
    one-day overlap — dedupe is the unique proposal index), oldest→newest,
    with FULL bodies. Raises ComposioError upward; scan_replies records
    it in gmail_scan_state.last_error."""
    if not composio_service.is_configured():
        raise composio_service.ComposioError("COMPOSIO_API_KEY not set — inbox scan disabled")

    lookback = after_date or (date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    query = f"after:{lookback.strftime('%Y/%m/%d')}"
    if sender_terms:
        froms = " OR ".join(f"from:{t}" for t in sender_terms)
        query = f"({froms}) {query}"

    slugs = await _slugs_for(user_id)
    search_slug = (
        _pick(slugs, "SEARCH") or _pick(slugs, "QUERY") or _pick(slugs, "LIST")
    )
    get_slug = (
        _pick(slugs, "MESSAGE")
        or _pick(slugs, "EMAIL")
    )
    if not search_slug or not get_slug:
        raise composio_service.ComposioError(
            f"gmail tool discovery failed (search={search_slug}, get={get_slug})"
        )

    search_resp = await composio_service.execute_tool(user_id, search_slug, {"query": query})
    # Tolerant traversal: response shape has moved between SDK versions;
    # threads → messages meta is the stable Gmail shape we rely on.
    def _find_messages(node) -> List[dict]:
        if isinstance(node, dict):
            if "id" in node and ("threadId" in node or "thread_id" in node):
                return [node]
            out: List[dict] = []
            for v in node.values():
                out.extend(_find_messages(v))
            return out
        if isinstance(node, list):
            out: List[dict] = []
            for v in node:
                out.extend(_find_messages(v))
            return out
        return []

    metas = _find_messages(search_resp)
    # bounded pass — but NEVER silent: if the query returned more than
    # the cap we say so, and the scan keeps its cursor behind the last
    # message actually processed so nothing is skipped forever (review
    # round 1, finding 8).
    truncated = len(metas) > SCAN_PAGE_CAP
    messages: List[dict] = []
    for meta in metas[:SCAN_PAGE_CAP]:
        msg_id = str(meta.get("id"))
        detail = await composio_service.execute_tool(user_id, get_slug, {"message_id": msg_id})
        payload = detail.get("payload") if isinstance(detail, dict) else None
        headers = payload.get("headers") if isinstance(payload, dict) else None
        internal_date = None
        if isinstance(detail, dict):
            raw = detail.get("internalDate") or detail.get("internal_date")
            if raw:
                try:
                    from datetime import datetime, timezone

                    # NAIVE UTC — the app's timestamp convention. A tz-aware
                    # arrival time mixed with naive sent_at raises TypeError
                    # in the stats subtraction (review round 1, finding 9).
                    internal_date = datetime.fromtimestamp(
                        int(raw) / 1000, tz=timezone.utc
                    ).replace(tzinfo=None)
                except (ValueError, OSError):
                    internal_date = None
        body = _body_text(payload)
        messages.append(
            {
                "id": msg_id,
                "from": _header(headers, "from") or meta.get("from"),
                "subject": _header(headers, "subject"),
                "body": body,
                "received_at": internal_date,
            }
        )
    # oldest→newest by ARRIVAL TIME (message-id string sort is not
    # chronological), id as the stable tiebreak
    messages.sort(key=lambda m: (m["received_at"] is None, m["received_at"], str(m["id"])))
    return {"messages": messages, "truncated": truncated}


# ---------------------------------------------------------------------------
# Scheduled maintenance — called by the worker after each hunt
# ---------------------------------------------------------------------------

def run_outcome_maintenance(db) -> dict:
    """DB-only sweep for every user with sent applications + an inbox scan
    for users with a connected Gmail entity. Sync (worker context, no
    event loop): the async scan runs on a throwaway loop. One user's
    failure never kills the pass — it lands in their scan state."""

    from app.models import Application
    from app.services.outcome_service import propose_no_response_sweep, scan_replies

    summary = {"swept_users": 0, "sweep_proposals": 0, "scanned_users": 0, "scan_errors": 0}

    user_ids = [
        row[0]
        for row in db.query(Application.user_id)
        .filter(Application.sent_at.isnot(None))
        .distinct().all()
    ]
    for uid in user_ids:
        try:
            created = propose_no_response_sweep(db, user_id=uid)
            summary["sweep_proposals"] += created
            summary["swept_users"] += 1
        except Exception as e:  # noqa: BLE001
            logger.error("Outcome sweep failed for %s: %s", uid, e)

    if composio_service.is_configured():
        for uid in user_ids:
            try:
                connections = asyncio.run(
                    composio_service.list_connections(str(uid))
                )
                if not any(c.get("app_name") == "gmail" for c in connections):
                    continue
                asyncio.run(
                    scan_replies(db, uid, gmail_fetch_messages)
                )
                summary["scanned_users"] += 1
            except Exception as e:  # noqa: BLE001 — recorded in scan state
                summary["scan_errors"] += 1
                logger.error("Outcome scan failed for %s: %s", uid, e)
    return summary
