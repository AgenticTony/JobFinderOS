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

## Queue

| # | Work order | Pri | Depends on | Why now |
|---|---|---|---|---|
| **WO-08** | Strip dead surface (`cover_note`, adzuna, teamtailor) | P1 | — | Cheap. Shrinks what every later WO must carry. −20% scoring cost |
| **WO-06** | Country routing in sourcing | **P0** | — | The product does not currently work for its target user. See ARCHITECTURE D1 |
| **WO-01** | Fabrication harness | **P0** | — | Blocks the first real user who is not the owner |
| **WO-05** | Observability + per-call AI cost rows | P1 | — | Required before deploy. Cost blindness already caused a 2.1× error. **Must include Sentry PII scrubbing** — the integration captures request bodies, i.e. CV text (F7) |
| **WO-04** | Worker / scheduler split | P1 | — | Required before more than one API replica |
| **WO-03** | Supabase Postgres migration | P1 | WO-05 | Absorbs MIGRATION.md. WO0 (off-site backups) is its first action |
| **WO-07** | Deploy + off-site backups | P1 | WO-03, WO-04, WO-12 | Never deployed. Supabase Free has **no** automatic backups (F4), and Render's free tier spins down after 15 min — budget a $7/mo worker (F5) |
| **WO-11** | Drop asyncpg; unify on psycopg3 | **P0** | — | asyncpg is documented to fail on both Supabase poolers (ARCHITECTURE F2). Blocks WO-03 and WO-07 |
| **WO-12** | Decide the Postgres connection path (Supavisor session vs IPv4 add-on vs IPv6 host) | **P0** | WO-11 | Render has no outbound IPv6; the direct connection cannot work (F1) |
| **WO-02** | Tailoring quality validation | P2 | WO-01 | Needs the harness to measure against |
| **WO-09** | Re-score the legacy backlog | P3 | WO-08 | Any prompt change forces it; ~$0.33 at corrected pricing |
| **WO-10** | Mount email verification + password reset | P2 | — | Routers written, never mounted |
| **WO-15** | Career-site discovery (self-expanding employer boards) | P2 | WO-06 | Replaces the deleted slug-scraper with a mechanism that finds employer career feeds from Platsbanken application_urls (verified 2026-08-27: 79% carry one, custom-domain /jobs.json probes 4/4). Direct-from-employer inventory competitors don't have; vendor-neutral (Teamtailor JSON Feed now, Greenhouse/Lever later). ToS gate before shipping |
| **WO-14** | Hunt cadence + trial gating | P1 | — | Trial CAC $5.88 → $1.00 by capping *scoring* (not display) at 10/day. Keeps the Hunt button; makes repeat presses a free no-op. Also records the `run_matching` service-clamp gap |
| **WO-13** | Billing + tax posture (Paddle as Merchant of Record) | P1 | WO-07 | Decided. UK has a **zero** VAT threshold for non-established sellers, so a two-country launch means two registrations from the first sale — an MoR removes both. ~54% margin, ~6–7 user break-even |

**Every P0 is DONE (2026-08-27): WO-01, WO-06, WO-08, WO-11, WO-12.**
WO-01 (fabrication guard) shipped all three layers and its first live
measurement — the judge found real fabrications in 4/5 tailored
documents, so **WO-02 (tailoring quality) is now evidence-backed and
urgent**: no real user until the prompt-side fabrication rate drops.
Then WO-05 (observability) and WO-04 (worker split).

**WO-11 and WO-12 are new P0s from the 2026-08-27 stack audit** and block the
entire deploy path. They are cheap (WO-11 is a driver swap on a stack that
already pins psycopg3) but nothing deploys until they are settled.

One standing constraint from the audit: **the frontend must remain
static-exportable.** The first `cookies()` call or server action moves it off
Cloudflare Pages and onto Workers (F6).

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
