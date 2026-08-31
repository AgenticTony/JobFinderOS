# JobFinderOS — Complete Project Index

> Purpose: file-by-file map of this repo, same format as `docs/TALENTHIVE_INDEX.md` (which indexes the
> `talenthive/` reference clone). Read this to find where anything lives before grepping.
> Indexed: 2026-08-30 · HEAD `79dcfcb` · post-WO-07 deploy, MIG-WO1 executed, MIG-WO2/WO-03 remaining.
> Verified stats: `backend/app` 8,840 lines (69 files) · `frontend/src` 4,981 lines TS (19 files) + 143 CSS ·
> 217 test functions · 11 Alembic migrations · 8 registered scrapers.

---

## 1. What JobFinderOS Is

The job-seeker inversion of TalentHive. One CV on file → jobs continuously scraped (SE + UK) →
AI match against the CV (GLM via Z.ai) → user approves → AI tailors CV + cover letter under a
three-layer anti-fabrication guard → user reviews → send (email w/ 3 PDFs via Resend, or browser/manual).

```
scrape (8 sources) → dedupe → per-user gates (location/language/freshness) → store
  → AI match vs CV (glm-5.1, anchored rubric, temp 0, dead-band re-score)
  → user approves match in UI
  → AI tailors CV + cover letter (fabrication Layers A/B/C)
  → user approves draft → send
```

Product/pricing/invariants live in `PRD.md` / `CLAUDE.md` / `docs/work-orders/`.

## 2. Architecture (as deployed 2026-08-28, WO-07)

```
Frontend   Next.js 16 static export (out/)          → Cloudflare Pages  jobfinderos.pages.dev
API        FastAPI, Render free web, frankfurt      ← /api/v1/*  (Docker, boot migrations)
Hunt worker one-shot cron image (Dockerfile.hunt)   → Render cron  schedule 0 6,18 * * * UTC
DB         Supabase Postgres EU — Supavisor SESSION pooler, psycopg3 sync+async
CV storage Supabase Storage private bucket "cvs"    (dev: backend/uploads/ on disk)
AI         GLM glm-5.1 @ Z.ai (openai-compat)       (Mistral EU endpoint armed as config switch)
Email      Resend (apply emails w/ 3 PDFs)
Local dev  launchd uvicorn 127.0.0.1:8000 + nightly backup launchd 04:30 (ops/)
```

Scheduler is deliberately OUT of the API process (WO-04): the cron worker claims a
`SystemLock` row ("hunt", TTL 45 min) before every scheduled hunt.

## 3. Top-level map

| Path | What it is |
|---|---|
| `backend/` | FastAPI + SQLAlchemy 2 app, Alembic, tests, scripts (§4) |
| `frontend/` | Next.js 16 console + landing, static export (§5) |
| `ops/` | Deploy/backup/verify scripts + launchd plists (§6) |
| `docs/` | Work orders, deploy runbook, marketing research, source notes, indexes (§7) |
| `agent/skills/`, `.agents/skills/` | Vendored Supabase skill packs, pinned by `skills-lock.json` (gitignored tooling) |
| `talenthive/` | Read-only reference clone; indexed in `docs/TALENTHIVE_INDEX.md` instead |
| `.remember/`, `.playwright-mcp/` | Agent-session memory + browser-automation artifacts (ephemeral, gitignored) |

---

## 4. Backend — `backend/`

FastAPI + SQLAlchemy 2 (sync engine; async engine only for fastapi-users auth), pydantic v2,
per-user tenancy enforced at routes. Run: `cd backend && .venv/bin/uvicorn app.main:app --port 8000`.
Tests: `PYTHONPATH=. .venv/bin/python -m pytest tests/ -q` (217 test functions).

### 4.1 Entry & API

