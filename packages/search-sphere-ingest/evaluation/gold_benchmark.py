"""Reproducible gold-standard extraction and chunking benchmark.

Uses synthetic public fixtures with known ground truth to measure accuracy,
reading order, table continuation/separation, panel proposals, graph/chart
preservation, source-coordinate validity, chunk link completeness, and latency.
No sensitive data, network calls, or fabricated scores.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from dataclasses import asdict, dataclass
from io import BytesIO

import pymupdf as fitz
from PIL import Image, ImageDraw

from search_sphere_ingest import DocumentPipeline, PipelineConfig, Status
from search_sphere_ingest.models import (
    Axis,
    Box,
    ChartValue,
    Edge,
    Node,
    NormalizedDocument,
    Page,
    Paragraph,
    SourceRef,
    Table,
    Visual,
)
from search_sphere_ingest.panels import propose_panels


@dataclass
class GoldMetrics:
    native_cer: float
    native_wer: float
    reading_order_accuracy: float
    table_cell_accuracy: float
    table_merge_precision: float
    table_merge_recall: float
    panel_detection_accuracy: float
    diagram_node_recall: float
    diagram_edge_recall: float
    chart_value_recall: float
    source_coordinate_validity: float
    chunk_source_link_completeness: float
    missing_content_rate: float
    extraction_latency_seconds: float
    peak_rss_bytes: int
    hosted_provider_calls: int
    unsupported_or_partial_documented: list[str]


def _levenshtein(a: Sequence[str] | str, b: Sequence[str] | str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


from collections.abc import Sequence  # noqa: E402


def cer(reference: str, hypothesis: str) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _levenshtein(reference, hypothesis) / len(reference)


def wer(reference: str, hypothesis: str) -> float:
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _levenshtein(ref_words, hyp_words) / len(ref_words)


def _multicolumn_pdf() -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 45), "Wide Title Banner", fontsize=18)
        page.insert_text((40, 100), "Left column first sentence.", fontsize=11)
        page.insert_text((40, 130), "Left column second sentence.", fontsize=11)
        page.insert_text((330, 100), "Right column first sentence.", fontsize=11)
        page.insert_text((330, 130), "Right column second sentence.", fontsize=11)
        page.insert_text((40, 740), "Wide Footer Note", fontsize=10)
        return doc.tobytes()


def _rotated_pdf() -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 120), "Rotated page exact sentence.", fontsize=14)
        page.set_rotation(90)
        return doc.tobytes()


def _cross_page_and_unrelated_tables_pdf() -> bytes:
    with fitz.open() as doc:
        # Page 1: Table A at bottom (should merge with Page 2 top)
        p1 = doc.new_page(width=600, height=800)
        top1 = 680
        rows1 = [("Metric", "Value (ms)"), ("Latency P50", "18"), ("Latency P95", "42")]
        for y in range(top1, top1 + 100, 25):
            p1.draw_line((40, y), (400, y))
        for x in (40, 220, 400):
            p1.draw_line((x, top1), (x, top1 + 75))
        for r, row in enumerate(rows1):
            for c, val in enumerate(row):
                p1.insert_text((45 + c * 180, top1 + r * 25 + 16), val, fontsize=10)

        # Page 2: Table A continuation at top + unrelated Table B in middle
        p2 = doc.new_page(width=600, height=800)
        top2 = 40
        rows2 = [("Metric", "Value (ms)"), ("Latency P99", "88")]
        for y in range(top2, top2 + 75, 25):
            p2.draw_line((40, y), (400, y))
        for x in (40, 220, 400):
            p2.draw_line((x, top2), (x, top2 + 50))
        for r, row in enumerate(rows2):
            for c, val in enumerate(row):
                p2.insert_text((45 + c * 180, top2 + r * 25 + 16), val, fontsize=10)

        top3 = 350
        rows3 = [("Region", "Status", "Budget ($)"), ("Singapore", "Free", "0")]
        for y in range(top3, top3 + 75, 25):
            p2.draw_line((40, y), (520, y))
        for x in (40, 200, 360, 520):
            p2.draw_line((x, top3), (x, top3 + 50))
        for r, row in enumerate(rows3):
            for c, val in enumerate(row):
                p2.insert_text((45 + c * 160, top3 + r * 25 + 16), val, fontsize=10)

        return doc.tobytes()


def _panel_fixture(layout: str) -> bytes:
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    if layout == "vertical":
        draw.rectangle((60, 100, 420, 880), outline="black", width=12)
        draw.rectangle((580, 100, 940, 880), outline="black", width=12)
    elif layout == "horizontal":
        draw.rectangle((100, 60, 900, 420), outline="black", width=12)
        draw.rectangle((100, 580, 900, 940), outline="black", width=12)
    elif layout == "grid":
        draw.rectangle((60, 60, 420, 420), outline="black", width=12)
        draw.rectangle((580, 60, 940, 420), outline="black", width=12)
        draw.rectangle((60, 580, 420, 940), outline="black", width=12)
        draw.rectangle((580, 580, 940, 940), outline="black", width=12)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def run_benchmark() -> GoldMetrics:
    started = time.monotonic()
    pipeline = DocumentPipeline(PipelineConfig(target_tokens=380, max_tokens=480))

    # 1. Multi-column reading order + CER/WER
    mc = pipeline.ingest(_multicolumn_pdf())
    mc_texts = [obj.text for obj in mc.document.objects if isinstance(obj, Paragraph)]
    expected_order = [
        "Wide Title Banner",
        "Left column first sentence.",
        "Left column second sentence.",
        "Right column first sentence.",
        "Right column second sentence.",
        "Wide Footer Note",
    ]
    joined_mc = " ".join(mc_texts)
    expected_joined = " ".join(expected_order)
    order_hits = sum(
        joined_mc.find(expected_order[i]) < joined_mc.find(expected_order[i + 1])
        for i in range(len(expected_order) - 1)
        if expected_order[i] in joined_mc and expected_order[i + 1] in joined_mc
    )
    reading_order_acc = order_hits / (len(expected_order) - 1)

    # 2. Rotated PDF
    rot = pipeline.ingest(_rotated_pdf())
    rot_text = " ".join(obj.text for obj in rot.document.objects if isinstance(obj, Paragraph))
    rot_cer = cer("Rotated page exact sentence.", rot_text)

    # 3. Tables: cross-page merge + false-merge rejection
    tbl_res = pipeline.ingest(_cross_page_and_unrelated_tables_pdf())
    tables = [obj for obj in tbl_res.document.objects if isinstance(obj, Table)]
    expected_t1 = [["Latency P50", "18"], ["Latency P95", "42"], ["Latency P99", "88"]]
    expected_t2 = [["Singapore", "Free", "0"]]
    cell_matches = 0
    total_cells = 9
    if len(tables) == 2:
        for r, gold in zip(tables[0].rows, expected_t1, strict=False):
            for a, b in zip(r, gold, strict=False):
                cell_matches += int(a == b)
        for r, gold in zip(tables[1].rows, expected_t2, strict=False):
            for a, b in zip(r, gold, strict=False):
                cell_matches += int(a == b)
    table_cell_acc = cell_matches / total_cells
    merge_precision = (
        1.0
        if len(tables) == 2 and len(tables[0].sources) == 2 and len(tables[1].sources) == 1
        else 0.0
    )
    merge_recall = 1.0 if len(tables) >= 1 and len(tables[0].rows) == 3 else 0.0

    # 4. Panel separation (vertical, horizontal, 2x2 grid)
    v_panels = propose_panels(_panel_fixture("vertical"))
    h_panels = propose_panels(_panel_fixture("horizontal"))
    g_panels = propose_panels(_panel_fixture("grid"))
    panel_acc = (int(len(v_panels) == 2) + int(len(h_panels) == 2) + int(len(g_panels) == 4)) / 3.0

    # 5. All 5 chunking strategies: chart, diagram, image + text/table
    ref = SourceRef(
        page_number=1,
        bbox=Box(x0=10, y0=10, x1=400, y1=300),
        region_id="gold-visual-1",
        reading_order=0,
        provider="synthetic-gold",
    )
    chart_obj = Visual(
        object_id="gold-chart-1",
        kind="chart",
        title="Monthly Latency",
        chart_type="bar",
        visual_description="Bar chart comparing P50 and P95 latency.",
        axes=[Axis(label="Percentile", unit="ms", scale="linear")],
        legend=["API"],
        values=[
            ChartValue(
                series="API",
                label="P50",
                raw_value="18",
                value=18.0,
                unit="ms",
                evidence="visible_label",
                verified=False,
            ),
            ChartValue(
                series="API",
                label="P95",
                raw_value="42",
                value=42.0,
                unit="ms",
                evidence="visible_label",
                verified=False,
            ),
        ],
        sources=[ref],
        status=Status.PARTIAL,
    )
    diagram_obj = Visual(
        object_id="gold-diagram-1",
        kind="diagram",
        title="Ingestion Flow",
        visual_description="Upload flows to Worker; Queue is isolated.",
        nodes=[
            Node(node_id="n1", label="Upload", kind="process"),
            Node(node_id="n2", label="Worker", kind="process"),
            Node(node_id="n3", label="Isolated", kind="decision"),
        ],
        edges=[
            Edge(source="n1", target="n2", label="enqueue", direction="directed", visible=True),
        ],
        sources=[ref],
        status=Status.PARTIAL,
    )
    image_obj = Visual(
        object_id="gold-image-1",
        kind="image",
        title="Receipt Scan",
        visual_description="Printed receipt header.",
        ocr_text="TOTAL 0.00 USD",
        sources=[ref],
        status=Status.PARTIAL,
    )
    visual_doc = NormalizedDocument(
        document_id="gold-visuals",
        source_sha256="0" * 64,
        config_fingerprint="gold",
        trace_id="gold-trace",
        title="Visuals",
        pages=[
            Page(
                page_number=1,
                width=600,
                height=800,
                rotation=0,
                selectable_characters=0,
                cropbox=(0.0, 0.0, 600.0, 800.0),
                pdf_to_page=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            )
        ],
        objects=[chart_obj, diagram_obj, image_obj],
        status=Status.PARTIAL,
    )
    visual_chunks = pipeline.chunk(visual_doc)
    all_docs = [mc.document, rot.document, tbl_res.document, visual_doc]
    all_chunks = mc.chunks + rot.chunks + tbl_res.chunks + visual_chunks
    all_obj_ids = {obj.object_id for doc in all_docs for obj in doc.objects}
    linked = sum(
        1
        for chunk in all_chunks
        if chunk.source_object_ids and all(oid in all_obj_ids for oid in chunk.source_object_ids)
    )
    valid_coords = sum(
        1
        for chunk in all_chunks
        if chunk.sources
        and all(src.bbox.x1 > src.bbox.x0 and src.bbox.y1 > src.bbox.y0 for src in chunk.sources)
    )
    chart_text = "\n".join(c.text for c in visual_chunks if c.kind == "chart")
    diagram_text = "\n".join(c.text for c in visual_chunks if c.kind == "diagram")
    chart_val_recall = sum(val in chart_text for val in ("18", "42")) / 2.0
    node_recall = sum(lbl in diagram_text for lbl in ("Upload", "Worker", "Isolated")) / 3.0
    edge_recall = 1.0 if "Upload -> Worker: enqueue" in diagram_text else 0.0

    elapsed = round(time.monotonic() - started, 4)
    raw_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = raw_rss if sys.platform == "darwin" else raw_rss * 1024

    return GoldMetrics(
        native_cer=round((cer(expected_joined, joined_mc) + rot_cer) / 2.0, 4),
        native_wer=round(wer(expected_joined, joined_mc), 4),
        reading_order_accuracy=round(reading_order_acc, 4),
        table_cell_accuracy=round(table_cell_acc, 4),
        table_merge_precision=merge_precision,
        table_merge_recall=merge_recall,
        panel_detection_accuracy=round(panel_acc, 4),
        diagram_node_recall=round(node_recall, 4),
        diagram_edge_recall=edge_recall,
        chart_value_recall=chart_val_recall,
        source_coordinate_validity=round(valid_coords / len(all_chunks), 4),
        chunk_source_link_completeness=round(linked / len(all_chunks), 4),
        missing_content_rate=0.0
        if cell_matches == total_cells and reading_order_acc == 1.0
        else 1.0,
        extraction_latency_seconds=elapsed,
        peak_rss_bytes=peak,
        hosted_provider_calls=0,
        unsupported_or_partial_documented=[
            "scanned_pdf_without_ocr_returns_PARTIAL",
            "local_docling_and_grounding_dino_disabled_on_512mb_free_tier",
            "model_generated_chart_numbers_and_arrows_marked_unverified",
        ],
    )


def main() -> None:
    metrics = run_benchmark()
    print(json.dumps(asdict(metrics), indent=2))


if __name__ == "__main__":
    main()
