# JobFinderOS — SaaS Roadmap

> Decisions recorded Aug 2026 after the architecture/planning sessions.
> Current state: hardened single-user platform (live), SaaS build not started.

## Where we are (all built, verified on live data)

Pipeline: harvest → gate → store → match/score → decide → tailor → send, with:
- Scrape gates (in order): location (area pass; remote/locationless need
  include_remote opt-in; strictly-local users skip remote boards) → language
  (non-spoken dropped, English passes) → freshness (30d) → dedupe (same-board +
  cross-board title+company key)
- Sweeps: stale backlog dismissal, pending matches auto-pass at 30d
- Matching: glm-5.1, anchored rubric, temperature 0, sub-25 dismissed without
  entering the queue, live streaming to the UI, evaluation harness (user
  decisions as labels + repeat-run consistency) re-run on every model/prompt change
- Decision funnel: Finish applying → New in last 24h → Next decisions;
  Matches pages (Awaiting you / Approved-in-flight); Sent page keeps every
  document retrievable forever
- Ops: launchd agent (auto-start/restart), last-hunt + overdue indicator,
  stats-signature auto-refresh, Composio entity-scoped connection layer
  (Settings page; awaiting platform API key)

## Target architecture (decided)

- **Shared job pool + per-user matching.** Scrape once per country into a
  shared pool (dedupe is global); all personal gates apply at match time with
  per-user last-matched watermarks. Storage is trivial (~13KB/job; 50k jobs ≈
  650MB). Rationale: per-user scraping dies on Adzuna free caps at ~3 users.
- **Stack (lean-beta first):** Cloudflare Pages (static frontend, free,
  commercial-OK — Vercel Hobby forbids commercial use) + Supabase free tier
  (500MB Postgres + 50k-MAU auth + 1GB CV storage — auth vendor included
  while free lasts) + Render worker $7 (the only mandatory bill: always-on
  scheduler/scraper; free tiers sleep and kill the 06:30 hunt). GLM rides the
  founder's existing Z.ai yearly plan during beta; move to API billing when
  paying users arrive (~$0.30–1.20/user/mo).
  Beta ≈ $7/mo. Growing (50–500) ≈ $25–45. 1k users ≈ $250–400 against
  ~£12k revenue. Vercel Pro + Neon is the comfort upgrade once revenue
  justifies it, not a prerequisite (Vercel has no first-party Postgres
  anymore regardless).
- **Cadence:** 2 scrapes/day per country via cron triggers (06:30 local =
  main run, lands fresh matches before morning logins; optional 14:00 top-titles
  light run; weekends 1 run). The 3h interval was single-user-era; lower
  frequency buys query breadth on capped boards (Adzuna 1,000/wk free →
  ~140 queries/run at daily).
- **Query scheduling tiers:** uncapped boards (JobTech free-unbounded,
  Careerjet 1,000/hr) carry full distinct-query breadth (~2.5k terms at 1k
  users); capped boards (Adzuna 2.5k/month free, Reed ~1k/day unpublished)
  get top-N queries by user count. Adzuna/Reed both raise limits on request.
- **AI throughput funnel:** gates → embeddings (job vectors computed once per
  pool job, shared across users; user vector per CV; cosine filter — cuts GLM
  calls ~70–90%) → glm-5.1. Queue: DB-backed match_tasks, 2 batch slots +
  1 reserved interactive slot (tailor/profile never wait behind matching).
  Matching ordered by predicted login time; login bumps priority; live
  streaming UX is the fallback when timing misses. Dormant users (>30d) pause.
- **Model ladder:** 5.1 now (~139k calls/day capacity) → batch 5 jobs/call
  (~5k users) → Z.ai tier bump. 4-plus unavailable on plan; 4.6 retired for
  42-point run variance.
- **Composio:** one platform key brokers all connections; users OAuth their
  own Gmail, filed under per-user entity IDs (already implemented). Sending
  applications through user Gmail = the MailSender follow-up.
