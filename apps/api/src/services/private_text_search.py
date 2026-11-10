"""Owner-authorized text search with bounded native parsing and no model provider."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from weakref import WeakKeyDictionary

from fastapi import HTTPException
from sqlalchemy import select

from src.models.document import Document
from src.models.user import User

_slots = WeakKeyDictionary()
SAFE_ERRORS = frozenset({"specific_search_terms_required", "invalid_pdf", "encrypted_pdf", "pdf_page_limit", "pdf_text_limit"})


async def parse_native_text(content, query, limit):
    process = None
    try:
        async with asyncio.timeout(12):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "src.processing.private_text",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin"),
                     "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                     "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"},
            )
            header = json.dumps({"query": query, "limit": limit}).encode() + b"\n"
            output, _ = await process.communicate(header + content)
            if process.returncode != 0 or len(output) > 131072:
                raise HTTPException(422, detail={"error_code": "pdf_processing_failed"})
            result = json.loads(output)
            if result.get("error_code"):
                code = result["error_code"] if result["error_code"] in SAFE_ERRORS else "pdf_processing_failed"
                raise HTTPException(422, detail={"error_code": code})
            return result
    except TimeoutError:
        raise HTTPException(503, detail={"error_code": "private_search_timeout"}) from None
    except (ValueError, OSError):
        raise HTTPException(503, detail={"error_code": "private_search_unavailable"}) from None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def authorized_snapshot(db, document_id, user_id):
    row = (await db.execute(select(
        Document.id, Document.filename, Document.storage_key, Document.file_size,
        Document.current_version_id, User.is_active,
    ).join(User, User.id == Document.user_id).where(
        Document.id == document_id, Document.user_id == user_id,
    ))).one_or_none()
    if row is None:
        raise HTTPException(404, "Document not found.")
    if not row.is_active:
        raise HTTPException(403, "Account is inactive.")
    if not 0 < row.file_size <= 20 * 1024 * 1024:
        raise HTTPException(422, detail={"error_code": "pdf_size_limit"})
    return row


async def private_text_search(db, storage, document_id, user_id, query, limit):
    slots = _slots.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(1))
    try:
        async with asyncio.timeout(30), slots:
            before = await authorized_snapshot(db, document_id, user_id)
            try:
                async with asyncio.timeout(15):
                    content = await storage.download(before.storage_key)
            except Exception:
                raise HTTPException(503, detail={"error_code": "private_storage_unavailable"}) from None
            if len(content) != before.file_size:
                raise HTTPException(409, "Document changed; refresh and retry.")
            result = await parse_native_text(content, query, limit)
            source_hash = hashlib.sha256(content).hexdigest()
            del content
            # Fresh SELECTs bypass the session's ORM identity cache. PostgreSQL is
            # READ COMMITTED, so deletion/disable/version changes remain visible.
            after = await authorized_snapshot(db, document_id, user_id)
            if before != after:
                raise HTTPException(409, "Document changed; refresh and retry.")
            for number, row in enumerate(result["results"], 1):
                row.update({"citation_number": number, "document_id": document_id,
                            "filename": after.filename, "source_sha256": source_hash,
                            "source_kind": "native_pdf_text"})
            return {"mode": "private_text_search", "generated_answer": False,
                    "external_provider_used": False, **result}
    except TimeoutError:
        raise HTTPException(503, detail={"error_code": "private_search_busy"}) from None
