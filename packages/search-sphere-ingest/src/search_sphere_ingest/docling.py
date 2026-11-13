"""Opt-in Docling/TableFormer adapter for separately provisioned CPU environments.

Not imported by the native pipeline; never loads a local generative model.
"""

import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from .errors import CapabilityUnavailable, IngestError
from .models import Cell, Issue, NormalizedDocument, Page, Status, Table, Visual, stable_id
from .native import source_ref
from .text import Hierarchy


class DoclingLayoutProvider:
    name = "docling:tableformer-accurate"

    def __init__(
        self, *, artifacts_path: Path, memory_budget_mb: int, enable_ocr: bool = False
    ) -> None:
        if memory_budget_mb < 2048:
            raise CapabilityUnavailable("docling_memory_not_approved_for_free_render")
        if not artifacts_path.is_dir():
            raise CapabilityUnavailable("docling_prefetched_artifacts_required")
        self.artifacts_path, self.enable_ocr = artifacts_path, enable_ocr

    def parse(self, content: bytes, pages: list[Page], checksum: str) -> NormalizedDocument:
        try:
            from docling.datamodel.base_models import DocumentStream, InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError:
            raise CapabilityUnavailable("docling_extra_required") from None
        options = PdfPipelineOptions(artifacts_path=self.artifacts_path)
        options.do_ocr = self.enable_ocr
        options.do_table_structure = True
        options.table_structure_options.mode = TableFormerMode.ACCURATE
        options.do_picture_classification = True
        options.do_picture_description = False
        options.generate_page_images = False
        options.generate_picture_images = False
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        try:
            conversion = converter.convert(
                DocumentStream(name="source.pdf", stream=BytesIO(content)),
                max_num_pages=len(pages),
                max_file_size=len(content),
            )
        except Exception:
            raise IngestError("docling_conversion_failed") from None
        return self.normalize(conversion.document, pages, checksum, str(conversion.status))

    def normalize(
        self, doc: Any, pages: list[Page], checksum: str, conversion_status: str = "success"
    ) -> NormalizedDocument:
        document = NormalizedDocument(
            document_id=checksum,
            source_sha256=checksum,
            config_fingerprint="",
            pages=pages,
            trace_id=uuid.uuid4().hex,
        )
        hierarchy = Hierarchy()
        for order, (item, level) in enumerate(doc.iterate_items()):
            if not getattr(item, "prov", None):
                continue
            prov = item.prov[0]
            info = pages[prov.page_no - 1]
            box = prov.bbox.to_top_left_origin(page_height=info.height)
            ref = source_ref(checksum, prov.page_no, (box.l, box.t, box.r, box.b), order, "docling")
            label = str(item.label.value)
            if label == "table":
                data = item.data
                if not 1 <= data.num_cols <= 64 or not 1 <= data.num_rows <= 10_000:
                    raise IngestError("docling_table_budget")
                grid = [[""] * data.num_cols for _ in range(data.num_rows)]
                cells = []
                for cell in data.table_cells:
                    row, column = cell.start_row_offset_idx, cell.start_col_offset_idx
                    grid[row][column] = cell.text
                    cell_ref = ref
                    if cell.bbox:
                        bounds = cell.bbox.to_top_left_origin(page_height=info.height)
                        cell_ref = source_ref(
                            checksum,
                            prov.page_no,
                            (bounds.l, bounds.t, bounds.r, bounds.b),
                            order,
                            "docling",
                        )
                    cells.append(
                        Cell(
                            row=row,
                            column=column,
                            text=cell.text,
                            header=cell.column_header,
                            row_span=cell.row_span,
                            column_span=cell.col_span,
                            source=cell_ref,
                        )
                    )
                # Grid retains covered cells as empty; cells preserve merged relationships.
                document.objects.append(
                    Table(
                        object_id=stable_id(ref.region_id, "table"),
                        headers=grid[0],
                        rows=grid[1:],
                        cells=cells,
                        row_pages=[prov.page_no] * (len(grid) - 1),
                        sources=[ref],
                        section_id=hierarchy.stack[-1].section_id if hierarchy.stack else None,
                        heading_path=[s.title for s in hierarchy.stack],
                    )
                )
            elif label == "picture":
                ref.evidence = ["docling_picture_classification_requires_panel_validation"]
                document.objects.append(
                    Visual(object_id=stable_id(ref.region_id, "image"), sources=[ref])
                )
            elif getattr(item, "text", "").strip():
                if label in {"section_header", "title"}:
                    hierarchy.heading(item.text, min(6, max(1, getattr(item, "level", level))), ref)
                document.objects.append(hierarchy.paragraph(item.text, ref))
        document.sections = hierarchy.sections
        if "success" not in conversion_status.lower():
            document.issues.append(Issue(code="docling_conversion_partial", stage="layout"))
        for page in pages:
            if page.selectable_characters < 10 and not self.enable_ocr:
                document.issues.append(
                    Issue(
                        code="docling_scan_requires_ocr", stage="ocr", page_number=page.page_number
                    )
                )
                # Even if an OCR-disabled pass omitted text regions, keep a full-page source.
                if not any(
                    isinstance(item, Visual) and item.sources[0].page_number == page.page_number
                    for item in document.objects
                ):
                    ref = source_ref(
                        checksum,
                        page.page_number,
                        (0, 0, page.width, page.height),
                        len(document.objects),
                        "docling-scan-fallback",
                    )
                    document.objects.append(
                        Visual(object_id=stable_id(ref.region_id, "image"), sources=[ref])
                    )
        if document.issues:
            document.status = Status.PARTIAL
        return document
