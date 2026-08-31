"""
Phase 1b multi-user tests: isolation, IDOR, rate limits, GDPR erasure,
per-user matching. Runs on throwaway SQLite via TestClient — no network.
"""

import os
import uuid
from pathlib import Path

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AIUsage,
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


class TestHuntTopMatchesScoped:
    """P0-1 (beta review): the hunt's top_matches query filtered only
    decision IS NULL + job.status == 'matched' — the shared status flag
    any user's matcher sets — so ANY user's Hunt returned the top-10
    GLOBALLY-ranked pending matches, including other users' CV-derived AI
    output (reasoning, matched_skills) via MatchWithJobResponse.

    Two users with distinct pending matches; A presses Hunt through the
    real route (scraping stubbed, matching off — the leak is in the read).
    B's matches score HIGHER than A's, so under the global ranking they
    come first: exactly the live repro where Alice's hunt returned 10/10
    of Bob's matches."""

    def test_hunt_returns_only_the_callers_matches(self, client, db, monkeypatch):
        from types import SimpleNamespace

        a_email = f"h1a-{uuid.uuid4().hex[:6]}@test.example"
        b_email = f"h1b-{uuid.uuid4().hex[:6]}@test.example"
        a_uid = uuid.UUID(_register(client, a_email))
        b_uid = uuid.UUID(_register(client, b_email))

        def _seed_matches(owner, tag, scores):
            """One pending match per score on its own 'matched' job —
            job.status is the SHARED flag, so B's matcher activity makes
            A's jobs 'matched' too; that shared state is the trap."""
            ids = []
            for n, score in enumerate(scores):
                job = JobPosting(
                    source="manual", source_id=uuid.uuid4().hex[:8],
                    title=f"Dev {tag} {n}", company="X",
                    url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                )
                db.add(job)
                db.flush()
                m = MatchResult(
                    user_id=owner, job_id=job.id, score=score, tier="good_match",
                    reasoning=f"{tag}-HUNT-REASONING-{n}",
                    matched_skills='["' + tag.lower() + '-secret-skill"]',
                )
                db.add(m)
                db.flush()
                ids.append(m.id)
            return ids

        # B's pending matches outrank A's globally: 91-93 vs 55-60
        b_ids = _seed_matches(b_uid, "BOB", [93, 92, 91])
        a_ids = _seed_matches(a_uid, "ALICE", [60, 55])
        db.commit()

        # Hunt through the real route as A — scraping stubbed to a skipped
        # run (no network), matching off (no AI spend)
        _auth_client(client, a_email)
        monkeypatch.setattr(
            "app.services.pipeline.scrape_source",
            lambda db_, source, ctx=None: SimpleNamespace(
                source=source, status="skipped", jobs_found=0, jobs_new=0,
                error=None,
            ),
        )
        r = client.post(
            "/api/v1/pipeline/run", json={"sources": ["arbeitnow"], "match": False}
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"

        payload = r.json()
        got_ids = [m["id"] for m in payload["top_matches"]]
        leaked = sorted(set(got_ids) & set(b_ids))
        assert not leaked, (
            f"CROSS-USER LEAK (P0-1): A's hunt returned B's match ids {leaked} "
            f"(full response ids: {got_ids}) — the top_matches query is not "
            "scoped to the requesting user"
        )
        assert set(got_ids) == set(a_ids), (
            f"A's hunt must return exactly A's pending matches {sorted(a_ids)}, "
            f"got {got_ids}"
        )
        # The AI output derived from B's CV must not appear anywhere in
        # A's response payload — not as a row, not as content bytes
        blob = r.text
        for forbidden in ("BOB-HUNT-REASONING", "bob-secret-skill"):
            assert forbidden not in blob, (
                f"CROSS-USER LEAK (P0-1): B's CV-derived AI output {forbidden!r} "
                "appears in A's hunt response payload"
            )
        # Non-vacuous: the response DOES carry A's own content
        assert "ALICE-HUNT-REASONING" in blob, (
            "A's own matches did not come back — an over-broad filter would "
            "pass the leak assertions by returning nothing"
        )


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


class TestGDPRErasureFKChain:
    """P0-2 (beta review, LIVE-confirmed): erasure deleted matches BEFORE
    drafts/applications. application_drafts.match_id, applications.match_id
    and applications.draft_id are NOT-DEFERRABLE FKs with no ON DELETE
    action, so on Postgres DELETE /account/delete 500'd with an
    IntegrityError and the rollback kept EVERY personal row — for exactly
    the users who had drafted or applied.

    The old erasure fixture seeded NULL match_id/draft_id, which is exactly
    why the suite never caught it. This test builds the REAL chain with
    every FK set; on Postgres the constraint is what makes it fail loudly,
    and on SQLite (no FK enforcement) the row-count assertions still verify
    the delete covers every table including ai_usage."""

    def _seed_chain(self, client, db, tag):
        email = f"{tag}-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        match = MatchResult(user_id=uid, job_id=job.id, score=88,
                            tier="excellent_match", decision="approved")
        db.add(match)
        db.flush()
        # The real chain: draft -> match, application -> match AND draft.
        # NULL FKs (what the old fixture seeded) never trip the constraint.
        draft = ApplicationDraft(
            user_id=uid, job_id=job.id, match_id=match.id,
            cover_letter=f"{tag} cover letter", tailored_cv=f"{tag} tailored cv",
            changes_summary="[]", status="ready",
        )
        db.add(draft)
        db.flush()
        db.add(Application(
            user_id=uid, job_id=job.id, match_id=match.id, draft_id=draft.id,
            method="email", status="sent", subject=f"{tag} subject",
            body=f"{tag} body", target_email="boss@acme.example",
        ))
        # ai_usage: user-linked telemetry (no FK) — erasure must take it too
        db.add(AIUsage(user_id=uid, kind="tailor", model="glm-test",
                       prompt_tokens=10, completion_tokens=5))
        db.commit()
        return email, uid, job

    def test_erasure_commits_on_full_fk_chain(self, client, db):
        from app.models import User as UserModel

        _, uid, job = self._seed_chain(client, db, "gdfk")

        r = client.delete("/api/v1/account/delete")
        assert r.status_code == 200, (
            f"erasure returned {r.status_code}: {r.text[:300]} — the FK "
            "chain (draft.match_id / application.match_id / "
            "application.draft_id) is breaking the delete transaction"
        )

        assert db.query(UserModel).filter(UserModel.id == uid).count() == 0
        assert db.query(Profile).filter(Profile.user_id == uid).count() == 0
        assert db.query(MatchResult).filter(MatchResult.user_id == uid).count() == 0
        assert db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid).count() == 0
        assert db.query(Application).filter(Application.user_id == uid).count() == 0
        assert db.query(AIUsage).filter(AIUsage.user_id == uid).count() == 0, (
            "ai_usage rows for the erased user survived — usage telemetry "
            "is user-linked and must go with the account"
        )
        # the shared scraped posting itself is not personal data — it stays
        assert db.query(JobPosting).filter(JobPosting.id == job.id).count() == 1

    def test_erasure_deletes_the_cv_file(self, client, db):
        """The CV bytes must go with the account, through the storage
        backend (local path or remote object key)."""
        from app.services.storage import get_storage

        email = f"gdcv-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        key = get_storage().save(
            f"gdpr-cv-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-1.4 erasure cv",
            "application/pdf",
        )
        profile = db.query(Profile).filter(Profile.user_id == uid).first()
        profile.cv_file_path = key
        db.commit()
        try:
            r = client.delete("/api/v1/account/delete")
            assert r.status_code == 200, r.text
            assert not Path(key).exists(), (
                f"CV file {key} outlived erasure — storage deletion must "
                "go through the storage backend (local paths AND keys)"
            )
        finally:
            if Path(key).exists():
                Path(key).unlink()

    def test_failed_erasure_never_destroys_the_cv_file(self, client, db, monkeypatch):
        """Ordering regression (part of the live repro): the CV file was
        deleted from storage BEFORE the transaction committed, so when the
        DB deletes failed the user kept all their rows AND lost their CV.
        File removal must happen only after a successful commit — a
        rolled-back erasure leaves both the rows and the file intact."""
        from sqlalchemy.orm import Session as OrmSession

        from app.services.storage import get_storage

        email = f"gdtx-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        key = get_storage().save(
            f"gdpr-tx-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-1.4 tx cv",
            "application/pdf",
        )
        profile = db.query(Profile).filter(Profile.user_id == uid).first()
        profile.cv_file_path = key
        db.commit()

        def boom(self):
            raise RuntimeError("simulated commit failure")

        # SYNC Session only — fastapi-users auth runs on the async session,
        # so this trips exactly the route's own db.commit()
        monkeypatch.setattr(OrmSession, "commit", boom)
        try:
            with pytest.raises(RuntimeError):
                client.delete("/api/v1/account/delete")

            assert Path(key).exists(), (
                "CV file was destroyed by an erasure that NEVER COMMITTED "
                "— storage deletion must run after db.commit()"
            )
            # nothing was erased: the transaction rolled back entirely
            assert db.query(Profile).filter(Profile.user_id == uid).count() == 1
        finally:
            monkeypatch.undo()
            db.rollback()
            if Path(key).exists():
                Path(key).unlink()


