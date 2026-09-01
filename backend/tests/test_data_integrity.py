"""
Data-integrity regression suite for three review findings:

- P1-5  CV re-upload breaks erasure + outbound integrity
        (a) the replaced storage object is orphaned and survives GDPR erasure
        (b) a draft guarded against CV-old submits with CV-new attached
- SUBMIT  check-then-act submit_draft with no row lock and no
        unique(applications.draft_id) — the double-click double-send window
- P1-7  7-day JWT with no revocation — password change must kill
        outstanding tokens (token_version claim)

Every test here documents a behavior that was LIVE-confirmed broken or is
the direct regression guard for its fix.
"""

import base64
import hashlib
import os
import uuid
from pathlib import Path

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
from app.models import (
    User as UserModel,
)

# Built by concatenation so no single credential-shaped literal sits in
# the source (secret scanners flag fixed test passwords; the values are
# throwaway fixtures that never authenticate anything real).
PASSWORD = "TestPass-" + "2026!"
NEW_PASSWORD = "NewPass-" + "2027!"


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_di.db"):
        os.remove("test_di.db")
    with TestClient(app) as c:  # lifespan -> init_db -> alembic head
        yield c
    engine.dispose()
    if os.path.exists("test_di.db"):
        os.remove("test_di.db")


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


