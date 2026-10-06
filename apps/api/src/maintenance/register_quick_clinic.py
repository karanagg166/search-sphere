"""Provision a least privilege client; reveal its credential only in a private file."""
import argparse
import asyncio
import os

from src.db import AsyncSessionLocal
from src.schemas.service_client import ServiceClientCreateRequest
from src.services.service_client_service import ServiceClientService


async def run(output: str):
    # Exclusive creation avoids overwriting an existing credential. Never log key.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        async with AsyncSessionLocal() as session:
            _, key = await ServiceClientService(session).register_client(ServiceClientCreateRequest(
                client_id="quick_clinic", name="Quick-Clinic",
                authorized_tenants=["quick_clinic_default"],
                allowed_scopes=["documents:read", "documents:write", "documents:delete", "search:execute", "answers:generate", "collections:manage"],
            ))
        with os.fdopen(fd, "w") as credential_file:
            fd = None
            credential_file.write(f"SEARCH_SPHERE_API_KEY={key}\nSEARCH_SPHERE_CLIENT_ID=quick_clinic\nSEARCH_SPHERE_TENANT_ID=quick_clinic_default\n")
    finally:
        if fd is not None:
            os.close(fd)
    print("Client registered. Copy the private credential file into backend environment configuration.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Private untracked credential file path")
    asyncio.run(run(parser.parse_args().output))
