# JobFinderOS — Project Memory

> Auto-loaded context for AI coding sessions. Keep this current as the project evolves.
> Last updated: 2026-08-26

## MANDATORY DEVELOPMENT STANDARDS — read before every task

These are binding rules learned from review passes where bugs shipped that
should have been caught at build time. They are NOT optional.

### 1. Adversarial self-review before every commit
After writing code, re-read the diff looking for what you got wrong:
- **Grep for the old pattern surviving** — if you refactored X to Y,
  `grep -rn "old_pattern"` must return zero relevant hits. Verify, don't trust intent.
- **Verify function boundaries by inspection** — when moving code between
  functions, read the actual function spans (line numbers) to confirm the
  change landed where intended. The temperature=0.0 bug was a change placed
  in `tailor_application` while the comment said `match_job`.
- **Check that every claim in the commit message is true in the code** —
  if the commit says "all call sites scoped", grep to prove it.

### 2. Write the test that would catch the bug BEFORE writing the fix
If you can't describe how the bug would manifest, you don't understand it.
- Assert on the **outbound artifact** (what reaches the outside world),
  not just the row state. The TestOutboundIdentity pattern: two users,
  assert the AI prompt receives the right CV text, the email payload carries
  only the sender's name, Bob's name never appears in Alice's subject.
- **Prove the test catches the bug**: temporarily revert the fix, confirm
  the test fails with a message naming the blast radius, then restore.
  A green test that has never been seen red is not evidence.

### 3. When you say "every X is Y", grep to prove it
A claim about the codebase is a fact claim. Verify by execution:
```
grep -rn "get_active_profile(db)" app/ | grep -v "user_id="
grep -rn "temperature=" app/services/ai_service.py
grep -rn "time.sleep" app/services/scrapers/
```
Do this BEFORE the commit, not after the reviewer finds it.

### 4. Testing is part of building, not an afterthought
Every feature ships WITH its tests in the same commit. The test suite
grows with the codebase. No "I'll add tests later" — that's how the
temperature bug survived three review passes.

### 5. Green CI does not mean correct
CI proves the code runs, not that it's right. The reviewer's method:
prove it's broken → fix it → prove the fix works → prove the test
would catch a regression. That's the standard.

### 6. Function-span verification for any code-moving change
When moving, copying, or parameterizing code between functions:
```python
src = open(file).read()
fn_a = src.index("def function_a")
fn_b = src.index("def function_b")
calls_in_a = re.findall(r"self\._method\([^)]*\)", src[fn_a:fn_b])
# VERIFY the change is in the right function
```
Never trust line numbers from a previous grep — the file may have changed.

### 7. Concurrent sessions on this repo will collide
If another session is working on this repo, changes will be swept up or
reverted accidentally. Serialize sessions or use separate branches. The
`git checkout -- .` command is particularly dangerous — it reverts ALL
uncommitted work, not just yours.

### 8. The test database must NEVER be the live database
`tests/conftest.py` owns `DATABASE_URL` before any app import. Never
override it from a test module. Never remove the conftest guard. The
drop_all() in test fixtures will destroy production data if the binding
drifts. This happened once (recovered from backup); the conftest makes
it structurally impossible now.

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

- **Backend:** FastAPI + SQLAlchemy 2 + SQLite→Postgres (`backend/jobfinderos.db`), Python 3.12, pydantic v2
  - Run: `cd backend && .venv/bin/uvicorn app.main:app --port 8000` (under launchd agent)
  - API docs: http://localhost:8000/docs
  - Docker: `docker build -t jobfinderos-backend backend/`
- **Frontend:** Next.js 16 + React 19 + Tailwind 4 + Zustand, Node 24
  - Run: `cd frontend && npm run dev` → http://localhost:3000
  - Type-check: `npx tsc --noEmit --noUnusedLocals --noUnusedParameters`
- **Tests:** `cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/ -q`
  - All 42+ tests must be green before any commit
  - Flow test: `PYTHONPATH=. .venv/bin/python tests/test_flow.py`
  - Calibration (opt-in, costs API calls): `RUN_CALIBRATION=1 pytest tests/test_calibration.py`

## Pipeline (implemented & verified)

```
scrape (9 sources) → dedupe → per-user gates (location/language/freshness) → store
  → AI match vs CV (score/tier/skills, glm-5.1, anchored rubric, temp=0)
  → user approves match in UI
  → AI tailors CV + cover letter for THAT job (ApplicationDraft, user edits)
  → user approves draft → send: email w/ 3 PDFs (Resend) or browser/manual
```

## KEY INVARIANTS — never break these

