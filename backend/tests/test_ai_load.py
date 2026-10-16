"""
AI provider load resilience — the 2026-10-16 beta-day incident.

Three new users onboarded within an hour; their first hunts fanned out
MATCH_CONCURRENCY-wide each on one flat-rate Z.ai key while the users
clicked "Prepare application". 3 of 4 tailored drafts failed:
  1 x RateLimitError 429 (Z.ai code 1302) straight onto the draft row
  2 x "Malformed tailoring response" — three bad replies inside ~3s

The contract these tests pin:
  - an INTERACTIVE call (tailor/judge — the user is waiting) retries a
    429 with bounded backoff, honouring Retry-After; a BACKGROUND match
    call does not (the matcher leaves the job 'new' for the next run)
  - an empty reply is a format failure and gets the same re-ask as a
    malformed one, instead of failing the draft on the first blip
  - persistent 429 fails the draft after a BOUNDED number of attempts
    with a user-readable error, never an infinite loop
  - every retry goes back through the same fabrication guard, and
    ai_usage keeps per-user attribution (429s now leave a row too —
    the incident was undiagnosable from ai_usage because they didn't)
  - a process-wide gate caps concurrent provider calls and lets
    interactive calls jump ahead of background matching
"""

import json
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.main import app
from app.models import AIUsage, JobPosting, MatchResult, Profile
from app.models import User as UserModel

GOOD = json.dumps({"cover_letter": "Dear Acme, I built payment services "
                   "in Python at Svenska Spel 2019 to 2023.",
                   "tailored_cv": "Erik Lindberg. Software Engineer, Svenska "
                   "Spel, Stockholm 2019 to 2023. Python.",
                   "changes_summary": ["refocused"]})
CV = ("Erik Lindberg. Software Engineer, Svenska Spel, Stockholm 2019 to "
      "2023. Built payment services in Python.")


def _rate_limit(retry_after=None):
    """A real openai.RateLimitError carrying Z.ai's 1302 body."""
    headers = {} if retry_after is None else {"retry-after": str(retry_after)}
    resp = httpx.Response(
        429, headers=headers,
        request=httpx.Request("POST", "https://api.z.ai/api/coding/paas/v4"),
        json={"code": "1302", "message": "Rate limit reached for requests"},
    )
    return openai.RateLimitError(
        "Error code: 429 - {'code': '1302', 'message': 'Rate limit reached "
        "for requests'}", response=resp, body=None)


def _resp(content):
    return SimpleNamespace(
        id=f"req_{uuid.uuid4().hex[:6]}",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                              prompt_tokens_details=None),
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content=content, reasoning_content=None))],
    )


class _ScriptedClient:
    """OpenAI-shaped client. Tailor calls consume `script` in order (the
    last step repeats); judge calls answer `judge_reply`. Every call's
    kind and the user prompt it carried are recorded."""

    def __init__(self, script, judge_reply='{"unsupported": []}'):
        self.script = list(script)
        self.judge_reply = judge_reply
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        system = kw["messages"][0]["content"]
        kind = "judge" if "strict fact-checker" in system else "tailor"
        self.calls.append((kind, kw["messages"][1]["content"]))
        if kind == "judge":
            return _resp(self.judge_reply)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return _resp(step)

    def kinds(self):
        return [k for k, _ in self.calls]


def _svc(client):
    from app.services.ai_service import AIService

    svc = AIService.__new__(AIService)
    svc.model = "glm-5.1"
    svc.thinking = "disabled"
    svc.max_tokens = 2000
    svc.client = client
    return svc


@pytest.fixture()
def sleeps(monkeypatch):
    import app.services.ai_service as ai_mod

    recorded = []
    monkeypatch.setattr(ai_mod.time, "sleep", lambda s: recorded.append(s))
    return recorded


# =====================================================================
# Service level: what reaches (and comes back from) the provider
# =====================================================================


