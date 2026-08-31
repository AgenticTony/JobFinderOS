# JobFinderOS — Full Pre-Beta Code Review

> **Date:** 2026-08-30 · **HEAD:** `79dcfcb` (+ uncommitted docs/CLAUDE.md changes)
> **Trigger:** Beta opens this week — real SE+UK users registering, uploading CVs, receiving AI-tailored application emails.
> **Method:** Three-wave agent review — 7 specialist reviewers (security, AI systems, pipeline/concurrency, data layer, frontend, infra/ops, adversarial cross-cutting) → 7 independent verification agents re-checked every finding against the code → master review re-verified all P0s and top P1s by direct inspection.
> **Verification result:** 62 findings → **61 confirmed, 1 confirmed with corrected mechanism, 0 refuted.**
> **Live pass (same day, post-report — Part II):** an isolated rig — real Postgres 16, real Z.ai calls, live scrapers, two real users — reproduced the blockers end to end: **11 findings LIVE · 27 confirmed in code · 0 already fixed · 1 new defect found (fuzzy-dedupe false positive) · 2 corrections to this report (AI-9 mechanism, AI-11 fix).**
> Every finding below carries file:line evidence quoted from the code by at least two independent readers (specialist + verifier), and all P0s additionally by the master review.

---

## Executive verdict

**The beta is NOT ready to open today.** Six P0 blockers — a cross-user data leak on the main button, GDPR erasure that crashes for exactly the users who engaged most, an unbounded spend/spam surface on signup, silent loss of users' typed work in the console, no backup of production CV files, and an email-apply leg that appears to be switched off in production. None of these are hard to fix — most are one-to-ten-line changes — but between them they cover every beta risk class that matters: cross-tenant leakage, spend, legal (GDPR), data loss, and core-loop functionality.

**Live reproduction — Part II of this report.** The verdict above is no longer static-analysis opinion. An isolated rig with real Postgres, real Z.ai calls and two real users (Alice: Swedish nurse in Malmö; Bob: London backend engineer) reproduced the blockers end to end: Alice received 10/10 of Bob's matches on the hunt button; Bob's GDPR erasure returned a 500 with every PII row surviving while his CV file was already destroyed — and the proposed delete-reorder fix was then verified to commit cleanly on that same live chain. The live pass also found one defect all seven code reviewers missed (the fuzzy dedupe gate silently hiding the most relevant jobs) and corrected two of this report's proposed fixes (AI-9, AI-11 — both rewritten below).

The encouraging news: the deep machinery held up. The verification pass killed nothing because there was little to kill — but the specialists also explicitly checked and CLEARED the load-bearing invariants (IDOR guards on detail routes, judge fail-closed, outbound identity in submit, per-user locks in-process, cost math, boot advisory locks, indexes for hot queries). The defects live at the seams: a summary query, a delete order, an env var, a React key, a config file.

**Execution evidence:** local suite = 222 passed, 2 skipped (SQLite, 9.1s). CI is RED on main and — critically — its backend jobs die at the lint step BEFORE tests run, so CI has provided zero test signal since it went red (see OPS-3).

---

## P0 — Beta blockers (fix before any external user registers)

