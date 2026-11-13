from pathlib import Path

import pymupdf as fitz
import pytest

from search_sphere_ingest import DocumentPipeline, IngestError, PipelineConfig
from search_sphere_ingest.persistence import DirectoryResultStore


def test_namespace_isolation_idempotence_and_immutable_results(
    text_pdf: bytes, tmp_path: Path
) -> None:
    store = DirectoryResultStore(tmp_path)
    result = DocumentPipeline().ingest(text_pdf)
    identity = store.save("tenant-a", result)
    assert store.save("tenant-a", result) == identity
    assert store.load("tenant-a", identity) == result
    with pytest.raises(FileNotFoundError):
        store.load("tenant-b", identity)
    with pytest.raises(ValueError):
        store.load("tenant-a", "../../escape")
    assert all(path.stat().st_mode & 0o077 == 0 for path in tmp_path.rglob("*.json"))


def test_compressed_pdf_stream_expansion_is_bounded() -> None:
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((20, 30), "Public text")
        pdf.update_stream(page.get_contents()[0], b" " * 2_000_000, compress=True)
        data = pdf.tobytes(deflate=True)
    with pytest.raises(IngestError, match="decompression_budget"):
        DocumentPipeline(PipelineConfig(max_content_bytes=100_000)).extract(data)


def test_progress_callback_can_stop_work_without_modifying_source(text_pdf: bytes) -> None:
    events = []

    def progress(stage, page, region):
        events.append((stage, page, region))
        raise IngestError("cancelled")

    with pytest.raises(IngestError, match="cancelled"):
        DocumentPipeline(progress=progress).extract(text_pdf)
    assert events
