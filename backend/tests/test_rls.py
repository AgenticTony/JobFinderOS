"""MIG-WO3: the RLS trap tests — Postgres only (SQLite never enables RLS;
the migration no-ops there and RequestSessionLocal's listener does too).

The reviewer-specified core (MIGRATION.md): two tenants, RLS on, a
deliberately UNSCOPED SELECT from a REQUEST session returns ZERO rows
when no identity is propagated, and only the caller's rows when one is.
Revert-checked: dropping the propagation listener must make the
unscoped-with-sub SELECT see everything it should not (the test goes
red), proving the listener is load-bearing, not decorative.

The service side: SessionLocal (the worker/scheduler/pipeline factory)
sees ALL rows regardless of context — background jobs must never see
RLS errors.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.database import (
    RequestSessionLocal,
    SessionLocal,
    _propagate_request_jwt,
    engine,
)
from app.models import JobPosting, MatchResult, Profile, User

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="RLS is Postgres-only — the migration and listener no-op on SQLite",
)


@pytest.fixture(scope="module", autouse=True)
def schema():
    """Boot the schema the way production does (init_db → alembic head —
    which includes the MIG-WO3 migration under test: RLS, policies,
    grants, and the vanilla-PG auth.uid() shim)."""
    from app.core.database import init_db

    init_db()
    yield


@pytest.fixture()
def db():
    """SERVICE session (the documented seeding path — RLS-blind)."""
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def two_tenants(db):
    """Two users, each with a profile, a match on a shared job — committed
    through the SERVICE session (the documented seeding path)."""
    users = []
    for n in range(2):
        uid = uuid.uuid4()
        email = f"rls{n}-{uuid.uuid4().hex[:6]}@test.example"
        db.add(User(id=uid, email=email, hashed_password="supabase-auth"))
        db.flush()
        db.add(Profile(user_id=uid, is_active=1, remote_ok=1, remote_only=0,
                       include_remote=0, onboarded=0))
        job = JobPosting(
            source="manual", source_id=uuid.uuid4().hex[:8],
            title=f"RLS job {n}", company="X",
            url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
        )
        db.add(job)
        db.flush()
        db.add(MatchResult(user_id=uid, job_id=job.id, score=70 + n,
                           tier="good_match"))
        users.append(uid)
    db.commit()
    return users


def _unscoped_count(table: str, sub=None) -> int:
    """A deliberately UNSCOPED SELECT through a REQUEST session — the
    exact bug class RLS exists to catch (a forgotten user_id filter).
    sub=None models a session the auth dependency never stamped."""
    with RequestSessionLocal() as session:
        if sub is not None:
            session.info["rls_sub"] = sub  # what get_authenticated_user sets
        return session.execute(text(f"SELECT count(*) FROM {table}")).scalar()


class TestRequestSessionIsolation:
    def test_no_identity_sees_nothing(self, two_tenants):
        """Fail-closed: a request session with NO propagated sub runs as
        authenticated with auth.uid() NULL — the IS NOT NULL guard must
        filter EVERY row, even from a query with no user_id filter."""
        assert _unscoped_count("profiles") == 0
        assert _unscoped_count("match_results") == 0
        assert _unscoped_count("users") == 0

    def test_identity_sees_only_its_own_rows(self, two_tenants):
        a, _b = two_tenants
        # rls_sub is what the VERIFIED dependency stamps on the session
        # (MIG-WO3 review 2026-09-09: session.info, never a contextvar —
        # threadpool context copies don't cross dependencies).
        assert _unscoped_count("profiles", sub=a) == 1
        assert _unscoped_count("match_results", sub=a) == 1
        assert _unscoped_count("users", sub=a) == 1

    def test_service_session_sees_everything(self, two_tenants):
        """The worker/scheduler half of the two-session story: the
        SERVICE factory is not RLS-scoped — background jobs operate the
        shared pool and every user's rows without RLS errors."""
        with SessionLocal() as session:
            assert session.execute(text("SELECT count(*) FROM profiles")).scalar() >= 2
            assert session.execute(
                text("SELECT count(*) FROM match_results")).scalar() >= 2


class TestWriteIsolation:
    """The WITH CHECK half of the layer — review (2026-09-09): every
    read-side assertion is a SELECT count; nothing ever proved a write
    the policies should REFUSE is refused. A future edit narrowing the
    policies to FOR SELECT, or dropping WITH CHECK, would have left the
    suite green with cross-tenant writes permitted. These pin it."""

    def test_insert_for_another_tenant_is_refused(self, two_tenants):
        import pytest as _pytest
        from sqlalchemy.exc import DBAPIError

        a, b = two_tenants
        with RequestSessionLocal() as session:
            session.info["rls_sub"] = a
            job_id = session.execute(
                text("SELECT id FROM job_postings LIMIT 1")
            ).scalar()
            with _pytest.raises(DBAPIError, match="row-level security"):
                session.execute(
                    text(
                        "INSERT INTO match_results (user_id, job_id, score, "
                        "tier) VALUES (:u, :j, 50, 'fair_match')"
                    ),
                    {"u": b, "j": job_id},
                )
                session.commit()

    def test_update_of_another_tenants_row_affects_nothing(self, two_tenants):
        a, b = two_tenants
        with RequestSessionLocal() as session:
            session.info["rls_sub"] = a
            result = session.execute(
                text(
                    "UPDATE profiles SET full_name = 'hijacked' "
                    "WHERE user_id = :victim"
                ),
                {"victim": b},
            )
            session.commit()
            assert result.rowcount == 0, (
                "an authenticated session UPDATED another tenant's row — "
                "the USING half of the policy is not filtering writes"
            )
        # the victim's row is untouched
        with SessionLocal() as session:
            name = session.execute(
                text("SELECT full_name FROM profiles WHERE user_id = :v"),
                {"v": b},
            ).scalar()
            assert name is None


class TestListenerIsLoadBearing:
    def test_without_propagation_the_isolation_disappears(self, two_tenants):
        """Revert-check: the listener removed — the regression shape of
        someone deleting the after_begin hook or its dialect branch —
        the UNSCOPED request SELECT MUST leak rows (running as postgres,
        the owner). That leak is the proof the listener is load-bearing;
        restoring it must bring the isolation back."""
        from sqlalchemy import event as sa_event

        sa_event.remove(RequestSessionLocal, "after_begin", _propagate_request_jwt)
        try:
            leaked = _unscoped_count("profiles")
            assert leaked >= 2, (
                "removing the propagation listener changed nothing — RLS "
                "was already inert, so these tests prove nothing about it"
            )
        finally:
            sa_event.listen(RequestSessionLocal, "after_begin", _propagate_request_jwt)
        assert _unscoped_count("profiles") == 0, (
            "isolation did not return with the listener restored"
        )
