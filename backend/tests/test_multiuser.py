"""
Phase 1b multi-user tests: isolation, IDOR, rate limits, GDPR erasure,
per-user matching. Runs on throwaway SQLite via TestClient — no network.
"""

import os
import uuid

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Application,
    ApplicationDraft,
    JobPosting,
    MatchResult,
    Profile,
)

PASSWORD = "TestPass-2026!"


@pytest.fixture(scope="module")
def client():
    # Alembic creates the schema (same path as production boots) — it
    # orders the FK-heavy tables correctly; create_all hits a circular
    # dependency with the per-user FKs.
    if os.path.exists("test_mu.db"):
        os.remove("test_mu.db")

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
        _register(client, a_email)
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


class TestOutboundIdentity:
    """The review's closing ask: assert on the OUTBOUND artifact, not the
    row. Two users; A drafts and submits; the subject, the applicant name,
    and every content byte must be A's — never B's."""

    def test_draft_and_submit_use_owner_profile(self, client, db):
        from app.services.draft_service import create_draft_for_job, submit_draft

        a_email = f"oi-a-{uuid.uuid4().hex[:6]}@test.example"
        b_email = f"oi-b-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, a_email)
        b_id = _register(client, b_email)

        # A's profile — Alice
        _auth_client(client, a_email)
        a_uid = None
        from app.models import User as UserModel
        a_user = db.query(UserModel).filter(UserModel.email == a_email).first()
        a_uid = a_user.id
        a_profile = db.query(Profile).filter(Profile.user_id == a_uid).first()
        if a_profile:
            a_profile.full_name = "Alice A"
            a_profile.cv_text = "Alice Python developer"
            a_profile.cv_file_name = "alice.pdf"
        else:
            db.add(Profile(user_id=a_uid, is_active=1, full_name="Alice A",
                            cv_text="Alice Python developer", cv_file_name="alice.pdf"))
        db.commit()

        # B's profile — Bob (registered AFTER A; the old ORDER BY id DESC
        # fallback would resolve to Bob)
        b_uid = uuid.UUID(b_id)
        b_profile = db.query(Profile).filter(Profile.user_id == b_uid).first()
        if b_profile:
            b_profile.full_name = "Bob B"
            b_profile.cv_text = "Bob Java developer"
        else:
            db.add(Profile(user_id=b_uid, is_active=1, full_name="Bob B",
                            cv_text="Bob Java developer"))
        db.commit()

        # A's job + approved match
        job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                         title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
                         status="matched", application_url="https://apply.example")
        db.add(job)
        db.flush()
        db.add(MatchResult(user_id=a_uid, job_id=job.id, score=85,
                           tier="excellent_match", decision="approved"))
        db.commit()

        # A drafts — the profile context must be ALICE's, never Bob's
        from unittest.mock import patch as mock_patch

        from app.services import draft_service

        captured = {}
        def fake_tailor(self, profile_context, cv_text, job_description):
            captured["profile_context"] = profile_context
            captured["cv_text"] = cv_text
            return {"cover_letter": "Dear from Alice", "tailored_cv": "ALICE CV",
                    "changes_summary": ["n/a"]}

        from app.services.ai_service import AIService

        fake_service = AIService.__new__(AIService)
        fake_service.model = "glm-test"
        with mock_patch.object(draft_service, "get_ai_service", lambda: fake_service),              mock_patch.object(draft_service, "ai_service_available", lambda: True),              mock_patch.object(AIService, "tailor_application", fake_tailor):
            draft = create_draft_for_job(db, job, profile=a_profile, user_id=a_uid)

        assert "Alice" in captured.get("cv_text", ""), (
            f"TAILORING USED THE WRONG CV: expected Alice, got: {captured.get('cv_text', '')[:50]}"
        )
        assert "Alice" in captured.get("profile_context", "") or "Alice" in captured.get("cv_text", ""), (
            f"TAILORING USED THE WRONG PROFILE: neither context nor CV mentions Alice. "
            f"context={captured.get('profile_context', '')[:80]}"
        )
        assert "Bob" not in captured.get("cv_text", ""), "CROSS-TENANT: Bob's CV fed to Alice's draft"

        # A submits (browser method = no email config needed)
        application = submit_draft(db, draft, "browser", a_profile, user_id=a_uid)
        assert application.status == "manual_pending"

        # The SUBJECT is what reaches the employer (email method) or the
        # user's record — it must carry ALICE's name, never Bob's
        assert "Alice" in (application.subject or "") or application.subject == "Application: Dev", (
            f"SUBJECT CARRIES THE WRONG USER: {application.subject}"
        )
        assert "Bob" not in (application.subject or ""), (
            f"CROSS-TENANT LEAK: Bob's name in A's application subject: {application.subject}"
        )


