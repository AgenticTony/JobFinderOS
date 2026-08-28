# WO-04 — Worker / scheduler split

> Priority: P1 · Depends on: nothing · Status: **executed 2026-08-28**
> Fixes ARCHITECTURE D3: the in-process scheduler blocks horizontal
> scaling — two API replicas = two racing hunt cycles.

## Scope (from D3 + the WO-05 deferral)

1. **The scheduler leaves the API process.** A dedicated worker
   entrypoint (`python -m app.services.worker`) runs the scheduler +
   hunts; the API process never starts one in production (ENABLE_
   SCHEDULER's shipped default is false — its class default is now
   test-pinned). Dev keeps single-process convenience behind the same
   flag (the local .env legitimately sets true).
2. **A DB claim lock guards every scheduled hunt** — portable
   (SQLite + Postgres, no advisory-lock dialect split), so even a
   double-started worker or a stray second process cannot double-fire.
   A crashed holder self-heals: stale claims (past a 45-min TTL) are
   stealable. The first-ever-claim INSERT race is closed by the PK
   itself: the losing inserter returns False cleanly.
3. **user_id lands on ai_usage rows** (deferred from WO-05): a
   request-context contextvar set by middleware (best-effort JWT
   decode — auth itself stays at the routes); record_ai_usage reads
   it. Per-user cost attribution — the trial budget's meter — works
   for every kind (match/tailor/judge/…).

## Acceptance criteria — all verified

- [x] `python -m app.services.worker` starts the scheduler; the API
      lifespan does not (default pinned by test)
- [x] Two concurrent claims → exactly one wins (3 claim tests)
- [x] A stale claim (crashed holder, TTL elapsed) is stealable
- [x] The claim is ALWAYS released — success, failure, the
      nothing-to-do path, and idempotent re-release (mocked-session
      test)
- [x] The deterministic PK-collision race test: the losing inserter
      returns False, never raises
- [x] A request through the API leaves ai_usage rows carrying the
      caller's user_id (revert-checked: attribution removed → red)
- [x] Deploy shape documented: Render web = uvicorn (Dockerfile CMD,
      unchanged), worker = `python -m app.services.worker`
- [x] Tests red-first; 148 passed + 2 skipped, ruff clean

## Process note

RC1 (claim-guard removed) stayed green and proved nothing — the claim
tests never race. The fix was a deterministic race test (a SneakyQuery
that inserts behind claim_hunt's back between query and commit); only
then did the guard's removal go red. The green-revert-check-that-
wasn't is now the session's sixth face of the lesson.
