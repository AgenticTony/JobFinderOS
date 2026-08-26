# JobFinderOS — Project Memory

> Auto-loaded context for AI coding sessions. Keep this current as the project evolves.
> Last updated: 2026-08-24

## What this is

**JobFinderOS** — an operating system for job hunting. The job-seeker inversion of
[TalentHive](https://github.com/AgenticTony/TalentHiv) (Anthony's recruiter-side AI screening
tool, cloned read-only at `talenthive/`). One CV on file → many scraped jobs → AI match →
approve → AI-tailored CV & cover letter → review → send.

**Owner:** Anthony Foran (GitHub: AgenticTony). Solo project, personal use, two-country
scope (Sweden + UK) for multi-user testing later.

**Full TalentHive reference index:** `docs/TALENTHIVE_INDEX.md` (file-by-file map of the
foundation, including how the GLM screening engine works).

## Stack & run commands

- **Backend:** FastAPI + SQLAlchemy 2 + SQLite (`backend/jobfinderos.db`), Python 3.13, pydantic v2
  - Run: `cd backend && .venv/bin/uvicorn app.main:app --port 8000` (venv exists, deps installed)
  - API docs: http://localhost:8000/docs
- **Frontend:** Next.js 16 + React 19 + Tailwind 4 + Zustand, Node 24
  - Run: `cd frontend && npm run dev` → http://localhost:3000
  - Type-check: `npx tsc --noEmit`
- Tests: `cd backend && .venv/bin/python -m tests.test_flow` (mocked-AI e2e: match→approve→apply)

## Pipeline (implemented & verified)

```
scrape (9 sources) → dedupe → per-user location filter → store
  → AI match vs CV (score/tier/skills/apply-recommendation, "you" voice)
  → user approves match in UI
  → AI tailors CV + cover letter for THAT job (ApplicationDraft, user edits)
  → user approves draft → send: email w/ 3 PDFs (Resend) or browser/manual
```

Status values: jobs `new→matched→approved|rejected|dismissed→applied`;
drafts `drafting→ready→submitted|failed`.

## KEY INVARIANTS — never break these

1. **The original CV is immutable.** `Profile.cv_text` + stored PDF are written exactly once
   (at upload), read-only forever. Every job-specific version lives in its own
   `ApplicationDraft` row. Documented in cv_service/draft_service docstrings.
2. **All AI output talks TO the job seeker** ("Your tech stack…"), never "the candidate".
   Exception: cover letters are first-person (sent TO employers). Enforced in prompts.
3. **Nothing is sent without explicit user approval** — approve match → review draft → send.
   LinkedIn/Indeed ToS prohibit bot applies; browser path = open posting + copy cover note.
4. **Zero fabrication in tailoring** — every fact must trace to the original CV (prompt-enforced).
5. **Profession-agnostic platform** — queries/titles derive from each user's CV via onboarding,
   never hardcoded to tech. Global defaults for targeted boards are EMPTY until onboarding.

## AI setup (GLM via Z.ai)

