"""Validate binary documents using their parsers, independent of filenames."""
from io import BytesIO

import fitz
from fastapi import HTTPException
from PIL import Image


def validate_binary_document(data: bytes, detected_mime: str, reported_mime: str | None) -> None:
    reported = "image/jpeg" if reported_mime == "image/jpg" else reported_mime
    if reported and reported != detected_mime:
        raise HTTPException(status_code=400, detail="Reported MIME type does not match file content.")
    try:
        if detected_mime == "application/pdf":
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if pdf.page_count == 0 or pdf.needs_pass:
                    raise ValueError("Empty or encrypted PDF")
        else:
            with Image.open(BytesIO(data)) as image:
                image.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed or unsupported document content.") from exc