class TestInteractiveRateLimitRetry:
    def test_429_then_success_returns_the_package(self, sleeps):
        client = _ScriptedClient([_rate_limit(), GOOD])
        out = _svc(client).tailor_application("profile", CV, "job")
        assert out["cover_letter"].startswith("Dear Acme")
        assert client.kinds() == ["tailor", "tailor"], (
            "a single 429 on a user-waiting tailor must be retried, not "
            "written onto the draft as the 2026-10-16 RateLimitError was")
        assert sleeps and sleeps[0] > 0, "retry must back off, not hammer"

    def test_retry_after_header_is_honoured(self, sleeps):
        client = _ScriptedClient([_rate_limit(retry_after=7), GOOD])
        _svc(client).tailor_application("profile", CV, "job")
        assert sleeps[0] == pytest.approx(7, abs=0.01), (
            f"Retry-After: 7 ignored — slept {sleeps}")

    def test_retry_after_is_capped(self, sleeps):
        import app.services.ai_service as ai_mod

        client = _ScriptedClient([_rate_limit(retry_after=3600), GOOD])
        _svc(client).tailor_application("profile", CV, "job")
        assert sleeps[0] <= ai_mod.RATE_LIMIT_MAX_WAIT_S, (
            "a hostile/huge Retry-After must not park the user's request")

    def test_persistent_429_is_bounded_and_named(self, sleeps):
        import app.services.ai_service as ai_mod

        client = _ScriptedClient([_rate_limit()])
        with pytest.raises(ai_mod.AIBusyError, match="(?i)busy"):
            _svc(client).tailor_application("profile", CV, "job")
        assert len(client.calls) == 1 + ai_mod.RATE_LIMIT_RETRIES, (
            f"{len(client.calls)} provider calls — retry must be bounded")
        assert len(sleeps) == ai_mod.RATE_LIMIT_RETRIES

    def test_judge_429_is_retried_too(self, sleeps):
        """The judge runs inside the same user-waiting prepare request —
        a judge 429 failed the draft just like a tailor 429."""
        client = _ScriptedClient([GOOD])
        calls = {"n": 0}
        real = client._create

        def flaky(**kw):
            if "strict fact-checker" in kw["messages"][0]["content"]:
                calls["n"] += 1
                if calls["n"] == 1:
                    client.calls.append(("judge", ""))
                    raise _rate_limit()
            return real(**kw)

        client.chat.completions.create = flaky
        assert _svc(client).judge_fabrication("src", "doc") == []
        assert calls["n"] == 2

    def test_background_match_is_not_retried(self, sleeps):
        """Matching is background work: a 429 there skips the job (left
        'new' for the next run). Retrying it would add load exactly
        when the key is saturated and the user is waiting on a tailor."""
        import app.services.ai_service as ai_mod

        client = _ScriptedClient([_rate_limit()])
        svc = _svc(client)
        with pytest.raises(openai.RateLimitError):
            svc._complete("s", "u", temperature=0.0, kind="match")
        assert len(client.calls) == 1
        assert sleeps == []
        assert ai_mod.RATE_LIMIT_RETRIES > 0


class TestEmptyReplyIsAFormatFailure:
    def test_empty_then_success_recovers(self, sleeps):
        client = _ScriptedClient(["", GOOD])
        out = _svc(client).tailor_application("profile", CV, "job")
        assert out["tailored_cv"].startswith("Erik Lindberg")
        assert client.kinds() == ["tailor", "tailor"], (
            "an empty reply must get the malformed-reply re-ask, not "
            "fail the draft on the first blip")

    def test_persistent_empty_still_fails_bounded(self, sleeps):
        import app.services.ai_service as ai_mod

        client = _ScriptedClient([""])
        with pytest.raises(ValueError, match="Malformed tailoring response"):
            _svc(client).tailor_application("profile", CV, "job")
        assert len(client.calls) == 1 + ai_mod.TAILOR_FORMAT_RETRIES

    def test_format_retries_back_off_beyond_a_burst(self, sleeps):
        """2026-10-16: two drafts burned all three attempts inside ~3s of
        sleep (1s, 2s) during the load spike. The re-asks must spread
        far enough to outlast a burst."""
        client = _ScriptedClient(["not json"])
        with pytest.raises(ValueError):
            _svc(client).tailor_application("profile", CV, "job")
        assert sum(sleeps) >= 8, f"format re-asks slept only {sleeps}"


