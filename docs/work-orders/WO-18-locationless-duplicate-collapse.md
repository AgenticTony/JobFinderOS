# WO-18 — Collapse location-less cross-board duplicates; prefer original-source apply links

> Priority: P1 (beta) · Depends on: none
> Status: **COMPLETE — 2026-09-08** (see execution record)

## Why (the incident, 2026-08-31)

The same ad — **"Junior Developer på stort bolag i Lund"** (ManpowerGroup
Nordics via Aplitrak) — entered the pool **three times**:

| row | source | apply link | matched | outcome |
|---|---|---|---|---|
| job_postings #47 | jobtech #31322561 (original) | `aplitrak.com/...` — **live (200)** | 55 | user **rejected** |
| #424 | careerjet (re-post) | `jobviewtrack.com/...` — **dead (502)** | 65 | approved |
| #425 | careerjet (re-post) | `jobviewtrack.com/...` — **dead (502)** | 68 | approved |

All three consequences the dedupe gates exist to prevent, observed live:

- **3× AI spend for one job.**
- **Three contradictory verdicts for the identical ad** (55/65/68 — the
  spread is expected ±11 single-sample noise, but the user sees three
  different numbers for one job and reasonably distrusts all of them).
- **The user approved the two copies whose apply links are dead and
  rejected the one copy with a live apply portal.** The browser hand-off
  then pointed them at a 502. (Mitigated same day by the hand-off
  liveness probe — `draft_service._probe_apply_portal` — which warns on
  a definite HTTP ≥ 400; this WO is the structural fix.)

## Why every existing gate missed these

*(Corrected 2026-09-08 against the live rows — the original write-up
below assumed the careerjet copies carried no location.)*

The real rows carry **conflicting locations and differing company
strings**: #47 is `Experis AB` / `Malmö, Skåne län`, the careerjet twins
are `Manpower` and `Experis` / `Lund, Skåne`. (Careerjet can also return
no location at all — both failure modes are covered.)

- **Exact gate**: keys on normalized title+company — the three company
  strings (`experisab` / `manpower` / `experis`) hash differently, and
  the key does not normalize legal suffixes.
- **Match-time cross-board gate**: dismisses when a posting with the
  SAME key already has a match — same key problem.
- **Fuzzy gate (the Pågen rule)**: `likely_same_job` required the same
  municipality on both sides — `malmö` vs `lund` is rejected before any
  employer-link route runs. A copy with NO location was equally dead.

