"""Bounded subprocess OCR; no Python ML runtime or model weights loaded into the API."""

import csv
import shutil
import subprocess
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from .errors import CapabilityUnavailable, IngestError, ResourceLimit
from .models import Box, OCRRegion, SourceRef


class TesseractOCRProvider:
    def __init__(
        self, language: str = "eng", *, timeout_seconds: float = 15, psm: int = 11
    ) -> None:
        if not language.replace("+", "").replace("_", "").isalnum():
            raise ValueError("Invalid OCR language")
        if not 0 <= psm <= 13:
            raise ValueError("Invalid Tesseract PSM")
        executable = shutil.which("tesseract")
        if not executable:
            raise CapabilityUnavailable("tesseract_executable_unavailable")
        self.executable, self.language, self.timeout, self.psm = (
            executable,
            language,
            timeout_seconds,
            psm,
        )
        self.name = "tesseract:" + language

    def recognize(self, image: bytes, source: SourceRef) -> list[OCRRegion]:
        if len(image) > 8 * 1024 * 1024:
            raise ResourceLimit("ocr_image_budget")
        with Image.open(BytesIO(image)) as picture:
            width, height = picture.size
            if width * height > 16_000_000:
                raise ResourceLimit("ocr_image_budget")
            ocr_image = image
            if width * height > 4_000_000:
                scale = (4_000_000 / (width * height)) ** 0.5
                resized = picture.convert("RGB").resize(
                    (max(1, int(width * scale)), max(1, int(height * scale)))
                )
                width, height = resized.size
                buf = BytesIO()
                resized.save(buf, format="PNG")
                ocr_image = buf.getvalue()
        with TemporaryDirectory(prefix="ss-ocr-") as directory:
            path = Path(directory) / "source.png"
            path.write_bytes(ocr_image)
            try:
                # output file avoids buffering arbitrary OCR output in subprocess pipes.
                subprocess.run(
                    [
                        self.executable,
                        str(path),
                        str(Path(directory) / "ocr"),
                        "-l",
                        self.language,
                        "--psm",
                        str(self.psm),
                        "tsv",
                    ],
                    timeout=self.timeout,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env={"PATH": str(Path(self.executable).parent), "OMP_THREAD_LIMIT": "1"},
                )
            except subprocess.TimeoutExpired:
                raise IngestError("ocr_timeout", retryable=True) from None
            except subprocess.CalledProcessError:
                raise CapabilityUnavailable("ocr_language_or_engine_unavailable") from None
            output = Path(directory) / "ocr.tsv"
            if output.stat().st_size > 1_000_000:
                raise ResourceLimit("ocr_response_budget")
            rows = csv.DictReader(StringIO(output.read_text()), delimiter="\t")
            regions = []
            for row in rows:
                if not row.get("text", "").strip() or float(row["conf"]) < 0:
                    continue
                x, y, w, h = (int(row[key]) for key in ("left", "top", "width", "height"))
                left, top, right, bottom = source.bbox.tuple()
                if w <= 0 or h <= 0 or not 0 <= x < x + w <= width or not 0 <= y < y + h <= height:
                    raise IngestError("invalid_ocr_coordinates")
                regions.append(
                    OCRRegion(
                        text=row["text"],
                        language=self.language,
                        recognition_model=self.name,
                        confidence=max(0, min(1, float(row["conf"]) / 100)),
                        bbox=Box(
                            x0=left + x / width * (right - left),
                            y0=top + y / height * (bottom - top),
                            x1=left + (x + w) / width * (right - left),
                            y1=top + (y + h) / height * (bottom - top),
                        ),
                    )
                )
            return regions