class TestRateLimitObservability:
    def test_429_leaves_an_attributed_usage_row(self, client_app, sleeps):
        """The incident could not be reconstructed from ai_usage: a 429
        raised before record_ai_usage ran. Each rejected attempt now
        leaves a zero-token row with detail, attributed to the user."""
        import app.services.ai_service as ai_mod

        uid = uuid.uuid4()
        token = ai_mod.current_user_id.set(uid)
        try:
            client = _ScriptedClient([_rate_limit(), GOOD])
            _svc(client).tailor_application("profile", CV, "job")
        finally:
            ai_mod.current_user_id.reset(token)
        with SessionLocal() as check:
            rows = (check.query(AIUsage).filter(AIUsage.user_id == uid)
                    .order_by(AIUsage.id).all())
        assert [(r.kind, r.detail) for r in rows] == [
            ("tailor", "rate_limited (429)"), ("tailor", None)], rows
        assert rows[0].prompt_tokens == 0 and rows[0].cost_usd == 0


# =====================================================================
# Concurrency gate: interactive first, process-wide cap
# =====================================================================


class TestAICallGate:
    def test_capacity_is_never_exceeded(self):
        from app.services.ai_service import AICallGate

        gate = AICallGate(capacity=3, interactive_reserve=1)
        live, peak, lock = [0], [0], threading.Lock()

        def work(interactive):
            with gate.slot(interactive=interactive):
                with lock:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
                time.sleep(0.01)
                with lock:
                    live[0] -= 1

        threads = [threading.Thread(target=work, args=(i % 2 == 0,))
                   for i in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert peak[0] <= 3

    def test_background_cannot_take_the_reserved_slots(self):
        from app.services.ai_service import AICallGate

        gate = AICallGate(capacity=3, interactive_reserve=1)
        hold = threading.Event()
        started = []

        def bg(n):
            with gate.slot(interactive=False):
                started.append(n)
                hold.wait(5)

        threads = [threading.Thread(target=bg, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.1)
        assert len(started) == 2, (
            f"{len(started)} background calls in flight with capacity 3 "
            "reserve 1 — background must leave the reserve free")
        # ...and an interactive call still gets straight in
        got = threading.Event()

        def fg():
            with gate.slot(interactive=True):
                got.set()

        f = threading.Thread(target=fg)
        f.start()
        assert got.wait(1), "interactive call blocked behind background"
        hold.set()
        for t in threads + [f]:
            t.join(5)

    def test_waiting_interactive_jumps_the_background_queue(self):
        from app.services.ai_service import AICallGate

        gate = AICallGate(capacity=1, interactive_reserve=0)
        order = []
        release = threading.Event()

        def holder():
            with gate.slot(interactive=False):
                release.wait(5)

        def call(tag, interactive):
            with gate.slot(interactive=interactive):
                order.append(tag)

        h = threading.Thread(target=holder)
        h.start()
        time.sleep(0.05)
        bgs = [threading.Thread(target=call, args=(f"bg{n}", False))
               for n in range(3)]
        for t in bgs:
            t.start()
        time.sleep(0.05)
        fg = threading.Thread(target=call, args=("fg", True))
        fg.start()
        time.sleep(0.05)
        release.set()
        for t in [h, fg] + bgs:
            t.join(5)
        assert order[0] == "fg", f"interactive waited behind background: {order}"

    def test_complete_runs_inside_the_gate(self, monkeypatch, sleeps):
        """The gate only helps if _complete actually takes it — and a
        backoff sleep must happen OUTSIDE the slot (a sleeping retry
        holding a slot is a slot nobody can use)."""
        import app.services.ai_service as ai_mod

        gate = ai_mod.AICallGate(capacity=1, interactive_reserve=0)
        monkeypatch.setattr(ai_mod, "_ai_gate", lambda: gate)
        seen = []
        client = _ScriptedClient([_rate_limit(), GOOD])
        real = client._create

        def spy(**kw):
            seen.append(gate.in_use)
            return real(**kw)

        client.chat.completions.create = spy
        monkeypatch.setattr(ai_mod.time, "sleep",
                            lambda s: seen.append(("sleep", gate.in_use)))
        _svc(client).tailor_application("p", CV, "job")
        assert seen == [1, ("sleep", 0), 1], seen


# =====================================================================
# Draft level: the row the user sees, through the real guard
# =====================================================================


@pytest.fixture(scope="module")
def client_app():
    with TestClient(app) as c:  # lifespan -> init_db -> alembic head
        yield c
    engine.dispose()


@pytest.fixture()
def db(client_app):
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


def _seed(db):
    uid = uuid.uuid4()
    db.add(UserModel(id=uid, email=f"load-{uid.hex[:6]}@test.example",
                     hashed_password="test-only"))
    db.flush()
    profile = Profile(user_id=uid, is_active=1, full_name="Erik Lindberg",
                      cv_text=CV, cv_file_name="erik.pdf")
    job = JobPosting(source="manual", source_id=uuid.uuid4().hex[:8],
                     title="Dev", company="Acme",
                     url=f"https://x/{uuid.uuid4().hex[:6]}", status="matched",
                     description="A Python role.")
    db.add_all([profile, job])
    db.flush()
    db.add(MatchResult(user_id=uid, job_id=job.id, score=61,
                       tier="good_match", decision="approved"))
    db.commit()
    return uid, profile, job


def _wire(monkeypatch, client):
    from app.core.config import settings
    from app.services import draft_service

    svc = _svc(client)
    monkeypatch.setattr(draft_service, "get_ai_service", lambda: svc)
    monkeypatch.setattr(draft_service, "ai_service_available", lambda: True)
    # The judge ON: every retried tailor must still face the full guard.
    monkeypatch.setattr(settings, "FABRICATION_JUDGE", "on")
    return draft_service


class TestDraftSurvivesLoad:
    def test_429_then_success_draft_ready_and_guarded(self, db, monkeypatch,
                                                      sleeps):
        uid, profile, job = _seed(db)
        client = _ScriptedClient([_rate_limit(), GOOD])
        ds = _wire(monkeypatch, client)
        d = ds.create_draft_for_job(db, job, profile=profile, user_id=uid)
        assert d.status == "ready", d.error
        assert client.kinds() == ["tailor", "tailor", "judge"], (
            "the recovered package must go through the judge")
        # the judge saw the package that was stored — not a stale one
        judge_prompt = client.calls[-1][1]
        assert d.cover_letter in judge_prompt and d.tailored_cv in judge_prompt
        assert "Erik Lindberg" in client.calls[1][1], (
            "the retried tailor prompt must carry this user's CV")

    def test_empty_then_success_draft_ready(self, db, monkeypatch, sleeps):
        uid, profile, job = _seed(db)
        client = _ScriptedClient(["", GOOD])
        ds = _wire(monkeypatch, client)
        d = ds.create_draft_for_job(db, job, profile=profile, user_id=uid)
        assert d.status == "ready", d.error
        assert client.kinds() == ["tailor", "tailor", "judge"]

    def test_retry_never_bypasses_a_judge_block(self, db, monkeypatch, sleeps):
        """A package recovered by retry that the judge flags is treated
        exactly like any other flagged package: regenerated, then blocked."""
        uid, profile, job = _seed(db)
        client = _ScriptedClient(
            [_rate_limit(), GOOD],
            judge_reply=json.dumps({"unsupported": [
                {"claim": "AWS Certified", "why": "not in CV"}]}))
        ds = _wire(monkeypatch, client)
        d = ds.create_draft_for_job(db, job, profile=profile, user_id=uid)
        assert d.status == "failed" and d.fabrication_blocked is True, d.error

    def test_persistent_429_draft_failed_with_clear_error(self, db,
                                                          monkeypatch, sleeps):
        import app.services.ai_service as ai_mod

        uid, profile, job = _seed(db)
        client = _ScriptedClient([_rate_limit()])
        ds = _wire(monkeypatch, client)
        d = ds.create_draft_for_job(db, job, profile=profile, user_id=uid)
        assert d.status == "failed"
        assert len(client.calls) == 1 + ai_mod.RATE_LIMIT_RETRIES
        assert "busy" in d.error.lower() and "try again" in d.error.lower(), (
            f"user-facing error is not actionable: {d.error!r}")
        assert "1302" not in d.error and "Error code" not in d.error, (
            f"raw provider payload leaked to the user: {d.error!r}")
        assert "Error:" not in d.error and "AIBusy" not in d.error, (
            f"exception class name is noise to the user: {d.error!r}")
        assert d.fabrication_blocked is False