1. **The original CV is immutable.** Written once at upload, read-only forever.
   Every job-specific version lives in its own `ApplicationDraft` row.
2. **All AI output talks TO the job seeker** ("Your tech stack…"), never "the candidate".
3. **Nothing is sent without explicit user approval** — approve match → review draft → send.
4. **Zero fabrication in tailoring** — every fact must trace to the original CV.
5. **Profession-agnostic platform** — queries/titles derive from each user's CV via onboarding.
6. **Per-user data isolation** — every profile/match/draft/application lookup is scoped
   by user_id. The unsafe unscoped call is inexpressible (required keyword-only params).
7. **Job postings are a shared pool** — user.status mutations NEVER write to job_postings.
   Per-user state lives in match_results (decision, dismissed_reason) and applications.
8. **Outbound content must carry the sender's identity** — test on the artifact
   (AI prompt input, email payload, PDF attachments), not just the row ownership.

## AI setup (GLM via Z.ai)

- **Model: `glm-5.1`** (switched from glm-4.6 which had 24-point run-to-run variance)
- Matching: temperature=0.0, anchored rubric in prompt, ~6s/call, 10 concurrent
- Tailoring: default temperature 0.3 (variety in cover letters is desirable)
- Prompt version: `AIService.matching_prompt_version()` — SHA-256 of the prompt
  text; any accidental edit changes the version and calibration tests fail
- Dead-band: scores in [18, 25) are re-scored once and averaged before keep/dismiss
- All 243 existing match rows are `legacy-unversioned` — re-score needed (~$1)

## Job sources

| Source | Countries | Auth | Notes |
|---|---|---|---|
| jobtech (Platsbanken) | SE | none (key optional) | Government open data, effectively uncapped |
| reed | GB | basic-auth key | 2,000 req/hr — effectively unlimited |
| careerjet | SE+GB | key + declared IP + Referer | 1,000 req/hr |
| adzuna | (none — module retained) | app_id + key | Demoted from the GB pack (WO-08: Reed carries the UK). Retained for the US/AU expansion backbone |
| arbeitnow, remotive, jobicy, workingnomads | shared | none | Public feeds |

## Multi-user architecture (Phase 1b, COMPLETE)

- **Auth on every route**: `Depends(current_active_user)` — 401 for anonymous callers.
  Frontend: /login page, axios Bearer interceptors, 401 → redirect, sidebar Sign out.
- **Per-user data model**: user_id FKs on profiles (UNIQUE), match_results
  (composite unique user_id+job_id), application_drafts, applications. All NOT NULL.
- **user_id is required and keyword-only** across 12+ functions — the unsafe
  unscoped call is a TypeError at import time.
