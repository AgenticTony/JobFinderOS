# WO-29 — Persona landing pages: message-to-person, grounded in real voice

> Priority: P2 · Depends on: WO-28 (shared landing components) ·
> Status: designed 2026-10-15, not started · Rationale owner: this file
> (method evidence: 2026-10-15 video analysis of one-to-one landing
> pages) + `docs/research/competitor-rea-teardown-2026-10-11.md` §6
> (funnel) + the Rusta och Matcha GTM thread (agency segments).

## Why

The method, from the source video: a landing page converts when the
message matches the person — so generate one page per persona, each
headline in the customer's own words, mined from real reviews, and point
each ad (or outreach link) at its matching page. The presenter shipped
20 variations of one product page in minutes and pairs each to a
targeted ad.

We can run the same machine, with two honest differences:

1. **Our personas must be mined, not invented.** He has hundreds of real
   product reviews to mine. We have three beta users — so the persona
   research step (Scope A) comes FIRST, and a persona that can't be
   grounded in real, attributable jobseeker language is killed, not
   shipped with plausible-sounding filler. Three grounded personas beat
   twenty invented ones — and our entire landing claim is that we don't
   invent.
2. **Our front doors are outreach and long-tail, not paid ads** (no paid
   acquisition — WO-26 non-goal). The immediate use: Rusta och Matcha
   coaches email a jobseeker segment and link that segment's page.
   Swedish long-tail search is the second door, shared with WO-26.

## Scope

### A. Voice-of-customer research doc (gate for everything else)

Produce `docs/research/persona-voice-2026-10.md`: per-candidate-persona
objection clusters, each with verbatim quotes and links. Sources to mine:

- Swedish jobseeker forums: r/arbetsloshet, Flashback job threads,
  Arbetsförmedlingen-adjacent communities
- Negative reviews of the category's apps (App Store / Google Play,
  Trustpilot) — the complaints are the persona objections
- Our own beta users' words (onboarding feedback, the external tester's
  stall notes)
- Competitor marketing angles as persona evidence (what they promise to
  whom)

**Kill rule: a persona ships only with ≥3 real, attributable quotes
behind its objections.** Fewer → it doesn't ship in v1.

### B. Three personas, Swedish-first

Expected shapes (confirm/reject against the research before building):

| Persona | Working name | Core objection cluster |
|---|---|---|
| Career changer | omställare | "Applying into the void, no callbacks, is it me or the CV?" |
| New grad | nyexaminerad | "No Swedish experience, CV filtered out, need the first foot in" |
| Recently laid off | uppsagd | "Urgent, CV outdated, don't know where the market moved" |

Swedish is the primary variant; English the translation (reverse of the
usual direction — market reality).

### C. One parameterized route, not page copies

`/p/[persona]` + `/sv/p/[persona]`, one `PersonaPage` component
composing **WO-28's shared sections** (demo, comparison, guard receipt)
plus persona-specific dict entries: headline, promise, the 3 objections
that persona actually has (in quoted real language), persona FAQ subset,
signup CTA. Decide and document slug language once (Swedish slugs for
both locales vs neutral) — stable after ship.

### D. Entry points

- Agency outreach: handoff URL pattern documented for Rusta och Matcha
  segments (e.g. `jobfinderos.com/sv/p/nyexaminerad` in the coach's
  email to that segment)
- Internal: linked from the main landing's footer/resources section —
  not the hero (landing owns the hero, WO-26 precedent)
- Signup CTA carries locale, same as main landing

## Hard constraints

1. **No invented personas** — kill rule above; the research doc with
   sources is a build prerequisite, not documentation-after.
2. **Persona pain-point claims trace to quotes; product claims stay true
   of the product today** — same claims-audit standard as WO-28.
3. **Swedish-first authoring**, EN the translation.
4. **i18n discipline:** dict-parity walker + `i18n-scan.test.ts` FILES
   cover the new route/component; language toggle above the fold on
   every persona page (house rule).
5. **Shared components, not copies** — persona pages compose WO-28's
   extracted sections; a divergent copy of a section is a bug.
6. **Static export; no backend per persona, no analytics-based
   persona detection** — pages are static and honest.

## Acceptance criteria

- [ ] `docs/research/persona-voice-2026-10.md` exists with per-persona
      quote clusters and sources
- [ ] 3 personas × 2 languages render from one route + one component;
      slug-language decision documented
- [ ] Every shipped persona traceable to ≥3 real quotes in the research
      doc; any persona that failed the kill rule is recorded as killed
- [ ] Toggle above the fold; parity + scanner green with the new files
      in FILES
- [ ] Persona pages compose WO-28 sections (zero duplicated section
      code)
- [ ] Footer/resources link from the main landing + agency handoff URL
      pattern documented
- [ ] CI-exact locally: tsc clean, node --test green, static build +
      artifact check

## Non-goals

- Scaling to ~20 personas — only after a conversion signal from the
  agency channel justifies generation at that width.
- Paid ads of any kind.
- Role-keyed informational SEO pages — **WO-26 owns those** (role-keyed,
  pool-data-backed). This WO owns persona-keyed conversion pages. Do not
  merge the two.
- Dynamic per-visitor persona detection or A/B machinery.
