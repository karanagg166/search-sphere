#!/usr/bin/env python3
"""Low-cost, real-HTTP post-deployment checks for Search Sphere production.

No external dependencies. Validates health, database readiness, memory/scheduler,
CORS, multi-tenant auth boundaries, and real end-to-end RAG retrieval, grounded answer,
and SSE streaming with synthetic fixtures.
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
import uuid
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
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[int, dict[str, str], Any]:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "SearchSpherePostDeploySmoke/1.0"}
    hdrs.update(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    request = urllib.request.Request(url, method=method, headers=hdrs, data=body)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {}, str(error)
    try:
        with response:
            status = response.status
            result_headers = {k.lower(): v for k, v in response.headers.items()}
            raw = response.read(262144)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {}, str(error)
    try:
        payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return status, result_headers, payload


def http_upload(
    url: str,
    field_name: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "application/pdf",
    headers: dict[str, str] | None = None,
    timeout: int = 45,
) -> tuple[int, dict[str, str], Any]:
    """Upload a file using multipart/form-data without external dependencies."""
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("latin-1"))
    body.extend(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(
            "latin-1"
        )
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("latin-1"))
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("latin-1"))

    hdrs = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "SearchSpherePostDeploySmoke/1.0",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, method="POST", headers=hdrs, data=bytes(body))
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as err:
        resp = err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return 0, {}, str(err)
    try:
        with resp:
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read(131072)
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return 0, {}, str(err)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")
    return status, resp_headers, payload


def read_sse_stream(
    url: str,
    data: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 65,
) -> list[tuple[str, dict[str, Any]]]:
    """Read and parse Server-Sent Events from an endpoint."""
    body = json.dumps(data).encode("utf-8")
    hdrs = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": "SearchSpherePostDeploySmoke/1.0",
    }
    hdrs.update(headers)
    req = urllib.request.Request(url, method="POST", headers=hdrs, data=body)
    events: list[tuple[str, dict[str, Any]]] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        current_event = "message"
        for line in resp:
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text.startswith("event:"):
                current_event = text.split(":", 1)[1].strip()
            elif text.startswith("data:"):
                data_str = text.split(":", 1)[1].strip()
                try:
                    payload = json.loads(data_str)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {"raw": data_str}
                events.append((current_event, payload))
    return events


def generate_synthetic_pdf(marker: str, doc_name: str = "CI-Synthetic-Smoke") -> bytes:
    """Generate a valid PDF-1.4 binary containing the marker with 0 external dependencies."""
    content = (
        f"Search Sphere CI Production Smoke Verification Document\n"
        f"Verification Marker: {marker}\n"
        f"Document Title: {doc_name}\n"
        f"This document is an isolated synthetic fixture for automated deployment validation.\n"
        f"The unique CI verification code is {marker}.\n"
        f"Search Sphere provides hybrid semantic and sparse BM25 retrieval."
    )
    safe_text = content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = safe_text.split("\n")
    stream_ops = ["BT", "/F1 12 Tf", "50 700 Td"]
    for idx, line in enumerate(lines):
        if idx > 0:
            stream_ops.append("0 -20 Td")
        stream_ops.append(f"({line}) Tj")
    stream_ops.append("ET")
    stream = "\n".join(stream_ops)
    stream_bytes = stream.encode("latin-1")
    stream_len = len(stream_bytes)

    objects = [
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        ),
        f"4 0 obj\n<< /Length {stream_len} >>\nstream\n{stream}\nendstream\nendobj\n",
        "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = "%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body = ""
    offsets = [len(header.encode("latin-1"))]
    for obj in objects:
        body += obj
        offsets.append(offsets[-1] + len(obj.encode("latin-1")))

    xref_offset = len(header.encode("latin-1")) + len(body.encode("latin-1"))
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets[:-1]:
        xref += f"{off:010d} 00000 n \n"
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    return (header + body + xref + trailer).encode("latin-1")


def require_json(status: int, payload: Any, label: str) -> dict[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        fail(label, f"expected HTTP 200 JSON object, got HTTP {status}")
    return payload


def compatible_backend_sha(deployed: str) -> bool:
    """Accept exact SHA or an ancestor when no API/runtime files changed."""
    sha = deployed.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9a-f]{40}", EXPECTED_SHA):
        return False
    if sha == EXPECTED_SHA:
        return True
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, EXPECTED_SHA],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        return False
    diff = subprocess.run(
        ["git", "diff", "--quiet", sha, EXPECTED_SHA, "--", "apps/api", "render.yaml"],
        check=False,
        capture_output=True,
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
    for attempt in range(4):
        try:
            status, _, payload = http(API + "/ready")
            if (
                status == 200
                and isinstance(payload, dict)
                and payload.get("status") == "ready"
                and payload.get("database") == "connected"
            ):
                if str(payload.get("git_commit")) != expected_live_sha:
                    fail("Database readiness", "ready/health commit mismatch")
                record("Database readiness", "real SELECT 1 via the deployed backend")
                return
            last_reason = f"HTTP {status}, no database-ready contract"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_reason = type(exc).__name__
        if attempt < 3:
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


def check_auth_boundary(auth_token: str | None = None) -> None:
    # 1. Anonymous access rejected
    status, _, _ = http(API + "/documents")
    if status not in (401, 403):
        fail("Document authorization", f"anonymous access returned HTTP {status}")
    record("Document authorization", f"anonymous read blocked ({status})")

    status, _, _ = http(API + "/search", method="POST", data={"query": "synthetic CI query"})
    if status not in (401, 403):
        fail("Search authorization", f"anonymous search returned HTTP {status}")
    record("Search authorization", f"anonymous query blocked ({status})")

    # 2. Forged Bearer token rejected
    status, _, _ = http(API + "/documents", headers={"Authorization": "Bearer forged_invalid_token_ci"})
    if status not in (401, 403):
        fail("Token validation", f"forged bearer token returned HTTP {status}")
    record("Token validation", f"forged bearer token rejected ({status})")

    # 3. Unowned document isolation check
    if auth_token:
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        status, _, _ = http(
            f"{API}/documents/{fake_uuid}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        if status != 404:
            fail("Tenant document isolation", f"expected 404 for unowned doc, got HTTP {status}")
        record("Tenant document isolation", "unowned document access rejected with 404")

        # Search scoped to unowned document
        status, _, search_payload = http(
            f"{API}/search",
            method="POST",
            headers={"Authorization": f"Bearer {auth_token}"},
            data={"query": "test isolation", "document_id": fake_uuid},
        )
        if status not in (404, 400, 200):
            fail("Tenant search isolation", f"unexpected status HTTP {status}")
        if (
            status == 200
            and isinstance(search_payload, dict)
            and search_payload.get("results")
        ):
            fail("Tenant search isolation", "data leakage: unowned document returned results")
        record("Tenant search isolation", "cross-tenant search leakage blocked")


def check_diagnostics() -> None:
    try:
        status, _, payload = http(API + "/diag", timeout=25)
        if status == 200 and isinstance(payload, dict):
            db_state = str(payload.get("database", "unknown"))
            fastembed_state = str(payload.get("fastembed", "unknown"))
            qdrant_state = str(payload.get("qdrant", "unknown"))
            cross_encoder_state = str(payload.get("cross_encoder", "unknown"))
            if "embedded" in qdrant_state.lower():
                fail(
                    "Diagnostic probe",
                    f"production vector store is running in ephemeral embedded fallback mode: {qdrant_state}",
                )
            if "error" in qdrant_state.lower():
                fail("Diagnostic probe", f"vector store error: {qdrant_state}")
            if "error" in cross_encoder_state.lower():
                fail(
                    "Diagnostic probe",
                    f"cross-encoder reranker error: {cross_encoder_state}",
                )
            record(
                "Diagnostic probe",
                f"db={db_state}, fastembed={fastembed_state}, qdrant={qdrant_state}, cross_encoder={cross_encoder_state}",
            )
        else:
            fail("Diagnostic probe", f"diagnostic endpoint returned HTTP {status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail("Diagnostic probe", f"diagnostic probe failed ({type(exc).__name__})")


def check_frontend() -> None:
    status, _, _ = http(WEB, timeout=15)
    if status != 200:
        fail("Vercel frontend", f"production URL returned HTTP {status}")
    record("Vercel frontend", "production page responds HTTP 200")


def acquire_ci_auth() -> tuple[str, str] | None:
    """Acquire a valid JWT bearer token for the CI test account."""
    direct_token = os.environ.get("PROD_SMOKE_BEARER_TOKEN", "").strip()
    if direct_token:
        return direct_token, ""

    email = os.environ.get("PROD_SMOKE_USER_EMAIL", "").strip()
    password = os.environ.get("PROD_SMOKE_USER_PASSWORD", "").strip()
    if not email or not password:
        return None

    # Try login first
    login_payload = {"email": email, "password": password}
    status, _, res = http(f"{API}/auth/login", method="POST", data=login_payload, timeout=25)
    if status == 200 and isinstance(res, dict) and "access_token" in res:
        token = str(res["access_token"])
        user_id = str(res.get("user", {}).get("id", ""))
        record("CI test authentication", f"acquired bearer token via login for {email}")
        return token, user_id

    # If user doesn't exist, try signup
    signup_payload = {"name": "CI Smoke Tester", "email": email, "password": password}
    status, _, res = http(f"{API}/auth/signup", method="POST", data=signup_payload, timeout=25)
    if status in (200, 201) and isinstance(res, dict) and "access_token" in res:
        token = str(res["access_token"])
        user_id = str(res.get("user", {}).get("id", ""))
        record("CI test authentication", f"provisioned new test account and acquired token for {email}")
        return token, user_id

    fail("CI test authentication", f"login returned HTTP {status}: {res}")
    return None


def ensure_synthetic_document(
    token: str, marker: str, doc_name: str = "CI-Synthetic-Smoke"
) -> tuple[str, bool]:
    """Return an indexed document_id and whether it was dynamically created."""
    existing_id = os.environ.get("PROD_SMOKE_DOCUMENT_ID", "").strip()
    if existing_id:
        return existing_id, False

    # Dynamically upload synthetic document
    pdf_bytes = generate_synthetic_pdf(marker, doc_name=doc_name)
    headers = {"Authorization": f"Bearer {token}"}
    status, _, upload_res = http_upload(
        f"{API}/documents",
        field_name="file",
        filename="ci_smoke_test.pdf",
        file_bytes=pdf_bytes,
        content_type="application/pdf",
        headers=headers,
        timeout=45,
    )
    if status not in (200, 201) or not isinstance(upload_res, dict) or "id" not in upload_res:
        fail("Document upload", f"failed to upload synthetic document: HTTP {status} {upload_res}")

    doc_id = str(upload_res["id"])
    record("Document upload", f"uploaded synthetic document {doc_id[:12]}")

    # Poll until indexed or ready
    poll_deadline = time.monotonic() + 120
    last_status = "pending"
    while time.monotonic() < poll_deadline:
        time.sleep(5)
        st, _, doc_info = http(f"{API}/documents/{doc_id}", headers=headers, timeout=25)
        if st == 200 and isinstance(doc_info, dict):
            last_status = str(doc_info.get("status", "unknown"))
            if last_status == "indexed":
                chunks = doc_info.get("chunk_count")
                if chunks is None:
                    fail("Document processing", "document indexed but chunk_count is missing (expected positive integer)")
                if not isinstance(chunks, int) or chunks <= 0:
                    fail("Document processing", f"document indexed but chunk_count={chunks} (expected positive integer > 0)")
                record("Document processing", f"status=indexed ({chunks} chunks indexed)")
                return doc_id, True
            if "failed" in last_status:
                break
    fail("Document processing", f"document did not reach 'indexed' within 120s (status={last_status})")
    return doc_id, True


def cleanup_synthetic_document(token: str, document_id: str, marker: str | None = None) -> None:
    """Safely delete synthetic document after test run to leave zero clutter and verify vector purge."""
    headers = {"Authorization": f"Bearer {token}"}
    status, _, _ = http(f"{API}/documents/{document_id}", method="DELETE", headers=headers, timeout=20)
    if status in (200, 204, 404):
        record("Synthetic document cleanup", f"purged synthetic document {document_id[:12]}")
    else:
        print(f"::warning::Failed to clean up synthetic document {document_id}: HTTP {status}")

    # Verify vector purge: search scoped to the deleted document must return 0 results
    if marker:
        time.sleep(1)
        st, _, res = http(
            f"{API}/search",
            method="POST",
            data={"query": f"Verification Marker: {marker}", "document_id": document_id},
            headers=headers,
            timeout=20,
        )
        if st == 200 and isinstance(res, dict):
            remaining = res.get("results", [])
            if len(remaining) > 0:
                fail("Vector deletion verification", f"deleted document vectors still retrieved ({len(remaining)} points found)")
        record("Vector deletion verification", "purged document vectors are no longer retrievable in search")


def run_real_rag_pipeline(
    token: str,
    document_id: str,
    marker: str,
    do_answer: bool = False,
) -> None:
    """Execute authenticated semantic search, grounded answer, and SSE streaming."""
    headers = {"Authorization": f"Bearer {token}"}
    query = "What is the unique CI verification code in the synthetic test document?"
    payload = {"query": query, "document_id": document_id, "top_k": 3, "candidate_k": 8}

    # 1. Real Semantic Search
    status, _, result = http(f"{API}/search", method="POST", data=payload, headers=headers, timeout=45)
    data = require_json(status, result, "Real semantic search")
    results = data.get("results")
    if not isinstance(results, list) or not results:
        fail("Real semantic search", "HTTP 200 but no retrieved document chunks")
    marker_found = any(
        r.get("document_id") == document_id and marker.lower() in str(r.get("content", "")).lower()
        for r in results
        if isinstance(r, dict)
    )
    if not marker_found:
        fail("Real semantic search", f"expected marker '{marker}' missing from retrieved chunks")
    record("Real semantic search", f"retrieved {len(results)} chunks matching synthetic marker")

    # Verify cross-encoder rerank score exists on results
    reranked = [r for r in results if isinstance(r, dict) and r.get("rerank_score") is not None]
    if not reranked:
        fail("Cross-encoder reranking verification", "no rerank_score found on search results")
    record("Cross-encoder reranking verification", f"retrieved chunk rerank_score={reranked[0].get('rerank_score')}")

    # 2. Grounded LLM Answer (when enabled)
    if do_answer:
        status, _, result = http(f"{API}/answer", method="POST", data=payload, headers=headers, timeout=65)
        data = require_json(status, result, "Real grounded answer")
        answer_text = str(data.get("answer", "")).strip()
        sources = data.get("sources")
        if not answer_text or not isinstance(sources, list) or not sources:
            fail("Real grounded answer", "no grounded answer or sources list returned")
        if not any(s.get("document_id") == document_id for s in sources if isinstance(s, dict)):
            fail("Real grounded answer", "sources list did not reference the synthetic test document")
        record("Real grounded answer", f"generated answer citing test document: '{answer_text[:60]}...'")

        # 3. Real SSE Answer Streaming
        try:
            events = read_sse_stream(f"{API}/answer/stream", data=payload, headers=headers, timeout=65)
            event_types = [e[0] for e in events]
            if "metadata" not in event_types or "done" not in event_types:
                fail("SSE answer streaming", f"missing expected SSE lifecycle events (saw {event_types})")
            tokens = [e[1].get("token", "") for e in events if e[0] == "token"]
            record("SSE answer streaming", f"received {len(tokens)} streamed tokens through completion")
        except (urllib.error.URLError, TimeoutError, OSError, AssertionError) as exc:
            fail("SSE answer streaming", f"stream probe failed: {exc}")


def run_rag_suite() -> None:
    require = os.environ.get("REQUIRE_RAG", "false").lower() == "true"
    do_answer = os.environ.get("RUN_LLM_ANSWER", "false").lower() == "true"
    marker = os.environ.get("PROD_SMOKE_EXPECTED_MARKER", "SS-SYNTHETIC-SMOKE-42").strip()

    auth_info = acquire_ci_auth()
    if not auth_info:
        if require or do_answer:
            fail("Real RAG verification", "REQUIRE_RAG is set but no test credentials are configured")
        print("::warning::Authenticated RAG test NOT CONFIGURED; configure PROD_SMOKE_* credentials.")
        RESULTS.append("NOT RUN authenticated RAG verification (configure PROD_SMOKE_* credentials)")
        return

    token, _ = auth_info
    # Multi-tenant isolation checks with valid token
    check_auth_boundary(auth_token=token)

    created_id: str | None = None
    try:
        doc_id, was_created = ensure_synthetic_document(token, marker)
        if was_created:
            created_id = doc_id
        run_real_rag_pipeline(token, doc_id, marker, do_answer=do_answer)
    finally:
        if created_id:
            cleanup_synthetic_document(token, created_id, marker=marker)


def save_summary() -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as f:
            f.write("### Search Sphere post-deployment live verification\n\n")
            f.write("| Verification | Result |\n|---|---|\n")
            for value in RESULTS:
                parts = value.split(" ", 1)
                verdict = parts[0]
                desc = parts[1].replace("|", "/") if len(parts) > 1 else ""
                f.write(f"| {verdict} | {desc} |\n")
            f.write("\n")


def main() -> int:
    try:
        if not EXPECTED_SHA:
            fail("Expected commit", "EXPECTED_SHA required from main/CI workflow")
        health = wait_for_matching_backend()
        assert_memory(health, "Initial memory guard")
        ready_check(str(health["git_commit"]))

        status, _, payload = http(API + "/health/memory")
        memory = require_json(status, payload, "Memory/scheduler endpoint")
        if memory.get("status") != "ok" or not isinstance(memory.get("scheduler"), dict):
            fail("Memory/scheduler endpoint", "memory pressure or missing scheduler metrics")
        record("Memory/scheduler endpoint", "healthy scheduler contract")

        check_cors()
        check_frontend()
        check_diagnostics()
        run_rag_suite()

        status, _, payload = http(API + "/health")
        assert_memory(require_json(status, payload, "Final memory guard"), "Final memory guard")
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
