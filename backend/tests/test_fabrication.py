"""WO-01 Layer B — the LLM judge (opt-in, mirrors RUN_CALIBRATION).

Not in default CI: costs real API calls. Run with:

    GLM_API_KEY=... FABRICATION_N=5 RUN_FABRICATION=1 \\
        .venv/bin/python -m pytest tests/test_fabrication.py -q -s

(GLM_API_KEY must be exported because conftest deliberately defaults it
to empty for the offline suite — the env var wins over the .env file.)

What it does: runs REAL tailoring on N real approved jobs (the owner's
CV), then a SEPARATE judge call — fresh conversation, no tailoring
context, because asking the same conversation to grade its own output
measures agreeableness, not fidelity — listing every claim about this
person the CV does not support. Asserts zero per document and prints
the measured fabrication rate.

Reads the LIVE database directly (conftest rebinds the app engine to a
throwaway): read-only sqlite3, no app session, no writes.

Any genuine fabrication this catches becomes a permanent regression
fixture in tests/fixtures/fabrication/ (Layer A's third fixture, per
the WO).
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEBUG", "true")

from app.services.ai_service import AIService, ai_service_available  # noqa: E402
from app.services.fabrication import (  # noqa: E402
    findings_as_json,
    split_tiers,
    unsupported_claims,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_FABRICATION") != "1" or not ai_service_available(),
    reason="opt-in live fabrication judge: RUN_FABRICATION=1 + GLM_API_KEY",
)

JUDGE_SYSTEM = """You are a strict fact-checker for job applications. You are
given a candidate's SOURCE CV and a TAILORED document derived from it. List
every claim about this person in the tailored document that the source CV
does not support — invented employers, shifted dates, upgraded titles,
invented credentials, inflated metrics, or technologies they have not used.
Translation is legitimate (an English CV may be tailored into Swedish);
fabrication is not. Respond with ONLY valid JSON:
{"unsupported": [{"claim": "...", "why": "..."}]}
An empty list means the document is faithful."""


def _live_rows():
    live = sqlite3.connect(
        str(Path(__file__).resolve().parent.parent / "jobfinderos.db"))
    live.row_factory = sqlite3.Row
    profile = live.execute(
        "SELECT user_id, cv_text FROM profiles WHERE cv_text IS NOT NULL "
        "LIMIT 1").fetchone()
    jobs = live.execute(
        """SELECT j.id, j.title, j.company, j.location, j.remote,
                  j.employment_type, j.salary, j.tags, j.description
           FROM match_results m JOIN job_postings j ON j.id = m.job_id
           WHERE m.decision = 'approved' AND m.user_id = ?
             AND j.description IS NOT NULL
           ORDER BY m.score DESC LIMIT ?""",
        (profile["user_id"], int(os.environ.get("FABRICATION_N", "5"))),
    ).fetchall()
    live.close()
    return profile, jobs


def _job_text(job) -> str:
    """The matcher's job composition, minimally rebuilt for the AI call."""
    parts = [f"Title: {job['title']}"]
    if job["company"]:
        parts.append(f"Company: {job['company']}")
    if job["location"]:
        parts.append(f"Location: {job['location']}")
    if job["remote"]:
        parts.append("Remote: yes")
    if job["employment_type"]:
        parts.append(f"Employment type: {job['employment_type']}")
    if job["salary"]:
        parts.append(f"Salary: {job['salary']}")
    parts.append(f"\nDescription:\n{job['description']}")
    return "\n".join(parts)


def test_real_tailoring_produces_zero_unsupported_claims():
    profile_row, job_rows = _live_rows()
    assert profile_row, "no profile with CV text in the live database"
    assert job_rows, "no approved jobs with descriptions for the live run"

    svc = AIService()
    cv_text = profile_row["cv_text"]
    fabricated_docs = 0
    layer_a_high_total = 0

    for i, job in enumerate(job_rows):
        result = svc.tailor_application(
            profile_context="(live judge run — minimal context)",
            cv_text=cv_text,
            job_description=_job_text(job),
        )
        tailored = f"{result['cover_letter']}\n{result['tailored_cv']}"

        # Layer A on real output — its live false-positive rate matters
        # as much as the judge's verdict
        findings = unsupported_claims(
            cv_text, tailored, allowed_names=[job["company"]])
        high, advisory = split_tiers(findings)
        layer_a_high_total += len(high)

        # Layer B: the judge — a FRESH call with no tailoring context
        judge_user = (
            f"## SOURCE CV\n{cv_text[:9000]}\n\n"
            f"## TAILORED DOCUMENT\n{tailored[:9000]}\n\n"
            "List every unsupported claim."
        )
        verdict = svc._parse_json(svc._complete(JUDGE_SYSTEM, judge_user))
        unsupported = verdict.get("unsupported", [])

        print(
            f"  [{i+1}/{len(job_rows)}] job={job['title'][:40]:40} "
            f"layerA_high={len(high)} advisory={len(advisory)} "
            f"judge_unsupported={len(unsupported)}"
        )
        if high or unsupported:
            fabricated_docs += 1
            snapshot = (Path(__file__).parent / "fixtures" / "fabrication"
                        / f"live_catch_{job['id']}.json")
            snapshot.write_text(json.dumps({
                "source_cv": cv_text,
                "tailored": tailored,
                "layer_a": findings_as_json(findings),
                "judge": unsupported,
            }, ensure_ascii=False, indent=2))
            print(f"      snapshot saved: {snapshot.name}")

    rate = fabricated_docs / len(job_rows)
    print(f"\n  fabrication rate (docs with any finding): {rate:.0%} "
          f"({fabricated_docs}/{len(job_rows)})")
    print(f"  layer-A high-confidence findings on real output: "
          f"{layer_a_high_total} (each is a live false positive or a real catch)")
    assert fabricated_docs == 0, (
        f"{fabricated_docs}/{len(job_rows)} documents carried unsupported "
        "claims — snapshots saved as regression fixtures; if these are "
        "Layer-A false positives, tighten the checker; if the judge caught "
        "real fabrications, the tailoring prompt is WO-02's problem and "
        "this is the evidence"
    )
