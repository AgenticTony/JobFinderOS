"""WO-22 follow-up 2 — honest 408/429 copy, one recovery-record builder,
blank-email twins (2026-11-17).

Every test maps to a contract acceptance item:

- AC-1: transient statuses get their own copy, not the expiry text —
  HTTP 408 and 429 say the link most likely still works (they never say
  'expired'), dead_code stays None, the link stays, no twin lookup
  fires; 404/410/5xx keep today's expiry warning word for word; 409 and
  every other 4xx keep today's expiry warning with dead_code None.
- AC-2: ONE builder for the recovery record — both twin branches of
  submit_draft call _link_recovery_record, and its note + stored JSON
  are byte-identical to today's (apart from recovered_at).
- AC-3: a blank (empty or whitespace-only) application_email is NOT an
  email path — the email-only twin pass ignores it, so the hand-off
  falls back to the plain expiry warning with an empty link_recovery; a
  twin whose email has surrounding whitespace is still used, stripped.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_wo22_followup2.py -q
"""

import json
import uuid

import httpx
import pytest

from app.services import draft_service

DEAD_URL = "https://jobviewtrack.example/dead-apply"
LIVE_TWIN_URL = "https://acme-employer.example/careers/integration-developer"

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

# AC-1: the transient copies, exact.
TRANSIENT_408 = (
    "Apply portal returned HTTP 408 at hand-off — the portal timed out. "
    "The link most likely still works; try opening it again."
)
TRANSIENT_429 = (
    "Apply portal returned HTTP 429 at hand-off — the portal asked us to "
    "slow down. The link most likely still works; try opening it again "
    "in a minute."
)


def expiry_warning(code: int) -> str:
    return (
        f"Apply portal returned HTTP {code} at hand-off — "
        "this posting may have expired. Check the page loads before applying."
    )


# ---------------------------------------------------------------------
# probe boundary stub (the one external call the probe makes)
# ---------------------------------------------------------------------


def _stub_head(monkeypatch, outcomes):
    """Stub httpx.head: {url-fragment: status code | Exception};
    unmatched URLs answer 200. Returns the URLs the probe visited."""
    calls = []

    def fake_head(url, **kwargs):
        calls.append(url)
        for needle, result in outcomes.items():
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
    return calls


@pytest.fixture()
def db():
    from app.core.database import SessionLocal, engine
    from app.core.orm import Base
    from tests.conftest import stamp_alembic_head

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    stamp_alembic_head()
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()
    engine.dispose()