- Endpoint: `https://api.z.ai/api/coding/paas/v4` (OpenAI-compatible), key in `backend/.env`
- **Model: `glm-4.6` with `thinking: disabled`** — ~5s/match. (glm-4.5 was 75–107s/match —
  these are reasoning models; thinking eats token budget + latency. `GLM_THINKING=enabled`
  switches back, max_tokens auto-raises to 6000 so responses aren't empty.)
- httpx timeout: connect 10s / read 180s; max_retries=1
- Three AI ops: `extract_profile` (CV→structured profile), `match_job` (job↔CV, inverted
  TalentHive scoring: +12/+6/0/−8, tiers excellent/good/stretch/poor 80/50/30),
  `tailor_application` (CV+cover letter per job + changes summary), `suggest_search_queries`
  (onboarding, country-aware, ~45s latency is normal)
- Robustness: markdown-fence JSON extraction, reasoning_content fallback, broad exception
  catches so AI failures never 500 endpoints (errors surface on rows/UI instead)

## Job sources (all live-tested with real data)

| Source | Countries | Auth | Quirks & limits |
|---|---|---|---|
| jobtech (Platsbanken) | SE | none (key optional) | Official AF API. Keyless OK for light use. Top 100/query. |
| teamtailor | SE | none | Needs `TEAMTAILOR_SITES=slug1,slug2` (career-site JSON feeds). UNCONFIGURED currently. |
| careerjet | SE+GB | basic-auth key + **declared IP** + **Referer header** | Aggregator. IP-bound to home IP 31.211.228.218 (portal allows ≤8 IPs; CIDR counts per-IP). Referer must be `https://www.aifullbokad.se/find-jobs/` (declared site). If 403 returns later = home IP rotated → update portal. sv_SE + en_GB both work despite "country-limited" note. |
| reed | GB | basic-auth key (username=key, empty password) | 2,000 req/hr. Response: `{"results":[…]}`, field is `jobDescription` (docs said `description`), dates dd/mm/yyyy, no jobUrl in search (details endpoint fallback works). Max 10/employer/run (bulk posters spam location variants). |
| adzuna | GB | app_id + app_key | Free tier 25/min·250/day·1000/wk. 503 = rate bucket → retry w/ 6s backoff, 4s pacing between requests. One search PER QUERY (OR-combining trips limits). Usage: 6 hits/run — hourly scheduler would just exceed weekly cap; 2-3 runs/day is plenty. |
| arbeitnow, remotive, jobicy, workingnomads | shared | none | Plain public APIs/feeds. workingnomads has no id field — derive from URL slug. |

**Source packs** (`app/services/source_packs.py`): SE = jobtech, teamtailor, careerjet + 4 shared;
GB = reed, adzuna, careerjet + 4 shared. Explicit `sources` in pipeline API overrides pack.

## Secrets map (post-cleanup 2026-08-25)

- **Active secrets live ONLY in `backend/.env`** (gitignored — verified). That file is the
  single source of truth: GLM (Z.ai), REED_API_KEY, ADZUNA_APP_ID/APP_KEY, CAREERJET_API_KEY,
  CAREERJET_REFERER. Not yet configured: JOBTECH_API_KEY (optional), TEAMTAILOR_SITES,
  RESEND_API_KEY + APPLY_FROM_EMAIL (email auto-apply disabled until set).
- `backend/.env.example` = safe placeholder template (committed).
- `talenthive/` = local-only reference clone, **gitignored** from this repo; its
  `.env.example` was placeholder-cleaned locally on 2026-08-25.
- ⚠️ The TalentHiv GitHub repo's *history* still contains the old real keys (private repo —
  acceptable risk). Rotation advice if ever going public: Azure storage key first (unused by
  JobFinderOS), then GLM/Reed/Adzuna/Careerjet (update backend/.env same day each rotates).

## Onboarding system (built & verified)

Wizard (`OnboardingWizard.tsx`) shows after first CV upload: country cards (SE/GB with flags)
→ region→municipality cascading dropdowns (geo data: `app/data/geo.py`, 21 SE län/33 Skåne
kommuner, 12 GB regions; served via `/api/v1/profile/geo`) → remote-only toggle → **search
strategy question** → AI-suggested queries in two labeled groups ("From your experience"
chips + "Worth a look" rows with per-query why) → confirm → saves to profile (`onboarded=1`)
and auto-fires first targeted pipeline. Re-openable from Profile tab ("Edit setup").
Profile fields: country/region/municipality/remote_only/search_queries (+ migration in init_db).

