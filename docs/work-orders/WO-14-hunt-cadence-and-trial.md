# WO-14 — Hunt cadence and trial gating

> Priority: P1 · Depends on: nothing for v1 (WO-05 for v2) ·
> **Status: DONE 2026-08-31** (see Execution record; one dashboard-only
> human step remains, recorded below)
> Related: WO-13 (billing), ARCHITECTURE "Stack verification"

## The finding that shapes this

`match_results` carries `UniqueConstraint("user_id", "job_id")`, and the matcher
selects only jobs with no match row for that user
(`outerjoin ... MatchResult.id.is_(None)`). **A job is scored once per user,
ever.** Pressing Hunt fifty times does not re-score anything — runs 2–50 find
nothing to do and cost nothing in AI terms.

So per-user AI spend is bounded by *distinct jobs in that user's pool*, not by
button presses. This kills the obvious conclusion ("the Hunt button is a cost
leak") and redirects the work:

- What repeat Hunt presses actually cost is **job-board API quota** — every
  press re-scrapes.
- What costs real AI money is **pool size × users**, and the single largest
  moment is a new user's **first hunt**, which scores their whole accumulated
  backlog.

## Deliverable 1 — make repeat presses a free no-op

Do **not** remove the Hunt button. This is a stress-reduction product for people
who feel powerless; "check for new jobs now" is agency, and removing it trades a
real product cost for scrape quota, not AI spend.

Instead:

- Before scraping, check the last scrape time (`scrape_runs` already records
  this). If the enabled sources were scraped within a cooldown window, **skip
  the scrape** and return `"No new jobs since 14:20"` with the existing queue.
- The matching pass still runs — it is already cheap, because there is nothing
  unscored to work on.
- The button becomes free precisely when it would otherwise be wasteful, and
  the user keeps the control.

Server-scheduled hunts continue to do the real work on their own cadence.

## Deliverable 2 — stagger the first hunt

The most expensive moment in any user's life is their first hunt. It is also
the moment they are least committed, which makes it the worst possible place to
spend the most money.

**Score the day-1 backlog freshest-first, spread over days rather than at once.**
This is a cost fix and a retention mechanic pointing the same direction: it
manufactures the "new matches every morning" habit loop the product wants
anyway.

One nuance to build in: **do not drip evenly from day one.** The first session
must prove the product works. Give day 1 roughly 2–3× the daily allowance, then
settle into cadence. Still bounded, still cheap, but the first impression lands.

## Deliverable 3 — trial gating

**Cap scoring, not display.** Showing 3 jobs after scoring 40 costs exactly the
same as showing 40 — the money is spent before anything renders. A display cap
saves nothing.

**Cap at ~10 scored/day, not 3.** The keep rate is ~30%: cap scoring at 3/day
and the user sees roughly one match a day with most days empty — a trial that
demonstrates a broken product. Score ~10 to surface ~3 keepers, which makes
"3 new matches a day" an honest promise.

Economics at the verified $0.00358/job:

| trial design | jobs scored | cost/trial | CAC at 20% conversion |
|---|---|---|---|
| 7-day, uncapped | ~410 | $1.47 | $5.88 |
| 3-day, uncapped | ~250 | $0.90 | $3.60 |
| **7-day, 10 scored/day** | **70** | **$0.25** | **$1.00** |

### v1 / v2 — do not block on unbuilt infrastructure

- **v1: count cap.** 10 jobs scored per day per trial user, enforced through
  the existing per-user rate-limit buckets. Works today.
- **v2: AI-spend budget.** A per-trial budget (~$0.30) the pipeline spends
  down. Better, because it self-regulates across wildly different pools — a UK
  user against Reed's inventory and a Swedish nurse in a thin niche cost the
  same rather than getting the same job count at very different prices.
  **Depends on WO-05's per-call cost table**, which does not exist yet.

Ship v1. Move to v2 when the table lands.

## Known gap this work order does not close

`run_matching` still only *defaults* its limit:

```python
limit = limit or settings.MAX_JOBS_PER_MATCH_RUN
```

A passed value is used verbatim. Both current callers are now bounded at the
route boundary — `/matches/run` at `le=100`, `/pipeline/run` at
`le=MAX_JOBS_PER_MATCH_RUN` (25) — so there is **no live hole**. But:

1. The protection is per-route, not structural. The next caller inherits
   nothing. This is the same shape as the bug class that produced three P0
   cross-tenant leaks: correct on one route, unsafe on another.
2. **The two ceilings disagree** — 100 via one route, 25 via the other, for the
   same underlying spend.

Fix: clamp inside `run_matching` (`min(limit, MAX_JOBS_PER_MATCH_RUN)`) so
every caller inherits the bound, and reconcile the two route ceilings to one
number. The schema bounds then become defence-in-depth rather than the only
defence — which is the Layer-0 principle this codebase already applies to
tenancy, applied to spend.

## Acceptance criteria

- [x] Repeat Hunt within the cooldown performs **no scrape**, returns the
      last-scrape timestamp, and is asserted by a test counting scraper calls
      — `TestRepeatHuntCooldown` (counted `JobtechScraper.fetch` calls;
      second press leaves the counter untouched, summary carries
      `skipped_cooldown` + "no new jobs since HH:MM UTC"; backfill exempt)
- [x] First-hunt backlog is staggered; day 1 allowance is larger than
      subsequent days — `TRIAL_DAY1_SCORE_CAP=25` vs `TRIAL_DAILY_SCORE_CAP=10`
      (asserted in `test_day1_allowance_is_larger_and_binds`: 40 candidates,
      exactly 25 scored, backlog drips freshest-first over following days)
- [x] Trial cap is enforced on **AI calls made**, proven by a test that counts
      calls — the cap counts AI-scored rows only (cheap-gate dismissals
      excluded), lives INSIDE `run_matching` so manual and scheduled hunts
      both inherit it, and `_fake_ai` counts the actual evaluations
- [x] A trial user at their daily cap gets a clear message, not a silent empty
      queue — service returns `status=daily_cap_reached` + message;
      `/matches/run` short-circuits synchronously with the same message; the
      UI shows it as an amber status notice (not the red failure banner)
- [x] `run_matching` clamps its own limit; a test calls it directly with an
      absurd value and asserts the bound holds —
      `test_absurd_limit_is_clamped_structurally` (limit=99999 with
      MAX=3 → ≤3 evaluations)
- [x] The two route ceilings agree — both bound at
      `MAX_JOBS_PER_MATCH_RUN` (was `le=100` on `/matches/run`);
      `TestRouteCeilingsAgree` proves 150 passes validation and MAX+1 422s

## Execution record (2026-08-31)

- Config: `HUNT_SCRAPE_COOLDOWN_MINUTES=45`, `TRIAL_DAILY_SCORE_CAP=10`,
  `TRIAL_DAY1_SCORE_CAP=25` — the cap applies to ALL users while no plans
  exist (WO-16 lands tiers); v2's old blocker is GONE (WO-05's per-call
  cost table shipped as `ai_usage`), so the spend-budget upgrade is
  unblocked whenever wanted.
