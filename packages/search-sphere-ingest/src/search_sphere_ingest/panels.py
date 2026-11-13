"""Conservative panel proposals. Geometry is evidence, never proof of semantic separation."""

from io import BytesIO

from PIL import Image

from .errors import CapabilityUnavailable
from .models import Box


def propose_panels(image: bytes) -> list[Box]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise CapabilityUnavailable("opencv_extra_required") from None
    with Image.open(BytesIO(image)) as original:
        # Propose in a bounded image, then map every box back to original pixels.
        original_width, original_height = original.size
        original.thumbnail((1400, 1400))
        rgb = np.asarray(original.convert("RGB"))
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    foreground = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    joined = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, np.ones((5, 9), dtype=np.uint8))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # Inspect boundaries/axes as auxiliary evidence, without assuming axes equal panels.
    cv2.HoughLinesP(
        joined, 1, np.pi / 180, threshold=50, minLineLength=max(20, width // 5), maxLineGap=8
    )
    boxes = [cv2.boundingRect(contour) for contour in contours]
    significant = [(x, y, w, h) for x, y, w, h in boxes if w * h > width * height * 0.04]
    if not 2 <= len(significant) <= 8:
        return []
    # Keep all disconnected labels/legends by splitting only a full-height whitespace gutter.
    projection = (foreground > 0).sum(axis=0)
    middle = [x for x in range(width // 4, width * 3 // 4) if projection[x] == 0]
    runs: list[list[int]] = []
    for x in middle:
        if not runs or x != runs[-1][-1] + 1:
            runs.append([x])
        else:
            runs[-1].append(x)
    if not runs or len(max(runs, key=len)) < max(12, width // 25):
        return []
    gutter = max(runs, key=len)
    split = (gutter[0] + gutter[-1]) // 2
    if not any(x + w < split for x, _, w, _ in significant) or not any(
        x > split for x, _, w, _ in significant
    ):
        return []
    boundary = split * original_width / width
    return [
        Box(x0=0, y0=0, x1=boundary, y1=original_height),
        Box(x0=boundary, y0=0, x1=original_width, y1=original_height),
    ]
