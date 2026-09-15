# WO-02 — Tailoring quality: the production judge

> Priority: raised to **P1** (was P2) · Depends on: WO-01 (done) ·
> Status: **executed 2026-08-28** — the judge runs in production on
> every draft; the honest baseline is measured and recorded below.

## What the WO-01 arc established

- The opt-in judge was the ONLY mechanism with demonstrated catches:
  every real fabrication was semantic (invented work authorization,
  duties, practices, tool familiarity) — sentences, not tokens. The
  deterministic technology vocabulary: 87 entries, zero real catches.
- The original 4/5 → 80% fabrication rates were partly OURS: the lossy
  profile context ("Junior…" + "Years of experience: 20") invited
  competence inflation. Fixed in WO-01 r5 (lossy line removed; guard
  trusts only user-entered context).

## The honest baseline (2026-08-28, post-fixes)

Re-measured through the corrected checker AND corrected context, 5 real
approved jobs, owner's CV:

```
fabrication rate (docs with any finding): 40% (2/5)
layer-A high-confidence FALSE positives:  0      <- the first clean number
```

Down from 80% — the reviewer's prediction held: a large share of the
measured rate was our input defect surfacing through the model. The
residual 40% is the prompt-side remainder (snapshots under
tests/fixtures/fabrication/live_catch_*.json).

## The control (this WO's deliverable)

**`AIService.judge_fabrication` runs in production on every draft**,
inside the same regenerate-then-block loop as Layer A:

- Runs AFTER Layer A is clean (no point judging a document Layer A
  already rejected).
- A FRESH call — never the tailoring conversation (grading your own
  output measures agreeableness, not fidelity).
- A judge finding joins the high-confidence path: regenerate with the
  claim named in the correction, block after MAX_FABRICATION_RETRIES
  with the claim named in the error.
- Kill switch: `FABRICATION_JUDGE=off` (emergency cost lever; Layer A
  still guards). Test suite defaults to off — draft tests script Layer
  A and spend nothing; TestProductionJudge opts in per-test.
- Cost: +1 call per draft attempt (~$0.004); worst case 3 tailor + 3
  judge per blocked draft ≈ $0.03 — bounded by the existing retry cap.
- Latency: +~6s per attempt on a request that already runs 5–20s in a
  600s-timeout threadpool.

## Acceptance criteria — all verified

- [x] Judge runs on every draft in production (regenerate-then-block,
      5 red-first tests: clean-ready, finding-regenerates-then-blocks
      and names the claim, finding-recovers, kill-switch spends nothing)
- [x] Revert-check: judge call disabled → judge tests red
- [x] Honest baseline measured and recorded (40%, Layer-A FP 0)
- [x] Existing draft flows unaffected (suite green with judge off by
      default; 127 passed + 2 skipped)
- [x] Cost lever documented and tested

## The N=20 attempt and the pool ceiling (2026-08-28)

FABRICATION_N=20 was attempted; the query returned **5** — that is the
entire eligible pool (approved + description). The 40% (2/5) figure is
therefore both the baseline AND the ceiling of what the current pool
can measure. Before any prompt-side tuning: approve a broader set of
matches (or widen the harness's sampling to high-scoring pending
matches, clearly labelled as pre-approval) to get N≥20.

