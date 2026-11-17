"""WO-22 follow-up — apply-time link recovery corrections (2026-11-17).

Every test maps to a contract acceptance item:

- AC-1: the dead wrapper (the `_probe_apply` + `_portal` name) is gone —
  deleted, not folded — and nothing in backend/app, backend/tests or
  backend/scripts names it; `_probe_apply_link`'s behaviour is otherwise
  unchanged.
- AC-2: the twin lookup loads full rows only for same-bucket candidates
  (an (id, title) pre-filter pass first; full JobPosting rows —
  descriptions included — only for the survivors).
- AC-3: transient statuses are not death — HTTP 408 and 429 leave the
  link in place with the warning showing and never trigger the twin
  lookup; 404/410/5xx stay the expiry class; 401/403/timeout unchanged.
- AC-4: email-only twins are usable for recovery (owner decision
  2026-11-17): no HTTP link, so no probe, ranked below any twin whose
  HTTP link passed the re-probe; the original link stays and the
  recovery is recorded as the twin's email + source.

Run: cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_wo22_followup.py -q
"""

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


# ---------------------------------------------------------------------
# AC-1 — the dead wrapper is gone; _probe_apply_link unchanged
# ---------------------------------------------------------------------


def test_ac_1(monkeypatch):
    # The wrapper was deleted, not folded: no attribute left behind.
    forbidden = "_probe_apply" + "_portal"
    assert not hasattr(draft_service, forbidden), (
        f"{forbidden} must not exist — delete it, do not keep a "
        "delegating wrapper"
    )

    # Nothing in backend/app, backend/tests or backend/scripts names it.
    import pathlib

    backend = pathlib.Path(draft_service.__file__).resolve().parents[2]
    for sub in ("app", "tests", "scripts"):
        for path in sorted((backend / sub).rglob("*.py")):
            assert forbidden not in path.read_text(encoding="utf-8"), (
                f"{path} still names the deleted wrapper"
            )

    # _probe_apply_link's behaviour is otherwise unchanged — the exact
    # (warning, dead_code) tuples, probed directly at the boundary.
    assert draft_service._probe_apply_link(None) == (None, None)
    assert draft_service._probe_apply_link("") == (None, None)

    for code, expected in ((200, (None, None)), (405, (None, None))):
        _stub_head(monkeypatch, {"jobviewtrack.example": code})
        assert draft_service._probe_apply_link(DEAD_URL) == expected, (
            f"HTTP {code} must stay silent"
        )

    _stub_head(monkeypatch, {"jobviewtrack.example": 401})
    assert draft_service._probe_apply_link(DEAD_URL) == (BOT_BLOCK_401, None)
    _stub_head(monkeypatch, {"jobviewtrack.example": 403})
    assert draft_service._probe_apply_link(DEAD_URL) == (BOT_BLOCK_403, None)

    _stub_head(monkeypatch, {"jobviewtrack.example": 404})
    assert draft_service._probe_apply_link(DEAD_URL) == (
        expiry_warning(404),
        404,
    )

    _stub_head(
        monkeypatch, {"jobviewtrack.example": httpx.ConnectTimeout("no route")}
    )
    assert draft_service._probe_apply_link(DEAD_URL) == (None, None), (
        "a timeout is probe noise, never a verdict"
    )


# ---------------------------------------------------------------------
# shared fixtures/row factories (mirrors the parent WO-22 test file)
# ---------------------------------------------------------------------


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


def _actor(db, label="wo22f"):
    import uuid

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
    import uuid

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
# AC-2 — full JobPosting rows load only for same-bucket candidates
# ---------------------------------------------------------------------


