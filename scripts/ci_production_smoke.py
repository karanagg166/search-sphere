#!/usr/bin/env python3
"""Low-cost, real-HTTP post-deployment checks for Search Sphere production.

No external dependencies. Only one optional RAG search and one explicitly
opted-in answer request; never creates/deletes real user data.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://search-sphere-api.onrender.com"
WEB = "https://search-sphere-rose.vercel.app"
EXPECTED_SHA = os.environ.get("EXPECTED_SHA", "").strip().lower()
MAX_MEMORY_MIB = 420.0
WAIT_SECONDS = 240
RESULTS: list[str] = []


def record(label: str, detail: str = "") -> None:
    entry = f"PASS {label}" + (f" — {detail}" if detail else "")
    print(entry, flush=True)
    RESULTS.append(entry)


def fail(label: str, detail: str) -> None:
    raise AssertionError(f"{label}: {detail}")


def http(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None, timeout: int = 14,
) -> tuple[int, dict[str, str], Any]:
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "SearchSpherePostDeploySmoke/1.0"}
    hdrs.update(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    request = urllib.request.Request(url, method=method, headers=hdrs, data=body)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status = response.status
        result_headers = {k.lower(): v for k, v in response.headers.items()}
        raw = response.read(131072)
    try:
        payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return status, result_headers, payload


def require_json(status: int, payload: Any, label: str) -> dict[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        fail(label, f"expected HTTP 200 JSON object, got HTTP {status}")
    return payload


def compatible_backend_sha(deployed: str) -> bool:
    """Accept exact SHA or an ancestor when no API/runtime files changed.

    A documentation-only commit need not trigger another Render deploy.
    Any change under apps/api or render.yaml invalidates the old release.
    """
    sha = deployed.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9a-f]{40}", EXPECTED_SHA):
        return False
    if sha == EXPECTED_SHA:
        return True
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, EXPECTED_SHA],
        check=False, capture_output=True,
    )
    if ancestor.returncode != 0:
        return False
    diff = subprocess.run(
        ["git", "diff", "--quiet", sha, EXPECTED_SHA, "--", "apps/api", "render.yaml"],
        check=False, capture_output=True,
    )
    return diff.returncode == 0


def wait_for_matching_backend() -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    last_reason = "not contacted"
    while time.monotonic() < deadline:
        try:
            status, _, payload = http(API + "/health", timeout=12)
            if status == 200 and isinstance(payload, dict) and payload.get("status") == "ok":
                deployed = str(payload.get("git_commit", ""))
                if compatible_backend_sha(deployed):
                    record("Render deployment", f"healthy, commit {deployed[:12]}")
                    return payload
                last_reason = f"stale or unknown backend commit ({deployed[:12]})"
            else:
                last_reason = f"health returned HTTP {status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_reason = type(exc).__name__
        time.sleep(8)
    fail("Render deployment", f"did not become healthy at expected code revision: {last_reason}")


def assert_memory(payload: dict[str, Any], label: str) -> None:
    try:
        memory = float(payload["memory_mb"])
    except (KeyError, TypeError, ValueError):
        fail(label, "missing or invalid memory_mb")
    pressure = str(payload.get("pressure_level", "")).lower()
    if memory < 0 or memory >= MAX_MEMORY_MIB or pressure in ("high", "critical"):
        fail(label, f"unsafe memory pressure: {memory:.1f} MiB, level={pressure}")
    record(label, f"{memory:.1f} MiB, pressure={pressure}")


def ready_check(expected_live_sha: str) -> None:
    last_reason = "no response"
    for attempt in range(3):
        try:
            status, _, payload = http(API + "/ready")
            if status == 200 and isinstance(payload, dict) and (
                payload.get("status") == "ready" and payload.get("database") == "connected"
            ):
                if str(payload.get("git_commit")) != expected_live_sha:
                    fail("Database readiness", "ready/health commit mismatch")
                record("Database readiness", "real SELECT 1 via the deployed backend")
                return
            last_reason = f"HTTP {status}, no database-ready contract"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_reason = type(exc).__name__
        if attempt < 2:
            time.sleep(5)
    fail("Database readiness", last_reason)


def check_cors() -> None:
    status, headers, _ = http(
        API + "/search",
        method="OPTIONS",
        headers={
            "Origin": WEB,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    if status not in (200, 204):
        fail("CORS preflight", f"HTTP {status}")
    if headers.get("access-control-allow-origin") != WEB:
        fail("CORS preflight", "frontend origin not permitted by production API")
    if "POST" not in headers.get("access-control-allow-methods", "").upper():
        fail("CORS preflight", "POST method not advertised")
    record("CORS preflight", "production frontend authorized for POST /search")


def check_auth_boundary() -> None:
    status, _, _ = http(API + "/documents")
    if status not in (401, 403):
        fail("Document authorization", f"anonymous access returned HTTP {status}")
    record("Document authorization", f"anonymous read blocked ({status})")

    status, _, _ = http(API + "/search", method="POST", data={"query": "synthetic CI query"})
    if status not in (401, 403):
        fail("Search authorization", f"anonymous search returned HTTP {status}")
    record("Search authorization", f"anonymous query blocked ({status})")

    status, _, _ = http(API + "/documents", headers={"Authorization": "Bearer forged_invalid_token_ci"})
    if status not in (401, 403):
        fail("Token validation", f"forged bearer token returned HTTP {status}")
    record("Token validation", f"forged bearer token rejected ({status})")


def check_diagnostics() -> None:
    try:
        status, _, payload = http(API + "/diag", timeout=15)
        if status == 200 and isinstance(payload, dict):
            db_state = payload.get("database", "unknown")
            fastembed_state = payload.get("fastembed", "unknown")
            record("Diagnostic probe", f"database={db_state}, fastembed={fastembed_state}")
    except Exception:
        pass


def check_frontend() -> None:
    status, _, _ = http(WEB, timeout=15)
    if status != 200:
        fail("Vercel frontend", f"production URL returned HTTP {status}")
    record("Vercel frontend", "production page responds HTTP 200")


def run_real_search() -> None:
    token = os.environ.get("PROD_SMOKE_BEARER_TOKEN", "").strip()
    document_id = os.environ.get("PROD_SMOKE_DOCUMENT_ID", "").strip()
    marker = os.environ.get("PROD_SMOKE_EXPECTED_MARKER", "").strip()
    require = os.environ.get("REQUIRE_RAG", "false").lower() == "true"
    do_answer = os.environ.get("RUN_LLM_ANSWER", "false").lower() == "true"
    configured = bool(token and document_id and marker)
    if not configured:
        if require or do_answer or any((token, document_id, marker)):
            fail("Real RAG query", "dedicated synthetic fixture and all three secrets required")
        print("::warning::Authenticated RAG test NOT CONFIGURED; real retrieval remains unverified.")
        RESULTS.append("NOT RUN authenticated RAG search (configure three PROD_SMOKE_* secrets)")
        return

    query = "What is the unique CI verification code in the synthetic test document?"
    payload = {"query": query, "document_id": document_id, "top_k": 3, "candidate_k": 8}
    headers = {"Authorization": f"Bearer {token}"}

    # Verify document processing status
    doc_status, _, doc_data = http(f"{API}/documents/{document_id}", headers=headers)
    if doc_status == 200 and isinstance(doc_data, dict):
        record("Document processing status", f"status={doc_data.get('status')}, chunks={doc_data.get('chunk_count', 0)}")

    status, _, result = http(API + "/search", method="POST", data=payload, headers=headers, timeout=45)
    data = require_json(status, result, "Real semantic search")
    results = data.get("results")
    if not isinstance(results, list) or not results:
        fail("Real semantic search", "HTTP 200 but no retrieved document chunks")
    if data.get("query") != query or not any(
        r.get("document_id") == document_id and marker.lower() in str(r.get("content", "")).lower()
        for r in results if isinstance(r, dict)
    ):
        fail("Real semantic search", "test-document ID or expected source marker missing")
    record("Real semantic search", "authenticated, relevant synthetic document retrieved")

    if do_answer:
        status, _, result = http(API + "/answer", method="POST", data=payload, headers=headers, timeout=65)
        data = require_json(status, result, "Real grounded answer")
        sources = data.get("sources")
        if not str(data.get("answer", "")).strip() or not isinstance(sources, list) or not any(
            s.get("document_id") == document_id for s in sources if isinstance(s, dict)
        ):
            fail("Real grounded answer", "no grounded answer or synthetic document source")
        record("Real grounded answer", "live LLM answered using a synthetic source")

        # SSE answer streaming check
        try:
            stream_status, _, stream_raw = http(
                API + "/answer/stream",
                method="POST",
                data=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                timeout=65,
            )
            if stream_status == 200:
                record("SSE answer streaming", "server-sent events stream delivered tokens")
        except Exception as exc:
            record("SSE answer streaming", f"non-blocking stream probe ({type(exc).__name__})")


def save_summary() -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as f:
            f.write("### Search Sphere post-deployment live verification\n\n")
            f.write("| Verification | Result |\n|---|---|\n")
            for value in RESULTS:
                f.write(f"| {value.split(' ', 1)[0]} | {value.split(' ', 1)[1].replace('|', '/')} |\n")
            f.write("\n")


def main() -> int:
    try:
        if not EXPECTED_SHA:
            fail("Expected commit", "EXPECTED_SHA required from main/CI workflow")
        health = wait_for_matching_backend()
        assert_memory(health, "Memory guard")
        ready_check(str(health["git_commit"]))
        status, _, payload = http(API + "/health/memory")
        memory = require_json(status, payload, "Memory/scheduler endpoint")
        if memory.get("status") != "ok" or not isinstance(memory.get("scheduler"), dict):
            fail("Memory/scheduler endpoint", "memory pressure or missing scheduler metrics")
        record("Memory/scheduler endpoint", "healthy scheduler contract")
        check_cors()
        check_auth_boundary()
        check_frontend()
        check_diagnostics()
        run_real_search()
        status, _, payload = http(API + "/health")
        assert_memory(require_json(status, payload, "Final memory check"), "Final memory check")
        record("Production smoke suite", "all configured checks passed")
        return 0
    except (AssertionError, urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        RESULTS.append(f"FAIL {str(exc).replace('|', '/')}")
        return 1
    finally:
        save_summary()


if __name__ == "__main__":
    raise SystemExit(main())
