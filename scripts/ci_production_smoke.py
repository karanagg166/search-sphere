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
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

if __package__:
    from .smoke_fixtures import (
        FixtureManager,
        signed_filename,
        signing_key,
        verified_filename,
    )
else:
    from smoke_fixtures import (
        FixtureManager,
        signed_filename,
        signing_key,
        verified_filename,
    )


API = "https://search-sphere-api.onrender.com"
WEB = "https://search-sphere-rose.vercel.app"
EXPECTED_SHA = os.environ.get("EXPECTED_SHA", "").strip().lower()
MAX_MEMORY_MIB = 420.0
WAIT_SECONDS = 240
RESULTS: list[str] = []
SYNTHETIC_DOCUMENT_IDS: set[str] = set()
ACTIVE_CI_TOKEN: str | None = None
ACTIVE_CI_USER_ID: str | None = None
ACTIVE_CI_EMAIL: str | None = None

MANIFEST_PATH = Path(".ci_smoke_manifest.json")
CI_RUN_ID = uuid.uuid4().hex
fixture_manager = FixtureManager(MANIFEST_PATH, CI_RUN_ID)


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
    """A release gate requires the exact intended revision."""
    sha = deployed.strip().lower()
    return bool(re.fullmatch(r"[0-9a-f]{40}", EXPECTED_SHA) and sha == EXPECTED_SHA)


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
            inference = payload.get("inference", {})
            if payload.get("database") != "ok" or payload.get("vector_store") != "supabase":
                fail("Diagnostic probe", "PostgreSQL/pgvector is not the configured healthy backend")
            if (
                not isinstance(inference, dict)
                or inference.get("provider") != "cohere"
                or inference.get("mode") != "hosted"
                or inference.get("embedding_model") != "embed-english-light-v3.0"
                or inference.get("rerank_model") != "rerank-v3.5"
                or payload.get("local_model_weights") is not False
            ):
                fail("Diagnostic probe", "Hosted-only inference metadata is missing or unexpected")
            record("Diagnostic probe", "PostgreSQL/pgvector and hosted Cohere configuration verified; inference is tested by the authenticated journey")
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
        record("CI test authentication", "authenticated dedicated synthetic account")
        return token, user_id

    # If user doesn't exist, try signup
    signup_payload = {"name": "CI Smoke Tester", "email": email, "password": password}
    status, _, res = http(f"{API}/auth/signup", method="POST", data=signup_payload, timeout=25)
    if status in (200, 201) and isinstance(res, dict) and "access_token" in res:
        token = str(res["access_token"])
        user_id = str(res.get("user", {}).get("id", ""))
        record("CI test authentication", "provisioned dedicated synthetic account")
        return token, user_id

    fail("CI test authentication", f"login/signup returned HTTP {status}")
    return None


def get_all_documents(token: str) -> list[dict[str, Any]]:
    """Use the API's offset contract; never treat a partial listing as success."""
    headers = {"Authorization": f"Bearer {token}"}
    all_docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = 50
    for offset in range(0, 2500, limit):
        status, _, payload = http(f"{API}/documents?offset={offset}&limit={limit}", headers=headers, timeout=20)
        data = require_json(status, payload, "Fixture listing")
        docs = data.get("documents")
        total = data.get("total")
        if not isinstance(docs, list) or not isinstance(total, int) or total < 0 or len(docs) > limit:
            fail("Fixture listing", "invalid document page")
        for doc in docs:
            if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or doc["id"] in seen:
                fail("Fixture listing", "invalid or repeated document identity")
            seen.add(doc["id"])
            all_docs.append(doc)
        if len(all_docs) >= total:
            return all_docs
        if len(docs) != limit:
            fail("Fixture listing", "incomplete document page")
    fail("Fixture listing", "bounded listing exceeded 2500 documents; cleanup must be reviewed")
    return []


def ensure_synthetic_document(
    token: str,
    marker: str,
    doc_name: str | None = None,
    user_id: str = "",
    email: str = "",
) -> tuple[str, bool]:
    """Return an indexed document_id and whether it was dynamically created."""
    if os.environ.get("PROD_SMOKE_DOCUMENT_ID", "").strip():
        fail("Document upload", "a fresh owned fixture is required for the complete journey")

    actual_name = doc_name or f"CI-Synthetic-Smoke-{CI_RUN_ID}"
    actual_filename = signed_filename(user_id, CI_RUN_ID)

    # Dynamically upload synthetic document
    pdf_bytes = generate_synthetic_pdf(marker, doc_name=actual_name)
    headers = {"Authorization": f"Bearer {token}"}
    status, _, upload_res = http_upload(
        f"{API}/documents",
        field_name="file",
        filename=actual_filename,
        file_bytes=pdf_bytes,
        content_type="application/pdf",
        headers=headers,
        timeout=45,
    )
    if status not in (200, 201) or not isinstance(upload_res, dict) or "id" not in upload_res:
        fail("Document upload", f"failed to upload synthetic document: HTTP {status} {upload_res}")

    doc_id = str(upload_res["id"])
    SYNTHETIC_DOCUMENT_IDS.add(doc_id)
    fixture_manager.register_fixture(
        doc_id=doc_id,
        filename=actual_filename,
        marker=marker,
        user_id=user_id,
        email=email,
    )
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


