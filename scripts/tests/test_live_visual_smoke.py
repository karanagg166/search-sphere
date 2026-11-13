"""Real raster fixture plus deterministic metric accounting; no live model claim."""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_visual_smoke as visual


def test_visual_fixture_contains_actual_raster_labels_and_only_native_marker():
    content = visual.visual_pdf("PUBLIC-VISUAL-MARKER")
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        assert len(pdf) == 1
        assert len(pdf[0].get_images()) == 1
        assert pdf[0].get_text().strip() == "PUBLIC-VISUAL-MARKER"
        assert pdf[0].get_image_info()[0]["width"] == 1000


def test_visual_metrics_cannot_promote_generated_values_or_edges_to_verification():
    data = [{"kind": "chart", "review_required": True, "series": [{"points": [
        {"value": "42", "readability": "readable", "verified": False},
        {"value": "80", "readability": "approximate", "verified": False}]}]},
        {"kind": "diagram", "review_required": True,
         "nodes": [{"node_id": "a", "label": "Start"}, {"node_id": "b", "label": "Review"}],
         "edges": [{"source": "a", "target": "b", "visible": False, "direction": "directed"}]}]
    result = visual.evaluate(data)
    assert result["visible_label_matches"] == 1
    assert result["diagram_label_matches"] == 2
    assert result["diagram_relation_matches"] == 1
    assert result["independent_visual_accuracy_accepted"] is False
