# WO-31 — AI capacity: survive 10–500 concurrent users

> Priority: **P0** (phase A is beta-safe now; phase B is the post-beta launch prerequisite) · Depends on: PR #132 ✅
> (in-process `AICallGate`, interactive 429 retry, zero-token 429 rows) ·
> Status: **design + measurements only (2026-10-16); build waits for owner approval**
> Origin: 2026-10-16 beta day, the first real multi-user load (3 signups).
> Rationale owners: AI config → `ARCHITECTURE.md`/`CLAUDE.md` "AI setup";
> residency → `MIGRATION.md` MIG-WO5; trial economics → WO-14 / WO-16.

## TL;DR

1. **The current key may not be used this way.** `GLM_BASE_URL` points at
   the **GLM Coding Plan** endpoint. Z.ai's subscription terms §4.2 limit
   that quota to officially supported coding tools. They prohibit
   "directly invoking model APIs from your own applications, bots,
   websites, SaaS products or other systems". §4.3 ("Personal-Use Only")
   prohibits letting "customers" use the quota. Penalties: suspension,
   termination, quota reclaimed, no refund (§4.1, §4.3, §6). Error code
   **1313** ("Fair Usage Policy") is how this would show up. The usage
   policy adds that concurrency "dynamically adjusts" by tier and that
   "Accounts with more than three violations may be banned". Scaling to
   500 users on this key would mean scaling a terms violation.
   **Owner decision (2026-10-16 13:45 CEST):** the Coding Plan key stays
   for the beta (a handful of testers). **Swapping to pay-as-you-go is
   the post-beta launch PREREQUISITE (phase B), not an option.** It is a
   separate small config change, not built by this WO, and it does not
   block the beta-safe work in phase A.
2. **The 3-sample keeper protocol is the biggest volume lever, not the
   prefilter.** Calls per job follow `needs_another_sample` exactly:
   sub-13 jobs cost 1 call, the dead-band costs about 2, keepers cost 3.
   On the 3 real users' data, **keepers use 51% of all match calls**. A
   perfect prefilter that dropped every sub-13 job would save only 23%.
3. **A keyword prefilter would silently hide good jobs.** All 3 CVs are
   English. Profile keywords are English (extraction translates them).
   Swedish ads are 76% and 87% of the two larger users' *good* matches (11% for the third). A lexical
   top-150 cut keeps only 37 of 85 good matches for the largest user.
   Stage 1 must work across languages (multilingual embeddings). It
   decides **order and timing, never dismissal**.
4. **Throughput, not cost, is the constraint.** Measured ceiling on the
   current key: ~40 calls/min sustained, 54 peak. Then 1302 throttling
   (37→6/min). Cost at pay-as-you-go list prices is ~$1.2–3.0 per active user
   per month. Five hundred first hunts in one hour, as today's pipeline
   runs them (570 calls each), would take **~119 hours** at 40/min.
5. **Architecture pick: one background AI worker plus a DB work queue**,
   not a Postgres distributed semaphore. The API makes only interactive
   calls. The worker owns all match calls, schedules users fairly,
   adapts its concurrency (AIMD, additive increase / multiplicative
   decrease) and backs off when the API reports interactive 429s. The
   cron only scrapes and enqueues.

## Evidence (production, read-only, 2026-10-16)

Read with `SET TRANSACTION READ ONLY`. Only aggregates were printed (no
CV or job text). Scripts are in the session notes folder
(`prefilter_estimate.py`, `probe2-4.py`). Rerun them before building.

### Load window 08:55–09:40 UTC
| Metric | Value |
|---|---|
| AI calls | 1,146 (1,116 match · 25 tailor · 2 extract · 2 suggest · 1 judge) |
| Sustained / peak | ~40/min · 54/min (09:37) |
| Throttle | 09:19: throughput 41/37 → 6 → 3/min for ~2 min, then recovered to ~40. One interactive tailor failed with 429 **1302** ("Rate limit reached for requests") |
| ai_usage 429 rows | 0. PR #132's zero-token 429 rows are merged but were not deployed at the time. The next incident will be visible |

