import pytest

from search_sphere_ingest import IngestError, PipelineConfig
from search_sphere_ingest.isolation import extract_isolated


def test_real_disposable_native_worker_preserves_text(text_pdf: bytes) -> None:
    doc = extract_isolated(text_pdf)
    assert doc.pages[0].selectable_characters > 0
    assert doc.objects


def test_worker_error_and_partial_are_not_silently_accepted(scan_pdf: bytes) -> None:
    with pytest.raises(IngestError, match="partial_extraction_requires_review"):
        extract_isolated(scan_pdf, PipelineConfig(require_complete=True))
    with pytest.raises(IngestError, match="invalid_pdf"):
        extract_isolated(b"bad")
