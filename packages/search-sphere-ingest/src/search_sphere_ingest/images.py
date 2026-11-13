"""Bounded raster preparation; preserve original pixel coordinates and EXIF evidence."""

import hashlib
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .errors import IngestError, ResourceLimit
from .models import Issue, NormalizedDocument, Page, Status, stable_id
from .native import source_ref


def extract_image(pipeline: Any, source: str | Path | bytes) -> NormalizedDocument:
    if isinstance(source, bytes):
        data = source
    else:
        with Path(source).open("rb") as handle:
            data = handle.read(pipeline.config.max_file_bytes + 1)
    if not data or len(data) > pipeline.config.max_file_bytes:
        raise ResourceLimit("image_file_budget")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise IngestError("unsupported_image_type")
            if width * height > pipeline.config.max_image_pixels:
                raise ResourceLimit("image_pixel_budget")
            orientation = image.getexif().get(274, 1)
            buffer = BytesIO()
            image.convert("RGB").save(buffer, format="PNG")
            crop = buffer.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise IngestError("invalid_image") from None
    if len(crop) > pipeline.config.max_crop_bytes:
        raise ResourceLimit("image_crop_budget")
    checksum = hashlib.sha256(data).hexdigest()
    ref = source_ref(checksum, 1, (0, 0, width, height), 0).model_copy(
        update={"coordinate_system": "image_pixels"}
    )
    objects, issues = pipeline.router.route(crop, ref)
    if orientation != 1:
        issues.append(Issue(code="exif_orientation_preserved", stage="preparation", page_number=1))
    page = Page(
        page_number=1,
        width=width,
        height=height,
        rotation={3: 180, 6: 90, 8: 270}.get(orientation, 0),
        selectable_characters=0,
        cropbox=(0, 0, width, height),
        pdf_to_page=None,
        status=Status.PARTIAL
        if issues or any(item.status != Status.COMPLETED for item in objects)
        else Status.COMPLETED,
    )
    document = NormalizedDocument(
        document_id=checksum,
        source_sha256=checksum,
        config_fingerprint=stable_id(
            pipeline.config.model_dump(),
            "image",
            getattr(pipeline.router.vision, "name", None),
            getattr(pipeline.router.ocr, "name", None),
        ),
        pages=[page],
        objects=objects,
        issues=issues,
        status=page.status,
        trace_id=uuid.uuid4().hex,
    )
    if pipeline.config.require_complete and document.status != Status.COMPLETED:
        raise IngestError("partial_extraction_requires_review")
    return document
