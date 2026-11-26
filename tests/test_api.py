import httpx
import respx
from fastapi.testclient import TestClient

from app import aqi, main

GEO = {"results": [{"name": "Delhi", "admin1": "NCT", "latitude": 28.65, "longitude": 77.23, "country": "India"}]}
AIR = {
    "current": {"time": "2026-11-27T08:00", "pm2_5": 134.6, "pm10": 136.0, "nitrogen_dioxide": 105.4, "ozone": 6.0, "us_aqi": 195},
    "hourly": {
        "time": [f"2026-11-27T{h:02d}:00" for h in range(24)],
        "pm2_5": [150] * 12 + [60, 50, 40, 45, 80] + [140] * 7,
        "pm10": [160] * 12 + [70, 60, 50, 55, 90] + [150] * 7,
    },
}
BODY = {"profile": {"city": "Delhi", "commute_mode": "car", "commute_km": 12, "conditions": ["asthma"]}}


def mock_upstream():
    respx.get(aqi.GEOCODE_URL).mock(return_value=httpx.Response(200, json=GEO))
    return respx.get(aqi.AIR_URL).mock(return_value=httpx.Response(200, json=AIR))


@respx.mock
def test_plan_happy_path_without_ai_key():
    mock_upstream()
    with TestClient(main.app) as c:
        r = c.post("/api/plan", json=BODY)
    assert r.status_code == 200
    data = r.json()
    assert data["current"]["category"] == "Very Poor"
    assert data["ai"]["source"] == "rules"
    assert "Delhi" in data["ai"]["text"]
    assert data["actions"] and data["windows"]


@respx.mock
def test_air_data_is_cached():
    air = mock_upstream()
    with TestClient(main.app) as c:
        c.post("/api/plan", json=BODY)
        c.post("/api/plan", json=BODY)
    assert air.call_count == 1


@respx.mock
def test_unknown_city_returns_404():
    respx.get(aqi.GEOCODE_URL).mock(return_value=httpx.Response(200, json={}))
    with TestClient(main.app) as c:
        r = c.post("/api/plan", json={"profile": {"city": "Nowhereville"}})
    assert r.status_code == 404
    assert "Nowhereville" in r.json()["detail"]


@respx.mock
def test_upstream_failure_returns_502():
    respx.get(aqi.GEOCODE_URL).mock(return_value=httpx.Response(500))
    with TestClient(main.app) as c:
        r = c.post("/api/plan", json=BODY)
    assert r.status_code == 502


def test_invalid_input_rejected():
    with TestClient(main.app) as c:
        r = c.post("/api/plan", json={"profile": {"city": "Delhi", "commute_km": 9999}})
        r2 = c.post("/api/plan", json={"profile": {"city": "Delhi"}, "question": "x" * 401})
    assert r.status_code == 422 and r2.status_code == 422


@respx.mock
def test_gemini_used_when_key_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_upstream()
    gem = respx.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*").mock(
        return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Stay safe, Delhi!"}]}}]})
    )
    with TestClient(main.app) as c:
        r = c.post("/api/plan", json={**BODY, "question": "Can I jog tonight?"})
    assert r.json()["ai"] == {"text": "Stay safe, Delhi!", "source": "gemini"}
    sent = gem.calls.last.request
    assert sent.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(sent.url)  # key never in URL
    assert b"Can I jog tonight?" in sent.content


@respx.mock
def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_upstream()
    respx.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*").mock(return_value=httpx.Response(503))
    with TestClient(main.app) as c:
        r = c.post("/api/plan", json=BODY)
    assert r.status_code == 200 and r.json()["ai"]["source"] == "rules"


def test_security_headers_and_health():
    with TestClient(main.app) as c:
        r = c.get("/health")
        page = c.get("/")
    assert r.json()["status"] == "ok"
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "<main" in page.text and 'lang="en"' in page.text


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT", 3)
    with TestClient(main.app) as c:
        codes = [c.post("/api/plan", json={"profile": {"city": "X"}}).status_code for _ in range(5)]
    assert codes[:3] == [422, 422, 422] and codes[3:] == [429, 429]
