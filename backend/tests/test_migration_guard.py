"""WO-25 — migration drift guard.

The 2026-09-28 outage: an unmerged branch's build ran `upgrade head`
against production and stamped `d94f2a6c8e1b`; every main-built boot
then died on "Can't locate revision". These tests pin the three answers:
refuse a branch build's migration (prevent), fail with a diagnosis on an
unknown stamp (explain), and keep the graph single-headed.

Integration tests migrate a THROWAWAY SQLite file in tmp_path — never the
suite database conftest owns.
"""

import inspect
import textwrap
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

from alembic import command
from app.core import database
from app.core.migration_guard import (
    MigrationGuardError,
    assert_may_migrate,
    assert_single_head,
    deploy_identity,
    revision_state,
)

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# The WO-19b migration adds profiles.work_rights on top of its parent.
HEAD = "d94f2a6c8e1b"
PARENT = "b7e2d4f8a1c3"
FOREIGN = "deadbeef0000"  # a revision no branch of this repo ever had

RENDER_BRANCH = {"RENDER": "true", "RENDER_GIT_BRANCH": "feat/x",
                 "RENDER_GIT_COMMIT": "abc1234def"}
RENDER_MAIN = {"RENDER": "true", "RENDER_GIT_BRANCH": "main"}
RENDER_NO_BRANCH = {"RENDER": "true"}
LOCAL: dict = {}


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def scratch_db(tmp_path):
    """A throwaway SQLite DB migrated to PARENT (one pending revision)."""
    url = f"sqlite:///{tmp_path / 'wo25.db'}"
    cfg = _cfg(url)
    command.upgrade(cfg, PARENT)
    eng = create_engine(url)
    yield cfg, eng
    eng.dispose()


def _stamp(eng) -> list[str]:
    with eng.connect() as c:
        return [r[0] for r in c.execute(text("SELECT version_num FROM alembic_version"))]


def _profile_columns(eng) -> set[str]:
    return {c["name"] for c in sa_inspect(eng).get_columns("profiles")}


# --- revision_state: the arithmetic the guard stands on -------------------

class TestRevisionState:
    def test_repo_head_is_what_this_file_assumes(self):
        # If a new migration lands, bump HEAD/PARENT here — the integration
        # tests below need "PARENT has exactly one pending revision".
        assert _script().get_heads() == [HEAD]
        assert _script().get_revision(HEAD).down_revision == PARENT

    def test_at_head_nothing_pending(self):
        s = revision_state(_script(), (HEAD,))
        assert s.unknown == () and s.pending == ()

    def test_one_behind_one_pending(self):
        s = revision_state(_script(), (PARENT,))
        assert s.pending == (HEAD,)

    def test_fresh_database_everything_pending(self):
        s = revision_state(_script(), ())
        assert HEAD in s.pending and len(s.pending) > 5

    def test_foreign_stamp_is_unknown(self):
        s = revision_state(_script(), (FOREIGN,))
        assert s.unknown == (FOREIGN,) and s.pending == ()


# --- assert_may_migrate: the branch rule (AC 1-5) --------------------------

