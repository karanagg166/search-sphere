from datetime import UTC, datetime, timedelta

import pytest

from scripts.warm_monitor import admission, transition


def config(now, **kwargs):
    return {"enabled": True, "terms_verified": True, "sampled_at": now.isoformat(), "used_workspace_hours": 0,
            "other_remaining_hours": 0, "safety_hours": 24, "free_service_count": 1,
            "start_hour_utc": 0, "end_hour_utc": 24, **kwargs}


@pytest.mark.parametrize("month,allowed", [(4, True), (1, False)])
def test_full_month_includes_safety_and_shared_750_hour_cap(month, allowed):
    now = datetime(2026, month, 1, 12, tzinfo=UTC)
    assert admission(config(now), now)[0] is allowed  # 720+24 fits; 744+24 does not.


def test_two_continuous_services_exceed_shared_budget():
    now = datetime(2026, 4, 1, 12, tzinfo=UTC)
    assert not admission(config(now, free_service_count=2), now)[0]
    assert not admission(config(now, free_service_count=2, other_services_budget_verified=True, other_remaining_hours=720), now)[0]


def test_unknown_stale_exhausted_disabled_and_delayed_schedules_are_safe():
    now = datetime(2026, 4, 15, 12, tzinfo=UTC)
    for overrides in [{"enabled": False}, {"used_workspace_hours": 750}, {"terms_verified": False},
                      {"sampled_at": (now - timedelta(days=2)).isoformat()}, {"other_remaining_hours": float("nan")}]:
        assert not admission(config(now, **overrides), now)[0]
    assert admission(config(now, start_hour_utc=6, end_hour_utc=18), now)[0]
    assert not admission(config(now, start_hour_utc=6, end_hour_utc=10), now)[0]
    assert admission([], now) == (False, "invalid_budget")


def test_alert_only_on_third_consecutive_failure_and_reset_after_recovery():
    now = datetime.now(UTC)
    state = {}
    for number in range(1, 6):
        state = transition(state, False, now + timedelta(minutes=10 * number), "http_5xx")
        assert state["alert"] is (number == 3)
    recovered = transition(state, True, now + timedelta(hours=3), "ready")
    assert recovered["recovered"] and recovered["failures"] == 0 and recovered["schedule_gap"]
