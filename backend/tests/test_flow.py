"""
Flow test: profile -> AI match (mocked GLM) -> decision -> draft -> submit.

Run:  .venv/bin/python -m tests.test_flow
Uses a temp SQLite DB; no network, no API keys.
"""

import json
import os
import sys
import uuid

# Under pytest, tests/conftest.py owns DATABASE_URL for the whole
# session (a hard set here overrode it and re-introduced the
# import-order database roulette). setdefault keeps this script
# runnable standalone: PYTHONPATH=. python tests/test_flow.py
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_flow.db")
os.environ.setdefault("DEBUG", "true")  # test env — production guards relaxed

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.crud import set_match_decision  # noqa: E402
from app.models import JobPosting, MatchResult, Profile  # noqa: E402
from app.services import matcher_service  # noqa: E402
from app.services.ai_service import AIService  # noqa: E402
from app.services.draft_service import create_draft_for_job, submit_draft  # noqa: E402

MATCH_JSON = json.dumps(
    {
        "score": 85,
        "tier": "excellent_match",
        "reasoning": "Strong overlap between your skills and the job requirements.",
        "matched_skills": ["Python", "FastAPI", "SQL"],
        "missing_skills": ["Kubernetes"],
        "transferable_skills": ["Regulatory compliance -> audit readiness"],
        "recommendation": "apply",
        "cover_note": "I bring 5 years of Python backend experience...",
        "confidence": "high",
    }
)

TAILOR_JSON = json.dumps(
    {
        "cover_letter": "Dear Acme team,\n\nI am applying for the Senior Python Developer role...",
        "tailored_cv": "PROFESSIONAL SUMMARY\nBackend developer with Python and FastAPI...",
        "changes_summary": ["Reordered skills to front-load Python and FastAPI for this role."],
    }
)

PROFILE_JSON = json.dumps(
    {
        "full_name": "Test User",
        "email": "test@example.com",
        "professional_title": "Backend Developer",
        "experience_years": 5,
        "skills": [{"name": "Python", "level": "expert"}],
        "keywords": ["python", "fastapi"],
        "summary": "Backend developer.",
    }
)


def fake_complete(self, system_prompt, user_message, temperature=0.3):
    if "application package" in system_prompt:  # tailor_application
        return TAILOR_JSON
    if "career coach analyzing" in system_prompt:  # extract_profile
        return PROFILE_JSON
    return MATCH_JSON


def main():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    AIService._complete = fake_complete  # monkeypatch the GLM call
    fake_service = AIService.__new__(AIService)
    fake_service.model = "glm-test"
    matcher_service.ai_service_available = lambda: True
    matcher_service.get_ai_service = lambda: fake_service
    from app.services import draft_service
    draft_service.ai_service_available = lambda: True
    draft_service.get_ai_service = lambda: fake_service

    db = SessionLocal()
    try:
        user_id = uuid.uuid4()
        profile = Profile(
            is_active=1,
            user_id=user_id,
            full_name="Test User",
            cv_text="Backend developer with Python, FastAPI, SQL. 5 years experience.",
            preferred_roles=json.dumps(["Backend Developer", "Python Developer"]),
        )
        db.add(profile)

        job = JobPosting(
            source="manual",
            source_id="t1",
            title="Senior Python Developer",
            company="Acme",
            url="https://example.com/job/1",
            description="We need a Python developer with FastAPI and SQL experience.",
            status="new",
        )
        db.add(job)
        db.commit()

        # 1. matching
        summary = matcher_service.run_matching(db, profile=profile, user_id=user_id)
        assert summary["matches_created"] == 1, summary
        match = db.query(MatchResult).first()
        assert match.score == 85 and match.tier == "excellent_match"
        assert job.status == "matched"
        print("PASS matching:", summary)

        # 2. decision
        match = set_match_decision(db, match, "approved")
        assert match.decision == "approved"  # job.status is user-scoped state now — lives in match_results
        print("PASS decision: approved")

        # 3. draft tailoring (mocked AI)
        draft = create_draft_for_job(db, job, profile=profile, user_id=user_id)
        assert draft.status == "ready", draft.error
        assert draft.cover_letter.startswith("Dear Acme")
        assert draft.tailored_cv.startswith("PROFESSIONAL SUMMARY")
        print("PASS draft: tailored CV + cover letter ready")

        # 4. submit (browser method — no email config needed)
        application = submit_draft(db, draft, "browser", profile, user_id=user_id)
        assert application.status == "manual_pending"
        assert draft.status == "submitted"
        # applied-ness derives from the applications row per user
        print("PASS submit:", application.status, "| job:", job.status)

        print("\nALL FLOW TESTS PASSED")
    finally:
        db.close()
        if os.path.exists("test_flow.db"):
            os.remove("test_flow.db")




if __name__ == "__main__":
    sys.exit(main())
