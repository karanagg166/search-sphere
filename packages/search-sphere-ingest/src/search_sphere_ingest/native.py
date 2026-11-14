"""Native PDF adapter, reusing Search Sphere's table and reading-order strategy."""

import math
import re
from statistics import median
from typing import Any

import pymupdf

from .models import Box, Cell, Page, SourceRef, Status, Table, stable_id

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


def _extract_table_object(
    page: Any, table: Any, info: Page, checksum: str, index: int, *, strategy: str
) -> Table | None:
    rows = table.extract()
    if not 2 <= len(rows) <= 10_000 or not 2 <= table.col_count <= 64:
        return None
    if any(len(row) != table.col_count for row in rows):
        return None
    has_merged = any(cell is None for row in rows for cell in row)
    if has_merged and len(table.rows) != len(rows):
        return None
    full: list[list[str]] = []
    for r_idx, row in enumerate(rows):
        clean_row: list[str] = []
        for c_idx, val in enumerate(row):
            if val is not None:
                clean_row.append(str(val).strip())
            elif r_idx == 0 and c_idx > 0:
                clean_row.append(clean_row[c_idx - 1])
            else:
                clean_row.append("")
        full.append(clean_row)
    headers = [str(name or "").strip() for name in table.header.names]
    internal_header = not table.header.external and full[0] == headers
    if not all(headers) or len(headers) != table.col_count:
        if not any(any(char.isalpha() for char in value) for value in full[0]):
            return None
        headers = [val if val else f"col_{idx + 1}" for idx, val in enumerate(full[0])]
        internal_header = True
    body = full[1:] if internal_header else full
    if not body:
        return None
    # Reject likely prose or attention-heatmap false-positives from non-strict line detection.
    if strategy != "lines_strict":
        all_cells = [c for r in body for c in r]
        avg_cell_len = sum(len(c) for c in all_cells) / max(1, len(all_cells))
        if avg_cell_len > 160:
            return None
        if table.col_count >= 10 and avg_cell_len < 3.5:
            return None
        short_headers = sum(1 for h in headers if len(h.strip()) <= 2)
        if short_headers > len(headers) * 0.6 and table.col_count >= 6:
            return None
    ref = source_ref(checksum, info.page_number, tuple(table.bbox), index)
    if has_merged:
        ref.evidence.append("merged_cells_require_review")
    if strategy != "lines_strict":
        ref.evidence.append(f"table_strategy_{strategy}")
    caption = ""
    for line in page.get_textbox(
        fitz.Rect(table.bbox[0], max(0, table.bbox[1] - 36), table.bbox[2], table.bbox[1])
    ).splitlines():
        if re.match(r"^\s*Table\s+[\w.-]+\s*[:. -]", line, re.I):
            caption = line.strip()
    col_x = [float(cell[0]) if cell else None for cell in table.rows[0].cells]
    cells = []
    for row_idx, row in enumerate(table.rows):
        for col_idx, bounds in enumerate(row.cells):
            if bounds is None:
                continue
            row_number = row_idx if internal_header else row_idx + 1
            col_span = 1
            for next_c in range(col_idx + 1, table.col_count):
                next_x = col_x[next_c]
                if row.cells[next_c] is None and (next_x is None or bounds[2] > next_x + 1.0):
                    col_span += 1
                else:
                    break
            row_span = 1
            for next_r in range(row_idx + 1, len(table.rows)):
                if (
                    table.rows[next_r].cells[col_idx] is None
                    and bounds[3] > table.rows[next_r].bbox[1] + 1.0
                ):
                    row_span += 1
                else:
                    break
            row_span = min(row_span, len(body) + 1 - row_number)
            col_span = min(col_span, table.col_count - col_idx)
            cell_ref = source_ref(checksum, info.page_number, tuple(bounds), index)
            cells.append(
                Cell(
                    row=row_number,
                    column=col_idx,
                    text=full[row_idx][col_idx],
                    header=internal_header and row_idx == 0,
                    row_span=max(1, row_span),
                    column_span=max(1, col_span),
                    source=cell_ref,
                )
            )
    col_edges: list[float] = []
    for c_idx in range(table.col_count):
        edge = col_x[c_idx]
        if edge is None:
            for r_obj in table.rows:
                if r_obj.cells[c_idx] is not None:
                    edge = float(r_obj.cells[c_idx][0])
                    break
        if edge is not None:
            col_edges.append(edge)
    if len(col_edges) == table.col_count:
        col_edges.append(float(table.bbox[2]))
    else:
        col_edges = []
    return Table(
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
        column_edges=col_edges,
        status=Status.PARTIAL if has_merged else Status.COMPLETED,
    )


def native_tables(page: Any, info: Page, checksum: str) -> list[Table]:
    output: list[Table] = []
    seen_rects: list[Any] = []
    for strategy in ("lines_strict", "lines"):
        for table in page.find_tables(strategy=strategy, use_layout=False).tables:
            rect = fitz.Rect(table.bbox)
            if any(
                (rect & existing).get_area() >= min(rect.get_area(), existing.get_area()) * 0.5
                for existing in seen_rects
            ):
                continue
            parsed = _extract_table_object(
                page, table, info, checksum, len(output), strategy=strategy
            )
            if parsed is not None:
                output.append(parsed)
                seen_rects.append(rect)
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
