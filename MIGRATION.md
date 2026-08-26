# MIGRATION — Supabase consolidation (PROPOSED, not started)

Status: **proposed**. Nothing in this document has been begun. The decision
gate below must be answered first. This plan exists so the decision is made
against a real scope and cost estimate, not vibes — and so that, if approved,
it runs to the same standard as the rest of the repo: every work order ships
with tests that have been seen to fail against the regression it guards.

---

## The decision gate

Proceed with this migration **only if** at least one of these is true:

1. Google OAuth (or any social login) is wanted in v1.
2. Real users are ≥2 weeks away (the migration fits comfortably before
   deployment; after real users hold passwords, its cost roughly doubles).
3. Operating one vendor (auth + database + storage) matters more to you
   than shipping the current stack this week.

**Stay on fastapi-users** if users must be live this week and OAuth is
later: the remaining signup work is ~half a day (mount fastapi-users' own
verify/reset routers + Resend wiring; policy and rate limits already
shipped in 96b4cd7), deployment proceeds on Neon + Render per CLAUDE.md,
and RLS remains available on Neon as Layer 3 with identical discipline.

One correction recorded from the deciding conversation, so it stops being
re-litigated: **RLS is NOT a reason to choose Supabase.** Row-level
security is a Postgres feature; Neon provides the same machinery with the
same JWT-propagation discipline. Supabase's genuine value here is managed
auth (verification, reset, rate limits, policy, OAuth), vendor
consolidation for a solo operator, and the EU region for GDPR. The
tenancy backstop is bought by moving to managed Postgres with identity
propagation — available on either vendor.

Also resolved by this decision either way: the ROADMAP previously planned
"Google OAuth later via Supabase" alongside fastapi-users — two auth
systems. Whichever way the gate falls, that drift ends.

## Destination

| Component        | From                                  | To                                        |
|------------------|---------------------------------------|-------------------------------------------|
| Database         | SQLite (local), Postgres 16 (CI only) | Supabase Postgres, **EU region**          |
| Auth             | fastapi-users on a second async engine| Supabase Auth (JWKS-verified JWT) + local mirror |
| Tenancy backstop | application-layer only (Layers 0/1)   | + RLS with per-request JWT propagation    |
| Storage          | local disk / Supabase Storage (built) | unchanged — already Supabase              |
| Email (app mail) | Resend                                | unchanged (auth emails come from Supabase)|
| Frontend         | 7-day JWT in localStorage             | supabase-js session, httpOnly refresh cookie |
| Processes        | API (matching in-threadpool)          | API + worker split, Postgres-backed queue |
| Hosting          | undeployed                           | Render (web + worker) + Cloudflare Pages  |

