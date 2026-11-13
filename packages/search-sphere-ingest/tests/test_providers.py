import hashlib
import shutil
from io import BytesIO
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from conftest import panel_image
from PIL import Image

from search_sphere_ingest import DocumentPipeline, IngestError, PipelineConfig, Status
from search_sphere_ingest.docling import DoclingLayoutProvider
from search_sphere_ingest.gemini import GeminiVisionProvider
from search_sphere_ingest.models import OCRRegion, Visual
from search_sphere_ingest.panels import propose_panels
from search_sphere_ingest.providers import merge_image_evidence
from search_sphere_ingest.tesseract import TesseractOCRProvider


def test_free_guard_before_constructing_client() -> None:
    with pytest.raises(IngestError, match="free_account_not_verified"):
        GeminiVisionProvider("synthetic-key", approved_free_key_sha256="")


@pytest.mark.parametrize(
    "raw",
    ["not json", '{"kind":"chart"}', '{"kind":"image","visual_description":"x","extra":"bad"}'],
)
def test_provider_invalid_json_is_safe_error(text_pdf: bytes, raw: str) -> None:
    pytest.importorskip("google.genai")
    provider = GeminiVisionProvider(
        "synthetic",
        approved_free_key_sha256=hashlib.sha256(b"synthetic").hexdigest(),
        client=SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=raw))
        ),
    )
    ref = DocumentPipeline().extract(text_pdf).objects[0].sources[0]
    with pytest.raises(IngestError, match="invalid_provider_response"):
        provider.understand(b"image", ref)


def test_provider_numbers_and_edges_are_never_self_verified(text_pdf: bytes) -> None:
    pytest.importorskip("google.genai")
    raw = '{"kind":"chart","visual_description":"Public bars","values":[{"series":"S","label":"A","value":10,"evidence":"visible_label","verified":true}],"panel_status":"verified_single"}'
    provider = GeminiVisionProvider(
        "synthetic",
        approved_free_key_sha256=hashlib.sha256(b"synthetic").hexdigest(),
        client=SimpleNamespace(
            models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=raw))
        ),
        min_interval_seconds=0,
        max_calls=1,
    )
    ref = DocumentPipeline().extract(text_pdf).objects[0].sources[0]
    visual = provider.understand(b"image", ref)
    assert visual.status == Status.PARTIAL
    assert visual.values[0].value == 10 and visual.values[0].verified is False
    assert visual.sources[0].model == "gemini-3.5-flash-lite"
    with pytest.raises(IngestError, match="provider_call_budget"):
        provider.understand(b"image", ref)


@pytest.mark.parametrize("code,retryable", [(429, True), (503, True), (401, False)])
def test_rate_limits_and_provider_failures_are_classified(
    text_pdf: bytes, code: int, retryable: bool
) -> None:
    pytest.importorskip("google.genai")

    def fail(**kwargs):
        error = RuntimeError("sensitive provider body must never escape")
        error.code = code
        raise error

    provider = GeminiVisionProvider(
        "synthetic",
        approved_free_key_sha256=hashlib.sha256(b"synthetic").hexdigest(),
        client=SimpleNamespace(models=SimpleNamespace(generate_content=fail)),
    )
    ref = DocumentPipeline().extract(text_pdf).objects[0].sources[0]
    with pytest.raises(IngestError) as error:
        provider.understand(b"image", ref)
    assert error.value.retryable is retryable
    assert "sensitive" not in str(error.value)


def test_opencv_proposes_panels_without_discarding_parent(scan_pdf: bytes) -> None:
    pytest.importorskip("cv2")
    boxes = propose_panels(panel_image())
    assert len(boxes) == 2
    assert boxes[0].x1 == boxes[1].x0
    doc = DocumentPipeline(PipelineConfig(use_opencv=True)).extract(scan_pdf)
    visuals = [obj for obj in doc.objects if isinstance(obj, Visual)]
    assert len(visuals) == 3
    assert all(
        obj.sources[0].parent_region_id == visuals[0].sources[0].region_id for obj in visuals[1:]
    )
    assert doc.status == Status.PARTIAL


def test_ocr_and_description_merge_preserves_literal_evidence(text_pdf: bytes) -> None:
    ref = DocumentPipeline().extract(text_pdf).objects[0].sources[0]
    visual = Visual(
        object_id="image", sources=[ref], visual_description="A red sign; wording uncertain."
    )
    word = OCRRegion(text="BRIDGE CLOSED", bbox=ref.bbox, language="eng", recognition_model="test")
    merged = merge_image_evidence(visual, [word, word])
    assert merged.ocr_text == "BRIDGE CLOSED"
    assert len(merged.text_regions) == 1
    assert merged.visual_description == visual.visual_description


def test_docling_is_explicitly_blocked_in_512mb_environment(tmp_path) -> None:
    with pytest.raises(IngestError, match="docling_memory"):
        DoclingLayoutProvider(artifacts_path=tmp_path, memory_budget_mb=512)


def test_actual_transport_timeout_is_retryable(text_pdf: bytes) -> None:
    pytest.importorskip("google.genai")
    import httpx

    def timeout(**kwargs):
        raise httpx.ReadTimeout("untrusted provider details")

    provider = GeminiVisionProvider(
        "synthetic",
        approved_free_key_sha256=hashlib.sha256(b"synthetic").hexdigest(),
        client=SimpleNamespace(models=SimpleNamespace(generate_content=timeout)),
    )
    ref = DocumentPipeline().extract(text_pdf).objects[0].sources[0]
    with pytest.raises(IngestError) as error:
        provider.understand(b"image", ref)
    assert error.value.retryable and str(error.value) == "provider_unavailable"


@pytest.mark.skipif(not shutil.which("tesseract"), reason="real tesseract binary unavailable")
def test_actual_tesseract_on_real_raster_text() -> None:
    with fitz.open() as pdf:
        page = pdf.new_page(width=500, height=150)
        page.insert_text((20, 70), "PUBLIC VALUE 120", fontsize=26)
        image = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
        page.insert_image(page.rect, stream=image)
        # A new PDF with only a raster page genuinely requires OCR.
        with fitz.open() as scan:
            raster = scan.new_page(width=500, height=150)
            raster.insert_image(raster.rect, stream=image)
            data = scan.tobytes()
    doc = DocumentPipeline(ocr=TesseractOCRProvider()).extract(data)
    visual = next(item for item in doc.objects if isinstance(item, Visual))
    assert "PUBLIC" in visual.ocr_text and "120" in visual.ocr_text
    assert all(region.bbox.x1 <= 500 for region in visual.text_regions)
    assert doc.status == Status.PARTIAL  # OCR is not chart/image interpretation.


def test_photo_without_text_never_creates_fake_search_chunk() -> None:
    image = Image.new("RGB", (300, 200), "blue")
    output = BytesIO()
    image.save(output, "PNG")
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.insert_image(page.rect, stream=output.getvalue())
        data = doc.tobytes()
    result = DocumentPipeline().ingest(data)
    assert result.document.status == Status.PARTIAL
    assert result.chunks == []
