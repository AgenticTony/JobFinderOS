# WO-05 — Observability + per-call AI cost rows

> Priority: P1 · Status: **executed 2026-08-28** (with MIG-WO0, the
> off-site backup precondition, in the same session)

## What shipped

### 1. `ai_usage` — one row per AI call (the table three work orders needed)

Model + Alembic migration (`20260828_d8a2b3c4e9f5`). Recorded inside
`AIService._complete` — the ONE call site every AI operation uses —
labelled by kind (`match|tailor|judge|extract|suggest|unknown`):

- **Cost accounting**: `cost_usd` in micro-dollars, computed at write
  from the verified price table (`glm-5.1`: 1.40/0.26/4.40 per M,
  docs.z.ai 2026-08-27; cached tokens priced at the cached rate). The
  1.9× price-blindness class of error becomes a query.
- **Price-drift detection**: recorded token counts vs billed —
  compare any month's `SUM(cost_usd)` against the invoice.
- **Residency audit trail**: `endpoint` (GLM_BASE_URL hostname),
  `model`, `request_id`, timestamp — the exact per-request fields
  Mistral's regional-inference docs prescribe for auditability, ready
  when the EU endpoint lands (WO-15/MIG-WO5).
- Recorder is failure-tolerant (observability never breaks the call
  it observes) and uses its own short-lived session — works for the
  matcher, drafts, judge and scheduler identically.

### 2. Sentry — gated, PII-scrubbed (F7)

`app/core/telemetry.py`. Init is a no-op without `SENTRY_DSN`;
`scrub_pii` (the `before_send` hook) drops request/response bodies
WHOLE and redacts every known PII-carrying field (cv_text,
tailored_cv, cover_letter, reasoning, skills, …) recursively, plus
frame locals, plus truncates CV-sized strings. `send_default_pii=False`
(no IPs/headers). **When the DSN is created: use the EU region** —
same posture as the AI-residency decision.

### 3. MIG-WO0 — off-site backups (the unrecoverable-risk item)

`ops/backup.sh` gained the off-site step: `OFFSITE_BACKUP_TARGET`
(rsync `user@host:/path` or `rclone:remote:bucket` via OFFSITE_CMD),
**verified by file count** and failing non-zero on an unreachable
destination; unset warns loudly that backups live on ONE disk.
Locally simulated end-to-end (27 files synced + verified); the
unreachable-destination failure mode exits non-zero (proven); unset
prints the one-disk warning (proven).

**Remaining human step:** set `OFFSITE_BACKUP_TARGET` to the real
target (a second machine or cloud bucket — the user's choice of
provider) in the deployment environment. The mechanics are proven;
the credential is yours.

## Acceptance criteria — all verified

- [x] Usage row per call: kind/model/endpoint/request_id/tokens — tested
- [x] Cost math at verified prices incl. cached rate — tested, exact
- [x] Sentry no-op without DSN — tested; scrub drops bodies + redacts
      PII fields recursively — tested
- [x] Off-site sync verifies (count match) and fails loudly — proven
      locally, both directions
- [x] 133 passed + 2 skipped, ruff clean
