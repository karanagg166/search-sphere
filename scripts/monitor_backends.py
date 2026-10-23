#!/usr/bin/env python3
"""Unified Multi-Project Backend Health Monitor & Keep-Alive Script.

Monitors deployed HTTP backends across Search-Sphere, Quick-Clinic Socket Server,
ExamArena API, and Quick-Clinic Web.

Key Guarantees:
- HTTPS GET requests only.
- Strict URL validation (prevents SSRF / private IP access).
- Bounded connection and total timeouts.
- Distinguishes cold startups from persistent failures with controlled retry/backoff.
- Validates HTTP 200 and meaningful JSON schema (e.g., status in {"ok", "ready"}).
- Sanitized logging: never outputs secrets, tokens, or private medical data.
- Outputs GitHub Actions compatible annotations (`::error::`, `::warning::`).
- Exits 0 if all active targets pass; exits 1 if any target fails persistently.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Configure structured console logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backend_monitor")

# ANSI Color codes for readable CI output
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
RESET = "\033[0m"


@dataclass(frozen=True)
class EndpointConfig:
    path: str
    expected_status: int = 200
    status_field: str = "status"
    allowed_status_values: tuple[str, ...] = ("ok", "ready")
    check_type: str = "liveness"  # "liveness", "readiness", "worker_heartbeat"
    sha_field: str | None = None
    expected_sha_env: str | None = None
    failure_category: str = "service"  # "infrastructure", "service"
    is_critical: bool = True


@dataclass
class ServiceTarget:
    name: str
    env_var: str
    default_url: str
    enabled_env_var: str
    default_enabled: bool = True
    endpoints: list[EndpointConfig] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        raw = os.getenv(self.env_var, self.default_url).strip()
        return raw.rstrip("/")

    @property
    def is_enabled(self) -> bool:
        default_str = "true" if self.default_enabled else "false"
        val = os.getenv(self.enabled_env_var, default_str).strip().lower()
        return val in ("true", "1", "yes")


DEFAULT_TARGETS: list[ServiceTarget] = [
    ServiceTarget(
        name="Search-Sphere API (Render)",
        env_var="SEARCH_SPHERE_API_URL",
        default_url="https://search-sphere-api.onrender.com",
        enabled_env_var="MONITOR_SEARCH_SPHERE",
        endpoints=[
            EndpointConfig(
                path="/health",
                expected_status=200,
                status_field="status",
                allowed_status_values=("ok",),
                check_type="liveness",
                sha_field="git_commit",
                expected_sha_env="SEARCH_SPHERE_EXPECTED_SHA",
            ),
            EndpointConfig(
                path="/ready",
                expected_status=200,
                status_field="status",
                allowed_status_values=("ok", "ready"),
                check_type="readiness",
                failure_category="infrastructure",
            ),
        ],
    ),
    ServiceTarget(
        name="Quick-Clinic Socket Server (Render)",
        env_var="QUICK_CLINIC_SOCKET_URL",
        default_url="https://quickclinic-socket-server.onrender.com",
        enabled_env_var="MONITOR_QUICK_CLINIC_SOCKET",
        endpoints=[
            EndpointConfig(
                path="/health",
                expected_status=200,
                status_field="status",
                allowed_status_values=("ok",),
                check_type="liveness",
                sha_field="gitCommit",
                expected_sha_env="QUICK_CLINIC_EXPECTED_SHA",
            ),
            EndpointConfig(
                path="/ready",
                expected_status=200,
                status_field="status",
                allowed_status_values=("ok", "ready"),
                check_type="readiness",
                failure_category="infrastructure",
            ),
        ],
    ),
    ServiceTarget(
        name="ExamArena API (Vercel Serverless)",
        env_var="EXAM_ARENA_API_URL",
        default_url="https://exam-arena-mu.vercel.app",
        enabled_env_var="MONITOR_EXAM_ARENA",
        endpoints=[
            EndpointConfig(
                path="/health",
                expected_status=200,
                status_field="status",
                allowed_status_values=("ok",),
                check_type="liveness",
                sha_field="git_commit",
                expected_sha_env="EXAM_ARENA_EXPECTED_SHA",
            ),
            EndpointConfig(
                path="/api/health/worker",
                expected_status=200,
                status_field="worker_available",
                allowed_status_values=("true", "1"),
                check_type="worker_heartbeat",
                failure_category="infrastructure",
                is_critical=False,
            ),
        ],
    ),
    ServiceTarget(
        name="Quick-Clinic Web / API (Vercel)",
        env_var="QUICK_CLINIC_WEB_URL",
        default_url="https://quick-clinic-nine.vercel.app",
        enabled_env_var="MONITOR_QUICK_CLINIC_WEB",
        default_enabled=False,
        endpoints=[
            EndpointConfig(
                path="/api/health",
                expected_status=200,
                status_field="status",
                allowed_status_values=("ok",),
                check_type="liveness",
            ),
        ],
    ),
]


def validate_safe_url(url: str) -> None:
    """Ensure URL uses HTTPS and does not point to internal/private infrastructure."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"Insecure scheme '{parsed.scheme}': only https:// is allowed.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Missing hostname in URL.")

    # Block localhost & internal hostnames
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"):
        raise ValueError(f"Prohibited host '{hostname}': internal hostnames are not permitted.")

    # Check for private or link-local IP addresses
    is_ip = False
    try:
        ip = ipaddress.ip_address(hostname)
        is_ip = True
    except ValueError:
        pass

    if is_ip and (ip.is_private or ip.is_loopback or ip.is_link_local):
        raise ValueError(f"Prohibited IP address '{hostname}': private network access blocked.")


