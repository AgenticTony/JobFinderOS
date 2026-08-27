# JobFinderOS — Architecture

> Status: decided 2026-08-27, from measurement rather than preference.
> Owns: the technical shape. PRD.md owns what we are building; ROADMAP.md
> owns market/strategy; docs/work-orders/ owns sequenced execution.

## Process shape

Two processes, not one.

```
  API (stateless, scales horizontally)      WORKER (single, scheduled)
  ├─ auth, profiles, CV upload              ├─ scrape 6 live sources
  ├─ match queue, approve/dismiss           ├─ dedupe → per-user gates
  ├─ draft create / edit / approve          ├─ AI score vs CV
  └─ send (Resend) + document retrieval     └─ write match_results
                    └──────── Postgres ─────────┘
```

**The scheduler must NOT run in-process with the API.** It currently does
(`APScheduler` `BackgroundScheduler` as a daemon thread, [app/services/scheduler.py]).
Deploy two API replicas and two hunt cycles race each other, double-scoring
every job and double-charging every user. This is latent today because there
is exactly one replica; it becomes a live bug on the day of the first scale-up,
which is the worst possible day to find it. See WO-04.

**Queue:** Postgres `SELECT … FOR UPDATE SKIP LOCKED`. No Redis, no Celery.
At 1,000 users on hourly cycles this is trivial load, and it is one fewer
vendor, one fewer failure mode, one fewer thing to learn.

## Stack

| Layer | Choice | Note |
|---|---|---|
| Runtime | FastAPI + SQLAlchemy 2 + Alembic, Python 3.12 | Unchanged; correct |
| DB | Supabase Postgres | Decided; see WO-03 |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` | No Redis |
| Auth | **Supabase Auth** (recorded decision, MIGRATION.md/WO-03) | Sequencing is open; the direction is not |
| Storage | Supabase Storage | Backend already implemented |
| Email | Resend | Working |
| Frontend | Next.js 16 + React 19 + Tailwind 4 + Zustand | Working |
| Hosting | Render (API + worker) + Cloudflare Pages | **Render needs a paid worker + Supavisor — see audit F1/F5** |
| Observability | Sentry + structured logs + **per-call AI cost rows** | Does not exist yet; WO-05 |

### Auth — the decision is recorded, not reopened here

Supabase Auth is the recorded decision (MIGRATION.md, WO-03). An earlier draft
of this document reversed it on clean-sheet reasoning. That was wrong on
process and on two facts, and the reversal is withdrawn:

- *"Buys no user-visible benefit"* — false. Password reset and email
  verification are user-visible, and today a forgotten password means an
  unrecoverable account holding the user's CV. (It does not follow that this
  favours migrating: `get_reset_password_router` and `get_verify_router` exist
  in the installed fastapi-users and are ~half a day to wire. The benefit is
  real and is available either way — so it does not move the decision.)
- *"Re-opens the bug class behind the three P0 leaks"* — overstated. Those
  leaks came from unscoped profile resolution (`get_active_profile(db)` in
  `create_draft_for_job`, `submit_draft`, `retry_application`), not from who
  mints the JWT. Swapping the token issuer does not touch that path.

**Auth direction has now wobbled three times. It stays recorded until new
evidence — not new aesthetics — reopens it.** Sequencing remains a live call:
WO-03 brings Supabase Postgres regardless, and the auth swap may slip behind a
first deploy if time demands.

### Where clean-sheet and reality diverge

**Hosting.** Render ships fastest. Azure Container Apps matches the owner's
stated cloud preference and is the more marketable line on a CV about to be
used in a real job hunt — at the cost of real ops surface (registry,
networking, identity) that Render hides. A deliberate trade, not a technical
one.

**Hosting has a career dimension.** Render ships fastest. Azure Container Apps
matches the owner's stated cloud preference and is the more marketable line on
a CV about to be used in a real job hunt. That is a deliberate trade, not a
technical one — make it consciously.

## Stack verification against official docs — 2026-08-27

Every vendor claim below was checked against the vendor's own documentation,
not recalled. Seven findings change the plan; three confirm it.

### F1 — Render has no outbound IPv6; Supabase direct connections are IPv6-only

Supabase's connection guide: direct connection (port 5432) is **"IPv6 by
default; IPv4 with add-on"**, while both Supavisor modes are "IPv4-only on
all tiers". Render does not support outbound IPv6. **The direct connection
string will not work from Render at all.**

Options, in preference order:
1. **Supavisor session mode (port 5432, IPv4)** — supports prepared statements,
   correct for a persistent server. Free.
2. Supavisor transaction mode (6543) — requires disabling prepared statements.
3. IPv4 add-on — Pro plan and above only, paid.
4. Host somewhere with native IPv6 (e.g. Fly.io) and keep the direct connection.

This is a deploy blocker, not a tuning detail. Decide it before WO-07.

### F2 — We run asyncpg, which is documented to fail on BOTH Supabase poolers

`app/core/database.py` runs **two engines**: sync `psycopg` for the app, and
async **`asyncpg`** for the fastapi-users auth layer. Supabase's own docs state
transaction mode *"does not support prepared statements"*, and asyncpg uses
them by default. Open Supabase issue #39227 reports asyncpg failing on
*both* poolers — `DuplicatePreparedStatementError` on transaction mode, and
connection timeouts on session mode under burst — with `statement_cache_size=0`,
UUID statement names, `NullPool` and a compute upgrade all failing to fix it.

**Fix: drop asyncpg entirely.** SQLAlchemy 2.0's `postgresql+psycopg://`
dialect serves both `create_engine` and `create_async_engine`, so one driver
covers the sync app and the async auth layer. psycopg 3.3.4 is already pinned;
asyncpg can be removed from the lockfile. This also collapses
`async_database_url()`'s driver translation into a no-op.

