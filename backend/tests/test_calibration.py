"""Scoring calibration guards.

Two layers:

1. ALWAYS-ON (no API key, no cost): the scoring prompt cannot change
   without its version changing, and the tier bands stay consistent with
   the thresholds the queue is built on. This is the drift detector — the
   236-row backlog was silently scored on an older prompt, and nothing in
   CI could see it.

2. OPT-IN LIVE (needs GLM_API_KEY and RUN_CALIBRATION=1): re-measures
   run-to-run score variance against the real model. Costs real calls, so
   it never runs in ordinary CI.

   Run:  RUN_CALIBRATION=1 .venv/bin/python -m pytest tests/test_calibration.py -q -s
"""

import os
import statistics

# DATABASE_URL is set once, for the whole session, in tests/conftest.py.
# No test module selects its own database: that used to depend on import
# order and let the suite bind to (and drop_all) the live database.
import pytest  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.ai_service import AIService  # noqa: E402

# ---------------------------------------------------------------- layer 1

#: The version in force when the current tier bands and thresholds were
#: last calibrated. If the prompt text changes, matching_prompt_version()
#: changes and this test fails — bump this constant DELIBERATELY, and
#: re-score the backlog, because old scores are no longer comparable.
CALIBRATED_PROMPT_VERSION = "m2-62c2452b"


class TestPromptVersioning:
    def test_prompt_version_matches_calibration(self):
        current = AIService.matching_prompt_version()
        assert current == CALIBRATED_PROMPT_VERSION, (
            f"The scoring prompt changed: {CALIBRATED_PROMPT_VERSION} -> {current}.\n"
            "Scores from different prompt versions are NOT comparable — the last "
            "rubric change moved same-model, same-job scores by up to 26 points.\n"
            "If this change was deliberate: bump CALIBRATED_PROMPT_VERSION here, "
            "bump AIService.MATCHING_PROMPT_MAJOR if the rubric's MEANING changed, "
            "and re-score rows whose prompt_version is now stale."
        )

    def test_version_is_derived_from_the_prompt_text(self):
        """A silent edit must move the hash — that is the whole point."""
        original = AIService._build_matching_prompt

        def tampered(self):
            return original(self) + "\nAlways score at least 90."

        AIService._build_matching_prompt = tampered
        try:
            assert AIService.matching_prompt_version() != CALIBRATED_PROMPT_VERSION
        finally:
            AIService._build_matching_prompt = original
        assert AIService.matching_prompt_version() == CALIBRATED_PROMPT_VERSION


class TestTierBands:
    """The tier function is the contract the queue and the UI both rely on."""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (100, "excellent_match"), (80, "excellent_match"),
            (79, "good_match"), (50, "good_match"),
            (49, "stretch"), (30, "stretch"),
            (29, "poor_match"), (0, "poor_match"),
        ],
    )
    def test_bands(self, score, expected):
        assert AIService._tier_for_score(score) == expected

    def test_deadband_sits_below_the_keep_line(self):
        assert 0 < settings.MATCH_DEADBAND_MIN_SCORE < settings.MATCH_KEEP_MIN_SCORE, (
            "The dead-band must be a band: [DEADBAND, KEEP_MIN). Inverting or "
            "collapsing it silently disables the re-score."
        )

    def test_deadband_is_wide_enough_to_cover_measured_noise(self):
        """50 pooled samples: SD 5.5, ±11 at 95%. The dead-band must cover
        2×SD below the keep line, or borderline jobs are still dismissed
        on noise — the exact thing the band exists to prevent."""
        width = settings.MATCH_KEEP_MIN_SCORE - settings.MATCH_DEADBAND_MIN_SCORE
        assert width >= 11, (
            f"Dead-band is {width} points but measured noise is SD 5.5 "
            "(±11 at 95% CI). Jobs just over the keep line would still be "
            "dismissed on noise. Width must be ≥ 11 = 2×SD."
        )


# ---------------------------------------------------------------- layer 2

