"""Conservative panel proposals. Geometry is evidence, never proof of semantic separation."""

from collections.abc import Callable
from io import BytesIO

from PIL import Image

from .errors import CapabilityUnavailable
from .models import Box


def _longest_zero_run(projection: "object", start: int, end: int, min_width: int) -> int | None:
    middle = [idx for idx in range(start, end) if projection[idx] == 0]  # type: ignore[index]
    runs: list[list[int]] = []
    for idx in middle:
        if not runs or idx != runs[-1][-1] + 1:
            runs.append([idx])
        else:
            runs[-1].append(idx)
    if not runs:
        return None
    best = max(runs, key=len)
    if len(best) < min_width:
        return None
    return (best[0] + best[-1]) // 2


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
    significant = [(x, y, w, h) for x, y, w, h in boxes if w * h > width * height * 0.02]
    if not 2 <= len(significant) <= 32:
        return []

    fg_mask = foreground > 0
    v_split = _longest_zero_run(
        fg_mask.sum(axis=0), width // 5, width * 4 // 5, max(12, width // 25)
    )
    h_split = _longest_zero_run(
        fg_mask.sum(axis=1), height // 5, height * 4 // 5, max(12, height // 25)
    )

    has_v = (
        v_split is not None
        and any(x + w < v_split for x, _, w, _ in significant)
        and any(x > v_split for x, _, _, _ in significant)
    )
    has_h = (
        h_split is not None
        and any(y + h < h_split for _, y, _, h in significant)
        and any(y > h_split for _, y, _, _ in significant)
    )

    if has_v and has_h and v_split is not None and h_split is not None:
        bx = v_split * original_width / width
        by = h_split * original_height / height
        quadrants: list[tuple[float, float, float, float, Callable[[int, int, int, int], bool]]] = [
            (0.0, 0.0, bx, by, lambda x, y, w, h: x + w < v_split and y + h < h_split),
            (
                bx,
                0.0,
                float(original_width),
                by,
                lambda x, y, w, h: x > v_split and y + h < h_split,
            ),
            (
                0.0,
                by,
                bx,
                float(original_height),
                lambda x, y, w, h: x + w < v_split and y > h_split,
            ),
            (
                bx,
                by,
                float(original_width),
                float(original_height),
                lambda x, y, w, h: x > v_split and y > h_split,
            ),
        ]
        populated = [
            Box(x0=x0, y0=y0, x1=x1, y1=y1)
            for x0, y0, x1, y1, pred in quadrants
            if any(pred(x, y, w, h) for x, y, w, h in significant)
        ]
        if len(populated) >= 2:
            return populated

    if has_v and v_split is not None:
        boundary = v_split * original_width / width
        return [
            Box(x0=0, y0=0, x1=boundary, y1=original_height),
            Box(x0=boundary, y0=0, x1=original_width, y1=original_height),
        ]

    if has_h and h_split is not None:
        boundary = h_split * original_height / height
        return [
            Box(x0=0, y0=0, x1=original_width, y1=boundary),
            Box(x0=0, y0=boundary, x1=original_width, y1=original_height),
        ]

    return []


def estimate_skew_degrees(image: bytes) -> float:
    """Estimate dominant text/document skew angle in [-45, 45] degrees using OpenCV."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise CapabilityUnavailable("opencv_extra_required") from None
    with Image.open(BytesIO(image)) as original:
        original.thumbnail((1400, 1400))
        rgb = np.asarray(original.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    fg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(fg > 0))
    if len(coords) < 100:
        return 0.0
    angle = float(cv2.minAreaRect(coords[:, ::-1])[-1])
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90
    return round(angle, 2)