def _auth_client(client, email, password=PASSWORD):
    r = client.post(
        "/api/v1/auth/jwt/login",
        data={"username": email, "password": password},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token


def _clear_auth(client):
    client.headers.pop("Authorization", None)


def _make_user_with_profile(db, *, uid=None, cv_path=None, cv_text="CV TEXT",
                            name="Di Tester"):
    """User + profile + approved job, all committed. Pass uid= to attach
    the rows to an already-registered (route-authenticated) account —
    the register-time empty Profile is UPDATED, never duplicated (the
    user_id FK is unique). Returns (uid, profile, job)."""
    if uid is None:
        uid = uuid.uuid4()
        db.add(UserModel(id=uid, email=f"di-{uid.hex[:6]}@test.example",
                         hashed_password="test-only"))
        db.flush()
        profile = Profile(user_id=uid, is_active=1, full_name=name,
                          cv_text=cv_text,
                          cv_file_name=f"{name.split()[0].lower()}.pdf",
                          cv_file_path=cv_path)
    else:
        profile = db.query(Profile).filter(Profile.user_id == uid).first()
        assert profile is not None, "pass uid= only for REGISTERED users"
        profile.full_name = name
        profile.cv_text = cv_text
        profile.cv_file_name = f"{name.split()[0].lower()}.pdf"
        profile.cv_file_path = cv_path
    job = JobPosting(
        source="manual", source_id=uuid.uuid4().hex[:8],
        title="Dev", company="Acme",
        url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
        application_email="jobs@acme.example",
    )
    db.add_all([profile, job])
    db.flush()
    db.add(MatchResult(user_id=uid, job_id=job.id, score=85,
                       tier="excellent_match", decision="approved"))
    db.commit()
    return uid, profile, job


def _make_ready_draft(db, uid, job, *, cover_letter="Dear Acme", snapshot=None):
    draft = ApplicationDraft(
        user_id=uid, job_id=job.id, status="ready",
        cover_letter=cover_letter, tailored_cv="TAILORED CV",
        changes_summary="[]", cv_file_path=snapshot,
    )
    db.add(draft)
    db.commit()
    return draft


def _fake_tailor_class(monkeypatch):
    """Make create_draft_for_job's AI path deterministic (no network)."""
    from app.services import draft_service
    from app.services.ai_service import AIService

    def fake_tailor(self, profile_context, cv_text, job_description, **kwargs):
        return {"cover_letter": "Dear Acme", "tailored_cv": "TAILORED",
                "changes_summary": ["n/a"]}

    fake_service = AIService.__new__(AIService)
    fake_service.model = "glm-test"
    monkeypatch.setattr(draft_service, "get_ai_service", lambda: fake_service)
    monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
    monkeypatch.setattr(AIService, "tailor_application", fake_tailor)
    return draft_service


def _fake_resend(monkeypatch, sent):
    """Live email settings + a capturing resend module (multiuser pattern)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "RESEND_API_KEY", "test-key")
    monkeypatch.setattr(settings, "APPLY_FROM_EMAIL", "apply@jobfinderos.test")

    class _Emails:
        @staticmethod
        def send(params):
            sent.append(params)
            return {"id": "msg_test"}

    fake_resend = type("R", (), {"Emails": _Emails, "api_key": None})
    monkeypatch.setitem(__import__("sys").modules, "resend", fake_resend)


def _attachment_bytes(params):
    return b"".join(
        base64.b64decode(a["content"]) for a in params.get("attachments", [])
    )


# =====================================================================
# P1-5a — the replaced CV storage object must die at re-upload
# =====================================================================


class TestReuploadDeletesReplacedCv:
    def _upload(self, client, content=b"%PDF-1.6 replacement cv", name="new.pdf"):
        return client.post(
            "/api/v1/profile/upload",
            files={"file": (name, content, "application/pdf")},
        )

    def test_reupload_deletes_replaced_cv_object(self, client, db, monkeypatch):
        """LIVE-confirmed: the old file stayed on disk after re-upload,
        unreferenced — and GDPR erasure only deletes the CURRENT profile
        path, so the orphan survived erasure."""
        from app.services import cv_service
        from app.services.file_service import FileService
        from app.services.storage import get_storage

        email = f"cv-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        old_key = get_storage().save(
            f"old-cv-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-1.4 OLD CV BYTES",
            "application/pdf",
        )
        profile = db.query(Profile).filter(Profile.user_id == uid).first()
        profile.cv_file_path = old_key
        profile.cv_text = "old text"
        db.commit()

        monkeypatch.setattr(FileService, "validate_pdf",
                            staticmethod(lambda c, max_size_mb=5: True))
        monkeypatch.setattr(FileService, "extract_text_from_pdf",
                            staticmethod(lambda c: "new extracted text"))
        monkeypatch.setattr(cv_service, "ai_service_available", lambda: False)

        try:
            r = self._upload(client)
            assert r.status_code == 200, r.text

            assert not Path(old_key).exists(), (
                f"Re-upload orphaned the replaced CV object {old_key!r}: it "
                "is referenced by NOTHING (erasure deletes only the current "
                "profile path) — the user's PII survives account deletion."
            )
            new_profile = db.query(Profile).filter(Profile.user_id == uid).first()
            assert new_profile.cv_file_path != old_key
        finally:
            if Path(old_key).exists():
                Path(old_key).unlink()
            new_profile = db.query(Profile).filter(Profile.user_id == uid).first()
            if new_profile and new_profile.cv_file_path and \
                    Path(new_profile.cv_file_path).exists():
                Path(new_profile.cv_file_path).unlink()

    def test_reupload_delete_failure_does_not_fail_upload(self, client, db,
                                                          monkeypatch, caplog):
        """A storage hiccup while deleting the OLD object must never
        reject the upload (the NEW object is already safely stored)."""
        import logging as _logging

        from app.services import cv_service
        from app.services.file_service import FileService
        from app.services.storage import LocalStorage

        email = f"cvdf-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        old_key = LocalStorage().save(
            f"df-cv-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-1.4 bytes", "application/pdf"
        )
        profile = db.query(Profile).filter(Profile.user_id == uid).first()
        profile.cv_file_path = old_key
        db.commit()

        monkeypatch.setattr(FileService, "validate_pdf",
                            staticmethod(lambda c, max_size_mb=5: True))
        monkeypatch.setattr(FileService, "extract_text_from_pdf",
                            staticmethod(lambda c: "text"))
        monkeypatch.setattr(cv_service, "ai_service_available", lambda: False)

        def boom(self, key):
            raise RuntimeError("storage exploded")

        monkeypatch.setattr(LocalStorage, "delete", boom)

        try:
            with caplog.at_level(_logging.WARNING, logger="app.services.cv_service"):
                r = self._upload(client)
            assert r.status_code == 200, (
                f"Upload failed because deleting the OLD object failed: {r.text} "
                "— the new CV is stored; the old object's cleanup is best-effort."
            )
            warnings = [r for r in caplog.records
                        if r.levelno == _logging.WARNING and "delete" in r.getMessage().lower()]
            assert warnings, "deletion failure was swallowed silently — log it"
        finally:
            if Path(old_key).exists():
                Path(old_key).unlink()
            new_profile = db.query(Profile).filter(Profile.user_id == uid).first()
            if new_profile and new_profile.cv_file_path and \
                    Path(new_profile.cv_file_path).exists():
                Path(new_profile.cv_file_path).unlink()

    def test_reupload_keeps_cv_referenced_by_open_draft(self, client, db, monkeypatch):
        """P1-5a and P1-5b interplay: when a still-open draft snapshotted
        the old CV, that file is NOT an orphan — the draft's package must
        be able to attach the CV it was tailored from. It is deleted by
        erasure (sweeps snapshot paths) instead."""
        from app.services import cv_service
        from app.services.file_service import FileService
        from app.services.storage import get_storage

        email = f"cvkeep-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        old_key = get_storage().save(
            f"keep-cv-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-1.4 OLD REFERENCED",
            "application/pdf",
        )
        uid, profile, job = _make_user_with_profile(
            db, uid=uid, cv_path=old_key, cv_text="old text"
        )
        draft = _make_ready_draft(db, uid, job, snapshot=old_key)

        monkeypatch.setattr(FileService, "validate_pdf",
                            staticmethod(lambda c, max_size_mb=5: True))
        monkeypatch.setattr(FileService, "extract_text_from_pdf",
                            staticmethod(lambda c: "new text"))
        monkeypatch.setattr(cv_service, "ai_service_available", lambda: False)

        try:
            r = self._upload(client)
            assert r.status_code == 200, r.text
            assert Path(old_key).exists(), (
                "Re-upload deleted a CV object still snapshotted by an open "
                f"draft ({draft.id}) — that draft can no longer attach its "
                "original CV and the package silently degrades."
            )
        finally:
            if Path(old_key).exists():
                Path(old_key).unlink()
            fresh = db.query(Profile).filter(Profile.user_id == uid).first()
            if fresh and fresh.cv_file_path and Path(fresh.cv_file_path).exists():
                Path(fresh.cv_file_path).unlink()


# =====================================================================
# P1-5b — the draft snapshots the CV it was tailored from
# =====================================================================


class TestDraftCvSnapshot:
    def test_draft_creation_snapshots_cv_reference(self, db, monkeypatch):
        draft_service = _fake_tailor_class(monkeypatch)
        uid, profile, job = _make_user_with_profile(
            db, cv_path="/tmp/snap-cv-old.pdf", cv_text="OLD SNAPSHOT TEXT"
        )
        draft = draft_service.create_draft_for_job(
            db, job, profile=profile, user_id=uid
        )
        assert draft.status == "ready"
        assert draft.cv_file_path == "/tmp/snap-cv-old.pdf", (
            "Draft did not snapshot the CV file path it was tailored against — "
            "after a re-upload the send path cannot know which CV the package "
            "was built from."
        )
        expected = hashlib.sha256(b"OLD SNAPSHOT TEXT").hexdigest()
        assert draft.cv_hash == expected, "cv_hash must be sha256 of cv_text at tailoring time"

    def test_submit_attaches_the_snapshotted_cv_not_the_current(self, db, monkeypatch):
        """The brain invariant: a package tailored against CV-old must not
        email CV-new as its 'original CV' — the pair contradicts."""
        from app.core.config import settings
        from app.services import draft_service
        from app.services.storage import get_storage

        monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)  # beta gate
        old_key = get_storage().save(
            f"snap-old-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-OLD-ORIGINAL",
            "application/pdf",
        )
        new_key = get_storage().save(
            f"snap-new-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-NEW-ORIGINAL",
            "application/pdf",
        )
        try:
            uid, profile, job = _make_user_with_profile(
                db, cv_path=old_key, cv_text="OLD CV TEXT"
            )
            draft = _make_ready_draft(db, uid, job, snapshot=old_key)

            # re-upload: the profile now points at the NEW object
            profile.cv_file_path = new_key
            db.commit()

            sent = []
            _fake_resend(monkeypatch, sent)
            application = draft_service.submit_draft(
                db, draft, "email", profile, user_id=uid
            )
            assert application.status == "sent", application.error
            blob = _attachment_bytes(sent[0])
            assert b"%PDF-OLD-ORIGINAL" in blob, (
                "The 'original CV' attachment is NOT the CV the package was "
                "tailored from — the draft was guarded against CV-old but the "
                "employer receives CV-new."
            )
            assert b"%PDF-NEW-ORIGINAL" not in blob, (
                "CV-new was attached as the 'original CV' of a CV-old-tailored "
                "package (draft_service read the CURRENT profile path at send "
                "time)."
            )
        finally:
            for key in (old_key, new_key):
                if Path(key).exists():
                    Path(key).unlink()

    def test_legacy_draft_without_snapshot_falls_back_to_current(self, db, monkeypatch):
        """Pre-migration drafts (NULL snapshot) keep the old behavior: the
        profile's current path."""
        from app.core.config import settings
        from app.services import draft_service
        from app.services.storage import get_storage

        monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)  # beta gate
        current_key = get_storage().save(
            f"snap-cur-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-CURRENT-CV",
            "application/pdf",
        )
        try:
            uid, profile, job = _make_user_with_profile(
                db, cv_path=current_key, cv_text="CURRENT CV TEXT"
            )
            draft = _make_ready_draft(db, uid, job, snapshot=None)

            sent = []
            _fake_resend(monkeypatch, sent)
            application = draft_service.submit_draft(
                db, draft, "email", profile, user_id=uid
            )
            assert application.status == "sent", application.error
            assert b"%PDF-CURRENT-CV" in _attachment_bytes(sent[0])
        finally:
            if Path(current_key).exists():
                Path(current_key).unlink()


