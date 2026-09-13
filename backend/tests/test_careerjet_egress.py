"""Careerjet static egress (2026-09-11).

Careerjet allowlists caller IPs — at most 8 ADDRESSES, and a /24 counts
as 256 — so Render's shared outbound ranges can never be declared.
Production therefore sends Careerjet traffic through a static-IP proxy
(CAREERJET_PROXY_URL) whose addresses are declared in the partner portal.

What must hold on the outbound artifact:
  - with a proxy configured, EVERY request the scraper makes goes through
    it: the search, the ambiguous-location retry, AND the public-IP lookup
    that fills user_ip. A lookup that bypassed the proxy would report
    Render's IP as user_ip while the search arrived from the proxy's.
  - without a proxy, requests go direct — the laptop path, unchanged.

Blast radius when this breaks: every Careerjet search is rejected, and the
run still records 'completed, jobs_found=0' (per-search failures are
logged, not raised) — silent loss of the only source with no overlap.
"""

import pytest

from app.core.config import settings
from app.services.scrapers import careerjet

PROXY = "http://user:secret@static-egress.example:9293"
PROXY_IP = "203.0.113.7"    # TEST-NET-3: what the lookup sees through the proxy
DIRECT_IP = "198.51.100.9"  # TEST-NET-2: what it sees going direct


class _Resp:
    def __init__(self, *, text="", payload=None):
        self.text = text
        self.status_code = 200
        self._payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _jobs(n=1):
    return {"type": "JOBS", "jobs": [{
        "url": f"https://jobviewtrack.example/{i}", "title": f"Job {i}",
        "company": "Acme", "locations": "Malmö", "description": "d",
        "date": "Wed,15 Nov 2025 19:13:43 GMT",
    } for i in range(n)]}


@pytest.fixture()
def wire(monkeypatch):
    """Stub httpx.get in the careerjet module; record every outbound call.
    The IP-lookup echo answers like the real thing: the proxy's address
    when the call went through the proxy, the direct address otherwise."""
    calls, queue = [], []

    def fake_get(url, params=None, **kwargs):
        calls.append({"url": url, "params": params, "proxy": kwargs.get("proxy")})
        if "ipify" in url:
            return _Resp(text=PROXY_IP if kwargs.get("proxy") else DIRECT_IP)
        return queue.pop(0) if queue else _Resp(payload=_jobs())

    monkeypatch.setattr(careerjet.httpx, "get", fake_get)
    monkeypatch.setattr(careerjet, "_cached_public_ip", None)
    monkeypatch.setattr(settings, "CAREERJET_API_KEY", "test-key")
    return calls, queue


CTX = {"country": "SE", "queries": ["python"], "municipality": "Malmö"}


def _searches(calls):
    return [c for c in calls if c["url"] == careerjet.SEARCH_URL]


class TestCareerjetStaticEgress:
    def test_every_request_goes_through_the_proxy(self, wire, monkeypatch):
        calls, _ = wire
        monkeypatch.setattr(settings, "CAREERJET_PROXY_URL", PROXY)
        jobs = careerjet.CareerjetScraper().fetch(CTX)

        assert jobs, "fixture sanity: the stubbed search returns one job"
        lookups = [c for c in calls if "ipify" in c["url"]]
        assert lookups and _searches(calls), f"expected a lookup and a search: {calls}"
        for c in calls:
            assert c["proxy"] == PROXY, (
                f"{c['url']} bypassed CAREERJET_PROXY_URL — it would egress from "
                "Render's shared IPs, which the 8-address allowlist can never "
                "cover: every search rejected, run recorded 'completed, 0'"
            )

    def test_user_ip_is_the_proxys_address(self, wire, monkeypatch):
        calls, _ = wire
        monkeypatch.setattr(settings, "CAREERJET_PROXY_URL", PROXY)
        careerjet.CareerjetScraper().fetch(CTX)

        sent = {c["params"]["user_ip"] for c in _searches(calls)}
        assert sent == {PROXY_IP}, (
            f"user_ip sent as {sent}, expected the proxy's {PROXY_IP} — a direct "
            "lookup reports Render's IP while the search arrives from the proxy"
        )

    def test_location_retry_is_proxied_too(self, wire, monkeypatch):
        calls, queue = wire
        monkeypatch.setattr(settings, "CAREERJET_PROXY_URL", PROXY)
        queue.extend([
            _Resp(payload={"type": "LOCATIONS", "locations": ["Malmö, Skåne"]}),
            _Resp(payload=_jobs()),
        ])
        careerjet.CareerjetScraper().fetch(CTX)

        searches = _searches(calls)
        assert len(searches) == 2, f"expected search + location retry: {searches}"
        assert all(c["proxy"] == PROXY for c in searches), (
            "the ambiguous-location retry bypassed the proxy — rejected by "
            "the allowlist on exactly the searches that needed a second try"
        )

    def test_without_a_proxy_requests_go_direct(self, wire, monkeypatch):
        calls, _ = wire
        monkeypatch.setattr(settings, "CAREERJET_PROXY_URL", "")
        careerjet.CareerjetScraper().fetch(CTX)

        assert all(c["proxy"] is None for c in calls), (
            f"direct egress (laptop/dev) must not be routed anywhere: {calls}"
        )
        assert {c["params"]["user_ip"] for c in _searches(calls)} == {DIRECT_IP}