def cleanup_synthetic_document(
    token: str,
    document_id: str,
    marker: str | None = None,
    max_retries: int = 3,
) -> bool:
    """Safely delete synthetic document after test run to leave zero clutter and verify vector purge."""
    headers = {"Authorization": f"Bearer {token}"}
    success = False
    last_status = 0
    for attempt in range(1, max_retries + 1):
        status, _, _ = http(f"{API}/documents/{document_id}", method="DELETE", headers=headers, timeout=20)
        last_status = status
        if status in (200, 204, 404):
            success = True
            break
        if attempt < max_retries:
            time.sleep(1.0 * attempt)

    if not success:
        fixture_manager.mark_fixture_failed(document_id, f"HTTP {last_status}")
        fail("Synthetic document cleanup", f"deletion returned HTTP {last_status}")

    # Inaccessibility and physical cleanup are separate assertions. A missing
    # document or an empty search alone cannot prove that stored vectors are gone.
    if marker:
        st, _, res = http(
            f"{API}/search", method="POST",
            data={"query": f"Verification Marker: {marker}", "document_id": document_id},
            headers=headers, timeout=20,
        )
        if st != 404 and (st != 200 or not isinstance(res, dict) or res.get("results") != []):
            fail("Deletion access verification", f"unexpected search response HTTP {st}")
    st, _, receipt = http(f"{API}/documents/{document_id}/cleanup", headers=headers, timeout=20)
    if (
        st != 200 or not isinstance(receipt, dict)
        or receipt.get("document_id") != document_id or receipt.get("status") != "complete"
        or type(receipt.get("vectors_remaining")) is not int or receipt["vectors_remaining"] != 0
        or receipt.get("object_deleted") is not True
    ):
        fixture_manager.mark_fixture_failed(document_id, "physical_cleanup_unverified")
        fail("Physical deletion verification", f"scoped cleanup receipt missing or incomplete (HTTP {st})")
    fixture_manager.mark_fixture_cleaned(document_id)
    SYNTHETIC_DOCUMENT_IDS.discard(document_id)
    record("Synthetic document cleanup", f"purged synthetic document {document_id[:12]}")
    record("Vector deletion verification", "owner-scoped physical count is zero and private object deletion is verified")
    return True


def sweep_dangling_synthetic_documents(
    token: str, user_id: str | None = None, user_email: str | None = None,
) -> None:
    """Delete only same-owner fixtures with valid release-run signatures."""
    del user_email
    if not user_id:
        fail("Fixture provenance", "authenticated owner identity is required")
    signing_key()
    for doc in get_all_documents(token):
        if doc.get("user_id") != user_id or not verified_filename(str(doc.get("filename", "")), user_id):
            continue
        cleanup_synthetic_document(token, str(doc["id"]))