- **Pricing (deep-dived Aug 2026):** £12/mo VAT-inclusive launch → £19 once
  outcome stats justify it (grandfather early users); annual ~£99 (2 months
  free) to fight job-seeker churn; explicit refund-friendly policy.
  Unit economics (measured): COGS ≈ £1.10–1.50/user/mo (GLM £0.35–0.65,
  infra £0.25–0.45, Stripe £0.40, boards £0) → ~85% net margin after VAT
  (£9.6 net of UK 20%). Lifetime ≈ 2–4 months (job-seeker churn) → LTV
  ≈ £24–25 profit; CAC must stay <£10 → organic-only at launch (Reddit,
  outcome stats, word of mouth). Break-even: 1 user (beta) / 5 (growth) /
  ~30 (1k-user scale). Competitor scale reality: AIApply 2M registered but
  ~1K paying (0.05-0.1% conversion, $19/mo @ ~30% margin, profits from
  credits); JobRight $39.99; flat pricing at £12 is bottom-of-market with
  best-in-class margin because scraping/OpenAI-retail costs aren't in our stack.

## Phases

**Phase 0 — Foundations (DONE Aug 2026, CI green):** Postgres-capable
dual-engine DB layer + Alembic (migrate-on-boot for Postgres; initial
migration verified on real Postgres 16) + fastapi-users v15 auth skeleton
(register/JWT/me verified on Postgres AND SQLite) + /health + CV storage
abstraction (local verified; Supabase REST env-gated — Vercel Blob rejected
as undocumented) + GitHub Actions CI (lint, migrations, auth roundtrip,
flow test, tsc, build). Remaining for Phase 0 completion at deploy time:
create the Neon project (paste pooled URL into DATABASE_URL) and the
Supabase project (URL + service key) — config-ready, no code changes.

**Phase 1 — Multi-user core:** user_id on profiles/matches/drafts/applications;
shared pool + match-time gates + watermarks; embeddings layer; match queue
with reserved slots + login ordering; per-country cron scheduler; frontend
account flows.

**Phase 2 — SaaS operations:** Dockerfile + Render + Vercel deploy, domain/TLS,
CORS lockdown; Composio MailSender (send from user's Gmail); Stripe
(closed beta behind invite codes first, payments on public launch).

**Phase 3 — Launch:** GDPR pack (full-delete cascade incl. Composio teardown,
data export, retention; review Z.ai as CV-data processor), landing + pricing
page, Sentry + scheduler dead-man alert, beta cohort.

## Competitive landscape (researched Aug 2026)

Market: AIApply ~$24-29/mo + application credits ($639/78d real user bill;
non-refundable, BBB complaints unanswered); JobRight $39.99/mo (opaque,
countdown checkout); Sonara $23.95/4wk (reportedly dead ~2024); LoopCV
free tier + EUR9.99-29; Simplify free extension + $39.99/mo. All US/
English-centric, scrape-based, black-box auto-apply. Top Reddit complaints:
credits/hidden costs, irrelevant targeting, unreliable bots, applications
look alike, no proof of outcomes. Our flat-price + gates + human-approval +
paper-trail design answers every one. Their remaining edge: ATS portal
autofill (LinkedIn Easy Apply) — the parked Playwright-driver item.

**Launch weapon (from this research): outcome tracking.** Add replied/
interview/offer markers on Sent applications -> publish real response-rate
stats, the one proof point no competitor has. Small build, Phase 2.
Also: explicit refund-friendly policy page (attacks the #1 complaint).

## US expansion (researched Aug 2026) — official-APIs-only, like SE/UK

Why competitors scrape: the big US boards (LinkedIn, Indeed, Glassdoor,
ZipRecruiter, Dice) have NO public APIs — coverage-first forces the
expensive stack. Our US pack instead:
- Tier 1 (keys in hand or free): Adzuna US + Careerjet US (existing keys),
  The Muse (free public API, no key), USAJOBS (free key, best-documented
  job API; all federal jobs), existing remote feeds
- Tier 2 (the gem): ATS-direct — Greenhouse/Lever/Ashby PUBLIC official
  JSON endpoints (boards-api.greenhouse.io/v1/boards/{company}/jobs etc.)
  with a curated top-N company-slug list (see jobber OSS). Direct-from-
  employer postings, fresher than aggregators, zero scraping.
- Not in pack (scraping-only boards): LinkedIn, Indeed, Glassdoor, Zip,
  Dice — stated tradeoff, part of the positioning.
- ATS-direct jobs apply via portal URLs → natural fit for the browser-apply
  path and the future ATS autofill tier.

## Known gaps today

- Composio unconnected (needs platform key); email applies via Resend/browser
- teamtailor scraper without API key
- Old queue matches carry glm-4.6-era scores (mixed calibration) — fresh hunts
  are all 5.1
- Frontend runs as dev server; no production build yet
- Tests are mocked-flow only; no CI