Unchanged by design: FastAPI, sync SQLAlchemy, the route/service Layer-1
shape, the frontend itself, the scoring pipeline, all 4 FK tables (they
point at the local mirror, not Supabase's schema).

## Work orders

Ordered so each step is independently verifiable and independently
rollback-able. The user-UUID remap happens in WO2, **before** RLS lands in
WO3 — never remap users under live RLS policies keyed to `sub`.

### WO1 — Database: SQLite → Supabase Postgres (~0.5–1 day)

Scope: provision the Supabase project (EU); `DATABASE_URL` to its
connection string; alembic to head; data migration script for the live
rows (2 users, 2 profiles, 399 jobs, 243 matches, 4 drafts, 2
applications — counts verified 2026-08-26); `ops/backup.sh` rewritten for
Supabase (pg_dump + PITR note; the sqlite3 `.backup` choreography retires).

Tests/done: the full suite already runs green on Postgres 16 in CI — that
is the de-risking fact for this WO. Done when local dev + tests run
against the Supabase DB and a row-count + invariant diff against the
pre-migration snapshot matches exactly (the re-score script's
snapshot-then-verify discipline, applied here).

Rollback: the SQLite file is untouched until WO2 commits; point
`DATABASE_URL` back.

### WO2 — Auth: fastapi-users → Supabase Auth (~1–1.5 days)

Scope: create the 2 real users in Supabase (real passwords, verified
addresses); remap the 4 FK columns to the new Supabase UUIDs in one
transaction with a pre-write snapshot; `users.py` shrinks to JWKS
verification (~50 lines: fetch keys, verify RS256, read `sub`);
`deps.get_authenticated_user` verifies + upserts the mirror row on first
sight; delete the 3 auth routers, the async engine/`auth_engine`/the
dual-database-URL machinery, **and the 96b4cd7 password-policy +
auth-rate-limit code (Supabase provides both — this deletion is the
"waste" the decision gate accepts)**; frontend login → supabase-js,
interceptor → session refresh, logout via SDK; GDPR delete becomes
dual-delete (local cascade + Supabase admin API) with the rate-limit
memory purge kept.

Tests: **test-only JWKS** — a fixture keypair, the JWKS fetch monkeypatched,
`_register`/`_auth_client` mint signed tokens. The hidden cost of this WO
is exactly this infrastructure; budget it. The proof the swap is invisible
above the boundary: `TestLayer1Routes`, `TestOutboundEmailBoundary`, and
the two-tenant route test pass unchanged, on minted tokens.

Rollback: fastapi-users code is deleted only after the new flow is green;
keep it on a branch until WO3 ships.

### WO3 — RLS + JWT propagation (~1 day)

Scope: per-request propagation middleware — `SET LOCAL
request.jwt.claims` on every request-scoped transaction (SQLAlchemy event
listener); RLS policies on profiles, match_results, application_drafts,
applications keyed to `auth.uid()`; the **two-session story made
explicit**: request sessions propagate the JWT (RLS enforced), worker/
scheduler sessions run service-role (RLS not enforced) as two named
session factories — the discipline point, now structural and documented.

Tests (the WO's core): the reviewer-specified trap — two tenants, RLS on,
a deliberately unscoped `SELECT` returns **zero rows**. Revert-checked by
dropping the propagation listener and watching it fail. Plus: background
jobs (scheduler, pipeline) prove they run on the worker factory and never
see RLS errors.

Rollback: RLS policies are additive; `ENABLE ROW LEVEL SECURITY` off
restores WO2 behaviour instantly.

### WO4 — Deploy on the final stack (~0.5–1 day)

Scope: Render web service + worker (the API/worker split this plan bakes
in: matching/pipeline out of the request threadpool, Postgres-backed job
queue via `FOR UPDATE SKIP LOCKED`, scheduler into the worker);
Cloudflare Pages frontend; Sentry + structured logs with request IDs
(minimal observability, its own roadmap item to grow); secrets via
platform env, none in the repo.

Done: `https://` custom domain, health/ready probes, one real signup +
login + hunt exercised end-to-end in the browser (the standard the
frontend auth work set: verified on the wire, not claimed).

## Traps (each is a line item, not a footnote)

1. **JWT propagation is the whole value of WO3.** A service-role key
   bypasses RLS entirely; skipping propagation means paying the migration
   cost for auth convenience only.
2. **Never remap user UUIDs under live RLS** — hence WO2 before WO3.
3. **GDPR is dual-delete** — local cascade alone stops being erasure the
   day auth moves.
4. **Background jobs have no user JWT** — the two-session story must be
   designed (WO3), not discovered in production.
5. **Test JWKS infra is real work** (WO2's hidden cost) — CI cannot reach
   Supabase.
6. **Frontend session shape changes** (refresh flow replaces the 7-day
   token) — the interceptor work from 71ba301 is redone in a new shape,
   browser-verified again before cutover.

## Estimate

**3–4 working days** at this repo's standard (tests-first, revert-checked,
boundary-crossing), not the 1.5–2 days of the original sketch — the delta
is WO2's test infrastructure, the frontend session rework, and dual-delete.
Sequencing rule: this lands **before deployment and before real users** —
deploy once, on the final stack.