| File | Role | Key contents |
|---|---|---|
| `app/main.py` | FastAPI entry | Lifespan: `init_db()` (Alembic under Postgres advisory lock 821371), `start_scheduler()`, taxonomy warm-up. Routers under `/api/v1/{profile,pipeline,jobs,matches,applications,settings,account}` + fastapi-users `/auth/jwt /auth /users` with login/register rate-limit deps. `GET /health` (DB readiness), CORS, global exception handler. |
| `app/users.py` | fastapi-users v15 auth | Separate async engine (aiosqlite/psycopg). `UserManager`: password validation (8–72 bytes), `on_after_register` creates the Profile row. Bearer + JWT (7-day). `current_active_user`. |
| `app/api/deps.py` | Shared dependencies | `register/login_rate_limit` (keyed by submitted email); `get_authenticated_user`; `get_user_profile` (404 if no CV); `owns_or_404` IDOR guard — fails closed on NULL; `set_user_context_middleware` stamps `ai_service.current_user_id` ContextVar for per-user cost rows. |

### 4.2 `app/api/v1/`

| File | Role | Routes |
|---|---|---|
| `account.py` | GDPR | `DELETE /account/delete` (cascade + CV file + rate-limit purge; shared job pool stays), `GET /account/export` (portability JSON). |
| `applications.py` | Draft review + send | `POST /draft/{job_id}` (prepare; needs approved match; rate-limited), `GET /drafts`, `GET /draft/{id}`, `GET /draft/{id}/download/{cover-letter,cv}` (PDF, sanitized filenames), `PUT /draft/{id}` (edits), `POST /draft/{id}/submit` (email/browser/manual), `GET /`, `GET /{id}`, `POST /{id}/retry`. All `owns_or_404`. |
| `jobs.py` | Shared job pool | `GET /` (filter status/source/q), `GET /{id}`, `POST /` (manual add), `PATCH /{id}/status` (refuses re-queue to "new" when a match exists), `DELETE /{id}` (per-user aware; physical delete only when unreferenced). |
| `matches.py` | Match results + approval | `GET /` (tier/recommendation/min_score/pending filters), `GET /{id}`, `POST /{id}/decision` (approve/reject), `POST /run` (background matching, rate-limited, requires CV). |
| `pipeline.py` | The main button | `POST /run` (validates sources vs registry, threadpool `run_pipeline`, returns scrape summaries + top matches), `GET /status` (sources, stats, recent runs, matching flag, honest next-run time). |
| `profiles.py` | CV + onboarding | `POST /upload` (PDF → extract → store → AI extraction), `GET /me`, `PUT /me`, `POST /onboarding` (country/region/municipalities/radius/queries; SE-only occupation codes validated server-side), `POST /suggest-queries` (AI, mode field/adjacent/widen), `GET /geo`, `GET /status`. |
| `settings.py` | Integrations | `GET /integrations` (Composio connections), `POST /integrations/composio/connect`. |

### 4.3 `app/core/`

| File | Role | Key contents |
|---|---|---|
| `config.py` | pydantic-settings | All env: DB URL, GLM (model `glm-5.1`, thinking toggle, Mistral EU block), Resend, matching caps (`MAX_JOBS_PER_MATCH_RUN=200`, keep-min 25, deadband 13, time budget 420s), `FABRICATION_JUDGE` kill switch, Sentry, per-source keys, scheduler/CORS. `_production_guards`: DEBUG=false requires strong AUTH_SECRET + Postgres + non-wildcard CORS. |
| `database.py` | Engine/session/init | Sync engine + `get_db`; `init_db()`: Alembic upgrade under advisory lock; legacy-SQLite stamp-then-upgrade path. |
| `dburl.py` | URL normalization | `normalize_postgres_url` (asyncpg → psycopg forms), `async_database_url`. Dependency-free so alembic env can import it. |
| `dedupe.py` | Dedupe keys + fuzzy pair rule | `dedupe_key_for` (md5 title+company+location); `likely_same_job` (municipality + title-token overlap ≥0.6 + employer link). |
| `orm.py` | Declarative Base | 13 lines, no app imports. |
| `ratelimit.py` | Sliding-window limiter | In-process, thread-safe. Buckets: cv_upload 5/h, ai_suggest 10/h, hunt 12/h, match_run 12/h, draft_prepare 20/h, auth_register 5/h, auth_login 10/15min. `clear_user()` for GDPR. |
| `telemetry.py` | Sentry, gated + PII-scrubbed | `scrub_pii` before_send drops bodies/cookies, redacts `_PII_KEYS` (cv_text, cover_letter, full_name…). |
| `timeutil.py` | `utc_now()` | Naive-UTC replacement for deprecated `datetime.utcnow()`. |

