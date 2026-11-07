import hashlib
import importlib.util
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

spec = importlib.util.spec_from_file_location("client_bootstrap", Path(__file__).parents[1] / "register_service_client.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("INGESTION_TEST_DATABASE_URL", "")
    assert url, "INGESTION_TEST_DATABASE_URL is required for the client bootstrap gate"
    parsed = urlsplit(url)
    assert parsed.hostname in {"localhost", "127.0.0.1", "host.docker.internal", "postgres"}
    assert parsed.path in {"/ss_test", "/search_sphere_test"}
    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    client_id = "bootstrap_fixture_" + uuid.uuid4().hex[:12]
    try:
        yield connection, client_id
    finally:
        await connection.execute("DELETE FROM service_clients WHERE client_id = $1", client_id)
        await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [["documents:read"], ["documents:read", "conversations:read", "conversations:write"]])
async def test_bootstrap_is_dry_run_first_scoped_and_never_changes_migration_history(database, scopes):
    connection, client_id = database
    revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    args = bootstrap.arguments([
        "--client-id", client_id, "--name", "Synthetic bootstrap fixture",
        "--tenant", "synthetic-school", *[part for scope in scopes for part in ["--scope", scope]],
    ])
    key = "synthetic-bootstrap-test-key-" + uuid.uuid4().hex
    result = await bootstrap.register(connection, args, key)
    assert result["action"] == "would_create" and key not in str(result)
    assert await connection.fetchval("SELECT count(*) FROM service_clients WHERE client_id=$1", client_id) == 0
    args.apply = True
    assert (await bootstrap.register(connection, args, key))["action"] == "create"
    row = await connection.fetchrow("SELECT api_key_hash, authorized_tenants, allowed_scopes FROM service_clients WHERE client_id=$1", client_id)
    assert row["api_key_hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert row["authorized_tenants"] == '["synthetic-school"]'
    assert json.loads(row["allowed_scopes"]) == sorted(scopes)
    with pytest.raises(ValueError, match="replace-existing"):
        await bootstrap.register(connection, args, key)
    args.replace_existing = True
    assert (await bootstrap.register(connection, args, key))["action"] == "update"
    await connection.execute("UPDATE service_clients SET status='revoked', revoked_at=now() WHERE client_id=$1", client_id)
    with pytest.raises(ValueError, match="reactivate"):
        await bootstrap.register(connection, args, key)
    assert await connection.fetchval("SELECT version_num FROM alembic_version") == revision


@pytest.mark.parametrize("tenant", ["*", "../another-tenant", ""])
def test_bootstrap_rejects_wildcard_or_invalid_tenants(tenant):
    with pytest.raises(SystemExit):
        bootstrap.arguments([
            "--client-id", "synthetic", "--name", "Synthetic fixture", "--tenant", tenant, "--scope", "documents:read",
        ])
