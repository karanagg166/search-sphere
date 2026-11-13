"""Route visual crops once, preserve parents, and retain unavailable content honestly."""

from io import BytesIO

from PIL import Image

from .errors import IngestError
from .models import Box, Issue, SourceRef, Status, Visual, stable_id
from .panels import propose_panels
from .providers import AssetStore, OCRProvider, VisionProvider, merge_image_evidence


class RegionRouter:
    def __init__(
        self,
        vision: VisionProvider | None,
        ocr: OCRProvider | None,
        assets: AssetStore | None,
        use_opencv: bool,
    ) -> None:
        self.vision, self.ocr, self.assets, self.use_opencv = vision, ocr, assets, use_opencv

    def route(self, crop: bytes, source: SourceRef) -> tuple[list[Visual], list[Issue]]:
        issues = []
        source = source.model_copy(
            update={"source_image": stable_id(source.region_id, "original_crop")}
        )
        if self.assets:
            self.assets.put(source.source_image or "", crop, "image/png")
        else:
            issues.append(
                Issue(
                    code="crop_not_persisted_use_original_pdf",
                    stage="storage",
                    region_id=source.region_id,
                    page_number=source.page_number,
                )
            )
        panels = propose_panels(crop) if self.use_opencv else []
        if panels:
            # Keep parent plus children: a gutter could separate a title from its chart.
            parent = Visual(
                object_id=stable_id(source.region_id, "image"),
                sources=[source],
                uncertainties=["panel_proposals_require_review"],
                panel_status="separated",
            )
            output = [parent]
            with Image.open(BytesIO(crop)) as image:
                width, height = image.size
                for index, box in enumerate(panels):
                    data = BytesIO()
                    image.crop(box.tuple()).save(data, format="PNG")
                    x, y, right, bottom = source.bbox.tuple()
                    ref = source.model_copy(
                        update={
                            "region_id": stable_id(source.region_id, index),
                            "parent_region_id": source.region_id,
                            "bbox": Box(
                                x0=x + box.x0 / width * (right - x),
                                y0=y + box.y0 / height * (bottom - y),
                                x1=x + box.x1 / width * (right - x),
                                y1=y + box.y1 / height * (bottom - y),
                            ),
                            # Child links to retained parent crop; bbox identifies its original location.
                            "evidence": source.evidence + ["opencv_whitespace_panel_proposal"],
                        }
                    )
                    child, failures = self._understand(data.getvalue(), ref)
                    output.append(child)
                    issues.extend(failures)
            return output, issues
        visual, failures = self._understand(crop, source)
        return [visual], issues + failures

    def _understand(self, crop: bytes, ref: SourceRef) -> tuple[Visual, list[Issue]]:
        issues = []
        visual = Visual(object_id=stable_id(ref.region_id, "image"), sources=[ref])
        if self.vision:
            try:
                visual = self.vision.understand(crop, ref)
                ref = ref.model_copy(
                    update={
                        "provider": self.vision.name,
                        "model": visual.sources[0].model,
                        "evidence": ref.evidence + ["unverified_visual_interpretation"],
                    }
                )
                # Provider-supplied IDs and source references can never redirect ownership.
                visual = Visual.model_validate(
                    {
                        **visual.model_dump(),
                        "object_id": stable_id(ref.region_id, visual.kind),
                        "sources": [ref],
                    }
                )
            except IngestError as error:
                issues.append(
                    Issue(
                        code=error.code,
                        stage="vision",
                        page_number=ref.page_number,
                        region_id=ref.region_id,
                        retryable=error.retryable,
                    )
                )
        else:
            issues.append(
                Issue(
                    code="vision_provider_unavailable",
                    stage="vision",
                    page_number=ref.page_number,
                    region_id=ref.region_id,
                )
            )
        if self.ocr:
            try:
                regions = self.ocr.recognize(crop, ref)
                if any(
                    item.bbox.x0 < ref.bbox.x0
                    or item.bbox.y0 < ref.bbox.y0
                    or item.bbox.x1 > ref.bbox.x1
                    or item.bbox.y1 > ref.bbox.y1
                    for item in regions
                ):
                    raise IngestError("invalid_ocr_coordinates")
                visual = merge_image_evidence(visual, regions)
            except IngestError as error:
                issues.append(
                    Issue(
                        code=error.code,
                        stage="ocr",
                        page_number=ref.page_number,
                        region_id=ref.region_id,
                        retryable=error.retryable,
                    )
                )
        # Geometric separation alone cannot certify a chart or graph as complete.
        if visual.panel_status == "unresolved":
            visual.status = Status.PARTIAL
        return visual, issues
