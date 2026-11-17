"""WO-22 slice s1 — apply-time link recovery: on a dead portal, hand off
the live twin from the pool. Acceptance tests (red-first per the WO).

Every test maps to a contract acceptance item:

- AC-1: dead apply URL + live pool twin -> the application row carries
  the twin's link (the outbound artifact) and the response exposes a
  substitution note naming the source the original was opened from.
- AC-2: dead apply URL + twin whose own link is also dead -> the
  original warning, no substitution. The twin is re-probed (its
  application_url, else its url) before being trusted; an email-only
  twin has no probeable HTTP link and is never used.
- AC-3: 401/403/timeout probe outcomes are NOT death — the pool lookup
  never fires for them.
- AC-4: no twin in the pool -> today's behavior, warning byte-identical.
- AC-5: every substitution is audited on the application row itself
  (dead URL, twin URL, source, timestamp) — no separate log table.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_wo22_apply_link_recovery.py -q
"""

import json
import uuid
from datetime import datetime

import httpx
import pytest

from app.core.database import engine
from app.core.orm import Base
from app.models import (
    ApplicationDraft,
    JobPosting,
    Profile,
    User,
)
from app.schemas.application import ApplicationResponse
from app.services import draft_service

DEAD_URL = "https://jobviewtrack.example/dead-apply"
TWIN_URL = "https://acme-employer.example/careers/integration-developer"

# Today's warning text, verbatim from the probe (the AC-4 contract:
# byte-identical). 401/403 have their own text; every other >= 400
# (405 aside) is the expiry class.
BOT_BLOCK_401 = (
    "Apply portal blocked our automated check (HTTP 401) — many employer "
    "and recruiter sites block bots while serving browsers normally. The "
    "link will likely open fine; check the page if it does not."
)
BOT_BLOCK_403 = (
    "Apply portal blocked our automated check (HTTP 403) — many employer "
    "and recruiter sites block bots while serving browsers normally. The "
    "link will likely open fine; check the page if it does not."
)


def expiry_warning(code: int) -> str:
    return (
        f"Apply portal returned HTTP {code} at hand-off — "
        "this posting may have expired. Check the page loads before applying."
    )


@pytest.fixture()
def db():
    from app.core.database import SessionLocal
    from tests.conftest import stamp_alembic_head

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    stamp_alembic_head()
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------
# row factories
# ---------------------------------------------------------------------


def _actor(db, label="wo22"):
    user = User(id=uuid.uuid4(), email=f"{label}-{uuid.uuid4().hex[:6]}@test.example",
                hashed_password="test-only")
    db.add(user)
    db.flush()
    profile = Profile(
        is_active=1, user_id=user.id, full_name="W O Two Two",
        cv_file_name="cv.pdf", cv_text="integration developer",
        languages='["English"]',
    )
    db.add(profile)
    db.commit()
    return user, profile


def _job(db, *, source="careerjet", title="Integration Developer",
         company="Acme AB", url=DEAD_URL, application_url=None,
         application_email=None, description=None):
    job = JobPosting(
        source=source, source_id=uuid.uuid4().hex[:8], title=title,
        company=company, url=url, status="matched",
        application_url=application_url, application_email=application_email,
        description=description,
    )
    db.add(job)
    db.commit()
    return job


def _ready_draft(db, user, job):
    draft = ApplicationDraft(
        user_id=user.id, job_id=job.id, status="ready",
        cover_letter="Dear Acme", tailored_cv="TAILORED CV",
        changes_summary="[]",
    )
    db.add(draft)
    db.commit()
    return draft


def _submit(db, monkeypatch, user, profile, job, responses):
    """Stub the probe's single boundary (httpx.head): {url-fragment:
    status code | Exception}; unmatched URLs answer 200. Returns the
    submitted application row and every URL the probe visited."""
    calls = []

    def fake_head(url, **kwargs):
        calls.append(url)
        for needle, result in responses.items():
            if needle in url:
                if isinstance(result, Exception):
                    raise result

                class _R:
                    status_code = result

                return _R()

        class _R200:
            status_code = 200

        return _R200()

    monkeypatch.setattr(httpx, "head", fake_head)
    draft = _ready_draft(db, user, job)
    app = draft_service.submit_draft(db, draft, "browser", profile,
                                     user_id=user.id)
    return app, calls


# ---------------------------------------------------------------------
# AC-1 — dead apply URL + live twin: hand off the twin, say so
# ---------------------------------------------------------------------


def test_ac_1(db, monkeypatch):
    user, profile = _actor(db)
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", url=TWIN_URL,
         application_url=None, application_email=None)

    app, _ = _submit(db, monkeypatch, user, profile, dead,
                     {"jobviewtrack.example": 404})

    assert app.status == "manual_pending", "recovery must not block the hand-off"
    # The outbound artifact: the row hands off the TWIN's link.
    assert app.apply_url == TWIN_URL, (
        f"the row must carry the live twin link, got {app.apply_url!r}"
    )
    # The application API response exposes the substitution note naming
    # the source the original posting was opened from instead.
    response = ApplicationResponse.from_orm_application(app)
    assert response.apply_url == TWIN_URL
    note = response.error or ""
    assert "jobtech" in note, (
        f"the substitution note must name the source the link came from: {note!r}"
    )
    assert "dead" in note.lower(), f"the note must say why: {note!r}"


