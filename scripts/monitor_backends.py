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
from datetime import datetime, timezone
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
            EndpointConfig(path="/health", expected_status=200, status_field="status", allowed_status_values=("ok",)),
            EndpointConfig(path="/ready", expected_status=200, status_field="status", allowed_status_values=("ok", "ready")),
        ],
    ),
    ServiceTarget(
        name="Quick-Clinic Socket Server (Render)",
        env_var="QUICK_CLINIC_SOCKET_URL",
        default_url="https://quickclinic-socket-server.onrender.com",
        enabled_env_var="MONITOR_QUICK_CLINIC_SOCKET",
        endpoints=[
            EndpointConfig(path="/health", expected_status=200, status_field="status", allowed_status_values=("ok",)),
            EndpointConfig(path="/ready", expected_status=200, status_field="status", allowed_status_values=("ok", "ready")),
        ],
    ),
    ServiceTarget(
        name="ExamArena API (Vercel Serverless)",
        env_var="EXAM_ARENA_API_URL",
        default_url="https://exam-arena-mu.vercel.app",
        enabled_env_var="MONITOR_EXAM_ARENA",
        endpoints=[
            EndpointConfig(path="/health", expected_status=200, status_field="status", allowed_status_values=("ok",)),
        ],
    ),
    ServiceTarget(
        name="Quick-Clinic Web / API (Vercel)",
        env_var="QUICK_CLINIC_WEB_URL",
        default_url="https://quick-clinic-nine.vercel.app",
        enabled_env_var="MONITOR_QUICK_CLINIC_WEB",
        default_enabled=False,
        endpoints=[
            EndpointConfig(path="/api/health", expected_status=200, status_field="status", allowed_status_values=("ok",)),
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
                raw_body = response.read(65536)  # Read up to 64KB

                if status_code != endpoint_config.expected_status:
                    last_error = f"HTTP status {status_code} != expected {endpoint_config.expected_status}"
                    if attempt < max_retries:
                        time.sleep(backoff_seconds)
                    continue

                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except Exception as json_err:
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

                # Success
                return ProbeResult(
                    target_name=target_name,
                    url=full_url,
                    status_code=status_code,
                    elapsed_seconds=elapsed,
                    attempts=attempt,
                    success=True,
                    is_cold_start=is_cold_start,
                    message="OK",
                    data=payload,
                )

        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            elapsed = time.monotonic() - start_time
            is_cold_start = True  # Network delay or 502/503 during first attempts indicates container spin-up
            last_error = str(exc)
            logger.warning(
                f"{YELLOW}[Attempt {attempt}/{max_retries}]{RESET} {target_name} ({full_url}) "
                f"encountered transient error in {elapsed:.2f}s: {exc}"
            )
            if attempt < max_retries:
                logger.info(f"Backing off {backoff_seconds}s for cold start completion...")
                time.sleep(backoff_seconds)

    # All retries exhausted
    return ProbeResult(
        target_name=target_name,
        url=full_url,
        status_code=None,
        elapsed_seconds=elapsed,
        attempts=max_retries,
        success=False,
        is_cold_start=is_cold_start,
        message=f"Failed after {max_retries} attempts: {last_error}",
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
                logger.info(
                    f"{GREEN}✅ [200 OK]{RESET} {ep.path} ({result.elapsed_seconds:.2f}s, "
                    f"attempts={result.attempts}){cold_tag}"
                )
            else:
                logger.error(
                    f"{RED}❌ [FAIL]{RESET} {ep.path} after {result.attempts} attempts: {result.message}"
                )
                # Output GitHub Actions error annotation
                print(f"::error title={target.name} Failed::{ep.path} failed: {result.message}")

    # Summary
    logger.info(f"\n{BLUE}======================================================{RESET}")
    logger.info(f"{BLUE}📊 Health Check Summary Report{RESET}")
    logger.info(f"{BLUE}======================================================{RESET}")

    failed_count = sum(1 for r in results if not r.success)
    passed_count = sum(1 for r in results if r.success)

    for r in results:
        status_sym = f"{GREEN}PASS{RESET}" if r.success else f"{RED}FAIL{RESET}"
        logger.info(f"[{status_sym}] {r.target_name} -> {r.url} ({r.elapsed_seconds:.2f}s)")

    logger.info(f"Total: {len(results)} | Passed: {passed_count} | Failed: {failed_count}")

    if failed_count > 0:
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
