"""Deterministic whitespace partitions for native single/multi-column PDF blocks."""


def _gaps(blocks, axis):
    intervals = sorted((float(b["bbox"][axis]), float(b["bbox"][axis + 2])) for b in blocks)
    occupied_end = intervals[0][1]
    gaps = []
    for start, end in intervals[1:]:
        if start > occupied_end:
            gaps.append((occupied_end, start))
        occupied_end = max(occupied_end, end)
    return gaps


def split_text_columns(block):
    """PyMuPDF can place disconnected columns in a single text block."""
    lines = block.get("lines", [])
    if block.get("type") != 0 or len(lines) < 2:
        return [block]
    indexed = [{**line, "source_line_index": index} for index, line in enumerate(lines)]

    def partition(group, depth=0):
        if depth >= 16:
            return [group]
        gutters = [gap for gap in _gaps(group, 0) if gap[1] - gap[0] >= 12]
        if not gutters:
            return [group]
        split = sum(max(gutters, key=lambda gap: gap[1] - gap[0])) / 2
        left = [line for line in group if line["bbox"][2] <= split]
        right = [line for line in group if line["bbox"][0] >= split]
        overlap = min(max(line["bbox"][3] for line in left), max(line["bbox"][3] for line in right)) - max(
            min(line["bbox"][1] for line in left), min(line["bbox"][1] for line in right),
        )
        if overlap <= 0:
            return [group]  # Sequential indented lines are not columns.
        return partition(left, depth + 1) + partition(right, depth + 1)

    groups = partition(indexed)
    if len(groups) == 1:
        return [block]
    return [{
        **block, "lines": sorted(group, key=lambda line: (line["bbox"][1], line["bbox"][0])),
        "bbox": (min(line["bbox"][0] for line in group), min(line["bbox"][1] for line in group),
                 max(line["bbox"][2] for line in group), max(line["bbox"][3] for line in group)),
        "source_part": part, "source_line_indices": [line["source_line_index"] for line in group],
    } for part, group in enumerate(groups)]


def reading_order(blocks, page_width, depth=0):
    """Preserve PDF order separately; infer reading order only from coordinates.

    Wide headers/footers partition bands before column detection. Within each
    band, a shared vertical gutter takes precedence over paragraph/line gaps.
    Overlapping or complex layouts retain a conservative visual ordering.
    """
    if len(blocks) <= 1:
        return list(blocks)
    if depth >= 16:
        return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    left = min(b["bbox"][0] for b in blocks)
    right = max(b["bbox"][2] for b in blocks)
    width = max(right - left, page_width * 0.5)
    for wide in sorted(blocks, key=lambda b: b["bbox"][1]):
        if wide["bbox"][2] - wide["bbox"][0] < width * 0.7:
            continue
        above = [b for b in blocks if b is not wide and b["bbox"][3] <= wide["bbox"][1]]
        below = [b for b in blocks if b is not wide and b["bbox"][1] >= wide["bbox"][3]]
        if len(above) + len(below) == len(blocks) - 1:
            return reading_order(above, page_width, depth + 1) + [wide] + reading_order(below, page_width, depth + 1)
    vertical = [gap for gap in _gaps(blocks, 0) if gap[1] - gap[0] >= max(12, width * 0.025)]
    if vertical:
        split = sum(max(vertical, key=lambda gap: gap[1] - gap[0])) / 2
        before = [b for b in blocks if b["bbox"][2] <= split]
        after = [b for b in blocks if b["bbox"][0] >= split]
        return reading_order(before, page_width, depth + 1) + reading_order(after, page_width, depth + 1)
    horizontal = _gaps(blocks, 1)
    if horizontal:
        split = sum(horizontal[0]) / 2
        before = [b for b in blocks if b["bbox"][3] <= split]
        after = [b for b in blocks if b["bbox"][1] >= split]
        return reading_order(before, page_width, depth + 1) + reading_order(after, page_width, depth + 1)
    return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