Set `prepare_threshold=None` if transaction mode is ever used — psycopg3
auto-prepares after a threshold and would hit the same wall.

### F3 — No connection pool sizing is configured

`create_engine` is called with only `pool_pre_ping=True`. SQLAlchemy defaults
to `pool_size=5, max_overflow=10` — so **two engines can open 30 connections
from a single instance.** Supabase Nano (free) allows 60 direct / 200 pooler
clients on 0.5 GB RAM. One instance is survivable; two are not. Set explicit
`pool_size`/`max_overflow` before deploying more than one replica.

### F4 — Supabase free tier has NO automatic backups

Not "daily only" — none. Daily backups with 7-day retention start at Pro;
PITR is a separate paid add-on that replaces daily backups. Nano also caps at
0.5 GB RAM and 500 MB database. **WO-07's off-site backups are the only backup
that will exist at beta**, which promotes it from hygiene to prerequisite.

### F5 — Render's free tier spins down after ~15 minutes of inactivity

Fatal for an hourly hunt worker. Background workers start at **$7/month**.
Budget the worker as paid from day one, or accept that the hunt cycle only
runs when something else happens to wake the service.

### F6 — Cloudflare Pages is for static exports; full-stack Next.js goes via Workers

Cloudflare's current framework guide routes full-stack Next.js (SSR, RSC,
server actions, route handlers, middleware) to **"vinext on Workers"**; Pages
covers static exports.

**We are fine, but only by luck.** The frontend is three files
(`layout.tsx`, `page.tsx`, `login/page.tsx`) — a client-rendered SPA with no
dynamic routes, no `next/image`, no cookies, no server actions, no route
handlers. It hits none of Next.js's documented static-export blockers. Add
`output: 'export'` to `next.config.ts` and Cloudflare Pages is correct.

Constraint to record: **the frontend must stay static-exportable.** The first
server action or `cookies()` call silently moves us onto Workers.

### F7 — Sentry captures request bodies by default

`send_default_pii` defaults to `False`, but the FastAPI integration still
captures "request details including HTTP method, URL, headers, form data, and
JSON payloads" plus database queries. **On this app that means CV text, job
descriptions and JWTs can land in Sentry.** Configure scrubbing and a
`before_send` filter as part of WO-05 — this is a GDPR surface, not just noise.

### Confirmed as planned

- **`FOR UPDATE SKIP LOCKED`** — Postgres docs: *"any selected rows that cannot
  be immediately locked are skipped"*, explicitly endorsed to *"avoid lock
  contention with multiple consumers accessing a queue-like table"*. The
  documented caveat (an inconsistent view of the table) is exactly what a work
  queue wants. Correct choice.
- **Resend** — 40 MB per email after base64 encoding, max 50 recipients. Our
  three PDFs are nowhere near it. Free tier is 3,000/month and **100/day**;
  Pro is $20/month for 50,000. Free covers beta; the daily cap is the one to
  watch.
- **Supabase Storage** — 50 MB max file size on Free. CV PDFs are far below it.

## AI configuration — settled by measurement

| Decision | Value | Evidence |
|---|---|---|
| Matching model | `glm-5.1`, temperature 0.0 | Beat mistral-small (systematically generous, forwards 61%) and mistral-large (compresses range). **The cost half of that comparison used stale prices — see below** |
| Tailoring model | `glm-5.1`, temperature 0.3 | **UNVALIDATED — never quality-tested.** WO-02 |
| Prompt caching | Already live, 99% hit in-run | Cached $0.26/M vs $1.40/M input — an 81% discount |
| Batching | **Rejected** | −8pts on top matches; destroys cache (90%→38%); real saving only 14% |
| Embeddings | **Not in the stack** | Failed recall as a selector on two models |
| Dead-band | `[13, 25)`, re-score once and average | Measured score SD 5.5 → ±11 at 95% |
| Measured cost | **~$4.51/user/month** (~22% of a €19 price point) | scoring $0.00358/job, tailoring $0.01047/package, at verified prices |

### Price correction, 2026-08-27 — and which verdicts survive it

