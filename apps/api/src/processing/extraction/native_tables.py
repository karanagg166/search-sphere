"""Reliable native tables, with conservative cross-page continuation checks."""

import hashlib
import json
import re

import pymupdf


def native_tables(page, page_number, source_hash):
    candidates = []
    for index, table in enumerate(page.find_tables(strategy="lines_strict", use_layout=False).tables):
        rows = table.extract()
        source_rows = rows
        if not 2 <= len(rows) <= 10000 or not 2 <= table.col_count <= 64:
            continue
        # Merged/missing cells are complex layouts, handled by hosted recognition.
        if any(len(row) != table.col_count or any(cell is None for cell in row) for row in rows):
            continue
        rows = [[str(cell).strip() for cell in row] for row in rows]
        if any(len(cell) > 8000 for row in rows for cell in row):
            raise ValueError("Table cell exceeds the bounded extraction limit")
        header = table.header
        headers = [str(value or "").strip() for value in header.names]
        if len(headers) != table.col_count or not all(headers) or len(set(headers)) != len(headers):
            continue
        if not header.external and rows[0] == headers:
            rows = rows[1:]
        if not rows:
            continue
        bbox = tuple(float(value) for value in table.bbox)
        caption = ""
        caption_box = pymupdf.Rect(bbox[0], max(0, bbox[1] - 36), bbox[2], bbox[1])
        for line in page.get_textbox(caption_box).splitlines():
            if re.match(r"^\s*Table\s+[\w.-]+\s*[:. -]", line, re.I):
                caption = line.strip()
        identity = json.dumps([source_hash, page_number, bbox, index], separators=(",", ":"))
        data = {
            "evidence_id": "table_" + hashlib.sha256(identity.encode()).hexdigest()[:32], "kind": "table",
            "headers": headers, "rows": rows, "row_pages": [page_number] * len(rows),
            "caption": caption, "units": [left or right for name in headers for left, right in re.findall(r"\(([^)]+)\)|\[([^]]+)\]", name)],
            "pages": [page_number], "regions": [{"page_number": page_number, "bbox": list(bbox)}],
            "column_edges": [float(cell[0]) for cell in table.rows[0].cells if cell] + [bbox[2]],
            "page_width": float(page.cropbox.width), "page_height": float(page.cropbox.height),
            "extraction_method": "native_pdf_table", "review_required": False, "row_offset": 0,
            "cells": [{"row": r, "column": c, "row_span": 1, "column_span": 1,
                       "header": not header.external and r == 0, "text": str(source_rows[r][c] or ""),
                       "page_number": page_number,
                       "bbox": list(bounds)} for r, row in enumerate(table.rows) for c, bounds in enumerate(row.cells) if bounds],
        }
        masks = [bbox]
        if header.external:
            masks.append(tuple(header.bbox))
        if caption:
            masks.extend(tuple(rect) for rect in page.search_for(caption) if rect.intersects(caption_box))
        if len(json.dumps(data).encode()) > 2_000_000 or len(candidates) >= 32:
            raise ValueError("Table exceeds the bounded extraction limit")
        candidates.append({"type": "table", "bbox": bbox, "structured": data, "original_order": index, "masks": masks})
    return candidates


def continue_tables(pages):
    """Merge only adjacent-page matching headers, columns, margins, and captions."""
    from dataclasses import replace

    previous = None
    for page in pages:
        for index, block in enumerate(page.blocks):
            if block.block_type != "table":
                continue
            data = dict(block.structured)
            if len(data.get("column_edges", [])) != len(data["headers"]) + 1:
                previous = None
                continue  # No merge without explicit, validated column geometry.
            if previous:
                prior = previous
                before = prior["regions"][-1]
                after = data["regions"][0]
                same_columns = len(prior["column_edges"]) == len(data["column_edges"]) and all(
                    abs(left / prior["page_width"] - right / data["page_width"]) <= 0.015
                    for left, right in zip(prior["column_edges"], data["column_edges"], strict=True)
                )
                if (
                    before["page_number"] + 1 == after["page_number"] and prior["headers"] == data["headers"] and same_columns
                    and before["bbox"][3] / prior["page_height"] >= 0.8 and after["bbox"][1] / data["page_height"] <= 0.25
                    and (not data["caption"] or data["caption"] == prior["caption"] or data["caption"].lower() == prior["caption"].lower() + " (continued)")
                ):
                    data["evidence_id"] = prior["evidence_id"]
                    data["row_offset"] = prior["row_offset"] + len(prior["rows"])
            page.blocks[index] = replace(block, content=table_text(data), structured=data)
            previous = data


def table_text(data, rows=None):
    rows = data["rows"] if rows is None else rows
    header = " | ".join(data["headers"])
    body = "\n".join(" | ".join(row) for row in rows)
    return f"Table {data['evidence_id']} {data.get('caption', '')}\n{header}\n{body}".strip()
