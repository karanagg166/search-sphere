"""Inspect originals without rasterizing or rewriting clean native PDFs."""

import hashlib
import math
import zlib
from pathlib import Path
from typing import Any

import pymupdf

from .config import PipelineConfig
from .errors import IngestError, ResourceLimit
from .models import Page

fitz: Any = pymupdf


def load_pdf(source: str | Path | bytes, config: PipelineConfig) -> bytes:
    if isinstance(source, bytes):
        data = source
    else:
        # No URL fetch or shell execution; enforce the limit during the read, too.
        with Path(source).open("rb") as handle:
            data = handle.read(config.max_file_bytes + 1)
    if len(data) > config.max_file_bytes:
        raise ResourceLimit("file_too_large")
    if not data.startswith(b"%PDF-"):
        raise IngestError("invalid_pdf")
    return data


def _check_streams(pdf: Any, config: PipelineConfig) -> None:
    """Bound Flate expansion before text/layout processing; reject nested filters."""
    if pdf.xref_length() > config.max_objects:
        raise ResourceLimit("too_many_pdf_objects")
    total = 0
    for xref in range(1, pdf.xref_length()):
        if not pdf.xref_is_stream(xref):
            continue
        raw = pdf.xref_stream_raw(xref)
        if raw is None:
            continue
        total += len(raw)
        if total > config.max_content_bytes:
            raise ResourceLimit("pdf_stream_budget")
        filter_value = pdf.xref_get_key(xref, "Filter")[1]
        if filter_value in ("/FlateDecode", "[/FlateDecode]"):
            try:
                decoded = zlib.decompressobj().decompress(raw, config.max_content_bytes + 1)
            except zlib.error:
                raise IngestError("malformed_pdf_stream") from None
            total += len(decoded)
            if total > config.max_content_bytes:
                raise ResourceLimit("pdf_decompression_budget")
        elif filter_value not in ("null", "/DCTDecode", "/JPXDecode", "/CCITTFaxDecode"):
            raise IngestError("unsupported_pdf_filter")
        if pdf.xref_get_key(xref, "Subtype")[1] == "/Image":
            try:
                width = int(pdf.xref_get_key(xref, "Width")[1])
                height = int(pdf.xref_get_key(xref, "Height")[1])
            except ValueError:
                raise IngestError("invalid_image_dimensions") from None
            if width <= 0 or height <= 0 or width * height > config.max_image_pixels:
                raise ResourceLimit("image_pixel_budget")


def inspect_pdf(data: bytes, config: PipelineConfig) -> tuple[Any, list[Page], str]:
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise IngestError("invalid_pdf") from None
    try:
        if pdf.needs_pass:
            raise IngestError("encrypted_pdf")
        if pdf.is_repaired:
            raise IngestError("repaired_pdf_requires_review")
        if not 0 < pdf.page_count <= config.max_pages:
            raise ResourceLimit("page_budget")
        _check_streams(pdf, config)
        pages = []
        characters = 0
        for number, page in enumerate(pdf, 1):
            width, height = float(page.cropbox.width), float(page.cropbox.height)
            if not all(
                math.isfinite(value) and 0 < value <= config.max_page_points
                for value in (width, height)
            ):
                raise ResourceLimit("page_dimensions")
            text = page.get_text("text")
            characters += len(text.encode())
            if characters > config.max_content_bytes:
                raise ResourceLimit("text_budget")
            pages.append(
                Page(
                    page_number=number,
                    width=width,
                    height=height,
                    rotation=page.rotation,
                    cropbox=tuple(page.cropbox),
                    pdf_to_page=tuple(page.transformation_matrix),
                    selectable_characters=len(text.strip()),
                )
            )
        return pdf, pages, hashlib.sha256(data).hexdigest()
    except BaseException:
        pdf.close()
        raise
