# WO-28 — Landing redesign port: bilingual FAQ, demo, comparison, outcome copy

> Priority: P1 · Depends on: i18n ✅ (#120/#121 merged; **deploy still
> pending — this WO's deploy carries it**) · Status: designed 2026-10-15,
> not started · Rationale owner: `docs/research/competitor-rea-teardown-
> 2026-10-11.md` §6 (funnel is the gap) + the 2026-10-13 landing audit
> (social proof, outcome hero, FAQ, sticky CTA) + the 2026-10-14
> assessment of the owner's redesign bundle (below).

## Why

The teardown's headline finding: every category winner converts from the
top of the funnel, and ours is one page selling restraint instead of
outcome. The owner dropped a complete redesign — `JobFinderOS
Landing.html`, repo root, untracked local reference — that answers the
audit's top findings and adds an interactive demo and comparison table.

**Decision already made (2026-10-14): port the content into the
bilingual `LandingView`; do not ship the standalone bundle.** It is
English-only (would undo #120/#121), unwired into the static export and
dicts, and carries a light theme + three new fonts that would split the
brand. This WO owns that port.

Second reason it is first in the queue: it is frontend-only with no GDPR
dependency, and it produces the asset the Rusta och Matcha outreach
points at. WO-24 (CV-koll) — the bigger funnel piece — is blocked on the
AI-residency decision; this isn't.

## Source copy (verbatim from the redesign bundle)

Embedded here so the WO survives the reference file being cleaned up.

**Hero:** h1 `Fewer applications.` / accent span `The right ones.` ·
badge `Sweden + UK · hunts at 06:00 and 18:00` (live-dot style).

**Final CTA:** `Your next job is already posted.` / `Somewhere in
tonight's 18:00 hunt. Upload your CV and see your first matches tomorrow
morning.` — the "tomorrow morning" promise is conservative-true (hunts
run 06:00/18:00; a signup after 18:00 sees matches at 06:00, an earlier
signup sees them same evening).

**Demo section:** "This morning's hunt" — match cards with verdict +
CV-evidence chips; minimum-score slider labelled `Drag to see what
reaches you`; show-what-gets-sent cover-letter toggle. All marked
"illustrative example".

**Comparison table** (`sr-only` caption: "JobFinderOS compared with
typical auto-apply tools"; columns Feature / Typical auto-apply tool /
JobFinderOS):

| Feature | Typical auto-apply tool | JobFinderOS |
|---|---|---|
| What gets sent | Hundreds of applications a day | Only jobs you approve |
| Duplicate listings | Same job applied to via 3–9 agencies | Reposts merged into the original |
| Why a job matched | A score, or nothing | Skills you have, gaps, what carries over |
| Claims in your CV | Rewritten to hit keywords | Checked line by line against your real CV |
| Job sources | Often scraped behind logins | Official, public feeds only |

**FAQ (6 Q&A, verbatim answers in the bundle):**

1. Does it apply to jobs without asking me? — No. Nothing is sent until
   you approve it…
2. Where do the jobs come from? — Official, public sources only: Sweden's
   national job feed and one of the UK's largest job feeds. No logins, no
   grey-zone scraping. Agency reposts are merged into the original
   listing so you see each job once.
3. Will employers know I used AI? — Your drafts only contain claims your
   CV can back up, written for that specific listing…
4. What happens to my CV and data? — **⚠ see claims table: the bundle's
   "stored in the EU (Frankfurt)" is wrong — Supabase is eu-west-1
   (Dublin); Render region to verify. State the EU without a city, or
   state the true regions.**
5. What does it cost? — Nothing during the beta, and no card is needed.
   When paid plans arrive there are no credits, no auto-renewing trials,
   one-click cancellation, and a refund for unused months if you get the
   job. **⚠ forward promise — keep the "when paid plans arrive" framing
   or drop the refund clause (owner call).**
6. Does it work in Swedish and English? — **⚠ verify before shipping:
   "writes your CV and cover letter in the language of the listing" must
   be true of the tailor today, not aspirational.**

**Dropped by default:** the "Two thirds of hiring managers say
AI-optimised CVs make skills harder to verify. Source: Forbes, March
2026" stat — unverifiable. Ships only if the owner supplies a citation
that checks out (hard constraint #2).

## Scope

### A. Componentize the landing (prep for WO-29)

Pull the reusable sections out of `LandingView.tsx` into
`components/landing/` pieces (how-it-works, verdict, sources gallery,
guard receipt, terms) each fed from the dicts. WO-29's persona pages
compose these; this step is why the two WOs are sequenced.

### B. Hero + outcome copy

Port hero h1, badge, and the final-CTA block; CTA line gains the file
facts (`PDF or Word · up to 10 MB` — verify against the actual upload
constraint before shipping).

### C. Interactive demo

Port the demo as a client component: three sample match cards with
verdict + evidence chips, the score slider, the send-toggle. Static,
hard-coded samples, labelled illustrative. No backend, no real user data.

### D. Comparison table + FAQ

Port the table (generic "typical auto-apply tool" column — never a named
competitor) and the 6-question FAQ, bilingual.

### E. Social proof slot

The honest placeholder pattern from the bundle ("Ask for one result in
their words… Name · role · city · with permission"). A real quote drops
in when the owner supplies one with permission; nothing fabricated.

### F. Sticky mobile CTA

Audit finding #4: long page, CTA only in header and footer. Sticky
bottom bar on small viewports; must not cover footer content or the
on-screen keyboard.

### G. Swedish for everything new

All new copy lands in both dicts in the same commit. Swedish is
authored, not machine-afterthought; flag for a native pass by the owner
if any line reads stiff.

## Hard constraints

1. **Bilingual in the same commit** — no English-only state ever lands
   (the bundle's failure mode; house rule from #120/#121).
2. **Claims audit before merge.** Every new factual claim maps to
   verified reality — see the ⚠ flags above (Frankfurt→Dublin, the
   listing-language claim, the refund framing, upload limits) and the
   infra claims checked against actual regions. The Forbes stat is
   dropped unless the owner supplies a verifiable citation.
3. **Demo data is labelled illustrative** and never presented as a real
   user's hunt; no fabricated social proof anywhere.
4. **The comparison table attacks the category pattern, not a named
   competitor** — defensibility over punch.
5. **Static export (F6):** client components only; no `cookies()`, no
   server actions.
6. **Port, don't ship the bundle.** Light theme and the new fonts are a
   separate owner decision — out of scope here.
7. **Test discipline:** dict-parity walker covers the new keys;
   `i18n-scan.test.ts` FILES updated for any new file with user-visible
   text; red-first where a test can fail.

## Acceptance criteria

- [ ] `/` and `/sv` render all ported sections; parity + scanner green;
      no English-only copy anywhere
- [ ] Claims table worked through: each ⚠ resolved by verify/reword/drop,
      recorded in the PR description
- [ ] Region claim matches actual infrastructure (no "Frankfurt")
- [ ] Demo slider + send-toggle function client-side, labelled
      illustrative
- [ ] Sticky mobile CTA visible on small viewports, footer and keyboard
      not obstructed
- [ ] CI-exact locally: `tsc --noEmit` clean, `node --test` green,
      static build + artifact check
- [ ] **Deployed** via `ops/deploy_frontend.sh` — carries the pending
      #120/#121 i18n deploy
- [ ] Landing sections extracted as shared components, ready for WO-29
      to compose

## Non-goals

- Light-theme rebrand and the bundle's fonts — separate owner decision.
- llms.txt / OKF / per-role SEO pages (WO-26 owns).
- Anonymous CV-koll (WO-24 owns the free tool).
- Persona pages (WO-29 owns; depends on this WO's components).
- Live counters or real testimonials — no real data to show yet.
