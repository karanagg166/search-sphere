"""Public extraction/chunking facade; no application or persistence imports."""

import asyncio
import logging
import re
import resource
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from .chunking import Chunker, TiktokenTokenizer
from .config import PipelineConfig
from .errors import IngestError, ResourceLimit
from .models import (
    Chunk,
    ContentObject,
    IngestResult,
    Issue,
    NormalizedDocument,
    Status,
    Table,
    Visual,
    stable_id,
)
from .native import native_tables, render, source_ref, text_blocks, vector_data, visual_boxes
from .preparation import inspect_pdf, load_pdf
from .providers import (
    AssetStore,
    LayoutProvider,
    OCRProvider,
    ProgressSink,
    Tokenizer,
    VisionProvider,
)
from .reading_order import reading_order, split_text_columns
from .routing import RegionRouter
from .tables import merge_continuations
from .text import Hierarchy

logger = logging.getLogger("search_sphere_ingest")


class DocumentPipeline:
    def __init__(
        self,
        config: PipelineConfig | None = None,
        *,
        tokenizer: Tokenizer | None = None,
        vision: VisionProvider | None = None,
        ocr: OCRProvider | None = None,
        layout: LayoutProvider | None = None,
        assets: AssetStore | None = None,
        progress: ProgressSink | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.tokenizer = tokenizer or TiktokenTokenizer()
        self.layout = layout
        self.router = RegionRouter(vision, ocr, assets, self.config.use_opencv)
        self.progress = progress

    def extract(self, source: str | Path | bytes) -> NormalizedDocument:
        if (
            isinstance(source, bytes) and source.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF"))
        ) or (
            not isinstance(source, bytes)
            and Path(source).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ):
            return self.extract_image(source)
        started = time.monotonic()
        data = load_pdf(source, self.config)
        prep_issues: list[Issue] = []
        pdf, pages, checksum = inspect_pdf(data, self.config, issues=prep_issues)
        fingerprint = stable_id(
            self.config.model_dump(),
            getattr(self.layout, "name", None),
            getattr(self.router.vision, "name", None),
            getattr(self.router.ocr, "name", None),
        )
        document = NormalizedDocument(
            document_id=checksum,
            source_sha256=checksum,
            config_fingerprint=fingerprint,
            title=str((pdf.metadata or {}).get("title", "")),
            pages=pages,
            issues=prep_issues,
            trace_id=uuid.uuid4().hex,
        )
        try:
            if self.layout:
                document = self.layout.parse(data, pages, checksum)
                document.config_fingerprint = fingerprint
                document = self._route_layout(pdf, document, started)
            else:
                document = self._native(pdf, document, started)
            document.objects = cast(
                list[ContentObject],
                merge_continuations(
                    list(document.objects), pages, self.config.continuation_threshold
                ),
            )
            if (
                len(document.objects) > self.config.max_objects
                or len(document.model_dump_json().encode()) > self.config.max_content_bytes
            ):
                raise ResourceLimit("normalized_document_budget")
            for page in document.pages:
                if any(issue.page_number == page.page_number for issue in document.issues) or any(
                    getattr(item, "status", Status.COMPLETED) != Status.COMPLETED
                    and any(ref.page_number == page.page_number for ref in item.sources)
                    for item in document.objects
                ):
                    page.status = Status.PARTIAL
            if document.issues or any(page.status != Status.COMPLETED for page in document.pages):
                document.status = Status.PARTIAL
            if self.config.require_complete and document.status != Status.COMPLETED:
                raise IngestError("partial_extraction_requires_review")
            document.elapsed_seconds = time.monotonic() - started
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            document.peak_rss_bytes = int(rss * (1 if sys.platform == "darwin" else 1024))
            logger.info(
                "extraction_finished",
                extra={
                    "trace_id": document.trace_id,
                    "pages": len(pages),
                    "objects": len(document.objects),
                    "status": document.status,
                    "elapsed_seconds": document.elapsed_seconds,
                    "peak_rss_bytes": document.peak_rss_bytes,
                },
            )
            return NormalizedDocument.model_validate(document.model_dump())
        except ValidationError:
            raise IngestError("invalid_extraction_schema") from None
        finally:
            pdf.close()

    def _check(self, started: float, stage: str, page: int, region: str | None = None) -> None:
        if time.monotonic() - started > self.config.timeout_seconds:
            raise IngestError("processing_timeout", retryable=True)
        if self.progress:
            self.progress(stage, page, region)

    def _native(self, pdf: Any, document: NormalizedDocument, started: float) -> NormalizedDocument:
        hierarchy = Hierarchy()
        crop_total = visual_count = 0
        for page, info in zip(pdf, document.pages, strict=True):
            self._check(started, "layout", info.page_number)
            tables = native_tables(page, info, document.source_sha256)
            blocks, baseline = text_blocks(page, tables)
            boxes = visual_boxes(page, tables, info.selectable_characters == 0)
            # Keep labels that overlap visuals as native evidence; do not send text to an LLM.
            candidates: list[tuple[float, float, str, Any]] = [
                (block["bbox"][1], block["bbox"][0], "text", block) for block in blocks
            ]
            candidates += [
                (table.sources[0].bbox.y0, table.sources[0].bbox.x0, "table", table)
                for table in tables
            ]
            candidates += [(box[1], box[0], "visual", box) for box in boxes]
            ordered: list[dict[str, Any]] = []
            for _, _, kind, value in candidates:
                if kind == "text":
                    ordered.extend(
                        {"kind": kind, "value": part, "bbox": part["bbox"]}
                        for part in split_text_columns(value)
                    )
                else:
                    ordered.append(
                        {
                            "kind": kind,
                            "value": value,
                            "bbox": value.sources[0].bbox.tuple() if kind == "table" else value,
                        }
                    )
            for order, region in enumerate(reading_order(ordered, info.width)):
                kind, value = region["kind"], region["value"]
                self._check(started, "region", info.page_number)
                if len(document.objects) >= self.config.max_objects:
                    raise ResourceLimit("object_budget")
                if kind == "text":
                    text = "\n".join(
                        "".join(span["text"] for span in line["spans"]) for line in value["lines"]
                    ).strip()
                    if not text:
                        continue
                    ref = source_ref(
                        document.source_sha256, info.page_number, tuple(value["bbox"]), order
                    )
                    size = max(span["size"] for line in value["lines"] for span in line["spans"])
                    spans = [
                        span
                        for line in value["lines"]
                        for span in line["spans"]
                        if span["text"].strip()
                    ]
                    all_bold = bool(spans) and all(
                        bool(span.get("flags", 0) & 16) for span in spans
                    )
                    numbered = re.match(r"^(\d+(?:\.\d+)*)\.?\s+\S", text)
                    is_bold_heading = (
                        all_bold
                        and len(text) < 120
                        and len(value["lines"]) <= 2
                        and size >= baseline * 0.98
                        and not text.endswith((".", ",", ";"))
                    )
                    if (
                        len(text) < 180 and (size >= baseline * 1.2 or numbered)
                    ) or is_bold_heading:
                        level = (
                            min(6, numbered.group(1).count(".") + 1)
                            if numbered
                            else (1 if size >= baseline * 1.5 else 2)
                        )
                        ref.evidence = ["heuristic_heading_requires_review"]
                        hierarchy.heading(text, level, ref)
                        if not document.title and level == 1:
                            document.title = text
                    document.objects.append(hierarchy.paragraph(text, ref))
                elif kind == "table":
                    table: Table = value
                    table.heading_path = [section.title for section in hierarchy.stack]
                    table.section_id = hierarchy.stack[-1].section_id if hierarchy.stack else None
                    table.sources[0].reading_order = order
                    document.objects.append(table)
                else:
                    visual_count += 1
                    if visual_count > self.config.max_visual_regions:
                        raise ResourceLimit("visual_region_budget")
                    ref = source_ref(document.source_sha256, info.page_number, value, order)
                    crop = render(page, ref.bbox, self.config.max_render_side)
                    crop_total += len(crop)
                    if crop_total > self.config.max_crop_bytes:
                        raise ResourceLimit("crop_byte_budget")
                    visuals, issues = self.router.route(crop, ref)
                    for visual in visuals:
                        visual.heading_path = [section.title for section in hierarchy.stack]
                        visual.section_id = (
                            hierarchy.stack[-1].section_id if hierarchy.stack else None
                        )
                        visual.native_text = page.get_textbox(
                            visual.sources[0].bbox.tuple()
                        ).strip()
                        x0, y0, x1, y1 = visual.sources[0].bbox.tuple()
                        nearby = page.get_textbox(
                            (
                                max(0, x0 - 8),
                                max(0, y0 - 40),
                                min(info.width, x1 + 8),
                                min(info.height, y1 + 40),
                            )
                        )
                        visual.caption = "\n".join(
                            line.strip()
                            for line in nearby.splitlines()
                            if re.match(r"^(?:Figure|Fig\.|Chart|Diagram)\s+\w", line.strip(), re.I)
                        )
                        paths: list[dict[str, object]] = []
                        truncated_paths = False
                        for path in page.get_drawings():
                            bounds = path["rect"]
                            if bounds.x1 < x0 or bounds.x0 > x1 or bounds.y1 < y0 or bounds.y0 > y1:
                                continue
                            if len(paths) >= 1000:
                                truncated_paths = True
                                break
                            paths.append(
                                {
                                    "bbox": list(bounds),
                                    "type": path["type"],
                                    "items": [vector_data(segment) for segment in path["items"]],
                                }
                            )
                        visual.native_vector_paths = paths
                        if (
                            truncated_paths
                            and "native_vector_paths_truncated" not in visual.uncertainties
                        ):
                            visual.uncertainties.append("native_vector_paths_truncated")
                    document.objects.extend(visuals)
                    document.issues.extend(issues)
            if info.selectable_characters == 0 and not boxes:
                document.issues.append(
                    Issue(code="scan_without_regions", stage="layout", page_number=info.page_number)
                )
        document.sections = hierarchy.sections
        return document

    def _route_layout(
        self, pdf: Any, document: NormalizedDocument, started: float
    ) -> NormalizedDocument:
        output: list[ContentObject] = []
        total = count = 0
        for item in document.objects:
            if not isinstance(item, Visual):
                output.append(item)
                continue
            ref = item.sources[0]
            self._check(started, "region", ref.page_number, ref.region_id)
            count += 1
            if count > self.config.max_visual_regions:
                raise ResourceLimit("visual_region_budget")
            crop = render(pdf[ref.page_number - 1], ref.bbox, self.config.max_render_side)
            total += len(crop)
            if total > self.config.max_crop_bytes:
                raise ResourceLimit("crop_byte_budget")
            visuals, issues = self.router.route(crop, ref)
            output.extend(visuals)
            document.issues.extend(issues)
        document.objects = output
        return document

    def chunk(self, document: NormalizedDocument) -> list[Chunk]:
        return Chunker(self.config, self.tokenizer).chunk(document)

    def extract_image(self, source: str | Path | bytes) -> NormalizedDocument:
        from .images import extract_image

        return extract_image(self, source)

    async def aextract_image(self, source: str | Path | bytes) -> NormalizedDocument:
        return await asyncio.to_thread(self.extract_image, source)

    def ingest(self, source: str | Path | bytes) -> IngestResult:
        document = self.extract(source)
        return IngestResult(document=document, chunks=self.chunk(document))

    async def aextract(self, source: str | Path | bytes) -> NormalizedDocument:
        # Cancellation stops awaiting, not a native parser thread. Hosts must isolate
        # untrusted work in an OS-limited process for a hard memory/time boundary.
        return await asyncio.to_thread(self.extract, source)

    async def aingest(self, source: str | Path | bytes) -> IngestResult:
        return await asyncio.to_thread(self.ingest, source)
