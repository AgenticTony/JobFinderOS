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


class TestLiveSchemaCarriesTheLayer:
    """MIG-WO3 review round 2 (2026-09-09): nothing anywhere asserted
    the LIVE schema has RLS enabled with the own_rows policy — the
    metadata drift test compares Python lists, and conftest re-applies
    the layer to test databases, so production could silently diverge
    while the suite stayed green. Audit round 3 strengthened this to
    EVERY public table: the four service tables carry Supabase's default
    anon grants, and the anon key is public by design."""

    def test_every_public_table_has_rls_and_identity_tables_the_policy(self):
        from app.core.rls_sql import ALL_RLS_TABLES

        with engine.connect() as conn:
            not_relayed = conn.execute(
                text(
                    "SELECT relname FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "AND NOT c.relrowsecurity"
                )
            ).scalars().all()
            assert not not_relayed, (
                f"tables without ENABLE ROW LEVEL SECURITY after init_db: "
                f"{not_relayed} — with Supabase's default grants the anon "
                "key (published to every browser) reaches them via the "
                "Data API"
            )
            for table in ALL_RLS_TABLES:
                policies = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies "
                        "WHERE schemaname = 'public' AND tablename = :t "
                        "AND policyname = 'own_rows'"
                    ),
                    {"t": table},
                ).scalar()
                assert policies == 1, (
                    f"{table} has no own_rows policy after init_db — "
                    "RLS without a policy blocks even the owner's reads; "
                    "with grants but no policy it leaks"
                )

    def test_anon_role_holds_no_table_grants(self):
        """Security Advisor follow-through (2026-09-09). Review made the
        original version honest: (a) provision the adversary — seed
        Supabase-shaped anon grants first, or the revoke has nothing to
        remove and the test passes vacuously on CI's vanilla PG;
        (b) assert via aclexplode on pg_class.relacl —
        information_schema.role_table_grants only shows grants where
        the grantor is a currently-enabled role, so a supabase_admin
        grant is invisible to a postgres connection (the same blind
        spot that scopes REVOKE itself)."""
        with engine.begin() as conn:
            # Seed the adversary: recreate Supabase's creation-time
            # default, grantor postgres
            conn.execute(text(
                "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE "
                "ON profiles, job_postings TO anon"))
        from app.core.rls_sql import ensure_rls

        with engine.begin() as conn:
            ensure_rls(conn)
        with engine.connect() as conn:
            leaked = conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace, "
                    "aclexplode(c.relacl) a "
                    "JOIN pg_roles r ON r.oid=a.grantee "
                    "WHERE n.nspname='public' AND r.rolname='anon'"
                )
            ).scalars().all()
            assert not leaked, (
                f"anon still holds grants on {leaked} — one accidental "
                "DISABLE ROW LEVEL SECURITY away from full exposure "
                "(and TRUNCATE is not governed by RLS at all)"
            )

    def test_no_anon_default_privilege_survives(self):
        """The future-tables half of the revocation (review: it had NO
        assertion — the neighbouring default-privileges test counts
        entries with no grantee filter and stays green regardless).
        Assert via aclexplode(defaclacl) that NO grantor's public-
        schema default ACL grants anon anything."""
        with engine.connect() as conn:
            n = conn.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl d "
                    "JOIN pg_namespace ns ON ns.oid=d.defaclnamespace, "
                    "aclexplode(d.defaclacl) a "
                    "JOIN pg_roles r ON r.oid=a.grantee "
                    "WHERE ns.nspname='public' AND r.rolname='anon'"
                )
            ).scalar()
            assert n == 0, (
                f"{n} anon default-privilege entries survive — the next "
                "table created under that grantor re-grants anon full DML"
            )

    def test_table_created_now_gets_no_anon_grants(self):
        """Ground truth for the role-scoping blind spot (review, 2026-09-09):
        ALTER DEFAULT PRIVILEGES rewrites only the executing role's entry,
        and stock Supabase installs a supabase_admin-grantor entry too.
        The question that actually matters: does a table created by OUR
        migration role (postgres) arrive clean? Probe it directly."""
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS __anon_probe(id int)"))
            try:
                n = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class c "
                        "JOIN pg_namespace ns ON ns.oid=c.relnamespace, "
                        "aclexplode(c.relacl) a "
                        "JOIN pg_roles r ON r.oid=a.grantee "
                        "WHERE ns.nspname='public' "
                        "AND c.relname='__anon_probe' AND r.rolname='anon'"
                    )
                ).scalar()
            finally:
                conn.execute(text("DROP TABLE __anon_probe"))
        assert n == 0, (
            "a table created right now, as postgres (our migration role), "
            "arrives with anon grants — future migrations re-open the "
            "exposure the revocation closed"
        )

    def test_future_table_default_privileges_exist(self):
        """The default privileges are what make ensure_rls-per-boot
        necessary (they pre-grant future tables); assert they are
        actually installed, so the fail-open precondition is at least
        visible — paired with the per-boot re-assertion in init_db."""
        with engine.connect() as conn:
            has_default = conn.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl d "
                    "JOIN pg_namespace n ON n.oid = d.defaclnamespace "
                    "WHERE n.nspname = 'public'"
                )
            ).scalar()
            assert has_default >= 1, (
                "no default privileges in public — future tables will not "
                "be pre-granted; if this is intentional, the per-boot "
                "ensure_rls in init_db covers them anyway"
            )


class TestMultiTransactionRequests:
    """Audit round 3 residual: the RLS identity rides session.info —
    which must survive a route that COMMITS mid-request and opens a
    second transaction from the handler's context. The PG suite covers
    this incidentally; this is the deliberate trap (the audit's PUT
    /profile/me shape, second transaction, identity-less = the
    ObjectDeletedError 500)."""

    def test_committing_route_keeps_identity_across_transactions(
        self, two_tenants
    ):
        from fastapi.testclient import TestClient

        from app.main import app
        from tests.auth_helpers import mint_token

        a, _b = two_tenants
        token = mint_token(a, f"rls-mt-{a.hex[:6]}@test.example")
        with TestClient(app) as client:
            r = client.put(
                "/api/v1/profile/me",
                json={"full_name": "Second Txn Name"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, (
                f"{r.status_code}: {r.text[:200]} — a committing route "
                "lost its RLS identity on the second transaction (the "
                "ObjectDeletedError/CORS-less-500 shape)"
            )
        with SessionLocal() as session:
            name = session.execute(
                text("SELECT full_name FROM profiles WHERE user_id = :u"),
                {"u": a},
            ).scalar()
        assert name == "Second Txn Name"


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