**DESIGN DECISION — search strategy, never age:** queries derive from CV titles by default
(`field` mode). `adjacent` adds near-neighbour variants; `widen` mode decomposes the CV into
underlying capabilities and maps to job families the CV never names — each pivot carries a
second-person "why" citing CV evidence (verified live: casino/regulated background →
Supporttekniker, IT-säkerhet, Teknisk projektledare etc., in Swedish, ~8s). The mode is
ALWAYS the user's explicit choice in the wizard — never inferred from age or any protected
characteristic. (Age-conditional steering was considered and rejected: it would automate the
very discrimination AF's Kortrapport 2026:1 documents — callback rates drop from the 40s
despite no age-performance link. Strategy ≠ demographics. Titles-from-CV remains the default
because it's the strongest signal for most users.)

**Per-user scrape context** flows: profile → `build_scrape_context()` → scrapers (jobtech
queries, reed keywords+location, adzuna what/where, careerjet locale sv_SE/en_GB) +
`passes_location_filter()` universal gate (remote jobs & location-less jobs pass; out-of-area
never stored → never matched).

**Anthony's active setup:** SE · Skåne län · Malmö · 8 junior fullstack/.NET/backend/AI
queries (bilingual, AI-suggested from his CV).

## Frontend map

Single-page dashboard (`src/app/page.tsx`, ~1000 lines, all views inline):
tabs Dashboard / Matches / Applications (draft review + sent history) / Profile.
- `MatchCard`: ScoreRing + TierBadge + skill chips (You have / They want / transferable) +
  Approve/Reject → "Prepare application"
- `DraftCard`: AI changes summary, editable cover letter + tailored CV textareas, Save,
  PDF download buttons (`/download/cover-letter`, `/download/cv` — auto-save-before-download),
  "Approve & send by email" / "Approve & apply in browser"
- Run Pipeline = fast scrape (~10s) + background matching + 8s polling (streams matches in;
  `matching_running` flag from `/api/v1/pipeline/status`)
- `AdzunaAttribution` renders "Jobs by Adzuna" on adzuna-sourced cards (ToS requirement)
- PDFs: `app/services/pdf_service.py` (fpdf2, unicode font fallback for å/ä/ö; multi_cell
  needs `new_x="LMARGIN"` or cursor sticks at right margin)

## Hard-won lessons (don't re-learn these)

1. **Async endpoints + blocking IO = frozen server.** All heavy work (AI calls, PDFs,
   pipeline) goes through `run_in_threadpool`.
2. **Unhandled exceptions → Starlette 500 → NO CORS headers** → browser shows misleading
   "CORS error". Catch everything in pipeline/matcher; errors belong in response summaries.
3. **GLM latency is prompt-size × reasoning.** Small test prompts lie (3s); real match
   prompts were 75-107s. Fix = glm-4.6 + thinking disabled (~5s) + capped prompt sizes
   (5k chars CV/job). `max_tokens=2000` with thinking ON returns EMPTY (reasoning eats budget).
4. **Live-test every blind-built scraper** — docs always differ from reality (Reed's results
   wrapper + jobDescription; Adzuna's rate buckets).
5. **Python `hash()` is per-process randomized** — never use for persistent IDs (MD5 instead).
6. **Adzuna 503 = rate limit** (not content); same query flips 200/503 under load.
7. **SQLite + autoflush=False**: flush each insert so same-run dedup queries see prior rows.
8. **Per-job commits in matcher** so live polling UI streams results instead of one dump.
9. **Match time budget** (`MATCH_TIME_BUDGET_SECONDS=420`) < frontend's 600s axios timeout.

## Current state (2026-08-24)

- DB: ~600 jobs (≈580 are OLD broad-scraped `new` backlog — **candidate for purge** so queue
  only holds targeted Malmö jobs), 97+ matched, 1 draft submitted (Koppla test), 1 excellent
  match at 92 (remote fullstack internship)
- All 9 sources verified live; onboarding complete for SE user
- Servers were running on :8000/:3000 during the session

## Decided architecture (parked until multi-user build)

- **Connected email via Composio** (decided 2026-08-25): the multi-user email-apply path uses
  each user's OWN email account, connected through Composio (managed OAuth: Gmail + Microsoft,
  covers most SE/UK users incl. hotmail). Rationale: platform ESPs cannot send from consumer
  addresses (@gmail/@hotmail = spoofing); user-owned sending = authentic applications, replies
  land natively in their inbox, zero platform email cost. Build behind a pluggable
  `MailSender` interface (resend | composio | direct-oauth | smtp). Rules: minimal scopes
  (send-only, never read), tokens encrypted at rest, deleted on disconnect, UI states exactly
  what access means. STRATEGIC reason: Composio is the platform's integration LAYER, not just
  email — its tool catalog (calendar, docs, etc.) is the expansion surface for future features
  (interview scheduling, follow-up nudges, CV cloud storage).
- **Pricing economics** (if commercialized): ~$3–4.50/user/month all-in cost at 100 users,
  one run/day; £10–15/month price point ≈ 70% margin; free tier (weekly runs + blurred
  matches) for conversion; LTV ~£20–45 → organic acquisition channels only (AF-adjacent).
  Shared job data + per-query scrape caching keeps DB and free API tiers viable at scale.

## Model bake-off (measured 2026-08-25, real CV + 3 jobs, thinking disabled)

glm-4.6: 7-11s/call, 3 concurrent, ~29k calls/day capacity. Scores: 88/20/8.
glm-5.1: ~6.2s/call, 10 concurrent, ~139k/day. Scores 72/22/8 — ordering preserved,
systematically lower (thresholds are calibrated to 4.6; recalibrate before switching).
glm-5.2: ~6s, 10 concurrent. 62/18/8 — lower still.
glm-4-plus: 429 insufficient quota on current plan — unavailable.
Follow-up evaluation (same day, user-approved vs junk jobs, 2 runs each):
4.6 is NOISY — same job scored 92/68 run-to-run (24-pt swing); 5.1 stable
within 1-6 pts. Both rank approved > junk correctly every run. Conclusion:
4.6's weakness is variance, not direction; the free fixes are temperature 0.3→0
and rubric anchors in the prompt. Re-run the harness after anchoring; 5.1's
case is consistency + speed + concurrency. Caveat: user approvals partly echo
4.6's on-screen scores (circular labels) — clean signal = approved→sent→reply.
DECISION (same day): switched the whole service to glm-5.1 (GLM_MODEL in
.env/config). After rubric anchors + temperature 0 were added to the matching
prompt, 4.6 STILL swung 84/42 on a borderline job; 5.1's worst spread was 13
and its anchored scores land correctly in the existing tier bands (top approved
82-85, mid 65-72, borderline 42-55, junk 8-15) — existing thresholds hold
(keep-min 25, excellent >=80). 5.1 also ~30% faster + 10x concurrent; user's
max yearly plan makes cost moot. Tailor/profile/suggest calls also run on 5.1
(better writing, single calls) at their own temperature (0.3 via default).
Remaining upgrade levers: batch-5-per-call, Z.ai tier.

## Phase 0 — enterprise foundations (DONE, Aug 2026, CI green)

- Dual engines off one DATABASE_URL: sync app engine (psycopg) + async auth
  engine (asyncpg/aiosqlite) — fastapi-users v15 SQLAlchemy adapter is
  async-only (official docs). Neon pooled URL works as-is.
- Alembic owns the Postgres schema (backend/alembic; env.py reads DATABASE_URL,
  renders fastapi-users GUID as portable sa.Uuid). Postgres envs migrate on
  boot via init_db; SQLite dev keeps create_all + light column migrations.
- Auth skeleton (app/users.py): users table (UUID), POST /api/v1/auth/register,
  /api/v1/auth/jwt/login (Bearer JWT, 7-day), /api/v1/users/me. AUTH_SECRET in
  backend/.env (generated). Verify/reset routers await the mailer (Phase 2).
  Frontend auth UI = Phase 1.
- /health: {status, database, version} — uptime-ready.
- CV storage abstraction (app/services/storage.py): STORAGE_BACKEND=local
  (default, verified) | supabase (official REST docs). Vercel Blob REJECTED:
  undocumented REST, SDK-only. CV upload path routed through get_storage().
- CI (.github/workflows/ci.yml): backend job = ruff (I,F) + alembic upgrade on
  a Postgres 16 service + schema assertions + TestClient auth roundtrip on
  Postgres + flow test (now runs with EMPTY GLM key — draft_service mocked
  too); frontend job = tsc + next build. Green on main.

## Review-fix status (Aug 2026, verification pass 3)

ACCURATE STATUS: 20 of 22 review findings fixed and execution-verified
(CI run 32936771722 + live CORS check + 15-test pytest suite). Route auth
(findings #2/#4: Depends(current_active_user) on business routes + frontend
token layer) is DELIBERATELY DEFERRED TO PHASE 1b — it is NOT done, and the
single-user CORS lockdown is the interim mitigation, not auth. Multi-user
verdict from review: "single-user production grade: yes; multi-user schema:
no" — user_id columns = 0, get_active_profile call sites = 12 (global
singleton; second CV upload takes over the app), draft PDF downloads are
IDOR-open. That IS Phase 1b per ROADMAP. Phase 1b additions from the review:
IDOR checks on integer-ID downloads, on_after_register creates Profile,
per-user rate limiting, dependency pinning/lockfile, Dockerfile, account
deletion (GDPR). Residuals from verification pass 3 (fixed 2026-08-26):
utc_now() helper replaces all datetime.utcnow() (28 deprecation warnings → 0,
naive-UTC storage semantics preserved); storage.py stale docstring corrected;
NextHunt/HuntPulse raw new Date() → parseUtcDate.

