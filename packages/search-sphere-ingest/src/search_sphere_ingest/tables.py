"""Evidence-based table continuation; retain every original cell and segment."""

from difflib import SequenceMatcher

from .models import Page, Table


def continuation_score(before: Table, after: Table, pages: list[Page]) -> float:
    left, right = before.sources[-1], after.sources[0]
    if right.page_number != left.page_number + 1 or before.section_id != after.section_id:
        return 0
    p, q = pages[left.page_number - 1], pages[right.page_number - 1]
    if left.bbox.y1 / p.height < 0.72 or right.bbox.y0 / q.height > 0.25:
        return 0
    if (
        len(before.headers) != len(after.headers)
        or not before.column_edges
        or not after.column_edges
    ):
        return 0
    if len(before.column_edges) != len(after.column_edges) or any(
        abs(a / p.width - b / q.width) > 0.025
        for a, b in zip(before.column_edges, after.column_edges, strict=True)
    ):
        return 0
    a, b = (
        before.title.casefold().strip(),
        after.title.casefold().replace("(continued)", "").strip(),
    )
    if b and a != b:
        return 0
    header_similarity = SequenceMatcher(
        None, "|".join(before.headers).casefold(), "|".join(after.headers).casefold()
    ).ratio()
    # Geometry is required but insufficient: headers and section/caption context dominate.
    return 0.35 + 0.55 * header_similarity + (0.1 if a and b == a else 0)


def merge_continuations(objects: list[object], pages: list[Page], threshold: float) -> list[object]:
    output: list[object] = []
    previous: Table | None = None
    for item in objects:
        if not isinstance(item, Table):
            output.append(item)
            continue
        if previous is not None:
            score = continuation_score(previous, item, pages)
            if score >= threshold:
                offset = len(previous.rows)
                # Drop the repeated header cells; keep their segment via sources.
                cells = previous.cells + [
                    cell.model_copy(update={"row": cell.row + offset})
                    for cell in item.cells
                    if not cell.header
                ]
                merged = previous.model_copy(
                    update={
                        "rows": previous.rows + item.rows,
                        "row_pages": previous.row_pages + item.row_pages,
                        "cells": cells,
                        "sources": previous.sources + item.sources,
                        "continuation_confidence": score,
                        "column_edges": item.column_edges,
                    }
                )
                output[output.index(previous)] = merged
                previous = merged
                continue
        output.append(item)
        previous = item
    return output
