"""Deterministic crops for scans and vector/image regions, excluding native tables."""

import pymupdf


def visual_regions(page, blocks, tables):
    native = [pymupdf.Rect(table["bbox"]) for table in tables]
    candidates = [pymupdf.Rect(block["bbox"]) for block in blocks if block.get("type") == 1]
    candidates += [pymupdf.Rect(rect) + (-24, -32, 24, 24) for rect in page.cluster_drawings()]
    full = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    regions = []
    for rect in candidates:
        rect &= full
        if rect.width < 60 or rect.height < 40 or rect.get_area() < 4000:
            continue
        if any((rect & table).get_area() >= min(rect.get_area(), table.get_area()) * 0.8 for table in native):
            continue
        for prior in regions:
            if prior.intersects(rect):
                prior |= rect
                break
        else:
            regions.append(rect)
    # Repeat merging because one region may bridge two previous clusters.
    changed = True
    while changed:
        changed = False
        for index, rect in enumerate(regions):
            for other in regions[index + 1:]:
                if rect.intersects(other):
                    rect |= other
                    regions.remove(other)
                    changed = True
                    break
            if changed:
                break
    return sorted(regions, key=lambda rect: (rect.y0, rect.x0))


def render_region(page, rect):
    """Bound raster dimensions and normalize PDF rotation without changing provenance."""
    rotation = page.rotation
    try:
        page.set_rotation(0)
        scale = min(2.0, 1600 / max(rect.width, rect.height))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, colorspace=pymupdf.csRGB, alpha=False)
        data = pixmap.tobytes("png")
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("Layout crop exceeds the hosted image limit")
        return data
    finally:
        page.set_rotation(rotation)