class TestGDPRExportCompleteness:
    """P1-6: portability covers the user's OWN content. Drafts' cover
    letters / tailored CVs and applications' subject/body/target_email were
    missing from the export payload — the exact documents the user wrote or
    approved are the core of a data export."""

    def test_export_contains_draft_and_application_content(self, client, db):
        email = f"gdx-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        db.add(ApplicationDraft(
            user_id=uid, job_id=job.id,
            cover_letter="P1-6 cover letter", tailored_cv="P1-6 tailored cv",
            changes_summary="[]", status="ready",
        ))
        db.add(Application(
            user_id=uid, job_id=job.id, method="email", status="sent",
            subject="P1-6 subject", body="P1-6 body",
            target_email="boss@acme.example",
        ))
        db.commit()

        r = client.get("/api/v1/account/export")
        assert r.status_code == 200, r.text
        payload = r.json()

        drafts = payload.get("drafts")
        assert drafts, "export payload has no drafts section"
        d = drafts[0]
        assert d["cover_letter"] == "P1-6 cover letter", (
            f"draft cover_letter missing from export: {d}"
        )
        assert d["tailored_cv"] == "P1-6 tailored cv", (
            f"draft tailored_cv missing from export: {d}"
        )

        apps = payload["applications"]
        assert apps, "export payload has no applications section"
        a = apps[0]
        expected = {
            "subject": "P1-6 subject",
            "body": "P1-6 body",
            "target_email": "boss@acme.example",
        }
        for field, value in expected.items():
            assert a.get(field) == value, (
                f"application {field} missing/wrong in export: {a}"
            )


