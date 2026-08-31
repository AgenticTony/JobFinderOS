"""One-off diagnostic (2026-08-31): does the [:5000] job-text truncation
change verdicts for the jobs it actually truncated?

Population: match_results permanently dismissed below the dead-band
(below_threshold, score < 13) whose COMPOSED job text (what the model
actually sees — matcher_service._job_text) exceeded 5,000 characters,
i.e. they were scored on partial text and then irreversibly dismissed.

Exactly ONE variable changes vs production: the job-description cap
(5,000 -> 12,000 characters). The CV cap, system prompt, model,
temperature, and the SAMPLING POLICY are identical to production —
needs_another_sample / resolve_samples are IMPORTED from
matcher_service, never copied (the F1 lesson: a shadow copy of the
protocol once dismissed 62 rows on a single sample).

READ-ONLY on match data: this probe never writes match_results. The
only DB writes are the ai_usage observability rows _complete already
emits, labelled kind='truncation_probe' so the spend is attributable.

Usage (from backend/):
  .venv/bin/python scripts/probe_truncation_rescore.py --list       # no AI spend
  .venv/bin/python scripts/probe_truncation_rescore.py --run        # ~$0.2-0.4
  .venv/bin/python scripts/probe_truncation_rescore.py --run --n 40 # more of the population
"""

import argparse
import inspect
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import JobPosting, MatchResult, Profile  # noqa: E402
from app.services.ai_service import (  # noqa: E402
    CV_GUARD_CHARS,
    AIService,
    get_ai_service,
)
from app.services.cv_service import build_profile_context  # noqa: E402
from app.services.matcher_service import (  # noqa: E402
    _job_text,
    needs_another_sample,
    resolve_samples,
)

JD_CAP = 12_000
CV_SLICE = CV_GUARD_CHARS  # mirrors production (2026-08-31: caps removed) — the CV is not the variable under test
MAX_CALLS = 120  # spend guard: abort the probe past this many GLM calls

# Mirrors AIService.match_job's user message with ONE change: the
# job-description slice is [:JD_CAP]. The drift guard below fails loudly
# if match_job's template changes shape so this probe can never silently
# diverge from the production prompt.
USER_TEMPLATE = """
## My Profile & Preferences
{profile_context}

## My CV (evidence)
{cv_text}

## Job Posting
{job_description}

Evaluate this job for me and respond with ONLY valid JSON in the required format.
"""


def drift_guard() -> None:
    """The probe is only valid while it mirrors match_job exactly."""
    src = inspect.getsource(AIService.match_job)
    for marker in (
        "## My Profile & Preferences",
        "## My CV (evidence)",
        "## Job Posting",
        "{cv_text[:CV_GUARD_CHARS]}",
        "{job_description[:5000]}",
    ):
        if marker not in src:
            raise SystemExit(
                f"match_job's prompt template has drifted ({marker!r} missing) — "
                "update USER_TEMPLATE in this probe before trusting its results."
            )


def probe_population(db):
    """Dismissed-below-deadband rows whose composed job text was truncated."""
    rows = (
        db.query(MatchResult, JobPosting)
        .join(JobPosting, MatchResult.job_id == JobPosting.id)
        .filter(
            MatchResult.dismissed_reason == "below_threshold",
            MatchResult.score < settings.MATCH_DEADBAND_MIN_SCORE,
        )
        .all()
    )
    pop = []
    for mr, job in rows:
        text = _job_text(job)
        if len(text) > 5_000:
            pop.append(
                {
                    "job_id": str(job.id),
                    "title": job.title,
                    "company": job.company,
                    "source": job.source,
                    "composed_len": len(text),
                    "orig_score": mr.score,
                    "job_text": text,
                }
            )
    pop.sort(key=lambda j: -j["composed_len"])
    return pop


