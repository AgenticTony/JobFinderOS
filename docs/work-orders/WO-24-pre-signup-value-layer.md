# WO-24 — Pre-signup value layer: anonymous CV-koll + LinkedIn import

> Priority: P1 · Depends on: the AI-residency decision (see Hard
> constraints #1) · Status: designed 2026-10-15, not started
> Rationale owner: `docs/research/competitor-rea-teardown-2026-10-11.md`
> §6.1, §6.4, §9.2 — this WO owns the HOW, not the why.

## Why

Every category winner converts from free, pre-signup utility — nobody pays
acquisition cost for job seekers:

| Competitor | Their pre-signup surface (teardown evidence) |
|---|---|
| AIApply | 30+ free **no-signup** tools; `resume-builder/from-linkedin` — LinkedIn URL → instant resume, value delivered *before* an account exists |
| JobRight | signup path is `/onboarding-v3/signup` — the funnel itself is a versioned product, on at least its third iteration |
| Teal / Huntr / Simplify | free tracker / extension is the entire acquisition engine; paid tiers convert from it |

We have one landing page. And our only external beta tester (2026-09)
stalled at the onboarding cliff — WO-23 fixed recovery *inside* the app;
this WO attacks the front of the funnel.

The wedge: a **grounded** CV scan is the one free tool none of them can
honestly offer. Their tailoring invents (the "fake resumes" fear ranks as
content in this category); our matcher output is guard-checked against
the CV. "See what a Swedish/UK employer actually sees — nothing invented"
is a free tool that is simultaneously the product demo and the trust claim.

## Scope

### A. Anonymous CV-koll (ships first)

Public page (SE + EN, above-the-fold language toggle per house rule),
**no account required**:

1. Input: CV file upload or paste; optionally a pasted job ad.
2. Processing: one matcher-style call over CV (+ optional ad), read-only,
   same rubric discipline as production scoring (temperature 0).
3. Output: a grounded report — what the CV supports today, what the ad
   asks that the CV does not show (gaps stated as *absence*, never
   invented), and a match score when an ad was provided.
4. Conversion moment: "Want this hunting for you every morning?" →
   signup, carrying the parsed CV forward so onboarding never re-asks.

### B. LinkedIn import at signup

- **v1 (safe, ship first):** user supplies their own exported LinkedIn
  data (Settings → Get a copy of your data) or pastes their profile;
  parse → prefill the CV. No fetching by us.
- **v2 (gated):** URL → fetch profile. **Blocked on a documented ToS/legal
  review** — AIApply does it, but that is their risk appetite, not ours.
  Do not ship v2 without that gate passing (same pattern as WO-15's ToS
  gate).

## Hard constraints

1. **The GDPR trigger fires before this ships.** An anonymous CV is a
   third party's CV entering the system — ROADMAP's stated trigger
   ("before the first CV that is not the founder's"). Prerequisite:
   either the EU-resident model path (Mistral EU endpoint — blocked on
   the `tier_not_allowed` unlock, probe 2026-08-27) or SCC + TIA +
   sub-processor disclosure accepted by the owner. This is a real
   dependency, not paperwork.
2. **Process-and-forget.** No row, no object, no vector store after the
   response is returned. Anonymous ≠ logged-for-later.
3. **Zero-fabrication in the report itself.** The report states only what
   the CV + ad contain. Gaps are "not shown in your CV", never generated
   filler. The tool must survive the same honesty standard as the tailor.
4. **Unauthenticated spend is capped.** Per-IP rate limit + a global
   daily budget + a kill switch. Free tools are acquisition spend; treat
   them with the same cost discipline as the hunt.
5. **No account wall, no email capture to see results.** Results first,
   signup CTA after — the entire point is value before commitment.

## Acceptance criteria

- [ ] Anonymous user gets a grounded report with no account; post-response
      persistence verified empty (DB + storage + logs scrubbed of CV text)
- [ ] No claim in any report absent from the user's inputs (spot-audit +
      the honesty rule wired into the prompt, tested)
- [ ] SE + EN UI, language toggle above the fold
- [ ] Rate limit + daily cost cap + kill switch enforced server-side,
      tested by simulating abuse
- [ ] Signup from the tool carries the parsed CV forward (no re-upload)
- [ ] LinkedIn v1 import prefills every field manual onboarding asks,
      from the user's own export file
- [ ] v2 explicitly marked blocked in this file until the ToS review is
      written down and passes

## Non-goals

- No auto-apply, no sends, no document generation to send — the report is
  read-only advice.
- No free-tool farm (30 AIApply-style tools). One tool, done properly,
  that demos the actual product. Revisit only if analytics justify more.