class TestDeleteJobFKChain:
    """P0-2's sibling: delete_job() removed matches before drafts and
    applications — the same NOT-DEFERRABLE FKs made DELETE /jobs/{id} 500
    for any job that had been drafted or applied to."""

    def test_delete_job_with_drafted_applied_chain(self, client, db):
        email = f"djf-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title="Dev", company="X", url=f"https://x/{uuid.uuid4().hex[:6]}",
            status="matched",
        )
        db.add(job)
        db.flush()
        match = MatchResult(user_id=uid, job_id=job.id, score=75,
                            tier="good_match")
        db.add(match)
        db.flush()
        draft = ApplicationDraft(user_id=uid, job_id=job.id, match_id=match.id,
                                 cover_letter="x", tailored_cv="y",
                                 changes_summary="[]", status="ready")
        db.add(draft)
        db.flush()
        db.add(Application(user_id=uid, job_id=job.id, match_id=match.id,
                           draft_id=draft.id, method="browser",
                           status="manual_pending"))
        db.commit()

        r = client.delete(f"/api/v1/jobs/{job.id}")
        assert r.status_code == 204, (
            f"job delete returned {r.status_code}: {r.text[:300]} — the FK "
            "chain (draft.match_id / application.match_id / "
            "application.draft_id) is breaking the delete transaction"
        )

        assert db.query(MatchResult).filter(MatchResult.user_id == uid).count() == 0
        assert db.query(ApplicationDraft).filter(ApplicationDraft.user_id == uid).count() == 0
        assert db.query(Application).filter(Application.user_id == uid).count() == 0
        # no other user references the posting — the shared row goes too
        assert db.query(JobPosting).filter(JobPosting.id == job.id).count() == 0


