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
- **Pricing:** £10–15/mo holds. AI ≈ $0.30–1.20/user/mo realistic; board APIs
  free through beta; storage a rounding line.

## Phases

**Phase 0 — Foundations:** Neon Postgres migration (SQLAlchemy config + data
copy), fastapi-users auth skeleton, CI (ruff + tsc + flow test + build),
CV storage → Vercel Blob.

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

## Known gaps today

- Composio unconnected (needs platform key); email applies via Resend/browser
- teamtailor scraper without API key
- Old queue matches carry glm-4.6-era scores (mixed calibration) — fresh hunts
  are all 5.1
- Frontend runs as dev server; no production build yet
- Tests are mocked-flow only; no CI
