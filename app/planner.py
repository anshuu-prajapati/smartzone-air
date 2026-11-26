"""Deterministic, explainable recommendations. The LLM only rephrases and personalises these facts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .aqi import AirSnapshot, category

Commute = Literal["car", "two_wheeler", "auto", "bus", "metro", "cycle", "walk", "wfh"]

# Approximate tailpipe CO2 per passenger-km (kg). Sources vary; values are indicative, labelled as estimates in the UI.
CO2_KG_PER_PKM: dict[str, float] = {
    "car": 0.17, "two_wheeler": 0.06, "auto": 0.08, "bus": 0.03,
    "metro": 0.02, "cycle": 0.0, "walk": 0.0, "wfh": 0.0,
}
# Rough share of on-road PM2.5 exposure a commuter gets in each mode, relative to an open two-wheeler (1.0).
EXPOSURE_FACTOR: dict[str, float] = {
    "car": 0.6, "two_wheeler": 1.0, "auto": 1.0, "bus": 0.8,
    "metro": 0.4, "cycle": 1.2, "walk": 1.1, "wfh": 0.2,
}
SENSITIVE = {"asthma", "copd", "heart", "pregnant", "elderly", "child"}


class Profile(BaseModel):
    city: str = Field(min_length=2, max_length=80)
    commute_mode: Commute = "two_wheeler"
    commute_km: float = Field(default=10, ge=0, le=200)
    workdays_per_week: int = Field(default=5, ge=0, le=7)
    conditions: list[Literal["asthma", "copd", "heart", "pregnant", "elderly", "child", "none"]] = Field(
        default_factory=list, max_length=7
    )
    has_purifier: bool = False
    outdoor_exercise: bool = True
    cooks_with: Literal["lpg", "electric", "biomass", "other"] = "lpg"


class Action(BaseModel):
    title: str
    detail: str
    impact: str
    kind: Literal["routine", "indoor", "alternative", "health"]


class Window(BaseModel):
    start: str
    end: str
    avg_naqi: int
    category: str


def best_windows(snap: AirSnapshot, hours: int = 2, limit: int = 2) -> list[Window]:
    """Cleanest consecutive daytime (06:00–21:00) windows in the forecast."""
    now = snap.current.get("time") or ""
    rows = [r for r in snap.hourly
            if r["naqi"] is not None and r["time"] >= now and 6 <= int(r["time"][11:13]) <= 21]
    candidates: list[Window] = []
    for i in range(len(rows) - hours + 1):
        chunk = rows[i:i + hours]
        if chunk[-1]["time"][:10] != chunk[0]["time"][:10]:
            continue
        avg = round(sum(r["naqi"] for r in chunk) / hours)
        candidates.append(Window(start=chunk[0]["time"], end=chunk[-1]["time"], avg_naqi=avg, category=category(avg)))
    candidates.sort(key=lambda w: w.avg_naqi)
    chosen: list[Window] = []
    for w in candidates:
        if all(abs(_hour_index(w.start) - _hour_index(c.start)) >= hours for c in chosen):
            chosen.append(w)
        if len(chosen) == limit:
            break
    return chosen


def _hour_index(iso: str) -> int:
    return int(iso[8:10]) * 24 + int(iso[11:13])


def commute_impact(profile: Profile, alt: str) -> tuple[float, int]:
    """Weekly CO2 saved (kg) and % change in commute PM exposure when switching to `alt`."""
    weekly_km = profile.commute_km * 2 * profile.workdays_per_week
    co2 = (CO2_KG_PER_PKM[profile.commute_mode] - CO2_KG_PER_PKM[alt]) * weekly_km
    base = EXPOSURE_FACTOR[profile.commute_mode]
    exposure_pct = round((EXPOSURE_FACTOR[alt] - base) / base * 100) if base else 0
    return round(co2, 1), exposure_pct


def build_plan(snap: AirSnapshot, profile: Profile) -> dict:
    aqi = snap.aqi or 0
    sensitive = bool(SENSITIVE.intersection(profile.conditions))
    severe_for_user = aqi > (100 if sensitive else 200)
    actions: list[Action] = []

    windows = best_windows(snap)
    if profile.outdoor_exercise:
        if windows and windows[0].avg_naqi <= (100 if sensitive else 200):
            w = windows[0]
            actions.append(Action(
                kind="routine", title=f"Move outdoor exercise to {_day(w.start, snap)} {w.start[11:16]}–{_end(w.end)}",
                detail=f"Forecast NAQI averages {w.avg_naqi} ({w.category}) then, the cleanest daytime slot.",
                impact=f"About {max(0, aqi - w.avg_naqi)} NAQI points lower than right now.",
            ))
        else:
            actions.append(Action(
                kind="routine", title="Exercise indoors today",
                detail="No daytime window is forecast to be safe enough for outdoor exercise for your profile.",
                impact="Heavy breathing outdoors can multiply the pollution you inhale several times over.",
            ))

    for alt in _better_modes(profile.commute_mode):
        co2, exp = commute_impact(profile, alt)
        if co2 > 0 or exp < 0:
            actions.append(Action(
                kind="alternative", title=f"Try {alt.replace('_', ' ')} instead of {profile.commute_mode.replace('_', ' ')}",
                detail=f"For your {profile.commute_km:g} km commute, {profile.workdays_per_week} days a week.",
                impact=f"≈{co2:g} kg CO₂ less per week; your on-road PM exposure changes by about {exp:+d}%.",
            ))
            break

    if profile.has_purifier:
        actions.append(Action(kind="indoor", title="Run the purifier in the room you sleep in",
                              detail="Close doors, set it to high for 30 min, then auto. Clean or replace the filter monthly in smog season.",
                              impact="A well-sized HEPA purifier can cut indoor PM2.5 by roughly half or more."))
    else:
        actions.append(Action(kind="indoor", title="Build a DIY box-fan filter",
                              detail="Tape a MERV-13 (or equivalent) filter to a box or pedestal fan. Cost: roughly ₹1,500–2,500.",
                              impact="Low-cost DIY filters have been shown to reduce indoor PM2.5 substantially in a closed room."))
    if aqi > 200:
        actions.append(Action(kind="indoor", title="Keep windows closed until the afternoon",
                              detail=("Ventilate briefly when the forecast is cleanest. "
                                      "Avoid incense, mosquito coils and frying without an exhaust fan."),
                              impact="Stops outdoor smog from building up indoors and removes avoidable indoor sources."))
    if profile.cooks_with == "biomass":
        actions.append(Action(kind="alternative", title="Switch cooking from biomass to LPG or induction",
                              detail="Check eligibility for PMUY LPG connections if relevant.",
                              impact="Removes one of the largest sources of household PM2.5."))
    if severe_for_user:
        actions.append(Action(kind="health", title="Wear a well-fitted N95 outdoors",
                              detail="Cloth and surgical masks do little against PM2.5."
                              + (" Keep your rescue medication on you." if sensitive else ""),
                              impact="A properly fitted N95 filters ≥95% of fine particles."))

    if sensitive:  # health protection first for sensitive groups
        actions.sort(key=lambda a: a.kind != "health")
    return {
        "location": snap.location.__dict__, "current": snap.current, "sensitive": sensitive,
        "windows": [w.model_dump() for w in windows], "actions": [a.model_dump() for a in actions],
        "hourly": [h for h in snap.hourly if h["time"] >= (snap.current.get("time") or "")][:48],
    }


def _day(iso: str, snap: AirSnapshot) -> str:
    today = (snap.current.get("time") or iso)[:10]
    return "today" if iso[:10] == today else "tomorrow" if iso[:10] > today else iso[:10]


def _end(iso: str) -> str:
    h = (int(iso[11:13]) + 1) % 24
    return f"{h:02d}:00"


def _better_modes(mode: str) -> list[str]:
    order = {"car": ["metro", "bus", "wfh"], "two_wheeler": ["metro", "bus"], "auto": ["metro", "bus"],
             "bus": ["metro"], "cycle": ["metro"], "walk": ["metro"], "metro": [], "wfh": []}
    return order.get(mode, [])
