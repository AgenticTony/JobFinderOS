# MIGRATION — Supabase consolidation (DECIDED; not yet started)

Status: **decided — go** (settled 2026-08-27). Reason: vendor consolidation
for a solo operator and managed auth we never maintain — explicitly NOT
for RLS (see the correction below; that backstop is available on any
managed Postgres). Nothing has been started; MIG-WO0 is the first action.
The decision gate is kept below as historical context so it isn't
re-litigated, not as an open question.

This plan runs to the same standard as the rest of the repo: every work
order ships with tests that have been seen to fail against the regression
it guards.

> **Numbering:** this file's sequence is prefixed MIG-WO0…MIG-WO5
> (resolved 2026-08-27). The execution queue in `docs/work-orders/`
> numbers its items WO-01…WO-13 — different items, same neighborhood.
> `WO-03` there is the Supabase Postgres migration and absorbs this
> file's whole sequence when it completes; MIG-WO references are only
> used inside this document.

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
rollback-able. The user-UUID remap happens in MIG-WO2, **before** RLS lands in
MIG-WO3 — never remap users under live RLS policies keyed to `sub`.

### MIG-WO0 — Off-site backups (~30 min, HARD PRECONDITION)

Every later work order assumes the data survives its own mistake; today
that assumption rests on one drive — `ops/backup.sh` writes to
`~/backups` on the same disk as the SQLite file it protects. This is the
only item in the plan whose downside is unrecoverable, so it goes first:
copy the current backup set off-machine (any of: another machine,
encrypted cloud storage, an S3 bucket). Nothing else starts until a copy
exists somewhere the laptop cannot destroy.

### MIG-WO1 — Database: SQLite → Supabase Postgres — **EXECUTED 2026-08-28**

Scope: provision the Supabase project (EU); `DATABASE_URL` to its
Supavisor SESSION-mode pooler URL (the decided path — WO-12); alembic to head; data migration script for the live
rows (2 users, 2 profiles, 399 jobs, 243 matches, 4 drafts, 2
applications — counts verified 2026-08-26); `ops/backup.sh` rewritten for
Supabase (see the PITR note below; the sqlite3 `.backup` choreography
retires).

**Alembic vs Supabase's schemas (scope + trap):** `env.py` sets
`target_metadata = Base.metadata` with no `include_object` filter and no
`version_table_schema`. Supabase Postgres ships `auth`, `storage` and
`realtime` schemas; autogenerate against it diffs their tables as
unknown and happily emits DROPs. MIG-WO1 adds an `include_object` filter
restricting autogenerate to `public` and pins `version_table_schema`
before the first `alembic revision --autogenerate` against Supabase.

De-risking: the suite now runs on BOTH backends in CI (matrix,
sqlite + postgres) — and making that true found real divergence: 22
tests failed on Postgres because SQLite silently does not enforce
foreign keys while the suite fabricated profiles and matches with
user_ids that had no users row (fixed — the tests now create the user
rows both backends demand). Backend divergence now fails CI instead of
surfacing mid-MIG-WO1.

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
and enabling it replaces daily backups). Decide before MIG-WO1 whether
paying users' CVs justify Pro + the PITR add-on from day one — this
moves beta cost well off $0 and belongs in the MIG-WO4 budget line, not
discovered after the first support email.

### MIG-WO2 — Auth: fastapi-users → Supabase Auth (~1–1.5 days)

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
keep it on a branch until MIG-WO3 ships.

### MIG-WO3 — RLS + JWT propagation (~1 day)

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
restores MIG-WO2 behaviour instantly.

### MIG-WO4 — Deploy on the final stack (~0.5–1 day)

Scope: Render web service + worker (the API/worker split this plan bakes
in: matching/pipeline out of the request threadpool, Postgres-backed job
queue via `FOR UPDATE SKIP LOCKED`, scheduler into the worker);
Cloudflare Pages frontend; Sentry + structured logs with request IDs
(minimal observability, its own roadmap item to grow); secrets via
platform env, none in the repo.

Done: `https://` custom domain, health/ready probes, one real signup +
login + hunt exercised end-to-end in the browser (the standard the
frontend auth work set: verified on the wire, not claimed).

### MIG-WO5 — AI inference residency + AI Act posture (decision-gated; runs alongside MIG-WO4)

Why: CVs are high-stakes personal data leaving the EEA on every match —
Z.ai's DPA (verified 2026-08-27) processes API data "generally from
Singapore", names no SCCs, and never mentions GDPR. No adequacy decision
covers Singapore or the corporate group's home jurisdiction. CVs also
routinely carry incidental Article 9 special-category data (trade-union
roles, disability accommodations, religious schools, health-shaped
employment gaps), which raises the transfer stakes above ordinary
category. And independent of law: "your CV goes to a Chinese AI company"
is a one-screenshot trust problem for a product serving people in a
stressful economic position.