### 4.4 `app/models/` (10 tables)

| File | Table | Key columns / constraints |
|---|---|---|
| `user.py` | `users` | fastapi-users UUID table + `display_name`. |
| `profile.py` | `profiles` | `user_id` UNIQUE NOT NULL (one per user). Immutable CV block (cv_text/path), AI-extracted JSON (skills/roles/education/keywords), preferences, onboarding (country/region, `municipalities` JSON, `search_radius_km`, `occupation_codes` JSON, queries, languages). |
| `job.py` | `job_postings` | **No user_id — shared pool.** source, `dedupe_key` (indexed), description, salary, status lifecycle, published/scraped timestamps. |
| `match.py` | `match_results` | user_id+job_id NOT NULL, `UniqueConstraint(user_id, job_id)`. score/tier/reasoning/skill lists/recommendation/confidence, decision, per-user `dismissed_reason`, `prompt_version` (indexed). |
| `draft.py` | `application_drafts` | cover_letter/tailored_cv/changes_summary, status (drafting/ready/submitted/failed), WO-01 guard columns: `fabrication_findings` (JSON), `fabrication_retries`, `fabrication_blocked`. |
| `application.py` | `applications` | method (email/browser/manual), status (queued/sent/failed/manual_pending), subject/body/target_email, sent_at, error. |
| `ai_usage.py` | `ai_usage` | One row per AI call: kind, model, `endpoint` (residency audit), tokens, `cost_usd` (micro-dollars), `user_id` nullable (system calls). |
| `scrape_run.py` | `scrape_runs` | Audit per source run: status, jobs_found/new, matches_created, error. |
| `scrape_watermark.py` | `scrape_watermarks` | Delta-scrape watermarks; unique (source, query, scope). |
| `system_lock.py` | `system_locks` | Portable SQLite+Postgres claim lock ("hunt"), TTL-stealable. |

Also `app/crud/__init__.py` (221 ln): `list_matches` (excludes dismissed), `set_match_decision`
(never writes shared job.status), per-user-aware `delete_job`, `get_stats`.

### 4.5 `app/schemas/`

| File | Role |
|---|---|
| `application.py` | Draft/application DTOs; `DraftResponse` parses JSON columns + fabrication fields. |
| `common.py` | `parse_json_list` / `dump_json_list` helpers for JSON Text columns. |
| `job.py` | `JobCreate` (manual), `JobResponse`, `JobDetailResponse`, `JobStatusUpdate`. |
| `match.py` | `MatchDecision`, `MatchResponse`, `MatchWithJobResponse`. |
| `pipeline.py` | `PipelineRunRequest` (sources validated vs registry at boundary; `max_matches` server-clamped = cost-DoS guard), run/summary DTOs. |
| `profile.py` | `OnboardingRequest`, `ProfilePreferencesUpdate`, `ProfileResponse`. |

### 4.6 `app/services/`

