"""Re-score the legacy backlog with 3-sample averaging.

Re-scores all match_results rows whose prompt_version is 'legacy-unversioned'
(the pre-anchors, pre-temperature-fix prompt). Each keeper gets 3 samples
averaged (±6 instead of ±11), stamped with the current prompt_version.
Sub-25 scores get single-sample auto-pass (the triage path — no point
averaging a confident rejection).

Usage:
    .venv/bin/python scripts/rescore_backlog.py [--dry-run]

Cost: ~243 rows × 3 samples × $0.004 ≈ $3.00
Time: ~243 × 3 × 6s ≈ 73 minutes (single-threaded, respectful of API limits)
"""

import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEBUG", "true")

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import JobPosting, MatchResult, Profile  # noqa: E402
from app.services.ai_service import AIService  # noqa: E402
from app.services.cv_service import build_profile_context  # noqa: E402
from app.services.matcher_service import _job_text  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not settings.GLM_API_KEY:
        print("ERROR: GLM_API_KEY not set — cannot re-score.")
        return 2

    db = SessionLocal()
    svc = AIService()

    backlog = (
        db.query(MatchResult)
        .filter(MatchResult.prompt_version == "legacy-unversioned")
        .all()
    )
    print(f"Backlog: {len(backlog)} legacy-unversioned matches")
    if dry_run:
        for m in backlog[:5]:
            job = db.get(JobPosting, m.job_id)
            print(f"  [{m.id}] job={m.job_id} score={m.score} tier={m.tier} title={job.title[:40] if job else '?'}")
        print(f"  ... and {len(backlog) - 5} more")
        print("DRY RUN — no changes made. Run without --dry-run to re-score.")
        return 0

    # Profile for scoring context
    profile = db.query(Profile).filter(Profile.cv_text.isnot(None)).first()
    if not profile:
        print("ERROR: no profile with CV text found.")
        return 2
    ctx = build_profile_context(profile)
    print(f"Profile: {profile.full_name} ({len(profile.cv_text)} chars CV)")

    updated = 0
    errors = 0
    for i, match in enumerate(backlog):
        job = db.query(JobPosting).get(match.job_id)
        if not job or not job.description:
            print(f"  [{i+1}/{len(backlog)}] match={match.id} job={match.job_id}: SKIP (no job/description)")
            match.prompt_version = "orphaned-no-description"
            match.decision = "rejected"
            match.dismissed_reason = "no_description"
            db.add(match)
            db.commit()
            continue

        try:
            text = _job_text(job)
            scores = []
            for sample in range(3):
                result = svc.match_job(
                    profile_context=ctx,
                    cv_text=profile.cv_text,
                    job_description=text,
                )
                scores.append(result["score"])
                if sample == 0 and result["score"] < settings.MATCH_KEEP_MIN_SCORE:
                    # Confident rejection from triage — single sample suffices
                    break

            averaged = round(statistics.mean(scores)) if len(scores) > 1 else scores[0]
            tier = AIService._tier_for_score(averaged)
            old_score = match.score

            match.score = averaged
            match.tier = tier
            match.prompt_version = AIService.matching_prompt_version()
            match.model_used = svc.model
            # Clear any auto-pass/rejection decision from the old scoring if
            # the new score clears the keep line — the old decision was made
            # on an obsolete prompt
            if (
                match.decision == "rejected"
                and match.dismissed_reason in ("below_threshold", "dead_band_confirmed")
                and averaged >= settings.MATCH_KEEP_MIN_SCORE
            ):
                match.decision = None
                match.decided_at = None
                match.dismissed_reason = None

            db.add(match)
            db.commit()
            updated += 1

            if (i + 1) % 10 == 0 or i == len(backlog) - 1:
                print(
                    f"  [{i+1}/{len(backlog)}] job={job.title[:35]:35} "
                    f"{old_score:>3} -> {averaged:>3} ({tier[:14]:14}) "
                    f"samples={sorted(scores)}"
                )

        except Exception as e:
            errors += 1
            print(f"  [{i+1}/{len(backlog)}] match={match.id} ERROR: {type(e).__name__}: {e}")
            db.rollback()

    print(f"\nDone: {updated} re-scored, {errors} errors, {len(backlog) - updated - errors} skipped")
    print(f"New prompt_version on all: {AIService.matching_prompt_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
