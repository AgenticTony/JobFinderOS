# WO-18 — Collapse location-less cross-board duplicates; prefer original-source apply links

> Priority: P1 (beta) · Depends on: none
> Status: not started

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

## Why every existing gate misses these

- **Exact gate**: `dedupe_key_for(title, company, location)` — the
  careerjet copies carry **no location**, so their keys differ from the
  original's (`...|lund` vs `...|`).
- **Match-time cross-board gate**: dismisses when a posting with the
  SAME key already has a match — same key problem.
- **Fuzzy gate (the Pågen rule)**: `likely_same_job` requires the same
  municipality on both sides — a location-less copy never qualifies.

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

## Data for reproduction

- Rows: `job_postings` ids 47, 424, 425 (scraped 2026-08-28/30).
- Liveness, probed 2026-08-31: both jobviewtrack URLs → 502;
  `arbetsformedlingen.se/platsbanken/annonser/31322561` and its
  Aplitrak apply URL → 200.
- The application trail: draft #… (careerjet copy) `submitted`,
  application `manual_pending` with the dead URL — the record was
  honest; the incident is what motivated the probe + this WO.