The cost work above was built on $0.60/$0.11/$2.20 per M (input/cached/output).
Z.ai's own pricing page lists GLM-5.1 at **$1.40 / $0.26 / $4.40** — 2.3× on
input, 2× on output. Every absolute dollar figure previously published for this
project was low by ~2.1×.

**What survives, because it is ratio-driven and the ratios barely moved:**

| conclusion | old | verified | holds? |
|---|---|---|---|
| cache discount | 82% | 81% | yes |
| output vs cached input | 20× | 17× | yes |
| output share of scoring bill | 73% | 70% | yes |
| batching rejected (−8pts on top matches) | — | — | yes, quality-based |
| embedding funnel rejected (recall) | — | — | yes, quality-based |

**What does NOT survive and must be re-run:** the two verdicts whose *cost* leg
compared GLM against Mistral at the stale GLM price. Mistral's prices were
correct; GLM's were 2.3× low, so the two-tier triage stack that "cost 3% more"
may now be materially cheaper. Both verdicts also have a quality leg — Mistral
Large compresses the score range (GLM 88→72 at the top, 5→25 at the keep line),
the same pathology that killed batching — and **that leg is price-independent
and still disqualifying.** Re-run the cost arithmetic before treating the cost
claim as settled; do not re-run the quality finding.

This is the second time this project has published numbers resting on
unverified vendor pricing. It is the whole argument for WO-05's per-call cost
recording: measure spend from the invoice-bearing response, never from a
remembered price.

### Two architectural laws that came out of testing

**1. Prompt prefix order is load-bearing.**
`rubric → profile → CV → job`. Everything that varies goes LAST. The rubric
(~1,437 tok) and CV (~1,066 tok) are 67% of every call and byte-identical, so
the provider caches them at 82% off. Reordering the prompt so anything varies
earlier is a ~47% cost regression wearing the costume of a refactor. Any PR
that touches prompt assembly must state the cache-hit rate before and after.

**2. Every field the model emits must have a consumer.**
Output is billed at 20× the cached input rate and caching can never touch it —
it is now ~65% of the scoring bill. `cover_note` was generated on every scored
job, stored, serialized over the API, and rendered by no component: 20% of the
scoring bill for nothing. Adding a field to a response schema is a recurring
cost, not a one-off.

## Invariants

Carried forward from CLAUDE.md; these are load-bearing and enforced in tests.

1. Original CV immutable — written once, read-only forever.
2. All AI output addresses the seeker ("Your…"), never "the candidate".
3. Nothing sent without explicit approval — approve match → review draft → send.
4. **Zero fabrication in tailoring** — every fact traces to the original CV.
   *Currently enforced by prompt text only. WO-01 makes it a control.*
5. Profession-agnostic — queries derive from each user's CV.
6. Per-user isolation — `user_id` required and keyword-only; the unsafe
   unscoped call is a `TypeError` at import time.
7. Job postings are a shared pool — per-user state lives in `match_results`
   and `applications`, never on `job_postings.status`.
8. Outbound content carries the sender's identity — assert on the artifact
   (AI prompt, email payload, PDF), not on row ownership.

## Known defects

Ranked by product impact, not by effort.

**D1 — No country routing in sourcing (severity: highest).**
Every enabled source is queried for every user regardless of country. A Malmö
user queries Reed (a UK board) with `locationName=Malmö` and gets zero rows;
a UK user would query the Swedish government API with "London". The pool that
survives is whatever is country-agnostic — the generic remote feeds.

Measured on the live pool (399 postings, one SE user in Malmö):

```
  jobicy        156        USA                    73   <- largest bucket
  arbeitnow     131        (none)                 25
  workingnomads  43        Remote job             17
  jobtech        38        Berlin                 12
  remotive       18        Malmö                  11   <- the user's city
  careerjet      13        Stockholm               9
  reed            0        UK                     10
  adzuna          0
```

83% of the pool comes from three country-agnostic remote aggregators, while
the Swedish government source that should dominate for a Swedish user
contributed 38 rows. **Scoring quality is downstream of pool quality — no
amount of model tuning fixes a pool that does not contain the right jobs.**
This is the top-priority fix. See WO-06.

**D2 — Fabrication invariant is unenforced.** Every test touching tailoring
mocks `tailor_application`. WO-01.

**D3 — In-process scheduler blocks horizontal scaling.** WO-04.

**D4 — No observability, no off-site backups, never deployed.** WO-05, WO-07.

**D5 — Email verification and password reset routers written but unmounted.**

## Retired

- **Adzuna** — 0 rows contributed; a non-blocking token-bucket pacer maintained
  for nothing. Revisit only at US/AU expansion, where aggregators matter.
- **Teamtailor** — never configured (`TEAMTAILOR_SITES` empty), 0 rows.
- **`cover_note`** — dead output field, 20% of scoring cost. Remove (WO-08).
- **Batching** — measured and rejected; do not revisit without new pricing.
- **Embedding funnel** — measured and rejected as a selector on two models.

Deleting a source is a config and code change, not just a config change —
leaving dead scrapers registered means every future refactor pays to maintain
them.