class TestPerUserMatching:
    def test_same_job_matchable_by_two_users(self, db):
        """The UNIQUE(job_id) constraint is gone — the schema now allows the
        same job scored by two different users."""
        from app.models import User as UserModel

        u1, u2 = uuid.uuid4(), uuid.uuid4()
        # Postgres enforces match_results.user_id — the users must exist
        db.add_all([
            UserModel(id=u1, email=f"mu-{u1.hex[:8]}@test.example",
                      hashed_password="test-only"),
            UserModel(id=u2, email=f"mu-{u2.hex[:8]}@test.example",
                      hashed_password="test-only"),
        ])
        db.flush()
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
        # whoever was newest, so Bob is the trap). Postgres enforces the
        # profiles FK — the users rows must exist (SQLite never checked).
        from app.models import User as UserModel

        db.add_all([
            UserModel(id=a_uid, email=f"alice-{a_uid.hex[:6]}@test.example",
                      hashed_password="test-only"),
            UserModel(id=b_uid, email=f"bob-{b_uid.hex[:6]}@test.example",
                      hashed_password="test-only"),
        ])
        db.flush()
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


class TestCostDoSClamp:
    """max_matches is client-controlled and caps AI calls per run; the
    rate limiter buckets RUNS, not spend. An unbounded value is a
    cost-DoS vector from one authenticated account (found in review,
    2026-08-27; the schema shipped without an upper bound)."""

    def test_max_matches_above_server_cap_is_rejected(self, client, db):
        from pydantic import ValidationError

        from app.core.config import settings
        from app.schemas.pipeline import PipelineRunRequest

        cap = settings.MAX_JOBS_PER_MATCH_RUN
        # A hostile payload must die at the schema, before any route code
        with pytest.raises(ValidationError):
            PipelineRunRequest(max_matches=100_000)
        with pytest.raises(ValidationError):
            PipelineRunRequest(max_matches=cap + 1)
        # Legitimate values still pass — the cap must not break real use
        ok = PipelineRunRequest(max_matches=cap)
        assert ok.max_matches == cap
        assert PipelineRunRequest().max_matches is None

    def test_pipeline_route_rejects_hostile_payload(self, client, db):
        """Through the HTTP boundary: 422 before anything runs."""
        email = f"clamp-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, email)
        _auth_client(client, email)
        r = client.post("/api/v1/pipeline/run", json={"max_matches": 100000})
        assert r.status_code == 422, f"{r.status_code}: {r.text[:200]}"

    def test_pipeline_route_rejects_unknown_source(self, client, db):
        """Client-supplied source names are registry-validated at the
        boundary: a removed scraper (teamtailor post-WO-08) or typo gets
        a 422 naming the valid sources — not a silently dropped source
        or a failed ScrapeRun per hunt."""
        email = f"src-{uuid.uuid4().hex[:6]}@test.example"
        _register(client, email)
        _auth_client(client, email)
        r = client.post("/api/v1/pipeline/run",
                        json={"sources": ["jobtech", "teamtailor"]})
        assert r.status_code == 422, f"{r.status_code}: {r.text[:200]}"
        assert "teamtailor" in r.text and "jobtech" in r.text, (
            f"error should name the unknown source and the valid ones: {r.text[:200]}"
        )