This round's judge catches (snapshots kept as fixtures) — the residual
fabrication classes on real output:
- live_catch_580: invented project feature ("TalentHiv included
  authentication"), invented frontend competence ("responsive web
  development"), invented AI tooling ("AI-based development tools")
- live_catch_583: fullstack self-description upgrades, specific stack
  claims (React/TypeScript, C#/.NET with Web API) not in the CV

Layer A: 0 high-confidence findings, 0 false positives, 1 advisory —
the deterministic layer is now clean on real output; every real catch
is the judge's.

## What is deliberately NOT in this WO

Prompt-side tuning of the tailor. The residual 40% needs a larger
sample than 5 jobs before prompt surgery — the re-measurement protocol
(RUN_FABRICATION=1) is now cheap and repeatable, and every catch
auto-saves a regression fixture. The next rate check should run
FABRICATION_N=20 before any prompt change, and after any, so the
effect is measured, not felt.

## Addendum — 2026-09-15: t1→t2 craft adoption, measured before/after

The first deliberate prompt change since this WO, per its own
discipline (same-day before/after, same pool, same model; N=5 — the
pool ceiling this WO already recorded):

- **t1 change**: four craft rules adopted into
  `_build_tailoring_prompt()` from the `cv-writing.skill` tier-1
  review — terminology mirroring, voice preservation (no polishing
  the CV's own phrasing; banned register: hedged intensifiers,
  spearheaded/orchestrated-class verbs, rule-of-three lists),
  cover-letter structure (why THIS employer + practicalities line),
  Swedish register (understated; no American superlatives).
- **t1 review findings (same day, code review of 898b3f5)**: three of
  the rules pushed the model toward text the fabrication guard cannot
  trace — the guard's truth is CV + profile and it NEVER sees the job
  posting:
  1. "why THIS employer (something only true of them)" induced quoted
     posting details; the 342 letter's "Operations Engineering Team"
     became a HIGH-tier Layer-A org finding — a FALSE POSITIVE the
     rule created (t1's layer-A 0→1), which in production would
     regenerate and could block a truthful draft.
  2. Mirroring the posting's terms produces vocabulary untraceable to
     the CV; the judge flags it and the correction message then
     contradicts the system prompt. It also contradicted the
     voice-preservation rule directly.
  3. "Only when the profile states them" is always true —
     build_profile_context always renders remote/location, and
     preferred_locations is GUARD TRUTH, so "open to relocating to
     Berlin" passes invisibly in production (the harness caught it
     only because its context is minimal). Availability/notice are
     never carried anywhere — pure invention license.
  Feeding the posting to the guards was rejected: it would bless
  "you require Kubernetes; I have deep Kubernetes experience".
- **t2 hardening**: all three rules narrowed to CV-traceable forms —
  posting-aligned emphasis keeps the CV's OWN term (no synonym
  swaps); employer specificity is paraphrased, never the posting's
  proper nouns or figures; practicalities are CV-carried only and
  availability/notice/relocation statements are forbidden outright.
- **Versioning**: the tailor prompt is pinned —
  `AIService.tailoring_prompt_version()` = `t2-95af0d8b` (t1 was
  `t1-c3417954`), hash of prompt + input composition (mirrors the
  match-prompt scheme; `TestTailorPromptCraft` fails on any silent
  edit). The harness snapshots record the version from t1 onward.
- **Measured** (5 approved jobs, owner's CV, glm-5.1):

  | | pre-t1 | t1 | t2 |
  |---|---|---|---|
  | docs with findings | **5/5** | **4/5** | **4/5** |
  | judge findings | 14 | 11 | 8 |
  | layer-A high | 0 | 1 (rule-induced FP) | **0** |

  Job 583 (IT konsult, Swedish) is clean under both t1 and t2 — its
  pre-t1 catches were the dropped "Junior", "jobbar dagligen med
  C#/.NET", and a consulting-experience implication. The t1→t2 deltas
  verify each fix at its own site: the Berlin relocation claim
  (present pre-t1 AND t1) is gone in t2; the 342 team-name FP is
  gone (layer-A high back to 0); 316 dropped 3→1 judge findings, 580
  4→2.

- **Honest read**: direction consistent across the arc, magnitude
  within noise at N=5 with unquantified judge-strictness variance,
  and t1's doc-level number was partially FP-inflated (342 counted
  on both a real judge finding and the FP). NOT a claim the rate is
  fixed; three same-day measurements on one pool are the recorded
  baseline the next change measures against.
- **Residual classes** (t2 snapshots — all pre-existing, none
  rule-induced): frequency/intensity inflation ("use daily", "write
  TypeScript daily", "the last year building exactly that"), skill-list
  embellishment ("responsiv webbutveckling", "AI-baserade
  utvecklingsverktyg" added to SKILLS the CV doesn't list),
  project-property embellishment ("structured outputs", "production
  frontends"). Candidate t3 rule: frequency and intensity claims
  must quote the CV's own wording — the same shape as the
  language-proficiency rule.
- **On-disk fixtures** (append-only since the round-2 review — the
  2026-09-15 runs initially OVERWROTE the 2026-08-28 fixtures by job
  id, and the fixture test stayed green because it checks filenames,
  not content; 580's 'LLM AI-baserade' Layer-A catch survived only in
  git): live_catch_{580,583}.json are the restored 2026-08-28
  fixtures; live_catch_583_pre-t1.json holds that job's pre-t1
  catches (clean under t1 and t2); live_catch_{300,316,342,580}_
  t2-95af0d8b.json are the t2 catches. The harness now names
  snapshots live_catch_\<id\>_\<prompt-version\>.json and refuses to
  overwrite an existing file; TestLiveCatchFixturesLoadBearing pins
  every recorded catch by name.
