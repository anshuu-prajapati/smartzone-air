import pytest

from app import aqi
from app.planner import Profile, best_windows, build_plan, commute_impact


@pytest.mark.parametrize(
    ("pm25", "expected"),
    [(0, 0), (30, 50), (45, 75), (60, 100), (90, 200), (120, 300), (250, 400), (900, 500)],
)
def test_pm25_sub_index_breakpoints(pm25, expected):
    assert aqi.sub_index(pm25, aqi._PM25_BREAKPOINTS) == expected


def test_naqi_takes_worst_pollutant_and_handles_missing():
    assert aqi.naqi(25, 300) == aqi.sub_index(300, aqi._PM10_BREAKPOINTS)
    assert aqi.naqi(None, None) is None
    assert aqi.naqi(-5, None) is None


@pytest.mark.parametrize(("value", "name"), [(None, "Unknown"), (40, "Good"), (100, "Satisfactory"),
                                             (150, "Moderate"), (250, "Poor"), (350, "Very Poor"), (480, "Severe")])
def test_category(value, name):
    assert aqi.category(value) == name


def test_best_windows_finds_clean_afternoon(snapshot):
    windows = best_windows(snapshot)
    assert windows, "expected at least one window"
    assert windows[0].start.endswith("14:00")
    assert windows[0].avg_naqi <= 100
    # windows do not overlap
    if len(windows) > 1:
        assert windows[0].start != windows[1].start


def test_commute_impact_car_to_metro():
    p = Profile(city="Delhi", commute_mode="car", commute_km=10, workdays_per_week=5)
    co2, exposure = commute_impact(p, "metro")
    assert co2 == pytest.approx((0.17 - 0.02) * 100, abs=0.1)
    assert exposure < 0


def test_profile_validation_rejects_bad_input():
    with pytest.raises(ValueError):
        Profile(city="X")
    with pytest.raises(ValueError):
        Profile(city="Delhi", commute_km=-1)
    with pytest.raises(ValueError):
        Profile(city="Delhi", commute_mode="rocket")


def test_plan_for_sensitive_user_includes_health_action(snapshot):
    p = Profile(city="Delhi", conditions=["asthma"], commute_mode="two_wheeler")
    plan = build_plan(snapshot, p)
    kinds = [a["kind"] for a in plan["actions"]]
    assert plan["sensitive"] is True
    assert "health" in kinds and "alternative" in kinds and "indoor" in kinds
    assert any("N95" in a["title"] for a in plan["actions"])


def test_plan_suggests_indoor_exercise_when_no_safe_window(snapshot):
    snapshot.hourly = [dict(h, naqi=350, category="Very Poor") for h in snapshot.hourly]
    plan = build_plan(snapshot, Profile(city="Delhi", conditions=["child"]))
    assert any(a["title"] == "Exercise indoors today" for a in plan["actions"])


def test_biomass_cooking_triggers_fuel_switch(snapshot):
    plan = build_plan(snapshot, Profile(city="Delhi", cooks_with="biomass"))
    assert any("biomass" in a["title"].lower() for a in plan["actions"])


def test_windows_ignore_past_hours(snapshot):
    snapshot.current["time"] = "2026-11-27T16:00"
    windows = best_windows(snapshot)
    assert all(w.start >= "2026-11-27T16:00" for w in windows)


def test_exercise_title_says_today(snapshot):
    plan = build_plan(snapshot, Profile(city="Delhi"))
    assert plan["actions"][0]["title"].startswith("Move outdoor exercise to today 14:00")


def test_sensitive_user_sees_health_first_and_future_hours_only(snapshot):
    plan = build_plan(snapshot, Profile(city="Delhi", conditions=["asthma"]))
    assert plan["actions"][0]["kind"] == "health"
    assert all(h["time"] >= snapshot.current["time"] for h in plan["hourly"])