class TestFabricationGuardLayerC:
    """WO-01 Layer C: the runtime control. High-confidence findings drive
    REGENERATION (never strip — a mutilated document the user cannot see
    was altered is worse), up to 2 retries; a survivor BLOCKS the draft
    and names the untraceable claim. Advisory findings persist for the
    review UI. Drives the PRODUCTION service with a scripted tailor."""

    @staticmethod
    def _seed(client, db):
        email = f"fab-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = ("Erik Lindberg. Software Engineer, Svenska Spel, Stockholm "
                     "2019 to 2023. Built payment services in Python. MSc "
                     "Computer Science, Lunds Universitet 2015.")
        db.commit()
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                         description="A Python role.")
        db.add(job)
        db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()
        return uid, job

    @staticmethod
    def _script_tailor(monkeypatch, outputs):
        """outputs: list of dicts consumed in order (regeneration pulls
        the next). Mirrors the fake-tailor pattern the route tests use."""
        from app.services import draft_service
        from app.services.ai_service import AIService

        calls = {"n": 0}

        def fake_tailor(self, profile_context, cv_text, job_description,
                        correction=None):
            i = min(calls["n"], len(outputs) - 1)
            calls["n"] += 1
            return dict(outputs[i])

        fake = AIService.__new__(AIService)
        fake.model = "glm-test"
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(AIService, "tailor_application", fake_tailor)
        return calls

    CLEAN = {"cover_letter": "Dear Acme, I built payment services in Python "
             "at Svenska Spel 2019 to 2023.",
             "tailored_cv": "Erik Lindberg. Software Engineer, Svenska Spel, "
             "Stockholm 2019 to 2023. Python. MSc Computer Science, Lunds "
             "Universitet 2015.",
             "changes_summary": ["refocused"]}
    FABRICATED = {"cover_letter": "AWS Certified Solutions Architect.",
                  "tailored_cv": "Erik Lindberg. Kubernetes. AWS Certified "
                  "Solutions Architect. Acme Global Ltd 2017.",
                  "changes_summary": ["refocused"]}

    def test_clean_draft_ready_zero_findings(self, client, db, monkeypatch):
        from app.services.draft_service import create_draft_for_job

        uid, job = self._seed(client, db)
        calls = self._script_tailor(monkeypatch, [self.CLEAN])
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        d = create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "ready", d.error
        assert calls["n"] == 1, "clean output must not trigger regeneration"
        import json
        assert json.loads(d.fabrication_findings or "[]") == []
        assert d.fabrication_blocked is False

    def test_fabrication_triggers_regeneration_then_succeeds(self, client, db, monkeypatch):
        from app.services.draft_service import create_draft_for_job

        uid, job = self._seed(client, db)
        calls = self._script_tailor(
            monkeypatch, [self.FABRICATED, self.FABRICATED, self.CLEAN])
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        d = create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "ready", d.error
        assert calls["n"] == 3, (
            f"expected fabricated, fabricated, clean = 3 tailor calls, "
            f"got {calls['n']} — the retry loop is not running"
        )
        assert d.fabrication_retries == 2
        assert d.fabrication_blocked is False

    def test_surviving_fabrication_blocks_and_names_the_claim(self, client, db, monkeypatch):
        """After 2 retries the finding is not a heuristic firing once —
        it is the model repeatedly asserting something the CV does not
        support. The send must be blocked and the claim named."""
        from app.services.draft_service import create_draft_for_job

        uid, job = self._seed(client, db)
        self._script_tailor(monkeypatch, [self.FABRICATED])
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        d = create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "failed", (
            f"a finding surviving 2 retries must block, got {d.status}"
        )
        assert d.fabrication_blocked is True
        assert d.fabrication_retries == 2
        assert "certified" in (d.error or "").lower(), (
            f"the block must NAME the untraceable claim: {d.error!r}"
        )

    def test_advisory_technology_flags_but_never_blocks(self, client, db, monkeypatch):
        """'Azure' vs 'Microsoft Azure' class false positives: flag for
        the review UI, never auto-act."""
        from app.services.draft_service import create_draft_for_job

        advisory = dict(self.CLEAN,
                        tailored_cv=self.CLEAN["tailored_cv"] + " Azure.")
        uid, job = self._seed(client, db)
        calls = self._script_tailor(monkeypatch, [advisory])
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        d = create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "ready", (
            "advisory findings must never block or regenerate"
        )
        assert calls["n"] == 1
        import json
        findings = json.loads(d.fabrication_findings or "[]")
        assert any(f["kind"] == "technology" and f["tier"] == "advisory"
                   for f in findings), findings