- **IDOR**: `owns_or_404` fails CLOSED on NULL (a NULL-owner row is nobody's).
- **Rate limits**: sliding-window per user on AI-spending endpoints.
- **GDPR**: DELETE /api/v1/account/delete (cascade + CV file + token death +
  rate-limit memory purge); GET /api/v1/account/export (portability).
- **Cross-tenant dismissal fixed**: dismissals live on match_results.dismissed_reason
  (per-user, per-job), never on shared job_postings.status.

## Production infrastructure

- **CI** (.github/workflows/ci.yml): 3 jobs — Backend (ruff + Alembic on Postgres 16 +
  multi-user tests + flow test), Frontend (tsc + next build), Docker (build + smoke test).
  Installs from `requirements.lock` (71 pinned deps).
- **Dockerfile**: python:3.12-slim, non-root user, lockfile-only install,
  ships Alembic for boot migrations.
- **launchd agent**: `com.jobfinderos.backend` — RunAtLoad + KeepAlive.
- **Boot migrations**: Postgres = upgrade head; legacy SQLite = stamp + upgrade.
  Both backends verified (up/down on real Postgres 16, live SQLite migration).

## Score calibration system

- **prompt_version column** on match_results: `AIService.matching_prompt_version()`
  = SHA-256 of the scoring prompt text (format: `m2-<8-char-hash>`). Stored on
  every match row. Cross-version scores are NOT comparable.
- **Dead-band**: `MATCH_DEADBAND_MIN_SCORE=18` vs `MATCH_KEEP_MIN_SCORE=25`.
  Scores in the band get one re-score, averaged. Below 18 = permanent dismiss.
  `dismissed_reason` column tracks why (below_threshold, dead_band_confirmed, etc.).
- **Calibration tests** (tests/test_calibration.py): 4 always-on tests that pin
  the prompt hash (accidental prompt edits break CI), verify dead-band ordering.
  Opt-in live variance check behind `RUN_CALIBRATION=1`.
- **Score variance**: glm-5.1 @ temp 0 has mean spread ~7-10 (measured). Not
  deterministic (MoE routing, batching, GPU order), but tight enough that the
  dead-band catches the borderline cases. Tier bands hold: 80/50/30/25.

## Frontend design system — "The Hunting Console"

- **Token system** (globals.css @theme): ink #0C0E12, surface #12151C, line #1E2330,
  text tiers (hi/mid/low), ONE accent: signal amber #F5A524. Geist Sans + Geist Mono.
- **Sidebar**: expandable groups (Matches→Awaiting/Approved, Applications→Review/Sent),
  collapsible to icon rail (persists), user chip + hunt cycle countdown + Sign out.
- **Hunt Pulse** (signature element): funnel strip (Hunted→Matched→Awaiting→Drafts→Sent)
  with live counts, breathing attention dots, +N delta, next-hunt countdown in header.
- **Match cards**: company tile left, score ring right, salary chip, NEW badge (rolling 24h),
  posted-date chip (red at 21+ days), language badge, tier badge.
- **Applications**: accordion drafts (one open at a time, unsaved chip), Sent page
  with expandable document retrieval (cover letter + CV PDFs forever).
- **Login page**: /login route, form-based, stores JWT in localStorage.

## Frontend patterns (learned the hard way)

- `parseUtcDate(iso)` in utils.ts — API serves naive UTC; `new Date()` treats
  offsetless as local per ECMA-262. All timestamps go through this helper.
- PDF downloads: axios blob fetch (not window.open — carries no Bearer header).
- Error display: `apiErrorMessage(err)` interceptor unwraps backend `detail`.
- Background refresh protection: dirty-ref guard on Profile inputs.
- localStorage keys: `jfos-token` (JWT), `jfos-rail-collapsed` (sidebar state).

## Hard-won lessons (don't re-learn these)

1. **Async endpoints + blocking IO = frozen server.** All heavy work via `run_in_threadpool`.
2. **Unhandled exceptions → Starlette 500 → NO CORS headers** → misleading browser errors.
3. **GLM latency is prompt-size × reasoning.** Thinking disabled = ~5s. Small prompts lie.
4. **Live-test every blind-built scraper** — docs always differ from reality.
5. **Python `hash()` is per-process randomized** — never for persistent IDs (MD5 instead).
6. **`datetime.utcnow()` is deprecated** — use `utc_now()` from `app/core/timeutil.py`
   (returns naive-UTC, same storage semantics, no aware/naive mixing).
7. **SQLite create_all hits CircularDependency** with per-user FKs — use Alembic (TestClient).
8. **conftest.py owns DATABASE_URL** — never override from a test module.
9. **Concurrent sessions on this repo will collide** — serialize or branch.
10. **Green CI ≠ correct** — verify by adversarial execution, not proxy signals.

## Open items / next steps

- [ ] **Re-score the 236 legacy-unversioned matches** (~$1, one script) — puts the
      whole queue on one scoring function
- [ ] Phase 1a-static: landing page (marketing, pricing, FAQ — no data model deps)
- [ ] Phase 1c: signup UI, wizard entry from signup, console auth guard polish
- [ ] Composio: connect Gmail (Settings page ready; needs platform API key)
- [ ] Query-subscription model: designed in ROADMAP. NOT urgent — the old
      ">3 concurrent UK users" trigger was Adzuna-shaped and wrong (Adzuna has
      contributed zero rows; Reed carries the UK). Efficiency/UX win, build on
      measured pressure.
- [ ] Adzuna commercial terms: ask at US/AU expansion, not before. In SE/UK
      Adzuna is redundant garnish; in US/AU the aggregators ARE the backbone
      (Indeed's API died 2023; Seek has none and owns Jora). Same email should
      settle the caching/redistribution ToS question that gates shared-fetch.
- [ ] UK test user walkthrough (Profile → Edit setup → GB)
- [ ] Teamtailor slugs, JobTech free key for production
- [ ] Deploy: Render (worker) + Cloudflare Pages (frontend) + Postgres.
      The Postgres/auth vendor decision is SETTLED (Supabase consolidation) — see MIGRATION.md (repo root); MIG-WO0 is the first action.
      (Historical: this line and ROADMAP briefly disagreed on Neon vs
      Supabase — resolved in favour of Supabase, see MIGRATION.md. The
      auth question follows the Postgres one:)
      Supabase Postgres makes Supabase Auth nearly free; Neon makes it a
      third vendor for one feature. RLS is available on BOTH and is not a
      reason to pick either (it is a Postgres feature, not a Supabase one).
- [ ] Playwright ATS drivers for structured portal applies (staged, human-confirmed)
