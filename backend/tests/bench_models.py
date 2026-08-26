"""
Benchmark GLM models on the Z.ai coding endpoint with a REAL match prompt.

Measures latency + JSON validity for each candidate model using the active
profile's CV and a real scraped job, through the exact matcher code path.

Run: .venv/bin/python -m tests.bench_models
"""

import sys
import time

from app.core.database import SessionLocal
from app.models import JobPosting, Profile
from app.services.ai_service import AIService
from app.services.cv_service import build_profile_context

# As measured 2026-08-25/26 (CLAUDE.md bake-off): 4.6 = 3 concurrent + noisy
# scoring; 5.1 = 10 concurrent, stable, CURRENT production matcher. 4-plus
# is unavailable on the current plan (429 insufficient quota).
CANDIDATES = ["glm-4.6", "glm-5.1", "glm-5.2"]


def main():
    db = SessionLocal()
    profile = db.query(Profile).filter(Profile.is_active == 1).first()
    job = (
        db.query(JobPosting)
        .filter(JobPosting.status == "new", JobPosting.description.isnot(None))
        .order_by(JobPosting.id)
        .first()
    )
    db.close()

    if not profile or not job:
        print("Need an active profile and a queued job first")
        return 1

    from app.services.matcher_service import _job_text

    system_prompt = AIService._build_matching_prompt(AIService.__new__(AIService))
    user_message = f"""
## My Profile & Preferences
{build_profile_context(profile)}

## My CV (evidence)
{profile.cv_text[:5000]}

## Job Posting
{_job_text(job)[:5000]}

Evaluate this job for me and respond with ONLY valid JSON in the required format.
"""

    service = AIService()  # raises without key
    print(f"Job: {job.title[:60]} | CV: {len(profile.cv_text)} chars\n")

    results = []
    for model in CANDIDATES:
        service.model = model
        start = time.time()
        try:
            raw = service._complete(system_prompt, user_message)
            elapsed = time.time() - start
            parsed = service._parse_json(raw)
            valid = all(k in parsed for k in ("score", "tier", "recommendation", "cover_note"))
            results.append((model, elapsed, valid, parsed))
            print(
                f"{model:<10} {elapsed:6.1f}s  valid_json={valid}  "
                f"score={parsed.get('score')} tier={parsed.get('tier')}"
            )
        except Exception as e:
            elapsed = time.time() - start
            results.append((model, elapsed, False, {}))
            print(f"{model:<10} {elapsed:6.1f}s  FAILED: {type(e).__name__}: {str(e)[:80]}")

    print("\n--- ranking by speed (valid JSON only) ---")
    for model, elapsed, valid, parsed in sorted(results, key=lambda r: r[1]):
        if valid:
            print(f"{model:<10} {elapsed:6.1f}s  score={parsed.get('score')} rec={parsed.get('recommendation')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
