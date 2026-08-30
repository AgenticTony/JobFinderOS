"""Delta-scrape tests — watermarks, published-after fetching, union hunts.

The delta system: every successful fetch of a (source, query, scope)
records a watermark; later fetches pass published-after = watermark −
24h overlap so hunts read exactly the new arrivals. A query or scope
never fetched before gets None → the deep backfill. Scheduled hunts
scrape the UNION of all users' contexts once per country.
"""

from datetime import timedelta

import pytest

from app.core.database import SessionLocal


@pytest.fixture()
def db():
    """Per-file session fixture (same shape as test_units/test_multiuser):
    clean per-user data between tests, schema stays — Alembic owns it."""
    from app.models import (
        AIUsage,
        Application,
        ApplicationDraft,
        JobPosting,
        MatchResult,
        Profile,
        ScrapeRun,
        ScrapeWatermark,
        SystemLock,
        User,
    )

    session = SessionLocal()
    # Ensure the schema exists (this file has no _client fixture to
    # trigger app startup) — create_all adds missing tables only.
    from app.core.database import engine
    from app.core.orm import Base

    Base.metadata.create_all(engine)
    for model in (Application, ApplicationDraft, MatchResult, Profile,
                  JobPosting, AIUsage, ScrapeRun, ScrapeWatermark, SystemLock,
                  User):
        session.query(model).delete()
    session.commit()
    yield session
    session.rollback()
    session.close()
    # Release pooled connections to the shared sqlite file — other
    # modules' fixtures DELETE the file between tests; an orphaned
    # pooled connection turns later writes into readonly errors.
    from app.core.database import engine
    engine.dispose()


def _ctx(queries, munis=None):
    return {
        "country": "SE",
        "queries": queries,
        "municipalities": munis or [],
        "languages": [],
        "remote_only": False,
        "include_remote": True,
    }


class TestWatermarkLifecycle:
    def test_no_watermark_means_backfill(self, db):
        from app.services.pipeline import delta_since_for

        assert delta_since_for(db, "jobtech", _ctx(["utvecklare"], ["Malmö"])) is None

    def test_watermark_yields_delta_with_overlap(self, db):
        from app.core.timeutil import utc_now
        from app.models import ScrapeWatermark
        from app.services.pipeline import DELTA_OVERLAP_HOURS, delta_since_for, set_watermarks

        ctx = _ctx(["utvecklare"], ["Malmö"])
        set_watermarks(db, "jobtech", ctx)
        since = delta_since_for(db, "jobtech", ctx)
        assert since is not None
        expected_max = utc_now() - timedelta(hours=DELTA_OVERLAP_HOURS)
        assert abs((since - expected_max).total_seconds()) < 30, (
            "delta cutoff = watermark minus the 24h overlap"
        )

    def test_new_query_under_known_scope_forces_backfill(self, db):
        """The pair key: adding a search term to an existing scope must
        deep-fetch that term's history, not just the last day."""
        from app.services.pipeline import delta_since_for, set_watermarks

        ctx = _ctx(["utvecklare"], ["Malmö"])
        set_watermarks(db, "jobtech", ctx)
        assert delta_since_for(db, "jobtech", ctx) is not None
        wider = _ctx(["utvecklare", "backend"], ["Malmö"])
        assert delta_since_for(db, "jobtech", wider) is None

    def test_new_scope_forces_backfill(self, db):
        """A new user in a new city: their municipality set has no
        watermark — the first fetch reads the full history."""
        from app.services.pipeline import delta_since_for, set_watermarks

        set_watermarks(db, "jobtech", _ctx(["utvecklare"], ["Malmö"]))
        assert delta_since_for(db, "jobtech", _ctx(["utvecklare"], ["Örebro"])) is None

    def test_scope_key_is_order_insensitive(self, db):
        from app.services.pipeline import delta_since_for, set_watermarks

        set_watermarks(db, "jobtech", _ctx(["utvecklare"], ["Malmö", "Lund"]))
        assert delta_since_for(db, "jobtech", _ctx(["utvecklare"], ["Lund", "Malmö"])) is not None


class TestJobtechDeltaParams:
    def _capture(self, monkeypatch, hits=None):
        """Stub httpx.get in the jobtech module; return captured params."""
        from app.services.scrapers import jobtech

        captured = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": (hits or [])[:1], "total": len(hits or [])}

        def fake_get(url, params=None, **kwargs):
            captured.append(params)
            return _Resp()

        monkeypatch.setattr(jobtech.httpx, "get", fake_get)
        return captured

    def test_delta_since_adds_published_after(self, monkeypatch):
        from datetime import datetime, timezone

        from app.services.scrapers import jobtech

        captured = self._capture(monkeypatch, hits=[])
        since = datetime(2026, 8, 29, 18, 0, tzinfo=timezone.utc)
        jobtech.JobtechScraper().fetch({**_ctx(["utvecklare"]), "delta_since": since})
        flat = [pair[1] for pair in captured[0] if pair[0] == "published-after"]
        assert flat == ["2026-08-29"], f"published-after must be the cutoff date: {captured[0]}"

    def test_backfill_omits_published_after(self, monkeypatch):
        from app.services.scrapers import jobtech

        captured = self._capture(monkeypatch, hits=[])
        jobtech.JobtechScraper().fetch({**_ctx(["utvecklare"]), "delta_since": None})
        assert all(
            p[0] != "published-after" for req in captured for p in req
        ), "backfill must not send a date cutoff"


class TestUnionContexts:
    def test_union_merges_users_per_country(self, db):
        from app.models import Profile, User
        from app.services.pipeline import build_union_contexts

        import uuid as _uuid

        for i, (country, munis, queries) in enumerate(
            [
                ("SE", '["Malmö", "Lund"]', '["utvecklare", "python"]'),
                ("SE", '["Stockholm"]', '["backend"]'),
                ("GB", '["Manchester"]', '["developer"]'),
            ]
        ):
            u = User(id=_uuid.uuid4(), email=f"u{i}-{i}@test.example", hashed_password="x")
            db.add(u)
            db.add(Profile(
                is_active=1, user_id=u.id, full_name="T", cv_file_name="cv.pdf",
                cv_text="dev", country=country, region=None,
                municipalities=munis, search_queries=queries, languages='["sv"]',
            ))
        db.commit()

        ctxs = {c["country"]: c for c in build_union_contexts(db)}
        assert set(ctxs) == {"SE", "GB"}, "one context per country"
        se = ctxs["SE"]
        assert sorted(se["municipalities"]) == ["Lund", "Malmö", "Stockholm"]
        assert sorted(se["queries"]) == ["backend", "python", "utvecklare"]
        assert "sv" in se["languages"]

    def test_no_profiles_no_contexts(self, db):
        from app.services.pipeline import build_union_contexts

        assert build_union_contexts(db) == []


class TestBackfillSchema:
    def test_backfill_defaults_false_and_accepts_true(self):
        from app.schemas.pipeline import PipelineRunRequest

        assert PipelineRunRequest().backfill is False
        assert PipelineRunRequest(backfill=True).backfill is True

    def test_backfill_is_not_a_cost_vector(self):
        """backfill spends API calls, not AI calls — the clamp that
        matters (max_matches) still bounds spend."""
        from app.core.config import settings
        from app.schemas.pipeline import PipelineRunRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            PipelineRunRequest(max_matches=settings.MAX_JOBS_PER_MATCH_RUN + 1)