def _actor(db, label="wo22f2"):
    from app.models import Profile, User

    user = User(id=uuid.uuid4(),
                email=f"{label}-{uuid.uuid4().hex[:6]}@test.example",
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
    from app.models import JobPosting

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
    from app.models import ApplicationDraft

    draft = ApplicationDraft(
        user_id=user.id, job_id=job.id, status="ready",
        cover_letter="Dear Acme", tailored_cv="TAILORED CV",
        changes_summary="[]",
    )
    db.add(draft)
    db.commit()
    return draft


def _submit(db, monkeypatch, user, profile, job, responses):
    """Submit via the browser path with the probe boundary stubbed:
    {url-fragment: status | Exception}; unmatched URLs answer 200."""
    calls = _stub_head(monkeypatch, responses)
    draft = _ready_draft(db, user, job)
    app = draft_service.submit_draft(db, draft, "browser", profile,
                                     user_id=user.id)
    return app, calls


# ---------------------------------------------------------------------
# AC-1 — transient statuses get their own copy, not the expiry text
# ---------------------------------------------------------------------


def test_ac_1(db, monkeypatch):
    # At the probe boundary: the exact transient copies, dead_code None.
    _stub_head(monkeypatch, {"jobviewtrack.example": 408})
    assert draft_service._probe_apply_link(DEAD_URL) == (TRANSIENT_408, None)
    _stub_head(monkeypatch, {"jobviewtrack.example": 429})
    assert draft_service._probe_apply_link(DEAD_URL) == (TRANSIENT_429, None)

    # Neither copy says 'expired'.
    assert "expired" not in TRANSIENT_408.lower()
    assert "expired" not in TRANSIENT_429.lower()

    # The expiry class is untouched, word for word.
    for code in (404, 410, 500, 502, 503):
        _stub_head(monkeypatch, {"jobviewtrack.example": code})
        assert draft_service._probe_apply_link(DEAD_URL) == (
            expiry_warning(code),
            code,
        ), f"HTTP {code} expiry text drifted"

    # A 409 — neither transient nor definite — keeps today's expiry
    # warning word for word, with dead_code None.
    _stub_head(monkeypatch, {"jobviewtrack.example": 409})
    assert draft_service._probe_apply_link(DEAD_URL) == (expiry_warning(409), None)

    # 401/403 keep their bot-block copy; 405 stays silent.
    _stub_head(monkeypatch, {"jobviewtrack.example": 401})
    assert draft_service._probe_apply_link(DEAD_URL) == (BOT_BLOCK_401, None)
    _stub_head(monkeypatch, {"jobviewtrack.example": 403})
    assert draft_service._probe_apply_link(DEAD_URL) == (BOT_BLOCK_403, None)
    _stub_head(monkeypatch, {"jobviewtrack.example": 405})
    assert draft_service._probe_apply_link(DEAD_URL) == (None, None)

    # At hand-off: 408 and 429 leave the link in place, show their own
    # copy exactly, record nothing, and never fire the twin lookup —
    # a live twin is sitting right there and must NOT be swapped in.
    for label, outcome, expected in (
        ("408", 408, TRANSIENT_408),
        ("429", 429, TRANSIENT_429),
    ):
        user, profile = _actor(db, label=f"wo22f2{label}")
        dead = _job(db, url=DEAD_URL, application_url=None,
                    application_email=None)
        _job(db, source="jobtech", url=LIVE_TWIN_URL,
             application_url=None, application_email=None)

        app, _ = _submit(db, monkeypatch, user, profile, dead,
                         {"jobviewtrack.example": outcome})

        assert app.apply_url == DEAD_URL, (
            f"HTTP {label} is transient — the link stays, no twin lookup"
        )
        assert app.link_recovery is None, (
            f"HTTP {label} must never be recorded as a substitution"
        )
        assert app.error == expected, (
            f"HTTP {label} hand-off copy drifted: {app.error!r}"
        )


# ---------------------------------------------------------------------
# AC-2 — ONE builder for the recovery record, called by both branches
# ---------------------------------------------------------------------


def _spy_on_builder(monkeypatch):
    """Wrap _link_recovery_record with a spy that records each call and
    then delegates to the real, un-mocked builder."""
    real = draft_service._link_recovery_record
    calls = []

    def spy(dead_url, code, source, twin_key, twin_value, note_tail):
        calls.append({
            "dead_url": dead_url,
            "code": code,
            "source": source,
            "twin_key": twin_key,
            "twin_value": twin_value,
            "note_tail": note_tail,
        })
        return real(dead_url, code, source, twin_key, twin_value, note_tail)

    monkeypatch.setattr(draft_service, "_link_recovery_record", spy)
    return calls


def test_ac_2(db, monkeypatch):
    calls = _spy_on_builder(monkeypatch)

    # Path 1 — the URL-twin branch.
    user, profile = _actor(db, label="wo22f2url")
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", url=LIVE_TWIN_URL,
         application_url=None, application_email=None)

    app, _ = _submit(db, monkeypatch, user, profile, dead,
                     {"jobviewtrack.example": 404})

    assert len(calls) == 1, f"the URL branch must call the builder once: {calls!r}"
    expected_note = (
        "The scraped apply link was dead (HTTP 404) — opened the original "
        "posting from jobtech instead."
    )
    assert app.error == expected_note
    audit = json.loads(app.link_recovery)
    assert audit == {
        "dead_url": DEAD_URL,
        "twin_url": LIVE_TWIN_URL,
        "source": "jobtech",
        "note": expected_note,
        "recovered_at": audit["recovered_at"],
    }
    assert list(audit.keys()) == [
        "dead_url", "twin_url", "source", "recovered_at", "note",
    ], f"the stored key order drifted: {list(audit.keys())}"
    assert calls[0] == {
        "dead_url": DEAD_URL,
        "code": 404,
        "source": "jobtech",
        "twin_key": "twin_url",
        "twin_value": LIVE_TWIN_URL,
        "note_tail": "opened the original posting from jobtech instead.",
    }
    # the builder's note is what the row and the record both carry
    assert audit["note"] == app.error

    # Path 2 — the email-only-twin branch.
    user2, profile2 = _actor(db, label="wo22f2mail")
    dead2 = _job(db, title="Radiology Nurse", company="Care AB",
                 url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobboard", title="Radiology Nurse",
         company="Care AB", url="",
         application_url=None, application_email="jobs@care.example")

    app2, _ = _submit(db, monkeypatch, user2, profile2, dead2,
                      {"jobviewtrack.example": 410})

    assert len(calls) == 2, f"the email branch must call the builder too: {calls!r}"
    expected_note2 = (
        "The scraped apply link was dead (HTTP 410) — the original posting "
        "from jobboard takes applications by email: jobs@care.example."
    )
    assert app2.error == expected_note2
    audit2 = json.loads(app2.link_recovery)
    assert audit2 == {
        "dead_url": DEAD_URL,
        "twin_email": "jobs@care.example",
        "source": "jobboard",
        "note": expected_note2,
        "recovered_at": audit2["recovered_at"],
    }
    assert list(audit2.keys()) == [
        "dead_url", "twin_email", "source", "recovered_at", "note",
    ], f"the stored key order drifted: {list(audit2.keys())}"
    assert calls[1] == {
        "dead_url": DEAD_URL,
        "code": 410,
        "source": "jobboard",
        "twin_key": "twin_email",
        "twin_value": "jobs@care.example",
        "note_tail": (
            "the original posting from jobboard takes applications by "
            "email: jobs@care.example."
        ),
    }
    assert audit2["note"] == app2.error


