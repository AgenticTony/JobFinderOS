"""First real Composio tool call (2026-08-31 spike) — the proof step.

Run AFTER the user completes the Gmail Connect Link. Polls for the
ACTIVE gmail connection, discovers a READ-ONLY gmail tool at runtime
(tool slugs are never hardcoded — vendor rule), executes it through
composio_service.execute_tool — the codebase's real execution path —
and prints the provider result plus the Composio log id.

Usage (from backend/):
  .venv/bin/python scripts/composio_first_call.py --wait 600
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _onboarded_user_id():
    from sqlalchemy import text

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT u.id FROM users u JOIN profiles p ON p.user_id = u.id "
            "WHERE p.cv_text IS NOT NULL LIMIT 1"
        )).one()
        return str(row[0])
    finally:
        db.close()


def _pick_readonly_tools(results):
    """Tolerant extraction: search returns a RECOMMENDED PLAN (guidance
    text naming tool slugs) rather than a structured tool list — pull
    candidate slugs from any structured entries AND from the plan text,
    preferring obviously read-only ones (profile/labels/get, no send)."""
    import re

    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and (
                    item.get("slug") or item.get("tool_slug") or item.get("name")
                ):
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(results)
    text = json.dumps(results, default=str)
    for slug in re.findall(r"\b([A-Z][A-Z0-9]{2,}_[A-Z0-9_]{2,})\b", text):
        found.append({"slug": slug})
    preferred = [
        t for t in found
        if any(k in str(t.get("slug") or t.get("tool_slug") or t).lower()
               for k in ("profile", "label", "get_"))
        and "send" not in str(t.get("slug") or t.get("tool_slug") or t).lower()
    ]
    # de-duplicate, preserving order
    seen, ordered = set(), []
    for t in preferred or found:
        s = str(t.get("slug") or t.get("tool_slug") or t.get("name"))
        if s not in seen:
            seen.add(s)
            ordered.append(t)
    return ordered


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=600,
                    help="seconds to wait for the gmail connection")
    args = ap.parse_args()

    from app.services import composio_service

    user_id = await _onboarded_user_id()
    print(f"user: {user_id}")

    deadline = time.time() + args.wait
    gmail = None
    while True:
        conns = await composio_service.list_connections(user_id)
        gmail = next(
            (c for c in conns
             if c["app_name"] == "gmail" and c["status"] == "ACTIVE"),
            None,
        )
        if gmail:
            break
        remaining = conns and [f"{c['app_name']}={c['status']}" for c in conns]
        if time.time() > deadline:
            raise SystemExit(
                "gmail not connected yet — open the Connect Link and retry. "
                f"Current: {remaining or 'no connections'}"
            )
        print(f"waiting for gmail connection (status: {remaining or 'none'})…")
        await asyncio.sleep(5)

    print(f"connected account: {gmail['id']} (ACTIVE)")

    results = await composio_service.search_tools(
        user_id,
        "read-only gmail: fetch the authenticated user's email address or "
        "list message labels — no sending, no mutation",
    )
    candidates = _pick_readonly_tools(results)
    if not candidates:
        print("search returned no tools — raw response:")
        print(json.dumps(results, indent=1, default=str)[:1200])
        raise SystemExit(1)
    print("discovered tools:", [
        (c.get("slug") or c.get("tool_slug") or c.get("name")) for c in candidates[:5]
    ])
    tool = candidates[0]
    slug = tool.get("slug") or tool.get("tool_slug") or tool.get("name")

    outcome = await composio_service.execute_tool(user_id, slug, {})
    print(f"\n=== FIRST REAL TOOL CALL: {slug} ===")
    print(json.dumps(outcome, indent=1, default=str)[:1500])
    log_id = outcome.get("log_id") or outcome.get("id") or outcome.get("request_id")
    print(f"\nComposio log id: {log_id}")


if __name__ == "__main__":
    asyncio.run(main())
