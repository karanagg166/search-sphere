"""Normalize ordered block dictionaries from native or hosted parsers.

The host supplies dictionaries, never application classes or database objects.
Complete source structures remain authoritative; normalized views are for chunking.
"""

import re
import uuid
from typing import Any, Literal

from .models import (
    Axis,
    Box,
    Cell,
    ChartValue,
    ContentObject,
    Edge,
    Node,
    NormalizedDocument,
    OCRRegion,
    Page,
    SourceRef,
    Status,
    Table,
    Visual,
    stable_id,
)
from .text import Hierarchy


def normalize_blocks(
    pages: list[dict[str, Any]], *, checksum: str, title: str = ""
) -> NormalizedDocument:
    output: list[ContentObject] = []
    page_models = []
    hierarchy = Hierarchy()
    tables: dict[str, Table] = {}
    for page in pages:
        number = int(page["page_number"])
        blocks = page["blocks"]
        width = max(
            [float(block.get("provenance", {}).get("page_size", [612, 792])[0]) for block in blocks]
            + [1.0]
        )
        height = max(
            [float(block.get("provenance", {}).get("page_size", [612, 792])[1]) for block in blocks]
            + [1.0]
        )
        width = max(
            [width]
            + [float(block.get("provenance", {}).get("bbox", [0, 0, 1, 1])[2]) for block in blocks]
        )
        height = max(
            [height]
            + [float(block.get("provenance", {}).get("bbox", [0, 0, 1, 1])[3]) for block in blocks]
        )
        geometry = blocks[0].get("provenance", {}) if blocks else {}
        page_models.append(
            Page(
                page_number=number,
                width=width,
                height=height,
                rotation=geometry.get("page_rotation", 0),
                cropbox=geometry.get("cropbox", (0, 0, width, height)),
                pdf_to_page=geometry.get("pdf_to_page"),
                selectable_characters=sum(
                    len(block["content"]) for block in blocks if block["block_type"] == "text"
                ),
            )
        )
        for order, block in enumerate(blocks):
            provenance = block.get("provenance", {})
            data = block.get("structured", {})
            kind = block["block_type"]
            identity = str(
                data.get("evidence_id")
                or provenance.get("block_id")
                or stable_id(checksum, number, order)
            )
            if kind == "text":
                identity = stable_id("paragraph", identity)
            bounds = provenance.get("bbox", [0, 0, width, height])
            if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                bounds = [0, 0, width, height]
            ref = SourceRef(
                page_number=number,
                bbox=Box(x0=max(0, bounds[0]), y0=max(0, bounds[1]), x1=bounds[2], y1=bounds[3]),
                region_id=str(provenance.get("block_id") or identity),
                reading_order=order,
                parent_region_id=data.get("parent_region_id"),
                provider=provenance.get("source_kind", "native"),
                coordinate_system=provenance.get("coordinate_space", "unrotated_pdf_points"),
                source_image=provenance.get("asset_id"),
            )
            path = [section.title for section in hierarchy.stack]
            section = hierarchy.stack[-1].section_id if hierarchy.stack else None
            if kind == "text":
                text = block["content"]
                numbered = re.match(r"^(\d+(?:\.\d+)*)\.?\s+\S", text)
                if len(text) < 180 and numbered:
                    hierarchy.heading(text, min(6, numbered.group(1).count(".") + 1), ref)
                elif provenance.get("heading_level"):
                    hierarchy.heading(text, int(provenance["heading_level"]), ref)
                paragraph = hierarchy.paragraph(text, ref)
                output.append(paragraph.model_copy(update={"object_id": identity}))
            elif kind == "table":
                cells = [
                    Cell(
                        row=cell["row"],
                        column=cell["column"],
                        text=cell["text"],
                        header=cell.get("header", False),
                        row_span=cell.get("row_span", 1),
                        column_span=cell.get("column_span", 1),
                        source=ref.model_copy(
                            update={
                                "bbox": Box(
                                    x0=cell["bbox"][0],
                                    y0=cell["bbox"][1],
                                    x1=cell["bbox"][2],
                                    y1=cell["bbox"][3],
                                )
                            }
                        )
                        if cell.get("bbox")
                        else ref,
                    )
                    for cell in data.get("cells", [])
                ]
                table = Table(
                    object_id=identity,
                    title=data.get("title") or data.get("caption", ""),
                    headers=data["headers"],
                    rows=data["rows"],
                    row_pages=data.get("row_pages", [number] * len(data["rows"])),
                    cells=cells,
                    units=data.get("units", []),
                    sources=[ref],
                    heading_path=path,
                    section_id=section,
                    status=Status.PARTIAL if data.get("review_required") else Status.COMPLETED,
                )
                if identity in tables:
                    previous = tables[identity]
                    merged = previous.model_copy(
                        update={
                            "rows": previous.rows + table.rows,
                            "row_pages": previous.row_pages + table.row_pages,
                            "sources": previous.sources + table.sources,
                            "cells": previous.cells
                            + [
                                cell.model_copy(update={"row": cell.row + len(previous.rows)})
                                for cell in cells
                                if not cell.header
                            ],
                        }
                    )
                    output[output.index(previous)] = merged
                    tables[identity] = merged
                else:
                    output.append(table)
                    tables[identity] = table
            elif kind in {"image", "chart", "diagram"}:
                values = []
                for series in data.get("series", []):
                    for point in series["points"]:
                        raw = point.get("value")
                        try:
                            value = (
                                float(raw)
                                if raw is not None and point.get("readability") != "unreadable"
                                else None
                            )
                        except (ValueError, TypeError):
                            value = None
                        evidence: Literal["unreadable", "estimated", "visible_label"] = (
                            "unreadable"
                            if value is None
                            else "estimated"
                            if point.get("readability") == "approximate"
                            else "visible_label"
                        )
                        values.append(
                            ChartValue(
                                series=series["name"],
                                label=point["label"],
                                value=value,
                                raw_value=str(raw) if raw is not None else None,
                                evidence=evidence,
                            )
                        )
                # Preserve string-valued labels in OCR/native evidence if parsing a
                # number fails. Never coerce an unreadable value to a guessed number.
                output.append(
                    Visual(
                        object_id=identity,
                        kind=kind,
                        title=data.get("title", ""),
                        caption=data.get("caption", ""),
                        chart_type=data.get("chart_type", "unknown"),
                        visual_description=data.get("visual_description")
                        or data.get("description", ""),
                        ocr_text=data.get("ocr_text") or data.get("visible_text", ""),
                        axes=[Axis.model_validate(axis) for axis in data.get("axes", [])],
                        legend=data.get("legend", []),
                        values=values,
                        nodes=[
                            Node(
                                node_id=node["node_id"],
                                label=node["label"],
                                kind=node.get("kind", "unknown"),
                                bbox=Box(
                                    x0=node["bbox"][0],
                                    y0=node["bbox"][1],
                                    x1=node["bbox"][2],
                                    y1=node["bbox"][3],
                                )
                                if node.get("bbox")
                                else None,
                            )
                            for node in data.get("nodes", [])
                        ],
                        edges=[
                            Edge(
                                source=edge["source"],
                                target=edge["target"],
                                label=edge.get("label", ""),
                                direction=edge.get("direction", "uncertain"),
                            )
                            for edge in data.get("edges", [])
                        ],
                        uncertainties=data.get("uncertainties", []),
                        text_regions=[
                            OCRRegion(
                                text=entry["text"],
                                bbox=Box(
                                    x0=entry["bbox"][0],
                                    y0=entry["bbox"][1],
                                    x1=entry["bbox"][2],
                                    y1=entry["bbox"][3],
                                ),
                                language=entry.get("language", "und"),
                                style=entry.get("style", "unknown"),
                                recognition_model="cohere_hosted_unverified",
                                confidence=entry.get("confidence"),
                            )
                            for entry in data.get("text_regions", [])
                        ],
                        sources=[ref],
                        heading_path=path,
                        section_id=section,
                        panel_status="separated"
                        if data.get("panel_status") == "separated"
                        else "unresolved",
                    )
                )
    return NormalizedDocument(
        document_id=checksum,
        source_sha256=checksum,
        config_fingerprint=stable_id("hosted-blocks-v1"),
        title=title,
        pages=page_models,
        objects=output,
        sections=hierarchy.sections,
        trace_id=uuid.uuid4().hex,
        status=Status.PARTIAL
        if any(getattr(item, "status", Status.COMPLETED) == Status.PARTIAL for item in output)
        else Status.COMPLETED,
    )
