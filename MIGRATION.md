# MIGRATION — Supabase consolidation (DECIDED; not yet started)

Status: **decided — go** (settled 2026-08-27). Reason: vendor consolidation
for a solo operator and managed auth we never maintain — explicitly NOT
for RLS (see the correction below; that backstop is available on any
managed Postgres). Nothing has been started; WO0 is the first action.
The decision gate is kept below as historical context so it isn't
re-litigated, not as an open question.

This plan runs to the same standard as the rest of the repo: every work
order ships with tests that have been seen to fail against the regression
it guards.

---

## The decision gate (historical — resolved GO)

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
systems. That drift ends here.

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

### WO0 — Off-site backups (~30 min, HARD PRECONDITION)

Every later work order assumes the data survives its own mistake; today
that assumption rests on one drive — `ops/backup.sh` writes to
`~/backups` on the same disk as the SQLite file it protects. This is the
only item in the plan whose downside is unrecoverable, so it goes first:
copy the current backup set off-machine (any of: another machine,
encrypted cloud storage, an S3 bucket). Nothing else starts until a copy
exists somewhere the laptop cannot destroy.

### WO1 — Database: SQLite → Supabase Postgres (~0.5–1 day)

Scope: provision the Supabase project (EU); `DATABASE_URL` to its
connection string; alembic to head; data migration script for the live
rows (2 users, 2 profiles, 399 jobs, 243 matches, 4 drafts, 2
applications — counts verified 2026-08-26); `ops/backup.sh` rewritten for
Supabase (see the PITR note below; the sqlite3 `.backup` choreography
retires).

**Alembic vs Supabase's schemas (scope + trap):** `env.py` sets
`target_metadata = Base.metadata` with no `include_object` filter and no
`version_table_schema`. Supabase Postgres ships `auth`, `storage` and
`realtime` schemas; autogenerate against it diffs their tables as
unknown and happily emits DROPs. WO1 adds an `include_object` filter
restricting autogenerate to `public` and pins `version_table_schema`
before the first `alembic revision --autogenerate` against Supabase.

De-risking: the suite now runs on BOTH backends in CI (matrix,
sqlite + postgres) — and making that true found real divergence: 22
tests failed on Postgres because SQLite silently does not enforce
foreign keys while the suite fabricated profiles and matches with
user_ids that had no users row (fixed — the tests now create the user
rows both backends demand). Backend divergence now fails CI instead of
surfacing mid-WO1.

Tests/done: full suite + flow green on both CI legs; local dev + tests
run against the Supabase DB; a row-count + invariant diff against the
pre-migration snapshot matches exactly (the re-score script's
snapshot-then-verify discipline, applied here).

Rollback: **clean only before the first post-cutover write** — the
SQLite file is untouched by the migration itself, so pointing
`DATABASE_URL` back is lossless until something writes to Supabase; one
hunt on the new stack and a rollback silently discards it. With 2 users
that is tolerable — after any post-cutover write, roll FORWARD, not back.

**Supabase backup tiers (verified against official docs 2026-08-27):**
the Free plan has NO automatic backups at all (self-serve `db dump` is
the documented answer); Pro adds daily backups with 7-day retention;
PITR is a separate paid ADD-ON even on Pro (~$100/mo at 7-day retention,
and enabling it replaces daily backups). Decide before WO1 whether
paying users' CVs justify Pro + the PITR add-on from day one — this
moves beta cost well off $0 and belongs in the WO4 budget line, not
discovered after the first support email.

### WO2 — Auth: fastapi-users → Supabase Auth (~1–1.5 days)

Scope: create the 2 real users in Supabase (real passwords, verified
addresses); remap the 4 FK columns to the new Supabase UUIDs in one
transaction with a pre-write snapshot; `users.py` shrinks to JWKS
verification (~50 lines: fetch keys, verify RS256, read `sub`);
`deps.get_authenticated_user` verifies + upserts the mirror row on first
sight; delete the 3 auth routers, the async engine/`auth_engine`/the
dual-database-URL machinery, and the auth-specific signup hardening from
96b4cd7. Frontend login → supabase-js, interceptor → session refresh,
logout via SDK; GDPR delete becomes dual-delete (local cascade + Supabase
admin API) with the rate-limit memory purge kept.

