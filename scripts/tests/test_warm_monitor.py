import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts import warm_monitor
from scripts.warm_monitor import (
    admission,
    classify_status,
    probe_with_cold_recovery,
    transition,
)


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


def test_owner_override_requires_explicit_suspension_acceptance_and_live_inventory():
    now = datetime.now(UTC)
    requested = {"enabled": True, "mode": "owner_requested_single_service", "accept_allowance_suspension": True}
    assert admission(requested, now) == (False, "single_free_service_not_verified")
    assert admission(requested, now, verified_single_service=True) == (True, "single_service_unmetered_owner_override")
    requested["accept_allowance_suspension"] = "true"
    assert not admission(requested, now, verified_single_service=True)[0]
    requested["enabled"] = False
    assert admission(requested, now, verified_single_service=True) == (False, "disabled")


def test_live_inventory_cannot_admit_an_extra_service_foreign_workspace_or_paid_plan():
    from copy import deepcopy

    from scripts.warm_monitor import (
        BASE_URL,
        SERVICE_ID,
        WORKSPACE_ID,
        single_service_inventory,
    )

    rows = [{"service": {"id": SERVICE_ID, "ownerId": WORKSPACE_ID, "type": "web_service",
                         "repo": "https://github.com/karanagg166/search-sphere", "suspended": "not_suspended",
                         "serviceDetails": {"plan": "free", "url": BASE_URL}}}]
    assert single_service_inventory(rows)
    assert not single_service_inventory(rows * 2)
    assert not single_service_inventory([])
    for field, value in [("ownerId", "foreign"), ("id", "different"), ("suspended", "suspended")]:
        invalid = deepcopy(rows)
        invalid[0]["service"][field] = value
        assert not single_service_inventory(invalid)
    rows[0]["service"]["serviceDetails"]["plan"] = "starter"
    assert not single_service_inventory(rows)


def test_classify_status_covers_all_unambiguous_states():
    assert classify_status(ping_sent=False, healthy=False, reason="disabled", cold_start=False, failures=0, recovered=False) == "monitoring_disabled"
    assert classify_status(ping_sent=False, healthy=False, reason="workspace_budget_exceeded", cold_start=False, failures=0, recovered=False) == "skipped_budget"
    assert classify_status(ping_sent=True, healthy=True, reason="ready", cold_start=False, failures=0, recovered=False) == "healthy"
    assert classify_status(ping_sent=True, healthy=True, reason="cold_start_recovered", cold_start=True, failures=0, recovered=False) == "backend_cold_start"
    assert classify_status(ping_sent=True, healthy=True, reason="ready", cold_start=False, failures=0, recovered=True) == "recovery_confirmed"
    assert classify_status(ping_sent=True, healthy=False, reason="cold_or_network_unavailable", cold_start=False, failures=1, recovered=False) == "backend_unreachable"
    assert classify_status(ping_sent=True, healthy=False, reason="database_unavailable", cold_start=False, failures=1, recovered=False) == "database_unavailable"
    assert classify_status(ping_sent=True, healthy=False, reason="worker_unavailable", cold_start=False, failures=1, recovered=False) == "worker_unavailable"
    assert classify_status(ping_sent=True, healthy=False, reason="cold_or_network_unavailable", cold_start=False, failures=3, recovered=False) == "repeated_failure"


def test_probe_with_cold_recovery_distinguishes_cold_start_from_unreachable(monkeypatch):
    calls = iter([
        (False, "cold_or_network_unavailable", {"elapsed_seconds": 30.0}),
        (True, "ready", {"elapsed_seconds": 6.2, "git_commit": "abc"}),
    ])
    monkeypatch.setattr(warm_monitor, "probe_with_details", lambda *a, **kw: next(calls))
    healthy, reason, cold_start, details = probe_with_cold_recovery("/health", sleep_fn=lambda _: None)
    assert healthy is True
    assert reason == "cold_start_recovered"
    assert cold_start is True
    assert "cold_start_duration_seconds" in details

    monkeypatch.setattr(warm_monitor, "probe_with_details", lambda *a, **kw: (False, "cold_or_network_unavailable", {"elapsed_seconds": 30.0}))
    healthy2, reason2, cold_start2, _ = probe_with_cold_recovery("/health", sleep_fn=lambda _: None)
    assert healthy2 is False
    assert reason2 == "cold_or_network_unavailable"
    assert cold_start2 is False


def test_main_exits_nonzero_on_any_failed_admitted_probe_and_recovers(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "warm-state.json"
    summary_file = tmp_path / "summary.md"
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({
        "enabled": True,
        "mode": "owner_requested_single_service",
        "accept_allowance_suspension": True,
    }))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setattr(warm_monitor, "verify_single_free_service", lambda: True)

    # First run: probe fails even after cold-start retry -> must exit 1 (never green!)
    monkeypatch.setattr(warm_monitor, "probe_with_cold_recovery", lambda *a, **kw: (False, "cold_or_network_unavailable", False, {"elapsed_seconds": 65.0}))
    monkeypatch.setattr("sys.argv", ["warm_monitor.py", "--state", str(state_file), "--config", str(cfg_file)])
    assert warm_monitor.main() == 1
    out1 = json.loads(capsys.readouterr().out.splitlines()[0])
    assert out1["status"] == "backend_unreachable"
    assert out1["failures"] == 1
    assert out1["ping_sent"] is True

    # Second run: probe succeeds -> recovery_confirmed and exit 0
    monkeypatch.setattr(warm_monitor, "probe_with_cold_recovery", lambda *a, **kw: (True, "ready", False, {"elapsed_seconds": 0.4, "worker": "running"}))
    assert warm_monitor.main() == 0
    out2 = json.loads(capsys.readouterr().out.splitlines()[0])
    assert out2["status"] == "recovery_confirmed"
    assert out2["recovered"] is True
    assert out2["failures"] == 0
    assert "Search Sphere Warm Monitor" in summary_file.read_text()
