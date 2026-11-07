"""Bootstrap a scoped server client using backend SQL; never alter schema history.

Read DATABASE_URL and SEARCH_SPHERE_BOOTSTRAP_API_KEY from the operator environment.
Dry-run is the default. The key must be generated/stored outside command arguments.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import uuid

import asyncpg

SCOPES = (
    "documents:read", "documents:write", "documents:delete", "collections:manage",
    "search:execute", "answers:generate", "clients:manage",
    "conversations:read", "conversations:write",
)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--tenant", action="append", required=True)
    parser.add_argument("--scope", action="append", required=True, choices=SCOPES)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.client_id):
        parser.error("client-id must be 1-64 letters, digits, underscores or hyphens")
    if not args.name.strip() or len(args.name) > 255:
        parser.error("name must be 1-255 characters")
    if any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", tenant) for tenant in args.tenant):
        parser.error("tenants must be explicit identifiers; wildcard tenants are not allowed")
    return args


async def register(connection, args, api_key):
    if not api_key or len(api_key) < 32:
        raise ValueError("A generated server credential of at least 32 characters is required")
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
    scopes, tenants = sorted(set(args.scope)), sorted(set(args.tenant))
    async with connection.transaction():
        # Serialize bootstrap retries for the same identity without disclosing keys.
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", args.client_id)
        current = await connection.fetchrow(
            "SELECT id, status, revoked_at FROM service_clients WHERE client_id = $1 FOR UPDATE", args.client_id,
        )
        if current and (current["status"] != "active" or current["revoked_at"] is not None):
            raise ValueError("Refusing to reactivate a revoked client through bootstrap")
        if current and not args.replace_existing:
            raise ValueError("Client already exists; explicit --replace-existing is required")
        action = "update" if current else "create"
        if args.apply:
            if current:
                await connection.execute(
                    "UPDATE service_clients SET name=$1, api_key_hash=$2, api_key_prefix=$3, "
                    "allowed_scopes=$4::json, authorized_tenants=$5::json, updated_at=now() WHERE id=$6",
                    args.name, fingerprint, api_key[:12], json.dumps(scopes), json.dumps(tenants), current["id"],
                )
            else:
                await connection.execute(
                    "INSERT INTO service_clients (id,client_id,name,api_key_hash,api_key_prefix,status,"
                    "allowed_scopes,authorized_tenants,rate_limit_per_minute,created_at,updated_at) "
                    "VALUES ($1,$2,$3,$4,$5,'active',$6::json,$7::json,120,now(),now())",
                    str(uuid.uuid4()), args.client_id, args.name, fingerprint, api_key[:12], json.dumps(scopes), json.dumps(tenants),
                )
        return {"client_id": args.client_id, "action": action if args.apply else f"would_{action}", "scopes": scopes, "tenants": tenants}


async def main(argv=None):
    args = arguments(argv)
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise ValueError("DATABASE_URL is required")
    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        result = await register(connection, args, os.environ.get("SEARCH_SPHERE_BOOTSTRAP_API_KEY", ""))
        print(json.dumps(result))
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001 -- redact external database errors at CLI boundary
        # Database/driver errors can contain parameters or connection credentials.
        # Report the category only; never print a DSN, key, or raw database exception.
        raise SystemExit(f"Client bootstrap failed ({type(error).__name__}); check configuration and explicit scope.") from None