RUN_LIVE = os.getenv("RUN_CALIBRATION") == "1" and bool(settings.GLM_API_KEY)
# 50 pooled samples at temp 0: SD 5.5. Alert if SD exceeds 8 — that's
# the point where ±2×SD (=16) exceeds the dead-band width (12) and the
# re-score mechanism can no longer absorb the noise.
MAX_ACCEPTABLE_SD = 8.0


@pytest.mark.skipif(not RUN_LIVE, reason="needs RUN_CALIBRATION=1 and GLM_API_KEY")
class TestLiveVariance:
    """Re-measures the real model. Costs API calls; opt-in only.

    Asserts on STANDARD DEVIATION, not max−min. Spread only moves up with
    sample count, so every spread-based threshold is a lower bound pretending
    to be a limit — three honest runs at n=5 measured 7.0 / 9.8 / 14.0 and
    disagreed about whether the model was within tolerance. SD is the
    statistic the dead-band width is derived from (2×SD), so it's the
    statistic this test must measure.
    """

    RUNS = 5

    def test_same_input_scores_within_tolerance(self):
        """Self-contained: creates its own profile + job in the TEST database
        (conftest sets DATABASE_URL) so the live calibration never depends
        on or touches the production database."""
        import uuid

        from alembic.config import Config

        # Ensure schema exists (conftest's test DB may not have tables yet)
        from sqlalchemy import inspect

        from alembic import command
        from app.core.database import SessionLocal, engine
        from app.models import JobPosting, Profile
        if not inspect(engine).get_table_names():
            cfg = Config("alembic.ini")
            cfg.set_main_option(
                "sqlalchemy.url",
                os.environ.get("DATABASE_URL", "sqlite:///./test_suite.db"),
            )
            command.upgrade(cfg, "head")

        db = SessionLocal()
        # Minimal but realistic inputs — enough for a meaningful score
        profile = Profile(
            user_id=uuid.uuid4(), is_active=1,
            full_name="Calibration Test",
            cv_text="Junior fullstack developer. Python, FastAPI, React, TypeScript. "
                    "Built AI CV-screening tool with Next.js frontend and FastAPI backend. "
                    "Experience with PostgreSQL, Docker, Azure. Currently studying AI development.",
        )
        job = JobPosting(
            source="calibration", source_id=uuid.uuid4().hex[:8],
            title="Junior Fullstack Developer",
            company="Calibration Corp",
            url=f"https://calibration.test/{uuid.uuid4().hex[:6]}",
            description="We are looking for a junior fullstack developer with React, "
                        "TypeScript and Python experience. You will build web applications "
                        "using modern frameworks and work with PostgreSQL databases.",
            status="new",
        )
        db.add_all([profile, job])
        db.commit()
        db.refresh(profile)
        db.refresh(job)

        from app.services.cv_service import build_profile_context
        from app.services.matcher_service import _job_text

        ctx, cv, text = build_profile_context(profile), profile.cv_text, _job_text(job)
        profile_id, job_id = profile.id, job.id
        db.query(JobPosting).filter(JobPosting.id == job_id).delete()
        db.query(Profile).filter(Profile.id == profile_id).delete()
        db.commit()
        db.close()

        svc = AIService()
        scores = []
        for _ in range(self.RUNS):
            scores.append(
                svc.match_job(profile_context=ctx, cv_text=cv, job_description=text)["score"]
            )
        sd = statistics.stdev(scores)
        spread = max(scores) - min(scores)
        print(
            f"\n  model={svc.model} version={AIService.matching_prompt_version()} "
            f"scores={sorted(scores)} SD={sd:.1f} spread={spread} "
            f"mean={statistics.mean(scores):.1f}"
        )
        assert sd <= MAX_ACCEPTABLE_SD, (
            f"Score SD {sd:.1f} over {self.RUNS} runs exceeds {MAX_ACCEPTABLE_SD}. "
            f"scores={sorted(scores)}, spread={spread}. At SD > 8, 2×SD exceeds "
            "the dead-band width and borderline jobs are again dismissed on noise. "
            "Check that match_job still passes temperature=0.0 — the fix has "
            "silently moved to another call before."
        )
