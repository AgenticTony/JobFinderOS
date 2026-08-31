"""PIPE-15 / PIPE-16 scope-gate tests.

PIPE-15: scheduled hunts build UNION contexts that pin region=None and
never set search_radius_km — the cron path (06:00/18:00) applied the
strict gate only and never radius-fetched, so a "Malmö + 30km" user saw
no neighbouring-kommun jobs and a region-only user (Scotland, no
municipality) got no local on-site jobs at all. The fix: per-anchor
radius contexts and region-carrying contexts emitted ALONGSIDE the
union (never a max-radius union — that would mis-center geo_plan's
municipalities[0] anchor).

PIPE-16: matching had no location/remote/country gate, so union-stored
remote jobs entered EVERY strictly-local user's AI window. The live
repro (2026-08-30): Bob, a strictly-local London lead backend engineer,
spent ALL FOUR first-hunt evaluation slots on remote marketing/intern
ads — each permanently dismissed after the AI call. The fix: the
INGEST gate mirrored at match time, before any AI slot is spent.
"""

import uuid

import pytest  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402


@pytest.fixture()
def db():
    """Per-file session fixture (same shape as test_delta/test_radius):
    clean per-user data between tests, schema stays — Alembic owns it."""
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
    from app.core.database import engine

    engine.dispose()


def _onboarded_user(db, *, country, region=None, municipality=None,
                    municipalities=None, radius=None, include_remote=False,
                    remote_only=False, queries='["utvecklare"]'):
    """An onboarded profile row (the wizard output) plus its user."""
    from app.models import Profile, User

    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"sg-{uid.hex[:10]}@test.example",
                hashed_password="test-only"))
    db.flush()
    db.add(Profile(
        is_active=1, user_id=uid, full_name="Scope Tester",
        cv_file_name="cv.pdf", cv_text="developer python",
        country=country, region=region, municipality=municipality,
        municipalities=municipalities, search_radius_km=radius,
        include_remote=1 if include_remote else 0,
        remote_only=1 if remote_only else 0,
        search_queries=queries, languages='[]',
    ))
    db.commit()
    return uid


def _job_row(db, *, source="manual", remote=0, location=None, title="Dev",
             description="A real role with a real description."):
    from app.models import JobPosting

    j = JobPosting(
        source=source, source_id=str(uuid.uuid4())[:8], title=title,
        company="Acme", url=f"https://x/{uuid.uuid4().hex[:6]}", status="new",
        remote=remote, location=location, description=description,
    )
    db.add(j)
    db.commit()
    return j


def _fake_ai(monkeypatch, score=80):
    """A scripted AI service that records every job it is asked about.

    One EVALUATION (one AI slot) can make up to 3 model calls under the
    sampling policy, so the budget assertions count DISTINCT jobs, not
    raw calls — the same thing the matcher's `evaluated` counter counts.
    Returns an object with .jobs (titles, in evaluation order) and
    .calls (raw model-call count).
    """
    from app.services import matcher_service
    from app.services.ai_service import AIService

    seen = {"jobs": [], "calls": []}

    def fake_match(profile_context, cv_text, job_description):
        title = job_description.split("\n")[0].replace("Title: ", "")
        if title not in seen["jobs"]:
            seen["jobs"].append(title)
        seen["calls"].append(title)
        return {"score": score, "reasoning": "ok", "recommendation": "apply",
                "confidence": "high", "matched_skills": ["Python"],
                "missing_skills": [], "transferable_skills": []}

    svc = AIService.__new__(AIService)
    svc.model = "glm-test"
    svc.match_job = fake_match
    monkeypatch.setattr(matcher_service, "ai_service_available", lambda: True)
    monkeypatch.setattr(matcher_service, "get_ai_service", lambda: svc)
    return seen


# ---------------------------------------------------------------- PIPE-15