def test_ac_1_prefers_the_twin_application_url(db, monkeypatch):
    """The twin's direct apply route outranks its posting URL — same
    precedence the original row's apply_url already uses."""
    user, profile = _actor(db)
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", url="https://acme-employer.example/jobs/dev",
         application_url="https://ats-employer.example/apply/123",
         application_email=None)

    app, calls = _submit(db, monkeypatch, user, profile, dead,
                         {"jobviewtrack.example": 404})

    assert app.apply_url == "https://ats-employer.example/apply/123"
    # the twin's OWN link is what gets re-probed before being trusted
    assert any("ats-employer.example/apply/123" in c for c in calls), (
        f"the twin must be re-probed on its own apply link; probed: {calls!r}"
    )


# ---------------------------------------------------------------------
# AC-2 — twin also dead (or unprobeable): original warning, no swap
# ---------------------------------------------------------------------


def test_ac_2(db, monkeypatch):
    user, profile = _actor(db)
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    # same title + same company -> a true pool twin, but its link 410s too
    _job(db, source="jobtech", url=TWIN_URL,
         application_url=None, application_email=None)

    app, _ = _submit(db, monkeypatch, user, profile, dead,
                     {"jobviewtrack.example": 404, "acme-employer.example": 410})

    assert app.apply_url == DEAD_URL, "a dead twin is not a recovery — keep the original link"
    assert app.error == expiry_warning(404), (
        f"the original warning must stand unchanged: {app.error!r}"
    )
    assert app.link_recovery is None, "no substitution -> no audit entry"

    # Owner decision 2026-11-17 (reversing this file's s1 rule): an
    # email-only twin IS a recovery path. It has no HTTP link, so it is
    # not probed and there is no link to substitute — the original link
    # stays and the recovery is recorded as the twin's email + source.
    # A different title keeps this scenario's pool isolated from the
    # twin the first half of this test created.
    user2, profile2 = _actor(db, label="wo22mail")
    dead2 = _job(db, title="Radiology Nurse", company="Care AB",
                 url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", title="Radiology Nurse",
         company="Care AB", url="",
         application_url=None, application_email="jobs@care.example")
    app2, _ = _submit(db, monkeypatch, user2, profile2, dead2,
                      {"jobviewtrack.example": 404})
    assert app2.apply_url == DEAD_URL, (
        "an email-only twin has no HTTP link to substitute — keep the original"
    )
    recovery2 = json.loads(app2.link_recovery)
    assert recovery2["twin_email"] == "jobs@care.example"
    assert recovery2["source"] == "jobtech"
    assert recovery2["dead_url"] == DEAD_URL
    assert "dead" in (app2.error or "").lower(), app2.error
    assert "jobtech" in app2.error and "jobs@care.example" in app2.error, (
        f"the note must name the twin's source and application email: {app2.error!r}"
    )


# ---------------------------------------------------------------------
# AC-3 — 401/403/timeout are not death: the lookup never fires
# ---------------------------------------------------------------------


def test_ac_3(db, monkeypatch):
    for label, outcome, expected in (
        ("401", 401, BOT_BLOCK_401),
        ("403", 403, BOT_BLOCK_403),
        ("timeout", httpx.ConnectTimeout("probe timed out"), None),
    ):
        user, profile = _actor(db, label=f"wo22{label}")
        dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
        # a LIVE twin is sitting right there — a lookup here would swap it in
        _job(db, source="jobtech", url=TWIN_URL,
             application_url=None, application_email=None)

        app, _ = _submit(db, monkeypatch, user, profile, dead,
                         {"jobviewtrack.example": outcome})

        assert app.apply_url == DEAD_URL, (
            f"{label} is not death — the twin lookup must never fire for it"
        )
        assert app.link_recovery is None, (
            f"{label} must never be recorded as a substitution"
        )
        assert app.error == expected, (
            f"{label} outcome text drifted: {app.error!r}"
        )


# ---------------------------------------------------------------------
# AC-4 — no twin: today's behavior, byte-identical warning
# ---------------------------------------------------------------------


def test_ac_4(db, monkeypatch):
    user, profile = _actor(db)
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    # nothing else in the pool

    app, _ = _submit(db, monkeypatch, user, profile, dead,
                     {"jobviewtrack.example": 404})

    assert app.status == "manual_pending"
    assert app.apply_url == DEAD_URL
    assert app.error == expiry_warning(404), (
        f"no twin -> the warning must be byte-identical to today's: {app.error!r}"
    )
    assert app.link_recovery is None, "no substitution happened -> nothing audited"

    response = ApplicationResponse.from_orm_application(app)
    assert response.error == expiry_warning(404)
    assert response.link_recovery is None


# ---------------------------------------------------------------------
# AC-5 — the audit lives ON the application row, no separate log table
# ---------------------------------------------------------------------


def test_ac_5(db, monkeypatch):
    user, profile = _actor(db)
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", url=TWIN_URL,
         application_url=None, application_email=None)

    app, _ = _submit(db, monkeypatch, user, profile, dead,
                     {"jobviewtrack.example": 404})

    audit = json.loads(app.link_recovery)
    assert audit["dead_url"] == DEAD_URL
    assert audit["twin_url"] == TWIN_URL
    assert audit["source"] == "jobtech"
    # a real, parseable timestamp — the "when" of the substitution
    datetime.fromisoformat(audit["recovered_at"])
    # No separate log table was invented for this: the substitution
    # record is the application row's own column.
    assert not any(
        "recover" in name or "substitut" in name for name in Base.metadata.tables
    ), (
        "substitutions are audited on applications itself — no separate "
        "log table may exist"
    )