# ---------------------------------------------------------------------
# AC-3 — a blank application_email is not an email path
# ---------------------------------------------------------------------


def test_ac_3(db, monkeypatch):
    # Scenario 1 — the only twin in the pool has a BLANK application
    # email: it is not an email path. It is never handed off and never
    # recorded; with no other twin the hand-off falls back to the plain
    # expiry warning and link_recovery stays empty.
    user, profile = _actor(db, label="wo22f2blank")
    dead = _job(db, title="Integration Developer", company="Acme AB",
                url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", title="Integration Developer",
         company="Acme AB", url="",
         application_url=None, application_email="   ")

    app, calls = _submit(db, monkeypatch, user, profile, dead,
                         {"jobviewtrack.example": 404})

    assert app.apply_url == DEAD_URL, (
        "a blank-email twin must not be handed off — the original link stays"
    )
    assert app.error == expiry_warning(404), (
        f"no usable twin -> the plain expiry warning stands: {app.error!r}"
    )
    assert app.link_recovery is None, (
        "a blank-email twin must never be recorded as a recovery"
    )
    assert calls == [DEAD_URL], (
        f"nothing else to probe; probed: {calls!r}"
    )

    # Scenario 2 — a twin whose email has surrounding whitespace IS
    # used, and the note and link_recovery carry the STRIPPED address.
    user2, profile2 = _actor(db, label="wo22f2ws")
    dead2 = _job(db, title="Radiology Nurse", company="Care AB",
                 url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobboard", title="Radiology Nurse",
         company="Care AB", url="",
         application_url=None, application_email="  jobs@care.example  ")

    app2, _ = _submit(db, monkeypatch, user2, profile2, dead2,
                      {"jobviewtrack.example": 404})

    assert app2.apply_url == DEAD_URL
    assert app2.error is not None and "jobs@care.example" in app2.error, (
        f"the note must name the stripped application email: {app2.error!r}"
    )
    assert "jobs@care.example  " not in app2.error, (
        f"the note must carry the stripped address: {app2.error!r}"
    )
    audit = json.loads(app2.link_recovery)
    assert audit["twin_email"] == "jobs@care.example", (
        f"the record must carry the stripped address: {audit!r}"
    )