| File | Role | Key contents |
|---|---|---|
| `ai_service.py` (725 ln) | ⭐ All AI ops | `extract_profile`, `match_job` (anchored rubric, raises on unparseable JSON so job stays "new"), `tailor_application` (zero-fabrication rules, language-of-posting, correction regen), `suggest_search_queries`, `judge_fabrication` (fresh-context, FAILS CLOSED), `matching_prompt_version()` (sha256, `m2-…`), cost accounting + `record_ai_usage`, `_tier_for_score` (80/50/30 bands). |
| `matcher_service.py` (591 ln) | AI matching loop | Dead-band sampling policy (`resolve_samples`, `needs_another_sample`), per-user locks, newest-first window (500), language + exact + fuzzy dedupe gates, cheap gates (exclude keywords, no description) before spend cap, below-keep-min → auto-pass w/ `dismissed_reason`, IntegrityError reconciliation. |
| `pipeline.py` (615 ln) | Scrape→filter→store→match | Delta watermarks (jobtech only, 24h overlap), per-user scrape context (user_id required), location/radius gates, country routing via `blocked_for_user`, `build_union_contexts` (scheduled union hunt per country), maintenance sweeps (stale postings, stale pending matches). |
| `worker.py` (220 ln) | Cron worker (`python -m app.worker`) | Atomic `claim_hunt` (SystemLock, TTL 45 min, INSERT fallback), `run_scheduled_hunt` (union scrape + per-user matching), `--once` mode for Render cron. |
| `draft_service.py` (404 ln) | Draft create/review/submit | Tailor → Layer A `unsupported_claims` → WO-02 judge → regenerate-with-correction (max 2) → block; findings persisted. `submit_draft`: email w/ 3 PDFs (tailored CL + tailored CV + original CV); original CV immutable. |
| `fabrication.py` (503 ln) | Layer A deterministic checker | `Claim` atoms (year/organisation/credential/metric/technology), EN+SV org stopwords, credential equivalence groups (MSc↔Masterexamen), technology vocabulary, metric regexes. `unsupported_claims` → tiers high/advisory. |
| `apply_service.py` | Email retry path | Rebuilds approved tailored PDFs, falls back to plain email for legacy rows. |
| `cv_service.py` | CV upload orchestration | Validate/extract/store/AI-extract; `get_active_profile` keyword-only `user_id` (no unscoped fallback); matcher context builder. |
| `pdf_service.py` | Render application PDFs | fpdf2 `_UnicodePDF` (font fallback chain), markdown cleanup. |
| `language_filter.py` | Posting language gate | DE/FR/ES/IT/NL/SV/DA-NO/FI markers; English/unknown always pass. |
| `country_lexicon.py` | Location→country resolution | City/country→ISO sets; EEA bloc; `blocked_for_user` policy (WO-06). |
| `occupation_taxonomy.py` | Arbetsförmedlingen concepts | 3,262 occupation-name concepts, label↔code resolution, server-side `validate_codes`; fetch failure never cached. |
| `geo.py` | Municipality centroids | ~45 SE towns; `geo_plan` shared by scraper + store gate; `RADIUS_SUPPORTED_MUNICIPALITIES`. |
| `source_packs.py` | Country→boards mapping | SE=[jobtech, careerjet, shared-remote]; GB=[reed, careerjet, shared-remote]; shared remote = remotive/jobicy/workingnomads/arbeitnow. |
| `scheduler.py` | Optional in-process scheduler | ENABLE_SCHEDULER-gated; routes through the worker's claim-locked hunt; `next_run_from_fixed_times`. |
| `storage.py` | CV storage abstraction | `LocalStorage` (uploads/) / `SupabaseStorage` (private REST, service key). |
| `composio_service.py` | Composio REST client | Connections list + OAuth initiate; failures degrade, never 500. |
| `file_service.py` | PDF validation/extraction | pdfplumber; 5MB max, `%PDF` header. |
| `scrapers/base.py` | Scraper framework | `NormalizedJob` model, `strip_html`, `extract_apply_email`, `BaseScraper` ABC. |
| `scrapers/__init__.py` | Registry | **8 sources**: arbeitnow, remotive, jobicy, workingnomads, jobtech, reed, adzuna, careerjet. (Teamtailor removed WO-08.) |

### 4.7 Scrapers (auth & behavior)

| File | Source / pack | Auth |
|---|---|---|
| `jobtech.py` | Platsbanken — SE primary | Key optional; municipality/radius/occupation-field filters; delta `published-after`; pagination 3/5/10 pages. |
| `reed.py` | Reed — GB | Basic-auth key; 100/query; per-employer cap 10. |
| `careerjet.py` | Careerjet — SE+GB | Key + declared public IP + Referer. |
| `adzuna.py` | Adzuna — **in no pack** (US/AU expansion backbone, WO-08) | app_id+key; token-bucket pacer 25/min. |
| `arbeitnow.py`, `remotive.py`, `jobicy.py`, `workingnomads.py` | Shared remote pack | No keys. |

### 4.8 `app/data/`

| File | Role |
|---|---|
| `geo.py` | Static onboarding geography: SE+GB countries, all 21 Swedish län + 12 GB regions → municipalities. |

### 4.9 Alembic (`alembic/versions/`, 11 migrations)