class TestBranchRule:
    def _pending(self):
        return revision_state(_script(), (PARENT,))

    def _up_to_date(self):
        return revision_state(_script(), (HEAD,))

    def test_branch_build_with_pending_refused(self):
        with pytest.raises(MigrationGuardError) as e:
            assert_may_migrate(self._pending(), RENDER_BRANCH, "main")
        msg = str(e.value)
        assert "'feat/x'" in msg and HEAD in msg and "'main'" in msg

    def test_branch_build_with_nothing_pending_boots(self):
        assert_may_migrate(self._up_to_date(), RENDER_BRANCH, "main")

    def test_main_build_migrates(self):
        assert_may_migrate(self._pending(), RENDER_MAIN, "main")

    def test_missing_branch_var_fails_closed(self):
        with pytest.raises(MigrationGuardError, match="unknown branch"):
            assert_may_migrate(self._pending(), RENDER_NO_BRANCH, "main")

    def test_empty_branch_var_fails_closed(self):
        env = {"RENDER": "true", "RENDER_GIT_BRANCH": ""}
        with pytest.raises(MigrationGuardError, match="unknown branch"):
            assert_may_migrate(self._pending(), env, "main")

    def test_off_render_rule_does_not_apply(self):
        # Local dev / CI: layer 2 (the drift job) covers laptop runs.
        assert_may_migrate(self._pending(), LOCAL, "main")
        assert_may_migrate(self._pending(), {"RENDER_GIT_BRANCH": "feat/x"}, "main")

    def test_configured_branch_is_respected(self):
        env = {"RENDER": "true", "RENDER_GIT_BRANCH": "release"}
        assert_may_migrate(self._pending(), env, "release")
        with pytest.raises(MigrationGuardError):
            assert_may_migrate(self._pending(), RENDER_MAIN, "release")

    def test_unknown_stamp_fatal_even_off_render(self):
        state = revision_state(_script(), (FOREIGN,))
        for env in (LOCAL, RENDER_MAIN, RENDER_BRANCH):
            with pytest.raises(MigrationGuardError):
                assert_may_migrate(state, env, "main")


# --- _guarded_upgrade on a real migrated DB: assert on the SCHEMA ----------

class TestGuardedUpgradeIntegration:
    def test_branch_build_applies_nothing(self, scratch_db):
        cfg, eng = scratch_db
        with eng.connect() as conn, pytest.raises(MigrationGuardError):
            database._guarded_upgrade(cfg, conn, env=RENDER_BRANCH)
        # The outbound artifact is the schema: untouched.
        assert _stamp(eng) == [PARENT], (
            "a branch build moved the production stamp — the 2026-09-28 outage")
        assert "work_rights" not in _profile_columns(eng)

    def test_main_build_applies_pending(self, scratch_db):
        cfg, eng = scratch_db
        with eng.connect() as conn:
            database._guarded_upgrade(cfg, conn, env=RENDER_MAIN)
        assert _stamp(eng) == [HEAD]
        assert "work_rights" in _profile_columns(eng)

    def test_local_unaffected(self, scratch_db):
        cfg, eng = scratch_db
        with eng.connect() as conn:
            database._guarded_upgrade(cfg, conn, env=LOCAL)
        assert _stamp(eng) == [HEAD]

    def test_unknown_stamp_diagnosed_not_hashed(self, scratch_db):
        cfg, eng = scratch_db
        with eng.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num = :v"),
                      {"v": FOREIGN})
        with eng.connect() as conn, pytest.raises(MigrationGuardError) as e:
            database._guarded_upgrade(cfg, conn, env=LOCAL)
        msg = str(e.value)
        assert FOREIGN in msg and HEAD in msg
        assert "AHEAD of this build" in msg and "another branch" in msg
        assert _stamp(eng) == [FOREIGN]


# --- structural: init_db can't bypass the guard ----------------------------

class TestInitDbRoutesThroughGuard:
    def test_no_bare_upgrade_in_init_db(self):
        src = inspect.getsource(database.init_db)
        assert "command.upgrade(" not in src, (
            "init_db calls alembic directly — the WO-25 guard is bypassed")
        assert src.count("_guarded_upgrade(") == 3  # postgres, fresh, legacy

    def test_guarded_upgrade_checks_before_upgrading(self):
        src = inspect.getsource(database._guarded_upgrade)
        assert src.index("assert_may_migrate(") < src.index("command.upgrade(")


# --- single head -----------------------------------------------------------

def _write_rev(versions: Path, rev: str, down: str | None) -> None:
    (versions / f"{rev}.py").write_text(textwrap.dedent(f"""
        revision = {rev!r}
        down_revision = {down!r}
        branch_labels = None
        depends_on = None
        def upgrade(): pass
        def downgrade(): pass
    """))


