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
