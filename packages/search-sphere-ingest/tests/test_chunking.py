import pytest

from search_sphere_ingest import DocumentPipeline, IngestError, PipelineConfig
from search_sphere_ingest.models import Box, ChartValue, Edge, Node, Paragraph, Table, Visual
from search_sphere_ingest.text import sentences


def test_actual_token_budget_overlap_section_boundaries_and_no_loss(text_pdf: bytes) -> None:
    pipeline = DocumentPipeline(
        PipelineConfig(target_tokens=30, max_tokens=55, overlap_sentences=1)
    )
    document = pipeline.extract(text_pdf)
    original = next(item for item in document.objects if isinstance(item, Paragraph))
    text = " ".join(f"Sentence number {i} has useful evidence." for i in range(50))
    original = Paragraph.model_validate(
        {**original.model_dump(), "text": text, "sentences": sentences(text, original.object_id)}
    )
    document.objects = [original]
    chunks = pipeline.chunk(document)
    assert len(chunks) > 3
    assert all(chunk.token_count == pipeline.tokenizer.count(chunk.text) <= 55 for chunk in chunks)
    assert set(sentence.sentence_id for sentence in original.sentences) == {
        s for chunk in chunks for s in chunk.sentence_ids
    }
    assert any(
        set(a.sentence_ids) & set(b.sentence_ids) for a, b in zip(chunks, chunks[1:], strict=False)
    )
    assert all(chunk.heading_path == original.heading_path for chunk in chunks)


def test_oversized_unicode_sentence_preserves_characters(text_pdf: bytes) -> None:
    pipeline = DocumentPipeline(PipelineConfig(target_tokens=20, max_tokens=40))
    doc = pipeline.extract(text_pdf)
    paragraph = next(item for item in doc.objects if isinstance(item, Paragraph))
    paragraph.heading_path = []
    doc.title = ""
    text = "हिन्दी" * 100
    paragraph = Paragraph.model_validate(
        {**paragraph.model_dump(), "text": text, "sentences": sentences(text, paragraph.object_id)}
    )
    doc.objects = [paragraph]
    chunks = pipeline.chunk(doc)
    assert all(chunk.fragment for chunk in chunks)
    assert "".join(chunk.text.lstrip("\n") for chunk in chunks) == paragraph.text


def test_table_row_groups_repeat_headers_without_losing_rows(table_pdf: bytes) -> None:
    pipeline = DocumentPipeline(PipelineConfig(target_tokens=20, max_tokens=60))
    doc = pipeline.extract(table_pdf)
    table = next(item for item in doc.objects if isinstance(item, Table))
    table = Table.model_validate(
        {
            **table.model_dump(),
            "rows": [[f"ROW-{i}", str(i)] for i in range(30)],
            "row_pages": [1] * 30,
        }
    )
    doc.objects = [table]
    chunks = pipeline.chunk(doc)
    assert len(chunks) > 1
    assert all("Item | Value (mg)" in chunk.text for chunk in chunks)
    assert [i for chunk in chunks for i in range(chunk.row_start, chunk.row_end)] == list(range(30))
    doc.objects = [
        Table.model_validate(
            {**table.model_dump(), "rows": [["X" * 1000, "42"]], "row_pages": [1], "cells": []}
        )
    ]
    with pytest.raises(IngestError, match="table_row_exceeds_token_budget"):
        pipeline.chunk(doc)


def test_chart_and_diagram_objects_survive_linked_chunking(text_pdf: bytes) -> None:
    pipeline = DocumentPipeline(PipelineConfig(target_tokens=40, max_tokens=100))
    doc = pipeline.extract(text_pdf)
    ref = doc.objects[0].sources[0]
    chart = Visual(
        object_id="chart-1",
        kind="chart",
        title="Public bars",
        sources=[ref],
        values=[
            ChartValue(series="public", label=str(i), value=float(i), evidence="estimated")
            for i in range(30)
        ],
    )
    diagram = Visual(
        object_id="diagram-1",
        kind="diagram",
        sources=[ref],
        nodes=[Node(node_id="a", label="Start"), Node(node_id="b", label="Finish")],
        edges=[Edge(source="a", target="b", label="success")],
    )
    doc.objects = [chart, diagram]
    chunks = pipeline.chunk(doc)
    assert {chunk.source_object_ids[0] for chunk in chunks} == {"chart-1", "diagram-1"}
    assert any("Start -> Finish" in chunk.text for chunk in chunks)
    assert len(chart.values) == 30


@pytest.mark.parametrize("bounds", [(10, 0, 5, 20), (0, 0, float("inf"), 1), (0, 1, 3, 1)])
def test_bad_geometry_is_rejected(bounds: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        Box(x0=bounds[0], y0=bounds[1], x1=bounds[2], y1=bounds[3])


def test_unreadable_numeric_and_invalid_graph_rejected(text_pdf: bytes) -> None:
    with pytest.raises(ValueError):
        ChartValue(series="x", label="bad", value=9, evidence="unreadable")
    ref = DocumentPipeline().extract(text_pdf).objects[0].sources[0]
    with pytest.raises(ValueError):
        Visual(
            object_id="invalid",
            kind="diagram",
            sources=[ref],
            edges=[Edge(source="missing", target="also missing")],
        )
