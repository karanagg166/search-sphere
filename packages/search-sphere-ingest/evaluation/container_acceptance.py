"""Real synthetic gold PDFs; no provider mocks, network, database or clinical data.

Run inside the production candidate image with --network none --memory 512m.
This evaluates the native profile only, not Docling or remote vision accuracy.
"""

import json
import resource
import time
from io import BytesIO

import pymupdf as fitz
from PIL import Image, ImageDraw

from search_sphere_ingest import DocumentPipeline, Status
from search_sphere_ingest.isolation import extract_isolated
from search_sphere_ingest.models import Table
from search_sphere_ingest.panels import propose_panels


def native_pdf() -> bytes:
    with fitz.open() as doc:
        for number in range(30):
            page = doc.new_page()
            page.insert_text((40, 50), "1. Public gold evidence", fontsize=18)
            page.insert_text(
                (40, 90),
                f"Native sentence {number} contains exact evidence. No model generated this text.",
            )
        return doc.tobytes()


def table_pdf() -> bytes:
    with fitz.open() as doc:
        for top, values in (
            (690, [("A-101", "120"), ("B-102", "80")]),
            (40, [("C-103", "42"), ("D-104", "61")]),
        ):
            page = doc.new_page(width=600, height=800)
            rows = [("Item", "Value (mg)"), *values]
            for y in range(top, top + 100, 25):
                page.draw_line((40, y), (400, y))
            for x in (40, 220, 400):
                page.draw_line((x, top), (x, top + 75))
            for row_index, row in enumerate(rows):
                for col_index, text in enumerate(row):
                    page.insert_text(
                        (45 + col_index * 180, top + row_index * 25 + 16), text, fontsize=10
                    )
        return doc.tobytes()


def scan_pdf() -> bytes:
    with fitz.open() as source:
        page = source.new_page(width=500, height=200)
        page.insert_text((30, 80), "PUBLIC SCANNED EVIDENCE", fontsize=24)
        pixels = page.get_pixmap().tobytes("png")
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=200)
        page.insert_image(page.rect, stream=pixels)
        return doc.tobytes()


def main() -> None:
    started = time.monotonic()
    pipeline = DocumentPipeline()
    native = pipeline.ingest(native_pdf())
    table_result = pipeline.ingest(table_pdf())
    scan = pipeline.ingest(scan_pdf())
    text = "\n".join(getattr(item, "text", "") for item in native.document.objects)
    sentence_hits = sum(f"Native sentence {i} contains exact evidence." in text for i in range(30))
    tables = [item for item in table_result.document.objects if isinstance(item, Table)]
    expected = [["A-101", "120"], ["B-102", "80"], ["C-103", "42"], ["D-104", "61"]]
    cells_correct = sum(
        a == b
        for row, gold in zip(tables[0].rows, expected, strict=True)
        for a, b in zip(row, gold, strict=True)
    )
    all_chunks = native.chunks + table_result.chunks
    ids = {item.object_id for result in (native, table_result) for item in result.document.objects}
    linked = sum(
        all(identity in ids for identity in chunk.source_object_ids) for chunk in all_chunks
    )
    assert sentence_hits == 30 and cells_correct == 8 and len(tables) == 1
    assert scan.document.status == Status.PARTIAL
    assert native.document.status == table_result.document.status == Status.COMPLETED
    assert all(chunk.token_count <= 480 for chunk in all_chunks)
    assert linked == len(all_chunks)
    isolated = extract_isolated(native_pdf())
    assert len(isolated.pages) == 30 and isolated.status == Status.COMPLETED
    # Exercise the real production CPU panel detector, including a 16 MP input.
    image = Image.new("RGB", (4000, 4000), "white")
    drawing = ImageDraw.Draw(image)
    drawing.rectangle((200, 400, 1600, 3500), outline="black", width=20)
    drawing.rectangle((2400, 400, 3800, 3500), outline="black", width=20)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    panels = propose_panels(buffer.getvalue())
    assert len(panels) == 2 and panels[-1].x1 == 4000
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    assert peak < 384 * 1024 * 1024, "Native workload leaves insufficient memory headroom"
    print(
        json.dumps(
            {
                "profile": "native-only",
                "gold_documents": 3,
                "pages": 33,
                "exact_native_sentences": f"{sentence_hits}/30",
                "exact_table_cells": f"{cells_correct}/8",
                "cross_page_logical_tables": len(tables),
                "source_link_coverage": f"{linked}/{len(all_chunks)}",
                "scan_status": scan.document.status,
                "peak_rss_bytes": peak,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "remote_inference_tested": False,
                "opencv_real_panels": len(panels),
            }
        )
    )


if __name__ == "__main__":
    main()
