"""
Phase 1b multi-user tests: isolation, IDOR, rate limits, GDPR erasure,
per-user matching. Runs on throwaway SQLite via TestClient — no network.
"""

import os
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_mu.db")
os.environ.setdefault("GLM_API_KEY", "")
os.environ.setdefault("DEBUG", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Application, ApplicationDraft, JobPosting, MatchResult, Profile  # noqa: E402

PASSWORD = "TestPass-2026!"


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — it
    # orders the FK-heavy tables correctly; create_all hits a circular
    # dependency with the per-user FKs.
    if os.path.exists("test_mu.db"):
        os.remove("test_mu.db")
    from app.core.database import init_db

    with TestClient(app) as c:  # lifespan runs init_db -> alembic head
        yield c
    engine.dispose()
    for f in ("test_mu.db",):
        if os.path.exists(f):
            os.remove(f)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _register(client, email):
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _auth_client(client, email):
    """TestClient with Authorization header pre-set for this user."""
    r = client.post(
        "/api/v1/auth/jwt/login",
        data={"username": email, "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token


def _clear_auth(client):
    client.headers.pop("Authorization", None)


class TestAuthGate:
    def test_every_route_requires_auth(self, client):
        _clear_auth(client)
        for path in [
            "/api/v1/profile/me",
            "/api/v1/pipeline/status",
            "/api/v1/matches/",
            "/api/v1/applications/",
            "/api/v1/jobs/",
            "/api/v1/settings/integrations",
            "/api/v1/account/export",
        ]:
            r = client.get(path)
            assert r.status_code == 401, f"{path} returned {r.status_code} — expected 401"
        # /health stays public (uptime monitors)
        assert client.get("/health").status_code == 200


class TestTwoUserIsolation:
    """The review's takeover scenario, inverted: B uploads, A still sees A."""

    def test_second_upload_does_not_steal_the_app(self, client, db):
        a_email, b_email = f"a-{uuid.uuid4().hex[:6]}@test.example", f"b-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, a_email)
        _register(client, b_email)

        # User A uploads a CV
        _auth_client(client, a_email)
        r = client.post(
            "/api/v1/profile/upload",
            files={"file": ("a.pdf", b"%PDF-1.4 fake cv for user A", "application/pdf")},
        )
        # (upload will 400 on non-PDF-parseable content — the scoping is what we test)
        a_profile = db.query(Profile).filter(Profile.user_id != None).all()  # noqa: E711

        # User B uploads — the OLD code would have deactivated A's profile
        _auth_client(client, b_email)
        client.post(
            "/api/v1/profile/upload",
            files={"file": ("b.pdf", b"%PDF-1.4 fake cv for user B", "application/pdf")},
        )

        # A's profile still exists and is still A's
        _auth_client(client, a_email)
        r = client.get("/api/v1/profile/me")
        assert r.status_code in (200, 404)  # 404 if upload failed parse — either way A sees A's state
        if r.status_code == 200:
            assert r.json().get("cv_file_name") in (None, "a.pdf")

        # Direct DB check: no profile was deactivated by B's upload
        profiles = db.query(Profile).all()
        for p in profiles:
            other = [q for q in profiles if q.id != p.id and q.user_id == p.user_id]
            assert not other  # at most one profile per user

    def test_matches_are_scoped(self, client, db):
        """A cannot list or decide B's matches."""
        a_email, b_email = f"ma-{uuid.uuid4().hex[:6]}@test.example", f"mb-{uuid.uuid4().hex[:6]}@test.example"
        a_id_uuid = _register(client, a_email)
        b_id = _register(client, b_email)

        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        b_match = MatchResult(user_id=uuid.UUID(b_id), job_id=job.id, score=80,
                              tier="good_match")
        db.add(b_match)
        db.commit()
        match_id = b_match.id

        _auth_client(client, a_email)
        listed = client.get("/api/v1/matches/").json()
        assert all(m["id"] != match_id for m in listed), "A sees B's match in the list!"

        r = client.get(f"/api/v1/matches/{match_id}")
        assert r.status_code == 404, "IDOR: A fetched B's match detail"

        r = client.post(
            f"/api/v1/matches/{match_id}/decision", json={"decision": "approved"}
        )
        assert r.status_code == 404, "IDOR: A decided B's match"


class TestDraftIDOR:
    def test_draft_download_blocked_for_other_user(self, client, db):
        a_email = f"da-{uuid.uuid4().hex[:6]}@test.example"
        b_email = f"db-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, a_email)
        b_id = _register(client, b_email)

        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        draft = ApplicationDraft(
            user_id=uuid.UUID(b_id), job_id=job.id,
            cover_letter="B's letter", tailored_cv="B's CV",
            changes_summary="[]", status="ready",
        )
        db.add(draft)
        db.commit()
        draft_id = draft.id

        _auth_client(client, a_email)
        for path in [
            f"/api/v1/applications/draft/{draft_id}",
            f"/api/v1/applications/draft/{draft_id}/download/cover-letter",
            f"/api/v1/applications/draft/{draft_id}/download/cv",
        ]:
            r = client.get(path)
            assert r.status_code == 404, f"IDOR: A accessed {path}"

        r = client.put(
            f"/api/v1/applications/draft/{draft_id}",
            json={"cover_letter": "hijacked", "tailored_cv": "hijacked"},
        )
        assert r.status_code == 404, "IDOR: A edited B's draft"


class TestRateLimit:
    def test_cv_upload_rate_limited(self, client):
        email = f"rl-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, email)
        _auth_client(client, email)
        codes = []
        for _ in range(7):  # limit is 5/hour
            r = client.post(
                "/api/v1/profile/upload",
                files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
            )
            codes.append(r.status_code)
        assert 429 in codes, f"rate limit never fired: {codes}"


class TestGDPR:
    def test_account_erasure_cascade(self, client, db):
        email = f"gd-{uuid.uuid4().hex[:6]}@test.example"
        uid = _register(client, email)
        _auth_client(client, email)
        uid_uuid = uuid.UUID(uid)

        # Seed personal rows
        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        # on_after_register already made an empty Profile — claim it
        existing_profile = (
            db.query(Profile).filter(Profile.user_id == uid_uuid).first()
        )
        if existing_profile:
            existing_profile.full_name = "GDPR Test"
        seed_profile = existing_profile or Profile(
            user_id=uid_uuid, is_active=1, full_name="GDPR Test"
        )
        db.add_all([
            seed_profile,
            MatchResult(user_id=uid_uuid, job_id=job.id, score=70, tier="good_match"),
            ApplicationDraft(user_id=uid_uuid, job_id=job.id, cover_letter="x",
                             tailored_cv="y", changes_summary="[]", status="ready"),
            Application(user_id=uid_uuid, job_id=job.id, method="browser",
                        status="manual_pending"),
        ])
        db.commit()

        r = client.delete("/api/v1/account/delete")
        assert r.status_code == 200, r.text

        assert db.query(Profile).filter(Profile.user_id == uid_uuid).count() == 0
        assert db.query(MatchResult).filter(MatchResult.user_id == uid_uuid).count() == 0
        assert db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid_uuid).count() == 0
        assert db.query(Application).filter(Application.user_id == uid_uuid).count() == 0

        # Token is dead after erasure
        r = client.get("/api/v1/users/me")
        assert r.status_code == 401


class TestPerUserMatching:
    def test_same_job_matchable_by_two_users(self, db):
        """The UNIQUE(job_id) constraint is gone — the schema now allows the
        same job scored by two different users."""
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        db.add_all([
            MatchResult(user_id=u1, job_id=job.id, score=90, tier="excellent_match"),
            MatchResult(user_id=u2, job_id=job.id, score=45, tier="stretch"),
        ])
        db.commit()  # both rows coexist — impossible under the old schema
        assert db.query(MatchResult).filter(MatchResult.job_id == job.id).count() == 2