Initial schema → per-user FKs + composite unique → user_id NOT NULL backfill → prompt_version +
dismissed_reason → fabrication guard columns → profile municipalities → ai_usage table →
system_locks → scrape_watermarks → profile search_radius_km → profile occupation_codes.
`env.py` reads DATABASE_URL from env (never the ini), normalizes async→sync driver forms.

### 4.10 Tests (`tests/`, 217 test functions, ~6,000 lines)

| File | What it verifies |
|---|---|
| `conftest.py` | Owns DATABASE_URL before any app import; refuses to run against the live DB (born from the 243-match drop incident). |
| `test_units.py` (2,730 ln) | Gates + state machines: location, dedupe, language, submit flow, dead-band protocol, per-user dismissal, tenancy layer, strip-dead-surface, country routing, JobTech pagination. |
| `test_multiuser.py` (1,273 ln) | Auth gate, two-user isolation, IDOR, rate limits, GDPR erasure/export, outbound identity, judge fail-closed, cost-DoS clamp. |
| `test_taxonomy.py` | Occupation label resolution, JobTech occupation units, per-code watermarks. |
| `test_radius.py` | Centroid resolution, position+radius params, reduced radius gate, watermark scope. |
| `test_delta.py` | Watermark lifecycle (backfill vs delta), JobTech delta params, union contexts. |
| `test_calibration.py` | Pins prompt hash (`m2-57a0f692`) + tier bands; opt-in live variance (RUN_CALIBRATION=1). |
| `test_flow.py` | End-to-end with mocked GLM; standalone-runnable. |
| `test_fabrication.py` | Opt-in live Layer-B judge (RUN_FABRICATION=1); catches become fixtures. |
| `bench_models.py` | Manual GLM model bake-off (not a test). |
| `fixtures/fabrication/` | 5 regression fixtures incl. two live catches. |

### 4.11 Scripts & build

| File | Role |
|---|---|
| `scripts/bootstrap_user.py` | One-time: first account + stamp NULL-user_id rows. |
| `scripts/rescore_backlog.py` | Re-score legacy matches with the shared sampling protocol; ~$3/~73 min for 243 rows; --dry-run. |
| `Dockerfile` | API image: python:3.12-slim, lockfile install, non-root, alembic on board. |
| `Dockerfile.hunt` | Same layers, `CMD python -m app.services.worker --once` (Render cron). |
| `requirements.lock` | 71 pinned deps. `.env.example` documents all env vars incl. Mistral EU switch block. |

---

## 5. Frontend — `frontend/` (Next.js 16 + React 19 + Tailwind 4, static export)

4,981 lines TS across 19 files. Scripts: `dev`, `build` (guarded static export), `start`, `lint`.
Design system "The Hunting Console": ink `#0c0e12`, one amber accent `#f5a524`, Geist Sans/Mono.
**No Zustand store exists** (dependency declared but unused) — all state is useState in the console page.

### 5.1 Routes (`src/app/`)

| File | Role |
|---|---|
| `layout.tsx` | Fonts, `class="dark"`, metadata ("twice-daily hunts"). |
| `page.tsx` (438 ln) | Marketing landing: three acts, RadarScope hero, screenshots, GuardReceipt fact-guard demo, pricing-terms inversion. |
| `login/page.tsx` | Login/register toggle; JWT → localStorage `jfos-token`. |
| `app/page.tsx` (1,641 ln) | **The entire authenticated console** (view-state routing, no URL routes): Dashboard, Matches (awaiting/approved), Applications (review&send / sent), Profile, Settings. 60s status poll, 8s match poll, dirty-guard on profile inputs. |
| `globals.css` | @theme tokens, radar-sweep, reduced-motion kill-switch. |

### 5.2 Components (`src/components/`)

