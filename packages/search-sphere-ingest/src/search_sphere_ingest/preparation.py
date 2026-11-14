"""Inspect originals without rasterizing or rewriting clean native PDFs."""

import hashlib
import math
import re
import zlib
from pathlib import Path
from typing import Any

import pymupdf

from .config import PipelineConfig
from .errors import IngestError, ResourceLimit
from .models import Issue, Page

fitz: Any = pymupdf

_ALLOWED_PDF_FILTERS = frozenset(
    {
        "/FlateDecode",
        "/ASCII85Decode",
        "/ASCIIHexDecode",
        "/LZWDecode",
        "/RunLengthDecode",
        "/DCTDecode",
        "/JPXDecode",
        "/CCITTFaxDecode",
        "/JBIG2Decode",
    }
)


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


def _parse_filters(filter_value: str) -> list[str]:
    if not filter_value or filter_value == "null":
        return []
    return re.findall(r"/[A-Za-z0-9]+", filter_value)


def _check_streams(pdf: Any, config: PipelineConfig) -> None:
    """Bound Flate expansion before text/layout processing; reject nested/unknown filters."""
    if pdf.xref_length() > config.max_objects:
        raise ResourceLimit("too_many_pdf_objects")
    raw_total = 0
    decoded_total = 0
    max_stream_total = config.max_content_bytes * 4
    for xref in range(1, pdf.xref_length()):
        if not pdf.xref_is_stream(xref):
            continue
        raw = pdf.xref_stream_raw(xref)
        if raw is None:
            continue
        raw_total += len(raw)
        if raw_total > max_stream_total:
            raise ResourceLimit("pdf_stream_budget")
        filters = _parse_filters(pdf.xref_get_key(xref, "Filter")[1])
        if len(filters) > 3 or any(name not in _ALLOWED_PDF_FILTERS for name in filters):
            raise IngestError("unsupported_pdf_filter")
        is_image = pdf.xref_get_key(xref, "Subtype")[1] == "/Image"
        if is_image:
            try:
                width = int(pdf.xref_get_key(xref, "Width")[1])
                height = int(pdf.xref_get_key(xref, "Height")[1])
            except ValueError:
                raise IngestError("invalid_image_dimensions") from None
            if width <= 0 or height <= 0 or width * height > config.max_image_pixels:
                raise ResourceLimit("image_pixel_budget")
        if filters == ["/FlateDecode"]:
            stream_limit = config.max_image_pixels * 4 if is_image else config.max_content_bytes
            try:
                decoded = zlib.decompressobj().decompress(raw, stream_limit + 1)
            except zlib.error:
                raise IngestError("malformed_pdf_stream") from None
            if len(decoded) > stream_limit:
                raise ResourceLimit("pdf_decompression_budget")
            if not is_image:
                decoded_total += len(decoded)
                if decoded_total > max_stream_total:
                    raise ResourceLimit("pdf_decompression_budget")


def inspect_pdf(
    data: bytes, config: PipelineConfig, *, issues: list[Issue] | None = None
) -> tuple[Any, list[Page], str]:
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise IngestError("invalid_pdf") from None
    try:
        if pdf.needs_pass:
            raise IngestError("encrypted_pdf")
        if pdf.is_repaired:
            if issues is not None:
                issues.append(Issue(code="repaired_pdf_requires_review", stage="preparation"))
            elif config.require_complete:
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
