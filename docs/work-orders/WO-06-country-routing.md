# WO-06 — Country routing in sourcing

> Priority: **P0** · Depends on: nothing · Status: executed 2026-08-27
> Fixes ARCHITECTURE D1 ("no country routing in sourcing — highest
> severity"). The product does not currently work for its target user:
> a Malmö user's pool is dominated by jobs they cannot take.

## The diagnosis (live data, 2026-08-27)

Packs already scope WHICH sources run per country (`source_packs.py`),
and Careerjet already routes locale (`sv_SE`/`en_GB`). Two defects
remain, and they compound:

1. **The location gate has no country dimension.** For an
   `include_remote` user, ANY remote job passes — including "Remote ·
   USA" jobs that require US work authorization. Measured: the user's
   biggest location bucket is USA (73) against Malmö (11). The gate runs
   at SCRAPE time (`scrape_source` filters before storage), so foreign
   remote jobs aren't just scored — they're stored, flooding the pool
   and burying local keepers.
2. **The highest-precision source is volume-starved.** Keeper rate by
   source (live queue): jobtech **75%** (27/36), jobicy 27%, arbeitnow
   13% (127 rows). The Swedish official whole-market API fetches ONE
   page (limit 100) per query; the pool it should dominate contributed
   36 rows.

## The fixes

**A — Country-aware location gate** (`passes_location_filter` +
new `country_lexicon`): resolve a job's location to a country via a
word-boundary lexicon (country names in English/Swedish/German variants
+ major cities). Rule: a job whose location resolves to a DIFFERENT
country than the user's is blocked, regardless of remote flag — remote
in the US is not remote for a Swede. Unresolvable locations ("Remote",
"Anywhere", empty) keep today's behaviour: pass for `include_remote`
users. In-country jobs are untouched (local terms path, unchanged).

**B — Jobtech pagination**: walk `offset` per query up to a page cap,
stopping at a short page — feed the 75%-precision source the whole
market instead of page one.

## Acceptance criteria

1. Foreign-country-located jobs (USA, Berlin, London-for-SE) never pass
   the gate for an SE user — even remote-flagged, even with
   `include_remote=1`. London stays in-country for GB users.
2. Global/unresolvable locations ("Remote job", "Anywhere", none) still
   pass for remote-opted users; strictly-local behaviour unchanged.
3. Pool-level: a stubbed scrape storing mixed-location jobs writes zero
   foreign-located rows for the SE context.
4. Jobtech fetches multiple pages per query (stub: 100+100+37 → 237),
   stops on a short page, respects the cap.
5. Tests written first, seen red; revert-checks against production code;
   full suite green on both CI legs.
