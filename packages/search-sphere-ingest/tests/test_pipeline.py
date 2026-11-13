import hashlib
from pathlib import Path

import pymupdf as fitz
import pytest

from search_sphere_ingest import (
    DocumentPipeline,
    IngestError,
    NormalizedDocument,
    PipelineConfig,
    Status,
)
from search_sphere_ingest.models import Paragraph, Table, Visual


def test_real_native_pdf_independent_roundtrip_and_stable_ids(
    text_pdf: bytes, tmp_path: Path
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(text_pdf)
    pipeline = DocumentPipeline()
    result = pipeline.ingest(path)
    assert result.document.status == Status.COMPLETED
    assert result.document.source_sha256 == hashlib.sha256(text_pdf).hexdigest()
    assert len(result.document.pages) == 2
    assert result.chunks
    assert [item.chunk_id for item in result.chunks] == [
        item.chunk_id for item in pipeline.ingest(text_pdf).chunks
    ]
    assert (
        NormalizedDocument.model_validate_json(result.document.model_dump_json()) == result.document
    )
    assert path.read_bytes() == text_pdf
    assert all(item.source_object_ids for item in result.chunks)


def test_heading_context_crosses_pages_and_sentence_offsets_are_lossless(text_pdf: bytes) -> None:
    doc = DocumentPipeline().extract(text_pdf)
    second = next(
        item
        for item in doc.objects
        if isinstance(item, Paragraph) and item.sources[0].page_number == 2
    )
    assert second.heading_path == ["1. Native extraction"]
    for item in doc.objects:
        if isinstance(item, Paragraph):
            assert all(
                item.text[sentence.start : sentence.end] == sentence.text
                for sentence in item.sentences
            )
    assert "1. Native extraction" in DocumentPipeline().chunk(doc)[-1].text


def test_real_cross_page_table_is_one_logical_object(table_pdf: bytes) -> None:
    pipeline = DocumentPipeline()
    doc = pipeline.extract(table_pdf)
    tables = [item for item in doc.objects if isinstance(item, Table)]
    assert len(tables) == 1
    assert tables[0].rows == [["A-101", "120"], ["B-102", "80"], ["C-103", "42"], ["D-104", "61"]]
    assert tables[0].row_pages == [1, 1, 2, 2]
    assert len(tables[0].sources) == 2
    assert tables[0].sources[0].bbox.tuple() == pytest.approx((40, 690, 400, 765))
    assert tables[0].sources[1].bbox.tuple() == pytest.approx((40, 40, 400, 115))
    assert tables[0].continuation_confidence >= 0.9
    assert tables[0].cells
    chunks = [chunk for chunk in pipeline.chunk(doc) if chunk.kind == "table"]
    assert {id_ for chunk in chunks for id_ in chunk.source_object_ids} == {tables[0].object_id}
    assert chunks[0].row_start == 0 and chunks[-1].row_end == 4


def test_scan_is_preserved_and_explicitly_partial(scan_pdf: bytes) -> None:
    doc = DocumentPipeline().extract(scan_pdf)
    assert doc.status == Status.PARTIAL
    assert doc.pages[0].status == Status.PARTIAL
    assert any(issue.code == "vision_provider_unavailable" for issue in doc.issues)
    assert any(
        isinstance(item, Visual) and item.panel_status == "unresolved" for item in doc.objects
    )
    with pytest.raises(IngestError, match="partial_extraction_requires_review"):
        DocumentPipeline(PipelineConfig(require_complete=True)).extract(scan_pdf)


@pytest.mark.parametrize("data", [b"", b"not pdf", b"%PDF-invalid"])
def test_invalid_pdf_fails(data: bytes) -> None:
    with pytest.raises(IngestError):
        DocumentPipeline().extract(data)


def test_encryption_size_page_limits(text_pdf: bytes) -> None:
    with pytest.raises(IngestError, match="page_budget"):
        DocumentPipeline(PipelineConfig(max_pages=1)).extract(text_pdf)
    with pytest.raises(IngestError, match="file_too_large"):
        DocumentPipeline(PipelineConfig(max_file_bytes=1024)).extract(text_pdf + b" " * 2048)
    with fitz.open(stream=text_pdf, filetype="pdf") as doc:
        protected = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="synthetic-only")
    with pytest.raises(IngestError, match="encrypted_pdf"):
        DocumentPipeline().extract(protected)


def test_rotation_preserves_original_coordinates(text_pdf: bytes) -> None:
    with fitz.open(stream=text_pdf, filetype="pdf") as pdf:
        pdf[0].set_rotation(90)
        content = pdf.tobytes()
    doc = DocumentPipeline().extract(content)
    assert doc.pages[0].rotation == 90
    assert doc.objects[0].sources[0].coordinate_system == "unrotated_pdf_points"
    assert doc.objects[0].sources[0].bbox.x1 <= doc.pages[0].width


async def test_async_public_api(text_pdf: bytes) -> None:
    assert (await DocumentPipeline().aingest(text_pdf)).chunks


def test_vector_figure_retains_native_labels_geometry_and_uncertainty() -> None:
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.draw_line((100, 100), (100, 300))
        page.draw_line((100, 300), (400, 300))
        page.draw_bezier((110, 290), (200, 150), (300, 250), (390, 120))
        page.insert_text((120, 320), "Figure 1: Public curve")
        page.insert_text((120, 110), "Value 120")
        data = pdf.tobytes()
    result = DocumentPipeline().ingest(data)
    visual = next(item for item in result.document.objects if isinstance(item, Visual))
    assert visual.native_vector_paths
    assert "120" in visual.native_text
    assert "Figure 1" in visual.caption
    assert result.document.status == Status.PARTIAL
    NormalizedDocument.model_validate_json(result.document.model_dump_json())


def test_native_multilingual_labels_are_not_sent_to_a_model() -> None:
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 80), "公共文档数据", fontname="china-s")
        data = pdf.tobytes()
    doc = DocumentPipeline().extract(data)
    assert "公共文档数据" in "".join(
        item.text for item in doc.objects if isinstance(item, Paragraph)
    )
    assert doc.status == Status.COMPLETED