def test_ac_2(db):
    from sqlalchemy import event

    from app.models import JobPosting

    user, _profile = _actor(db)
    dead = _job(db, url=DEAD_URL, description="Acme seeks an integration developer for its Malmo team.")
    live_twin = _job(db, source="jobtech", url=LIVE_TWIN_URL,
                     description="Acme is hiring an integration developer in Malmo.")
    other_twin = _job(db, source="jobboard",
                      url="https://other.example/integration-developer",
                      description="Acme integration developer opening, Malmo office.")
    # same pool, different buckets — these must NEVER be loaded as full
    # rows: only their (id, title) is read by the pre-filter pass.
    _job(db, title="Radiology Nurse", company="Care AB",
         url="https://care.example/nurse", description="Care AB nurse ad.")
    _job(db, title="Data Scientist", company="Beta AB",
         url="https://beta.example/ds", description="Beta AB scientist ad.")
    _job(db, title="Warehouse Worker", company="Gamma AB",
         url="https://gamma.example/wh", description="Gamma AB warehouse ad.")

    # Fully load the job itself, then detach EVERYTHING: with an empty
    # identity map, every row a query returns has to be constructed
    # fresh, so the JobPosting 'load' event counts exactly the rows the
    # lookup pulled as full JobPosting rows. (Ids captured before the
    # detach — an expired detached instance has nothing to give.)
    same_bucket_ids = sorted([live_twin.id, other_twin.id])
    assert dead.description is not None
    db.expunge_all()

    loaded_ids = []

    def on_load(job, ctx):
        loaded_ids.append(job.id)

    event.listen(JobPosting, "load", on_load)
    try:
        twin = draft_service._find_live_twin(db, dead)
    finally:
        event.remove(JobPosting, "load", on_load)

    # a live twin is still found, as today
    assert twin is not None, "the live twin must still be found"
    assert twin.url == LIVE_TWIN_URL

    # only the same-bucket rows were loaded as full JobPosting rows —
    # the three non-matching rows were read as (id, title) at most.
    assert sorted(loaded_ids) == same_bucket_ids, (
        f"only same-bucket rows may be fully loaded; loaded: {len(loaded_ids)}"
    )


# ---------------------------------------------------------------------
# AC-3 — transient statuses are not death
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,outcome,expected_error,is_dead",
    [
        # the expiry class — dead, triggers the twin lookup
        ("404", 404, expiry_warning(404), True),
        ("410", 410, expiry_warning(410), True),
        ("500", 500, expiry_warning(500), True),
        ("502", 502, expiry_warning(502), True),
        ("503", 503, expiry_warning(503), True),
        # transient throttling / request timeout — their own copy (not
        # the expiry text), NOT death
        ("408", 408, (
            "Apply portal returned HTTP 408 at hand-off — the portal "
            "timed out. The link most likely still works; try opening "
            "it again."
        ), False),
        ("429", 429, (
            "Apply portal returned HTTP 429 at hand-off — the portal "
            "asked us to slow down. The link most likely still works; "
            "try opening it again in a minute."
        ), False),
        # bot-block class — unchanged (not death)
        ("401", 401, BOT_BLOCK_401, False),
        ("403", 403, BOT_BLOCK_403, False),
        # probe noise — unchanged (not death, silent)
        ("timeout", httpx.ConnectTimeout("probe timed out"), None, False),
    ],
)
def test_ac_3(db, monkeypatch, label, outcome, expected_error, is_dead):
    user, profile = _actor(db, label=f"wo22f{label}")
    dead = _job(db, url=DEAD_URL, application_url=None, application_email=None)
    # a LIVE twin is sitting right there — a lookup here would swap it in
    _job(db, source="jobtech", url=LIVE_TWIN_URL,
         application_url=None, application_email=None)

    app, _ = _submit(db, monkeypatch, user, profile, dead,
                     {"jobviewtrack.example": outcome})

    if is_dead:
        assert app.apply_url == LIVE_TWIN_URL, (
            f"HTTP {label} is the expiry class — the twin lookup must fire"
        )
        assert app.link_recovery is not None
        # the substitution note replaces the raw warning
        assert "dead" in (app.error or "").lower() and "jobtech" in app.error, (
            f"HTTP {label} substitution note drifted: {app.error!r}"
        )
    else:
        assert app.apply_url == DEAD_URL, (
            f"HTTP {label} is transient — the link stays, no twin lookup"
        )
        assert app.link_recovery is None, (
            f"HTTP {label} must never be recorded as a substitution"
        )
        assert app.error == expected_error, (
            f"HTTP {label} outcome text drifted: {app.error!r}"
        )


