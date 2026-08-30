"""Radius-search tests — commute-zone fetching on JobTech.

When the profile sets search_radius_km and the first chosen
municipality resolves to a centroid: the jobtech fetch sends
position + position.radius INSTEAD of municipality codes (the API's
distance filter replaces the kommune list), the watermark scope key
includes the radius (a radius change re-backfills), and the store
skips the strict local gate (the API already geo-filtered). Where no
centroid resolves, everything falls back to municipality codes.
"""

from datetime import datetime, timezone

import pytest

from app.core.database import SessionLocal


@pytest.fixture()
def db():
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


def _ctx(munis, radius):
    return {
        "country": "SE",
        "queries": ["utvecklare"],
        "municipalities": munis,
        "search_radius_km": radius,
        "languages": [],
        "remote_only": False,
        "include_remote": False,
    }


class TestGeoResolution:
    def test_known_municipality_resolves(self):
        from app.services.geo import resolve_position

        lat, lon = resolve_position(["Malmö"])
        assert 55.4 < lat < 55.8 and 12.8 < lon < 13.2

    def test_first_resolvable_wins(self):
        from app.services.geo import resolve_position

        # Unknown first, known second: the resolvable one anchors
        assert resolve_position(["Nagonby", "Lund"]) == (55.704, 13.191)

    def test_unknown_returns_none(self):
        from app.services.geo import resolve_position

        assert resolve_position(["Nagonby"]) is None
        assert resolve_position([]) is None

    def test_radius_geo_active_gating(self):
        from app.services.geo import radius_geo_active

        assert radius_geo_active(_ctx(["Malmö"], 30)) is True
        assert radius_geo_active(_ctx(["Malmö"], 0)) is False, "radius off = exact mode"
        assert radius_geo_active(_ctx(["Nagonby"], 30)) is False, (
            "radius set but no centroid = fall back to municipality codes"
        )


class TestJobtechPositionParams:
    def _capture(self, monkeypatch):
        from app.services.scrapers import jobtech

        captured = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": []}

        def fake_get(url, params=None, **kwargs):
            captured.append(params)
            return _Resp()

        monkeypatch.setattr(jobtech.httpx, "get", fake_get)
        return captured

    def test_radius_sends_position_not_municipality(self, monkeypatch):
        from app.services.scrapers import jobtech

        captured = self._capture(monkeypatch)
        jobtech.JobtechScraper().fetch(_ctx(["Malmö", "Lund"], 30))
        req = dict(captured[0])
        assert req.get("position") == "55.605,13.0"
        assert req.get("position.radius") == 30
        assert "municipality" not in req, (
            "radius mode must REPLACE municipality codes — sending both "
            "would intersect them and defeat the radius"
        )

    def test_no_radius_keeps_municipality_codes(self, monkeypatch):
        """Exact mode unchanged: municipality codes via taxonomy."""
        from app.services.scrapers import jobtech

        codes = {"malmö": "0180", "lund": "1282"}
        monkeypatch.setattr(jobtech, "_MUNICIPALITY_CODES", codes)
        captured = self._capture(monkeypatch)
        jobtech.JobtechScraper().fetch(_ctx(["Malmö"], 0))
        req = captured[0]
        assert ("municipality", "0180") in req
        assert all(p[0] != "position" for p in req)


class TestRadiusScopeKey:
    def test_radius_joins_the_watermark_scope(self):
        from app.services.pipeline import _scope_key

        assert _scope_key(_ctx(["Malmö"], 0)) == "malmö"
        assert _scope_key(_ctx(["Malmö"], 30)) == "malmö|r30", (
            "a radius change is a new fetch scope — it must backfill, "
            "not delta-miss its new coverage"
        )

    def test_radius_change_forces_backfill(self, db):
        from app.services.pipeline import delta_since_for, set_watermarks

        set_watermarks(db, "jobtech", _ctx(["Malmö"], 0))
        assert delta_since_for(db, "jobtech", _ctx(["Malmö"], 0)) is not None
        assert delta_since_for(db, "jobtech", _ctx(["Malmö"], 30)) is None, (
            "the 30 km scope has no watermark — deep backfill"
        )