class TestGuardSourceAlignment:
    """WO-01 review final: the model saw cv_text + profile_context; a
    guard verifying against the CV alone flags facts WE fed the model
    via the summary (preferred roles, location) — feeding a lossy
    summary, then flagging the model for using it."""

    def test_derived_skill_assertion_is_flagged_not_blessed(self, client, db, monkeypatch):
        """r5: profile.skills is AI-DERIVED — 'REST API Design' absent from
        the CV was silently blessed by the aligned source. Derived fields
        must NOT be guard truth: assert one, and the banner shows it (the
        user is the one positioned to correct a bad extraction)."""
        from app.services import draft_service
        from app.services.ai_service import AIService

        email = f"derive-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = "Erik Lindberg. Skills: Python."
        p.skills = '["Python", "Kubernetes"]'   # derived; not in the CV
        db.commit()
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                         description="A role.")
        db.add(job); db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()
        output = {"cover_letter": "I deploy Kubernetes clusters.",
                  "tailored_cv": "Erik. Kubernetes.", "changes_summary": []}

        def fake(self, profile_context, cv_text, job_description, correction=None):
            return dict(output)

        fake_svc = AIService.__new__(AIService); fake_svc.model = "glm-test"
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake_svc)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(AIService, "tailor_application", fake)
        d = draft_service.create_draft_for_job(db, job, profile=p, user_id=uid)
        import json as _json
        findings = _json.loads(d.fabrication_findings or "[]")
        assert any("kubernetes" in f["value"].lower() for f in findings), (
            f"AI-derived skill blessed as truth: {findings} — extraction "
            "fabrications must surface to the user, not hide in the source"
        )

    def test_user_entered_preference_is_not_flagged(self, client, db, monkeypatch):
        from app.services import draft_service
        from app.services.ai_service import AIService

        email = f"align-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = "Erik Lindberg. Skills: Python."   # Kubernetes NOT here
        p.preferred_roles = '["Kubernetes Developer"]'  # USER-entered target
        db.commit()
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                         description="A Python role.")
        db.add(job)
        db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()

        output = {"cover_letter": "I deploy Kubernetes clusters and build "
                  "with Python.",
                  "tailored_cv": "Erik Lindberg. Python. Kubernetes.",
                  "changes_summary": ["refocused"]}

        def fake(self, profile_context, cv_text, job_description, correction=None):
            return dict(output)

        fake_svc = AIService.__new__(AIService)
        fake_svc.model = "glm-test"
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake_svc)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(AIService, "tailor_application", fake)

        d = draft_service.create_draft_for_job(db, job, profile=p, user_id=uid)
        import json as _json
        findings = _json.loads(d.fabrication_findings or "[]")
        assert d.status == "ready", d.error
        assert not any("kubernetes" in f["value"].lower()
                       for f in findings), (
            f"a USER-ENTERED preference flagged: {findings} — the guard "
            "must not flag target roles the user themselves asked for"
        )