- Tests: `tests/test_wo14_hunt_gating.py` (+ one class in
  `test_multiuser.py` for the route boundary, using that module's proven
  client harness). Written RED first per the queue's rule 2 — all five
  behaviors failed against the pre-change code, then green: 369 passed /
  2 skipped suite-wide.
- Day-1 semantics: a user is on the boosted allowance while they have no
  scoring history or their first-ever row is under 24h old.
- Cap counts AI evaluations only (`decision IS NULL OR
  dismissed_reason='below_threshold'` today, UTC) — duplicate/keyword/
  no-description dismissals write rows but spend no AI.

## Hunt cadence reconciliation (the 2026-08-31 live finding)

Measured: the LIVE cron fired at 00:00, 03:00, 06:00, 09:00, 12:00 UTC
(every 3h) while every repo surface says twice daily. Official docs
(render.com/docs/cronjobs): schedules are standard five-field cron, **UTC
mandatory**, one active run at a time; blueprint `schedule` is applied to
the existing service on sync (render.com/docs/blueprint-spec — fields not
flagged immutable are updated per sync).

Repo state is CORRECT and mutually consistent (CI-enforced):
`render.yaml` `schedule: "0 6,18 * * *"` ⇔ `HUNT_TIMES_UTC: "06:00,18:00"`
⇔ runbook WO-07 — only the live dashboard's schedule is wrong, which also
makes the dashboard's next-hunt countdown lie to users.

**The one remaining step (owner, dashboard-only — no repo change):** set
the `jobfinderos-hunt` cron's schedule in the Render dashboard to
`0 6,18 * * *` (or trigger a blueprint sync, which reapplies render.yaml).
Until then the product is CORRECT but hunts 8× more often than designed.