class TestScheduledRadiusContexts:
    """A radius user's scheduled hunt must include a context anchored on
    THEIR geo_plan anchor with THEIR radius — not a max-radius union."""

    def test_radius_user_gets_per_anchor_context(self, db):
        from app.services.geo import geo_plan
        from app.services.pipeline import build_union_contexts

        _onboarded_user(db, country="SE", municipalities='["Malmö", "Lund"]',
                        radius=30)

        ctxs = build_union_contexts(db)
        se = [c for c in ctxs if c["country"] == "SE"]
        radius_ctxs = [c for c in se if c.get("search_radius_km")]
        assert len(radius_ctxs) == 1, (
            f"expected exactly one per-anchor radius context alongside the "
            f"union, got {[{k: c.get(k) for k in ('search_radius_km', 'municipalities')} for c in se]}"
        )
        rc = radius_ctxs[0]
        assert geo_plan(rc) == (55.605, 13.0, 30), (
            "the radius context must anchor on the USER'S primary town "
            "(municipalities[0]), not the union's — geo_plan mis-centered "
            "would exclude the user's own kommun"
        )
        # The union context still exists (query breadth for non-radius users)
        union = [c for c in se if not c.get("search_radius_km")]
        assert len(union) == 1
        assert sorted(union[0]["municipalities"]) == ["Lund", "Malmö"]

    def test_radius_context_feeds_jobtech_as_position(self, db, monkeypatch):
        """The composition the scheduled hunt actually performs:
        build_union_contexts -> JobtechScraper.fetch. The radius context
        must reach the API as position + position.radius (the commute-zone
        fetch), exactly like the per-user API path."""
        from app.services.pipeline import build_union_contexts
        from app.services.scrapers import jobtech

        _onboarded_user(db, country="SE", municipalities='["Malmö"]', radius=30)

        captured = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": []}

        monkeypatch.setattr(jobtech.httpx, "get",
                            lambda url, params=None, **kw: (captured.append(params), _Resp())[1])

        ctxs = build_union_contexts(db)
        radius_ctx = next(c for c in ctxs if c.get("search_radius_km"))
        jobtech.JobtechScraper().fetch(radius_ctx)

        req = dict(captured[0])
        assert req.get("position") == "55.605,13.0", (
            f"radius context fetch must be position-anchored: {req}"
        )
        assert req.get("position.radius") == 30
        assert "municipality" not in req

    def test_same_anchor_radius_users_share_one_context(self, db):
        """The cost bound: 10 Malmö+30km users must share ONE radius
        context (dedupe on the fetch identity — anchor position + radius
        — not on the user)."""
        from app.services.pipeline import build_union_contexts

        for i in range(10):
            _onboarded_user(db, country="SE", municipalities='["Malmö", "Lund"]',
                            radius=30, queries=f'["query{i}"]')

        ctxs = build_union_contexts(db)
        radius_ctxs = [c for c in ctxs if c.get("search_radius_km")]
        assert len(radius_ctxs) == 1, (
            f"10 users with the same anchor+radius produced {len(radius_ctxs)} "
            f"radius contexts — each context is a full country-pack scrape, "
            f"the dedupe bound is the whole point (PIPE-15)"
        )
        assert sorted(radius_ctxs[0]["queries"]) == sorted(f"query{i}" for i in range(10)), (
            "the shared context must carry the union of the group's queries"
        )
        assert len(ctxs) == 2, "union + one shared radius context, nothing else"

    def test_distinct_anchors_are_distinct_contexts_but_capped(self, db):
        """Different (anchor, radius) groups are genuinely different
        fetches — but the number of EXTRA contexts per country is capped
        so a pathological user base (every user in a different kommun)
        cannot multiply the scrape budget."""
        from app.services.pipeline import (
            MAX_EXTRA_SCOPE_CONTEXTS_PER_COUNTRY,
            build_union_contexts,
        )

        towns = ["Malmö", "Lund", "Stockholm", "Göteborg", "Uppsala",
                 "Umeå", "Luleå", "Kalmar", "Varberg", "Ystad"]
        for t in towns:
            _onboarded_user(db, country="SE", municipalities=f'["{t}"]', radius=30)

        ctxs = [c for c in build_union_contexts(db) if c["country"] == "SE"]
        extras = [c for c in ctxs if c.get("search_radius_km")]
        assert len(extras) == MAX_EXTRA_SCOPE_CONTEXTS_PER_COUNTRY, (
            f"{len(extras)} extra contexts for {len(towns)} distinct anchors — "
            f"the per-country cap ({MAX_EXTRA_SCOPE_CONTEXTS_PER_COUNTRY}) must bound "
            "scheduled scrape spend"
        )
        assert len(ctxs) == 1 + MAX_EXTRA_SCOPE_CONTEXTS_PER_COUNTRY

    def test_non_radius_users_unchanged_union_only(self, db):
        from app.services.pipeline import build_union_contexts

        _onboarded_user(db, country="SE", municipalities='["Malmö"]', radius=None)
        _onboarded_user(db, country="SE", municipalities='["Stockholm"]', radius=0)

        ctxs = build_union_contexts(db)
        assert len(ctxs) == 1, (
            f"no radius anywhere -> exactly the union context, got {ctxs}"
        )
        assert not ctxs[0].get("search_radius_km")
        assert ctxs[0]["region"] is None
        assert sorted(ctxs[0]["municipalities"]) == ["Malmö", "Stockholm"]

    def test_unanchorable_radius_gets_no_context(self, db):
        """Vellinge has no centroid: geo_plan is None, the fetch would be
        byte-identical to the strict-municipality fetch the union already
        performs — emitting a context would double the API calls for
        nothing (and churn the watermark scope)."""
        from app.services.pipeline import build_union_contexts

        _onboarded_user(db, country="SE", municipalities='["Vellinge"]', radius=30)

        ctxs = build_union_contexts(db)
        assert len(ctxs) == 1
        assert not ctxs[0].get("search_radius_km")
        assert sorted(ctxs[0]["municipalities"]) == ["Vellinge"]