### P0-1 · Cross-user match data leak in the hunt response
`backend/app/services/pipeline.py:459-466` (+ unscoped re-fetch at `api/v1/pipeline.py:56-63`)
```python
top_matches = (
    db.query(MatchResult)
    .join(JobPosting, MatchResult.job_id == JobPosting.id)
    .filter(MatchResult.decision.is_(None), JobPosting.status == "matched")
```
No `MatchResult.user_id == user_id` filter. Any user pressing Hunt gets the top-10 globally-ranked pending matches — including other users' `reasoning`, `matched_skills`, `missing_skills`, `transferable_skills` (CV-derived AI output) via `MatchWithJobResponse`. The shared `status == "matched"` flag is set by any user's matcher. Fires from the first multi-user hunt. Found independently by 2 specialists, confirmed by 2 verifiers + master.
**Fix (1 line + mirror in the route's re-fetch):** add `.filter(MatchResult.user_id == user_id)`. Add a two-user regression test (TestOutboundIdentity pattern: user A's hunt response must contain only A's match ids).
**🔴 LIVE-CONFIRMED (Part II §01):** Alice received 10/10 of Bob's pending matches in her hunt response — score, tier, and reasoning written against his CV (his employer scale, Kafka pipeline, London location all reconstructable from her screen). Blast radius measured and narrowed: acting on the leaked rows as Alice failed correctly — `GET /matches/` returned 0, `/matches/35` and the decision endpoint 404'd. **Disclosure-only: the IDOR guard and scoped list/detail routes held.** The one-line user_id filter (mirrored in the re-fetch) closes it.

### P0-2 · GDPR erasure 500s on Postgres for users with drafts/applications
`backend/app/api/v1/account.py:49-52`
```python
matches = db.query(MatchResult).filter(...).delete()      # deleted FIRST
drafts = db.query(ApplicationDraft).filter(...).delete()
applications = db.query(Application).filter(...).delete()
```
`application_drafts.match_id`, `applications.match_id`, `applications.draft_id` are NOT-DEFERRABLE FKs with no `ondelete` (initial migration lines 150/170-172). Deleting matches first violates the FK → IntegrityError → 500 → rollback → **the account and all PII survive** — for exactly the users who drafted or applied (draft_service.py:111,305 set those FKs in the normal flow). The CV file is deleted from storage BEFORE the DB deletes (account.py:42-47), leaving a dangling path. Tests pass because the multiuser fixture seeds NULL match_id (test_multiuser.py:241-244) and SQLite doesn't enforce FKs. Same FK-order bug in `crud/__init__.py:64-72` (`delete_job`) → `DELETE /jobs/{id}` 500s for any drafted/applied job. Found by 2 specialists, double-verified.
**Fix:** reorder both paths: applications → drafts → matches → profiles → user; move CV-file deletion after `db.commit()`; add a Postgres-leg regression test seeding a real draft/application chain. Durable: migration adding `ondelete=SET NULL` to the nullable FKs (+ CASCADE on user_id FKs).
**🔴 LIVE-CONFIRMED (Part II §02):** on a real Postgres 16 with a real submitted application chain, `DELETE /api/v1/account/delete` → HTTP 500 (`IntegrityError: update or delete on table "match_results" violates foreign key constraint "application_drafts_match_id_fkey"`), leaving users=1, profiles=1, matches=37, drafts=1, apps=1 — full PII intact (name, email, phone) — **while the CV file was already destroyed before the transaction.** The exact inverse of what the user asked for. **The proposed fix was then executed against the same live chain: applications → drafts → matches → profiles → user committed cleanly.** Same ordering bug in `delete_job` confirmed by inspection.

### P0-3 · Unbounded account/spend/spam factory on signup
`backend/app/api/deps.py:29`
```python
enforce(f"reg:{email.lower()}", "auth_register")
```
Register throttle keyed by the attacker-submitted email — a fresh bucket per address. No email verification is mounted (users.py:8-10), no per-IP limit exists (ratelimit.py:51-57 defers to a reverse proxy that isn't configured). Each throwaway account carries full AI budgets (12 hunts/h × up to 200 evaluations, 12 match-runs/h, 20 drafts/h…). Compounded by OPS-5 (in-process limiter resets on every Render cold start/deploy). Also the engine of the spam chain in SEC-6. Single-account brute force IS throttled; spraying and signup-farming are not.
**Fix:** per-IP signup/login buckets (`regip:{client.host}`, ~10 signups/day) + mount email verification (Resend is already a dependency) or a signup captcha. Cheap and collapses the multiplier.
**🔴 LIVE-CONFIRMED (Part II §03):** 8 signups with distinct emails from one client in ~8 seconds — 8/8 created, zero throttle; each account got full AI budgets and a profile row. Same-address hammering IS caught (429 after 5) — address-spraying is completely untouched.

### P0-4 · Users' typed cover letters are silently destroyed
`frontend/src/app/app/page.tsx:352` (`<motion.div key={view}>`) wrapping `ApplicationsView` (:390)
Draft editor state is `useState` inside `DraftCard` (:1055-1058). Any view switch — Dashboard, Matches, **or the Review↔Sent sub-tab** — changes the key, remounts the subtree, and discards typed edits. No navigation guard, no `beforeunload`, no auto-save (grep-verified zero matches). A beta user who edits a letter, taps "Sent" to check something, and returns finds their work gone. Directly violates the stated invariant. (Verifier note: it's worse than first reported — sub-tab switches remount too.)
**Fix:** hoist editor state to a `draft.id`-keyed cache above the keyed container (or drop `key={view}`), plus a dirty-check confirm on `setView` when any card is dirty; optionally debounced auto-save.
**🔴 LIVE-CONFIRMED (Part II §03):** driven in a real browser as Alice — typed 20 minutes of cover letter, clicked Sent, clicked back to Review & send: the accordion reopened collapsed and the textarea silently reverted to the AI original (`containsTypedText: false`). No warning, no confirm, no recovery. The sub-tab alone triggers it.

### P0-5 · Production CV files have no backup
`ops/backup.sh:80-87` (+ runbook claim at WO-07-runbook.md:127-129)
Production CVs live in the Supabase Storage private bucket (`render.yaml:64-65` → `storage.py:99`, keys `cvs/<name>`). backup.sh mirrors only the local dev dir `backend/uploads/cvs` (receives nothing in prod) and pg_dump captures rows, not Storage objects. **No step anywhere exports the bucket.** The runbook claims backup coverage exists. Bucket loss = permanent loss of every beta user's original CV. Aggravators: off-site never runs (OPS-2) and no restore path exists at all (OPS-4).
**Fix:** add a Storage export step to backup.sh (paginated `POST /storage/v1/object/list` + authenticated GETs with the service key — same API pattern as `provision_supabase_storage.py`), verify by count, include in off-site set; rehearse a restore once before beta.

### P0-6 · Email apply appears provisioned OFF in production
`render.yaml` (zero matches for RESEND), runbook secret table omits it
`RESEND_API_KEY` / `APPLY_FROM_EMAIL` appear nowhere in render.yaml or the runbook; `profiles.py:245` gates `email_apply_enabled` on the key; no doc records it being set on Render. If unset in the dashboard too, **the flagship send-with-PDFs loop is dead in production** (browser/manual apply still works), and the error message tells Render users to edit `backend/.env` — which doesn't exist in the container. The 6/6 deploy verifier never checked email. Cannot be fully confirmed from the repo (a dashboard-only secret is invisible) — **check the Render dashboard first.**
**Fix:** add both keys (`sync:false`) to render.yaml + runbook table; set them on Render; add an email-apply check to verify_deployment.sh; make the error string environment-neutral.

---

## P1 — Fix during beta week (ordered by user impact)

### Backend security / privacy
1. **Employer replies go to the owner's inbox** — `draft_service.py:389-397` and `apply_service.py:109-118` send with no `reply_to`; all applications go out from the shared `APPLY_FROM_EMAIL`. Interview invitations and offers silently never reach users. Fix: `"reply_to": profile.email` in both send paths (profile already in scope). Product-critical; 2 lines.
2. **Successful retry leaves the draft sendable → duplicate employer emails** — `apply_service.py:63-66` only handles the failed branch; on retry success the draft stays `"ready"`, `submit_draft` blocks only `"submitted"` (draft_service.py:281-284), and the frontend keeps it actionable (`openDrafts` filter, page.tsx:807). Fix: set `draft.status = "submitted"` when the retry succeeds; add the missing `enforce()` on submit/retry (see next).
3. **Unthrottled send/spam chain** — `POST /jobs/` (caller-controlled `application_email`), `PUT /draft/{id}` (arbitrary content), `POST /draft/{id}/submit` and `/retry` have NO rate limit (verified enforce inventory: only prepare/matches-run/pipeline/profiles are limited). ~20 arbitrary-destination emails/hour/account through the owner's Resend domain; unbounded via P0-3. Fix: `enforce()` buckets on create/submit/retry + per-account daily send cap + validate application_email.
**🔴 LIVE-CONFIRMED (Part II §03):** 25 jobs created in one burst pointing at 25 distinct targets — 25× 201. `application_email = "not-an-email-at-all <<>>"` was accepted verbatim as a destination. Confirmed enforce inventory: only prepare, matches-run, hunt, cv_upload, ai_suggest and the two auth routes are limited — nothing else.
4. **Any user can mutate the shared job pool** — `jobs.py:89` `PATCH /jobs/{id}/status` writes `job_postings.status` for everyone (no ownership concept exists); `"dismissed"` removes the job from EVERY user's matching queue (matcher_service.py:212), and the re-queue guard (:83) is unscoped across users. The frontend never calls this endpoint (grep-verified) — dead attack surface. Fix: delete the endpoint (per-user dismissals already live on match_results).
**🔴 LIVE-CONFIRMED (Part II §03):** Alice (a nurse) PATCHed five of Bob's London engineering jobs to `"dismissed"` — 5× HTTP 200 — and those rows are now excluded from **every** user's matching queue.
5. **CV re-upload breaks erasure + outbound integrity** — `cv_service.py:123-128` overwrites `cv_file_path`/`cv_text` in place (its own docstring says otherwise); the replaced storage object is orphaned and survives GDPR erasure; and a draft guarded against CV-old, submitted after re-upload, emails CV-old-tailored docs with CV-NEW attached as "original" (draft_service.py:374-383 reads the current path) — the pair can contradict. Fix: delete the replaced object at re-upload; snapshot the CV reference (path/hash) on the ApplicationDraft at creation.
**🔴 LIVE-CONFIRMED (Part II §06 ledger):** the old file confirmed on disk after re-upload — unreferenced, and it survives erasure.
6. **GDPR erasure/export gaps** — `ai_usage` rows (user-linked, no FK, never deleted, not in export, no documented retention); export omits drafts' cover letters/tailored CVs and application subject/body/target_email. Fix: NULL-or-delete ai_usage user_id on erasure (decide retention), extend export payload.
7. **7-day JWT in localStorage, no revocation** — config.py:90; password change doesn't revoke outstanding tokens. Fix: shorten lifetime or add a token-version check.
8. **Login password-spraying unthrottled** (per-account only) and **erasure doesn't purge email-keyed limiter buckets** — both cheap additions once P0-3's per-IP work lands.

### AI correctness
9. **Silent empty "ready" drafts — MECHANISM CORRECTED by live pass** — `tailor_application` (`ai_service.py:313`) is the only AI call trusting `_parse_json`'s `{}` fallback (`match_job` and `judge_fabrication` raise). **The live pass fired this on the very first real tailoring attempt: `status: "ready"` with a 0-character cover letter and 0-character CV — at 674 completion tokens on an 804-char CV, nowhere near the `max_tokens=2000` cap.** The truncation path the static review blamed exists but is NOT what fires in practice: the trigger is **any malformed model response → `{}`**, which makes it intermittent (re-running identical inputs succeeded). The empty draft passes the fabrication guard and judge vacuously, shows as ready-to-send, and dead-ends at submit ("Draft is not ready — prepare or fix it first"). Fix (per live pass, in priority order): **the raise IS the fix** — reject empty/missing keys in `tailor_application` exactly as `match_job` does; `finish_reason=="length"` checking and a larger tailor token budget are worthwhile hardening but would NOT have prevented the observed failure.
10. **Scoreless JSON permanently dismisses jobs as 0** — `ai_service.py:226` `parsed.get("score", 0)` + `_clamp_score` returning 0 on TypeError → single-sample "confident 0" → permanent `below_threshold` dismissal; the adjacent comment (:220-223) forbids exactly this for parse failures. Fix: treat missing/non-numeric score as a format error (raise; job stays "new").
11. **`extract_profile` failure silently wipes fields — ORIGINAL FIX WAS WRONG, corrected by live pass** — `ai_service.py:177` returns `{}` on a malformed extraction response → `_apply_extraction(profile, {})` nulls `full_name`/`email`/`phone`/`title`/`years` on a previously-good profile. **Live-verified with a simulated Z.ai hiccup during Alice's CV re-upload: every extracted field went from real values to None in one call** — outbound PDFs would go out addressed from "Applicant". The correction: the static review's fix (raise on empty parse) does NOT work — `cv_service`'s `except Exception` swallows the raise and `_apply_extraction` still runs and still wipes. **Fix: guard the write — skip `_apply_extraction` entirely when extraction produced nothing.**
12. **Employer-facing PDFs render `?` for em-dashes/curly quotes in production** — `pdf_service.py:56-57` latin-1 "replace" path is the DEFAULT in prod (python:3.12-slim has none of FONT_CANDIDATES); GLM prose routinely contains U+2014/2019. Fix: `apt-get install fonts-dejavu-core` in both Dockerfiles (or bundle a TTF).
**🔴 LIVE-CONFIRMED (Part II §04):** `python:3.12-slim` has **no `/usr/share/fonts` directory at all** — the latin-1 replace path is the production default. Rendering through the no-font path put 5 `?` characters into one short paragraph (`I'm excited ? truly ? about the role; the team?s ?first principles? culture`), and the live GLM cover letters were full of em-dashes and curly quotes.
13. **Prompt-version hash covers only half the scoring input** — `ai_service.py:609-619` hashes the system prompt; profile-context rendering/truncations are unversioned (the experience_years removal changed behavior with no version bump — the repo documents it). Fix: fold a composition-version constant into the hash.

### Pipeline / concurrency
14. **Manual Hunt never claims the hunt lock; no DB dedupe backstop** — `api/v1/pipeline.py:45` calls `run_pipeline` directly (only the cron worker claims SystemLock); per-user locks are process-local threading.Locks; `job_postings` has NO unique constraint on (source, source_id)/url/dedupe_key (verified in models + migrations) → a user pressing Hunt during the cron window double-scrapes, double-AI-scores the same user (IntegrityError reconciliation discards the loser's rows AFTER the spend), and inserts duplicate shared-pool rows. Fix: claim the hunt lock in the API path; migration adding `unique(source, source_id)` + ON CONFLICT upsert.
15. **Scheduled hunts ignore radius and region** — `pipeline.py:495-507` union contexts never set `search_radius_km` and pin `region=None` → cron-path ingestion (the automated path) never radius-fetches and applies the strict gate only; "Malmö + 30km" users never see neighboring-kommun jobs from scheduled hunts; region-only users get no local on-site jobs at all. Fix: emit per-anchor radius contexts alongside the union; carry regions. (Don't naively max-radius the union — `geo_plan` anchors on municipalities[0] and would mis-center.)
16. **Match-time has no location/remote/country gate** — union ctx ORs `include_remote` (pipeline.py:531-532); the matcher's candidate query (matcher_service.py:204-217) filters only no-prior-match + not-dismissed → remote jobs stored for the union enter every strictly-local user's window and consume their 200-evaluation budget before auto-dismissal. Fix: pre-filter candidates by the user's profile scope before any AI slot is spent.
**🔴 LIVE-CONFIRMED (Part II §04):** Bob — a London lead backend engineer — had **all four** of his first-hunt evaluation slots spent on "Product Marketing Lead - EMEA" (8), "Credit Analyst Intern" (14), "Summer Intern 2027" (8) and "Graduate Analyst - £50,000 + Share Options" (10), each permanently dismissed. Currently the biggest quiet money-waster.
17. **Watermarks advance on partial fetch failures** — `jobtech.py:182-187` per-page `break`; `pipeline.py:315-318` still stamps the watermark → in backfill mode the un-fetched pages are permanently skipped; one 06:00 hiccup drops that day's postings for every user in the shared scope. Fix: report per-unit health; stamp only fully-successful units.
18. **Worker lock undersized + unconditional release** — `worker.py:23,72-81`: TTL 45min vs per-user 420s budgets (~6 users exceed it); no renewal; release clears any owner's claim → overrun + second claimer = concurrent hunts. Fix: owner token + conditional release + TTL sized to users×budget.
19. **Deleted-user mid-run budget burn** — matcher keeps feeding an erased user's CV to GLM (up to 200 evaluations) with every INSERT failing the FK. Fix: periodic user-existence check in the loop; abort on repeated FK failures.
20. **NEW — found live, missed by all 7 code reviewers: fuzzy dedupe collapses genuinely different roles** — `core/dedupe.py` `likely_same_job` dismisses at title-token Jaccard ≥ 0.6 when the company matches; a four-token title differing by exactly one discriminating word scores exactly 0.6. The live run collapsed **"Senior Data Engineer (Python)" → "Senior Data Scientist (Python)"**, **"Senior Python Developer (Flask)" → "(FastAPI)"**, and **"Strategic Finance Assoc Principal" → "Principal"** (verified against all 183 postings; no duplicate `dedupe_keys` exist — the exact gate is fine, this is purely the fuzzy threshold). Different jobs, different applications — silently hidden **before any AI call** with `reasoning = "Not shown: duplicate"`, while the function's own docstring names the stakes: "a wrongly collapsed job is invisible forever." The Flask/FastAPI pair were among the only Python roles in Bob's queue. Fix: raise the threshold, or require the differing tokens to be non-discriminating (engineer vs scientist and Flask vs FastAPI are role identity, not noise); add a regression test with the live pairs.

### Frontend
20. **Stale-mount editor clobbers AI output** — page.tsx:1055-1056 initializes once; a draft mounted during "drafting" shows empty fields when it flips "ready"; saving then PUTs the empty `tailoredCv` over the AI's work. Fix: pristine-sync effect (the ProfileView pattern already in the file, :1331-1340); never PUT a field that was never populated.
21. **Core actions fail silently** — approve/reject, prepare, draft save, profile save, onboarding finish have no catch (only submit/retry/connect do); no ErrorBoundary/`onunhandledrejection` anywhere. On a cold-starting free tier this is the norm, not the exception. Fix: catch → existing error surfaces; add root `error.tsx`.
22. **PDF downloads silently no-op in Safari** — api.ts:245 `window.open` after an await breaks user activation; the crafted "Popup blocked" error is unreachable by the UI. Fix: programmatic `<a download>` click; surface failures.
23. **API-down renders as an empty-healthy console** — page.tsx:123-125 maps load failures to `[]`; empty-state copy invites a quota-spending hunt to "fix" it. Fix: error banner + retry when the initial load fails.

### Ops / deploy / CI
24. **OPS-2: Off-site backup never runs** — the launchd plist passes no `EnvironmentVariables`; `OFFSITE_BACKUP_TARGET` is env-only → nightly runs always take the "not set" branch and exit 0. Runbook claims "off-site B2 nightly" — no B2/rclone config exists anywhere. Fix: env dict in plist; confirm "off-site OK: N files" in the log.
25. **OPS-4: No restore path** — no restore script, no runbook section; `migrate_sqlite_to_supabase.py` can't consume pg_dump output. A backup never restored is unverified. Fix: ops/restore.sh + one rehearsal before beta.
26. **OPS-1: 60s axios timeout vs ~1min cold start** — api.ts:55 + render.yaml:5-6 (free tier spins down); the project's own verify script budgets 90s×6. First request after idle typically fails. Fix: ≥120s timeout + retry-on-timeout for GETs; consider a keep-warm ping.
27. **OPS-3: CI gate broken and partial** — (a) lint step red: unsorted/unused imports in test_delta/test_radius/test_taxonomy (reproduced locally); (b) CI runs only 3 of 8 test modules — delta/radius/taxonomy/calibration/fabrication NEVER run in CI; (c) `render.yaml autoDeploy: true` ships every main push with no full gate; (d) ci.yml:166 asserts cron `"0 * * * *"` vs render.yaml `"0 6,18 * * *"` — mutually incompatible, blueprint job permanently red. Fix: run the full suite on both legs, fix the three lint failures, make the assertion parse render.yaml. **Check the live Render cron cadence** — the runbook says hourly, the dashboard countdown says 06:00/18:00; one of them lies to users.
**🔴 LIVE (Part II §06):** 11 lint errors reproduced; cron assertion mutually incompatible with render.yaml on every push. *(Master reconciliation note: the live ledger records "CI runs 2 of 8 modules" — ci.yml's pytest steps invoke test_units, test_multiuser and test_flow, i.e. 3 of 8 by direct file inspection; either way, the five correctness modules — delta/radius/taxonomy/calibration/fabrication — never run in CI.)*
28. **OPS-6: No user-facing privacy disclosure** — zero matches in frontend for privacy/Z.ai/processor terms; CV text flows to api.z.ai (outside the EU) on every match/tailor/judge call. Art. 13 gap for EU beta users; cheap now, painful retroactively. Fix: disclosure at signup + CV upload naming processors (Z.ai, Supabase, Resend) and the erasure path.
29. **OPS-7: Production guards miss storage** — `_production_guards` checks AUTH_SECRET/DB/CORS but not `SUPABASE_URL/SERVICE_KEY` — after a service recreation (the documented sync:false incident), the app boots green and every CV upload 500s at runtime. Fix: extend the guards.

---

## P2 / P3 appendix (verified, lower urgency)

| ID | Sev | Location | Summary |
|---|---|---|---|
| FE-9 | P2 | page.tsx:1117, MatchCard.tsx:68 | Expandable card headers are div-onClick — keyboard users cannot open drafts/matches at all |
| FE-10 | P2 | OnboardingWizard.tsx:273 | `aria-modal` dialog with no focus trap/Escape/scroll lock — first-run UX lockout for AT users |
| FE-11 | P2 | GuardReceipt.tsx:6-11 + landing copy | Landing shows a hardcoded fabricated guard receipt as "proof" beside "0 facts invented" — honesty risk |
| FE-12 | P3 | api.ts:281 | Applications N+1 join fetches 500 jobs per refresh; >500 shared jobs degrades sent list to "Job #id" |
| DATA-4 | P2 | alembic/env.py:15 | env var unconditionally overrides the injected URL — if DATABASE_URL is only in backend/.env, boot "migrates" a fresh SQLite silently (Render unaffected) |
| DATA-5 | P2 | pipeline.py:95-111 | Watermark select-then-insert race; the existing except leaves PendingRollbackError → the hunt 500s after jobs committed, ScrapeRun stuck "running" |
| DATA-6 | P2 | database.py:100 | lock_timeout covers only the advisory conn — first post-beta DDL migration can block unboundedly (Render health-check death). Latent until the next migration ships |
| AI-14 | P2 | matcher_service.py:26,223 | Global `matching_running` flag misleads every user's dashboard while any user matches |
| PIPE-20 | P3 | jobtech.py:62-67 | Taxonomy failure caches an empty dict for the process lifetime (occupation_taxonomy fixed this exact bug; jobtech didn't) |
| PIPE-21 | P3 | pipeline.py:208-210 | Killed-worker ScrapeRun rows stuck "running" forever on dashboards |
| INFRA-30 | P2 | requirements.lock:52,58 | pytest + ruff ship in both production Docker images |
| INFRA-31 | P3 | ci.yml:55 | Lint covers I,F only; no S rules, no pip-audit/dependabot |
| SUBMIT | P2 | draft_service.py:281 | Check-then-act submit with no row lock/"sending" state + no unique on applications(draft_id) — double-click double-send window |
| HYGIENE | P3 | various | TeamtailorScraper dead `__all__` entry (live-confirmed: star-import raises AttributeError); zustand declared, never imported; `npm start` incompatible with `output:'export'`; `npm run lint` has no ESLint config; stray 0-byte frontend/jobfinderos.db; `.env.example` MAX_JOBS_PER_MATCH_RUN=25 vs code default 200; CLAUDE.md drift (says "9 sources"/"42+ tests" — 8 registered/217 functions; 236 vs 243 legacy matches) |

---

## Verified clean (checked explicitly, no action)

`owns_or_404` fails closed and is applied on every match/draft/application detail route; draft/submit/retry resolve the caller's profile at the route (tenancy Layer 1 traced end-to-end into the email payload); the fabrication judge fails closed on unparseable/wrong-type output; blocked drafts cannot reach submit; `save_draft_edits` on a blocked draft doesn't re-enable it; fastapi-users register/PATCH use `safe=True` (no superuser escalation — verified in venv source); CORS explicit-origin with wildcard refused at boot; Sentry scrubbing drops bodies/breadcrumbs/frame-locals; cost math (micro-dollar, `min(cached,prompt)`) checks out; no blocking IO on the event loop (all scrapers threadpooled); scraper timeouts and per-source failure isolation; `claim_hunt`'s conditional UPDATE is atomic; hot-query indexes exist; `uq_match_results_user_job` reconciles duplicate-row races; `parseUtcDate` used consistently (zero raw `new Date(iso)` on API data); 401 interceptor can't loop; poll intervals cleaned up correctly; send/hunt buttons have double-click guards; onboarding country-switch clears SE taxonomy correctly.

---

## Recommended fix sequence for beta week

**Day 1 — the hours-long, beta-gating batch:**
P0-1 (1 line) · P0-2 (reorder + PG test) · P0-6 (check Render dashboard; add env keys) · P1-1 reply_to (2 lines) · P1-2 retry→submitted (1 line) · P1-4 delete PATCH /jobs/status endpoint · P1-3 enforce() on submit/retry/create · OPS-3 fix lint + full test suite in CI + cron assertion + verify live cadence.

**Day 1–2 — the protect-the-user batch:**
P0-3 per-IP signup/login + verification gate · P0-4 draft-edit persistence + nav guard · P0-5 storage export in backup + OPS-2 off-site env + OPS-4 restore rehearsal · OPS-6 privacy disclosure · SUBMIT "sending" state.

**Day 2–3 — the quality-of-AI batch (amended by the live pass):**
AI-9 **the raise on empty/missing keys IS the fix** — the token budget increase alone would not have prevented the observed failure and is secondary hardening · AI-10 scoreless-JSON raise · AI-11 **guard the write: skip `_apply_extraction` on empty** — raising inside the AI service changes nothing, the caller's except swallows it · AI-12 fonts in Docker · AI-13 version-hash composition · **PIPE-20 dedupe threshold / discriminating-token fix + regression test** — it silently deletes the most relevant jobs from users' queues before any scoring, which reads to users as "this product doesn't find me anything"; same damage class as PIPE-16 and cheaper to fix.

**Day 3+ (can trail into early beta):**
PIPE-14 hunt lock on API path + (source,source_id) unique migration · PIPE-15/16 union radius contexts + match-time scope gates · PIPE-17/18 watermark health + lock owner/TTL · FE-20/21/22/23 frontend resilience batch · OPS-1 timeout/retry · OPS-7 storage guards · remaining appendix.

---

## Part II — Live verification pass (2026-08-30, same HEAD `79dcfcb`)

> **The report holds. Nothing was already fixed.** Every finding above was re-checked against the code, then an isolated live rig was built — real Postgres, real Z.ai calls, real scrapers, two real users — and the blockers were reproduced end to end. Every finding tested is present at HEAD.
>
> **11 reproduced live · 27 confirmed in code · 0 already fixed · 1 new defect found · 2 corrections to Part I.**

**Method and safety.** Isolated rig: throwaway Postgres 16 container, backend on port 8011, frontend on 3011, storage forced local. Live Z.ai calls (extraction, matching, tailoring, fabrication judge) and live scrapers — jobtech, reed, careerjet, arbeitnow, remotive, jobicy, workingnomads — producing 272 real postings. Two registered users with distinct CVs, countries and professions (Alice: Swedish nurse, Malmö; Bob: backend engineer, London). **No email was ever sent** — every submit used the manual method. The dev database, the live backend on port 8000, the Supabase bucket and the real CV file were not touched; the rig was torn down and the working tree left as it was.

**Why the suite didn't catch any of this.** `pytest tests/ -q` → 222 passed, 2 skipped, ~8s. Green — on SQLite, on a schema where the fixtures seed `match_id = NULL`. The blockers live exactly where the suite doesn't look: a single unscoped query, a delete order that only Postgres enforces, and a React key. Green CI proved the code runs — it never proved two users stay apart.

### §01 · The cross-user leak is real, and it fires on the main button

Bob hunted first and built up 10 pending matches. Then Alice pressed Hunt.

```
POST /api/v1/pipeline/run — as Alice (Swedish nurse)
top_matches returned to ALICE: 10

match_id=35 score=65 tier=good_match
  job: Member of Technical Staff (EMEA) @ Fireworks
  reasoning: "You have 9 years of distributed backend experience, deep
  Python expertise... (Kafka pipeline at 4M events/day, Django-to-
  FastAPI microservices migration on Kubernetes)... Your location
  in London matches the EMEA role, and your 9 years fits their
  3-10 year range."
  matched_skills: ['Python', 'Backend systems', 'Distributed systems',
                   'AWS/Infrastructure (Terraform, Kubernetes)', ...]
```

Bob's employment history is reconstructable from Alice's screen: years of experience, employer scale, education, city. CV-derived personal data crossing tenants on the product's primary action.

**Blast radius, measured.** Acting on the leaked rows as Alice: `GET /matches/` → count=0; `GET /matches/35` and the decision endpoint → 404. The leak is disclosure only — list/detail routes and the IDOR guard held. That narrows the fix (one `user_id` filter) but not the severity: the hunt response is what the dashboard renders.

### §02 · GDPR erasure fails on Postgres — and takes the CV with it

Bob approved a match, generated a real tailored draft, and submitted it (manual method — no email left the machine): the exact application → draft → match chain the fixtures never build. Then he asked to be forgotten.

```
DELETE /api/v1/account/delete — real Postgres 16
{"detail":"Internal server error"} [HTTP 500]

IntegrityError: update or delete on table "match_results" violates
foreign key constraint "application_drafts_match_id_fkey"

-- surviving after "erasure":
 users | profiles | matches | drafts | apps
     1 |        1 |      37 |      1 |    1

 full_name     | email                          | phone
 Bob Whitfield | bob.whitfield.jfos@example.com | +44 7700 900123

CV file on disk: GONE (deleted before the failed txn)
```

The outcome is worse than "the delete fails": the user is left with every field of their PII intact and their CV file destroyed — the exact inverse of what they asked for, hitting precisely the users who engaged most. **The proposed fix works:** run against the same live chain, applications → drafts → matches → profiles → user committed cleanly (apps=1, drafts=1, matches=37, profiles=1 deleted). The same ordering bug sits in `crud/delete_job`, confirmed by inspection.

### §03 · Three more blockers, reproduced against the running app

**P0-3 — eight accounts in eight seconds, no throttle.** The signup bucket is keyed by the submitted email, so a fresh address is a fresh bucket. Same-address hammering is caught (429 after 5) — address-spraying isn't touched at all. Each account carries full AI budgets, and registration writes a profile row per account.

```
8 signups, distinct emails, one client
201 201 201 201 201 201 201 201    → 8/8 created, zero throttle
same email x7:  201 400 400 400 400 429 429
```

**P0-4 — typed cover letter destroyed by a sub-tab click.** Driven in a real browser as Alice: opened a draft, typed into the cover letter, clicked Sent, clicked back to Review & send. The accordion reopened collapsed and the textarea had silently reverted to the AI original. No warning, no confirm, no recovery. The sub-tab alone triggers it.

```
Textarea value, before and after the round trip
before: "...Sincerely,\nAliceMY OWN CAREFULLY TYPED COVER LETTER —
         20 minutes of work that must not vanish."
after : "Dear Hiring Manager,\n\nAI-GENERATED DRAFT 2..."
        containsTypedText: false
```

**P1-4 — one user dismissed five jobs for everybody.** Alice sent `PATCH /jobs/{id}/status` with `"dismissed"` on five of Bob's London engineering jobs. All returned 200. Those rows are now excluded from every user's matching queue. The frontend never calls this endpoint — pure attack surface.

```
Shared job_postings.status, before → after
dismissed before: 0
alice → PATCH jobs 151..155 = "dismissed"  [200 ×5]
dismissed after:  5   ← removed from every user's queue
```

**P1-3 — 25 arbitrary send targets, unthrottled and unvalidated.** `POST /jobs/` has no rate limit and no validation on `application_email`. 25 jobs pointing at 25 different addresses in one burst; the string `"not-an-email-at-all <<>>"` was accepted as a destination. Submit and retry carry no `enforce()` either — the full limited inventory is prepare, matches-run, hunt, cv_upload, ai_suggest, and the two auth routes. Nothing else.

```
POST /api/v1/jobs/ ×25
201 ×25    attacker_jobs: 25 | distinct_targets: 25
application_email = "not-an-email-at-all <<>>"  →  201 accepted
```

### §04 · The AI findings reproduced — with two corrections to Part I

**AI-9 — an empty "ready" draft on the very first live attempt.** Bob's first real tailoring call returned `status: "ready"` with a zero-character cover letter and zero-character CV, stored silently. The fabrication guard passed vacuously; the judge passed; the UI showed a draft ready to send. Clicking send dead-ends on "Draft is not ready — prepare or fix it first".

```
ai_usage row for the empty draft
kind=tailor  model=glm-5.1  prompt_tokens=1660  completion_tokens=674
draft id=1   status=ready   cover_letter=0 chars   tailored_cv=0 chars
```

**The correction:** Part I attributed this to `max_tokens=2000` truncating long CVs. That path exists, but it is not what fired — the failing call spent 674 completion tokens on an 804-character CV, nowhere near the cap. The real trigger is broader and more frequent: any malformed model response becomes `{}`, and `tailor_application` is the one AI call that trusts that fallback instead of raising the way `match_job` does. Raising the token budget alone would not have prevented this. Re-running the identical inputs succeeded — it's intermittent, not reproducible on demand.

**AI-11 — one Z.ai hiccup wipes the whole profile, and Part I's fix doesn't work.** A simulated Z.ai failure during a re-upload of Alice's CV overwrote every extracted field with None — name, email, phone, location, title, years. Her outbound PDFs would go out addressed from "Applicant".

```
Alice's profile, across one failed extraction
BEFORE: full_name='Alice Nordstrom'  email='alice.nordstrom...'
        phone='+46 70 111 2233'  title='Senior Registered Nurse'  years=12
AFTER : full_name=None  email=None  phone=None  title=None  years=None
```

**The correction:** Part I said to fix this by raising on an empty parse, claiming "the existing except in cv_service already handles it". It doesn't — that except swallows the exception and leaves `extracted = {}`, and `_apply_extraction(profile, {})` still runs and still wipes. The mock raised, and the fields were wiped anyway. **The fix has to guard the write — skip `_apply_extraction` when extraction produced nothing.**

**AI-12 — employer-facing PDFs render "?" in production.** `python:3.12-slim` has no `/usr/share/fonts` directory at all. Neither Dockerfile installs one, so the latin-1 replace path is the production default. Rendering with the font list empty put five `?` characters into one short paragraph, and the live GLM cover letters are full of em-dashes and curly quotes.

```
Rendered PDF text, no-font path
in : I'm excited — truly — about the role; the team's "first principles" culture
out: I'm excited ? truly ? about the role; the team?s ?first principles? culture
```

**PIPE-16 — Bob's AI budget went to "Summer Intern 2027".** There is no scope gate before the AI spend — candidates are just the newest unmatched rows. A London lead backend engineer spent his four evaluation slots on a marketing lead, a credit analyst intern, a summer internship and a graduate analyst. Every one scored 8–14 and was permanently dismissed. This is the finding that quietly wastes the most money.

```
What Bob's first hunt actually paid GLM to score
score  title                                       source
    8  Product Marketing Lead - EMEA               arbeitnow
   14  Credit Analyst Intern                       arbeitnow
    8  Summer Intern 2027                          arbeitnow
   10  Graduate Analyst - £50,000 + Share Options  arbeitnow
```

### §05 · One defect the review missed

This only surfaced because the pipeline ran against live job data — seven reviewers reading the code did not catch it.

**NEW · DEDUPE FALSE POSITIVE — the fuzzy gate collapses genuinely different roles.** `likely_same_job` collapses two postings at a Jaccard title overlap of ≥ 0.6 when the company matches. A four-token title that differs by exactly one discriminating word scores exactly 0.6 — so *Senior Data Engineer (Python)* was dismissed as a duplicate of *Senior Data Scientist (Python)*, and *Senior Python Developer (Flask)* as a duplicate of the (FastAPI) role. Different jobs, different applications, silently hidden.

```
Collapsed pairs from the live run (verified against all 183 postings)
125 'Senior Python Developer (Flask)'   → 124 'Senior Python Developer (FastAPI)'
132 'Senior Data Engineer (Python)'     → 129 'Senior Data Scientist (Python)'
179 'Strategic Finance Assoc Principal' → 178 'Strategic Finance Principal'

note: no duplicate dedupe_keys exist in job_postings — the exact gate is
fine; this is entirely the fuzzy gate's 0.6 threshold.
```

These were among the most relevant postings in Bob's queue — the only Python roles he was shown — and they were killed before any AI call, with `reasoning = "Not shown: duplicate."` The function's own docstring names the stakes: "a wrongly collapsed job is invisible forever." It's currently taking that risk on a one-token difference. **Suggested fix:** raise the threshold, or require the differing tokens to be non-discriminating — engineer vs scientist and Flask vs FastAPI are role identity, not noise. Worth a regression test with these exact pairs. (Filed as **PIPE-20** in Part I.)

### §06 · Full verification ledger

LIVE = executed against the running system · CODE = verified by inspection at HEAD · PARTIAL = confirmed as far as the repo allows.

| ID | Finding | Verified | Evidence |
|---|---|---|---|
| P0-1 | Cross-user match leak in hunt response | **LIVE** | Alice got 10/10 of Bob's rows; write routes correctly 404 |
| P0-2 | GDPR erasure 500s on Postgres | **LIVE** | FK violation; PII intact, CV file destroyed; fix verified |
| P0-3 | Unbounded signup / spend factory | **LIVE** | 8/8 accounts, no per-IP bucket anywhere |
| P0-4 | Typed drafts silently destroyed | **LIVE** | Browser-driven; text reverted, no warning |
| P0-5 | No backup of production CV files | CODE | backup.sh mirrors local dirs only; zero bucket-export code |
| P0-6 | Email apply not provisioned on Render | PARTIAL | Zero RESEND matches in render.yaml — dashboard still unchecked |
| P1-1 | No reply_to — employer replies misrouted | CODE | Both send payloads carry from/to/subject/text only |
| P1-2 | Successful retry leaves draft sendable | CODE | Only the failed branch touches draft.status |
| P1-3 | Unthrottled send/spam chain | **LIVE** | 25 jobs, 25 targets, invalid address accepted |
| P1-4 | Any user mutates the shared job pool | **LIVE** | 5 jobs dismissed pool-wide by a nurse, HTTP 200 |
| P1-5 | CV re-upload orphans the old object | **LIVE** | Old file confirmed on disk, unreferenced, survives erasure |
| P1-6 | ai_usage never erased, export gaps | CODE | No FK, no delete, absent from export payload |
| P1-7 | 7-day JWT, no revocation | CODE | config.py lifetime; no token-version check |
| AI-9 | Silent empty "ready" drafts | **LIVE** | Fired at 674 tokens — mechanism corrected, §04 |
| AI-10 | Scoreless JSON dismisses a job as 0 | CODE | `parsed.get("score", 0)` + clamp-to-0 on TypeError |
| AI-11 | Failed extraction wipes the profile | **LIVE** | All fields nulled — Part I's fix doesn't work, §04 |
| AI-12 | "?" in employer-facing PDFs | **LIVE** | 5 substitutions; slim image has no fonts directory |
| AI-13 | Prompt hash covers only the system prompt | CODE | Hashes `_build_matching_prompt` alone |
| PIPE-14 | Manual hunt claims no lock; no unique key | CODE | API calls run_pipeline directly; no (source, source_id) constraint |
| PIPE-15 | Scheduled hunts drop radius and region | CODE | Union ctx pins region=None, never sets search_radius_km |
| PIPE-16 | No location/scope gate before AI spend | **LIVE** | 4 of 4 slots on irrelevant roles, §04 |
| PIPE-17 | Watermarks advance on partial failures | CODE | Per-page break, watermark stamped regardless |
| PIPE-18 | Worker lock undersized, released unconditionally | CODE | 45-min TTL, no renewal, no owner token |
| FE-21 | Core actions fail silently | CODE | No error.tsx, no ErrorBoundary, no rejection handler |
| FE-22 | PDF download no-ops in Safari | CODE | window.open after await; crafted error unreachable |
| FE-23 | API-down renders as an empty console | CODE | `.catch(() => [])` on all three loaders |
| OPS-1 | 60s timeout vs free-tier cold start | CODE | api.ts timeout: 60000 |
| OPS-2 | Off-site backup never runs | CODE | Env-only var, plist passes no environment |
| OPS-3 | CI gate broken and partial | **LIVE** | Lint fails with 11 errors; cron assertion contradicts render.yaml |
| OPS-4 | No restore path exists | CODE | ops/ has no restore script |
| OPS-6 | No privacy disclosure to users | CODE | Zero matches for privacy/processor terms in frontend |
| OPS-7 | Production guards miss storage config | CODE | Checks AUTH_SECRET/DB/CORS; no SUPABASE_* check |
| INFRA-30 | pytest + ruff in production images | CODE | requirements.lock lines 52, 58 |
| SUBMIT | No unique constraint on applications(draft_id) | CODE | Plain nullable FK, no constraint |
| HYGIENE | Dead TeamtailorScraper export | **LIVE** | Star-import raises AttributeError; zustand still unimported; stray 0-byte db present |

**The one thing this pass could not close: P0-6.** `RESEND_API_KEY` and `APPLY_FROM_EMAIL` appear nowhere in render.yaml or the runbook — that part is certain. Whether they're set as dashboard-only secrets is invisible from the repo, and the Render account was not touched. **Check the dashboard before beta; if unset, the flagship send-with-PDFs loop is dead in production.**

### §07 · What the live pass changes about the fix order

Part I's Day-1 sequence is sound. Three amendments:

1. **AI-9 needs the raise, not just the token budget.** Raising max_tokens would not have stopped the observed failure. Make `tailor_application` reject empty or missing keys the way `match_job` already does; treat the budget increase as a separate, smaller fix.
2. **AI-11's fix has to guard the write.** Raising inside the AI service changes nothing — the caller's except swallows it and the wipe still happens. Skip `_apply_extraction` on an empty result instead.
3. **Add the dedupe false positive (PIPE-20) to the AI batch.** It silently deletes the most relevant jobs from users' queues before any scoring happens — reads to the user as "this product doesn't find me anything." Same class of damage as PIPE-16, cheaper to fix.

On P0-1, the narrowed blast radius is worth knowing before writing the fix: the one-line `user_id` filter on the `top_matches` query, mirrored in the route's re-fetch, closes it. The surrounding tenancy machinery — the IDOR guard, the scoped list and detail routes, outbound identity in submit — all held under live cross-tenant probing.

---

## Methodology appendix

- **Wave 0:** full local test run (222 passed, 2 skipped, SQLite) + CI status check (red; backend legs fail at lint before tests).
- **Wave 1:** 7 specialist reviewers in parallel (security, AI systems, pipeline/concurrency, data layer, frontend, infra/ops, adversarial cross-cutting), each required to quote the motivating line for every finding with confidence ≥6/10 — unquotable findings suppressed at source.
- **Wave 2:** 7 verification agents, one per specialist, independently re-derived every claim from the code; adversarial mandate ("kill false positives"). Result: 61/62 confirmed as stated, 1 confirmed with corrected mechanism (DATA-5: the outcome stands — hunt fails and ScrapeRun sticks — but a try/except does exist and the failure surfaces as PendingRollbackError), 0 refuted.
- **Wave 3 (master):** re-verified all six P0s and the four highest-impact P1s by direct code inspection; merged 9 cross-agent duplicates (two P0s were independently found by two specialists each — counted once here); severity calibrated for a limited-user beta (impact-weighted, likelihood noted where exploitation requires an attacker).
- **Wave 4 (live pass — Part II):** independent live verification against the same HEAD: isolated rig (Postgres 16 container, backend :8011, frontend :3011, local storage), live Z.ai calls and all 7 live scrapers (272 real postings), two registered users with distinct CVs/countries/professions; no emails sent, dev DB and prod untouched, rig torn down. Reproduced 11 findings end to end, corrected 2 (AI-9 mechanism, AI-11 fix), found 1 new defect (PIPE-20 fuzzy-dedupe false positive), and verified the P0-2 fix live. Total findings after Part II: **63.**
- Coverage: all 133 code files in scope were within some specialist's file list; every specialist reported a full FILES READ list (auditable in the session log).

---

# Part III — Fix verification pass (2026-08-31, target `4bc70e9`)

> **Question put to this pass:** developers stated every finding in this report is fixed. Confirm or refute — including live cross-user leak testing.
> **Target:** origin/main tip `4bc70e9` (fix PRs #1–#20 squash-merged + dependabot bumps). NOTE: local `main` sat at the pre-fix `79dcfcb` during Part III setup and was fast-forwarded to the tip mid-pass; all verification below was completed against the fixed tree.
> **Method:** four parallel verification agents (security, AI/pipeline, frontend/ops, adversarial regression hunter) → master re-verification of every P0, every partial, and the claimed beta-blocking regression by direct code reading → live rig battery (47 checks) → browser test → local CI-equivalent.
> **Evidence:** CI on `4bc70e9` is GREEN on all six jobs (backend sqlite+postgres full-suite, frontend tsc+build, docker, pip-audit, blueprint). Local corroboration: `ruff --select I,F` clean; **352 passed, 2 skipped** (up from 222 tests — the fixes shipped ~130 new tests).

## Verdict summary

**Of the 63 findings: 60 VERIFIED FIXED, 3 PARTIAL (all with documented, accepted trade-offs), 0 NOT FIXED. All six P0s are fixed; four of them live-verified.** The developers' claim is substantively true — with one important caveat: **the fixes introduced one new real defect (REG1), live-confirmed, that should be fixed before or early in beta**, plus seven lower-severity new quirks.

### P0 dispositions

| ID | Verdict | Evidence |
|---|---|---|
| P0-1 | **FIXED — LIVE** | Hunt `top_matches` scoped by `user_id` in service AND route re-fetch (defense in depth); two-user regression test present. Rig: Alice's hunt returned only her ids (`top_ids=[12,11]`, Bob's were `[6,5]`); zero Bob CV-derived strings in her payload; his match detail + decision endpoints 404 for her. Cross-tenant sweep of every list endpoint clean. |
| P0-2 | **FIXED — LIVE** | Delete order applications→drafts→matches→profiles→user (+ai_usage, +user); CV file deleted only AFTER commit; same fix in `delete_job`; FK-chain test on the Postgres leg + failed-commit-keeps-CV test. Rig: on a REAL chain (approved match → submitted draft → application), erasure returned 200, all 6 tables zeroed, CV file gone post-commit, token dead (401), Alice untouched. |
| P0-3 | **PARTIAL — throttle works, IP key spoofable** | Per-IP buckets exist and fire (rig: 8×201 then 429×4 on distinct-email burst). BUT `_client_ip` prefers client-settable `True-Client-IP` then XFF **first hop** — client-controllable behind Render's append-only proxy (the code's own docstring documents this as an accepted brake, not attribution). Per-email + per-account buckets remain the real ceiling. Hardening item, not a blocker. |
| P0-4 | **FIXED — LIVE (browser)** | Editor state hoisted above the keyed container (`draftEdits` cache), pristine-derived values, dirty-guard confirm + `beforeunload`. Driven in a real browser: typed marker survived Dashboard AND the Part-II killer case (Review↔Sent sub-tab) round trips, with the unsaved-edit confirm dialog firing on every navigation and the "unsaved" chip showing. |
| P0-5 | **FIXED (code + tests + CI)** | `ops/storage_backup_lib.sh`: paginated bucket listing, authenticated GETs, count-verified atomic swap, traversal refusal; wired into backup.sh + off-site set; `ops/restore.sh` with dry-run, count verification, non-empty-target guard; rehearsal runbook; shell test suite. |
| P0-6 | **FIXED as far as the repo can show** | `RESEND_API_KEY` + `APPLY_FROM_EMAIL` declared (`sync:false`) in render.yaml + runbook table; storage production guards added; neutral error strings. **The Render dashboard check remains the one open human step** — a dashboard-only state is invisible from the repo. |

### Partials (accepted trade-offs)

1. **P0-3** — per-IP key spoofable on Render (above). Recommended post-beta: last-XFF-hop or an edge-guaranteed header.
2. **P1-6** — ai_usage is now DELETED on erasure (the privacy-critical half), but still absent from the export payload. Minor: rows hold telemetry only, and deleted users have no rows to export.
3. **OPS-2** — the plist now carries the env keys but ships them EMPTY; off-site still does not run until the owner fills `OFFSITE_BACKUP_TARGET` in the installed plist (runbook documents this as an owner step). The false "off-site runs nightly" claim is cured; the gap is now honest and waiting on the same one human step Part I flagged.

OPS-7's "reject local storage in prod" was deliberately substituted with blueprint-pinning (`STORAGE_BACKEND: supabase` literal) + key-presence guards — a reasonable engineering call, documented in the guard's comments.

### NEW defects introduced by the fixes (regression hunter + master + rig)

| ID | Sev | Status | Summary |
|---|---|---|---|
| **REG1** | **P1 — fix before/early beta** | **LIVE-CONFIRMED** | The new PIPE-16 scope gate writes **terminal** `out_of_scope` dismissal rows; the candidate query permanently excludes any (user,job) pair with a match row. Live: Alice with `include_remote=False` had the remote job dismissed `out_of_scope` (correctly, without AI spend); after re-onboarding with `include_remote=True`, **the job never returned** — scope changes are one-way for already-dismissed jobs. Since onboarding is user-editable any time, preference tweaks during beta silently shrink the pool forever. Fix: treat out-of-scope as a per-run skip (no row), or clear/re-evaluate `out_of_scope` rows when the scope hash changes. |
| REG2 | P2 | code-verified | The gate's jobtech/radius carve-out uses the reduced radius predicate for ALL jobtech rows, so remote/far jobtech rows still reach radius users' windows (asymmetric with REG1's permanence). |
| REG3 | P2 | **LIVE-CONFIRMED** | Manual jobs have no scope exemption: a blank/foreign-location manual job is dismissed `out_of_scope` → invisible and unapprovable. API-only today (frontend never calls POST /jobs). |
| REG4 | P2 | code-verified | Original-CV attachment pairs the draft snapshot's BYTES with the profile's CURRENT filename after a mid-review re-upload. |
| REG5 | P2 | code-verified | A failed extraction on re-upload keeps the PREVIOUS CV's identity fields (stale, unflagged) — including `reply_to` routing to the old CV's email. |
| REG6 | P2 | code-verified | `retry_application` still check-then-act without the `ready→sending` atomic claim — two rapid retries can double-send (the submit path is protected; retry isn't). |
| REG7 | P3 | code-verified | The new hunt-conflict 409 surfaces as axios's generic message; the hunt rate-limit also burns before the conflict check. |
| REG8 | P3 | code-verified | Process death between Resend acceptance and the outcome commit can double-send after the stranded-`sending` sweep restores the draft. Narrow window. |

### Live battery results (47/47 passed, then 5/6 dedupe probes)

Rig: throwaway Postgres 16 container, backend :8011 from the fixed tree with production-shaped env (email physically disabled — no RESEND key; AI capped at 4 jobs/run; no scraper keys so ingestion stayed deterministic), frontend :3011. Two real users (Swedish nurse / London engineer), real CV uploads (real GLM extraction), real matching (real GLM scoring), real tailoring.

- **Cross-user leaks: none.** Hunt response, match list, match detail, decision endpoint, draft list — all scoped; Bob's CV-derived content never appeared in Alice's payloads.
- **GDPR: full cascade on a real FK-constrained chain**; erasure 200; six tables zeroed; CV deleted after commit; token revoked; other user untouched.
- **Throttles: signup per-IP cap fires (429×4); job-create throttle fires; invalid `application_email` 422s at the boundary.**
- **State machines: double-submit 409; retry/submit leave the draft `submitted`; PATCH /jobs/{id}/status is gone (404) and the shared row was untouched.**
- **AI positive paths: extraction populated both profiles; matching scored only in-scope jobs (out-of-scope dismissed with ZERO ai_usage rows — PIPE-16's no-burn verified by row counts 8→14 for 2 in-scope jobs, 0 for the out-of-scope ones); tailoring produced a real non-empty draft (letter + 655-char tailored CV).**
- **Dedupe: all three role-identity pairs refuse to collapse; true duplicates (identical, case, reorder, Remote suffix, agency till/at) still collapse.** One pre-existing edge noted: differing diacritics in location ("Malmö" vs "Malmo") refuses collapse — a minor recall nit, not a regression.
- **Browser: typed draft text survived view switches and sub-tab switches with dirty-guard confirmation; the login page carries the Art. 13 disclosure naming Z.ai (outside EU) with a /privacy link.**

Safety notes: no email could leave (no key configured); the dev database, the live :8000 backend, the Supabase bucket and the real CV were never touched; the rig's one stray CV file in `backend/uploads/` was removed at teardown (the real CV was untouched); container and scratch dirs destroyed.

## Beta readiness verdict

**GO — conditional.** All six Part-I blockers and the Part-II live-repro defects are fixed and verified, most of them live. Three conditions before users arrive:

1. **Fix REG1's semantics** (out-of-scope = skip, not permanent dismissal) — a small change in `matcher_service` + a scope-widening regression test. Until then, beta users editing their location/remote preferences will silently lose jobs forever.
2. **Check the Render dashboard** for `RESEND_API_KEY`/`APPLY_FROM_EMAIL` (P0-6's repo-invisible half) and the live cron cadence.
3. **Fill `OFFSITE_BACKUP_TARGET`** in the installed backup plist (OPS-2) and run one storage-backup + restore rehearsal (the scripts exist and are tested).

---

# Part IV — External verification pass 2 + fix round (2026-08-31)

> An independent external pass against `4bc70e9` confirmed Part III's verdict on the six P0s and found one new blocker plus two compliance gaps. Every claim below was re-verified against the code by this review before any fix was applied; the three fixes then landed with tests. **Suite: 354 passed, 2 skipped; `ruff --select I,F` clean.**

## IV.1 · NEW P0 (found externally, verified, FIXED): DELETE /api/v1/jobs/{id}

**Claim verified in code and against live data.** `crud.delete_job`'s reference check counted only match/draft/application rows — so after removing the CALLER's (empty) rows, any posting nobody had matched was physically deleted from the **shared pool** by ANY authenticated user, permanently, for every user. Integer ids, trivially enumerable; the frontend never called the endpoint (pure attack surface, exactly like P1-4's PATCH). Read-only count on the production database at fix time: **76 of 385 postings (19%) deletable by any account that can log in.** (The external pass measured 156/399 = 39% hours earlier — the pool shrank between measurements; the vulnerability is identical.)

**Fix applied:** the route and the now-dead `crud.delete_job` are removed (same decision, same pattern as P1-4 — per-user removal already lives on `match_results.dismissed_reason`). Replacement test: `TestDeleteJobEndpointRemoved` asserts DELETE returns 405 and the shared row survives. Adversarial grep: zero surviving callers in app/tests/frontend.

## IV.2 · Compliance gap A (verified, FIXED): GDPR export omitted the CV

`grep -c cv_text account.py` → 0 before the fix. The most important document the user gave the platform was absent from their own data export (Art. 15/20), along with match reasoning (an AI assessment of the person) and ai_usage.

**Fix applied:** the export now includes `cv_text` + `cv_file_name` + the original PDF embedded base64 (best-effort — a storage hiccup skips the file but never fails the export; the text is the data), match `reasoning`/`recommendation`/skill lists/`dismissed_reason`, and an `ai_usage` section (kind/model/endpoint/tokens/cost converted micro-dollars→dollars/timestamps). New test pins all three groups. (This also closes Part III's P1-6 export partial.)

## IV.3 · Compliance gap B (verified, FIXED): no security headers on either tier

**Fix applied:**
- API: HTTP middleware sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Strict-Transport-Security` on every response. Test added.
- Frontend: `frontend/public/_headers` for Cloudflare Pages (static export — Next cannot add headers at runtime), including a CSP. **Documented trade-offs in the file:** `script-src/style-src 'unsafe-inline'` (Next's static bootstrap requires it; external script injection is still blocked — which is the protection that matters for a localStorage JWT), and `connect-src https:` + localhost (the API origin is build-time configurable; narrow it when the production host is permanent).

## IV.4 · External pass results upheld (spot-verified here)

Tenancy held under a deliberate cross-tenant sweep of all 25 live routes (every foreign resource 404; `/users/{id}` superuser-gated 403 on read/password-change/delete); six concurrent submits produced 1×201 + 5×409 and exactly one application row; erasure cascades and token revocation behaved (401 on password change; display-name change intentionally does not revoke); the scope gate fails closed on NULL/empty/whitespace/wrong-city/remote; zero emails/CV text/tokens in logs. Their retraction of the GET /jobs flag is correct — shared-pool visibility is invariant #7, not a leak.

## IV.5 · Still open after this round (updated after the REG1 fix round)

1. **REG1 — FIXED (same day, skip-not-dismiss).** The scope gate now SKIPS out-of-scope jobs per run instead of writing terminal `out_of_scope` rows — the gate is a free Python filter (no AI, no DB write), so re-evaluating it every run costs nothing, and a widened scope immediately re-admits previously-skipped jobs. Regression test `test_reg1_widened_scope_recovers_skipped_jobs` pins the exact live-proven scenario: strict run skips the remote job with **zero rows written**; after `include_remote=True` the job is evaluated and matched. Five existing assertions reworked from "dismissal row exists" to "no row, still eligible". No downstream consumer of the old rows existed (verified by grep — one writer, zero readers).
   - **REG3 deliberately left open (P2):** exempting manual jobs from the gate (so the user's own blank-location pastes are matchable) was attempted and REVERTED — without a `created_by` column on `job_postings`, an exemption also admits OTHER users' manual entries into this user's AI window, which is the exact spend leak PIPE-16 exists to stop. Skip semantics at least make the dead-end non-permanent. Real fix: `created_by` column + exempt only the creator. Documented in `stored_job_in_user_scope`'s docstring.
2. **Malformed-tailor retry (carried twice).** The AI-9 raise fires correctly (1 failure in 6 live calls, all `finish_reason: stop`) but there is no automatic retry — the draft lands `failed` and the user must press prepare again. The related token-budget call was vindicated live (7,153-char CV completed with room to spare) — no budget change needed.
3. **Event-loop blocking (carried).** `claim_hunt`/`release_hunt` run as synchronous DB calls inside the async hunt route (pipeline.py:63,89) — bounded (ms locally, more on a cold pooler) but should sit in `run_in_threadpool` like the hunt body.
4. **Owner steps (unchanged):** RESEND keys + cron cadence in the Render dashboard; `OFFSITE_BACKUP_TARGET` in the installed plist.
5. REG2, REG4–REG8 from Part III (P2/P3) unchanged.

## IV.6 · Verdict

The external pass's call — *"fix the DELETE endpoint, then open"* — is satisfied on the code side: the blocker is fixed with tests, both compliance gaps are closed, and **REG1 — the last live-proven data-loss defect — is fixed with its own recovery regression test.** **Suite after the full round: 355 passed, 2 skipped; lint clean.** Remaining before users arrive: the two Render-dashboard checks (RESEND keys, cron cadence) and the off-site backup target — both owner actions outside the repo. All fixes in this part are in the working tree, uncommitted; CI validates them on push.
