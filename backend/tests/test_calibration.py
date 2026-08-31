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
CALIBRATED_PROMPT_VERSION = "m2-86693873"  # AI-13: MATCHING_INPUT_COMPOSITION_VERSION
# (=2) folded into the hash — the hash used to cover only the system prompt,
# so the experience_years removal from the profile context changed what the
# model SAW with no version bump. The prompt TEXT is unchanged; the rubric's
# MEANING is unchanged — MATCHING_PROMPT_MAJOR stays m2. Cross-version scores
# are not comparable BY DESIGN (the 236 pre-constant rows are already
# legacy-unversioned); rows stamped m2-57a0f692 are now one composition era
# older and re-score when picked up.


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
# 50 pooled samples at temp 0 across real jobs: SD 5.5. Alert if pooled
# SD exceeds 8 — that's where ±2×SD (=16) exceeds the dead-band width (12)
# and the re-score mechanism can no longer absorb the noise.
MAX_ACCEPTABLE_SD = 8.0

# Ambiguous fixtures: the model must JUDGE these, not pattern-match them.
# The original synthetic pair (every requirement verbatim in the CV) read
# SD ~1.1 — five times headroom vs the real 5.5. A calibration test that
# can't detect real variance is an API smoke test wearing a guard's name.
CALIBRATION_PAIRS = [
    {
        "name": "partial overlap + seniority gap",
        "cv": (
            "Career changer with 20 years in casino operations management. "
            "Recently completed intensive programming bootcamp. Built fullstack "
            "web app with Python, FastAPI, React. Familiar with SQL and basic "
            "Docker. No professional software development experience yet — "
            "seeking first junior developer role."
        ),
        "job": (
            "Senior Fullstack Developer. Requirements: 5+ years professional "
            "software development. Expert-level React and TypeScript. Strong "
            "Python backend experience with FastAPI or Django. Production "
            "experience with PostgreSQL, Redis, and AWS. CI/CD and Kubernetes "
            "experience preferred. You will lead architecture decisions and "
            "mentor junior developers."
        ),
    },
    {
        "name": "adjacent domain + tool gap",
        "cv": (
            "Registered nurse with 8 years ICU experience. Master's in Health "
            "Informatics. Built clinical dashboards with Python and pandas. "
            "Experience with HL7/FHIR data standards. Learning web development "
            "with basic HTML/CSS/JavaScript. Strong analytical and "
            "documentation skills."
        ),
        "job": (
            "Healthcare Data Integration Engineer. Requirements: Experience "
            "with HL7 v2 and FHIR APIs. Python programming for data pipelines. "
            "SQL and database design. RESTful API development. Nice to have: "
            "clinical background, Epic or Cerner integration experience, "
            "TypeScript/Node.js."
        ),
    },
    {
        "name": "stack match + domain mismatch",
        "cv": (
            "Junior developer. Proficient in Python, JavaScript, React, "
            "Node.js. Built e-commerce platform and real-time chat app. "
            "Comfortable with Git, basic AWS, and MongoDB. Self-taught with "
            "2 years of freelance projects. No formal CS education."
        ),
        "job": (
            "Embedded Systems Developer. Requirements: C/C++ programming for "
            "ARM microcontrollers. Experience with RTOS, device drivers, and "
            "hardware-software interface. Familiarity with firmware debugging "
            "tools (JTAG, oscilloscope). Nice to have: Python for test "
            "automation, CI/CD for embedded targets."
        ),
    },
]


@pytest.mark.skipif(not RUN_LIVE, reason="needs RUN_CALIBRATION=1 and GLM_API_KEY")
class TestLiveVariance:
    """Re-measures the real model. Costs API calls; opt-in only.

    Asserts on POOLED STANDARD DEVIATION across multiple ambiguous pairs,
    not max−min of a single easy pair. Spread only moves up with sample
    count; SD across diverse inputs is the statistic the dead-band width
    is derived from. The fixtures are deliberately ambiguous (partial
    overlap, seniority gaps, domain mismatches) because the model must
    JUDGE them — a near-perfect match by construction produces artificially
    low variance (~1.1 SD vs the ~5.5 measured on real jobs).
    """

    RUNS = 5

    def _ensure_schema(self):
        from alembic.config import Config
        from sqlalchemy import inspect

        from alembic import command
        from app.core.database import engine
        if not inspect(engine).get_table_names():
            cfg = Config("alembic.ini")
            cfg.set_main_option(
                "sqlalchemy.url",
                os.environ.get("DATABASE_URL", "sqlite:///./test_suite.db"),
            )
            command.upgrade(cfg, "head")

    def test_same_input_scores_within_tolerance(self):
        import uuid

        self._ensure_schema()

        from app.core.database import SessionLocal
        from app.models import JobPosting, Profile
        from app.services.cv_service import build_profile_context
        from app.services.matcher_service import _job_text

        svc = AIService()
        all_scores = []
        pair_summaries = []

        for pair in CALIBRATION_PAIRS:
            db = SessionLocal()
            profile = Profile(
                user_id=uuid.uuid4(), is_active=1,
                full_name="Calibration Test",
                cv_text=pair["cv"],
            )
            job = JobPosting(
                source="calibration", source_id=uuid.uuid4().hex[:8],
                title=pair["name"],
                company="Calibration Corp",
                url=f"https://calibration.test/{uuid.uuid4().hex[:6]}",
                description=pair["job"],
                status="new",
            )
            db.add_all([profile, job])
            db.commit()
            db.refresh(profile)
            db.refresh(job)

            ctx = build_profile_context(profile)
            text = _job_text(job)
            profile_id, job_id = profile.id, job.id
            db.query(JobPosting).filter(JobPosting.id == job_id).delete()
            db.query(Profile).filter(Profile.id == profile_id).delete()
            db.commit()
            db.close()

            scores = []
            for _ in range(self.RUNS):
                scores.append(
                    svc.match_job(profile_context=ctx, cv_text=pair["cv"], job_description=text)["score"]
                )
            pair_sd = statistics.stdev(scores) if len(scores) > 1 else 0
            all_scores.extend(scores)
            pair_summaries.append((pair["name"], sorted(scores), pair_sd))

        # MAX within-job SD — the reviewer's call: the mean would let one
        # pair drift to 15 while two sit at 2 and still pass at 6.3. Max
        # gives a real signal with the current 7.4 already present.
        max_within_sd = max(sd for _, _, sd in pair_summaries)

        for name, scores, sd in pair_summaries:
            print(f"  {name}: scores={scores} SD={sd:.1f} mean={statistics.mean(scores):.1f}")
        print(
            f"\n  model={svc.model} version={AIService.matching_prompt_version()} "
            f"max within-job SD={max_within_sd:.1f} "
            f"(per-pair sorted desc: {sorted([f'{sd:.1f}' for _, _, sd in pair_summaries], reverse=True)}) "
            f"n={len(all_scores)} total samples"
        )
        assert max_within_sd <= MAX_ACCEPTABLE_SD, (
            f"Max within-job SD {max_within_sd:.1f} exceeds {MAX_ACCEPTABLE_SD}. "
            f"Per-pair SDs: {[f'{name}: {sd:.1f}' for name, _, sd in pair_summaries]}. "
            "At SD > 8, 2×SD exceeds the dead-band width and borderline jobs "
            "are again dismissed on noise. Check that match_job still passes "
            "temperature=0.0 — the fix has silently moved to another call before."
        )
