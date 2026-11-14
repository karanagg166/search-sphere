from io import BytesIO

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw

from evaluation.real_world_eval import run_evaluation
from search_sphere_ingest import DocumentPipeline, PipelineConfig, Status
from search_sphere_ingest.errors import CapabilityUnavailable
from search_sphere_ingest.models import Box, ChartValue, SourceRef, Table, Visual
from search_sphere_ingest.tesseract import TesseractOCRProvider


def test_real_world_evaluation_metrics_meet_deterministic_thresholds() -> None:
    metrics = run_evaluation()
    assert metrics.corpus_documents >= 5
    assert metrics.arxiv_pages_extracted == 8
    assert metrics.arxiv_heading_recall >= 0.85
    assert metrics.arxiv_phrase_recall >= 0.75
    assert metrics.multicolumn_reading_order_accuracy == 1.0
    assert metrics.multicolumn_cer == 0.0
    assert metrics.cross_page_merge_accuracy == 1.0
    assert metrics.merged_cell_table_accuracy == 1.0
    assert metrics.multilingual_en_hi_preservation == 1.0
    assert metrics.rotated_page_cer == 0.0
    assert metrics.scanned_pdf_partial_honesty is True
    assert metrics.chunk_token_limit_compliance == 1.0
    assert metrics.chunk_source_link_completeness == 1.0
    assert metrics.chunk_header_preservation == 1.0
    assert metrics.hosted_vlm_evaluated is False
    assert metrics.hosted_provider_calls == 0


def test_chunker_packs_chart_values_and_splits_text_heavy_ocr(text_pdf: bytes) -> None:
    pipeline = DocumentPipeline(PipelineConfig(target_tokens=120, max_tokens=180))
    doc = pipeline.extract(text_pdf)
    ref = doc.objects[0].sources[0]
    chart = Visual(
        object_id="packed-chart",
        kind="chart",
        title="Packed Series",
        sources=[ref],
        values=[
            ChartValue(series="S1", label=f"P{i}", value=float(i), evidence="estimated")
            for i in range(12)
        ],
    )
    long_ocr = " ".join(f"Sentence {i} from scanned receipt." for i in range(40))
    image = Visual(
        object_id="ocr-heavy-img",
        kind="image",
        title="Long OCR",
        visual_description="Scanned page.",
        ocr_text=long_ocr,
        sources=[ref],
    )
    doc.objects = [chart, image]
    chunks = pipeline.chunk(doc)
    chart_chunks = [c for c in chunks if c.kind == "chart"]
    image_chunks = [c for c in chunks if c.kind == "image"]
    # Packed chart values should not create 12 separate tiny chunks.
    assert 1 <= len(chart_chunks) <= 4
    # Text-heavy OCR image should produce linked OCR subchunks within token budget.
    assert len(image_chunks) >= 2
    assert all(c.token_count <= 180 for c in chunks)
    assert all(c.source_object_ids for c in chunks)


def test_tesseract_handles_large_image_within_16mp_budget() -> None:
    try:
        ocr = TesseractOCRProvider("eng")
    except CapabilityUnavailable:
        pytest.skip("tesseract not installed")
    img = Image.new("RGB", (2400, 2000), "white")  # 4.8 MP (> 4MP, <= 16MP)
    draw = ImageDraw.Draw(img)
    draw.text((200, 200), "LARGE IMAGE OK", fill="black")
    buf = BytesIO()
    img.save(buf, format="PNG")
    ref = SourceRef(
        page_number=1,
        bbox=Box(x0=0, y0=0, x1=2400, y1=2000),
        region_id="large-ocr",
        reading_order=0,
        coordinate_system="image_pixels",
    )
    regions = ocr.recognize(buf.getvalue(), ref)
    assert any("LARGE" in r.text or "IMAGE" in r.text for r in regions)


def test_merged_cell_table_is_extracted_with_partial_status() -> None:
    with fitz.open() as pdf:
        p = pdf.new_page(width=600, height=400)
        top = 50
        for y in (top, top + 25, top + 50, top + 75):
            p.draw_line((40, y), (490, y))
        p.draw_line((40, top), (40, top + 75))
        p.draw_line((190, top), (190, top + 75))
        p.draw_line((340, top + 25), (340, top + 75))
        p.draw_line((490, top), (490, top + 75))
        p.insert_text((45, top + 16), "Item", fontsize=10)
        p.insert_text((260, top + 16), "Metrics", fontsize=10)
        p.insert_text((45, top + 41), "A", fontsize=10)
        p.insert_text((195, top + 41), "Min", fontsize=10)
        p.insert_text((345, top + 41), "Max", fontsize=10)
        p.insert_text((45, top + 66), "B", fontsize=10)
        p.insert_text((195, top + 66), "10", fontsize=10)
        p.insert_text((345, top + 66), "20", fontsize=10)
        data = pdf.tobytes()
    doc = DocumentPipeline().extract(data)
    tables = [o for o in doc.objects if isinstance(o, Table)]
    assert len(tables) == 1
    assert tables[0].status == Status.PARTIAL
    assert any(cell.column_span > 1 for cell in tables[0].cells)