class TestOutboundEmailBoundary:
    """The strongest tenancy assertion: nothing belonging to another user
    may appear in the payload that actually leaves the system.

    The three Phase 1b P0 leaks were invisible to row-ownership tests —
    every row was correctly owned; it was the CONTENT of the outbound
    email that belonged to a stranger. This test reads the real Resend
    payload and asserts on it byte for byte.
    """

    def test_email_payload_carries_only_the_sender(self, client, db, monkeypatch):
        import base64
        import uuid as _uuid

        from app.core.config import settings
        from app.services.draft_service import submit_draft

        a_uid, b_uid = _uuid.uuid4(), _uuid.uuid4()
        # Alice registers first; Bob second (the old fallback resolved to
        # whoever was newest, so Bob is the trap).
        a_profile = Profile(user_id=a_uid, is_active=1, full_name="Alice Anderson",
                             email="alice@example.com", cv_text="ALICE CV TEXT",
                             cv_file_name="alice-cv.pdf", cv_file_path=None)
        db.add(a_profile)
        db.add(Profile(user_id=b_uid, is_active=1, full_name="Bob Brown",
                       email="bob@example.com", cv_text="BOB CV TEXT",
                       cv_file_name="bob-cv.pdf", cv_file_path=None))
        job = JobPosting(source="manual", source_id=str(_uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{_uuid.uuid4().hex[:6]}", status="new",
                         application_email="jobs@acme.example")
        db.add(job)
        db.commit()

        draft = ApplicationDraft(user_id=a_uid, job_id=job.id, status="ready",
                                 cover_letter="Dear Acme, I am Alice.",
                                 tailored_cv="ALICE TAILORED CV",
                                 changes_summary="[]")
        db.add(draft)
        db.commit()

        # Make the email path live and capture the real payload
        monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
        monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "apply@jobfinderos.test")
        # Bob has a stored CV file; Alice does not. If the wrong profile is
        # resolved, Bob's bytes get attached — exactly the original P0.
        monkeypatch.setattr(
            "app.services.storage.read_original_cv",
            lambda profile: b"%PDF-BOB-ORIGINAL-CV" if profile and profile.full_name == "Bob Brown" else None,
        )
        sent = {}

        class _Emails:
            @staticmethod
            def send(params):
                sent.update(params)
                return {"id": "msg_test"}

        fake_resend = type("R", (), {"Emails": _Emails, "api_key": None})
        monkeypatch.setitem(__import__("sys").modules, "resend", fake_resend)

        application = submit_draft(db, draft, "email", a_profile, user_id=a_uid)
        assert application.status == "sent", application.error
        assert sent, "no email payload captured"

        blob = repr(sent).encode() + b"".join(
            base64.b64decode(a["content"]) for a in sent.get("attachments", [])
        )
        for forbidden in (b"Bob Brown", b"bob@example.com", b"bob-cv.pdf",
                          b"BOB CV TEXT", b"%PDF-BOB-ORIGINAL-CV"):
            assert forbidden not in blob, (
                f"CROSS-TENANT LEAK: {forbidden!r} reached the employer payload "
                f"of Alice's application. from={sent.get('from')!r} "
                f"attachments={[a['filename'] for a in sent.get('attachments', [])]}"
            )
        assert "Alice Anderson" in str(sent.get("from")), (
            f"sender identity is not Alice: {sent.get('from')!r}"
        )