def verify_grounded_marker(answer: Any, sources: Any, document_id: str, marker: str, label: str) -> None:
    if not isinstance(answer, str) or marker.lower() not in answer.lower() or not isinstance(sources, list):
        fail(label, "expected unique marker is missing from the answer")
    cited = {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
    valid = set()
    for source in sources:
        if not isinstance(source, dict) or source.get("document_id") != document_id:
            fail(label, "source outside the synthetic document scope")
        source_id = source.get("source_id")
        if (
            type(source_id) is not int or source_id <= 0 or source_id in valid
            or type(source.get("start_page")) is not int or source["start_page"] != 1
            or source.get("end_page") != 1 or marker.lower() not in str(source.get("content", "")).lower()
        ):
            fail(label, "invalid, duplicate, or unsupported page citation")
        valid.add(source_id)
    if not cited or not cited.issubset(valid):
        fail(label, "missing or fabricated citation reference")


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
        verify_grounded_marker(answer_text, sources, document_id, marker, "Real grounded answer")
        record("Real grounded answer", "expected unique marker with valid supporting page citations")

        # 3. Real SSE Answer Streaming
        try:
            events = read_sse_stream(f"{API}/answer/stream", data=payload, headers=headers, timeout=65)
            event_types = [e[0] for e in events]
            if (
                not event_types or event_types[0] != "metadata" or event_types[-1] != "done"
                or event_types.count("done") != 1 or "error" in event_types
            ):
                fail("SSE answer streaming", f"missing expected SSE lifecycle events (saw {event_types})")
            streamed_sources = [e[1].get("sources") for e in events if e[0] == "source"]
            if len(streamed_sources) != 1:
                fail("SSE answer streaming", "exactly one source event is required")
            verify_grounded_marker(events[-1][1].get("answer"), streamed_sources[0], document_id, marker, "SSE answer streaming")
            tokens = [e[1].get("token", "") for e in events if e[0] == "token"]
            if "".join(tokens) != events[-1][1].get("answer"):
                fail("SSE answer streaming", "streamed tokens differ from the completed answer")
            record("SSE answer streaming", f"received {len(tokens)} streamed tokens through completion")
        except (urllib.error.URLError, TimeoutError, OSError, AssertionError) as exc:
            fail("SSE answer streaming", f"stream probe failed: {exc}")


def run_rag_suite() -> None:
    global ACTIVE_CI_TOKEN, ACTIVE_CI_USER_ID, ACTIVE_CI_EMAIL
    signing_key()
    marker = f"SS-SYNTH-{CI_RUN_ID}"

    auth_info = acquire_ci_auth()
    if not auth_info:
        fail("Real RAG verification", "dedicated synthetic account credentials are required")
    token, _ = auth_info
    status, _, identity = http(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = require_json(status, identity, "Fixture owner identity").get("id")
    if not isinstance(user_id, str) or not user_id:
        fail("Fixture owner identity", "authenticated user ID is required")
    email = os.environ.get("PROD_SMOKE_USER_EMAIL", "").strip()
    ACTIVE_CI_TOKEN = token
    ACTIVE_CI_USER_ID = user_id
    ACTIVE_CI_EMAIL = email

    # Proactively sweep dangling synthetic fixtures from prior runs with verified provenance
    sweep_dangling_synthetic_documents(token, user_id=user_id, user_email=email)

    # Multi-tenant isolation checks with valid token
    check_auth_boundary(auth_token=token)

    created_id: str | None = None
    try:
        doc_id, was_created = ensure_synthetic_document(
            token,
            marker,
            user_id=user_id,
            email=email,
        )
        if was_created:
            created_id = doc_id
        run_real_rag_pipeline(token, doc_id, marker, do_answer=True)
    finally:
        cleanup_failed = False
        for pending_id in list(SYNTHETIC_DOCUMENT_IDS):
            try:
                cleanup_synthetic_document(token, pending_id, marker=marker if pending_id == created_id else None)
            except (AssertionError, OSError, TimeoutError):
                cleanup_failed = True
        if cleanup_failed:
            fail("Synthetic document cleanup", "one or more physical cleanup assertions failed")


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
        mem_metrics = memory.get("scheduler", {}).get("memory", {})
        for req_key in ("current_mb", "limit_mb", "pressure_level", "raw_cgroup_mb", "estimated_working_set_mb", "process_rss_mb"):
            if req_key not in mem_metrics:
                fail("Memory/scheduler endpoint", f"missing required metric '{req_key}'")
        record("Memory/scheduler endpoint", "healthy scheduler contract and comprehensive memory telemetry")

        check_cors()
        check_frontend()
        check_diagnostics()
        run_rag_suite()

        # Allow post-RAG memory settling and garbage collection across up to 4 attempts
        final_payload: dict[str, Any] = {}
        for settle_attempt in range(4):
            if settle_attempt > 0:
                time.sleep(3)
            status, _, payload = http(API + "/health")
            if status == 200 and isinstance(payload, dict):
                final_payload = payload
                mem_mb = float(payload.get("memory_mb", 999.0))
                level = str(payload.get("pressure_level", "")).lower()
                if 0 <= mem_mb < MAX_MEMORY_MIB and level not in ("high", "critical"):
                    break

        assert_memory(require_json(200 if final_payload else 500, final_payload, "Final memory guard"), "Final memory guard")
        record("Production smoke suite", "all configured checks passed")
        return 0
    except (AssertionError, urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        RESULTS.append(f"FAIL {str(exc).replace('|', '/')}")
        return 1
    finally:
        if ACTIVE_CI_TOKEN and SYNTHETIC_DOCUMENT_IDS:
            for s_id in list(SYNTHETIC_DOCUMENT_IDS):
                try:
                    cleanup_synthetic_document(ACTIVE_CI_TOKEN, s_id)
                except (AssertionError, urllib.error.URLError, TimeoutError, OSError):
                    print("::warning::Cleanup remains unverified; the signed fixture can be recovered by the next run.")
        save_summary()


if __name__ == "__main__":
    raise SystemExit(main())
