"""MIG-WO2 cutover remap — the write path only --plan ever exercised
before review (2026-09-08) found three blockers in it:

  1. INSERT-before-DELETE collides with UNIQUE(users.email)  → parking
  2. conn.begin() after SQLAlchemy autobegin raises           → fresh engine.begin()
  3. --reverse carried both                                   → shared _relocate_user

These tests run the REAL remap functions against a scratch Alembic
schema and pin the constraints that make the fixes load-bearing.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

OPS_SCRIPT = Path(__file__).resolve().parent.parent.parent / "ops" / "mig_wo2_cutover.py"


def _load_cutover_module():
    spec = importlib.util.spec_from_file_location("mig_wo2_cutover", OPS_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def scratch_db(tmp_path):
    """A throwaway SQLite database with the REAL Alembic schema —
    create_all hits the per-user-FK circular dependency, so the
    migration chain builds it exactly as production boots do."""
    from alembic.config import Config

    from alembic import command

    url = f"sqlite:///{tmp_path / 'cutover_remap.db'}"
    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield create_engine(url)
    # tmp_path cleanup removes the file


def _seed(conn, mod, email):
    """One user + one row in every user_id table; returns (old_id, rows)."""
    uid = str(uuid.uuid4())
    conn.execute(
        text("INSERT INTO users (id, email, hashed_password, is_active, "
             "is_superuser, is_verified, token_version, created_at) VALUES "
             "(:id, :email, 'old-hash', 1, 0, 1, 0, '2026-01-01')"),
        {"id": uid, "email": email})
    conn.execute(text("INSERT INTO profiles (user_id, is_active, remote_ok, "
                      "remote_only, include_remote, onboarded, created_at, "
                      "updated_at) VALUES (:u, 1, 1, 0, 0, 0, '2026-01-01', "
                      "'2026-01-01')"),
                 {"u": uid})
    conn.execute(text("INSERT INTO match_results (user_id, job_id, score, tier, "
                      "created_at, updated_at) VALUES (:u, 1, 80, 'good_match', "
                      "'2026-01-01', '2026-01-01')"),
                 {"u": uid})
    conn.execute(text("INSERT INTO ai_usage (user_id, kind, model, endpoint, "
                      "created_at) VALUES (:u, 'match', 'glm', '/m', '2026-01-01')"),
                 {"u": uid})
    return uid


class TestCutoverRelocate:
    def test_relocate_moves_everything_and_honors_the_email_unique(self, scratch_db):
        mod = _load_cutover_module()
        with scratch_db.begin() as conn:
            old_id = _seed(conn, mod, "moving@example.com")
        backup = None
        with scratch_db.connect() as conn:
            backup = mod.backup_users_rows(conn, [old_id])

        new_id = str(uuid.uuid4())
        with scratch_db.begin() as wconn:
            row = backup[old_id]
            moved = mod._relocate_user(
                wconn, from_id=old_id, to_id=new_id,
                insert={"id": new_id, "email": row["email"],
                        "hashed_password": "supabase-auth",
                        "is_active": row["is_active"],
                        "is_superuser": row["is_superuser"],
                        "is_verified": True, "display_name": row["display_name"],
                        "token_version": 0, "created_at": row["created_at"]})
        assert moved == 3  # profile + match + ai_usage

        with scratch_db.connect() as conn:
            users = conn.execute(text("SELECT id, email FROM users")).mappings().all()
            assert len(users) == 1
            assert str(users[0]["id"]) == new_id
            assert users[0]["email"] == "moving@example.com"
            for table in ("profiles", "match_results", "ai_usage"):
                owner = conn.execute(
                    text(f"SELECT DISTINCT user_id FROM {table}")).scalar()
                assert str(owner) == new_id, f"{table} not moved"

    def test_naive_insert_without_parking_violates_the_unique(self, scratch_db):
        """RED PROOF the parking in _relocate_user is load-bearing: the
        review's reproduced failure — insert the new row while the old
        one still owns the UNIQUE email — must raise here, forever."""
        from sqlalchemy.exc import IntegrityError

        mod = _load_cutover_module()
        with scratch_db.begin() as conn:
            _seed(conn, mod, "kept@example.com")
        with pytest.raises(IntegrityError):
            with scratch_db.begin() as wconn:
                wconn.execute(
                    text("INSERT INTO users (id, email, hashed_password, "
                         "is_active, is_superuser, is_verified, token_version, "
                         "created_at) VALUES (:id, 'kept@example.com', "
                         "'supabase-auth', 1, 0, 1, 0, '2026-01-01')"),
                    {"id": str(uuid.uuid4())})

    def test_roundtrip_forward_then_reverse_restores_the_original_row(
        self, scratch_db,
    ):
        """The documented rollback path, executed: forward remap, then
        reverse from the snapshot's users_backup — original id, email
        AND password hash come back."""
        mod = _load_cutover_module()
        with scratch_db.begin() as conn:
            old_id = _seed(conn, mod, "roundtrip@example.com")
        with scratch_db.connect() as conn:
            backup = mod.backup_users_rows(conn, [old_id])

        new_id = str(uuid.uuid4())
        with scratch_db.begin() as wconn:
            row = backup[old_id]
            mod._relocate_user(wconn, from_id=old_id, to_id=new_id, insert={
                "id": new_id, "email": row["email"],
                "hashed_password": "supabase-auth",
                "is_active": row["is_active"], "is_superuser": row["is_superuser"],
                "is_verified": True, "display_name": row["display_name"],
                "token_version": 0, "created_at": row["created_at"]})
        # reverse: back onto the original row contents
        with scratch_db.begin() as wconn:
            mod._relocate_user(wconn, from_id=new_id, to_id=old_id, insert={
                "id": old_id, "email": row["email"],
                "hashed_password": row["hashed_password"],
                "is_active": row["is_active"], "is_superuser": row["is_superuser"],
                "is_verified": row["is_verified"], "display_name": row["display_name"],
                "token_version": row["token_version"],
                "created_at": row["created_at"]})

        with scratch_db.connect() as conn:
            final = conn.execute(
                text("SELECT id, email, hashed_password FROM users")).mappings().one()
            assert str(final["id"]) == old_id
            assert final["email"] == "roundtrip@example.com"
            assert final["hashed_password"] == "old-hash", (
                "reverse must restore the ORIGINAL hash — the snapshot "
                "backup is the rollback of record"
            )
            owner = conn.execute(
                text("SELECT DISTINCT user_id FROM profiles")).scalar()
            assert str(owner) == old_id

    def test_autobegin_connection_rejects_explicit_begin(self, scratch_db):
        """RED PROOF for the fresh-connection fix: a connection that has
        already run SELECTs (autobegin) refuses conn.begin() — exactly
        why phase 2 uses engine.begin() on its own connection."""
        from sqlalchemy.exc import InvalidRequestError

        with scratch_db.connect() as conn:
            conn.execute(text("SELECT count(*) FROM users"))
            with pytest.raises(InvalidRequestError):
                conn.begin()