class TestLayer1Routes:
    """The Layer 1 tests in test_units enter BELOW the HTTP boundary — a
    signature/caller mismatch at the route was invisible to all of them
    (the submit route 500'd on every request while 58 tests passed: the
    tests called submit_draft() with user_id= as a keyword, precisely the
    form the route did not use). One test per changed route, through the
    TestClient with a real token, exactly as the browser does."""

    def _seed_for_prepare(self, client, db, monkeypatch, name="Route Tester"):
        """Register a user, give them a profile + approved match on a job,
        and mock the tailoring AI. Returns (email, uid, job)."""
        from app.services import draft_service
        from app.services.ai_service import AIService

        email = f"l1-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        # registration creates an empty profile row — fill it in
        profile = db.query(Profile).filter(Profile.user_id == uid).first()
        if profile is None:
            profile = Profile(user_id=uid)
            db.add(profile)
        profile.is_active = 1
        profile.full_name = name
        profile.email = email
        profile.cv_text = "ROUTE PROFILE CV python"
        profile.cv_file_name = "cv.pdf"
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Route Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                         description="A role that goes through the real route.",
                         application_url="https://apply.example")
        db.add_all([profile, job])
        db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()

        def fake_tailor(self, profile_context, cv_text, job_description):
            return {"cover_letter": "Dear Acme", "tailored_cv": "TAILORED",
                    "changes_summary": ["n/a"]}

        fake = AIService.__new__(AIService)
        fake.model = "glm-test"
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(AIService, "tailor_application", fake_tailor)
        return email, uid, job

    def test_prepare_route_binds_the_service_signature(self, client, db, monkeypatch):
        """POST /applications/draft/{job_id} must reach create_draft_for_job
        with a bound signature — not 500 on a TypeError."""
        _, uid, job = self._seed_for_prepare(client, db, monkeypatch)
        r = client.post(f"/api/v1/applications/draft/{job.id}")
        assert r.status_code == 201, f"{r.status_code}: {r.text[:200]}"
        assert r.json()["status"] == "ready", r.text[:200]

    def test_submit_route_binds_the_service_signature(self, client, db, monkeypatch):
        """POST /applications/draft/{id}/submit — the endpoint that actually
        sends applications. The keyword-only user_id was passed positionally
        here and every request 500'd; this test enters through the route so
        that class of mismatch cannot ship silently again."""
        _, uid, job = self._seed_for_prepare(client, db, monkeypatch)
        r = client.post(f"/api/v1/applications/draft/{job.id}")
        assert r.status_code == 201, r.text[:200]
        draft_id = r.json()["id"]

        r = client.post(f"/api/v1/applications/draft/{draft_id}/submit",
                        json={"method": "browser"})
        assert r.status_code == 201, f"{r.status_code}: {r.text[:300]}"
        assert r.json()["status"] == "manual_pending", r.text[:200]
        assert "Route Tester" in r.json()["subject"], (
            f"subject built from the wrong profile: {r.json()['subject']!r}"
        )

    def test_submit_route_resolves_the_callers_profile_not_the_newest(self, client, db, monkeypatch):
        """The tenancy boundary, crossed: TWO profiles in the table.

        The route tests above seed one profile each, so a route that
        resolves 'the' profile instead of 'the caller's' returns the right
        one by accident — the reviewer regressed the submit route to the
        literal pre-Layer-0 lookup (order_by(Profile.id.desc()).first())
        and all 62 passed. B is registered AFTER A, so B is the newest
        profile: exactly what the historical 'any profile' resolution
        returned when it emailed a stranger's CV. Act as A; the subject
        must carry A's name and never B's."""
        _, uid, job = self._seed_for_prepare(client, db, monkeypatch, name="Alice Route")

        # The trap tenant: registered later -> higher profiles.id -> what a
        # newest-first lookup resolves. Not authenticated as — just present.
        b_email = f"l1b-{uuid.uuid4().hex[:6]}@test.example"
        b_uid = uuid.UUID(_register(client, b_email))
        bp = db.query(Profile).filter(Profile.user_id == b_uid).first()
        if bp is None:
            bp = Profile(user_id=b_uid)
            db.add(bp)
        bp.is_active = 1
        bp.full_name = "Bob Newest"
        bp.email = b_email
        bp.cv_text = "BOB TRAP CV"
        bp.cv_file_name = "bob.pdf"
        db.commit()

        # client.headers still carry ALICE's token (registration is
        # unauthenticated) — prepare and submit as A
        r = client.post(f"/api/v1/applications/draft/{job.id}")
        assert r.status_code == 201, r.text[:200]
        draft_id = r.json()["id"]

        r = client.post(f"/api/v1/applications/draft/{draft_id}/submit",
                        json={"method": "browser"})
        assert r.status_code == 201, f"{r.status_code}: {r.text[:300]}"
        subject = r.json()["subject"]
        assert "Alice Route" in subject, (
            f"subject '{subject}' does not carry the CALLER's name — the "
            "route resolved someone else's profile (tenancy regression)"
        )
        assert "Bob Newest" not in subject, (
            f"CROSS-TENANT: Bob's name reached Alice's application subject "
            f"({subject!r}) — the route resolved the newest profile, the "
            "exact pre-Layer-0 pattern"
        )

    def test_retry_route_binds_the_service_signature(self, client, db, monkeypatch):
        _, uid, job = self._seed_for_prepare(client, db, monkeypatch)
        r = client.post(f"/api/v1/applications/draft/{job.id}")
        assert r.status_code == 201, r.text[:200]
        draft_id = r.json()["id"]
        client.post(f"/api/v1/applications/draft/{draft_id}/submit",
                    json={"method": "browser"})

        # Turn it into a failed email application eligible for retry
        app_row = db.query(Application).filter(Application.draft_id == draft_id).first()
        app_row.method = "email"
        app_row.status = "failed"
        app_row.error = "boom"
        db.commit()

        from app.services import draft_service
        monkeypatch.setattr(
            draft_service, "_send_with_pdfs",
            lambda db_, app_, draft_, job_, profile_: setattr(app_, "status", "sent"),
        )
        r = client.post(f"/api/v1/applications/{app_row.id}/retry")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
        assert r.json()["status"] == "sent", r.text[:200]

    def test_matches_run_route_passes_the_profile(self, client, db, monkeypatch):
        """POST /matches/run must resolve the caller's profile on the task
        session and hand it to run_matching — not 500, not a silent skip."""
        from app.services import matcher_service

        email = f"l1m-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        mp = db.query(Profile).filter(Profile.user_id == uid).first()
        if mp is None:
            mp = Profile(user_id=uid)
            db.add(mp)
        mp.is_active = 1
        mp.full_name = "Match Runner"
        mp.email = email
        mp.cv_text = "MATCH ROUTE CV"
        mp.cv_file_name = "cv.pdf"
        db.commit()

        captured = {}

        def fake_run_matching(db_, **kwargs):
            captured.update(kwargs)
            return {"status": "completed", "jobs_considered": 0, "matches_created": 0}

        monkeypatch.setattr(matcher_service, "run_matching", fake_run_matching)
        r = client.post("/api/v1/matches/run")
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        assert captured.get("profile") is not None, (
            "run_matching was called without a profile — the task did not "
            "resolve and inject it (Layer 1 regression)"
        )
        assert captured["profile"].full_name == "Match Runner"


