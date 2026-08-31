# Work orders

Sequenced execution queue. One work order = one session (per the owner's
"one session = one epic" rule). Each is written to be handed to a builder
without this conversation as context.

## Document ownership — read this before adding a doc

Doc sprawl produced a real contradiction once already (ROADMAP said Neon,
MIGRATION said Supabase, CLAUDE.md said both). Every document below owns
exactly one question. If a fact belongs in two places, it belongs in one
and is *referenced* from the other.

| Doc | Owns | Does not own |
|---|---|---|
| `PRD.md` | what we are building, for whom, what it must never do | how it is built |
| `ARCHITECTURE.md` | the technical shape, stack, AI config, known defects | when things happen |
| `ROADMAP.md` | market position, expansion strategy, triggers | technical decisions |
| `CLAUDE.md` | AI-session context and engineering standards | product or architecture decisions |
| `docs/work-orders/` | sequenced execution, acceptance criteria | rationale (link to the doc that owns it) |
| `README.md` | how to run it locally | everything else |
| `MIGRATION.md` | *retire on WO-03 completion* — absorbed into WO-03 | |

## Numbering collision — RESOLVED 2026-08-27

`MIGRATION.md` now prefixes its Supabase sequence **MIG-WO0…MIG-WO5**; this
directory keeps **WO-01–WO-14**. Historical record of the collision:

| name | in MIGRATION.md | in this directory |
|---|---|---|
| WO3 / WO-03 | RLS policies | Supabase Postgres migration |
| WO4 / WO-04 | observability | worker / scheduler split |
| WO5 / WO-05 | inference residency | observability + AI cost rows |

This directory introduced the second scheme and should be the one to move.
Proposed fix (not executed — `MIGRATION.md` is being actively edited by another
session): rename this series to a distinct prefix, or absorb MIGRATION.md's
WO0–WO5 into this queue as the single source of sequencing. **Do not cite a
bare "WO3" in either document until this is settled** — always qualify it as
`MIGRATION.md WO3` or `work-orders/WO-03`.

## Queue — open work (board refreshed 2026-08-31)

| # | Work order | Pri | Depends on | Status | Why now |
|---|---|---|---|---|---|
| **WO-14** | Hunt cadence + trial gating | P1 | — | **NEXT UP** | Trial CAC $5.88 → $1.00 by capping *scoring* (not display) at 10/day. Keeps the Hunt button; makes repeat presses a free no-op. Also records the `run_matching` service-clamp gap. **2026-08-31 finding:** the LIVE cron fires every 3h while render.yaml, the runbook and `HUNT_TIMES_UTC` all say 06:00/18:00 — the dashboard's next-hunt countdown lies; reconciling the dashboard schedule belongs inside this WO |
| **WO-10** | Mount email verification + password reset | P2 | — | open | Routers written, never mounted. Beta users forget passwords; without this the only answer is a support channel |
| **WO-13** | Billing + tax posture (Paddle as Merchant of Record) | P1 | WO-07 ✅ | open | Decided. UK has a **zero** VAT threshold for non-established sellers, so a two-country launch means two registrations from the first sale — an MoR removes both. ~54% margin, ~6–7 user break-even |
| **WO-15** | Career-site discovery (self-expanding employer boards) | P2 | WO-06 ✅ | open | Replaces the deleted slug-scraper with a mechanism that finds employer career feeds from Platsbanken application_urls (verified 2026-08-27: 79% carry one, custom-domain /jobs.json probes 4/4). Direct-from-employer inventory competitors don't have; vendor-neutral (Teamtailor JSON Feed now, Greenhouse/Lever later). ToS gate before shipping |
| **WO-16** | Pricing + plan design (€24.99 / €59.97 quarterly) | P1 | WO-13, WO-14 | blocked | Decided. The category's weak point is **billing trust**, not features — AIApply has an F BBB rating over credits-on-top, LazyApply 2.1 over ignored refunds, Sonara auto-renews a €2.95 trial to €23.95. Clean billing is a free differentiator. Owns the price; WO-13 owns the tax posture |
| **WO-17** | Cancellation feedback loop | P2 | WO-13, WO-16 | blocked | The highest-signal moment in the product. Hired users are the only source of genuine 5-star reviews; everyone else is the only honest diagnostic we get. Hard constraint: cancel first, ask after — a survey before the button IS the friction that dominates Jobright's one-star reviews |
| **WO-18** | Location-less duplicate collapse; prefer original-source apply links | P1 | — | **assigned (developers)** | The 2026-08-31 apply-incident post-mortem: one ad × three pool copies scored 55/65/68; the user approved the dead-link copies and rejected the live portal. Full detail: `WO-18-locationless-duplicate-collapse.md` |

