"""Storage independent, versioned objects. Coordinates are unrotated PDF points."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(max_length=200_000)]


def stable_id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Status(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"


class Box(Model):
    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("Empty or reversed box")
        return self

    def tuple(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1


class Issue(Model):
    code: str
    stage: str
    page_number: int | None = None
    region_id: str | None = None
    retryable: bool = False


class SourceRef(Model):
    page_number: int = Field(ge=1)
    bbox: Box
    coordinate_system: Literal["unrotated_pdf_points", "image_pixels"] = "unrotated_pdf_points"
    region_id: str
    parent_region_id: str | None = None
    reading_order: int = Field(ge=0)
    # Opaque ID, resolved by an authorized persistence adapter; never a public URL.
    source_image: str | None = None
    provider: str = "pymupdf"
    model: str | None = None
    evidence: list[str] = Field(default_factory=list, max_length=64)
    confidence: float | None = Field(default=None, ge=0, le=1)


class Page(Model):
    page_number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int
    selectable_characters: int = Field(ge=0)
    status: Status = Status.COMPLETED
    # Original crop box and PDF transform permit mapping back to PDF user space.
    cropbox: tuple[float, float, float, float]
    pdf_to_page: tuple[float, float, float, float, float, float] | None


class Section(Model):
    section_id: str
    parent_section_id: str | None = None
    title: Text
    level: int = Field(ge=1, le=6)
    path: list[str] = Field(max_length=6)
    source: SourceRef


class Sentence(Model):
    sentence_id: str
    text: Text
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class Paragraph(Model):
    object_id: str
    kind: Literal["text"] = "text"
    text: Text
    section_id: str | None = None
    heading_path: list[str] = Field(default_factory=list, max_length=6)
    sentences: list[Sentence] = Field(default_factory=list, max_length=20_000)
    sources: list[SourceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def offsets(self) -> Self:
        previous_end = 0
        for sentence in self.sentences:
            if (
                sentence.start < previous_end
                or self.text[sentence.start : sentence.end] != sentence.text
            ):
                raise ValueError("Sentence offsets must match original paragraph")
            previous_end = sentence.end
        return self


class Cell(Model):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    text: Text
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    header: bool = False
    source: SourceRef


class Table(Model):
    object_id: str
    kind: Literal["table"] = "table"
    title: Text = ""
    headers: list[Text] = Field(min_length=1, max_length=64)
    rows: list[list[Text]] = Field(min_length=1, max_length=10_000)
    cells: list[Cell] = Field(default_factory=list, max_length=100_000)
    units: list[Text] = Field(default_factory=list)
    row_pages: list[int] = Field(default_factory=list)
    column_edges: list[float] = Field(default_factory=list)
    sources: list[SourceRef] = Field(min_length=1)
    section_id: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    continuation_confidence: float | None = Field(default=None, ge=0, le=1)
    status: Status = Status.COMPLETED

    @model_validator(mode="after")
    def rectangular(self) -> Self:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("Invalid table width")
        if len(self.row_pages) != len(self.rows):
            raise ValueError("Every row requires page provenance")
        for cell in self.cells:
            if (
                cell.column + cell.column_span > len(self.headers)
                or cell.row + cell.row_span > len(self.rows) + 1
            ):
                raise ValueError("Cell span outside table")
        return self


class OCRRegion(Model):
    text: Text
    bbox: Box
    language: str = "und"
    recognition_model: str
    style: Literal["printed", "handwritten", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0, le=1)


class ChartValue(Model):
    series: Text
    label: Text
    value: float | None
    raw_value: Text | None = None
    unit: Text = ""
    evidence: Literal["visible_label", "estimated", "unreadable"]
    verified: bool = False

    @model_validator(mode="after")
    def no_invention(self) -> Self:
        if self.evidence == "unreadable" and self.value is not None:
            raise ValueError("Unreadable values must be null")
        if self.verified and self.evidence != "visible_label":
            raise ValueError("Estimates cannot be verified labels")
        return self


class Axis(Model):
    label: Text
    unit: Text = ""
    scale: Literal["linear", "logarithmic", "categorical", "unknown"] = "unknown"


class Node(Model):
    node_id: str
    label: Text
    bbox: Box | None = None
    group: Text = ""
    kind: Literal["process", "decision", "component", "group", "unknown"] = "unknown"


class Edge(Model):
    source: str
    target: str
    label: Text = ""
    direction: Literal["directed", "undirected", "uncertain"] = "uncertain"
    visible: bool = False


class Visual(Model):
    object_id: str
    kind: Literal["image", "chart", "diagram"] = "image"
    title: Text = ""
    caption: Text = ""
    native_text: Text = ""
    # Native vector geometry is evidence for later geometric validation, not proof
    # that a particular chart series or arrow has been interpreted correctly.
    native_vector_paths: list[dict[str, object]] = Field(default_factory=list, max_length=1000)
    chart_type: Text = "unknown"
    visual_description: Text = ""
    ocr_text: Text = ""
    text_regions: list[OCRRegion] = Field(default_factory=list, max_length=2000)
    combined_text: Text = ""
    axes: list[Axis] = Field(default_factory=list, max_length=4)
    legend: list[Text] = Field(default_factory=list, max_length=100)
    values: list[ChartValue] = Field(default_factory=list, max_length=2000)
    nodes: list[Node] = Field(default_factory=list, max_length=2000)
    edges: list[Edge] = Field(default_factory=list, max_length=4000)
    sources: list[SourceRef] = Field(min_length=1)
    section_id: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    status: Status = Status.PARTIAL
    uncertainties: list[Text] = Field(default_factory=list, max_length=100)
    panel_status: Literal["unresolved", "separated", "verified_single"] = "unresolved"

    @model_validator(mode="after")
    def graph(self) -> Self:
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)) or any(
            edge.source not in ids or edge.target not in ids for edge in self.edges
        ):
            raise ValueError("Invalid diagram graph")
        if self.kind != "chart" and (self.values or self.axes):
            raise ValueError("Chart data on non-chart")
        if self.kind != "diagram" and (self.nodes or self.edges):
            raise ValueError("Diagram data on non-diagram")
        if self.status == Status.COMPLETED and (
            self.panel_status == "unresolved" or self.uncertainties
        ):
            raise ValueError("Uncertain visual cannot be complete")
        if self.status == Status.COMPLETED:
            if not (
                self.visual_description
                or self.ocr_text
                or self.native_text
                or self.values
                or self.nodes
            ):
                raise ValueError("Empty visual cannot be complete")
            if self.kind == "chart" and (
                not self.values or any(not value.verified for value in self.values)
            ):
                raise ValueError("Unverified chart cannot be complete")
            if self.kind == "diagram" and (
                not self.nodes or any(not edge.visible for edge in self.edges)
            ):
                raise ValueError("Unverified diagram cannot be complete")
        return self


ContentObject = Annotated[Paragraph | Table | Visual, Field(discriminator="kind")]


class NormalizedDocument(Model):
    schema_version: Literal["1"] = "1"
    pipeline_version: str = "0.3.0"
    document_id: str
    source_sha256: str
    config_fingerprint: str
    title: Text = ""
    pages: list[Page]
    sections: list[Section] = Field(default_factory=list)
    objects: list[ContentObject] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    status: Status = Status.COMPLETED
    trace_id: str
    elapsed_seconds: float = Field(default=0, ge=0)
    peak_rss_bytes: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def source_integrity(self) -> Self:
        if [page.page_number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("Pages must be consecutive and ordered")
        identities = [item.object_id for item in self.objects]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate object identities")
        for item in self.objects:
            for ref in item.sources:
                if ref.page_number > len(self.pages):
                    raise ValueError("Unknown source page")
                page = self.pages[ref.page_number - 1]
                if ref.bbox.x1 > page.width + 0.1 or ref.bbox.y1 > page.height + 0.1:
                    raise ValueError("Source outside original page")
        return self


class Chunk(Model):
    chunk_id: str
    document_id: str
    source_object_ids: list[str] = Field(min_length=1)
    kind: Literal["text", "table", "chart", "diagram", "image"]
    text: str
    token_count: int = Field(ge=1)
    tokenizer: str
    heading_path: list[str]
    sources: list[SourceRef] = Field(min_length=1)
    paragraph_ids: list[str] = Field(default_factory=list)
    sentence_ids: list[str] = Field(default_factory=list)
    row_start: int | None = None
    row_end: int | None = None
    fragment: bool = False


class IngestResult(Model):
    document: NormalizedDocument
    chunks: list[Chunk]
