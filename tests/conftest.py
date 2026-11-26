import pytest

from app import aqi, main
from app.aqi import AirSnapshot, Location


def make_hourly(values, day="2026-11-27"):
    return [
        {"time": f"{day}T{h:02d}:00", "pm2_5": v, "pm10": v, "naqi": aqi.naqi(v, v), "category": aqi.category(aqi.naqi(v, v))}
        for h, v in enumerate(values)
    ]


@pytest.fixture
def snapshot():
    # Smoggy night and morning, cleaner mid-afternoon
    values = [180] * 12 + [70, 60, 40, 35, 45, 90] + [150] * 6
    cur_v = 140
    a = aqi.naqi(cur_v, cur_v)
    return AirSnapshot(
        location=Location("Delhi, NCT", 28.6, 77.2, "India"),
        current={"time": "2026-11-27T08:00", "pm2_5": cur_v, "pm10": cur_v, "no2": 90, "ozone": 5,
                 "us_aqi": 190, "naqi": a, "category": aqi.category(a)},
        hourly=make_hourly(values),
    )


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    aqi._cache.clear()
    main._hits.clear()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
