"""Beta onboarding drip — signup trigger + erasure cleanup for Resend.

The daily "how to use this section" email series lives in a Resend
AUTOMATION (dashboard-editable copy, exact-day delays that a sleeping
free-tier API could never keep). This service is the app's half:

  - notify_signup(): on on_after_register, best-effort create the Resend
    contact and fire the `user.created` event the automation listens for.
    Gated by ONBOARDING_EMAILS_ENABLED (default off — the owner flips it
    when the automation copy is reviewed).
  - remove_contact(): on GDPR erasure, best-effort delete the contact so
    the drip never emails a deleted account. NOT gated by the enable
    flag — contacts may exist from a period when emails were on, so
    erasure attempts cleanup whenever the Resend key is present.

Both follow feedback_service.notify_owner's contract: never raise into
the caller (a signup must succeed, an erasure must complete), log the
outcome, return bool. Direct REST via httpx (already a dependency) —
the resend SDK's coverage of the Contacts/Events APIs is version-dependent,
the REST surface is the stable contract (resend.com/docs).
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

API = "https://api.resend.com"
ONBOARDING_EVENT = "user.created"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }


def notify_signup(user_email: str, first_name: str | None = None) -> bool:
    """Create-or-update the Resend contact, then fire the drip trigger.

    Returns True when BOTH calls succeeded. Any failure is logged and
    swallowed — registration must never fail because of onboarding.
    """
    if not settings.ONBOARDING_EMAILS_ENABLED or not settings.RESEND_API_KEY:
        return False
    email = (user_email or "").strip().lower()
    if not email:
        return False

    contact_ok = False
    try:
        resp = httpx.post(
            f"{API}/contacts",
            headers=_headers(),
            json={"email": email, **({"first_name": first_name} if first_name else {})},
            timeout=10,
        )
        # 201 created, 200 updated — both fine; anything else is a soft
        # failure we log (the event fire below may still work for an
        # already-existing contact).
        contact_ok = resp.status_code in (200, 201)
        if not contact_ok:
            logger.warning(
                "onboarding: contact create for %s -> %s %s",
                email, resp.status_code, resp.text[:120],
            )
    except Exception:  # noqa: BLE001 — best-effort by contract
        logger.exception("onboarding: contact create failed for %s", email)

    try:
        resp = httpx.post(
            f"{API}/events",
            headers=_headers(),
            json={"type": ONBOARDING_EVENT, "contact": {"email": email}},
            timeout=10,
        )
        if resp.status_code in (200, 201, 202):
            return True
        logger.warning(
            "onboarding: event fire for %s -> %s %s",
            email, resp.status_code, resp.text[:120],
        )
        return False
    except Exception:  # noqa: BLE001
        logger.exception("onboarding: event fire failed for %s", email)
        return False


def remove_contact(user_email: str) -> bool:
    """Erase the Resend contact so the drip stops with the account.

    Deletes by email (the Contacts API accepts the email query form);
    200/204 = gone, 404 = never existed (e.g. emails never enabled) —
    both count as success for erasure purposes.
    """
    if not settings.RESEND_API_KEY:
        return False
    email = (user_email or "").strip().lower()
    if not email:
        return False
    try:
        resp = httpx.delete(
            f"{API}/contacts",
            headers=_headers(),
            params={"email": email},
            timeout=10,
        )
        if resp.status_code in (200, 204, 404):
            return True
        logger.warning(
            "onboarding: contact delete for %s -> %s %s",
            email, resp.status_code, resp.text[:120],
        )
        return False
    except Exception:  # noqa: BLE001 — erasure must complete regardless
        logger.exception("onboarding: contact delete failed for %s", email)
        return False
