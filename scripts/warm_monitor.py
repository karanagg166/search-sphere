#!/usr/bin/env python3
"""Dependency-free, opt-in free-hour admission and consecutive-failure monitor.

All decisions occur BEFORE an HTTP request: a refused ping must not wake Render.
Usage snapshots are operator-supplied; this is not an authoritative billing meter.
"""

import argparse
import calendar
import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE_URL = "https://search-sphere-api.onrender.com"


def admission(config, now):
    if not isinstance(config, dict):
        return False, "invalid_budget"
    if config.get("enabled") is not True:
        return False, "disabled"
    if config.get("terms_verified") is not True:
        return False, "provider_terms_not_verified"
    try:
        sampled = datetime.fromisoformat(config["sampled_at"])
        if sampled.tzinfo is None or not 0 <= (now - sampled).total_seconds() <= 86400:
            return False, "usage_snapshot_stale"
        if sampled.strftime("%Y-%m") != now.strftime("%Y-%m"):
            return False, "usage_month_changed"
        used, reserved, safety = (float(config[key]) for key in ("used_workspace_hours", "other_remaining_hours", "safety_hours"))
        start, end = int(config["start_hour_utc"]), int(config["end_hour_utc"])
        services = int(config["free_service_count"])
        if not all(math.isfinite(value) and value >= 0 for value in (used, reserved, safety)) or safety < 24 or services < 1:
            return False, "invalid_budget"
        if not 0 <= start < end <= 24:
            return False, "invalid_window"
        days = calendar.monthrange(now.year, now.month)[1]
        # Count the planned service window plus its 15-minute idle tail for every
        # remaining day, including the snapshot day. Rounding upward is deliberate.
        remaining_days = days - sampled.day + 1
        planned = remaining_days * min(24, end - start + 0.25)
        if services > 1 and config.get("other_services_budget_verified") is not True:
            return False, "shared_budget_unknown"
        if used + reserved + planned + safety > 750:
            return False, "workspace_budget_exceeded"
        if not start <= now.hour < end:
            return False, "outside_window"
        return True, "admitted"
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "invalid_budget"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(path, *, expected_sha="", timeout=30):
    # Fixed owned target, no redirects, no auth, no inference or private responses.
    if path not in {"/health", "/ready"}:
        raise ValueError("Unsupported monitor endpoint")
    request = urllib.request.Request(BASE_URL + path, headers={"Accept": "application/json", "User-Agent": "SearchSphereMonitor/1"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(16_385)
            if response.status != 200 or len(raw) > 16_384:
                return False, "invalid_response"
            data = json.loads(raw)
        if data.get("status") not in ("ok", "ready"):
            return False, "not_ready"
        if path == "/ready" and data.get("database") != "connected":
            return False, "database_unavailable"
        if expected_sha and data.get("git_commit") != expected_sha:
            return False, "revision_mismatch"
        if data.get("worker") in {"stalled", "stopped", "recovering"}:
            return False, "worker_unavailable"
        if data.get("pressure_level") in {"high", "critical"}:
            return False, "memory_pressure"
        return True, "ready"
    except urllib.error.HTTPError as error:
        return False, "database_unavailable" if path == "/ready" and error.code == 503 else "http_5xx" if error.code >= 500 else "http_error"
    except (OSError, ValueError, TypeError, AttributeError):
        return False, "cold_or_network_unavailable"


def transition(state, success, now, reason):
    try:
        previous = max(0, min(1000, int(state.get("failures", 0))))
    except (ValueError, TypeError):
        previous = 0
    # A missing/delayed cron is a gap in evidence, not a healthy interval.
    gap = False
    try:
        gap = (now - datetime.fromisoformat(state["checked_at"])).total_seconds() > 1800
    except (KeyError, ValueError, TypeError):
        pass
    failures = 0 if success else min(1000, previous + 1)
    return {"checked_at": now.isoformat(), "failures": failures, "reason": reason,
            "schedule_gap": gap, "alert": failures == 3, "recovered": success and previous > 0}


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(state, handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="Private operator budget snapshot JSON")
    parser.add_argument("--instructions", action="store_true")
    args = parser.parse_args()
    if args.instructions:
        print(json.dumps({"endpoint": BASE_URL + "/health", "cron_utc": "7,17,27,37,47,57 * * * *", "enabled": False,
                          "reason": "Verify workspace usage and provider terms before enabling"}))
        return 0
    now = datetime.now(UTC)
    try:
        config = json.loads(args.config.read_text()) if args.config else json.loads(os.getenv("SS_WARM_CONFIG", "{}"))
        allowed, reason = admission(config, now)
    except (OSError, ValueError, TypeError):
        allowed, reason = False, "invalid_budget"
    if not allowed:
        print(json.dumps({"ping_sent": False, "reason": reason}))
        return 0
    try:
        state = json.loads(args.state.read_text()) if args.state.stat().st_size < 4096 else {}
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    healthy, reason = probe("/health", expected_sha=config.get("expected_sha", ""))
    # Reuse readiness twice daily, not on every ten-minute liveness probe.
    # A successful query, not merely a health ping, is the activity evidence.
    last_ready = state.get("ready_at", "")
    if healthy and (not last_ready or now.isoformat()[:13] != last_ready[:13]) and now.hour in {6, 18}:
        healthy, reason = probe("/ready", expected_sha=config.get("expected_sha", ""))
        if healthy:
            state["ready_at"] = now.isoformat()
    result = transition(state, healthy, now, reason)
    result["ready_at"] = state.get("ready_at", "")
    write_state(args.state, result)
    print(json.dumps({"ping_sent": True, **result}))
    if result["alert"]:
        print("::error title=Search Sphere monitor::Three consecutive admitted probes failed: " + reason)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