class TestScheduledRegionContexts:
    """Region-only users (no municipality — the wizard's whole-region
    path) need a region-carrying context or the union's region=None pins
    them out of every local on-site job."""

    def _region_ctx(self, ctxs, country, region):
        matches = [c for c in ctxs
                   if c["country"] == country and c.get("region") == region]
        assert matches, (
            f"no {country} context carries region={region!r}: "
            f"{[{k: c.get(k) for k in ('region', 'municipalities')} for c in ctxs]}"
        )
        return matches[0]

    def test_region_user_gets_region_carrying_context(self, db):
        from app.services.pipeline import build_union_contexts

        _onboarded_user(db, country="GB", region="Scotland",
                        municipalities=None)

        ctxs = build_union_contexts(db)
        rc = self._region_ctx(ctxs, "GB", "Scotland")
        # The gate's region clause requires the region AND no munis
        assert rc["municipalities"] == [] and rc["municipality"] is None
        # A local Scottish on-site job passes the ingest gate for this ctx
        from app.services.pipeline import passes_location_filter

        class _J:
            remote = False
            location = "Edinburgh, Scotland"

        assert passes_location_filter(_J(), rc) is True, (
            "the region context must admit local on-site jobs — currently "
            "region-only users get NO local jobs from scheduled hunts (PIPE-15)"
        )

    def test_region_contexts_dedupe_per_region(self, db):
        from app.services.pipeline import build_union_contexts

        for i in range(5):
            _onboarded_user(db, country="GB", region="Scotland",
                            queries=f'["q{i}"]')
        _onboarded_user(db, country="GB", region="Greater London")

        ctxs = build_union_contexts(db)
        regions = [c.get("region") for c in ctxs if c.get("region")]
        assert sorted(regions) == ["Greater London", "Scotland"], (
            f"one context per DISTINCT region (5 Scottish users share one), "
            f"got {regions}"
        )
        scot = self._region_ctx(ctxs, "GB", "Scotland")
        assert sorted(scot["queries"]) == sorted(f"q{i}" for i in range(5))

    def test_region_watermark_scope_distinguishes_regions(self, db):
        """Two region-only groups must not share the "" watermark bucket
        (the pre-PIPE-15 empty-municipality scope): the second group's
        first fetch would look watermarked by the first's and skip its
        own backfill."""
        from app.services.pipeline import _scope_key

        assert _scope_key({"municipalities": [], "region": "Scotland"}) == "region:scotland"
        assert _scope_key({"municipalities": [], "region": "Greater London"}) == "region:greater london"
        # Municipality-bearing scopes keep their exact historical keys
        assert _scope_key({"municipalities": ["Malmö"], "region": "Skåne län"}) == "malmö"
        assert _scope_key({"municipalities": [], "region": None}) == ""


# ---------------------------------------------------------------- PIPE-16

