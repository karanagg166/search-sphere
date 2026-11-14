"""Real-world document extraction & chunking evaluation suite.

Separates deterministic native extraction, local OCR/CV (Tesseract/OpenCV),
and hosted VLM evaluation. Never counts manually supplied fixture values as
VLM extraction evidence.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

from search_sphere_ingest import DocumentPipeline, PipelineConfig, Status
from search_sphere_ingest.errors import CapabilityUnavailable
from search_sphere_ingest.models import Paragraph, SourceRef, Table
from search_sphere_ingest.panels import estimate_skew_degrees, propose_panels
from search_sphere_ingest.tesseract import TesseractOCRProvider

from .gold_benchmark import cer, wer

CORPUS_DIR = Path(__file__).parent / "corpus"


@dataclass
class RealWorldMetrics:
    corpus_documents: int
    arxiv_pages_extracted: int
    arxiv_heading_recall: float
    arxiv_phrase_recall: float
    arxiv_tables_found: int
    arxiv_visuals_found: int
    multicolumn_reading_order_accuracy: float
    multicolumn_cer: float
    multicolumn_wer: float
    cross_page_merge_accuracy: float
    merged_cell_table_accuracy: float
    multilingual_en_hi_preservation: float
    rotated_page_cer: float
    scanned_pdf_partial_honesty: bool
    chunk_token_limit_compliance: float
    chunk_source_link_completeness: float
    chunk_header_preservation: float
    opencv_panel_accuracy: float | None
    opencv_skew_error_degrees: float | None
    tesseract_ocr_cer: float | None
    tesseract_ocr_wer: float | None
    hosted_vlm_evaluated: bool
    hosted_provider_calls: int
    extraction_latency_seconds: float
    peak_rss_bytes: int
    limitations: list[str]


def _multicolumn_and_tables_pdf() -> bytes:
    with fitz.open() as doc:
        p1 = doc.new_page(width=600, height=800)
        p1.insert_text((40, 45), "1. Executive Summary", fontsize=16)
        p1.insert_text((40, 95), "Left column paragraph one.", fontsize=11)
        p1.insert_text((40, 125), "Left column paragraph two.", fontsize=11)
        p1.insert_text((320, 95), "Right column paragraph one.", fontsize=11)
        p1.insert_text((320, 125), "Right column paragraph two.", fontsize=11)
        # Cross-page table at bottom of page 1
        top1 = 680
        rows1 = [("Metric", "Score"), ("Precision", "0.94"), ("Recall", "0.91")]
        for y in range(top1, top1 + 100, 25):
            p1.draw_line((40, y), (400, y))
        for x in (40, 220, 400):
            p1.draw_line((x, top1), (x, top1 + 75))
        for r, row in enumerate(rows1):
            for c, val in enumerate(row):
                p1.insert_text((45 + c * 180, top1 + r * 25 + 16), val, fontsize=10)

        p2 = doc.new_page(width=600, height=800)
        top2 = 40
        rows2 = [("Metric", "Score"), ("F1", "0.92")]
        for y in range(top2, top2 + 75, 25):
            p2.draw_line((40, y), (400, y))
        for x in (40, 220, 400):
            p2.draw_line((x, top2), (x, top2 + 50))
        for r, row in enumerate(rows2):
            for c, val in enumerate(row):
                p2.insert_text((45 + c * 180, top2 + r * 25 + 16), val, fontsize=10)

        # Table with horizontal merged header cell at y=300
        top3 = 300
        for y in (top3, top3 + 25, top3 + 50, top3 + 75):
            p2.draw_line((40, y), (490, y))
        p2.draw_line((40, top3), (40, top3 + 75))
        p2.draw_line((190, top3), (190, top3 + 75))
        p2.draw_line((340, top3 + 25), (340, top3 + 75))
        p2.draw_line((490, top3), (490, top3 + 75))
        p2.insert_text((45, top3 + 16), "Quarter", fontsize=10)
        p2.insert_text((260, top3 + 16), "Performance", fontsize=10)
        p2.insert_text((45, top3 + 41), "Q1", fontsize=10)
        p2.insert_text((195, top3 + 41), "Latency (ms)", fontsize=10)
        p2.insert_text((345, top3 + 41), "Throughput (rps)", fontsize=10)
        p2.insert_text((45, top3 + 66), "Q2", fontsize=10)
        p2.insert_text((195, top3 + 66), "19", fontsize=10)
        p2.insert_text((345, top3 + 66), "520", fontsize=10)
        return doc.tobytes()


def _multilingual_pdf() -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_text(
            (40, 60),
            "WE, THE PEOPLE OF INDIA, having solemnly resolved to constitute India.",
            fontsize=11,
        )
        # Render Devanagari via insert_htmlbox so Unicode shaping & fallback font embed cleanly
        page.insert_htmlbox(
            fitz.Rect(40, 100, 560, 200),
            "<p style='font-size:13pt'>हम, भारत के लोग, भारत को एक संपूर्ण प्रभुत्व-संपन्न राज्य बनाने के लिए।</p>",
        )
        return doc.tobytes()


def _rotated_pdf() -> bytes:
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text(
            (60, 120), "Rotated ninety degrees exact verification sentence.", fontsize=13
        )
        page.set_rotation(90)
        return doc.tobytes()


def _scanned_pdf() -> bytes:
    image = Image.new("RGB", (800, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 80), "INVOICE TOTAL 420.50 USD", fill="black")
    buf = BytesIO()
    image.save(buf, format="PNG")
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=400)
        page.insert_image(fitz.Rect(40, 40, 560, 200), stream=buf.getvalue())
        return doc.tobytes()


def _skewed_image(degrees: float = 7.0) -> bytes:
    image = Image.new("RGB", (800, 300), "white")
    draw = ImageDraw.Draw(image)
    for y in (60, 110, 160, 210):
        draw.rectangle((80, y, 720, y + 18), fill="black")
    rotated = image.rotate(degrees, expand=True, fillcolor="white")
    buf = BytesIO()
    rotated.save(buf, format="PNG")
    return buf.getvalue()


def _ocr_test_image() -> bytes:
    image = Image.new("RGB", (600, 140), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 50), "INVOICE TOTAL 420.50 USD", fill="black")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _panel_grid_image() -> bytes:
    image = Image.new("RGB", (800, 800), "white")
    draw = ImageDraw.Draw(image)
    for box in ((50, 50, 340, 340), (460, 50, 750, 340), (50, 460, 340, 750), (460, 460, 750, 750)):
        draw.rectangle(box, outline="black", width=10)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def run_evaluation() -> RealWorldMetrics:
    started = time.monotonic()
    manifest = json.loads((CORPUS_DIR / "MANIFEST.json").read_text())
    pipeline = DocumentPipeline(PipelineConfig(target_tokens=380, max_tokens=480))

    # 1. Real arXiv CC-BY paper (pages 1-8)
    arxiv_path = CORPUS_DIR / "arxiv_1706_03762_sample.pdf"
    arxiv_res = pipeline.ingest(arxiv_path)
    arxiv_doc = arxiv_res.document
    arxiv_text = "\n".join(obj.text for obj in arxiv_doc.objects if isinstance(obj, Paragraph))
    arxiv_exp = manifest["documents"][0]["expected"]
    section_titles = " ".join(s.title for s in arxiv_doc.sections).lower()
    heading_hits = sum(
        1
        for h in arxiv_exp["required_headings"]
        if h.lower() in section_titles or h.lower() in arxiv_text.lower()
    )
    phrase_hits = sum(1 for p in arxiv_exp["required_phrases"] if p in arxiv_text)
    arxiv_tables = [o for o in arxiv_doc.objects if isinstance(o, Table)]
    arxiv_visuals = [o for o in arxiv_doc.objects if o.kind in {"image", "chart", "diagram"}]

    # 2. Multi-column + cross-page + merged-cell tables
    mc_res = pipeline.ingest(_multicolumn_and_tables_pdf())
    mc_paras = [o.text for o in mc_res.document.objects if isinstance(o, Paragraph)]
    mc_joined = " ".join(mc_paras)
    expected_order = manifest["documents"][1]["expected"]["reading_order_phrases"]
    order_hits = sum(
        mc_joined.find(expected_order[i]) < mc_joined.find(expected_order[i + 1])
        for i in range(len(expected_order) - 1)
        if expected_order[i] in mc_joined and expected_order[i + 1] in mc_joined
    )
    mc_tables = [o for o in mc_res.document.objects if isinstance(o, Table)]
    cross_page_ok = 1.0 if any(len(t.sources) == 2 and len(t.rows) == 3 for t in mc_tables) else 0.0
    merged_ok = 1.0 if any("Quarter" in t.headers and len(t.rows) >= 1 for t in mc_tables) else 0.0

    # 3. Multilingual English + Hindi (Devanagari)
    hi_res = pipeline.ingest(_multilingual_pdf())
    hi_text = " ".join(o.text for o in hi_res.document.objects if isinstance(o, Paragraph))
    hi_phrases = manifest["documents"][2]["expected"]["required_phrases"]
    hi_acc = sum(1 for p in hi_phrases if p in hi_text) / len(hi_phrases)

    # 4. Rotated PDF & Scanned PDF honesty
    rot_res = pipeline.ingest(_rotated_pdf())
    rot_text = " ".join(o.text for o in rot_res.document.objects if isinstance(o, Paragraph))
    rot_cer = cer("Rotated ninety degrees exact verification sentence.", rot_text)
    scan_res = pipeline.ingest(_scanned_pdf())
    scan_honest = scan_res.document.status == Status.PARTIAL

    # 5. Chunking quality across all ingested documents
    all_docs = [arxiv_res.document, mc_res.document, hi_res.document, rot_res.document]
    all_chunks = arxiv_res.chunks + mc_res.chunks + hi_res.chunks + rot_res.chunks
    all_obj_ids = {obj.object_id for doc in all_docs for obj in doc.objects}
    token_ok = sum(1 for c in all_chunks if 1 <= c.token_count <= 480) / max(1, len(all_chunks))
    linked_ok = sum(
        1
        for c in all_chunks
        if c.source_object_ids and all(oid in all_obj_ids for oid in c.source_object_ids)
    ) / max(1, len(all_chunks))
    header_ok = sum(
        1 for c in all_chunks if not c.heading_path or all(h in c.text for h in c.heading_path[-1:])
    ) / max(1, len(all_chunks))

    # 6. Optional local OpenCV & Tesseract evaluation
    opencv_panel_acc: float | None = None
    opencv_skew_err: float | None = None
    try:
        panels = propose_panels(_panel_grid_image())
        opencv_panel_acc = 1.0 if len(panels) == 4 else 0.0
        est = estimate_skew_degrees(_skewed_image(7.0))
        opencv_skew_err = round(abs(abs(est) - 7.0), 2)
    except CapabilityUnavailable:
        pass

    tess_cer: float | None = None
    tess_wer: float | None = None
    try:
        ocr = TesseractOCRProvider("eng")
        ocr_img = _ocr_test_image()
        ref = SourceRef(
            page_number=1,
            bbox={"x0": 0, "y0": 0, "x1": 600, "y1": 140},  # type: ignore[arg-type]
            region_id="ocr-eval-1",
            reading_order=0,
            coordinate_system="image_pixels",
        )
        regions = ocr.recognize(ocr_img, ref)
        hyp = " ".join(r.text for r in regions)
        tess_cer = round(cer("INVOICE TOTAL 420.50 USD", hyp), 4)
        tess_wer = round(wer("INVOICE TOTAL 420.50 USD", hyp), 4)
    except CapabilityUnavailable:
        pass

    elapsed = round(time.monotonic() - started, 4)
    raw_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = raw_rss if sys.platform == "darwin" else raw_rss * 1024

    return RealWorldMetrics(
        corpus_documents=len(manifest["documents"]),
        arxiv_pages_extracted=len(arxiv_doc.pages),
        arxiv_heading_recall=round(heading_hits / len(arxiv_exp["required_headings"]), 4),
        arxiv_phrase_recall=round(phrase_hits / len(arxiv_exp["required_phrases"]), 4),
        arxiv_tables_found=len(arxiv_tables),
        arxiv_visuals_found=len(arxiv_visuals),
        multicolumn_reading_order_accuracy=round(order_hits / (len(expected_order) - 1), 4),
        multicolumn_cer=round(cer(" ".join(expected_order), mc_joined), 4),
        multicolumn_wer=round(wer(" ".join(expected_order), mc_joined), 4),
        cross_page_merge_accuracy=cross_page_ok,
        merged_cell_table_accuracy=merged_ok,
        multilingual_en_hi_preservation=round(hi_acc, 4),
        rotated_page_cer=round(rot_cer, 4),
        scanned_pdf_partial_honesty=scan_honest,
        chunk_token_limit_compliance=round(token_ok, 4),
        chunk_source_link_completeness=round(linked_ok, 4),
        chunk_header_preservation=round(header_ok, 4),
        opencv_panel_accuracy=opencv_panel_acc,
        opencv_skew_error_degrees=opencv_skew_err,
        tesseract_ocr_cer=tess_cer,
        tesseract_ocr_wer=tess_wer,
        hosted_vlm_evaluated=False,
        hosted_provider_calls=0,
        extraction_latency_seconds=elapsed,
        peak_rss_bytes=peak,
        limitations=[
            "hosted_vlm_chart_diagram_accuracy_requires_live_cohere_smoke",
            "handwriting_and_hindi_ocr_require_traineddata_or_hosted_vlm",
            "docling_tableformer_excluded_from_512mb_free_render",
        ],
    )


def main() -> None:
    print(json.dumps(asdict(run_evaluation()), indent=2))


if __name__ == "__main__":
    main()