class TestProductionJudge:
    """WO-02: the judge — the only mechanism with demonstrated catches —
    runs IN PRODUCTION on every draft (not just the opt-in harness),
    inside the same regenerate-then-block loop as Layer A."""

    CLEAN_TAILOR = {"cover_letter": "Hej, jag bygger betaltjanster i Python.",
                    "tailored_cv": "Erik. Python pa Svenska Spel.",
                    "changes_summary": []}

    @staticmethod
    def _seed(client, db):
        from app.services import draft_service
        from app.services.ai_service import AIService

        email = f"pj-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = "Erik. Python developer at Svenska Spel."
        db.commit()
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                         description="A Python role.")
        db.add(job); db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()
        return draft_service, AIService, p, job, uid

    @staticmethod
    def _script(monkeypatch, ds, AIS, tailor_outputs, judge_outputs):
        t_calls = {"n": 0}
        j_calls = {"n": 0}

        def fake_tailor(self, profile_context, cv_text, job_description,
                        correction=None):
            i = min(t_calls["n"], len(tailor_outputs) - 1)
            t_calls["n"] += 1
            return dict(tailor_outputs[i])

        def fake_judge(self, source_of_truth, tailored_text):
            i = min(j_calls["n"], len(judge_outputs) - 1)
            j_calls["n"] += 1
            return [dict(x) for x in judge_outputs[i]]

        fake = AIS.__new__(AIS)
        fake.model = "glm-test"
        monkeypatch.setattr(ds, "get_ai_service", lambda: fake)
        monkeypatch.setattr(ds, "ai_service_available", lambda: True)
        monkeypatch.setattr(AIS, "tailor_application", fake_tailor)
        monkeypatch.setattr(AIS, "judge_fabrication", fake_judge)
        monkeypatch.setattr(ds.settings, "FABRICATION_JUDGE", "on",
                            raising=False)
        return t_calls, j_calls

    def test_judge_clean_draft_is_ready(self, client, db, monkeypatch):
        ds, AIS, p, job, uid = self._seed(client, db)
        self._script(monkeypatch, ds, AIS, [self.CLEAN_TAILOR], [[]])
        d = ds.create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "ready", d.error

    def test_judge_finding_regenerates_then_blocks(self, client, db, monkeypatch):
        """A judge-found unsupported claim is semantic evidence — same
        loop as Layer A high: regenerate with the claim named, block
        after MAX retries, and the finding is recorded."""
        ds, AIS, p, job, uid = self._seed(client, db)
        catch = [{"claim": "EU citizen with full work rights",
                  "why": "not in the CV"}]
        t_calls, j_calls = self._script(
            monkeypatch, ds, AIS,
            [self.CLEAN_TAILOR, self.CLEAN_TAILOR, self.CLEAN_TAILOR],
            [catch, catch, catch])
        d = ds.create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "failed", (
            f"a judge finding surviving retries must block, got {d.status}"
        )
        assert d.fabrication_blocked is True
        assert "work rights" in (d.error or "").lower(), (
            f"block must NAME the judge's claim: {d.error!r}"
        )
        assert j_calls["n"] == 3 and t_calls["n"] == 3, (
            "judge + tailor both run every attempt"
        )

    def test_judge_finding_recovers_on_regeneration(self, client, db, monkeypatch):
        ds, AIS, p, job, uid = self._seed(client, db)
        catch = [{"claim": "invented metric 40%", "why": "not in the CV"}]
        self._script(monkeypatch, ds, AIS,
                     [self.CLEAN_TAILOR, self.CLEAN_TAILOR], [catch, []])
        d = ds.create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "ready", d.error
        assert d.fabrication_retries == 1

    def test_judge_kill_switch(self, client, db, monkeypatch):
        """FABRICATION_JUDGE=off disables the extra call (emergency cost
        lever) — Layer A still guards."""
        ds, AIS, p, job, uid = self._seed(client, db)
        t_calls, j_calls = self._script(
            monkeypatch, ds, AIS, [self.CLEAN_TAILOR], [[]])
        # _script opted in; now flip the switch off for this test
        monkeypatch.setattr(ds.settings, "FABRICATION_JUDGE", "off",
                            raising=False)
        d = ds.create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "ready"
        assert j_calls["n"] == 0, "judge ran despite the kill switch"


class TestJudgeFailClosed:
    """WO-02 review: the judge failed OPEN — _parse_json's {} on a
    decode failure read as 'faithful' and shipped the document, and
    truncation CORRELATES with fabrication count. It must fail closed
    (the caller's except marks the draft failed), and verdicts must be
    deterministic (temperature 0.0 — the retry loop and the measurement
    protocol both depend on it)."""

    def test_unparseable_judge_response_fails_the_draft(self, client, db, monkeypatch):
        from app.services import draft_service
        from app.services.ai_service import AIService

        email = f"fc-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = "Erik. Python at Svenska Spel."
        db.commit()
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                         description="A role.")
        db.add(job); db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                           tier="good_match", decision="approved"))
        db.commit()

        clean = {"cover_letter": "Python at Svenska Spel.", "tailored_cv":
                 "Erik. Python.", "changes_summary": []}
        monkeypatch.setattr(AIService, "tailor_application",
                            lambda self, **kw: dict(clean))
        monkeypatch.setattr(AIService, "judge_fabrication",
                            lambda self, a, b: (_ for _ in ()).throw(
                                ValueError("Unparseable JSON from fabrication judge")))
        fake = AIService.__new__(AIService); fake.model = "glm-test"
        monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake)
        monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(draft_service.settings, "FABRICATION_JUDGE", "on",
                            raising=False)
        d = draft_service.create_draft_for_job(db, job, profile=p, user_id=uid)
        assert d.status == "failed", (
            f"judge transport failure read as a verdict: {d.status} — a "
            "malformed judge response must fail CLOSED"
        )
        assert "judge" in (d.error or "").lower() or "unparseable" in (d.error or "").lower()

    def test_judge_called_at_temperature_zero(self, client, db, monkeypatch):
        from app.services.ai_service import AIService

        captured = {}
        real_complete = AIService._complete

        def spy(self, system_prompt, user_message, temperature=0.3, kind=None):
            if "fact-checker" in system_prompt:
                captured["temp"] = temperature
                return '{"unsupported": []}'
            return real_complete(self, system_prompt, user_message,
                                 temperature=temperature, kind=kind)

        monkeypatch.setattr(AIService, "_complete", spy)
        svc = AIService.__new__(AIService); svc.model = "glm-test"
        svc.judge_fabrication("CV", "doc")
        assert captured.get("temp") == 0.0, (
            f"judge temperature {captured.get('temp')!r} — verdicts must be "
            "deterministic (retry semantics + reproducible baselines)"
        )