| File | Role |
|---|---|
| `HuntPulse.tsx` | Signature funnel strip: Hunted→Matched→Awaiting→Drafts→Sent, live counts, +N deltas, countdown. |
| `Sidebar.tsx` | Collapsible nav rail (persisted), pending-count chips, user chip, sign-out; exports `View` union. |
| `MatchCard.tsx` | Expandable match card: company tile, ScoreRing, salary/posted/language chips, skill columns, approve/pass actions. |
| `OnboardingWizard.tsx` (837 ln) | 5-step setup: country → location (multi-municipality + SE radius) → languages → job titles (AI suggestions + SE taxonomy chips) → confirm. |
| `ScoreRing.tsx` / `TierBadge.tsx` | Score gauge + tier pill (80/50/30 bands). |
| `CvUpload.tsx` | Drag-drop PDF upload with escalating status copy. |
| `NextHunt.tsx` | Countdown + LiveDot (breathing amber). |
| `AdzunaAttribution.tsx` | ToS-required "Jobs by Adzuna" link. |
| `landing/{RadarScope,GuardReceipt,Reveal}.tsx` | Landing-only: radar backdrop, fact-guard receipt mock, scroll reveals. |

### 5.3 lib / types / config

| File | Role |
|---|---|
| `lib/api.ts` (291 ln) | Typed axios client. `api` (60s) + `slowApi` (600s for pipeline/CV); Bearer interceptor; 401→logout; `apiErrorMessage` unwraps backend `detail`; blob PDF downloads; all endpoint helpers. |
| `lib/utils.ts` | `cn`, tier config, `scoreColor`, **`parseUtcDate`** (offsetless-UTC fix), `timeAgo`. |
| `types/index.ts` | Full domain model (Job/Match/Draft/Profile/Onboarding/Stats/PipelineStatus…). |
| `next.config.ts` | `output:'export'` (CF Pages); build guard rejects localhost API URL in prod build. |
| `package.json` | next 16.2, react 19, framer-motion 12, tailwind 4.2; **zustand 5 declared, never imported**. |
| `public/screenshots/` | hunt-pulse.png + match-detail.png (used on landing). |

---

## 6. ops/

| File | Role |
|---|---|
| `backup.sh` | Nightly DB + CV backup (launchd 04:30): pg_dump or sqlite .backup, 30-day rotation, CV mirror with absence-date retention, MIG-WO0 off-site step (OFFSITE_BACKUP_TARGET rsync/rclone). |
| `com.jobfinderos.backend.plist` | launchd: local uvicorn, RunAtLoad+KeepAlive. |
| `com.jobfinderos.backup.plist` | launchd timer for backup.sh. |
| `deploy_frontend.sh` | CF Pages direct upload via wrangler (git push does NOT deploy frontend). |
| `migrate_sqlite_to_supabase.py` | MIG-WO1 data migration (snapshot source, per-table coercion, post-insert sequence fixes, row-count + queue-invariant verification, `--force` guard). |
| `provision_supabase_storage.py` | Idempotent private `cvs` bucket creation. |
| `verify_deployment.sh` | 6 post-deploy gates (health, CORS, auth roundtrip, frontend, API URL inlined in bundle). |

---

## 7. docs/

| File | Scope | Status |
|---|---|---|
| `work-orders/README.md` | The execution queue, status ledger, standing rules (test-first, revert-check, grep-prove, static-export constraint). | All P0s + WO-02/03/04/05 done; WO-07 deployed 2026-08-28. |
| `work-orders/WO-01…WO-17` (14 files) | Fabrication harness · tailoring judge · worker split · observability (ai_usage) · country routing · dead-surface strip · psycopg3 · Supavisor connection · billing (Paddle MoR) · hunt cadence/trial · career-site discovery · pricing (€24.99/mo, no credits, no auto-renew) · cancellation feedback. | Per-file statuses in README ledger; WO-13/14/15/17 not started. |
| `deploy/WO-07-runbook.md` | Deploy runbook (Render + CF Pages + Supabase). | DEPLOYED 2026-08-28, verifier 6/6; records the sync:false secrets incident. |
| `marketing/competitive-positioning.md` | Landing research: competitor billing pain, anti-volume stats, copy rules (never name competitors), must-not-claim-yet list. | Research backing WO-16. |
| `sources/fantastic-jobs-future-note.md` | Fantastic.jobs ATS feed evaluation: pricing, cautions, build-vs-buy (direct public ATS adapters first). | Noted 2026-08-30, revisit at real-user volume. |
| `TALENTHIVE_INDEX.md` | Full index of the talenthive/ reference clone. | 2026-08-24. |

