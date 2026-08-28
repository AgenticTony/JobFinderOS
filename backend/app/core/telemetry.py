"""Sentry telemetry — GATED and PII-scrubbed (WO-05 / F7).

F7: the integration captures REQUEST BODIES — on this API that means CV
text, cover letters and profile data. Initialization is a no-op without
SENTRY_DSN, and every event passes scrub_pii() which drops request
bodies entirely and redacts the fields that carry personal data. Run
Sentry in the EU region when the DSN is created (matches the residency
posture; Mistral runs Sentry in the EEA for the same reason).
"""

import logging

logger = logging.getLogger(__name__)

# Keys whose VALUES are personal data (CVs, letters, profiles, drafts).
# Redaction is by field NAME, recursively, plus bodies dropped whole.
_PII_KEYS = {
    "cv_text", "tailored_cv", "cover_letter", "changes_summary",
    "reasoning", "matched_skills", "missing_skills", "transferable_skills",
    "cover_note", "profile_context", "job_description", "cv_file_path",
    "ai_summary", "recent_roles", "skills", "search_queries",
    # identity fields (reviewer probe: full_name left the machine)
    "full_name", "email", "phone", "location", "preferred_locations",
    "display_name", "username",
}


def scrub_pii(event, hint=None):
    """Sentry before_send: drop request/response bodies entirely and
    redact known PII-carrying fields, recursively."""
    for pocket in ("request", "response"):
        body = (event.get(pocket) or {})
        for k in ("data", "body", "cookies", "query_string"):
            if k in body:
                body[k] = "[redacted]"
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = _redact(extra)
    # Breadcrumbs carry LOG MESSAGES — httpx/uvicorn logs include request
    # payloads; a reviewer probe showed cv_text riding through them
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, list):
        for c in crumbs:
            if not isinstance(c, dict):
                continue
            if "message" in c:
                c["message"] = _redact(c["message"]) if isinstance(c["message"], dict) \
                    else "[redacted message]"
            # DATA is the field integrations actually populate
            # (logging.py:378 puts every log record's extra here) — it
            # bypassed name-redaction AND the truncation cap until now
            if isinstance(c.get("data"), dict):
                c["data"] = _redact(c["data"])

    def _redact_frames(frames):
        for f in frames or []:
            vars_ = f.get("vars")
            if isinstance(vars_, dict):
                f["vars"] = _redact(vars_)

    try:
        _redact_frames(event["exception"]["values"][-1]["stacktrace"]["frames"])
        for exc in event.get("exception", {}).get("values", []):
            _redact_frames((exc.get("stacktrace") or {}).get("frames"))
    except (KeyError, TypeError):
        pass
    return event


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k in _PII_KEYS else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 2000:
        return obj[:2000] + "…[truncated]"  # CV-sized strings never leave
    return obj


def init_sentry() -> None:
    """Initialize Sentry only when SENTRY_DSN is set; no-op otherwise.
    PII scrubbing applies unconditionally when enabled."""
    from app.core.config import settings

    dsn = getattr(settings, "SENTRY_DSN", "") or ""
    if not dsn:
        logger.info("Sentry disabled (no SENTRY_DSN) — WO-05 telemetry off")
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        # COLLECTION-POINT fix (review r2): frame locals carry CV text
        # under arbitrary names (user_message, raw, result_text…) — name
        # redaction cannot enumerate them. Never collect them at all.
        include_local_variables=False,
        # EU region: create the project on sentry.io's EU data residency
        # (de.sentry.io / eu endpoints) — same posture as the AI residency
        # decision. The DSN encodes the region.
        before_send=scrub_pii,
        traces_sample_rate=0.1,
        send_default_pii=False,  # never send user PII headers/ips
        environment=getattr(settings, "ENVIRONMENT", "development"),
    )
    logger.info("Sentry initialized (PII-scrubbed, send_default_pii=False)")
