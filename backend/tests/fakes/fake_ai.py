"""WO-31 L1: an OpenAI-compatible fake GLM provider.

Injected at AIService.client (via a hand-built AIService — no network,
no key). It answers every kind the app sends, recognised by the system
prompt the real AIService builds:

  tailor  -> a package that quotes the CV's first line (traceable)
  judge   -> {"unsupported": []} unless judge_reply is set
  extract -> fields read from the CV's first line ("Name. Title, ...")
  suggest -> two direct queries + one pivot
  match   -> a deterministic score from hash(user prompt) — fitted to the
             measured 44% <13 / 24% band / 32% keep split

Provider behaviour, all optional:
  fail_first     — the first N calls raise a real openai.RateLimitError
                   (Z.ai code `code`, `retry-after`)
  rpm/concurrency — a token bucket (on `clock`) and an in-flight ceiling;
                   over either -> 429 1302 with retry-after
  quota_until    — every call before this instant raises 429 1316
                   "will reset at <time>"
  on_call        — hook(kind, prompt) run before answering (tests use it
                   to kill a drainer mid-call)

Every call is recorded as (kind, user_prompt) so tests assert on the
OUTBOUND artifact — which user's CV reached which call (standard #2).
"""

import hashlib
import json
import threading
import time
import uuid
from datetime import timedelta, timezone
from types import SimpleNamespace

import httpx
import openai


def rate_limit_error(code: str = "1302", retry_after=None,
                     message: str = "Rate limit reached for requests"):
    headers = {} if retry_after is None else {"retry-after": str(retry_after)}
    resp = httpx.Response(
        429, headers=headers,
        request=httpx.Request("POST", "https://api.z.ai/api/coding/paas/v4"),
        json={"code": code, "message": message},
    )
    return openai.RateLimitError(
        f"Error code: 429 - {{'code': '{code}', 'message': '{message}'}}",
        response=resp, body={"code": code, "message": message})


def _resp(content, finish_reason="stop"):
    return SimpleNamespace(
        id=f"req_{uuid.uuid4().hex[:6]}",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                              prompt_tokens_details=None),
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content, reasoning_content=None))],
    )


def kind_of(system_prompt: str) -> str:
    if "strict fact-checker" in system_prompt:
        return "judge"
    if "structured job-seeker profile" in system_prompt:
        return "extract"
    if "from_your_experience" in system_prompt:
        return "suggest"
    if "cover_letter" in system_prompt:
        return "tailor"
    return "match"


def _cv_line(prompt: str) -> str:
    """The first non-empty line after the CV heading in a user prompt."""
    for marker in ("## My CV (source of truth — every fact must come from here)\n", "## CV\n"):
        if marker in prompt:
            rest = prompt.split(marker, 1)[1]
            for line in rest.splitlines():
                if line.strip():
                    return line.strip()
    return ""


class FakeAI:
    def __init__(self, *, fail_first: int = 0, code: str = "1302", retry_after=2,
                 rpm=None, concurrency=None, quota_until=None, clock=None,
                 judge_reply='{"unsupported": []}', on_call=None, latency_s: float = 0.0):
        self.fail_first = fail_first
        self.code = code
        self.retry_after = retry_after
        self.rpm = rpm
        self.concurrency = concurrency
        self.quota_until = quota_until
        self.clock = clock
        self.judge_reply = judge_reply
        self.on_call = on_call
        self.latency_s = latency_s
        self.calls = []        # every attempt: (kind, prompt)
        self.answered = []     # successful replies: (kind, prompt)
        self.rejected = 0
        self._lock = threading.Lock()
        self._inflight = 0
        self._window = []      # timestamps of admitted calls (rpm bucket)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **_kw):
        return self  # the real SDK copies; the fake has no retries anyway

    def kinds(self):
        return [k for k, _ in self.calls]

    def _now(self):
        return self.clock() if self.clock else None

    def _admit(self):
        with self._lock:
            n = len(self.calls)
            if n <= self.fail_first:
                self.rejected += 1
                raise rate_limit_error(self.code, self.retry_after)
            now = self._now()
            if self.quota_until is not None and now is not None and now < self.quota_until:
                self.rejected += 1
                reset = self.quota_until.replace(tzinfo=timezone.utc).astimezone(
                    timezone(timedelta(hours=8)))
                raise rate_limit_error(
                    "1316", None,
                    f"Usage limit reached, will reset at {reset:%Y-%m-%d %H:%M:%S}")
            if self.concurrency is not None and self._inflight >= self.concurrency:
                self.rejected += 1
                raise rate_limit_error("1302", self.retry_after)
            if self.rpm is not None and now is not None:
                self._window = [t for t in self._window if now - t < timedelta(minutes=1)]
                if len(self._window) >= self.rpm:
                    self.rejected += 1
                    raise rate_limit_error("1302", self.retry_after)
                self._window.append(now)
            self._inflight += 1

    def _create(self, **kw):
        system = kw["messages"][0]["content"]
        prompt = kw["messages"][1]["content"]
        kind = kind_of(system)
        with self._lock:
            self.calls.append((kind, prompt))
        self._admit()
        try:
            if self.on_call is not None:
                self.on_call(kind, prompt)
            if self.latency_s:
                time.sleep(self.latency_s)
            content = self._answer(kind, prompt)
            with self._lock:
                self.answered.append((kind, prompt))
            return _resp(content)
        finally:
            with self._lock:
                self._inflight -= 1

    def _answer(self, kind: str, prompt: str) -> str:
        line = _cv_line(prompt)
        if kind == "judge":
            return self.judge_reply
        if kind == "tailor":
            return json.dumps({
                "cover_letter": f"Dear hiring team, {line}",
                "tailored_cv": line,
                "changes_summary": ["Your CV was reordered for this role."],
            })
        if kind == "extract":
            name, _, rest = line.partition(".")
            return json.dumps({
                "full_name": name.strip(), "email": "", "phone": "", "location": "",
                "professional_title": rest.split(",")[0].strip(),
                "experience_years": 4, "skills": [{"name": "Python", "level": "advanced"}],
                "recent_roles": [], "education": [], "certifications": [],
                "keywords": ["python"], "summary": "Backend developer.",
            })
        if kind == "suggest":
            return json.dumps({
                "from_your_experience": ["Backend developer", "Python developer"],
                "worth_a_look": [{"query": "Platform engineer", "why": "You ran services."}],
                "occupation_names": [],
            })
        digest = int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16) % 100
        score = 5 if digest < 44 else 18 if digest < 68 else 60
        return json.dumps({"score": score, "tier": "", "reasoning": "fake"})


def fake_service(fake: FakeAI):
    """An AIService whose client is the fake (no key, no network)."""
    from app.services.ai_service import AIService

    svc = AIService.__new__(AIService)
    svc.model = "glm-5.1"
    svc.thinking = "disabled"
    svc.max_tokens = 2000
    svc.client = fake
    return svc