class TestMatchTimeScopeGate:
    """The candidate window must respect the user's location scope
    BEFORE any AI slot is spent, mirroring the ingest gate exactly
    (a job the ingest gate stored FOR this user must still match)."""

    def test_live_scenario_remote_job_never_burns_an_ai_slot(self, db, monkeypatch):
        """The live repro (2026-08-30): Bob, strictly-local London, GB.
        The union context stored a remote marketing ad (someone opted
        into remote). Pre-fix, Bob's matcher evaluated it — one of four
        slots spent on 'Product Marketing Lead - EMEA' before dismissal.
        Post-fix it is dismissed for free and the local backend role
        gets the slot."""
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="GB", municipalities='["London"]',
                              include_remote=False)
        profile = db.query(Profile).filter(Profile.user_id == uid).one()

        remote_job = _job_row(
            db, remote=1, location="Remote",
            title="Product Marketing Lead - EMEA",
            description="Own marketing across EMEA for a remote-first org.",
        )
        local_job = _job_row(
            db, remote=0, location="London, UK",
            title="Lead Backend Engineer",
            description="Python services on a London team, hybrid.",
        )
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert ai["jobs"] == ["Lead Backend Engineer"], (
            f"AI evaluations spent on: {ai['jobs']} — the strictly-local "
            "user's window must spend slots ONLY on in-scope jobs (PIPE-16: "
            "the live repro burned all four first-hunt slots on remote ads)"
        )
        assert summary["matches_created"] == 1

        # The remote job was SKIPPED for free, per-user, before the AI —
        # REG1 fix: no dismissal row is written (a terminal row would make
        # the skip permanent; widening preferences must be able to recover
        # the job, which the candidate query's match-row exclusion would
        # prevent). The job simply stays eligible for a future run.
        assert db.query(MatchResult).filter(
            MatchResult.user_id == uid, MatchResult.job_id == remote_job.id
        ).count() == 0, "out_of_scope must be a per-run SKIP, not a row"
        db.refresh(remote_job)
        assert remote_job.status == "new"
        # ...and the local job got the slot: a real match row, not a dismissal
        db.refresh(local_job)
        assert local_job.status == "matched"
        kept = db.query(MatchResult).filter(
            MatchResult.user_id == uid, MatchResult.job_id == local_job.id
        ).one()
        assert kept.decision is None and kept.score == 80

    def test_reg1_widened_scope_recovers_skipped_jobs(self, db, monkeypatch):
        """REG1 (live-proven 2026-08-31): a user runs matching with
        include_remote=False — the remote job is skipped. They re-onboard
        with include_remote=True and run again: the job MUST come back.
        Under the old dismissal-row form it never did — the candidate
        query excludes any (user, job) with a match row, so one narrow
        run permanently deleted the job from the widened scope's world."""
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="GB", municipalities='["London"]',
                              include_remote=False)
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        remote_job = _job_row(db, remote=1, location="Remote",
                              title="Remote Recovery Dev",
                              description="Fully remote Python role.")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(db, profile=profile, user_id=uid)
        assert ai["jobs"] == [], "strictly-local run must skip the remote job"
        assert db.query(MatchResult).filter(
            MatchResult.user_id == uid).count() == 0, (
            "the skip must leave no row (a row would block recovery)"
        )

        # the user widens their preferences in the wizard and hunts again
        profile.include_remote = 1
        db.commit()
        matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert ai["jobs"] == ["Remote Recovery Dev"], (
            f"REG1 regression: after include_remote=True the previously "
            f"skipped job must be evaluated (got {ai['jobs']})"
        )
        row = db.query(MatchResult).filter(
            MatchResult.user_id == uid, MatchResult.job_id == remote_job.id
        ).one()
        assert row.decision is None and row.score == 80

    def test_remote_allowing_user_still_sees_remote_jobs(self, db, monkeypatch):
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="GB", municipalities='["London"]',
                              include_remote=True)
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        _job_row(db, remote=1, location="Remote", title="Remote Dev",
                 description="Work from anywhere.")
        _job_row(db, remote=0, location="London, UK", title="Local Dev",
                 description="London office.")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert sorted(ai["jobs"]) == ["Local Dev", "Remote Dev"], (
            "an include_remote user must still see remote jobs — the gate "
            f"must mirror the ingest gate, not be stricter than it (got {ai['jobs']})"
        )
        # and nothing was dismissed as out_of_scope
        assert db.query(MatchResult).filter(
            MatchResult.dismissed_reason == "out_of_scope").count() == 0

    def test_same_country_non_local_job_per_gate_semantics(self, db, monkeypatch):
        """A Manchester job inside a London-only user's window: the union
        stored it for the Manchester user; for THIS user the strict gate
        (the exact ingest predicate for their own fetches) says out of
        scope — no AI slot."""
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="GB", municipalities='["London"]')
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        _job_row(db, remote=0, location="Manchester, UK", title="Northern Dev",
                 description="Manchester office role.")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert ai["jobs"] == [], (
            "a same-country but out-of-municipality on-site job must not "
            f"spend this strictly-local user's AI budget (got {ai['jobs']})"
        )
        # REG1 semantics: skipped, never written — stays eligible for a
        # future run or a widened scope
        assert db.query(MatchResult).filter(
            MatchResult.dismissed_reason == "out_of_scope").count() == 0

    def test_ingest_mirror_radius_user_keeps_neighbouring_job(self, db, monkeypatch):
        """NOT-STRICTER-THAN-INGEST pin: a Malmö+30km user's own radius
        fetch stores the neighbouring-kommun ad through the REDUCED gate
        (passes_radius_gate). The match-time gate must make the same
        source-conditional decision — the Lund jobtech ad stays in the
        window, while the non-geo-filtered careerjet ad out of the area
        (stored via someone else's scope) does not."""
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="SE", municipalities='["Malmö"]',
                              radius=30)
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        lund_job = _job_row(db, source="jobtech", remote=0,
                            location="Lund, Skåne län", title="Lund Dev",
                            description="Python in Lund.")
        gbg_job = _job_row(db, source="careerjet", remote=0,
                           location="Göteborg, Sweden", title="Gbg Dev",
                           description="Python in Göteborg.")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert ai["jobs"] == ["Lund Dev"], (
            "the radius user's neighbouring-kommun jobtech ad (stored via "
            "the reduced gate) must still reach the AI — a match gate "
            "stricter than ingest would strand exactly the jobs PIPE-15 "
            f"went out of its way to fetch (got {ai['jobs']})"
        )
        # REG1 semantics: the out-of-area careerjet ad is SKIPPED (no row —
        # a widened scope or a future run can still admit it)
        assert db.query(MatchResult).filter(
            MatchResult.job_id == gbg_job.id,
            MatchResult.dismissed_reason == "out_of_scope").count() == 0, (
            "the careerjet ad out of the user's area must be skipped "
            "without a terminal dismissal row"
        )
        db.refresh(lund_job)
        assert lund_job.status == "matched"

    def test_region_user_sees_region_jobs_at_match_time(self, db, monkeypatch):
        from app.models import Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="GB", region="Scotland",
                              municipalities=None)
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        _job_row(db, remote=0, location="Edinburgh, Scotland",
                 title="Edinburgh Dev", description="Edinburgh office.")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert ai["jobs"] == ["Edinburgh Dev"]

    def test_remote_only_user_drops_on_site_jobs(self, db, monkeypatch):
        from app.models import MatchResult, Profile
        from app.services import matcher_service

        # The onboarding route guarantees remote_only implies
        # include_remote (api/v1/profiles.py) — mirror the wizard's
        # actual output or the fixture tests a state that cannot exist.
        uid = _onboarded_user(db, country="GB", municipalities='["London"]',
                              remote_only=True, include_remote=True)
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        _job_row(db, remote=0, location="London, UK", title="On-site Dev",
                 description="London office.")
        _job_row(db, remote=1, location="Remote", title="Remote Dev",
                 description="Anywhere.")
        ai = _fake_ai(monkeypatch)

        matcher_service.run_matching(db, profile=profile, user_id=uid)

        assert ai["jobs"] == ["Remote Dev"]
        # REG1 semantics: the on-site job is skipped, no terminal row
        assert db.query(MatchResult).filter(
            MatchResult.dismissed_reason == "out_of_scope").count() == 0

    def test_budget_accounting_evaluation_cap_not_consumed_by_scope(self, db, monkeypatch):
        """Budget accounting: with 3 out-of-scope jobs and 1 in-scope job
        the run spends exactly ONE evaluation (limit default applies
        after cheap gates — the scope gate is a cheap gate)."""
        from app.models import Profile
        from app.services import matcher_service

        uid = _onboarded_user(db, country="GB", municipalities='["London"]')
        profile = db.query(Profile).filter(Profile.user_id == uid).one()
        for i in range(3):
            _job_row(db, remote=1, location="Remote", title=f"Remote {i}",
                     description="Remote role.")
        _job_row(db, remote=0, location="London, UK", title="Local Dev",
                 description="Local role.")
        ai = _fake_ai(monkeypatch)

        summary = matcher_service.run_matching(
            db, profile=profile, user_id=uid, limit=2
        )

        assert ai["jobs"] == ["Local Dev"], (
            f"evaluation cap must count AI spend only — the matcher "
            f"evaluated {ai['jobs']} for what is 1 in-scope job"
        )
        # The one keeper earns its full 3-sample protocol — 3 model calls
        # total for the whole run, zero for the three out-of-scope ads
        assert len(ai["calls"]) == 3, ai["calls"]
        assert summary["jobs_considered"] == 1
        assert summary["matches_created"] == 1
