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

    # MANDATORY pre-write snapshot — a destructive script must never rely on
    # the operator having taken a manual backup (that's a habit, not a control).
    # The path is resolved from __file__ (NOT relative — sqlite3 on a missing
    # DB exits 0 and writes a valid EMPTY database, so a relative path run
    # from the repo root would create an empty snapshot and print a restore
    # command that destroys the real database). The snapshot is then verified
    # to contain rows before the first write.
    import subprocess
    import time as _time

    db_path = Path(__file__).resolve().parent.parent / "jobfinderos.db"
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}")
        return 2

    snapshot_name = f"jfos-rescore-{_time.strftime('%Y%m%d-%H%M%S')}.db"
    snapshot_dst = Path.home() / "backups" / "jobfinderos" / snapshot_name
    snapshot_dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sqlite3", str(db_path), f".backup '{snapshot_dst}'"],
        check=True,
        capture_output=True,
    )

    # Verify the snapshot actually contains data (sqlite3's .backup on a
    # missing/corrupted source writes a valid empty DB with exit 0)
    verify = subprocess.run(
        ["sqlite3", str(snapshot_dst), "SELECT COUNT(*) FROM match_results;"],
        capture_output=True,
        text=True,
    )
    row_count = int(verify.stdout.strip() or "0")
    if row_count == 0:
        snapshot_dst.unlink(missing_ok=True)
        print("ERROR: snapshot verification failed — 0 match_results rows.")
        print(f"The database at {db_path} may be empty or corrupted. Aborting.")
        return 2

    print(f"Snapshot: {snapshot_dst} ({row_count} match rows)")
    print(f"Restore:  cp {snapshot_dst} {db_path}")

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

            # BIDIRECTIONAL dismissal derivation (the one-directional version
            # left 176 sub-threshold rows live in the queue). Apply the same
            # rules the matcher uses, in both directions:
            if averaged >= settings.MATCH_KEEP_MIN_SCORE:
                # Score ROSE above keep-min: clear any auto-pass from the old
                # scoring (the old rejection was on an obsolete prompt)
                if (
                    match.decision == "rejected"
                    and match.dismissed_reason in ("below_threshold", "dead_band_confirmed")
                ):
                    match.decision = None
                    match.decided_at = None
                    match.dismissed_reason = None
            else:
                # Score FELL below keep-min: the old decision may have been
                # 'approved' or pending — sub-threshold rows never stay live
                match.decision = "rejected"
                match.decided_at = None
                match.dismissed_reason = "below_threshold"
                match.recommendation = "skip"
                match.reasoning = "Auto-passed: below the score threshold for your CV."

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

    # POST-RUN INVARIANT CHECK — the lesson from the 176-row leak: query the
    # invariant, not the symptom. These are the guarantees the matcher makes;
    # the re-score script must leave them intact.
    violations = {
        "sub-threshold without dismissal": db.query(MatchResult)
            .filter(MatchResult.score < settings.MATCH_KEEP_MIN_SCORE,
                    MatchResult.dismissed_reason.is_(None)).count(),
        "sub-threshold with wrong decision": db.query(MatchResult)
            .filter(MatchResult.score < settings.MATCH_KEEP_MIN_SCORE,
                    MatchResult.decision.is_(None)).count(),
        "strong row wrongly dismissed": db.query(MatchResult)
            .filter(MatchResult.score >= settings.MATCH_KEEP_MIN_SCORE,
                    MatchResult.dismissed_reason == "below_threshold").count(),
        "skip recommended on strong row": db.query(MatchResult)
            .filter(MatchResult.score >= 50,
                    MatchResult.recommendation == "skip").count(),
    }
    all_zero = all(v == 0 for v in violations.values())
    for name, count in violations.items():
        status = "OK" if count == 0 else f"VIOLATIONS: {count}"
        print(f"  {name}: {status}")
    if not all_zero:
        print("\nERROR: invariant violations detected — do NOT trust the queue until fixed.")
        return 1
    print("All invariants hold.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
