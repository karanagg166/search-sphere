"""Write a private dry-run receipt, then explicitly apply its reviewed digest.

Run with PYTHONPATH=apps/api and the existing private operator environment.
Never put a database URL or provider key in command arguments.
"""

import argparse
import asyncio
import json
import os
import stat
from collections import Counter
from pathlib import Path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("standalone", "external"))
    parser.add_argument("--client")
    parser.add_argument("--tenant")
    parser.add_argument("--collection")
    parser.add_argument("--subject")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--after-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", type=Path)
    parser.add_argument("--digest")
    args = parser.parse_args(argv)
    if args.apply:
        if not args.digest or args.output or any((args.kind, args.client, args.tenant, args.collection, args.subject, args.after_id)):
            parser.error("Apply requires only the reviewed receipt path and --digest.")
    elif not args.output or not all((args.kind, args.client, args.tenant)) or args.digest:
        parser.error("Dry run requires --kind, --client, --tenant and --output.")
    return args


def read_private_plan(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_size > 65536:
            raise ValueError("Receipt must be a private regular file of at most 64 KiB.")
        return json.load(handle)


def write_private_plan(path, plan):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


async def run(args):
    from src.db import AsyncSessionLocal
    from src.services.index_recovery import (
        RecoveryScope,
        apply_recovery_plan,
        build_recovery_plan,
        digest,
    )

    if args.apply:
        results = await apply_recovery_plan(AsyncSessionLocal, read_private_plan(args.apply), args.digest)
        return {"mode": "apply", "results": dict(Counter(row["result"] for row in results))}
    scope = RecoveryScope(args.kind, args.client, args.tenant, args.collection,
        args.subject if args.subject is not None else (args.tenant if args.kind == "standalone" else None))
    plan = await build_recovery_plan(AsyncSessionLocal, scope, limit=args.limit, after_id=args.after_id)
    write_private_plan(args.output, plan)
    return {"mode": "dry_run", "digest": digest(plan), "counts": dict(Counter(row["action"] for row in plan["entries"])),
            "has_more": plan["next_after_id"] is not None, "expires_in_seconds": 900}


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(run(arguments()))))
    except Exception as error:  # noqa: BLE001 -- redact all driver/configuration details at the operator boundary
        raise SystemExit("Index recovery failed (" + type(error).__name__ + "); review private configuration and receipt.") from None