Also live on the platform but tracked outside this queue (see CLAUDE.md open
items): **send-from-own-Gmail as a submit `method`** — the Composio
integration is proven and connected in production (2026-08-31); the dispatch
path behind the approval gates is the remaining build.

## Completed (ledger)

Nothing here is to be redone — this is the record of what shipped.

- **WO-01** fabrication harness — 2026-08-27. Post-fix baseline 40% (2/5)
  with ZERO Layer-A false positives; the judge runs in production on every
  draft (2026-08-28). Measurement protocol: FABRICATION_N=20 before/after
  any prompt change.
- **WO-02** tailoring quality validation — 2026-08-28 (rode with WO-01).
- **WO-03** Supabase Postgres migration — 2026-08-28: Alembic chain
  applied, 797 rows migrated with snapshot-verified counts + zero
  invariant violations, sequences fixed, backup.sh does pg_dump.
  Connection: aws-1-eu-west-1 session pooler (the aws-1 prefix was
  WO-12's discovery — not aws-0). MIGRATION.md stays alive only for its
  remaining MIG-WO2 (Supabase Auth) and MIG-WO3 (RLS).
- **WO-04** worker/scheduler split — 2026-08-28 (with the WO-03 wave).
- **WO-05** observability + per-call AI cost rows — 2026-08-28: ai_usage
  rows on every AI call (cost + price-drift + residency audit in one
  table), Sentry gated and PII-scrubbed (F7). MIG-WO0's backup step
  verified locally; the one human step — pointing
  OFFSITE_BACKUP_TARGET at the real target — remains outstanding.
- **WO-06** country routing in sourcing — 2026-08-27.
- **WO-07** deploy + off-site backups — 2026-08-28 (Render API + hunt
  cron + Cloudflare Pages via wrangler; Pages is a DIRECT-UPLOAD deploy,
  not Git-integrated — frontend changes need `wrangler pages deploy`).
  Live-verified 2026-08-31: health, security headers on both tiers, a
  full hunt cycle end-to-end, and the Gmail connect flow in production.
- **WO-08** strip dead surface — 2026-08-27.
- **WO-09** re-score the legacy backlog — 2026-08-31: 241/243 rows on the
  current prompt version; 2 stragglers noted in CLAUDE.md open items.
- **WO-11** drop asyncpg; unify on psycopg3 — 2026-08-27.
- **WO-12** Postgres connection path decision — 2026-08-27.

One standing constraint from the 2026-08-27 audit: **the frontend must
remain static-exportable.** The first `cookies()` call or server action moves
it off Cloudflare Pages and onto Workers (F6).

## Rules for every work order

Carried from CLAUDE.md; these are why the queue is short and the findings
were real.

1. Write the test that would catch the bug **before** the fix.
2. Prove the test catches it — revert the fix against **production code**,
   confirm red, restore. A green test never seen red is not evidence.
3. When a WO claims "every X is now Y", grep to prove it before commit.
4. Tests ship in the same commit as the feature.
5. Green CI does not mean correct.
6. One session on this repo at a time. Concurrent sessions collide, and
   `git checkout -- .` reverts everything, not just yours.
