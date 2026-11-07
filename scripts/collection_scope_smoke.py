"""Authenticated, model-free collection metadata scope checks after deployment."""

import hmac
import os
import re
import sys
import uuid

if __package__:
    from .ci_production_smoke import API, http, ready_check, wait_for_matching_backend
else:
    from ci_production_smoke import API, http, ready_check, wait_for_matching_backend

CLIENT = TENANT = "release_scope_checks"
ROOT = "/api/v1/collections"


def require(value, label):
    if not value:
        raise AssertionError(label)


class ScopeChecks:
    def __init__(self, api_key, owner_key, *, request=http):
        require(isinstance(api_key, str) and len(api_key) >= 32, "Missing scoped release credential")
        require(isinstance(owner_key, str) and len(owner_key) >= 32, "Missing fixture ownership key")
        self.request, self.owner_key = request, owner_key
        self.headers = {"Authorization": "Bearer " + api_key, "X-Client-ID": CLIENT, "X-Tenant-ID": TENANT}

    def signature(self, value):
        return hmac.new(self.owner_key.encode(), ("collection-scope-v1:" + value).encode(), "sha256").hexdigest()[:24]

    def identity(self, run, label):
        value = "ci_scope_" + run + "_" + label
        return value + "_" + self.signature(value)

    def owned(self, value):
        match = re.fullmatch(r"ci_scope_([0-9a-f]{32})_([abc])_([0-9a-f]{24})", value or "")
        return bool(match and hmac.compare_digest(value, self.identity(match[1], match[2])))

    def call(self, method, path, *, headers=None, data=None):
        code, _, value = self.request(API + path, method=method, headers=headers or self.headers, data=data)
        return code, value

    def sweep(self):
        print("CHECK signed collection cleanup", flush=True)
        offset, owned = 0, set()
        for _ in range(20):
            code, value = self.call("GET", ROOT + f"?limit=100&offset={offset}")
            require(code == 200 and isinstance(value, dict) and isinstance(value.get("collections"), list)
                and type(value.get("total")) is int, "Collection cleanup listing unavailable")
            rows = value["collections"]
            for row in rows:
                identifier = row.get("collection_id")
                if self.owned(identifier):
                    require(row.get("client_id") == CLIENT and row.get("tenant_id") == TENANT, "Cleanup scope mismatch")
                    owned.add(identifier)
            offset += len(rows)
            if offset >= value["total"]:
                break
            require(rows, "Collection cleanup pagination stalled")
        else:
            raise AssertionError("Collection cleanup listing exceeds bound")
        for identifier in owned:
            code, _ = self.call("DELETE", ROOT + "/" + identifier)
            require(code in (200, 404), "Collection deletion unavailable")
            code, _ = self.call("GET", ROOT + "/" + identifier)
            require(code == 404, "Collection deletion unverified")

    def exercise(self):
        print("CHECK delegated collection CRUD and pagination", flush=True)
        run = uuid.uuid4().hex
        a, b, c = (self.identity(run, label) for label in "abc")
        for identifier in (a, b):
            code, value = self.call("POST", ROOT, data={"collection_id": identifier, "name": "Public synthetic scope fixture"})
            require(code == 201 and value.get("collection_id") == identifier, "Synthetic collection creation failed")
        bound = {**self.headers, "X-Collection-ID": a}
        code, value = self.call("GET", ROOT + "?limit=1&offset=0", headers=bound)
        require(code == 200 and value.get("total") == 1 and [row["collection_id"] for row in value.get("collections", [])] == [a], "Bound catalog rows/count invalid")
        code, value = self.call("GET", ROOT + "?limit=1&offset=1", headers=bound)
        require(code == 200 and value == {"total": 1, "collections": []}, "Bound catalog offset invalid")
        for method in ("GET", "DELETE"):
            code, _ = self.call(method, ROOT + "/" + b, headers=bound)
            require(code == 403, "Conflicting collection path accepted")
        code, _ = self.call("POST", ROOT, headers=bound, data={"collection_id": c, "name": "Public synthetic forbidden"})
        require(code == 403, "Conflicting collection create accepted")
        code, _ = self.call("GET", ROOT + "/" + b)
        require(code == 200, "Foreign bound deletion altered the other collection")
        code, _ = self.call("GET", ROOT, headers={**self.headers, "X-Subject-ID": "synthetic_scope_subject"})
        require(code == 403, "Subject-only catalog expanded to tenant")
        code, value = self.call("GET", ROOT, headers={**bound, "X-Subject-ID": "synthetic_scope_subject"})
        require(code == 200 and value.get("total") == 1, "Explicit subject/collection delegation failed")
        for header, value in (("X-Tenant-ID", "unapproved_scope_tenant"), ("X-Client-ID", "unapproved_scope_client")):
            code, _ = self.call("GET", ROOT, headers={**self.headers, header: value})
            require(code == 403, "Foreign client/tenant was not denied")
        code, _ = self.call("GET", "/api/v1/documents")
        require(code == 403, "Metadata-only release key can read documents")

    def run(self):
        primary, cleanup = None, None
        try:
            self.sweep()
            self.exercise()
        except Exception as error:  # noqa: BLE001 -- preserve failure and attempt owned cleanup for every error
            primary = type(error).__name__
        try:
            self.sweep()
        except Exception as error:  # noqa: BLE001 -- report unverified cleanup without exposing response details
            cleanup = type(error).__name__
        if primary:
            print("FAIL collection scope assertions (" + primary + ")", flush=True)
        if cleanup:
            print("FAIL collection cleanup verification (" + cleanup + ")", flush=True)
        require(not primary and not cleanup, "Collection scope gate failed")
        print("PASS delegated collection scope and signed cleanup", flush=True)


def main():
    checks = ScopeChecks(os.environ.get("RELEASE_SCOPE_API_KEY", ""), os.environ.get("PROD_SMOKE_FIXTURE_KEY", ""))
    require(re.fullmatch(r"[0-9a-f]{40}", os.environ.get("EXPECTED_SHA", "")), "Missing exact serving revision")
    health = wait_for_matching_backend()
    ready_check(health["git_commit"])
    checks.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- CLI boundary must redact credentials and upstream responses
        print("FAIL collection release gate (" + type(error).__name__ + ")", file=sys.stderr)
        raise SystemExit(1) from None
