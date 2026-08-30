# Fantastic.jobs — future source candidate (noted 2026-08-30)

Revisit when real-user volume arrives. Not before: our free official
sources cover beta, and this is a paid credit-based feed.

## Why it's genuinely interesting

1. **SE tech/startup gap.** Platsbanken carries most Swedish
   employer-direct ads, BUT tech companies and startups frequently
   post ONLY on their own career pages + LinkedIn/Wellfound.
   Fantastic's ATS feed (200K companies, 55 ATS platforms) covers
   exactly that missing segment — the segment our earliest users
   (junior devs, Malmö/Lund) most care about.
2. **UK employer-direct gap.** We have Reed + Careerjet; the UK has
   no Platsbanken equivalent, so employer-direct coverage is our
   thinnest area. Their UK volume: ATS 120-150K/mo, combined
   (deduped) 560-680K/mo. Sweden combined: 55-65K/mo.

## Architecture fit — it is literally our shape

Their own guidance ("recurring requests, results stored in your own
database, only new jobs returned") is the delta-hunt design we
already run: watermarks == their since-parameters, pool ==
job_postings, per-user scope applied at store gates. Integration is
one scraper class in SCRAPER_REGISTRY + source-pack membership +
env keys. Their AI enrichments (salary, experience, summaries,
company data) complement — not replace — GLM matching; their docs
explicitly disclaim semantic matching, which is our layer.

## Cautions before adopting

- **Brand-promise tension:** our landing says "Official job-market
  data · No logins, no grey scraping." Their Job Board feed indexes
  LinkedIn/Wellfound/YC — third-party LinkedIn data provenance may
  conflict with that claim. Mitigation: adopt the ATS feed only
  (company career pages = public data), or vet their ToS posture and
  amend the marketing line consciously.
- **Pricing unknown.** Size before buying: the free trial's count
  endpoints (/active-ats-count, /active-jb-count with
  time_frame=1m) accept our actual filters (title, location) — get
  the exact scoped volume for Malmö/Lund dev queries and UK regions
  and price the plan against the margin model.
- **Additive, never replacement.** Official sources (jobtech, reed)
  stay the base layer; Fantastic augments. Dedupe already handles
  cross-source overlap (3-layer + fuzzy Pågen rule), and their
  exclude_ats_duplicate covers their own JB/ATS overlap.

## Trigger conditions

- UK user growth (our weakest direct-employer coverage), or
- users reporting jobs we miss that live on LinkedIn/career pages, or
- scale where a subscription feed beats per-source scraping effort.

## Pricing (added 2026-08-30, from their plans page)

Jobs-credit model: Starter-20k $95/mo (20K jobs, 10K requests),
Pro-50k $175, Pro-100k $250 (overage $0.0025/job), 600 req/min.
Modified-jobs endpoint needs Pro; org enrichments need Pro-100k.

**Unit economics vs our model:** ~$0.0025-0.005 per sourced job
(plan-amortized) lands right next to our per-job AI evaluation cost
(~$0.0034-0.007 incl. sampling). Adopting Fantastic roughly doubles
per-job pipeline cost vs free official sources — fine for the margin
model (~$1-2.50/user/month at 100 users on Starter/Pro-50k), but it
must BUY real coverage, not duplicate Platsbanken.

**Credit traps to design around:**
- Credits are per job RETURNED. Two overlapping filter requests pay
  twice for the same job. Ingest per UNION scope (title+location of
  all users' professions/regions), never per user.
- Full-country feeds are the wrong shape: Sweden combined 55-65K/mo
  fits Pro-100k ($250) but UK combined 560-680K/mo is enterprise
  territory — scoped filters are mandatory for UK.
- Their quota headers (x-api-jobs-*) give real-time budgeting; wire
  into hunt logging on integration.

**Product upgrade hiding in here:** the expired-jobs endpoints give
REAL closure data — our current 30-day age heuristic retires ads that
may still be open and keeps some that closed. Matching against
genuinely-open jobs is a honesty feature, not plumbing. (Caveat:
expired-jb only re-checks LinkedIn; Wellfound/YC listings need our
own freshness sweep — keep MAX_POSTING_AGE for those.)

**Mechanics map 1:1 onto the delta-hunt design:** their 24h
same-hour polling == our hunt cadence; date_created_gte recovery ==
watermark+overlap (their outage recipe is literally our
delta_since/backfill logic); 7d/6m backfills == backfill mode;
limit+offset pagination like jobtech. Integration risk is low.

Revised trigger: worth it from ~50-100 paying users, or earlier if
UK launches (our thinnest direct coverage) — scoped filters sized
first via the free-trial count endpoints.

## Build-vs-buy: direct ATS connections (added 2026-08-30)

The underlying ATS job-board APIs are mostly PUBLIC and KEYLESS
(Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee,
Teamtailor — per-company endpoints). Fantastic's fee buys the
200K-company who-uses-which-ATS directory, hourly-scale polling ops,
expiry/modified tracking, enterprise-ATS grinding (Taleo,
SuccessFactors, Workday), and LinkedIn data — not API access itself.

**Hybrid strategy recorded:**
- Phase 1 (free, small, targeted): direct adapters for the top 5-6
  modern ATS platforms + a curated company list for our launch
  regions (Skåne/Øresund tech, UK hubs). One adapter per platform,
  plus a generic schema.org JobPosting sitemap crawler (the
  Google-for-Jobs route) for any structured career page. Zero
  recurring cost; exactly our users' missing segment; consistent
  with the 'official public data' promise (public, keyless
  endpoints).
- Phase 2 (Fantastic): breadth — UK-wide, the enterprise long tail,
  LinkedIn jobs (no public API exists), and 58-country expansion.
  The fee is for the directory + ops we don't want to own.

ATS board endpoints are keyed by company token — the curation
burden of Phase 1 is maintaining OUR company list, which is small
and local. Revisit Phase 1 when the SE-tech coverage gap becomes
user-visible; it needs no subscription and no third-party spend.
