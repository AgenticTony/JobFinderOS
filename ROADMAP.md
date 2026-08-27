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
  650MB). Rationale: the shared pool is correct on its own merits — job
  postings are public facts, not user data, so storing one copy keeps
  cross-board dedupe working and every user's view equally fresh.
  CORRECTED 2026-08-26: the old rationale here read "per-user scraping dies
  on Adzuna free caps at ~3 users." That was Adzuna-shaped and wrong as a
  general claim — Adzuna has contributed ZERO rows to the live pool, and
  Reed alone (2,000 req/hr) carries hundreds of daily UK hunters. The cap
  matters only in markets where aggregators ARE the backbone (US/AU — see
  International expansion), not in SE/UK. Shared FETCH (query-subscription)
  is an efficiency win, not a scaling necessity; shared STORAGE is right
  regardless.
- **Stack (lean-beta first):** Cloudflare Pages (static frontend, free,
  commercial-OK — Vercel Hobby forbids commercial use) + Supabase free tier
  (500MB Postgres + 50k-MAU auth + 1GB CV storage — auth vendor included
  while free lasts) + Render worker $7 (the only mandatory bill: always-on
  scheduler/scraper; free tiers sleep and kill the 06:30 hunt). GLM rides the
  founder's existing Z.ai yearly plan during beta; move to API billing when
  paying users arrive (~$0.30–1.20/user/mo).
- **AI provider — DECIDED 2026-08-27: stay on GLM (Z.ai) for now.**
  Z.ai is a Chinese provider and the matcher sends full CV text to it. That
  is a third-country transfer of personal data under GDPR — but ONLY once
  the data belongs to someone else. Today the system holds exactly one CV:
  the founder's own. Own data, own choice, no controller obligations.
  TRIGGER TO REVISIT — before the first CV that is not the founder's.
  Not "at launch", not "when convenient": the obligation attaches the moment
  a third party's CV enters the system, including a friendly beta tester.
  At that point either (a) move EU users to an EU-resident model, or (b)
  keep GLM with SCCs + a transfer impact assessment + sub-processor
  disclosure in the privacy policy, and accept that some SE/UK users will
  decline on principle.
  Researched option (a) — CORRECTED to verified prices/facts 2026-08-27
  (the original passage was priced on a generation of list prices that no
  longer exists: GLM at $0.60/$2.20):
  - Verified pricing: GLM-5.1 via Z.ai $1.40 in / $0.26 cached / $4.40 out
    (docs.z.ai). GLM-5.2 via Mistral $1.40 / $0.14 cached / $4.40, with a
    1.1x regional surcharge on the EU endpoint — a 99%-cache-hit production
    workload may net CHEAPER EU-resident than China-routed (cache $0.154
    effective vs $0.26). Confirm from recorded per-call usage, not arithmetic.
  - Mistral's OWN models are quality-disqualified, now decisively: Large
    kept 16/16 jobs including ones GLM scored 8, 10 and 12 (re-cost run,
    2026-08-27). That is not range compression; it is a different judgment.
  - Two-tier mistral triage: REVERSED on cost at verified prices (22%
    cheaper in the interleaved test) but STILL REJECTED on quality — it
    buys a 22% saving for a queue 2.5x larger filled with jobs the
    calibrated model rejects. The original '100% recall' was a meaningless
    metric (Small forwarded 62%, Large keeps everything it is handed); the
    correct metric is precision at the keep line / queue inflation.
    Production note: GLM's test cache was 81% because the run interleaved
    three providers; sequential production runs hit 99%, so real GLM cost
    is lower than the comparison showed.
  - The EU-resident GLM path is real, not a proxy: Mistral hosts the MIT
    weights on its own EU/EEA infrastructure (announcement, 11 Aug 2026);
    the sub-processor list contains NO Z.ai and no China/Singapore entity
    — EU inference path is Mistral Compute (France), CoreWeave (EEA),
    Azure (Sweden/Norway). MUST select the EU endpoint explicitly (Google
    is listed for the US endpoint). BLOCKER: tier-gated — 403
    tier_not_allowed on our key (probe, 2026-08-27); identify the
    unlocking tier and cost. Mistral's DPA names SCCs Module 4, no
    training on API data by default, 30-day post-termination deletion —
    a materially better posture than Z.ai's DPA, which never names GDPR
    or SCCs.
  - NOT YET VERIFIED: Swedish-language scoring quality of GLM-5.2, and
    5.2 is NOT the model everything was tuned against. Before any swap,
    re-run scratchpad/variance.py (within-job SD <= 8) and scale.py
    (paired same-run vs GLM across the score range) — a provider change
    is a bigger calibration event than a version bump, and the tier bands
    (80/50/30) plus MATCH_KEEP_MIN_SCORE=25 are calibrated to GLM-5.1.
    Treat it like a prompt change: version it (rescore_backlog.py
    --prompt-version exists for the backlog), and never mix scales in
    one queue.
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

