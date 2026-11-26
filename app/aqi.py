"""Air-quality data: geocoding, Open-Meteo fetch with caching, and India NAQI computation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CACHE_TTL_S = 15 * 60

# India National AQI (CPCB) breakpoints: (conc_lo, conc_hi, aqi_lo, aqi_hi)
_PM25_BREAKPOINTS = [
    (0, 30, 0, 50), (30, 60, 50, 100), (60, 90, 100, 200),
    (90, 120, 200, 300), (120, 250, 300, 400), (250, 500, 400, 500),
]
_PM10_BREAKPOINTS = [
    (0, 50, 0, 50), (50, 100, 50, 100), (100, 250, 100, 200),
    (250, 350, 200, 300), (350, 430, 300, 400), (430, 600, 400, 500),
]
CATEGORIES = [
    (50, "Good"), (100, "Satisfactory"), (200, "Moderate"),
    (300, "Poor"), (400, "Very Poor"), (500, "Severe"),
]


class AirDataError(Exception):
    """Raised when location or air-quality data cannot be obtained."""


def sub_index(conc: float | None, breakpoints: list[tuple[float, float, int, int]]) -> int | None:
    """Linear interpolation of a pollutant concentration into a 0–500 sub-index."""
    if conc is None or conc < 0:
        return None
    for c_lo, c_hi, i_lo, i_hi in breakpoints:
        if conc <= c_hi:
            return round(i_lo + (i_hi - i_lo) * (conc - c_lo) / (c_hi - c_lo))
    return 500


def naqi(pm25: float | None, pm10: float | None) -> int | None:
    """India NAQI estimate = max of available PM sub-indices."""
    indices = [i for i in (sub_index(pm25, _PM25_BREAKPOINTS), sub_index(pm10, _PM10_BREAKPOINTS)) if i is not None]
    return max(indices) if indices else None


def category(aqi: int | None) -> str:
    if aqi is None:
        return "Unknown"
    for upper, name in CATEGORIES:
        if aqi <= upper:
            return name
    return "Severe"


@dataclass
class Location:
    name: str
    latitude: float
    longitude: float
    country: str = ""


@dataclass
class AirSnapshot:
    location: Location
    current: dict[str, Any]
    hourly: list[dict[str, Any]] = field(default_factory=list)

    @property
    def aqi(self) -> int | None:
        return self.current.get("naqi")


_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    if len(_cache) > 500:  # bound memory use
        _cache.clear()
    _cache[key] = (time.monotonic(), value)


async def geocode(client: httpx.AsyncClient, city: str) -> Location:
    key = f"geo:{city.lower()}"
    if (cached := _cache_get(key)) is not None:
        return cached
    resp = await client.get(GEOCODE_URL, params={"name": city, "count": 1, "language": "en"})
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        raise AirDataError(f"Could not find a place called '{city}'.")
    r = results[0]
    loc = Location(
        name=", ".join(p for p in (r.get("name"), r.get("admin1")) if p),
        latitude=r["latitude"], longitude=r["longitude"], country=r.get("country", ""),
    )
    _cache_set(key, loc)
    return loc


async def fetch_air(client: httpx.AsyncClient, loc: Location) -> AirSnapshot:
    key = f"air:{loc.latitude:.2f},{loc.longitude:.2f}"
    if (cached := _cache_get(key)) is not None:
        return cached
    params = {
        "latitude": loc.latitude, "longitude": loc.longitude,
        "current": "pm2_5,pm10,nitrogen_dioxide,ozone,us_aqi",
        "hourly": "pm2_5,pm10", "forecast_days": 2, "timezone": "auto",
    }
    resp = await client.get(AIR_URL, params=params)
    resp.raise_for_status()
    data = resp.json()
    cur = data.get("current") or {}
    if cur.get("pm2_5") is None and cur.get("pm10") is None:
        raise AirDataError("Air-quality data is not available for this location right now.")
    cur_aqi = naqi(cur.get("pm2_5"), cur.get("pm10"))
    current = {
        "time": cur.get("time"), "pm2_5": cur.get("pm2_5"), "pm10": cur.get("pm10"),
        "no2": cur.get("nitrogen_dioxide"), "ozone": cur.get("ozone"), "us_aqi": cur.get("us_aqi"),
        "naqi": cur_aqi, "category": category(cur_aqi),
    }
    h = data.get("hourly") or {}
    hourly = []
    for t, p25, p10 in zip(h.get("time", []), h.get("pm2_5", []), h.get("pm10", []), strict=False):
        a = naqi(p25, p10)
        hourly.append({"time": t, "pm2_5": p25, "pm10": p10, "naqi": a, "category": category(a)})
    snap = AirSnapshot(location=loc, current=current, hourly=hourly)
    _cache_set(key, snap)
    return snap