**Deletion is SURGICAL on the rate limiter:** only `login_rate_limit` /
`register_rate_limit` (the two deps in `api/deps.py`) and their
`auth_register`/`auth_login` bucket entries go. `core/ratelimit.py`
STAYS — `enforce()` is the only ceiling on GLM spend and guards five
AI-spending endpoints (match_run, draft_prepare, hunt, cv_upload,
ai_suggest) that Supabase replaces none of.

**AUTH_SECRET guard (deploy blocker):** `config.py`'s production guard
refuses to construct Settings when `DEBUG=false` if `AUTH_SECRET` is
weak. Once auth moves to Supabase that value is dead config — but the
guard still fires and blocks the deploy. Retire the check, or repoint it
at the Supabase JWT secret so the production guard keeps meaning
something. Repointing is preferred.

**UUID remap + constraints:** `profiles.user_id` is UNIQUE NOT NULL — a
remap can transiently collide two rows inside the transaction. With 2
users and random Supabase UUIDs the collision is theoretical, but write
the discipline anyway: one transaction, `SET CONSTRAINTS ALL DEFERRED`
(or an update order proven collision-free), verified by the same
row-count diff as the data migration. This is the detail that stalls a
migration at 11pm.

Doc-verified facts this WO relies on (checked 2026-08-27): new Supabase
projects default to asymmetric RS256 signing keys with a public JWKS
endpoint (`/auth/v1/.well-known/jwks.json`) — the JWKS-verification
design is correct for a project created today; `auth.admin.deleteUser`
defaults to a HARD delete (`shouldSoftDelete=false`), which is exactly
what GDPR erasure wants; leaked-password (HIBP) protection is Pro-plan-
only, and auth rate limits are largely per-IP (with `Sb-Forwarded-For`
available if auth traffic ever proxies through our backend instead of
the browser talking to Supabase directly).

Tests: **test-only JWKS** — a fixture keypair, the JWKS fetch monkeypatched,
`_register`/`_auth_client` mint signed tokens. The hidden cost of this WO
is exactly this infrastructure; budget it. The proof the swap is invisible
above the boundary: `TestLayer1Routes`, `TestOutboundEmailBoundary`, and
the two-tenant route test pass unchanged, on minted tokens.

Rollback: fastapi-users code is deleted only after the new flow is green;
keep it on a branch until WO3 ships.

### WO3 — RLS + JWT propagation (~1 day)

Scope: per-request propagation middleware on every request-scoped
transaction (SQLAlchemy event listener), using the DOCUMENTED direct-
connection pattern — `SET LOCAL role authenticated` + `SET LOCAL
request.jwt.claim.sub = <uuid>` (per Supabase's RLS guide); policies on
profiles, match_results, application_drafts, applications keyed to
`auth.uid()` with the `auth.uid() IS NOT NULL AND ...` guard (a NULL
identity silently filters everything, which is fail-closed and correct,
but must be deliberate); the **two-session story made
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
7. **Alembic autogenerate vs Supabase's schemas** — without an
   `include_object` filter, `alembic revision --autogenerate` diffs
   Supabase's `auth`/`storage`/`realtime` tables as unknown and emits
   DROPs for them (WO1 scope).
8. **SQLite never enforced the FKs** — the suite passed for weeks on
   non-enforcement; the CI matrix now catches this class, and any new
   test that fabricates rows without their parents will fail the
   Postgres leg first.
9. **AUTH_SECRET's production guard outlives its secret** — retire or
   repoint it in WO2 or the deploy refuses to boot (WO2 scope).

## Estimate

**3–4 working days** at this repo's standard (tests-first, revert-checked,
boundary-crossing), not the 1.5–2 days of the original sketch — the delta
is WO2's test infrastructure, the frontend session rework, and dual-delete.
Sequencing rule: this lands **before deployment and before real users** —
deploy once, on the final stack.
