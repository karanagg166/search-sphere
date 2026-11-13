"""Hosted layout responses are untrusted; only validated readable evidence survives."""

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pymupdf
import pytest
from PIL import Image

from src.inference.policy import ProviderInvalidResponseError
from src.processing.chunking.structured_chunks import chart_chunks
from src.processing.extraction.document_extractor import DocumentExtractor
from src.processing.extraction.hosted_layout import HostedLayoutRecognizer
from src.processing.extraction.medical_observation_extractor import (
    MedicalObservationExtractor,
)


def png():
    buffer = BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def chart_region():
    return {
        "kind": "chart", "bbox": [0, 0, 1000, 1000], "title": "Public synthetic chart",
        "axes": [{"label": "Month", "unit": "month", "scale": "categorical"}, {"label": "Count", "unit": "items", "scale": "linear"}],
        "legend": ["Public A"], "series": [{"name": "Public A", "points": [
            {"label": "January", "value": "42", "readability": "readable"},
            {"label": "February", "value": "80", "readability": "approximate"},
            {"label": "March", "value": "120", "readability": "unreadable"},
        ]}],
    }


def recognize(regions):
    recognizer = HostedLayoutRecognizer(SimpleNamespace(inspect_image=lambda *a, **kw: json.dumps({"regions": regions})))
    return recognizer.recognize(png(), page_number=2, bbox=[40, 80, 440, 380], source_hash="a" * 64, original_order=3)


def test_chart_preserves_source_crop_axes_units_readability_and_disables_exact_comparisons():
    chart = recognize([chart_region()])[0]
    assert chart.block_type == "chart" and chart.crop.startswith(b"\x89PNG")
    assert chart.provenance["page_number"] == 2 and chart.provenance["bbox"] == [40, 80, 440, 380]
    assert chart.structured["axes"][1]["unit"] == "items"
    points = chart.structured["series"][0]["points"]
    assert points[0]["value"] == "42" and points[1]["readability"] == "approximate"
    assert points[2]["value"] is None and all(point["verified"] is False for point in points)
    assert chart.structured["exact_comparisons_allowed"] is False


@pytest.mark.asyncio
async def test_chart_fits_one_logical_chunk_and_large_charts_split_at_point_boundaries():
    chart = recognize([chart_region()])[0]
    chunks = await chart_chunks(chart, 2, SimpleNamespace(token_count=AsyncMock(return_value=200)))
    assert len(chunks) == 1 and chunks[0].block_types == ["chart"]
    chart.structured["series"][0]["points"] *= 20
    counter = SimpleNamespace(token_count=AsyncMock(side_effect=lambda text, **kw: len(text) // 3))
    chunks = await chart_chunks(chart, 2, counter)
    assert len(chunks) > 1 and all(chunk.token_count <= 512 for chunk in chunks)
    assert chunks[0].provenance[0]["point_start"] == 0 and chunks[-1].provenance[0]["point_end"] == 60
    assert all(chunk.provenance[0]["evidence_id"] == chart.structured["evidence_id"] for chunk in chunks)


@pytest.mark.parametrize("region", [
    {"kind": "tool_call", "bbox": [0, 0, 1000, 1000]},
    {"kind": "text", "bbox": [0, 0, 1001, 1000], "visible_text": "public"},
    {"kind": "text", "bbox": [400, 0, 10, 1000], "visible_text": "public"},
    {"kind": "table", "bbox": [0, 0, 1000, 1000], "headers": ["A", "B"], "rows": [["42"]]},
    {**chart_region(), "exact_comparisons_allowed": True},
    {**chart_region(), "series": [{"name": "A", "points": [{"label": "A", "value": "42", "readability": "verified"}]}]},
])
def test_invalid_or_privilege_claiming_layout_output_is_rejected(region):
    with pytest.raises(ProviderInvalidResponseError):
        recognize([region])


def test_scanned_table_and_ocr_do_not_become_exact_clinical_values():
    table = {"kind": "table", "bbox": [0, 0, 1000, 500], "headers": ["Vital", "Value"], "rows": [["BP", "120/80 mmHg"]]}
    blocks = recognize([table, {"kind": "image", "bbox": [0, 500, 1000, 1000], "visible_text": "BP 120/80 mmHg", "description": "Generated example"}])
    document = SimpleNamespace(pages=[SimpleNamespace(page_number=1, blocks=blocks)])
    assert MedicalObservationExtractor().extract_from_extracted_document(document) == []


def test_scanned_page_uses_one_layout_call_and_does_not_duplicate_image_or_chart_text():
    calls = []
    def inspect(*args, **kwargs):
        calls.append(kwargs)
        return json.dumps({"regions": [chart_region()]})
    recognizer = HostedLayoutRecognizer(SimpleNamespace(inspect_image=inspect))
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_image(pymupdf.Rect(40, 80, 440, 380), stream=png())
    content = pdf.tobytes()
    pdf.close()
    extracted = DocumentExtractor(layout_recognizer=recognizer).extract(content)
    assert len(calls) == 1 and len(extracted.pages[0].blocks) == 1
    assert extracted.pages[0].blocks[0].block_type == "chart"


def test_dedicated_diagram_preserves_graph_ocr_boxes_and_unverified_edges():
    region = {"kind": "diagram", "bbox": [0, 0, 1000, 1000], "title": "Public flow",
        "nodes": [{"node_id": "a", "label": "Start", "kind": "process", "bbox": [50, 50, 400, 300]},
                  {"node_id": "b", "label": "Continue?", "kind": "decision", "bbox": [500, 500, 950, 900]}],
        "edges": [{"source": "a", "target": "b", "label": "next", "direction": "directed", "confidence": 0.8}],
        "visible_text": "Start Continue?", "description": "Two connected steps",
        "text_regions": [{"text": "Start", "bbox": [60, 60, 390, 290], "language": "en", "style": "printed"}]}
    block = recognize([region])[0]
    assert block.block_type == 'diagram' and block.crop.startswith(b'\x89PNG')
    assert block.structured['edges'][0]['visible'] is False
    assert block.structured['nodes'][0]['bbox'] == [60.0, 95.0, 200.0, 170.0]
    assert block.structured['ocr_text'] == 'Start Continue?'
    assert block.structured['visual_description'] == 'Two connected steps'
    assert block.structured['extraction_status'] == 'PARTIAL'
    region['edges'][0]['target'] = 'invented'
    with pytest.raises(ProviderInvalidResponseError):
        recognize([region])


def test_uncertain_panel_detection_retains_parent_original(monkeypatch):
    from search_sphere_ingest.models import Box
    monkeypatch.setattr('src.processing.extraction.hosted_layout.propose_panels', lambda image: [
        Box(x0=0, y0=0, x1=200, y1=300), Box(x0=200, y0=0, x1=400, y1=300)])
    blocks = recognize([chart_region()])
    assert len(blocks) == 2 and blocks[0].structured['container_only']
    assert blocks[0].crop == png() and not blocks[0].content
    assert blocks[1].structured['parent_region_id'] == blocks[0].provenance['block_id']
    assert blocks[1].structured['panel_status'] == 'unresolved'
    assert 'multiple_panels_not_separated' in blocks[1].structured['uncertainties']