**Hosting verified in-region — NOT a proxy (2026-08-27).** Three
independent confirmations that Mistral serves the MIT weights itself:
(1) the announcement (11 Aug 2026): "inference and the associated
processing take place in the selected region"; open models run "on the
same infrastructure, regional controls, and service commitments as
Mistral models"; (2) GLM-5.2 is a first-class catalogue entry, licence
Open — the MIT weights are what make hosting possible at all; (3) the
decisive one: **Z.ai is absent from Mistral's sub-processor list**, as
is any China- or Singapore-based entity. The EU-region inference path
is Mistral Compute (France), CoreWeave (EEA), Azure (Sweden/Norway).
Google appears only for the US API endpoint — **the EU endpoint must be
selected explicitly** or the residency guarantee does not apply.

**Blocker LIFTED (verified 2026-08-30).** The tier gate was the
account, not the product: with billing enabled on the correct
workspace (a new key from the billed workspace; the old TalentHive-era
key sat on an unbilled workspace and got 402s even after billing was
switched on elsewhere — keys are workspace-scoped), `models.list`
shows `glm-5-2` and `zai-glm-5-2` on BOTH `api.mistral.ai` AND
`api.eu.mistral.ai`, and standard chat completions succeed. Batch API
access opened with the same billing change (inline batch jobs are
accepted; no completion SLA — submit-and-harvest design required).

**CALIBRATION PASSED (2026-08-30, bench on live jobs).** GLM 5.2 via
Mistral scored 10 real jobs the 5.1 thresholds were built on, with
the active profile's real CV and the exact production prompt:
- keep/dismiss agreement 10/10 — no flips across the keep-min 25 line
- score drift mean +3.2, SD 6.0 — inside the ±11 band the dead-band
  protocol was designed to absorb
- ordering preserved (75→82, 68→72 ... 36→35, 26→28)
- latency 1.8–3.0s at full CV payload (5.1 documented 5–10s)
- JSON parse 10/10 first try
Verdict: nothing blocks moving scoring to `zai-glm-5-2`. Re-run this
bench if the model version changes again.

**DECISION (2026-08-30): stay on the Z.ai subscription while in
beta** — zero marginal cost, quota sufficient at current volume.
Mistral is the armed next step: the switch is configuration only, no
code changes (the AI client is OpenAI-compatible):

    # backend/.env (and the same three in Render when the time comes)
    GLM_BASE_URL=https://api.eu.mistral.ai/v1   # EU endpoint REQUIRED for the residency guarantee
    GLM_MODEL=zai-glm-5-2
    GLM_API_KEY=<Mistral key from the billed workspace>

The commented switch block sits in backend/.env ready to uncomment.
Trigger conditions for flipping: subscription quota starts
throttling hunts, real-user volume arrives, or the beta ends and the
GDPR posture must go live. Batch lane (50% off, cron path only)
remains designed-but-unbuilt until volume justifies it.

Original blocker record (2026-08-27), kept for the lesson:
`models.list` on all three endpoints (HTTP 200) showed no GLM — but a
direct one-token probe returned **HTTP 403 `tier_not_allowed`** (not
404) for `zai-glm-5-2` on BOTH endpoints: the model existed, was
recognized, and was gated behind a higher La Plateforme tier.
Tier-gated models do not appear in `models.list` — the list check
alone under-reports availability (silent-failure trap; caught by
re-verifying with status codes plus a direct probe).

**DPA comparison (verified 2026-08-27):** Mistral's DPA is a materially
better posture than Z.ai's — customer is controller, Mistral is
processor, API data is not used for training absent opt-in or explicit
feedback, SCCs Module 4 are named for restricted transfers, 30-day
deletion after termination. Z.ai's DPA never names GDPR or SCCs.

Re-calibration reminder unchanged: 5.2 is not the model everything was
tuned against — new matching_prompt_version, re-measured SD, tier
bands, backlog re-score (see the gates below). Incidental: Mistral runs
Sentry in the EEA — match the region when OUR observability lands
(MIG-WO4), for the same residency logic.

- **Interim posture (now → GLM-regional-or-equivalent):** stay on Z.ai
  direct under a documented wrapper — their API DPA (controller/processor
  terms), push Z.ai to commit to SCCs in writing, a written TIA, and
  **pseudonymisation as DEFENCE-IN-DEPTH ONLY** (strip names/contacts
  server-side before the call, reinsert after). Per Recital 26 this does
  NOT change the Chapter V position: a CV minus the name is still a
  near-unique fingerprint (employer + dates + title + city re-identifies
  trivially), and it helps the matching call more than the tailoring
  call, which needs the full CV by definition. Do not present it as a
  transfer remedy.
- **Unlock path + monitor:** (a) establish which La Plateforme tier
  unlocks third-party hosted models and its cost/commitment (console or
  docs — currently unknown); (b) once unlocked, CONFIRM the EU endpoint
  serves GLM and on what terms; (c) monitor via a DIRECT one-token
  probe (`POST /v1/chat/completions`, model `zai-glm-5-2`) against
  `api.eu.mistral.ai`, watching for the 403 to turn 200 — NOT via
  `models.list`, which is blind to tier-gated models. Alternatives to
  watch the same way: EU-hosted GLM from Nebius / Scaleway / EUrouter
  (each needs its own DPA diligence).