def evaluate_once(service, profile_context, cv_text, job_text) -> dict:
    """One sample, exactly as match_job would produce it — JD cap aside."""
    user_message = USER_TEMPLATE.format(
        profile_context=profile_context,
        cv_text=cv_text[:CV_SLICE],
        job_description=job_text[:JD_CAP],
    )
    raw = service._complete(
        service._build_matching_prompt(), user_message, temperature=0.0,
        kind="truncation_probe",
    )
    parsed = service._parse_json(raw)
    if not parsed:
        raise ValueError("Unparseable JSON from model (truncated/malformed response)")
    score = parsed.get("score")
    if (isinstance(score, bool) or not isinstance(score, (int, float))
            or not math.isfinite(score)):
        raise ValueError(f"Malformed score: {score!r}")
    return {
        "score": service._clamp_score(score),
        "tier": service._tier_for_score(score, parsed.get("tier")),
        "reasoning": parsed.get("reasoning", ""),
        "matched_skills": parsed.get("matched_skills", []),
        "missing_skills": parsed.get("missing_skills", []),
        "transferable_skills": parsed.get("transferable_skills", []),
        "recommendation": parsed.get("recommendation", "maybe"),
        "confidence": parsed.get("confidence", "medium"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="show population, no AI spend")
    ap.add_argument("--run", action="store_true", help="run the re-score")
    ap.add_argument("--n", type=int, default=20, help="how many jobs to re-score")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    if not (args.list or args.run):
        ap.error("choose --list or --run")

    drift_guard()
    db = SessionLocal()
    try:
        pop = probe_population(db)
        print(f"population: {len(pop)} dismissed-below-{settings.MATCH_DEADBAND_MIN_SCORE} "
              f"rows with composed job text > 5000 chars")
        if not pop:
            print("nothing to probe — no truncated postings were permanently dismissed")
            return
        if args.list:
            for j in pop:
                print(f"  {j['composed_len']:>6} chars  score={j['orig_score']:>2}  "
                      f"[{j['source']}] {j['title'][:60]} — {j['company']}")
            lengths = [j["composed_len"] for j in pop]
            print(f"\ncomposed len: min={min(lengths)} median={sorted(lengths)[len(lengths)//2]} "
                  f"max={max(lengths)}; >{JD_CAP} (still cut at the probe cap): "
                  f"{sum(1 for l in lengths if l > JD_CAP)}")
            return

        profile = db.query(Profile).filter(Profile.cv_text.isnot(None)).first()
        if profile is None:
            raise SystemExit("no profile with a CV — cannot re-score")
        profile_context = build_profile_context(profile)
        print(f"re-scoring {args.n} of {len(pop)} against the stored CV "
              f"({len(profile.cv_text)} chars, whole up to the shared "
              f"{CV_SLICE}-char context guard = production) "
              f"with JD cap {JD_CAP}")

        random.seed(args.seed)
        sample = random.sample(pop, min(args.n, len(pop)))
        service = get_ai_service()

        calls = 0
        results = []
        for i, job in enumerate(sample, 1):
            samples = []
            started = time.time()
            try:
                samples.append(
                    evaluate_once(service, profile_context, profile.cv_text, job["job_text"])
                )
                calls += 1
                while needs_another_sample(samples):
                    samples.append(
                        evaluate_once(service, profile_context, profile.cv_text, job["job_text"])
                    )
                    calls += 1
            except Exception as e:  # noqa: BLE001 — record and continue
                print(f"[{i:>2}/{len(sample)}] {job['title'][:50]!r} FAILED: {e}")
                results.append({**job, "error": str(e)})
                continue
            final, _ = resolve_samples(samples)
            row = {
                **{k: job[k] for k in ("job_id", "title", "company", "composed_len", "orig_score")},
                "new_score": final,
                "samples": [s["score"] for s in samples],
                "delta": final - job["orig_score"],
                "still_cut": job["composed_len"] > JD_CAP,
            }
            results.append(row)
            print(f"[{i:>2}/{len(sample)}] {job['orig_score']:>2} -> {final:>2} "
                  f"(samples {row['samples']}) {'STILL-CUT' if row['still_cut'] else ''} "
                  f"{job['title'][:48]!r} [{time.time()-started:.1f}s]", flush=True)
            if calls >= MAX_CALLS:
                print(f"spend guard hit ({calls} calls) — stopping early")
                break

        ok = [r for r in results if "new_score" in r]
        print("\n=== VERDICT ===")
        if not ok:
            print("no successful re-scores — check GLM connectivity")
            return
        deltas = [r["delta"] for r in ok]
        mean_o = statistics.mean(r["orig_score"] for r in ok)
        mean_n = statistics.mean(r["new_score"] for r in ok)
        tstat = (statistics.mean(deltas)
                 / (statistics.stdev(deltas) / len(deltas) ** 0.5)) if len(deltas) > 1 else 0.0
        print(f"n={len(ok)}  glim calls={calls}")
        print(f"mean orig={mean_o:.1f}  mean new={mean_n:.1f}  mean delta={statistics.mean(deltas):+.1f}")
        print(f"paired t ≈ {tstat:+.2f}  (|t| > 2 ≈ conventionally significant at n={len(ok)})")
        print(f"flipped out of permanent dismissal (>= {settings.MATCH_DEADBAND_MIN_SCORE}): "
              f"{sum(1 for r in ok if r['new_score'] >= settings.MATCH_DEADBAND_MIN_SCORE)}")
        print(f"would now be KEPT (>= keep-min {settings.MATCH_KEEP_MIN_SCORE}): "
              f"{sum(1 for r in ok if r['new_score'] >= settings.MATCH_KEEP_MIN_SCORE)}")
        out = Path("/tmp/truncation_probe_results.json")
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"full results: {out}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