## Pipeline gates & hygiene (all enforced every run)

Scrape-time gates (in order): location (area pass; remote/locationless only when
include_remote) → language (non-spoken languages dropped; English always passes) →
freshness (published_at older than MAX_POSTING_AGE_DAYS=30 dropped) → dedupe
(same-board IDs/URLs, plus cross-board normalized title+company via
jobs.dedupe_key — app/core/dedupe.py, backfilled at startup).

Matcher gates: exclude keywords → language backlog gate → cross-board dupe guard
(same dedupe_key already matched = dismissed) → AI score < MATCH_KEEP_MIN_SCORE=25
= job dismissed, NO match row (never enters the queue).

Sweeps (run_pipeline `_maintenance_sweeps`): stale unmatched postings >30d →
dismissed; pending matches older than MATCH_STALE_DAYS=30 → auto-passed.

Onboarding: country → region/city → languages (step 3) → remote switches
("Include remote jobs" opt-in + "Remote jobs only") → job titles (strategy
field/adjacent/widen). Profile.languages / include_remote drive the gates.

Tailor language rule: documents in the posting's language; mixed → dominant;
unclear → first profile working language.

Ops: backend runs under launchd agent `com.jobfinderos.backend`
(ops/com.jobfinderos.backend.plist in-repo; installed at
~/Library/LaunchAgents) — RunAtLoad + KeepAlive, logs /tmp/jobfinderos-backend.log.
Frontend is `npm run dev` on demand. Real fix later: deploy backend so hunts run 24/7.

## Open items / next steps

- [ ] Purge old broad-scraped `new` jobs (~580) from pre-onboarding era
- [ ] UK test user walkthrough (Profile → Edit setup → GB) — everything ready
- [ ] Optional: Teamtailor slugs (e.g. staffing agencies), JobTech free key for production
- [ ] Optional: scheduler (`ENABLE_SCHEDULER=true`, keep ≥2h interval for Adzuna weekly cap)
- [ ] Multi-user refactor: auth + per-user rows (reuse TalentHive User model lineage),
      SQLite→Postgres, per-user scheduler, Composio connected email (see decided architecture)
- [ ] Later phase: native per-source location params, digest email, saved searches,
      Playwright ATS drivers for structured portal applies (design agreed: staged,
      human-confirmed screenshot before submit), skills-first CV presentation toggle
      (de-emphasize chronology; presentation not fabrication — for the 55+ market)
- [ ] Placeholder-clean `talenthive/backend/.env.example` if TalentHiv ever goes public
- [x] Git: initialized + pushed 2026-08-25 (commit c768aa4, main → private repo
      github.com/AgenticTony/JobFinderOS). Pre-push audit: zero secrets staged;
      backend/.env, talenthive/, *.db, uploads/, node_modules excluded.
