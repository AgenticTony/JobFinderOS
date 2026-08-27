# WO-15 — Career-site discovery (self-expanding direct-from-employer source)

> Priority: P2 · **Depends on: WO-06** (country routing) · Status: designed, not started
> Origin: the WO-08 Teamtailor deletion review. The deleted scraper's
> slug design was blind to custom domains; this replaces it with a
> mechanism that discovers employer career boards from data we already
> scrape. All facts verified 2026-08-27 (live probes + live DB).

## The finding this is built on

The deleted Teamtailor scraper required hand-curated slugs at
`{slug}.teamtailor.com` — dead on arrival, and blind even if configured:
many Swedish Teamtailor customers run career sites on their own domains.
Probed against hosts harvested from our own Platsbanken rows (4/4 HTTP
200, two confirmed genuine JSON Feed 1.1):

```
jobs.avaron.se/jobs.json     → JSON Feed 1.1, "Avaron AB",  100 jobs
jobb.softhouse.se/jobs.json  → JSON Feed 1.1, "Softhouse",   10 jobs
jobb.a-hub.se/jobs.json      → HTTP 200
work.chopchop.se/jobs.json   → HTTP 200
```

Those two verified feeds alone hold 110 jobs against 38 from the entire
current Platsbanken scrape — direct-from-employer inventory, often listed
before or instead of any aggregator. This is inventory competitors
scraping Indeed/LinkedIn do not have.

**Verified harvest signal (live DB, 2026-08-27):** jobtech 38 rows, 30
with `application_url` (79%), 21 distinct hosts. All other sources carry
none — the signal lives in Platsbanken, which is why WO-06 gates this.

## Design: discover, don't configure

1. Post-scrape harvest step (cheap, in-pipeline): collect hosts from
   `application_url` on rows from verified primary sources.
2. Probe `https://{host}/jobs.json` once per host; classify the response
   (JSON Feed 1.1 → teamtailor-family; also recognize Greenhouse
   `boards-api.greenhouse.io` / Lever `api.lever.co` patterns from the
   same application_url hostnames — the mechanism is vendor-neutral).
3. Cache verdicts in a `career_sites` table: host, verdict, first_seen,
   last_probed, jobs_at_probe. **Negative results are cached too** —
   re-probe failures quarterly, never per cycle.
4. Poll positive hosts on the normal hunt cycle as a first-class source.

## Acceptance criteria

1. Discovery adds hosts without any configuration; no slug list exists
   anywhere in the codebase (grep-provable).
2. Crawling discipline, tested: identifying `User-Agent` with contact
   route, per-host rate limit, robots.txt respected, probe timeout,
   hard cap on probes per cycle; probes only target hosts derived from
   verified-source `application_url` values — never arbitrary input.
3. Negative cache proven: a host that 404s is probed once, not again
   within the TTL (test with a stubbed clock or recorded probe log).
4. Cross-source dedupe: the same job on Platsbanken and the career site
   (different IDs/URLs) collapses via the existing dedupe gate — tested
   with a real pair from the verified feeds.
5. The pipeline's per-source summaries surface discovery stats (hosts
   probed / found / polled) so growth is visible on the dashboard.

## Caveats carried from the review (do not skip)

- **ToS gate before shipping:** the feed being public does not
  automatically permit redistribution. Teamtailor ToS check outstanding —
  same discipline as the Adzuna commercial-terms item. Greenhouse and
  Lever publish documented public job-board endpoints (API consumption,
  not crawling) — verify current terms when those land.
- **Sample size:** 4 hosts proves custom domains work and disproves the
  slug design; it does not estimate the Swedish Teamtailor fraction.
  Re-measure discovery yield after WO-06 delivers a Platsbanken-dominant
  pool, before investing in vendor-specific parsers.
- **Sequencing is a dependency, not a preference:** building this before
  WO-06 means harvesting from 38 rows.

## Explicitly out of scope

Restoring the old slug scraper (`git show 99e50f5^:…/teamtailor.py`
recovers it if ever needed — recorded here so nobody 'helpfully' rebuilds
the flawed design).
