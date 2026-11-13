"""Provider-neutral, strict hosted region schema. No application imports."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(max_length=8000)]


class LayoutModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DiagramNode(LayoutModel):
    node_id: Text
    label: Text
    kind: Literal["process", "decision", "component", "group", "unknown"] = "unknown"
    bbox: list[float] = Field(default_factory=list, max_length=4)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DiagramEdge(LayoutModel):
    source: Text
    target: Text
    label: Text = ""
    direction: Literal["directed", "undirected", "uncertain"] = "uncertain"
    confidence: float | None = Field(default=None, ge=0, le=1)


class TableCell(LayoutModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    text: Text
    header: bool = False


class TextRegion(LayoutModel):
    text: Text
    bbox: list[float] = Field(min_length=4, max_length=4)
    language: Text = "und"
    style: Literal["printed", "handwritten", "unknown"] = "unknown"
    confidence: float | None = Field(default=None, ge=0, le=1)


class ChartPoint(LayoutModel):
    label: Text
    value: Text | None
    readability: Literal["readable", "approximate", "unreadable"]


class ChartSeries(LayoutModel):
    name: Text
    points: list[ChartPoint] = Field(max_length=200)


class ChartAxis(LayoutModel):
    label: Text
    unit: Text
    scale: Literal["linear", "logarithmic", "categorical", "unknown"]


class Region(LayoutModel):
    kind: Literal["text", "image", "table", "chart", "diagram"]
    bbox: list[float] = Field(min_length=4, max_length=4)
    visible_text: Text = ""
    description: Text = ""
    title: Text = ""
    caption: Text = ""
    headers: list[Text] = Field(default_factory=list, max_length=64)
    rows: list[list[Text]] = Field(default_factory=list, max_length=1000)
    column_edges: list[float] = Field(default_factory=list, max_length=65)
    units: list[Text] = Field(default_factory=list, max_length=64)
    axes: list[ChartAxis] = Field(default_factory=list, max_length=3)
    legend: list[Text] = Field(default_factory=list, max_length=100)
    series: list[ChartSeries] = Field(default_factory=list, max_length=32)
    chart_type: Text = "unknown"
    nodes: list[DiagramNode] = Field(default_factory=list, max_length=512)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=1024)
    cells: list[TableCell] = Field(default_factory=list, max_length=10_000)
    text_regions: list[TextRegion] = Field(default_factory=list, max_length=512)
    uncertainties: list[Text] = Field(default_factory=list, max_length=32)
    panel_status: Literal["single", "separated", "unresolved"] = "unresolved"

    @model_validator(mode="after")
    def validate_region(self) -> Self:
        if (
            not all(0 <= value <= 1000 for value in self.bbox)
            or self.bbox[0] >= self.bbox[2]
            or self.bbox[1] >= self.bbox[3]
        ):
            raise ValueError("Invalid normalized layout rectangle")
        if self.kind == "table" and (
            not self.headers
            or not self.rows
            or not all(self.headers)
            or len(set(self.headers)) != len(self.headers)
            or any(len(row) != len(self.headers) for row in self.rows)
        ):
            raise ValueError("Invalid rectangular table")
        if self.column_edges and (
            self.kind != "table"
            or len(self.column_edges) != len(self.headers) + 1
            or any(not self.bbox[0] <= value <= self.bbox[2] for value in self.column_edges)
            or any(
                right <= left
                for left, right in zip(self.column_edges, self.column_edges[1:], strict=False)
            )
        ):
            raise ValueError("Invalid table column geometry")
        if self.kind == "chart" and not (
            self.title or self.axes or self.series or self.visible_text
        ):
            raise ValueError("Chart contains no readable source information")
        if self.kind == "text" and not self.visible_text.strip():
            raise ValueError("Text region contains no visible text")
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)) or any(
            edge.source not in ids or edge.target not in ids for edge in self.edges
        ):
            raise ValueError("Invalid diagram relationships")
        if self.kind == "diagram" and not self.nodes:
            raise ValueError("Diagram requires visible nodes")
        if self.kind != "diagram" and (self.nodes or self.edges):
            raise ValueError("Diagram data on another content type")
        if any(
            cell.row + cell.row_span > len(self.rows) + 1
            or cell.column + cell.column_span > len(self.headers)
            for cell in self.cells
        ):
            raise ValueError("Table cell outside grid")
        for box in [node.bbox for node in self.nodes if node.bbox] + [
            entry.bbox for entry in self.text_regions
        ]:
            if (
                len(box) != 4
                or not all(0 <= value <= 1000 for value in box)
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                raise ValueError("Invalid child geometry")
            if (
                box[0] < self.bbox[0]
                or box[1] < self.bbox[1]
                or box[2] > self.bbox[2]
                or box[3] > self.bbox[3]
            ):
                raise ValueError("Child geometry outside region")
        return self


class PageLayout(LayoutModel):
    regions: list[Region] = Field(max_length=32)

    @model_validator(mode="after")
    def reject_duplicate_regions(self) -> Self:
        for index, region in enumerate(self.regions):
            a = region.bbox
            for other in self.regions[index + 1 :]:
                b = other.bbox
                overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
                    0, min(a[3], b[3]) - max(a[1], b[1])
                )
                if (
                    overlap
                    > min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) * 0.1
                ):
                    raise ValueError("Overlapping layout regions duplicate evidence")
        return self