class TestSingleHead:
    def test_repo_graph_has_one_head(self):
        assert assert_single_head(_script()) == HEAD

    def test_forked_graph_rejected(self, tmp_path):
        versions = tmp_path / "versions"
        versions.mkdir()
        _write_rev(versions, "aaaa00000001", None)
        _write_rev(versions, "bbbb00000002", "aaaa00000001")
        _write_rev(versions, "cccc00000003", "aaaa00000001")  # second child
        with pytest.raises(MigrationGuardError, match="2 heads"):
            assert_single_head(ScriptDirectory(str(tmp_path)))


# --- boot identity log -----------------------------------------------------

class TestDeployIdentity:
    def test_render_identity(self):
        assert deploy_identity(RENDER_BRANCH) == "feat/x@abc1234"

    def test_render_missing_vars_still_says_so(self):
        assert deploy_identity(RENDER_NO_BRANCH) == "<unknown branch>@<unknown commit>"

    def test_local_none(self):
        assert deploy_identity(LOCAL) is None


# --- layer 2: the CI drift script (scripts/check_migration_drift.py) -------

def _drift():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_migration_drift",
        Path(__file__).resolve().parent.parent / "scripts" / "check_migration_drift.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDriftScript:
    def test_no_url_skips_but_still_checks_heads(self):
        ok, msg = _drift().check(None)
        assert ok and "SKIPPED" in msg and HEAD in msg

    def test_at_head_passes(self, scratch_db):
        cfg, eng = scratch_db
        command.upgrade(cfg, "head")
        ok, msg = _drift().check(cfg.get_main_option("sqlalchemy.url"))
        assert ok and "at head" in msg

    def test_behind_head_passes_with_pending_note(self, scratch_db):
        cfg, _ = scratch_db
        ok, msg = _drift().check(cfg.get_main_option("sqlalchemy.url"))
        assert ok and "1 pending" in msg and HEAD in msg

    def test_unknown_stamp_fails_naming_it(self, scratch_db):
        cfg, eng = scratch_db
        with eng.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num = :v"),
                      {"v": FOREIGN})
        ok, msg = _drift().check(cfg.get_main_option("sqlalchemy.url"))
        assert not ok, "production is ahead of main and the drift job passed"
        assert FOREIGN in msg and "AHEAD of this build" in msg

    def test_zero_rows_fails_closed(self, scratch_db):
        # Under RLS a policy-less reader sees an EMPTY alembic_version —
        # that must never read as "fresh database, behind head".
        cfg, eng = scratch_db
        with eng.begin() as c:
            c.execute(text("DELETE FROM alembic_version"))
        ok, msg = _drift().check(cfg.get_main_option("sqlalchemy.url"))
        assert not ok and "ZERO rows" in msg and "RLS" in msg

    def test_unreadable_target_fails(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'empty.db'}"  # no alembic_version table
        ok, msg = _drift().check(url)
        assert not ok and "cannot read alembic_version" in msg

    def test_url_credentials_never_printed(self, tmp_path):
        url = "postgresql://reader:s3cret-pw@127.0.0.1:1/postgres"
        ok, msg = _drift().check(url)
        assert not ok and "s3cret-pw" not in msg

    def test_forked_graph_fails_before_any_db_read(self, tmp_path):
        versions = tmp_path / "versions"
        versions.mkdir()
        _write_rev(versions, "aaaa00000001", None)
        _write_rev(versions, "bbbb00000002", "aaaa00000001")
        _write_rev(versions, "cccc00000003", "aaaa00000001")
        ok, msg = _drift().check("sqlite://", script=ScriptDirectory(str(tmp_path)))
        assert not ok and "single head" in msg

    def test_locate_revision_finds_the_introducing_commit(self):
        import subprocess

        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True).stdout.strip() == "true"
        where = _drift().locate_revision(HEAD)
        if not shallow:  # CI's default checkout is depth-1: history absent
            assert where and where.startswith("introduced in "), where
        assert _drift().locate_revision("zzzz-not-a-rev-zzzz") is None
