# WO-30 — Landing copy & motion overhaul: research-first rewrite

> Priority: P1 · Depends on: WO-28 ✅ (deployed; this WO polishes it) ·
> Status: **in progress 2026-10-15** — research gate PASSED (REA copy
> teardown executed, `docs/research/landing-copy-teardown-2026-10-15.md`),
> hero + full SV de-jakt DEPLOYED (PR #126); remaining: section-copy
> pass, motion pass, final claims sweep · Rationale owner: this file
> (owner feedback 2026-10-15, post-deploy review) +
> `docs/research/competitor-rea-teardown-2026-10-11.md` §6 (funnel) +
> the 2026-10-13 landing audit finding 2 (hero sells restraint, not
> outcome).

## Decision record — hero (the WO's gate, 2026-10-15)

Owner picked from the teardown's four transcreated candidates:

> **EN:** "Job hunting is stressful enough. / Get your evenings back."
> **SV:** "Jobbsökandet är stressigt nog. / Få tillbaka dina kvällar."

Owner's test (recorded verbatim in spirit): a first-time visitor must
feel the promise AND understand what the product is — feeling-led beat
the cleverness-led options. The "void" direction ("Stop sending
applications into the void") was explicitly rejected: doesn't
transcreate into Swedish. The SV de-jakt acceptance criterion is MET
(zero jakt/jagar/jagade in the built /sv page — PR #126). Persona
heroes (WO-29, now scoped at up to five personas) follow the owner's
own pattern: *"Being unemployed is stressful enough. Job hunting
shouldn't have to be."* — pain acknowledged in their words, relief
promised.

## Why

The owner reviewed the deployed WO-28 landing and the verdict is
honest and specific: the hero "isn't captivating", "Fewer applications.
The right ones." "isn't really hitting home… kind of a boring title",
and "the copy is pretty bad throughout the landing page". Five
concrete threads from that review:

1. **The hero is weak.** It states a limit, not an outcome. Nothing in
   the fold answers *what you get* or *when* — the same gap the
   2026-10-13 audit flagged before the port. Four replacement
   directions were drafted 2026-10-15 (wake-up/matches, total
   coverage/personal filter, stop-the-spray, sharpened current) —
   selection waits on the copy research below.
2. **The hunt metaphor does not carry into Swedish.** "Jakt / jagar /
   jagade" reads awkward through the SV page. The Swedish copy needs
   natural verbs — *bevakar, söker, hittar, poängsätter* — even if the
   EN side keeps the hunt voice. Product identifiers (hunt pulse,
   NextHunt) are internal names and stay.
3. **Feature honesty on the landing is now a rule, not a preference.**
   The demo score slider shipped and had to be pulled (PR #125): the
   console has no live feed-filtering by drag. The landing shows only
   what the product does today — or what a merged WO explicitly plans,
   labelled as such.
4. **Real images only.** Two genuine screenshots exist
   (`/screenshots/hunt-pulse.png`, `/screenshots/match-detail.png`);
   the landing sticks to those. If a section needs a visual we don't
   have, the options are: take a new real screenshot, build it from the
   real component's markup (as the verdict cards now do), or cut the
   section — never fabricate product UI.
5. **The page reads static.** Reveal-on-scroll exists but is too
   subtle to register. A motion pass should make the page feel alive
   without violating the house style (critically damped, dies under
   `prefers-reduced-motion`).

## Scope

### A. Copy research first (the gate)

REA teardown of the category's landing **copy** — AIApply, JobRight,
Teal, Careerflow, Simplify, plus whatever the owner's own research
surfaces (owner is researching effective copy in parallel; merge both).
Extract per competitor: hero formula (promise shape, quantification,
time-to-value), sub-structure, CTA microcopy, objection handling,
social-proof framing. Output: `docs/research/landing-copy-teardown-<date>.md`
with a patterns table and a shortlist of formulas that fit an
honesty-constrained product (we cannot claim "3× more interviews" with
three beta users — the winning formulas must work with defensible
claims: time-to-value, coverage, evidence-per-match, approval control).

### B. Hero rewrite (from the research)

Apply the selected direction (or a new one the research surfaces) to
`hero.*` in both dicts. The four drafted candidates live in the
2026-10-15 session record for reference. Ship A/B-able variants as dict
pairs only if the owner wants to test; otherwise one hero, decisively.

### C. Full-page copy pass, both languages

Every section re-read against the research: badge, sub, stats labels,
steps, verdict, demo, comparison cells, sources cards, guard, proof
placeholder, terms, FAQ answers, final CTA, footer, login marketing
strings. Swedish pass rewrites hunt-metaphor copy into natural
Swedish per thread 2 — this is a re-translation, not a touch-up.

### D. Motion pass

Escalate `Reveal` (or add a section-transition variant): larger travel,
staggered children, scroll-linked progress on the Act II panel edge,
count-up on the stats band — all disabled under reduced-motion, all
GPU-friendly transforms/opacity only (static export, no JS bloat).

### E. Honesty sweep

Grep-level audit of every landing claim against the product (the WO-28
claims-table method, repeated after the new copy lands). No new
interaction demos; real screenshots/components only.

## Hard constraints

1. **Research gates the rewrite** — no hero or section copy ships
   before the teardown doc exists and the owner has picked a direction.
2. **Every claim defensible** (the standing rule): no invented
   percentages, no fake freshness, no unverifiable citations. The
   Forbes slot stays null until a verified citation exists.
3. **No feature-fabrication**: landing shows only shipped behaviour
   (thread 3). A planned feature may appear only if its WO is open and
   the copy says it is coming.
4. **Bilingual in the same commit**; Swedish authored natively, not
   metaphor-literal (thread 2).
5. **Static export + perf budget**: motion via CSS transforms/opacity;
   no scroll-listener thrash; `prefers-reduced-motion` respected.
6. **Scanner + parity tests cover every new string** (FILES already
   includes all landing components).

## Acceptance criteria

- [ ] `docs/research/landing-copy-teardown-<date>.md` exists with the
      per-competitor copy table + formula shortlist, and the owner has
      chosen the hero direction (record the choice in this file)
- [ ] New hero live in EN + SV; owner signs off on the H1 before deploy
- [ ] Zero occurrences of jakt/jagar/jagade in user-facing SV landing
      copy (grep in the built `out/sv.html`); natural Swedish verified
      by the owner or a native reader
- [ ] Motion pass visible on scroll (stats count-up, staggered
      reveals); verified dead under `prefers-reduced-motion`
- [ ] Claims sweep re-run on the final copy; every statement maps to
      product reality or is cut
- [ ] CI-exact: tsc clean, node --test green, build + artifact checks
- [ ] Deployed and verified on jobfinderos.com + /sv

## Non-goals

- Light theme / new fonts (the redesign bundle's brand split) — still a
  separate owner decision, untouched here.
- New screenshots of the console (if wanted, that's a quick follow-up:
  capture from the real console into `/screenshots/`).
- Persona pages (WO-29) — they inherit whatever copy this WO lands.
- Paid acquisition, testimonials, or any fabricated social proof.
