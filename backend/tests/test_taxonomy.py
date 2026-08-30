"""Occupation taxonomy tests — validated profession search units.

The AI suggests LABELS; the taxonomy service is the single authority
that turns a label into a real concept code. Unresolved labels and
unknown client-submitted codes are dropped — a fabricated code can
never reach the JobTech API. Each stored code becomes its own search
unit in the jobtech fetch (recall beyond title words) and its own
watermark key (a new code deep-backfills its history).
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

    # The shared sqlite scratch file may predate new columns
    # (create_all adds tables, never columns). Drop and recreate the
    # schema IN PLACE — never delete the file: other modules hold
    # pooled connections to its inode.
    from app.core.database import engine
    from app.core.orm import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    for model in (Application, ApplicationDraft, MatchResult, Profile,
                  JobPosting, AIUsage, ScrapeRun, ScrapeWatermark, SystemLock,
                  User):
        session.query(model).delete()
    session.commit()
    yield session
    session.rollback()
    session.close()
    from app.core.database import engine as _engine
    _engine.dispose()


TAXONOMY_HITS = [
    {"taxonomy/id": "Y8yf_nDR_FkB", "taxonomy/preferred-label": "Mjukvaruutvecklare"},
    {"taxonomy/id": "cmp1", "taxonomy/preferred-label": "Systemutvecklare/Programmerare"},
    {"taxonomy/id": "fin1", "taxonomy/preferred-label": "Chef, Corporate Finance"},
    {"taxonomy/id": "n1", "taxonomy/preferred-label": "Sjuksköterska, grundutbildad"},
    {"taxonomy/id": "n2", "taxonomy/preferred-label": "Sjuksköterska, akutmottagning"},
    {"taxonomy/id": "jjq8_QHL_yGc", "taxonomy/preferred-label": "Kassabiträde",
     "taxonomy/deprecated": True},
]


@pytest.fixture()
def taxonomy(monkeypatch):
    """Seed the per-process taxonomy cache with known concepts."""
    from app.services import occupation_taxonomy as ot

    table = {}
    for c in TAXONOMY_HITS:
        if c.get("taxonomy/deprecated"):
            continue
        table[ot._normalize(c["taxonomy/preferred-label"])] = {
            "code": c["taxonomy/id"], "label": c["taxonomy/preferred-label"],
        }
    monkeypatch.setattr(ot, "_BY_LABEL", table)
    return ot


def _ctx(queries=None, codes=None):
    return {
        "country": "SE",
        "queries": queries or ["utvecklare"],
        "occupation_codes": codes or [],
        "municipalities": [],
        "search_radius_km": 0,
        "languages": [],
        "remote_only": False,
        "include_remote": False,
    }


class TestResolution:
    def test_exact_and_normalized_labels_resolve(self, taxonomy):
        picks = taxonomy.resolve_labels(["Mjukvaruutvecklare", "  mjukvaruutvecklare "])
        assert [p["code"] for p in picks] == ["Y8yf_nDR_FkB"]

    def test_unknown_and_deprecated_labels_dropped(self, taxonomy):
        picks = taxonomy.resolve_labels(
            ["Mjukvaruutvecklare", "Hocus Pocus Developer", "Kassabiträde"]
        )
        assert [p["label"] for p in picks] == ["Mjukvaruutvecklare"]
        assert taxonomy.validate_codes(["not-a-code", "Y8yf_nDR_FkB"]) == [
            {"code": "Y8yf_nDR_FkB", "label": "Mjukvaruutvecklare"}
        ]

    def test_dedup_by_code(self, taxonomy):
        picks = taxonomy.resolve_labels(["Mjukvaruutvecklare", "mjukvaruutvecklare"])
        assert len(picks) == 1

    def test_unique_compound_prefix_resolves(self, taxonomy):
        """Official names are often compound ('Systemutvecklare/
        Programmerare') - the head of exactly ONE compound resolves."""
        picks = taxonomy.resolve_labels(["Systemutvecklare"])
        assert [p["code"] for p in picks] == ["cmp1"]

    def test_comma_compound_does_not_resolve(self, taxonomy):
        """Comma compounds NARROW ('Chef, Corporate Finance' is a
        subtype, not a synonym) — bare 'Chef' must resolve to nothing,
        never to the finance specialist concept (review finding,
        verified live). Slash compounds still resolve (test above)."""
        picks = taxonomy.resolve_labels(["Chef", "Sjuksköterska"])
        assert picks == []


class TestJobtechOccupationUnits:
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

    def test_codes_become_independent_search_units(self, monkeypatch):
        from app.services.scrapers import jobtech

        captured = self._capture(monkeypatch)
        jobtech.JobtechScraper().fetch(
            _ctx(queries=["utvecklare"], codes=["Y8yf_nDR_FkB", "w8rS_xdx_8Uo"])
        )
        kinds = [req[0][0] for req in captured]
        assert kinds == ["q", "occupation-name", "occupation-name"], (
            "one unit per query + one per code, taxonomy units independent "
            f"(never combined with q): {kinds}"
        )
        assert captured[1][0] == ("occupation-name", "Y8yf_nDR_FkB")

    def test_no_codes_keeps_plain_queries(self, monkeypatch):
        from app.services.scrapers import jobtech

        captured = self._capture(monkeypatch)
        jobtech.JobtechScraper().fetch(_ctx(queries=["utvecklare"], codes=[]))
        assert all(req[0][0] == "q" for req in captured)


class TestWatermarkUnits:
    def test_codes_join_the_watermark_keys(self):
        from app.services.pipeline import _watermark_queries

        assert _watermark_queries(_ctx(queries=["a"], codes=["X1"])) == ["a", "name:X1"]

    def test_new_code_forces_backfill(self, db, taxonomy):
        from app.services.pipeline import delta_since_for, set_watermarks

        set_watermarks(db, "jobtech", _ctx(queries=["a"], codes=[]))
        assert delta_since_for(db, "jobtech", _ctx(queries=["a"], codes=[])) is not None
        assert delta_since_for(db, "jobtech", _ctx(queries=["a"], codes=["X1"])) is None, (
            "a newly added profession must deep-backfill its history"
        )


class TestUnionCodes:
    def test_union_merges_codes_across_users(self, db):
        import uuid as _uuid

        from app.models import Profile, User
        from app.services.pipeline import build_union_contexts

        for i, (country, codes) in enumerate(
            [("SE", '[{"code":"A","label":"a"}]'), ("SE", '[{"code":"B","label":"b"},{"code":"A","label":"a"}]')]
        ):
            u = User(id=_uuid.uuid4(), email=f"occ{i}-{i}@test.example", hashed_password="x")
            db.add(u)
            db.add(Profile(
                is_active=1, user_id=u.id, full_name="T", cv_file_name="cv.pdf",
                cv_text="dev", country=country, region=None,
                occupation_codes=codes, search_queries='["utvecklare"]',
            ))
        db.commit()

        ctxs = {c["country"]: c for c in build_union_contexts(db)}
        merged = sorted(ctxs["SE"]["occupation_codes"])
        assert merged == ["A", "B"], (
            "union of every user's codes, deduped — PLAIN STRINGS, the "
            "shape build_scrape_context emits and the scraper consumes "
            "(this shipped with dicts and no-op'd every scheduled hunt)"
        )

    def test_union_context_feeds_the_scraper_as_codes(self, db, monkeypatch):
        """The composition the scheduled hunt actually performs:
        build_union_contexts -> JobtechScraper.fetch. The taxonomy unit
        must hit the API as a bare concept code (review finding: a
        stringified dict returns HTTP 200 with zero hits — silent
        no-op)."""
        import uuid as _uuid

        from app.models import Profile, User
        from app.services.pipeline import build_union_contexts
        from app.services.scrapers import jobtech

        u = User(id=_uuid.uuid4(), email="comp@test.example", hashed_password="x")
        db.add(u)
        db.add(Profile(is_active=1, user_id=u.id, full_name="T", cv_file_name="c.pdf",
                       cv_text="dev", country="SE", region=None,
                       occupation_codes='[{"code":"fg7B_yov_smw","label":"X"}]',
                       search_queries='["utvecklare"]'))
        db.commit()

        captured = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": []}

        monkeypatch.setattr(jobtech.httpx, "get",
                            lambda url, params=None, **kw: (captured.append(params), _Resp())[1])

        union_ctx = next(c for c in build_union_contexts(db) if c["country"] == "SE")
        jobtech.JobtechScraper().fetch(union_ctx)

        occ_params = [p for req in captured for p in req if p[0] == "occupation-name"]
        assert occ_params == [("occupation-name", "fg7B_yov_smw")], (
            f"taxonomy unit must be a bare code: {occ_params}"
        )


class TestFailureNotCached:
    def test_transient_feed_failure_is_retried(self, monkeypatch):
        """One outage must not pin an empty table for the process
        lifetime (review finding: everyone onboarding during the window
        silently lost their codes until the next deploy)."""
        import app.services.occupation_taxonomy as ot

        monkeypatch.setattr(ot, "_BY_LABEL", None)
        calls = {"n": 0}

        def flaky_get(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("feed down")
            return type("R", (), {
                "raise_for_status": lambda s: None,
                "json": staticmethod(lambda s=None: [
                    {"taxonomy/id": "ok1", "taxonomy/preferred-label": "Mjukvaruutvecklare"},
                ]),
            })()

        monkeypatch.setattr(ot.httpx, "get", flaky_get)
        assert ot.resolve_labels(["Mjukvaruutvecklare"]) == [], "first call: outage"
        assert ot._BY_LABEL is None, "failure must not be cached"
        assert ot.resolve_labels(["Mjukvaruutvecklare"]) == [
            {"code": "ok1", "label": "Mjukvaruutvecklare"}
        ], "second call: retried and resolved"


class TestSuggestionValidation:
    def test_ai_labels_resolved_and_dropped(self, monkeypatch, taxonomy):
        """SE suggestions: model output labels; only resolvable ones
        survive as {code,label} — GB gets none."""
        from app.services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc.model = "glm-test"
        svc._complete = lambda *a, **k: (
            '{"from_your_experience": ["utvecklare"], "worth_a_look": [], '
            '"occupation_names": ["Mjukvaruutvecklare", "Påhittat Yrke"]}'
        )
        svc._parse_json = lambda raw: __import__("json").loads(raw)

        se = svc.suggest_search_queries("cv text", "SE", "field")
        assert se["occupation_suggestions"] == [
            {"code": "Y8yf_nDR_FkB", "label": "Mjukvaruutvecklare"}
        ]
        gb = svc.suggest_search_queries("cv text", "GB", "field")
        assert "occupation_suggestions" not in gb

    def test_onboarding_drops_unknown_codes(self, db, taxonomy, monkeypatch):
        """The endpoint boundary: client-submitted codes are validated
        against the taxonomy; unknowns never reach the profile."""
        import asyncio
        import uuid as _uuid

        from app.api.v1.profiles import save_onboarding
        from app.models import Profile, User
        from app.schemas.profile import OnboardingRequest

        uid = _uuid.uuid4()
        db.add(User(id=uid, email=f"onb-{uid.hex[:8]}@test.example", hashed_password="x"))
        db.add(Profile(is_active=1, user_id=uid, full_name="T", cv_file_name="c.pdf",
                       cv_text="dev"))
        db.commit()

        payload = OnboardingRequest(
            country="SE", region="Skåne län", municipalities=["Malmö"],
            search_queries=["utvecklare"],
            occupation_codes=["Y8yf_nDR_FkB", "totally-fake-code"],
        )
        user = db.get(User, uid)
        result = asyncio.run(save_onboarding(payload, db, user))
        stored = result.occupation_codes
        assert [p["code"] for p in stored] == ["Y8yf_nDR_FkB"], (
            "unknown client codes dropped at the boundary"
        )