### Per new user (whole first hunt)
| User | Profile → first visible match | Hunt span | Rows scored | <13 / 13–24 / ≥25 / ≥50 | Match calls | Approved |
|---|---|---|---|---|---|---|
| c52b | 3m20s | 16m | 209 | 107 / 68 / 34 / 9 | 351 | 3 |
| ea18 | 2m58s | 19m | 287 | 97 / 82 / 108 / 25 | 592 | 0 |
| bc96 | 4m07s | 26m | 406 | 193 / 66 / 147 / 85 | 765 | 3 |
| **Σ** | | | **902** | 397 / 216 / 289 / 119 | **1,708** | 6 |

- Calls reconcile with the sampling policy: 1×397 + ~2×216 + 3×289 ≈
  1,696, against 1,708 measured. **Keepers use 867 calls (51%).**
- Mean first hunt: **~570 match calls per user**. "Rows" excludes
  `duplicate` dismissals, which spend no AI.
- Users approved 0–3 matches out of 34–147 visible. Nobody reviews 147
  matches in a first session.
- Steady state (2 established users, 21 days): median **9** match calls
  per active day, max 24. The sample is small, so the cost model below
  uses 10–30.

### Per-call cost (ai_usage, last 30 days, list price)
| Kind | n | Prompt / cached / completion tokens | List $/call |
|---|---|---|---|
| match | 2,359 | 3,703 / 2,956 / 332 | **$0.0033** (today's 1,708 calls: $5.00 → $0.0029) |
| tailor | 59 | 3,532 / 2,928 / 1,852 | $0.0098 |
| judge | 14 | 2,993 / 745 / 148 | $0.0040 |

The prompt-cache hit rate on match calls is ~80–90%. The peer
session's 08:50–09:45 read: match 89.9% cached, tailor 91.5%; $3.53
recorded for the window's 3 partial first hunts. Z.ai prefix caching is
automatic, and the CV-first prompt layout (`CV_GUARD_CHARS` comment in
`ai_service.py`; 86% measured in WO-05) is what earns it.

**What caching saves:** an average match call (3,703 prompt / 332
completion tokens) costs **$0.0066 uncached** against **$0.0029–0.0033
measured**. Caching removes **~55%** of match spend. Onboarding goes from
570 calls × $0.0066 = $3.79 uncached to **$1.67** cached per first hunt.

Two consequences for the build:
- Stage 1 must **never touch the stage-2 prompt**, and the
  CV-first prefix stays byte-identical across one user's calls
  (assertion T4b).
- **The hit rate was measured on the Coding Plan endpoint only.**
  Confirm it on the pay-as-you-go endpoint in phase B. Every cost
  number below assumes it holds there; at 0% cache, double them.

### Prefilter feasibility (lexical, measured)
I ranked each user's scored jobs by a BM25-style overlap between the
profile (title + search queries + keywords + skills) and the ad, then
measured how many LLM-scored matches each top-N cut keeps:

| User | top-50 keeps good (≥50) | top-100 | top-150 | Deepest good-match rank |
|---|---|---|---|---|
| c52b | 7/9 | 8/9 | 9/9 | 142 of 209 |
| ea18 | 9/25 | 10/25 | 15/25 | 277 of 287 |
| bc96 | 16/85 | 23/85 | 37/85 | 399 of 406 |

Using the CV's own top terms as the query instead: bc96 reaches 65/85 at
top-150. That is still a 24% loss. Cause: English CV and keywords against
Swedish ads. 74 of bc96's 85 good matches have Swedish descriptions. **A
lexical stage 1 is rejected on evidence.**

## Provider and plan decision inputs (D1)

| | Z.ai Coding Plan (today) | Z.ai pay-as-you-go | Mistral EU (`api.eu.mistral.ai`, `zai-glm-5-2`) |
|---|---|---|---|
| Use in our SaaS | **Prohibited** (§4.2, §4.3) | Permitted (standard API) | Permitted |
| Base URL | `api.z.ai/api/coding/paas/v4` | `api.z.ai/api/paas/v4` | `api.eu.mistral.ai/v1` |
| Model / recalibration | glm-5.1 | **glm-5.1, same model, no recalibration**, prompt version pin unchanged | GLM-5.2, needs a new prompt version, SD re-measurement and backlog re-score. The 10-job bench passed 10/10 keep/dismiss (MIG-WO5) |
| Price /1M (in / cached / out) | flat; 5-hour + weekly credits (Lite 2k/10k, Pro 12k/60k, Max 28k/140k); off-peak 50% | $1.40 / $0.26 / $4.40 | $1.40 / $0.14 / $4.40 × 1.1 regional |
| Match call (measured token mix) | $0 marginal, until quota error 1308/1310/1316 | **$0.0029** | **$0.0028** (cheaper cache) |
| Rate limits | not published; observed ~40/min ceiling; 1302 throttle | **not published**: must be measured | Tiers by cumulative spend; real limits only in the admin console; RPS + tokens/min + tokens/month |
| Residency | Singapore; DPA names no SCCs (MIG-WO5) | same as Coding Plan | **EU processing**, DPA names SCCs, Z.ai not a sub-processor |
| Batch lane | — | — | 50% off, no SLA (cron backlog only) |

**D1 (decided by the owner, 2026-10-16):**
- **Beta (now):** the Coding Plan key stays.
- **Phase B, post-beta launch prerequisite:** move to Z.ai
  pay-as-you-go. It is config only, done as its own small change:
  `GLM_API_KEY` + `GLM_BASE_URL=https://api.z.ai/api/paas/v4` on
  **both** Render services (API and hunt cron). The model is the same,
  so calibration is untouched. Then confirm the cache hit rate there.
- **(b)** Run the MIG-WO5 Mistral EU flip as its own recalibration WO,
  triggered by "beta ends / GDPR posture goes live". The price is
  equivalent and the residency is better.
- **Measure before 100 users.** No provider publishes limits we can plan
  on. Run the build-phase rate probe (§Load test, step L3) against
  the pay-as-you-go endpoint (phase B).

Cheap-model option for stage 1 (if embeddings fail their gate): Z.ai
GLM-4.7-FlashX is $0.07 / $0.01 / $0.40. It could run a batched title
triage (one call per ~40 titles). That is AI-judged and has variance, so
it is the fallback, not the default.

## Cost model (per month, list prices, post-beta Z.ai pay-as-you-go; assumes the measured ~90% cache hit holds)

Assumptions: steady state 10–30 match calls per active user-day
(measured median 9, max 24). 20 drafts per active user-month × (1.3
tailor + 1 judge). Onboarding is one-off: today's uncapped first hunt is
570 calls ($1.67). With this WO's first-look budget it is ~100 calls
($0.29), and the backlog drips afterwards.

| Users | Steady match | Drafts | Total / month | Onboarding (one-off, uncapped → first-look) | Avg calls/min (steady) |
|---|---|---|---|---|---|
| 10 | $9–26 | $3 | **$12–29** | $17 → $3 | 0.07–0.2 |
| 100 | $88–264 | $33 | **$121–297** | $167 → $29 | 0.7–2 |
| 500 | $440–1,320 | $165 | **$605–1,485** | $835 → $145 | 3.5–10 |

That is **≈ $1.2–3.0 per active user per month**, about 5–12% of the
€24.99 WO-16 price. Cost is not the risk. **Burst throughput is**, and
the table below sizes it.

| Scenario | Calls needed | @ 40/min (today) | @ 300/min | @ 1,500/min |
|---|---|---|---|---|
| 500 signups/h, today's first hunt (570 each) | 285k | 119 h | 16 h | 3.2 h |
| 500 signups/h, first-look N=40 (~100 each) | 50k | 21 h | 2.8 h | 33 min |
| 500 signups/h, first 5 visible per user (~12 each) | 6k | 2.5 h | 20 min | 4 min |
| 500 users, one scheduled cron run (~15 each) | 7.5k | 3.1 h | 25 min | 5 min |

→ To get a time-to-first-match (TTFM) of 5 minutes or less for a
500-user burst, the provider must sustain **≈1,500 RPM**. Below that,
the admission design (§3) makes the wait honest ("you're in line, about
N minutes") and fair. It does not make it disappear.

## Design

### 1. Two-stage scoring: stage 1 orders, stage 2 decides

**Stage 1 — multilingual embedding relevance (deterministic, shared).**
- Each pool job gets one embedding, computed once at ingest from title +
  first ~1,500 characters. It is stored on `job_postings` (pgvector on
  Supabase). The pool is shared, so 500 users pay for each job once.
- Each user gets one embedding, computed from CV text + professional
  title + `search_queries`. It is recomputed when any of those change.
- Rank = cosine similarity, computed per run in SQL **after**
  `_apply_cheap_gates`. Language, PIPE-16 scope, dedupe, WO-18 twin
  collapse and WO-19 eligibility all run first, unchanged. Stage 1 only
  ever sees their survivors (function-span test, T5).
- Provider: Mistral `mistral-embed` on the EU endpoint (residency
  aligned with MIG-WO5) or Z.ai's equivalent while on Z.ai.
  Verify the price at build. Order of magnitude: ~1M tokens/day of
  inflow, cents per day.
- It is a new processor of CV text, so the privacy notice and processor
  list need updating (the `/privacy` + `/sv/privacy` i18n files).

**Stage 2 — unchanged.** It uses the same prompt,
`matching_prompt_version`, dead-band, `needs_another_sample` and
`resolve_samples`. Stage 1 never writes `score`, `tier` or
`dismissed_reason`, and the calibration pins do not move (T4).

**N, and what happens to the rest:**
- **First hunt: N_first = 40 jobs at `onboarding` priority.** These are
  the top 40 by rank. Justification:
  - top-ranked keep rates measured 56–80% (lexical top-25), so 40 jobs
    is about 20–30 visible matches;
  - users approved 0–3 on day one;
  - 40 jobs ≈ 100 calls, versus 570.
- **Later hunts: N_run = 60 per run per user.** Daily in-scope inflow is
  ~10–30 jobs, so this rarely binds.
- **The rest stay candidates with no row written**, the REG1 lesson: a
  skip, not a dismissal. They go to the `backlog` priority class in rank
  order and are scored when there is idle capacity (beta uncapped), or
  within the WO-14 daily allowance (post-beta). If they reach
  `MAX_POSTING_AGE_DAYS` unscored, they simply leave the pool.
- **Similarity floor:** jobs below the floor go to the lowest class,
  `deep_backlog`. **5% of them are scored anyway as an audit sample.**
  That gives `prefilter_miss_rate` (good matches found below the floor)
  as a live metric, so stage 1's recall is never assumed.
- **Gate (must pass before stage 1 ships), on the 902 labelled rows
  above:** recall of good matches (≥50) in the top 40 by rank must be at
  least 80% per user, and **zero good matches below the floor**. If
  embeddings fail this gate, the fallback is the batched FlashX title
  triage, measured on the same set. If both fail, ship ordering-free
  (fair share only) and say so.

**Expected reduction:**
- Interactive first-look: 570 → ~100 calls per signup (**−82%** in the
  burst window).
- Total calls while beta is uncapped: −0 to −12%. The backlog is still
  drained, and only below-floor jobs (≈ part of the 23% sub-13 share) are
  deferred indefinitely.
- Post-beta, under the WO-14 caps: the cap now spends on the
  best-ranked jobs instead of the newest. That improves quality, not
  volume.
- **The real volume lever is D2** (below), which is the owner's call
  because it touches calibration.

**D2 (owner, calibration-affecting; not built by default):** keepers
take 3 samples (51% of calls) so a visible score is ±6, not ±11. Two
options:
- (i) A keeper becomes visible after 2 samples (±8), saving ~17% of
  calls.
- (ii) Show the triage score as "provisional" and run the top-up at
  `backlog` priority.

Both change the invariant "a row the user sees is a 3-sample mean". Both
need a calibration-doc update and new tests. Recommendation: **neither
during beta.** Revisit once real RPM limits are known.

### 2. One prioritised AI gate across users and processes

**Pick: move all background matching into one worker service.** Rejected
alternative: a Postgres token bucket or advisory-lock semaphore that both
processes acquire.

| | Postgres semaphore / token bucket | **Single AI worker + DB queue** |
|---|---|---|
| Per-call DB cost | Advisory locks hold a connection for each 6–25 s call (Supabase pooler limits). Lease rows mean 2+ round-trips per call plus polling to wait in priority order | None per call. One `SKIP LOCKED` claim per work item |
| Priority | Needs a cross-process wait queue, which is hard to get right (fairness, starvation) | Built into the worker's scheduler. The API makes only interactive calls, so it never queues behind background work |
| Fair share across users | Not addressed, still FIFO per hunt | Native (§3) |
| Egress (the 2026-09-11 incident) | Polling semaphores from N threads | One idle claim query, backing off 1 s → 10 s |
| Failure | A lock leaked by a crashed holder throttles everyone | A lease expires and the item is re-queued |
| Cost | $0 | One more Render worker (~$7/month, `0.5c-512mb`) |

How it works:
- **API process:** interactive kinds only (tailor, judge, extract,
  suggest, recheck). It keeps PR #132's `AICallGate` (reserve
  irrelevant now: no background in-process) and bounded 429 retry.
- **Worker** (`python -m app.ai_worker`, a new Render
  `type: worker`): it claims `match_work` items and runs one job's
  **whole** sampling policy per item. The 1–3 samples and
  `resolve_samples` stay atomic. It writes on its own thread, the same
  rules as `_run_matching_loop`.
- **Concurrency controller (AIMD):** start at `AI_BG_CONCURRENCY_START=4`.
  Add +1 per 60 s with no 429s, up to `AI_BG_CONCURRENCY_MAX`. On any
  1302/1305, halve and sleep for Retry-After / retry-after-ms, or for
  backoff with jitter. On quota codes **1308/1310/1316/1317** (and their
  spend-cap variants 1318–1321), pause background work until the reset
  time parsed from the message, and alert. **1313 (fair-use) pauses all
  background work and pages the owner**: on the Coding Plan it is the
  terms risk materialising. After phase B it should never happen.
- **Interactive pressure, cross-process:** every interactive 429 in the
  API upserts one row, `ai_pressure(last_interactive_429_at,
  interactive_inflight)`. The worker reads it at most every 5 s (a
  single-row PK read). If an interactive 429 happened in the last 60 s,
  the worker drops to `max(1, C/2)` and stops claiming new items until
  the window clears. **Interactive calls always outrank background
  calls.** In-process that is structural (the API does no background
  work); across processes it is achieved by the worker yielding.
- **The cron becomes an enqueuer.** It scrapes (hunt lock kept for the
  scrape only), then enqueues per-user work at `scheduled` priority.
  `run_matching`'s inline loop stays only behind a flag, for tests and
  local dev. The PIPE-18b heartbeat lock for matching goes away: the
  queue's leases replace it.
- **Manual Hunt from the API:** scrape, then enqueue at `manual`
  priority, then return immediately. There is no more 600 s blocking
  request.

### 3. Admission and first-hunt spreading

- **Priority classes** (strict between classes, fair within):
  `onboarding` (a new user's first-look N_first) > `manual` >
  `scheduled` > `backlog` > `deep_backlog`.
- **Fair share within a class:** the claim picks the user with the
  fewest items served in the last 10 minutes. Ties go to the oldest
  enqueue. A 454-job user can no longer take every slot: a second
  signup's first 5 jobs run within one item of being enqueued.
- **First-visible-fast:** a new user's first 5 items get a temporary
  boost above other `onboarding` work. Measured ~12 calls produce the
  first visible matches. At 300 RPM, 500 burst signups all see matches
  within ~20 min, and the first ones within seconds.
- **No stampede:** signup and onboarding never call the provider for
  matching directly. A burst only grows the queue, it never grows
  in-flight calls.
- **UX (EN + SV, i18n rule):**
  - While items are queued or running, the console polls the slim status
    endpoint every 5 s. That is the existing pool-version-cached payload
    (egress fix), so it stays cheap; hidden tabs still stop.
  - Hunt Pulse shows "Checking your first matches — 12 of 40" and, while
    queued, "You're in line — about N min". N = queue position ÷
    observed rate, never a promise.
  - Matches appear as each item commits (rows already stream). A NEW
    badge pulses on arrival.
  - When background work is paused on a quota error: "Matching is paused
    — new matches resume around HH:MM". Never a silent stall.

### 4. Observability and alerting

- `ai_usage` gains `latency_ms`, `status`
  (`ok|rate_limited|quota|error|format`), `provider_code` (e.g. 1302),
  `priority_class` and `queue_wait_ms`. One migration; the residency and
  cost fields stay as they are.
- Derived, via a read-only ops script (`ops/ai_health.py`) plus Sentry
  metrics:
  - 429 rate per 5 min split interactive/background
  - queue depth and oldest-item age per class
  - worker C (current concurrency)
  - **TTFM** per new user (profile created → first visible match; today
    3–4 min with 1–3 users)
  - first-look completion time
  - `prefilter_miss_rate`
  - interactive `AIBusyError` count
- **Set `SENTRY_DSN` (EU region, `*.de.sentry.io`, per MIG-WO5's
  residency note).** WO-05 scrubbing is already built.
- Alerts:

| Alert | Severity |
|---|---|
| Any 1313 / 1309 | page |
| Any quota pause (1308/1310/1316–1321) | page |
| Interactive `AIBusyError` ≥ 1 in 15 min | warn |
| 429 rate > 5% over 10 min | warn |
| Oldest `onboarding` item > 10 min | warn |
| TTFM p90 > 10 min over 1 h | warn |
| Worker heartbeat missing 2 min | page |
| Sentry cron monitor on `jobfinderos-hunt` | — (closes the 2026-09-11 blind spot) |

## Acceptance criteria

1. **Launch gate (phase B):** before beta ends, production
   `GLM_BASE_URL` on both services is not the Coding Plan endpoint. The
   boot log prints the host. T10 becomes a hard CI failure when
   `BETA_UNCAPPED_HUNTS` flips to False, and is a warning until then.
   The cache hit rate on the new endpoint is measured and recorded here
   (≥80%, or the cost model is re-run).
2. Under the fake-provider load test (500 signups over 60 min, provider
   ceiling 300 RPM with 429 above it):
   - **zero interactive failures**
   - interactive p95 gate wait < 10 s
   - TTFM p90 < 25 min and p50 < 10 min
   - no user's first visible match later than 30 min
   - background 429 rate < 2% after AIMD settles
3. With 10 users and a 40 RPM ceiling (today's key): TTFM p90 < 5 min,
   and zero interactive failures.
4. The calibration pins (`test_calibration`, `TestTailorPromptCraft`)
   are byte-unchanged. No stage-1 value reaches `match_results`.
5. A job below N or below the floor has **no match row**. Changing the
   profile re-ranks it, and it can be scored.
6. Each `(user, job)` is scored at most once, even with two workers
   running (composite unique + `SKIP LOCKED`).
7. Stage 1 passes its recall gate on the labelled set. The result table
   is recorded here.
8. Every new UI string exists in EN and SV (i18n test).

## Red-first test plan (each one seen failing against production code first)

| # | Test | Red against today because… |
|---|---|---|
| T1 | Fair share: user A enqueues 400 jobs, then B enqueues 40. B's 1st visible match commits before A's 41st item | Per-hunt pools: A's backlog holds its slots, and nothing schedules across users |
| T2 | Cross-process yield: background load on the worker, then the API records an interactive 429. The worker's C halves and claims pause within one loop | No cross-process signal exists (the cron's gate is separate) |
| T3 | AIMD + Retry-After: fake 429 with `retry-after: 3` means the worker waits ≥3 s (virtual clock) and C halves. 1316 with a reset time pauses background until then and emits an alert | Match calls never retry or back off; the SDK's `max_retries=1` retries blindly |
| T4 | Calibration untouched: prompt hash pins unchanged; stage 1 never writes score/tier/dismissed_reason | Guard for the new code: revert-check by making stage 1 write `dismissed_reason` |
| T5 | Gate order (function-span check, standard #6): ineligible / out-of-scope / duplicate jobs never reach ranking or `match_work` | New code path: revert-check by enqueueing before `_apply_cheap_gates` |
| T6 | Cross-lingual ranking: English CV ("backend developer, Python") ranks a Swedish "Systemutvecklare Python" ad above an English "Marketing intern" ad | **The measured failure.** A lexical ranker fails this; it is red against the BM25 baseline |
| T7 | Atomic item: a crash after sample 2 of 3 writes no row; lease expiry re-queues; no partial-sample row | New code: revert-check by committing per sample |
| T8 | Two workers, one queue: every `(user, job)` is scored exactly once | New code: revert-check by removing `SKIP LOCKED` |
| T9 | GDPR delete mid-queue: queued items cascade; an in-flight item drops its result | New code (the user-gone path exists inline today) |
| T10 | Provider guard: CI fails if `render.yaml` / config defaults point at `/api/coding/` while `BETA_UNCAPPED_HUNTS` is False (warning during beta) | It passes today **and is wrong** for launch: red-first by asserting on the current default with the beta flag off |
| T4b | Cache prefix preserved: for one user, two match calls' prompts share an identical CV-first prefix ≥ `CV_GUARD_CHARS`, before and after stage 1 lands | Guard: revert-check by inserting rank metadata ahead of the CV |
| T11 | Interactive `AIBusyError` does not occur under the T-load profile with 10 users at 40 RPM | Today's two un-coordinated gates (API + cron) sum to 2× the cap |

Postgres-only tests (SKIP LOCKED, leases) run on the CI Postgres 16
leg. Non-Postgres runs mark them skipped. Every test that uses a fake AI
client must assert on the outbound artifact, i.e. which user's CV
reached which call (standard #2, TestOutboundIdentity pattern).

## Load test approach (fake AI client)

- **L1 — `tests/fakes/fake_ai.py`:** an OpenAI-compatible stub injected
  at `AIService.client`. It returns deterministic scores from
  hash(job_id, user_id). Score distribution is fitted to today's
  (44% <13, 24% band, 32% keep). Latency is drawn from a lognormal
  (median 6 s, p95 25 s, compressed by a time factor). Provider
  behaviour: a token bucket at R RPM and a concurrency ceiling K. Above
  either, it returns 429 with code 1302 and `retry-after`. Optional
  scripted 1316 quota windows.
- **L2 — `scripts/load_ai.py --users 500 --signup-window 60m --rpm 300
  --time-factor 60`:** runs the real worker, the queue and the
  interactive path (simulated users open drafts at Poisson rate) against
  Postgres (docker or the CI service). It prints TTFM p50/p90/max,
  interactive wait p95 and failures, 429 rate, queue depth over time,
  and per-user completion. Acceptance criteria 2–3 are its pass/fail.
  The CI smoke runs `--users 20 --rpm 40` (fast); the full run is
  opt-in (`RUN_LOAD=1`).
- **L3 — real-provider rate probe (owner-run, costs money, opt-in):** a
  ramp of synthetic, CV-free match calls (canned public job text +
  fictional CV) at 20 → 600 RPM, recording when 429s start. This is how
  we get the real limit for the pay-as-you-go endpoint. Fictional data only: no user
  CV leaves for a capacity test.

## Build order (each a PR, red-first)

**Phase A: beta-safe, reduces burst load now (Coding Plan key stays):**

0. No code, today: set `MATCH_CONCURRENCY=4` on both Render services
   (PR #132's note). Keep the beta caps lifted only while there are
   fewer than ~10 testers.
1. Cross-process interactive priority, the minimum version: the
   `ai_pressure` row plus the cron yielding to it (T2), and match calls
   backing off on 429 (T3). This is a small change inside today's
   architecture, and it closes "API + cron = 2× the cap".
2. ai_usage observability columns + `SENTRY_DSN` + alerts (§4). We need
   to see before we change anything.
3. `match_work` queue + AI worker + AIMD + `ai_pressure` (T1–T3, T7–T9,
   T11) + the fake client and load harness (L1–L2). The cron and the
   Hunt route become enqueuers.
4. Admission and UX (§3): priority classes, first-visible boost,
   streaming copy in EN+SV.
5. Stage 1 embeddings: measure the recall gate on the labelled set
   first, then ship (T4–T6, privacy-notice update).
   Steps 3–5 stagger first hunts: the fair queue spreads signups, and
   stage 1 limits the first look. They are beta-safe, and the sooner the
   better.

**Phase B: post-beta launch prerequisites:**

6. Key + base-URL swap to pay-as-you-go on BOTH services (separate small
   change, owner-created key), then the T10 hard gate. Confirm the cache
   hit rate on the new endpoint.
7. L3 probe on the pay-as-you-go endpoint. Then set
   `AI_BG_CONCURRENCY_MAX` and record the RPM here. This must happen
   before marketing pushes toward 100+ users.

## Out of scope / coordinated elsewhere

- Tailoring robustness (20k tailor cap, lenient JSON, interactive 429
  retry): done in PR #132 by the "make-tailoring-survive-z" session. Not
  duplicated here.
  - Data point handed back: there were **22 tailor calls per new user on
    10-16 but only 2 judge calls**. Most tailor attempts died before the
    guard (re-asks / failures). Worth confirming they have stopped after
    the #132 deploy.
- Pricing/tier caps: WO-14 / WO-16. Residency recalibration: MIG-WO5
  follow-up.

## Sources

- Z.ai subscription terms (§4.1–4.3, §6): https://docs.z.ai/legal-agreement/subscription-terms
- GLM Coding Plan overview (credits per window, off-peak): https://docs.z.ai/devpack/overview
- Z.ai usage policy (concurrency by tier, >3 violations → ban, no refunds): https://docs.z.ai/devpack/usage-policy
- Z.ai error codes (1302, 1305, 1308–1321): https://docs.z.ai/api-reference/api-code
- Z.ai pay-as-you-go pricing: https://docs.z.ai/guides/overview/pricing
- Z.ai standard endpoint: https://docs.z.ai/api-reference/introduction
- Mistral rate limits & tiers: https://docs.mistral.ai/deployment/ai-studio/tier · https://help.mistral.ai/en/articles/392924-how-do-api-rate-limits-work-and-how-can-i-increase-them
- Mistral EU endpoint, GLM-5.2 pricing, DPA: `MIGRATION.md` MIG-WO5 (verified 2026-08-27/30)

All pages were retrieved 2026-10-16. Neither Z.ai nor Mistral publishes
numeric API rate limits, which is why L3 exists.