- **Migration trigger + gates (all must pass):** (1) GLM family served
  on an EU-resident endpoint; (2) quality bake-off passes — NOTE the
  re-calibration cost priced into this gate: a model change means a new
  matching_prompt_version, re-measured score variance (the dead-band
  [13,25) and keep-min were derived from SD 5.5 measured on glm-5.1),
  tier bands revisited, and a backlog re-score (the rescore_backlog.py
  --prompt-version tooling already exists for exactly this); (3) verified
  per-token pricing at the EU endpoint. Known so far (verified
  2026-08-27): GLM-5.2 via Mistral lists $1.40/$0.14-cached/$4.40 with a
  1.1x regional surcharge — input/output identical to Z.ai direct and
  cache CHEAPER ($0.14 vs Z.ai's $0.26), so a 99%-cache-hit workload may
  net out cheaper EU-resident than China-routed. Confirm with recorded
  per-call usage, not arithmetic. Mistral's own models remain
  quality-disqualified regardless of residency (large scored 45-58 on
  jobs GLM scored 18-22 — a different judgment, not compression).
- **AI Act items (live law since 2026-08-02):** JobFinderOS is NOT
  Annex III high-risk — point 4 covers employer-side recruitment/
  selection; we evaluate jobs, not candidates. But: an "AI-assisted"
  disclosure line in the UI (Art 50(1)); machine-readable marking on
  generated PDFs (Art 50(2)) — cheap as PDF metadata either way, since
  the tailored CV is grounded in the user's real CV; no manipulative
  monetisation toward job seekers (Art 5(1)(b) — unemployment is
  textbook "specific social or economic situation"); and the
  **employer-facing scope boundary**: any surface where employers see
  matched candidates flips classification to high-risk. That line
  belongs in PRD.md's scope boundaries as a hard product rule (PRD.md is
  mid-write by the other session — carry it there when it lands).
- **Official regional-inference spec (verified 2026-08-27, docs page):
  implementation is SDK `server="eu"` (mistral SDK >= 2.70) or base URL
  `api.eu.mistral.ai`; regional feature limits are function-calling-only,
  no Agents/Batch/Files — our workload is plain chat completions, fully
  compatible. Control plane (keys, billing, analytics) is NOT regional —
  acceptable: it carries no user CV data.
- **Residency audit trail is a documented requirement, and it is the same
  table as the cost recording:** Mistral's docs prescribe logging the
  endpoint hostname, model ID, timestamp, and response request-id per
  regional request (and the target base URL if proxied). Build ONE
  per-call table — tokens, cost, endpoint hostname, model, request-id,
  timestamp — and it serves cost accounting, the 1.9x-class price-drift
  detection, AND the residency audit trail. Two recommendations, one
  schema.
- **Zero data retention is a SEPARATE control from regional inference**
  (retention of request/response content after processing, vs where
  processing runs). The unlock-path checklist grows one item: evaluate
  Mistral's ZDR terms (and any tier gating) alongside the regional tier.
- **Gate before the first paying user:** one professional legal hour
  covering the Chapter V transfer stack and the Art 50(2) marking
  exemption. Cheap against 4%-of-turnover exposure.

## Traps (each is a line item, not a footnote)

1. **JWT propagation is the whole value of MIG-WO3.** A service-role key
   bypasses RLS entirely; skipping propagation means paying the migration
   cost for auth convenience only.
2. **Never remap user UUIDs under live RLS** — hence MIG-WO2 before MIG-WO3.
3. **GDPR is dual-delete** — local cascade alone stops being erasure the
   day auth moves.
4. **Background jobs have no user JWT** — the two-session story must be
   designed (MIG-WO3), not discovered in production.
5. **Test JWKS infra is real work** (MIG-WO2's hidden cost) — CI cannot reach
   Supabase.
6. **Frontend session shape changes** (refresh flow replaces the 7-day
   token) — the interceptor work from 71ba301 is redone in a new shape,
   browser-verified again before cutover.
7. **Alembic autogenerate vs Supabase's schemas** — without an
   `include_object` filter, `alembic revision --autogenerate` diffs
   Supabase's `auth`/`storage`/`realtime` tables as unknown and emits
   DROPs for them (MIG-WO1 scope).
8. **SQLite never enforced the FKs** — the suite passed for weeks on
   non-enforcement; the CI matrix now catches this class, and any new
   test that fabricates rows without their parents will fail the
   Postgres leg first.
9. **AUTH_SECRET's production guard outlives its secret** — retire or
   repoint it in MIG-WO2 or the deploy refuses to boot (MIG-WO2 scope).

## Estimate

**3–4 working days** at this repo's standard (tests-first, revert-checked,
boundary-crossing), not the 1.5–2 days of the original sketch — the delta
is MIG-WO2's test infrastructure, the frontend session rework, and dual-delete.
Sequencing rule: this lands **before deployment and before real users** —
deploy once, on the final stack.