---

## 8. Root files

| File | Role |
|---|---|
| `README.md` | Local quick-start, pipeline diagram, tier table. |
| `PRD.md` | Product definition: audience, invariants, never-do list. |
| `ARCHITECTURE.md` | Two-process shape, stack table, defect register (D/F items). |
| `ROADMAP.md` | Market position, expansion triggers, lean-beta costing. |
| `MIGRATION.md` | Supabase consolidation: MIG-WO0..WO5 sequence + 9 recorded traps. Status: WO0 mechanics done (one human step: real backup target), **WO1 executed 2026-08-28** (797 rows, CI matrixed sqlite+postgres), WO2 (Supabase Auth) + WO3 (RLS) remaining build work, WO4 overtaken by WO-07 deploy, WO5 decided 2026-08-30 (stay GLM beta; Mistral EU switch armed). |
| `CLAUDE.md` | AI-session project memory + binding engineering standards (adversarial self-review, test-before-fix, grep-prove claims). |
| `render.yaml` | Blueprint: API web (free, frankfurt, /health) + hunt cron. |
| `.github/workflows/ci.yml` | 4 jobs: Backend (sqlite+postgres matrix: ruff I,F → alembic → tests), Docker (both images), Render blueprint assertions, Frontend (tsc + build + export check). |
| `skills-lock.json` | Pins the two vendored Supabase skill packs. |

---

## 9. Excluded from this index by design

`talenthive/` (own index), `.remember/` + `.playwright-mcp/` (ephemeral agent artifacts),
`agent/` + `.agents/` (gitignored skill packs), `backend/uploads/` (CVs = personal data),
`*.db` files, build outputs (`node_modules/`, `.next/`, `out/`, `.venv/`, caches).

---

## 10. Findings from the indexing pass (all verified by execution)

1. **CI is RED on main** (last 5 runs failed, as of 2026-08-30). Two independent causes:
   - `ruff check --select I,F app/ tests/ scripts/` fails: unsorted imports + unused imports in
     `tests/test_delta.py`, `tests/test_radius.py`, `tests/test_taxonomy.py` (the most recent
     test files). Reproduces locally.
   - Render blueprint assertion: `ci.yml:166` asserts hunt cron schedule `"0 * * * *"` but
     `render.yaml:98` says `"0 6,18 * * *"` (changed in `ab69906`, 2026-08-29, after ci.yml's
     last edit). One of the two is stale; the deployed cadence and `HUNT_TIMES_UTC=06:00,18:00`
     say twice-daily is intended — the CI assertion is the stale side.
2. **Dead export**: `scrapers/__init__.py:35` lists `TeamtailorScraper` in `__all__` but never
   imports it (import raises AttributeError). WO-08 removed the class but not the `__all__` entry.
3. **Doc drift in CLAUDE.md**: "9 sources" (8 registered since WO-08); "42+ tests" (217 functions);
   open-items says "236 legacy-unversioned matches" while the AI section says 243.
4. **Config drift**: `.env.example` shows `MAX_JOBS_PER_MATCH_RUN=25`, code default is 200;
   `config.py` comments still reference glm-4.6 timings; `adzuna.py` defines
   `RETRY_DELAY_SECONDS = 6` but the retry sleeps 0.5s.
5. **Frontend**: `zustand` declared but never imported (and `next.config.ts` comment claims the
   app uses "zustand state" — stale); `npm start` can't work with `output: 'export'`; `npm run lint`
   has no ESLint config on disk; stray 0-byte `frontend/jobfinderos.db`.
6. **Minor dead code**: `core/dedupe.py` has an uncalled inner `core_title()` and a duplicate
   `_AGENCY_MARKERS` (the matcher_service copy is the live one); `fabrication.py` stopwords
   contain two duplicate entries; `ApplicationsView` has an unused `onPrepare` prop.
7. **Known-good patterns worth copying** (from indexing, not problems): conftest's live-DB guard,
   `owns_or_404` fail-closed IDOR, keyword-only `user_id` everywhere, judge fail-closed on
   unparseable output.