## Public site & account journey (spec; build as Phase 1a)

Route restructure (Next.js App Router groups):
- `/`            landing (public) — marketing, how-it-works, pricing, FAQ
- `/signup` `/login`  auth pages — hit the LIVE Phase-0 endpoints
                 (POST /api/v1/auth/register, /api/v1/auth/jwt/login)
- `/console`     the existing app (moved; client-side guard: no token ->
                 redirect /login; 401 responses -> clear token + redirect)

Landing page sections (content grounded in the competitive research):
1. Hero: "Your job hunt on autopilot — nothing sent without your approval."
   Sub: hunts twice daily, scores every job honestly (show the real
   0-100 + reasoning card), tailors CV + letter per job, YOU press send.
2. How it works: Hunt -> Match (transparent score) -> Tailor -> You send
   (four steps, real UI screenshots of Hunt Pulse / match card / draft)
3. Why different (attacks the documented complaints): flat £12 — no credits,
   no application limits; every document kept forever (paper trail); area +
   language gates (jobs actually in your region, in your language — SE/UK
   launch); original CV never modified; cancel anytime.
4. Comparison table vs $24-40/mo credit-based tools.
5. Pricing: £12/mo flat, £99/yr (2 months free), incl. VAT, refund-friendly
   policy line. (Outcome stats section: placeholder until beta data.)
6. FAQ built from the Reddit complaints: "does it apply without me?" (no),
   "which boards?" (official sources: Platsbanken/JobTech, Reed, Adzuna,
   Careerjet + employer-direct ATS), "what does the score mean?",
   "can I edit the letter?" (yes, always).
7. CTA: Create account -> upload CV -> onboarding wizard -> first hunt.

Account journey: signup (email+password; Google OAuth later via Supabase
auth if adopted) -> CV upload -> existing 5-step wizard (country, area,
languages, remote switches, titles) -> first hunt runs immediately ->
landed in the console with the live Hunt Pulse. Logged-out users hitting
/console are redirected to /login with return-to.

Build order: 1) route split + guard (console untouched, just moved),
2) signup/login pages on the existing endpoints, 3) landing page +
pricing (static content, no backend deps), 4) wizard entry from signup.
Marketing page is deployable to Cloudflare Pages independently of the
SaaS backend.

## Phases

**Phase 0 — Foundations (DONE Aug 2026, CI green):** Postgres-capable
dual-engine DB layer + Alembic (migrate-on-boot for Postgres; initial
migration verified on real Postgres 16) + fastapi-users v15 auth skeleton
(register/JWT/me verified on Postgres AND SQLite) + /health + CV storage
abstraction (local verified; Supabase REST env-gated — Vercel Blob rejected
as undocumented) + GitHub Actions CI (lint, migrations, auth roundtrip,
flow test, tsc, build). Remaining for Phase 0 completion at deploy time:
create the Postgres project (paste the pooled URL into DATABASE_URL) and the
Supabase project (URL + service key) — config-ready, no code changes.
Which Postgres vendor is still open (see docs/MIGRATION.md); if it is
Supabase, these collapse into ONE project rather than two.

**Phase 1a-static — Public marketing site (safe before the schema work;
touches no data model):** route split (/ vs /console), landing + pricing +
FAQ + how-it-works (static content only). Deployable to Cloudflare Pages
independently. Sequencing law from review pass 4: Phase 1b gets more
expensive with each account-touching surface built before it — so NOTHING
that creates or reads an account ships ahead of the schema.

