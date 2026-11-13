from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from search_sphere_ingest import DocumentPipeline, IngestError, PipelineConfig, Status
from search_sphere_ingest.persistence import DirectoryAssetStore


def image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (320, 200), "white").save(output, format="PNG")
    return output.getvalue()


def test_real_image_independent_api_preserves_pixels_and_missing_capability(tmp_path: Path) -> None:
    original = image_bytes()
    path = tmp_path / "public.png"
    path.write_bytes(original)
    pipeline = DocumentPipeline(assets=DirectoryAssetStore(tmp_path / "assets"))
    doc = pipeline.extract_image(path)
    assert doc.status == Status.PARTIAL
    assert doc.objects[0].sources[0].coordinate_system == "image_pixels"
    assert doc.objects[0].sources[0].bbox.tuple() == (0, 0, 320, 200)
    assert pipeline.extract(original).document_id == doc.document_id
    assert path.read_bytes() == original
    assert any(issue.code == "vision_provider_unavailable" for issue in doc.issues)
    assert pipeline.chunk(doc) == []


async def test_image_limits_and_async_api() -> None:
    assert (await DocumentPipeline().aextract_image(image_bytes())).status == Status.PARTIAL
    with pytest.raises(IngestError, match="image_pixel_budget"):
        DocumentPipeline(PipelineConfig(max_image_pixels=100)).extract_image(image_bytes())
    with pytest.raises(IngestError, match="invalid_image"):
        DocumentPipeline().extract_image(b"invalid")
    with pytest.raises(IngestError, match="partial_extraction_requires_review"):
        DocumentPipeline(PipelineConfig(require_complete=True)).extract_image(image_bytes())
