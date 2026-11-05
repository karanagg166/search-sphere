"""Build private download links after the caller has authorized document ownership."""

from fastapi import HTTPException

from src.models.document import Document
from src.schemas.document import DocumentResponse
from src.storage.object_storage import ObjectStorage


async def private_document_response(
    document: Document, storage: ObjectStorage
) -> DocumentResponse:
    """Do not return the historical public URL stored on the document row."""
    try:
        signed_url = await storage.create_signed_url(
            document.storage_key, expires_in=600
        )
    except Exception as exc:
        raise HTTPException(
            503, "Private document access is temporarily unavailable."
        ) from exc
    response = DocumentResponse.model_validate(document)
    return response.model_copy(update={"file_url": signed_url})