**Phase 1b — Multi-user core (DONE Aug 2026, CI green; see CLAUDE.md
Phase 1b section for the full record):** user_id FKs +
Alembic migration + backfill; every crud query and all 12
get_active_profile() sites scoped to the caller; Depends(current_active_user)
on every route + frontend token layer (findings #2/#4 — the two NOT fixed);
drop the is_active singleton (second upload currently takes over the app);
IDOR checks (draft PDF downloads serve any integer ID); on_after_register
creates the Profile row; per-user rate limiting on AI-spending endpoints;
pin dependencies + lockfile; Dockerfile; account deletion (GDPR). user_id on profiles/matches/drafts/applications;
shared pool + match-time gates + watermarks; embeddings layer; match queue
with reserved slots + login ordering; per-country cron scheduler; frontend
account flows.

**Phase 2 — SaaS operations:** Dockerfile + Render + Vercel deploy, domain/TLS,
CORS lockdown; Composio MailSender (send from user's Gmail); Stripe
(closed beta behind invite codes first, payments on public launch).

**Phase 1c — Account surfaces (only after 1b lands):** signup/login UI on
the Phase-0 endpoints, wizard entry from signup, console auth guard +
token layer. The landing page's CTAs link to these; until 1c exists the
public site collects interest (waitlist) instead of creating accounts.

**Phase 3 — Launch:** GDPR pack (full-delete cascade incl. Composio teardown,
data export, retention; review Z.ai as CV-data processor), outcome-stats
section on the landing page (real beta numbers), Sentry + scheduler
dead-man alert, beta cohort ramp. (Landing itself built in 1a.)

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

## Query-subscription model (designed; NOT architecturally urgent)

The architecture the fetch layer converges on when built right (not
limit-shaped — it's correct caching):

    search_queries    (normalized_text, country, region, municipality, sources)
                      last_fetched_at, next_fetch_at, subscriber_count
                      UNIQUE(normalized_text, country, region, municipality)
    user_query_subs   (user_id, query_id)
    job_query_hits    (job_id, query_id, first_seen_at)

The scheduler fetches each DISTINCT query once per interval. A user's
candidate set is jobs reachable through their subscriptions, minus what
they've already matched. Consequences:
- API cost scales with distinct (query x location) — saturates; ten users
  chasing the same roles cost one fetch
- AI cost scales with users x new jobs — never saturates (the real driver)
- New user signing up against an existing query gets INSTANT results
- Popular queries refresh most often = freshest data where demand is

Build trigger — CORRECTED 2026-08-26. The old trigger (">3 concurrent UK
users") was Adzuna-shaped: it assumed one source's 250/day free tier was
an architectural constraint. It is not. Eight of nine sources have no
meaningful ceiling, and the live DB confirms Adzuna has contributed ZERO
rows. Reed alone (2,000 req/hr) supports ~250 eight-query user-runs per
hour — hundreds of daily-hunting UK users before the first real limit.

So this is an EFFICIENCY AND UX optimization, not a scaling necessity:
- dedupes identical fetches across users chasing the same roles
- instant onboarding results from an already-warm query

Real trigger: MEASURED pressure — Reed or JobTech limits actually being
approached, or onboarding latency demonstrably costing conversions.
Plausibly hundreds of users away. Design the entities now (migration is
purely additive); do not build it out of momentum.

Corollary, from the same correction: when ONE dependency of nine is the
constraint, replace or demote the dependency — do not reshape the system
around it. Adzuna was demoted to best-effort in c120a20; the blocking
`time.sleep(4)` pacer it forced into the request path is gone.

AI cost levers (the real budget): retrieve-then-rerank (embeddings filter,
GLM top-slice only, 3-5x reduction); batch 5 jobs/call (amortizes the
~1,900-token rubric); prompt caching if Z.ai supports it (rubric is
byte-identical, ~40% of input tokens). Together: $3.00 -> $0.50-1.00
per user/month = 93-97% gross margin at £10-15.

## International expansion — official-APIs-only, per-market redundancy

The market sequence is SE (live) -> GB (built, awaiting a walkthrough) ->
US -> AU. Adding a market is a `SOURCE_PACKS` entry, geo data, a
`COUNTRIES` query-language entry, and live-testing every scraper
(lesson #4 — docs always differ from reality). Days per market, not
weeks, BECAUSE the two-country + profession-agnostic discipline forced
every generalization early.

### The principle: source redundancy is PER-MARKET

Sweden is the easy case — Platsbanken (JobTech) is one official API
covering essentially the whole market, so aggregators are garnish. The
UK is similar: Reed carries it, which is why Adzuna is redundant there.

The US and AU invert this. Their dominant boards are CLOSED:
- Indeed's Publisher API was shut down in 2023 — no self-serve keys,
  enterprise partnership only
- Seek (AU #1) has no public API; Jora is Seek-owned, same answer
- LinkedIn, Glassdoor, ZipRecruiter, Dice — no public APIs

So in the US and AU the AGGREGATORS ARE THE BACKBONE, not the garnish.
There is no Reed-equivalent to fall back on.

CONSEQUENCE FOR ADZUNA: demoted for the UK (correctly), but it is
market access for US/AU — its multi-country support is already in the
scraper (COUNTRY_CODES has US/DE/FR/NL; AU available). Careerjet is
locale-driven, so en_US / en_AU are one dict entry each.

=> The free-tier ceiling stops being "a limit on one redundant source"
   and becomes a limit on the international backbone. THIS is when the
   commercial-terms conversation with Adzuna becomes real — at US/AU
   expansion, not before. Ask then: volume tiers, per-request cost, AND
   whether their ToS permits caching/redistribution across end users
   (Careerjet worth confirming too; Reed and JobTech are open).
   That ToS answer also gates the shared-fetch layer above.

### US pack (researched Aug 2026)

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

COVERAGE CAVEAT: USAJOBS is FEDERAL ONLY — no state, municipal or private
sector. So US private-sector reach rests on Adzuna + Careerjet + ATS-direct.
This is the market where paid Adzuna quota most plausibly pays for itself,
and the reason not to promise US users the parity Swedes get from
Platsbanken. Verify actual coverage on a real US CV before pricing the market.

### AU pack (outline — needs the same research pass as US)

- Tier 1: Adzuna AU + Careerjet (en_AU) + existing remote feeds
- Seek / Jora: no public API (Seek owns Jora) — same stated tradeoff as
  LinkedIn/Indeed
- TO VERIFY: Workforce Australia (federal) — does it expose an API the way
  USAJOBS does? If yes it is the AU equivalent of the JobTech anchor.
  Unresearched; do not assume.
- Tier 2: ATS-direct works globally — Greenhouse/Lever/Ashby slugs for
  AU employers are the same mechanism as the US tier
- Compliance: AU Privacy Act (lighter than GDPR); Stripe Tax covers GST

### Per-market checklist (any new market)

1. SOURCE_PACKS entry + geo.py regions/cities + COUNTRIES query_language
2. Live-test EVERY scraper against real data (never trust the docs)
3. Verify coverage on a real CV from that market before promising parity
4. Compliance: data-protection regime + AI provider residency for those
   users (see the Z.ai/EU transfer question — it recurs per market)
5. Tax: Stripe Tax config for the jurisdiction

## Known gaps today

(verified 2026-08-26 23:20 — check before trusting)

> **Auth/vendor decision SETTLED (2026-08-27):** `MIGRATION.md` records
> the Supabase consolidation as DECIDED — sequence MIG-WO0…MIG-WO5
> (numbered distinctly from this queue's WO-01…WO-14; WO-03 here absorbs
> MIGRATION.md when it runs). Nothing started yet.

- Composio unconnected (needs platform key); email applies via Resend/browser
- 2 rows still on `legacy-unversioned` (transient API errors during the
  re-score; both junk, scores 8 and 18) — the rest of the queue is uniform
  on m2-62c2452b with score AND prose from the same sampling run
- Frontend runs as dev server; no production build deployed
- NOT deployed anywhere: backend is a launchd agent on one Mac. Dockerfile
  builds in CI but has never been deployed.
- Backups are on the SAME DISK as the database — off-site copy needed
  before any real user
- Signup incomplete: email verification + password reset routers unmounted
  (password policy + auth rate limits DID ship in 96b4cd7)
- Z.ai (GLM) processes CV text in China — a third-country transfer under
  GDPR for EU users. Mistral Large 3 (EU-resident) benchmarked cheaper;
  decision open, see the compliance note
- No observability: no Sentry, no error tracking, no metrics

RESOLVED since this list was written: CI exists (.github/workflows/ci.yml,
3 jobs); tests are no longer mocked-flow-only — 67 passing across 4 files
(units, multi-user, calibration, flow), each fix revert-checked against
production code.
