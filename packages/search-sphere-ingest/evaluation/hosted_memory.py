"""CPU/memory fault fixture only; synthetic transport is not live inference evidence."""

import json
import resource
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw
from src.main import app
from src.processing.extraction.hosted_layout import HostedLayoutRecognizer

assert "/health" in app.openapi()["paths"]

with Image.new("RGB", (4000, 4000), "white") as image:
    draw = ImageDraw.Draw(image)
    draw.rectangle((200, 400, 1600, 3500), outline="black", width=20)
    draw.rectangle((2400, 400, 3800, 3500), outline="black", width=20)
    stream = BytesIO()
    image.save(stream, format="PNG")
raw = json.dumps(
    {
        "regions": [
            {
                "kind": "image",
                "bbox": [0, 0, 1000, 1000],
                "description": "Synthetic two panels",
                "panel_status": "unresolved",
            }
        ]
    }
)
recognizer = HostedLayoutRecognizer(SimpleNamespace(inspect_image=lambda *args, **kwargs: raw))
blocks = recognizer.recognize(
    stream.getvalue(),
    page_number=1,
    bbox=[0, 0, 4000, 4000],
    source_hash="a" * 64,
    original_order=0,
)
assert len(blocks) == 2 and blocks[0].structured["container_only"]
assert blocks[1].provenance["bbox"] == [0, 0, 4000, 4000]
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
assert peak < 480, peak
print(
    json.dumps(
        {
            "workload": "API imports plus real 16MP raster and CV proposals",
            "peak_rss_mib": round(peak, 1),
            "provider_transport": "synthetic; not live inference evidence",
        }
    )
)
