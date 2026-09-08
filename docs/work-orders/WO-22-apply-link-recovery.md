# WO-22 — Apply-time link recovery: on a dead portal, hand off the live twin from the pool

> Priority: P2 · Depends on: WO-18 ✅ (its pair machinery is the lookup
> engine) · Status: designed, not started
> Origin: 2026-09-08 second review of `MadsLorentzen/ai-job-search`
> (same repo that seeded WO-18's source-ranking tiebreak, WO-19, WO-20,
> WO-21). Its apply flow (`.claude/commands/apply.md` +
> `09-web-research.md`) treats an unreachable posting as a RECOVERY
> problem, not a warning: escalate until the employer's own posting is
> found; only then declare unavailable.

## Why this exists

WO-18 stops the dead-link copy from WINNING the collapse when a better
twin exists in the pool. But when the pool holds ONLY the degraded copy
(aggregator-first ingestion, the original never scraped), the user still
gets a dead link — now with a warning (`_probe_apply_portal`, corrected
2026-09-08: 401/403 name the bot-block, only 404/410/5xx say expiry).
The reference repo's escalation ladder does better: a dead or walled
portal triggers a search for the employer's own posting, because "the
employer's own careers portal is almost always richer than the
aggregator that surfaced the posting, and it carries the reference ID
and grade that aggregators drop."

## Design

At hand-off (`submit_draft` browser/manual path), when the probe
returns a DEFINITE failure (HTTP 404/410 or 5xx — the expiry class,
never 401/403/timeout):

1. **Pool twin lookup** — the cheap, local version of their web search:
   query `job_postings` for same-`title_bucket` candidates and confirm
   with `likely_same_job` (WO-18's exact machinery), pick the best by
   `collapse_preference`. If a twin with a live-looking apply path
   exists (`application_url`/`application_email`, or a non-degraded
   `url`), hand off THAT link and say so on the application row:
   "the scraped apply link was dead; opened the original posting from
   <source> instead."
2. **Re-probe the twin** before trusting it — a twin whose own link is
   also dead is not a recovery.
3. If no twin: the current warning stands, unchanged.
4. Future (after WO-15): the lookup gains employer-direct career-site
   rows — their ladder's true endgame; ours arrives with the pool.

Not doing (deliberately): their robots.txt-aware curl-with-browser-
headers retry. It circumvents bot blocks that a site owner may intend
(robots opt-out), and our probe exists only to inform — recovery
through OUR OWN pool has no such question. Their "never draft from the
title" rule we already enforce harder (WO-01 fabrication guard).

## Acceptance criteria (red-first)

1. Dead apply URL + live twin in the pool → application row carries the
   twin's link + the substitution note; assert the twin's URL is what
   the row holds (outbound artifact, not row count).
2. Dead apply URL + twin whose link is also dead → original warning,
   no substitution.
3. 401/403/timeout probe outcomes never trigger the lookup (they are
   not death).
4. No twin → current behavior, byte-identical warning.
5. All substitutions logged (audit trail on the application row).

## Relation to the queue

- WO-20's nudges ("Finish applying" cliff) benefit from the same
  liveness signal — a shared helper, not a dependency.
- The 2026-09-08 probe fix (401/403 semantics) already shipped with
  WO-18's PR — this WO starts from the corrected baseline.
