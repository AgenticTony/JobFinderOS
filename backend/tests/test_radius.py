"""Radius-search tests — commute-zone fetching on JobTech.

When the profile sets search_radius_km and the first chosen
municipality resolves to a centroid: the jobtech fetch sends
position + position.radius INSTEAD of municipality codes (the API's
distance filter replaces the kommune list), the watermark scope key
includes the radius (a radius change re-backfills), and the store
skips the strict local gate (the API already geo-filtered). Where no
centroid resolves, everything falls back to municipality codes.
"""


import pytest

from app.core.database import SessionLocal


@pytest.fixture()
def db():
    # The shared sqlite scratch file may predate new columns
    # (create_all adds tables, never columns). Drop and recreate the
    # schema IN PLACE — never delete the file: other modules hold
    # pooled connections to its inode.
    from app.core.database import engine
    from app.core.orm import Base
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
    from tests.conftest import stamp_alembic_head

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # create_all rebuilt the HEAD-shaped schema but left no alembic_version
    # row — a later app boot in the same session (multiuser's TestClient ->
    # init_db) would misread the shape and re-run migrations against
    # existing tables (CircularDependencyError on sqlite, DuplicateTable on
    # postgres). Stamping head records what create_all actually built.
    stamp_alembic_head()
    session = SessionLocal()
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

    def test_strict_primary_anchor(self):
        """The anchor is the user's FIRST pick or nothing — silently
        substituting a resolvable later town would centre the commute
        zone elsewhere and exclude their own town entirely."""
        from app.services.geo import resolve_position

        assert resolve_position(["Nagonby", "Lund"]) is None
        assert resolve_position(["Lund", "Nagonby"]) == (55.704, 13.191)

    def test_geo_plan_shared_decision(self):
        from app.services.geo import effective_municipalities, geo_plan

        # Legacy single-field fallback feeds the SAME decision the
        # scraper and the store gate make — no divergence possible
        legacy_ctx = {"municipalities": [], "municipality": "Malmö",
                      "search_radius_km": 30}
        assert effective_municipalities(legacy_ctx) == ["Malmö"]
        assert geo_plan(legacy_ctx) == (55.605, 13.0, 30)
        # No radius / no anchor -> no plan (falls back to codes)
        assert geo_plan({"municipalities": ["Malmö"], "search_radius_km": 0}) is None
        assert geo_plan({"municipalities": ["Nagonby"], "search_radius_km": 30}) is None

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


class TestReducedRadiusGate:
    """The radius store gate waives ONLY the municipality clause —
    remote_only and WO-06 country routing still hold (review finding
    #1: the original full skip let a remote_only user's radius fetch
    store and AI-match on-site jobs)."""

    def _job(self, remote=False, location=None):
        from app.services.scrapers.base import NormalizedJob

        return NormalizedJob(
            source="jobtech", source_id="x1", title="Dev", company="Acme",
            url="https://x/1", description="d", remote=remote, location=location,
        )

    def test_remote_only_still_drops_on_site(self):
        from app.services.pipeline import passes_radius_gate

        ctx = {**_ctx(["Malmö"], 30), "remote_only": True}
        assert passes_radius_gate(self._job(remote=False), ctx) is False
        assert passes_radius_gate(self._job(remote=True), ctx) is True

    def test_country_routing_still_blocks_foreign(self):
        from app.services.pipeline import passes_radius_gate

        ctx = _ctx(["Malmö"], 30)  # SE user
        assert passes_radius_gate(
            self._job(remote=True, location="New York, USA"), ctx) is False

    def test_in_radius_on_site_job_passes(self):
        from app.services.pipeline import passes_radius_gate

        ctx = _ctx(["Malmö"], 30)
        assert passes_radius_gate(
            self._job(remote=False, location="Lund, Skåne län"), ctx) is True


class TestGateWiringInsideScrapeSource:
    """The WIRING, not just the helper: scrape_source must select the
    reduced gate for geo-filtered jobtech runs (review finding — the
    helper tests stay green if the caller reverts to skipping the gate
    entirely, which is how the original bug shipped)."""

    def _stub_fetch(self, monkeypatch, jobs):
        from app.services.scrapers import jobtech

        monkeypatch.setattr(jobtech.JobtechScraper, "fetch", lambda self, ctx: jobs)

    def _job(self, remote=False, location=None, source_id="x1"):
        from app.services.scrapers.base import NormalizedJob

        return NormalizedJob(
            source="jobtech", source_id=source_id, title="Dev", company="Acme",
            url=f"https://x/{source_id}", description="d",
            remote=remote, location=location,
        )

    def test_remote_only_radius_fetch_drops_on_site_at_store(self, db, monkeypatch):
        from app.models import JobPosting
        from app.services.pipeline import scrape_source

        self._stub_fetch(monkeypatch, [self._job(remote=False, location="Lund, Skåne län")])
        ctx = {**_ctx(["Malmö"], 30), "remote_only": True, "queries": ["dev"]}
        run = scrape_source(db, "jobtech", ctx)
        assert run.status == "completed"
        assert db.query(JobPosting).count() == 0, (
            "remote_only + radius: the reduced gate must still drop the "
            "on-site job at store time — this is the wiring that shipped broken"
        )

    def test_radius_fetch_stores_in_radius_on_site_job(self, db, monkeypatch):
        from app.models import JobPosting
        from app.services.pipeline import scrape_source

        self._stub_fetch(monkeypatch, [self._job(remote=False, location="Lund, Skåne län", source_id="x2")])
        run = scrape_source(db, "jobtech", {**_ctx(["Malmö"], 30), "queries": ["dev"]})
        assert run.status == "completed"
        assert db.query(JobPosting).count() == 1, (
            "a neighbouring-kommun ad inside the radius must store — the "
            "municipality clause is the ONLY thing waived"
        )

    def test_scraper_selects_full_gate_without_radius(self, db, monkeypatch):
        from app.models import JobPosting
        from app.services.pipeline import scrape_source

        self._stub_fetch(monkeypatch, [self._job(remote=False, location="Stockholm", source_id="x3")])
        run = scrape_source(db, "jobtech", {**_ctx(["Malmö"], 0), "queries": ["dev"]})
        assert run.status == "completed"
        assert db.query(JobPosting).count() == 0, (
            "no radius: the FULL gate applies and out-of-municipality ads "
            "never store"
        )


class TestRadiusScopeKey:
    def test_radius_joins_the_watermark_scope(self):
        from app.services.pipeline import _scope_key

        assert _scope_key(_ctx(["Malmö"], 0)) == "malmö"
        assert _scope_key(_ctx(["Malmö"], 30)) == "malmö|r30", (
            "a radius change is a new fetch scope — it must backfill, "
            "not delta-miss its new coverage"
        )

    def test_unanchorable_radius_leaves_scope_key_alone(self):
        """Vellinge has no centroid: the request is byte-identical to
        radius=0, so the watermark must not be invalidated (review
        finding #5: spurious deep backfills returning nothing new)."""
        from app.services.pipeline import _scope_key

        assert _scope_key(_ctx(["Vellinge"], 0)) == "vellinge"
        assert _scope_key(_ctx(["Vellinge"], 30)) == "vellinge"

    def test_radius_change_forces_backfill(self, db):
        from app.services.pipeline import delta_since_for, set_watermarks

        set_watermarks(db, "jobtech", _ctx(["Malmö"], 0))
        assert delta_since_for(db, "jobtech", _ctx(["Malmö"], 0)) is not None
        assert delta_since_for(db, "jobtech", _ctx(["Malmö"], 30)) is None, (
            "the 30 km scope has no watermark — deep backfill"
        )
