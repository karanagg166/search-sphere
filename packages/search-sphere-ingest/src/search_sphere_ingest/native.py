"""Native PDF adapter, reusing Search Sphere's table and reading-order strategy."""

import math
import re
from statistics import median
from typing import Any

import pymupdf

from .models import Box, Cell, Page, SourceRef, Table, stable_id

# PyMuPDF's extension exposes incomplete type stubs. Keep Any at this adapter boundary.
fitz: Any = pymupdf


def vector_data(value: Any) -> Any:
    if isinstance(value, (int, float)) and not math.isfinite(value):
        raise ValueError("Non-finite native geometry")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return [vector_data(part) for part in value]


def source_ref(
    checksum: str, page: int, box: tuple[float, ...], order: int, provider: str = "pymupdf"
) -> SourceRef:
    return SourceRef(
        page_number=page,
        bbox=Box(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
        region_id=stable_id(checksum, page, box, order),
        reading_order=order,
        provider=provider,
    )


def native_tables(page: Any, info: Page, checksum: str) -> list[Table]:
    output = []
    for index, table in enumerate(
        page.find_tables(strategy="lines_strict", use_layout=False).tables
    ):
        rows = table.extract()
        if not 2 <= len(rows) <= 10_000 or not 2 <= table.col_count <= 64:
            continue
        # Complex merged/missing cells require a structural provider; do not guess.
        if any(len(row) != table.col_count or any(cell is None for cell in row) for row in rows):
            continue
        headers = [str(name or "").strip() for name in table.header.names]
        full = [[str(value).strip() for value in row] for row in rows]
        internal_header = not table.header.external and full[0] == headers
        if not all(headers) or len(headers) != table.col_count:
            # PyMuPDF sometimes mistakes a caption for an external header. A full,
            # non-numeric first row is evidence for an internal header instead.
            if not all(value and any(char.isalpha() for char in value) for value in full[0]):
                continue
            headers, internal_header = full[0], True
        body = full[1:] if internal_header else full
        if not body:
            continue
        ref = source_ref(checksum, info.page_number, tuple(table.bbox), index)
        caption = ""
        for line in page.get_textbox(
            fitz.Rect(table.bbox[0], max(0, table.bbox[1] - 36), table.bbox[2], table.bbox[1])
        ).splitlines():
            if re.match(r"^\s*Table\s+[\w.-]+\s*[:. -]", line, re.I):
                caption = line.strip()
        cells = []
        for row_idx, row in enumerate(table.rows):
            for col_idx, bounds in enumerate(row.cells):
                if bounds is None:
                    continue
                row_number = row_idx if internal_header else row_idx + 1
                cell_ref = source_ref(checksum, info.page_number, tuple(bounds), index)
                cells.append(
                    Cell(
                        row=row_number,
                        column=col_idx,
                        text=full[row_idx][col_idx],
                        header=internal_header and row_idx == 0,
                        source=cell_ref,
                    )
                )
        output.append(
            Table(
                object_id=stable_id(ref.region_id, "table"),
                title=caption,
                headers=headers,
                rows=body,
                cells=cells,
                row_pages=[info.page_number] * len(body),
                sources=[ref],
                units=[
                    left or right
                    for name in headers
                    for left, right in re.findall(r"\(([^)]+)\)|\[([^]]+)\]", name)
                ],
                column_edges=[float(cell[0]) for cell in table.rows[0].cells if cell]
                + [float(table.bbox[2])],
            )
        )
    return output


def text_blocks(page: Any, tables: list[Table]) -> tuple[list[dict[str, Any]], float]:
    flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
    raw = page.get_text("dict", flags=flags)["blocks"]
    sizes = [
        span["size"]
        for block in raw
        for line in block.get("lines", [])
        for span in line["spans"]
        if span["text"].strip()
    ]
    baseline = float(median(sizes)) if sizes else 12.0
    masks = [fitz.Rect(table.sources[0].bbox.tuple()) for table in tables]
    output = []
    for block in raw:
        if block.get("type") != 0:
            continue
        lines = [
            line
            for line in block["lines"]
            if not any(
                (fitz.Rect(line["bbox"]) & mask).get_area()
                >= fitz.Rect(line["bbox"]).get_area() * 0.6
                for mask in masks
            )
        ]
        if lines:
            output.append({**block, "lines": lines})
    return output, baseline


def visual_boxes(
    page: Any, tables: list[Table], scanned: bool
) -> list[tuple[float, float, float, float]]:
    full = fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    if scanned:
        return [tuple(full)]
    masks = [fitz.Rect(table.sources[0].bbox.tuple()) for table in tables]
    candidates = [fitz.Rect(item["bbox"]) for item in page.get_image_info()]
    candidates += [fitz.Rect(box) + (-24, -32, 24, 24) for box in page.cluster_drawings()]
    result: list[Any] = []
    for rect in candidates:
        rect &= full
        if (
            rect.width < 20
            or rect.height < 20
            or any(
                (rect & mask).get_area() >= min(rect.get_area(), mask.get_area()) * 0.8
                for mask in masks
            )
        ):
            continue
        result.append(rect)
    changed = True
    while changed:
        changed = False
        for index, rect in enumerate(result):
            for other in result[index + 1 :]:
                if rect.intersects(other):
                    rect |= other
                    result.remove(other)
                    changed = True
                    break
            if changed:
                break
    return [tuple(rect) for rect in sorted(result, key=lambda rect: (rect.y0, rect.x0))]


def render(page: Any, bbox: Box, side: int) -> bytes:
    rotation = page.rotation
    try:
        page.set_rotation(0)
        scale = min(2.0, side / max(bbox.x1 - bbox.x0, bbox.y1 - bbox.y0))
        return bytes(
            page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                clip=fitz.Rect(bbox.tuple()),
                colorspace=fitz.csRGB,
                alpha=False,
            ).tobytes("png")
        )
    finally:
        page.set_rotation(rotation)
