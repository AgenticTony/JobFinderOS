# WO-26 — AI-search + content surface: llms.txt, OKF, per-role pages from the live pool

> Priority: P2 · Depends on: i18n (#120/#121 ✅ merged; deploy pending) ·
> Status: designed 2026-10-15, not started
> Rationale owner: `docs/research/competitor-rea-teardown-2026-10-11.md`
> §3, §6.2–6.3, §9.2.
> Numbering note: "WO-26" was floated 2026-09-14 for an over-eager-guard
> follow-up; that thread was absorbed by WO-23 (executed). Number reused.

## Why

Both leaders optimize for AI assistants, not just Google — and the
"AI job tool for Sweden/Europe" answer-slot is nearly uncontested:

| Competitor | Their machine (teardown evidence) |
|---|---|
| AIApply | `llms.txt` **plus an OKF (Open Knowledge Format) bundle** (`/okf/index.md`) — a knowledge graph declaring products/concepts for ChatGPT/Claude/Perplexity; 14 locales including Swedish; thousands of per-role programmatic pages (resume-examples, salaries, skills) |
| JobRight | `llms.txt`; every crawled job posting is a public SEO page; `/remote-jobs` curated board ("verified, updated hourly") |

We have none of this — `jobfinderos.com/llms.txt` is a 404 (teardown E5).

Our angle is honesty: their example pages are templates and generic
salary lists; ours can be **generated from the live shared pool** — real
Swedish/UK titles, real requirement frequencies, with provenance. Content
only we can generate truthfully (and the landing-audit rule holds: claims
must stay defensible).

## Scope

1. **`/llms.txt`** (served at the domain root): honest SE+EN product
   inventory — what JobFinderOS does and, as importantly, what it
   refuses to do (no auto-apply without approval, no invented claims).
   The refusals are the differentiator; state them.
2. **OKF bundle** (`/okf/index.md` + concept docs): concepts for
   honest matching, grounded tailoring, two-gate approval, outcome
   tracking. Follows the OKF spec AIApply also uses — being legible to
   AI agents is the point, not the format.
3. **Per-role pages**, seeded from the live pool: for the top N titles
   per country (Platsbanken/Reed), a page showing real requirement
   frequencies ("what <role> ads in Sweden ask for, from N live
   postings"), refreshed on a schedule, each with a provenance line and
   timestamp. Swedish + English.
4. **Sitemap + internal linking** from the landing page.

## Hard constraints

1. **Every number is backed by a query over the pool at build time.**
   No invented stats, no borrowed benchmarks (the landing redesign
   already caught one unverifiable Forbes stat — do not repeat it).
2. **Pages degrade to removal, not stale lies.** If a title's pool data
   thins below the threshold, the page delists until data returns.
3. **No fabricated social proof** — no invented user counts, no
   testimonials. Real numbers from real tables or nothing.
4. Generated pages must not imply we applied to anything (pool jobs are
   public facts; our users' activity is not).

## Acceptance criteria

- [ ] `/llms.txt` returns 200 with the honest-inventory content
- [ ] OKF index parses against the spec; concept docs cross-link
- [ ] ≥20 per-role pages live, each showing provenance (N postings,
      pool query date) in SE and EN
- [ ] Claims audit passes: every published stat reproducible by running
      the stated query
- [ ] Delisting path verified by starving one title's data in staging
- [ ] Sitemap submitted; pages linked from the landing page footer or
      resources section (not the hero — landing owns the hero)

## Non-goals

- No blog, no AIApply-style 30-tool farm (WO-24 owns the single free
  tool), no locale expansion beyond SE/EN.
- No paid acquisition.
