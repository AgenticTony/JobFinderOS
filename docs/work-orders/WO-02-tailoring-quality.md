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
