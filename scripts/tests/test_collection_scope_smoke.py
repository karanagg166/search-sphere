"""Offline synthetic release transport: no provider or production credentials."""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collection_scope_smoke import CLIENT, ROOT, TENANT, ScopeChecks

KEY = "public-synthetic-scope-api-credential-001"
OWNER = "public-synthetic-scope-owner-key-001"


class Catalog:
    def __init__(self, failure=None):
        self.rows, self.calls, self.failure = {}, [], failure

    def request(self, url, *, method="GET", headers=None, data=None):
        parsed = urlsplit(url)
        path = parsed.path
        self.calls.append((method, path))
        if headers.get("X-Tenant-ID") != TENANT or headers.get("X-Client-ID") != CLIENT:
            return (200 if self.failure == "tenant" else 403), {}, {}
        if path == "/api/v1/documents":
            return (200 if self.failure == "permissions" else 403), {}, {}
        assert path == ROOT or path.startswith(ROOT + "/")
        identifier = path[len(ROOT) + 1:] if path != ROOT else None
        bound = headers.get("X-Collection-ID")
        if method == "POST":
            identifier = data["collection_id"]
        if bound and identifier and bound != identifier and self.failure != {"GET": "read", "DELETE": "delete", "POST": "write"}[method]:
            return 403, {}, {}
        if method == "POST":
            self.rows[identifier] = {"client_id": CLIENT, "tenant_id": TENANT, "collection_id": identifier}
            if self.failure == "timeout":
                raise TimeoutError("PRIVATE SYNTHETIC DIAGNOSTIC")
            return 201, {}, self.rows[identifier]
        if identifier is not None:
            if method == "GET":
                return (200, {}, self.rows[identifier]) if identifier in self.rows else (404, {}, {})
            if self.failure == "cleanup":
                return 503, {}, {"detail": "PRIVATE SYNTHETIC DIAGNOSTIC"}
            self.rows.pop(identifier, None)
            return 200, {}, {"success": True}
        if headers.get("X-Subject-ID") and not bound and self.failure != "subject":
            return 403, {}, {}
        rows = [row for row in self.rows.values() if not bound or row["collection_id"] == bound or self.failure == "leak"]
        offset = int(parse_qs(parsed.query).get("offset", ["0"])[0])
        limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
        return 200, {}, {"total": len(rows), "collections": rows[offset:offset + limit]}


def test_full_scope_gate_removes_only_owned_catalogs(capsys):
    api = Catalog()
    foreign = ScopeChecks(KEY, "different-public-synthetic-owner-key-001", request=api.request)
    identifier = foreign.identity("0" * 32, "a")
    api.rows[identifier] = {"client_id": CLIENT, "tenant_id": TENANT, "collection_id": identifier}
    ScopeChecks(KEY, OWNER, request=api.request).run()
    assert list(api.rows) == [identifier]
    output = capsys.readouterr().out
    assert "PASS delegated collection" in output and KEY not in output and OWNER not in output


@pytest.mark.parametrize("failure", ["leak", "read", "write", "delete", "subject", "tenant", "permissions", "timeout"])
def test_scope_violation_never_passes_and_cleans_owned_rows(failure, capsys):
    api = Catalog(failure)
    with pytest.raises(AssertionError):
        ScopeChecks(KEY, OWNER, request=api.request).run()
    assert api.rows == {}
    output = capsys.readouterr().out
    assert "PASS delegated collection" not in output and "PRIVATE" not in output


def test_failed_cleanup_is_retained_and_recovered_on_next_attempt(capsys):
    api = Catalog("cleanup")
    with pytest.raises(AssertionError):
        ScopeChecks(KEY, OWNER, request=api.request).run()
    assert len(api.rows) == 2
    api.failure = None
    ScopeChecks(KEY, OWNER, request=api.request).run()
    assert api.rows == {}
    assert "FAIL collection cleanup verification" in capsys.readouterr().out


@pytest.mark.parametrize("key,owner", [("", OWNER), (KEY, ""), ("short", OWNER)])
def test_required_credentials_cannot_be_omitted(key, owner):
    api = Catalog()
    with pytest.raises(AssertionError):
        ScopeChecks(key, owner, request=api.request)
    assert api.calls == []