class TestJudgeWrongTypeFailsClosed:
    """WO-02 review follow-up: '{"unsupported": null}' / '"none"' / a dict
    all passed the key check, failed isinstance, and returned [] —
    FAITHFUL -> ships. Wrong type is the same transport/format failure
    as a missing key: never a verdict."""

    def test_non_list_unsupported_raises(self, monkeypatch):
        from app.services.ai_service import AIService

        svc = AIService.__new__(AIService)
        for raw in ('{"unsupported": null}', '{"unsupported": "none"}',
                    '{"unsupported": {"claim": "invented degree"}}'):
            monkeypatch.setattr(
                AIService, "_complete", staticmethod(
                    lambda s, u, temperature=0.0, kind=None, _r=raw: _r))
            try:
                out = AIService.judge_fabrication(svc, "cv", "doc")
                raise AssertionError(
                    f"{raw!r} read as verdict {out!r} — wrong type must "
                    "fail closed, not ship as faithful"
                )
            except ValueError:
                pass  # the required behaviour
        # valid shapes still work
        monkeypatch.setattr(
            AIService, "_complete", staticmethod(
                lambda s, u, temperature=0.0, kind=None: '{"unsupported": []}'))
        assert AIService.judge_fabrication(svc, "cv", "doc") == []


class TestUserIdOnCostRows:
    """WO-05's deferral, landed with WO-04: ai_usage rows carry the
    CALLER's user_id via request-context — the trial budget's meter."""

    def test_request_ai_calls_attributed_to_user(self, client, db, monkeypatch):
        from app.services import matcher_service
        from app.services.ai_service import AIService

        email = f"uid-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        p.cv_text = "Erik. Python developer at Svenska Spel."
        db.commit()

        class _U:
            prompt_tokens = 100
            completion_tokens = 20
            model_dump = lambda self: {"prompt_tokens": 100,
                                       "completion_tokens": 20}

        class _R:
            usage = _U()
            id = "req_uid_test"
            choices = [type("C", (), {"message": type("M", (), {
                "content": '{"score": 80, "reasoning": "ok", '
                '"recommendation": "apply", "confidence": "high", '
                '"matched_skills": ["Python"], "missing_skills": [], '
                '"transferable_skills": [], "cover_note": null}',
                "reasoning_content": None})()})()]

        def fake_complete(self, system_prompt, user_message,
                          temperature=0.3, kind="unknown"):
            from app.services.ai_service import record_ai_usage
            record_ai_usage(kind, "glm-5.1", _R())
            return _R().choices[0].message.content

        svc = AIService.__new__(AIService)
        svc.model = "glm-5.1"
        svc.max_tokens = 2000
        svc.thinking = {"type": "disabled"}
        monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
        monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)
        monkeypatch.setattr(AIService, "_complete", fake_complete)
        job = JobPosting(source="manual", source_id=str(uuid.uuid4())[:8],
                         title="Dev", company="Acme",
                         url=f"https://x/{uuid.uuid4().hex[:6]}", status="new",
                         description="A Python role with substance.")
        db.add(job); db.commit()

        from app.models import AIUsage
        before = db.query(AIUsage).filter(AIUsage.user_id == uid).count()
        r = client.post("/api/v1/matches/run?limit=5")
        assert r.status_code == 200, r.text[:200]
        import time as _t
        _t.sleep(2)  # BackgroundTasks run after the response
        from app.core.database import SessionLocal as _SL
        _db = _SL()
        try:
            jobs = _db.query(JobPosting).filter(JobPosting.status == "matched").count()
        finally:
            _db.close()
        assert jobs >= 1, "matching did not run via the route"
        after = db.query(AIUsage).filter(AIUsage.user_id == uid).count()
        assert after > before, (
            f"ai_usage rows not attributed to the caller ({before}->{after}) — "
            "trial budgets cannot meter per-user spend without user_id"
        )