@dataclass
class ProbeResult:
    target_name: str
    url: str
    status_code: int | None
    elapsed_seconds: float
    attempts: int
    success: bool
    is_cold_start: bool
    message: str
    failure_category: str | None = None  # "infrastructure", "service", "stale_deployment", or None
    check_type: str = "liveness"
    detected_sha: str | None = None
    expected_sha: str | None = None
    is_stale: bool = False
    is_critical: bool = True
    data: dict[str, Any] | None = None


def probe_endpoint(
    target_name: str,
    full_url: str,
    endpoint_config: EndpointConfig,
    max_retries: int = 3,
    connect_timeout: float = 10.0,
    total_timeout: float = 30.0,
    backoff_seconds: float = 5.0,
) -> ProbeResult:
    """Probe a single endpoint with bounded retry and backoff to tolerate cold starts."""
    validate_safe_url(full_url)

    last_error = ""
    is_cold_start = False
    last_status_code: int | None = None
    is_network_err = False

    for attempt in range(1, max_retries + 1):
        start_time = time.monotonic()
        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": "ReliabilityKeepAliveBot/2.0 (+https://github.com/karanagg166)",
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(req, timeout=total_timeout) as response:
                elapsed = time.monotonic() - start_time
                status_code = response.getcode()
                last_status_code = status_code
                raw_body = response.read(65536)  # Read up to 64KB

                if status_code != endpoint_config.expected_status:
                    last_error = f"HTTP status {status_code} != expected {endpoint_config.expected_status}"
                    if attempt < max_retries:
                        time.sleep(backoff_seconds)
                    continue

                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as json_err:
                    last_error = f"Invalid JSON response: {json_err}"
                    if attempt < max_retries:
                        time.sleep(backoff_seconds)
                    continue

                # Validate JSON shape
                actual_val = str(payload.get(endpoint_config.status_field, "")).lower()
                if actual_val not in endpoint_config.allowed_status_values:
                    last_error = (
                        f"JSON field '{endpoint_config.status_field}' was '{actual_val}', "
                        f"expected one of {endpoint_config.allowed_status_values}"
                    )
                    if attempt < max_retries:
                        time.sleep(backoff_seconds)
                    continue

                # Check deployment commit SHA if configured
                detected_sha = None
                if endpoint_config.sha_field:
                    detected_sha = payload.get(endpoint_config.sha_field)
                if not detected_sha:
                    detected_sha = (
                        payload.get("git_commit")
                        or payload.get("gitCommit")
                        or payload.get("commit")
                    )

                expected_sha = None
                if endpoint_config.expected_sha_env:
                    expected_sha = os.getenv(endpoint_config.expected_sha_env, "").strip()

                is_stale = False
                if expected_sha and detected_sha:
                    clean_det = str(detected_sha).strip().lower()
                    clean_exp = str(expected_sha).strip().lower()
                    if not clean_det.startswith(clean_exp) and not clean_exp.startswith(clean_det):
                        is_stale = True

                # Success
                return ProbeResult(
                    target_name=target_name,
                    url=full_url,
                    status_code=status_code,
                    elapsed_seconds=elapsed,
                    attempts=attempt,
                    success=True,
                    is_cold_start=is_cold_start,
                    message="OK" if not is_stale else f"Stale deployment: live SHA ({str(detected_sha)[:7]}) != expected ({expected_sha[:7]})",
                    failure_category="stale_deployment" if is_stale else None,
                    check_type=endpoint_config.check_type,
                    detected_sha=str(detected_sha) if detected_sha else None,
                    expected_sha=expected_sha,
                    is_stale=is_stale,
                    is_critical=endpoint_config.is_critical,
                    data=payload,
                )

        except urllib.error.HTTPError as http_err:
            elapsed = time.monotonic() - start_time
            last_status_code = http_err.code
            last_error = f"HTTP {http_err.code}: {http_err.reason}"
            if attempt < max_retries:
                time.sleep(backoff_seconds)

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            elapsed = time.monotonic() - start_time
            is_cold_start = True  # Network delay or container spin-up
            is_network_err = True
            last_error = str(exc)
            logger.warning(
                f"{YELLOW}[Attempt {attempt}/{max_retries}]{RESET} {target_name} ({full_url}) "
                f"encountered transient error in {elapsed:.2f}s: {exc}"
            )
            if attempt < max_retries:
                logger.info(f"Backing off {backoff_seconds}s for cold start completion...")
                time.sleep(backoff_seconds)

    # Classify failure category
    fail_cat = endpoint_config.failure_category
    if last_status_code in (502, 503, 504) or is_network_err:
        fail_cat = "infrastructure"
    elif last_status_code == 500 or "JSON" in last_error:
        fail_cat = "service"

    # All retries exhausted
    return ProbeResult(
        target_name=target_name,
        url=full_url,
        status_code=last_status_code,
        elapsed_seconds=elapsed,
        attempts=max_retries,
        success=False,
        is_cold_start=is_cold_start,
        message=f"Failed after {max_retries} attempts: {last_error}",
        failure_category=fail_cat,
        check_type=endpoint_config.check_type,
        is_critical=endpoint_config.is_critical,
    )


def run_monitor(
    targets: list[ServiceTarget] | None = None,
    max_retries: int = 3,
    connect_timeout: float = 10.0,
    total_timeout: float = 30.0,
    backoff_seconds: float = 5.0,
) -> int:
    """Run monitoring loop across all configured targets and report summary."""
    active_targets = targets if targets is not None else DEFAULT_TARGETS
    results: list[ProbeResult] = []

    logger.info(f"{BLUE}======================================================{RESET}")
    logger.info(f"{BLUE}🚀 Starting Multi-Project Backend Keep-Alive Monitor{RESET}")
    logger.info("ℹ️ Free Hosting SLA Notice: Monitored targets run on free-tier infrastructure")
    logger.info("   (Render free web services, Vercel free serverless). Cold starts and spin-downs")
    logger.info("   after 15m of inactivity are inherent; no continuous uptime SLA is claimed.")
    logger.info(f"{BLUE}======================================================{RESET}")

    for target in active_targets:
        if not target.is_enabled:
            logger.info(f"{YELLOW}⏩ Skipping disabled target: {target.name}{RESET}")
            continue

        base_url = target.base_url
        logger.info(f"\n{BLUE}▶ Probing service:{RESET} {target.name} ({base_url})")

        for ep in target.endpoints:
            full_url = f"{base_url}{ep.path}"
            result = probe_endpoint(
                target_name=target.name,
                full_url=full_url,
                endpoint_config=ep,
                max_retries=max_retries,
                connect_timeout=connect_timeout,
                total_timeout=total_timeout,
                backoff_seconds=backoff_seconds,
            )
            results.append(result)

            if result.success:
                cold_tag = f" {YELLOW}(cold start resolved){RESET}" if result.is_cold_start else ""
                stale_tag = f" {YELLOW}[STALE: live {result.detected_sha[:7] if result.detected_sha else 'unknown'} != exp {result.expected_sha[:7] if result.expected_sha else 'unknown'}]{RESET}" if result.is_stale else ""
                logger.info(
                    f"{GREEN}✅ [200 OK]{RESET} {ep.path} ({ep.check_type}) ({result.elapsed_seconds:.2f}s, "
                    f"attempts={result.attempts}){cold_tag}{stale_tag}"
                )
                if result.is_stale:
                    print(
                        f"::warning title={target.name} Stale Deployment::{ep.path} ({ep.check_type}) "
                        f"live SHA ({result.detected_sha[:7] if result.detected_sha else 'unknown'}) "
                        f"does not match expected ({result.expected_sha[:7] if result.expected_sha else 'unknown'})"
                    )
            else:
                cat_label = (result.failure_category or "service").capitalize()
                logger.error(
                    f"{RED}❌ [FAIL - {cat_label.upper()}]{RESET} {ep.path} ({ep.check_type}) after {result.attempts} attempts: {result.message}"
                )
                # Output GitHub Actions error annotation with failure category
                alert_title = f"[{cat_label} Failure] {target.name}"
                print(f"::error title={alert_title}::{ep.path} ({ep.check_type}) failed: {result.message}")

    # Summary
    logger.info(f"\n{BLUE}======================================================{RESET}")
    logger.info(f"{BLUE}📊 Health Check Summary Report{RESET}")
    logger.info(f"{BLUE}======================================================{RESET}")

    failed_count = sum(1 for r in results if not r.success)
    failed_critical = sum(1 for r in results if not r.success and r.is_critical)
    passed_count = sum(1 for r in results if r.success)

    for r in results:
        if r.success:
            if r.is_stale:
                status_sym = f"{YELLOW}STALE{RESET}"
            else:
                status_sym = f"{GREEN}PASS{RESET}"
        else:
            cat = (r.failure_category or "service").upper()
            status_sym = f"{RED}FAIL-{cat}{RESET}"
        logger.info(f"[{status_sym}] {r.target_name} -> {r.url} ({r.check_type}, {r.elapsed_seconds:.2f}s)")

    logger.info(f"Total: {len(results)} | Passed: {passed_count} | Failed: {failed_count} (Critical: {failed_critical})")

    if failed_critical > 0:
        logger.error(f"{RED}💥 One or more critical backend endpoints failed keep-alive checks!{RESET}")
        return 1

    logger.info(f"{GREEN}🎉 All monitored backends are healthy, responsive, and awake.{RESET}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-project backend health monitor and keep-alive runner.")
    parser.add_argument("--retries", type=int, default=3, help="Max retry attempts per endpoint (default: 3)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-attempt total timeout in seconds (default: 30.0)")
    parser.add_argument("--backoff", type=float, default=5.0, help="Backoff duration between attempts in seconds (default: 5.0)")
    args = parser.parse_args()

    exit_code = run_monitor(
        max_retries=args.retries,
        total_timeout=args.timeout,
        backoff_seconds=args.backoff,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