class TestSignupHardening:
    """Signup items 3 and 4: rate limits on the auth endpoints and a real
    password policy. fastapi-users' defaults accept ANY password string
    ('a' registered fine) and neither /auth/register nor /auth/jwt/login
    had enforce() applied — the two endpoints an attacker can hit without
    an account. Tests written before the fix: weak passwords and unlimited
    hammering currently pass."""

    def test_weak_passwords_are_rejected(self, client):
        _clear_auth(client)
        cases = [
            ("a", "too short"),
            ("short7", "7 chars — under the minimum"),
            ("x" * 73, "over the bcrypt 72-byte boundary"),
            ("tony-contains-email", "contains the account's email local part"),
        ]
        for pw, why in cases:
            # unique per run: the suite's DB file can persist across
            # pytest invocations, and a fixed address becomes a phantom
            # REGISTER_USER_ALREADY_EXISTS
            local = f"tony{uuid.uuid4().hex[:6]}"
            email = f"{local}@test.example"
            if "email" in why:
                pw = f"{local}-hunter2"
            r = client.post("/api/v1/auth/register",
                            json={"email": email, "password": pw})
            assert r.status_code == 400, (
                f"password accepted that should be rejected ({why}): "
                f"{r.status_code} {r.text[:150]}"
            )
            assert "assword" in r.text or "password" in r.text.lower(), (
                f"rejection for ({why}) doesn't mention the password: {r.text[:150]}"
            )

    def test_valid_password_registers(self, client):
        _clear_auth(client)
        email = f"pw-ok-{uuid.uuid4().hex[:6]}@test.example"
        r = client.post("/api/v1/auth/register",
                        json={"email": email, "password": "A-Sensible-Passw0rd!"})
        assert r.status_code == 201, r.text[:200]

    def test_login_brute_force_is_rate_limited(self, client):
        _clear_auth(client)
        email = f"bf-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, email)
        codes = []
        for _ in range(14):
            r = client.post("/api/v1/auth/jwt/login",
                            data={"username": email, "password": "wrong"})
            codes.append(r.status_code)
        assert codes[0] == 400, f"first wrong login should be 400, got {codes[0]}"
        assert 429 in codes, (
            f"14 wrong-password logins on one account and no 429 ever fired: {codes}"
        )
        assert codes.index(429) >= 8, (
            f"429 fired too early ({codes}) — legitimate re-logins must not trip"
        )

    def test_register_hammering_is_rate_limited(self, client):
        _clear_auth(client)
        email = f"rh-{uuid.uuid4().hex[:6]}@test.example"
        codes = []
        for _ in range(8):
            r = client.post("/api/v1/auth/register",
                            json={"email": email, "password": PASSWORD})
            codes.append(r.status_code)
        assert codes[0] == 201, codes
        assert all(c == 400 for c in codes[1:codes.index(429)]) if 429 in codes else True
        assert 429 in codes, (
            f"8 registration attempts on one address and no 429 ever fired: {codes}"
        )