# ---------------------------------------------------------------------
# AC-4 — email-only twins are usable for recovery (owner decision
# 2026-11-17, per the WO-22 design doc)
# ---------------------------------------------------------------------


def test_ac_4(db, monkeypatch):
    import json

    # Scenario 1 — the email-only twin wins alone. Its only live apply
    # path is the application email; its posting URL is degraded (an
    # aggregator interstitial, not the employer's page), so it has NO
    # probeable HTTP link.
    user, profile = _actor(db, label="wo22fmail")
    dead = _job(db, title="Integration Developer", company="Acme AB",
                url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", title="Integration Developer",
         company="Acme AB", url="https://jobviewtrack.com/jobview/xyz",
         application_url=None, application_email="jobs@acme.example")

    app, calls = _submit(db, monkeypatch, user, profile, dead,
                         {"jobviewtrack.example": 404})

    # no HTTP link to hand off — the ORIGINAL link stays
    assert app.apply_url == DEAD_URL, (
        "an email-only twin has no HTTP link — the original link stays"
    )
    # it has no HTTP link, so it is NOT probed — only the original link
    # was ever visited
    assert calls == [DEAD_URL], (
        f"the email-only twin must not be probed; probed: {calls!r}"
    )
    # the recovery is recorded: twin_email and source
    audit = json.loads(app.link_recovery)
    assert audit["twin_email"] == "jobs@acme.example"
    assert audit["source"] == "jobtech"
    assert audit["dead_url"] == DEAD_URL
    # the note says the scraped link was dead and names the twin's
    # source and application email
    assert "dead" in (app.error or "").lower(), app.error
    assert "jobtech" in app.error, app.error
    assert "jobs@acme.example" in app.error, app.error

    # Scenario 2 — a twin with an HTTP link that passed the re-probe
    # ranks ABOVE the email-only twin (its behaviour is unchanged).
    user2, profile2 = _actor(db, label="wo22frank")
    dead2 = _job(db, title="Radiology Nurse", company="Care AB",
                 url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobboard", title="Radiology Nurse", company="Care AB",
         url="", application_url=None,
         application_email="jobs@board.example")
    _job(db, source="jobtech", title="Radiology Nurse", company="Care AB",
         url=LIVE_TWIN_URL, application_url=None,
         application_email="jobs@direct.example")

    app2, calls2 = _submit(db, monkeypatch, user2, profile2, dead2,
                           {"jobviewtrack.example": 404})

    assert app2.apply_url == LIVE_TWIN_URL, (
        "a twin with a live HTTP link outranks an email-only twin"
    )
    assert any("acme-employer.example" in c for c in calls2), (
        f"the HTTP twin must be re-probed; probed: {calls2!r}"
    )
    audit2 = json.loads(app2.link_recovery)
    assert audit2["twin_url"] == LIVE_TWIN_URL
    assert audit2["source"] == "jobtech"

    # Scenario 3 — the HTTP twin's link is itself dead: it is skipped
    # (re-probed, as today) and the email-only twin is the recovery.
    user3, profile3 = _actor(db, label="wo22fdead")
    dead3 = _job(db, title="Dental Hygienist", company="Delta AB",
                 url=DEAD_URL, application_url=None, application_email=None)
    _job(db, source="jobtech", title="Dental Hygienist", company="Delta AB",
         url="https://dead-twin.example/careers/dev",
         application_url=None, application_email=None)
    _job(db, source="jobboard", title="Dental Hygienist", company="Delta AB",
         url="", application_url=None,
         application_email="jobs@board.example")

    app3, _ = _submit(db, monkeypatch, user3, profile3, dead3,
                      {"jobviewtrack.example": 404, "dead-twin.example": 410})

    assert app3.apply_url == DEAD_URL
    audit3 = json.loads(app3.link_recovery)
    assert audit3["twin_email"] == "jobs@board.example"
    assert audit3["source"] == "jobboard"
    assert "dead" in (app3.error or "").lower() and "jobs@board.example" in app3.error