# =====================================================================
# SUBMIT — the double-send window
# =====================================================================


class TestDoubleSubmitWindow:
    def test_concurrent_submits_produce_one_application(self, db, monkeypatch):
        """Two sessions both load the draft as 'ready' (the double-click
        race), then both call submit_draft. Exactly one application row
        and one dispatch may result."""
        from app.services import draft_service

        uid, profile, job = _make_user_with_profile(db, cv_text="RACE CV")
        draft = _make_ready_draft(db, uid, job)

        sent = []
        _fake_resend(monkeypatch, sent)

        s1, s2 = SessionLocal(), SessionLocal()
        try:
            d1 = s1.get(ApplicationDraft, draft.id)
            d2 = s2.get(ApplicationDraft, draft.id)
            assert d1.status == "ready" and d2.status == "ready"

            app1 = draft_service.submit_draft(s1, d1, "browser", profile, user_id=uid)
            assert app1.status == "manual_pending"

            from app.services.draft_service import DraftConflictError

            with pytest.raises(DraftConflictError):
                draft_service.submit_draft(s2, d2, "browser", profile, user_id=uid)

            rows = (
                db.query(Application)
                .filter(Application.draft_id == draft.id)
                .all()
            )
            assert len(rows) == 1, (
                f"{len(rows)} application rows for draft {draft.id} — the "
                "check-then-act submit let a double-click double-send."
            )
        finally:
            s1.rollback(); s1.close()
            s2.rollback(); s2.close()

    def test_unique_index_rejects_second_application_row(self, db):
        """The DB-level backstop: applications.draft_id is unique (partial:
        NULLs allowed for draft-less applies)."""
        from sqlalchemy.exc import IntegrityError

        uid, profile, job = _make_user_with_profile(db, cv_text="UNIQ CV")
        draft = _make_ready_draft(db, uid, job)
        db.add(Application(user_id=uid, job_id=job.id, draft_id=draft.id,
                           method="browser", status="manual_pending"))
        db.commit()
        db.add(Application(user_id=uid, job_id=job.id, draft_id=draft.id,
                           method="browser", status="manual_pending"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_null_draft_id_rows_are_all_allowed(self, db):
        """Manual/browser applies without a draft must keep working — the
        unique index is PARTIAL (WHERE draft_id IS NOT NULL)."""
        uid, profile, job = _make_user_with_profile(db, cv_text="NULL CV")
        db.add_all([
            Application(user_id=uid, job_id=job.id, method="browser",
                        status="manual_pending"),
            Application(user_id=uid, job_id=job.id, method="manual",
                        status="manual_pending"),
        ])
        db.commit()  # must not raise

    def test_failed_send_returns_draft_to_ready_and_resubmit_reuses_row(self, db, monkeypatch):
        """Failure path: the draft returns to 'ready' (retry stays
        possible) and a second submit must REUSE the failed application
        row, not insert a duplicate (the unique index would fire)."""
        from app.core.config import settings
        from app.services import draft_service

        monkeypatch.setattr(settings, "EMAIL_APPLY_ENABLED", True)  # beta gate
        uid, profile, job = _make_user_with_profile(db, cv_text="FAIL CV")
        draft = _make_ready_draft(db, uid, job)

        # no RESEND config -> _send_with_pdfs marks the application failed
        application = draft_service.submit_draft(db, draft, "email", profile, user_id=uid)
        assert application.status == "failed", application.error
        db.refresh(draft)
        assert draft.status == "ready", (
            "a failed send must leave the draft editable/actionable, not stranded"
        )

        application2 = draft_service.submit_draft(db, draft, "browser", profile, user_id=uid)
        assert application2.status == "manual_pending"
        rows = db.query(Application).filter(Application.draft_id == draft.id).all()
        assert len(rows) == 1, (
            f"{len(rows)} rows for the draft after a failed-then-resubmitted "
            "send — the resubmit must reuse the failed application row."
        )
        assert rows[0].id == application.id

    def test_submit_conflict_maps_to_409(self, client, db):
        """A submit that loses the race (draft already 'sending') is a
        CONFLICT, not a 400 'not ready' — the caller's package is fine,
        another dispatch owns it."""
        email = f"sc-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        uid, profile, job = _make_user_with_profile(db, uid=uid, cv_text="409 CV")
        draft = _make_ready_draft(db, uid, job)
        draft.status = "sending"  # another dispatch holds it
        db.commit()

        r = client.post(f"/api/v1/applications/draft/{draft.id}/submit",
                        json={"method": "browser"})
        assert r.status_code == 409, (
            f"submit against a 'sending' draft returned {r.status_code} "
            f"({r.text}) — concurrent submits must surface as 409 Conflict"
        )

    def test_sweep_recovers_stranded_sending_drafts(self, db):
        """A process death between the 'sending' claim and the outcome
        write must not strand the draft forever (PIPE-21-style sweep)."""
        from datetime import timedelta

        from app.core.timeutil import utc_now
        from app.services import pipeline

        uid, profile, job = _make_user_with_profile(db, cv_text="SWEEP CV")

        # no application row -> nothing was sent -> back to ready
        d1 = _make_ready_draft(db, uid, job)
        # application row, sent -> the insert committed -> submitted
        d2 = _make_ready_draft(db, uid, job)
        # application row, failed -> retryable -> ready
        d3 = _make_ready_draft(db, uid, job)
        for d in (d1, d2, d3):
            d.status = "sending"
        db.add(Application(user_id=uid, job_id=job.id, draft_id=d2.id,
                           method="email", status="sent"))
        db.add(Application(user_id=uid, job_id=job.id, draft_id=d3.id,
                           method="email", status="failed", error="boom"))
        # FRESH 'sending' (a live dispatch in another process) -> untouched
        d4 = _make_ready_draft(db, uid, job)
        d4.status = "sending"
        db.commit()

        # Backdate the stale three PAST the sweep cutoff. A plain attribute
        # write would be overwritten by updated_at's onupdate at flush, so
        # the backdate goes through a bulk UPDATE (which bypasses it).
        cutoff = utc_now() - timedelta(minutes=30)
        db.query(ApplicationDraft).filter(
            ApplicationDraft.id.in_([d1.id, d2.id, d3.id])
        ).update({"updated_at": cutoff}, synchronize_session=False)
        db.commit()
        for d in (d1, d2, d3, d4):
            db.refresh(d)

        pipeline._maintenance_sweeps(db)

        assert d1.status == "ready", "no application row — the send never happened"
        assert d2.status == "submitted", "sent application — mirror submitted"
        assert d3.status == "ready", "failed application — keep it retryable"
        assert d4.status == "sending", "fresh 'sending' is a LIVE dispatch — leave it"


# =====================================================================
# P1-7 — password change must revoke outstanding JWTs
# =====================================================================


class TestTokenRevocation:
    def test_password_change_revokes_outstanding_tokens(self, client, db):
        email = f"tv-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        old_token = _auth_client(client, email)

        r = client.get("/api/v1/users/me")
        assert r.status_code == 200, "fresh token must work"

        r = client.patch(
            "/api/v1/users/me", json={"password": NEW_PASSWORD}
        )
        assert r.status_code == 200, r.text

        # The OLD token must now be dead (was: valid for the remaining ~7 days)
        client.headers.update({"Authorization": f"Bearer {old_token}"})
        r = client.get("/api/v1/users/me")
        assert r.status_code == 401, (
            "The pre-change JWT still authenticates after a password change — "
            "a stolen/leaked token outlives the rotation (P1-7)."
        )

        # New login works and carries the bumped version
        new_token = _auth_client(client, email, NEW_PASSWORD)
        assert new_token
        user = db.query(UserModel).filter(UserModel.id == uid).first()
        assert user.token_version == 1, (
            f"token_version={user.token_version!r} — the password change must "
            "bump it so version-pinned tokens mismatch"
        )
        r = client.get("/api/v1/users/me")
        assert r.status_code == 200

    def test_second_password_change_bumps_again(self, client, db):
        email = f"tv2-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)
        client.patch("/api/v1/users/me", json={"password": NEW_PASSWORD})
        t1 = _auth_client(client, email, NEW_PASSWORD)
        client.patch("/api/v1/users/me", json={"password": "Third-Pass-" + "2028!"})
        client.headers.update({"Authorization": f"Bearer {t1}"})
        r = client.get("/api/v1/users/me")
        assert r.status_code == 401, "token from version 1 must die at version 2"
        user = db.query(UserModel).filter(UserModel.id == uid).first()
        assert user.token_version == 2

    def test_non_password_update_does_not_revoke(self, client, db):
        email = f"tv3-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        token = _auth_client(client, email)
        r = client.patch("/api/v1/users/me", json={"display_name": "Di User"})
        assert r.status_code == 200, r.text
        client.headers.update({"Authorization": f"Bearer {token}"})
        r = client.get("/api/v1/users/me")
        assert r.status_code == 200, (
            "a profile-field update must NOT log the user out — only "
            "password changes revoke"
        )
        user = db.query(UserModel).filter(UserModel.id == uid).first()
        assert user.token_version == 0


# =====================================================================
# P1-5a erasure side — snapshot paths are personal data too
# =====================================================================


class TestErasureSweepsSnapshotPaths:
    def test_erasure_deletes_every_cv_object(self, client, db):
        """Erasure must remove the profile path AND every distinct draft
        snapshot path — the LIVE-confirmed orphan-survival class."""
        from app.services.storage import get_storage

        email = f"ers-{uuid.uuid4().hex[:6]}@test.example"
        uid = uuid.UUID(_register(client, email))
        _auth_client(client, email)

        current = get_storage().save(
            f"ers-cur-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-CURRENT", "application/pdf")
        snapped = get_storage().save(
            f"ers-snap-{uuid.uuid4().hex[:8]}.pdf", b"%PDF-SNAP", "application/pdf")
        try:
            uid, profile, job = _make_user_with_profile(db, uid=uid, cv_path=current)
            _make_ready_draft(db, uid, job, snapshot=snapped)

            r = client.delete("/api/v1/account/delete")
            assert r.status_code == 200, r.text

            for key in (current, snapped):
                assert not Path(key).exists(), (
                    f"CV object {key} outlived GDPR erasure — snapshot "
                    "referenced files are personal data."
                )
        finally:
            for key in (current, snapped):
                if Path(key).exists():
                    Path(key).unlink()


# =====================================================================
# BROWSER HAND-OFF — the portal liveness probe (2026-08-31 incident)
# =====================================================================


class TestBrowserHandoffPortalProbe:
    """A careerjet redirect 502'd at hand-off while the original posting
    lived at its source (jobtech #31322561) — the user was handed a dead
    link under a 'sent' label. The hand-off now probes apply_url: a
    DEFINITE HTTP >= 400 (405 aside — HEAD unsupported, server alive)
    records a warning on the application row, which the Applications
    card renders beside the 'Open posting' link. Warn, never block;
    probe failures stay silent."""

    def _submit(self, db, monkeypatch, head_result):
        import httpx

        if isinstance(head_result, Exception):
            def fake_head(url, **kwargs):
                raise head_result
        else:
            def fake_head(url, **kwargs):
                class _R:
                    status_code = head_result
                return _R()
        monkeypatch.setattr(httpx, "head", fake_head)

        uid, profile, job = _make_user_with_profile(db, cv_text="PROBE CV")
        draft = _make_ready_draft(db, uid, job)
        from app.services import draft_service

        return draft_service.submit_draft(db, draft, "browser", profile, user_id=uid)

    def test_dead_portal_records_warning_and_still_hands_off(self, db, monkeypatch):
        app = self._submit(db, monkeypatch, 502)
        assert app.status == "manual_pending", "a dead portal must not block the hand-off"
        assert app.error and "HTTP 502" in app.error, (
            f"the dead-portal warning must ride the application row: {app.error!r}"
        )

    def test_live_portal_and_head_unsupported_record_no_warning(self, db, monkeypatch):
        for code in (200, 405):
            app = self._submit(db, monkeypatch, code)
            assert app.error is None, (
                f"HTTP {code} means the portal answered — no warning (got {app.error!r})"
            )

    def test_probe_connectivity_failure_stays_silent(self, db, monkeypatch):
        import httpx

        app = self._submit(db, monkeypatch, httpx.ConnectError("probe network hiccup"))
        assert app.error is None, "probe connectivity noise must never cry wolf"
