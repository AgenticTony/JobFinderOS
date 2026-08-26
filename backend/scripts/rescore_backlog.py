"""Re-score the legacy backlog with 3-sample averaging.

Re-scores match_results rows on a given prompt_version (default:
'legacy-unversioned'). Each keeper gets 3 samples averaged (±6 instead of
±11), and score AND prose are refreshed together via resolve_samples —
the shared matcher protocol — so no row ever pairs a current-prompt score
with legacy-prompt prose. Sub-25 scores get single-sample auto-pass (the
triage path — no point averaging a confident rejection).

Usage:
    .venv/bin/python scripts/rescore_backlog.py [--dry-run] [--prompt-version <ver>]

Cost: ~243 rows × 3 samples × $0.004 ≈ $3.00
Time: ~243 × 3 × 6s ≈ 73 minutes (single-threaded, respectful of API limits)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEBUG", "true")

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import JobPosting, MatchResult, Profile  # noqa: E402
from app.schemas.common import dump_json_list  # noqa: E402
from app.services.ai_service import AIService  # noqa: E402
from app.services.cv_service import build_profile_context  # noqa: E402
from app.services.matcher_service import _job_text, resolve_samples  # noqa: E402

# The auto-pass stamp the matcher writes on sub-threshold rows. A constant,
# not a literal in two places: the fall-branch writes it and the rise-branch
# recognizes it by exact match (so it can shed the stamp without ever
# touching fresh model prose, which just overwrote the field).
AUTOPASS_REASONING = "Auto-passed: below the score threshold for your CV."


def apply_rescore(match: MatchResult, samples: list, model: str) -> int:
    """Write a COMPLETE re-score onto a match row: score AND prose.

    resolve_samples is the shared protocol (matcher_service): the payload
    comes from the sample closest to the final mean. The previous version
    of this script kept only result["score"] and discarded the payloads —
    241 rows ended up with a current-prompt score next to legacy-prompt
    prose (verified against the pre-run snapshot: 0 cover_note changes).

    Then version stamps, then the bidirectional dismissal derivation,
    which overwrites the payload fields with the auto-pass stamp on
    sub-threshold rows — mirroring the matcher's auto-pass write.

    Mutates `match` in place; the caller commits. Returns the final score.
    """
    averaged, best_payload = resolve_samples(samples)
    match.score = averaged
    match.tier = AIService._tier_for_score(averaged)
    match.prompt_version = AIService.matching_prompt_version()
    match.model_used = model
    match.reasoning = best_payload.get("reasoning")
    match.matched_skills = dump_json_list(best_payload.get("matched_skills", []))
    match.missing_skills = dump_json_list(best_payload.get("missing_skills", []))
    match.transferable_skills = dump_json_list(best_payload.get("transferable_skills", []))
    match.recommendation = best_payload.get("recommendation")
    match.cover_note = best_payload.get("cover_note")
    match.confidence = best_payload.get("confidence")
    derive_dismissal(match, settings.MATCH_KEEP_MIN_SCORE)
    return averaged


def derive_dismissal(match: MatchResult, keep_min: int) -> None:
    """Bidirectional dismissal derivation — the SINGLE source of truth.

    The one-directional version left 176 sub-threshold rows live in the
    queue. After a score changes, apply the matcher's rules in BOTH
    directions:

    - score >= keep_min: a row that rose above keep-min sheds any auto-pass
      stamp from the older, lower score. The stamp is FOUR fields —
      decision, dismissed_reason, recommendation='skip', AND the
      AUTOPASS_REASONING text. Clearing only the first three leaves a
      strong row telling the user it was auto-passed for being too weak
      (MatchCard renders reasoning as the primary explanation). Reasoning
      is matched by exact stamp text so fresh model prose — written just
      before this runs — is never touched.
    - score < keep_min: sub-threshold rows never stay live, whatever the
      previous decision was — reject with below_threshold.

    Mutates `match` in place; the caller commits. Tests import THIS
    function, never a reimplementation — a copy in the test file only
    guards itself (regressing this script to the one-directional bug left
    26 tests passing when the test ran its own loop).
    """
    if match.score >= keep_min:
        if (
            match.decision == "rejected"
            and match.dismissed_reason in ("below_threshold", "dead_band_confirmed")
        ):
            match.decision = None
            match.decided_at = None
            match.dismissed_reason = None
            if match.recommendation == "skip":
                match.recommendation = None
            if match.reasoning == AUTOPASS_REASONING:
                match.reasoning = None
    else:
        match.decision = "rejected"
        match.decided_at = None
        match.dismissed_reason = "below_threshold"
        match.recommendation = "skip"
        match.reasoning = AUTOPASS_REASONING


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    # --prompt-version <ver>: re-score rows currently stamped with that
    # version. Default stays 'legacy-unversioned' (the original backlog).
    # Needed for the one-time correction after the payload bug: the broken
    # run stamped prompt_version=current while leaving legacy prose, so
    # those rows are selected by their CURRENT version — 'legacy-unversioned'
    # no longer matches them.
    target_version = "legacy-unversioned"
    if "--prompt-version" in sys.argv:
        target_version = sys.argv[sys.argv.index("--prompt-version") + 1]

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
        .filter(MatchResult.prompt_version == target_version)
        .all()
    )
    print(f"Backlog: {len(backlog)} rows on prompt_version '{target_version}'")
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
            samples = []
            for sample in range(3):
                result = svc.match_job(
                    profile_context=ctx,
                    cv_text=profile.cv_text,
                    job_description=text,
                )
                # FULL result dicts — the payload must be refreshed with the
                # score. The previous run kept only result["score"]: 241 rows
                # ended up with a current-prompt score next to legacy-prompt
                # prose (0 cover_note changes vs the pre-run snapshot).
                samples.append(result)
                if sample == 0 and result["score"] < settings.MATCH_KEEP_MIN_SCORE:
                    # Confident rejection from triage — single sample suffices
                    break

            old_score = match.score
            averaged = apply_rescore(match, samples, svc.model)

            db.add(match)
            db.commit()
            updated += 1

            if (i + 1) % 10 == 0 or i == len(backlog) - 1:
                print(
                    f"  [{i+1}/{len(backlog)}] job={job.title[:35]:35} "
                    f"{old_score:>3} -> {averaged:>3} ({match.tier[:14]:14}) "
                    f"samples={sorted(s['score'] for s in samples)}"
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