The 2026-09-02 sister-brand fix (PR #72, the Dispatcher incident) added
the shingle-identity employer link but kept the municipality
precondition — it collapsed pairs 424↔425 (both "Lund") but not the
original-vs-copy pairs. That precondition was the remaining blocker; the
shingle route itself is reused, not rebuilt.

## Constraint: precision first (DEDUPE-FP precedent)

Never collapse a pair that might be different jobs. The fuzzy gate
already refuses one-word role differences (Engineer vs Scientist) — a
false positive there collapses two real jobs into one. Any new rule
must be at least as precise: **title-only matching is not acceptable**
("Junior Developer" exists at every company in the country); company
equality must carry the weight.

## Design sketch (for discussion)

1. **Secondary match-time key when EITHER side lacks location**:
   normalized title + company only. A location-less copy of an
   already-matched job is dismissed as `duplicate` through the existing
   per-user flow.
2. **Both copies location-less in one batch**: same rule within the
   batch (`kept_batch` already handles this shape).
3. **Prefer the copy with a direct apply path** when collapsing. The
   fuzzy gate already has the flip mechanism (agency re-post of a
   direct ad). Generalize "agency copy" to **link-degraded copy**: a
   posting whose URL is an aggregator redirect (jobviewtrack.com, …)
   AND which has neither `application_url` nor `application_email` is
   the copy to dismiss. In the incident this alone would have kept the
   jobtech original (live portal) in front of the user.
4. *Optional, ingest-side*: stop storing a third copy of an already
   twice-stored secondary key. Match-time-only is acceptable for beta.

**Addition 2026-09-08 — source ranking as a collapse tiebreak**
(external validation: `MadsLorentzen/ai-job-search`, whose apply flow
prefers the employer's own posting over aggregator listings because
aggregators drop requisition IDs and seniority grades). Extend rule 3:
when collapsing copies, prefer the row from the higher-ranked source —
official/board APIs (jobtech, reed) and employer-direct (WO-15
career-site rows) over aggregators (careerjet, adzuna) — even when
both copies carry a live apply path. The aggregator copy's description
is a subset of the original's facts; the score the user sees should be
computed on the richest text, not on whichever copy ingested first.
This also complements the existing `kept_batch` preference with a
deterministic ordering instead of arrival order.

## Acceptance

- jobtech original matched + careerjet location-less copy enters the
  window → copy dismissed `duplicate` (per-user row only, shared rows
  untouched).
- Two location-less copies in one batch → exactly one survives, and the
  survivor is the one carrying `application_url` or
  `application_email`.
- Same title, **different company**, both location-less → never
  collapsed.
- One-word role difference, location-less → never collapsed
  (DEDUPE-FP parity).
- Live check: the three incident rows — post-fix, a fresh user's run
  evaluates exactly one of them.

## Execution record (2026-09-08)

**Shipped in `app/core/dedupe.py` + `app/services/matcher_service.py`,
tests in `tests/test_units.py` (three new classes, 19 tests), written
red-first — 14 failed against the pre-change code before
implementation. Suite: 436 passed / 2 skipped.**

What was built:

1. **Location tiers in `likely_same_job`** (the structural fix): both
   locations present and equal → all employer-link routes (unchanged
   behavior); either missing → all routes, the employer link carries
   the pair (WO-18 rule 1 — never title alone); both present but
   DIFFERENT → **ad-text identity (shingles) only** — the two-offices
   rule: same title + same company in two cities can be two real
   openings, so company equality and title-naming must not override an
   active location conflict. The live incident pairs (Malmö vs Lund)
   link via shingles; company-only pairs across conflicting cities
   never collapse.
2. **`collapse_preference`** (WO-18 rule 3 + the 2026-09-08
   source-ranking tiebreak): the survivor of any collapse is the copy
   with, in order — a non-degraded link (`is_link_degraded`:
   aggregator-redirect host AND no `application_url` /
   `application_email` — `jobviewtrack.com` today), a direct apply
   route, the higher-ranked source (`SOURCE_RANK`: official boards
   jobtech/reed + manual 0, curated feeds 1, aggregators
   careerjet/adzuna 2), the direct employer over a staffing agency
   (the Pågen rule, absorbed from the old `_is_agency_posting`
   heuristic), and the fuller description. Applied at both collapse
   sites: the fuzzy gate's stored-match flip and in-batch survivor
   choice (generalized from agency-vs-direct to any preference), and
   the twin pass below.
3. **Twin pass in `_apply_cheap_gates`** (the all-history net): the
   fuzzy window only sees undecided matches ≤14 days old — decided,
   auto-dismissed, and older matches blocked nothing. The twin pass
   snapshots the user's matched postings pre-run (with a
   `was_undecided` flag so mid-run retirements can't poison it),
   buckets by normalized title, confirms candidates with
   `likely_same_job`, and: a stored DECIDED or dismissed match drops
   the incoming twin (user judgments are never re-opened); a stored
   UNDECIDED match loses to a strictly preferred incoming copy (the
   flip — arrival order can no longer lock in the dead-link fragment).
4. **Not done, deliberately** (per the WO's own optionality): no
   ingest-side storage change, no backfill — historical rows keep
   their recorded decisions; the gate protects every fresh user and
   every future copy. `scripts/dedupe_existing_matches.py` keeps its
   score-first winner rule: it re-collapses ALREADY-SCORED rows (keep
   the best score the user saw), a different context from choosing
   which copy to score.

Verification: red-first (14 red pre-change, all green after); full
suite 436 passed / 2 skipped; flow test PASS; CI-shaped ruff
(`--select I,F`) clean; function-span verification on
`_apply_cheap_gates` / `_dismiss_fuzzy_duplicates` / `_collapse_key`;
**live check against the production rows** (read-only): all three
incident pairs now collapse through `likely_same_job`, and
`collapse_preference` strictly prefers #47 — the live-portal jobtech
original — over both jobviewtrack copies.

Known accepted corner (documented in code): if two NEW variants of one
job enter the same run AND both beat a stored undecided match, the
in-run preference between the two variants resolves only through the
fuzzy gate's in-batch list; in the pathological both-flip-the-stored
shape both can survive one run (one extra AI call, correct single-copy
display thereafter). This predates WO-18 (the agency flip had the same
structure) and is not made worse by it.

## Data for reproduction

- Rows: `job_postings` ids 47, 424, 425 (scraped 2026-08-28/30).
- Liveness, probed 2026-08-31: both jobviewtrack URLs → 502;
  `arbetsformedlingen.se/platsbanken/annonser/31322561` and its
  Aplitrak apply URL → 200.
- The application trail: draft #… (careerjet copy) `submitted`,
  application `manual_pending` with the dead URL — the record was
  honest; the incident is what motivated the probe + this WO.
