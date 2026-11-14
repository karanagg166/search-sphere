import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.gold_benchmark import run_benchmark


def test_gold_benchmark_metrics_meet_production_thresholds():
    metrics = run_benchmark()
    assert metrics.native_cer <= 0.02
    assert metrics.native_wer <= 0.02
    assert metrics.reading_order_accuracy == 1.0
    assert metrics.table_cell_accuracy == 1.0
    assert metrics.table_merge_precision == 1.0
    assert metrics.table_merge_recall == 1.0
    assert metrics.panel_detection_accuracy == 1.0
    assert metrics.diagram_node_recall == 1.0
    assert metrics.diagram_edge_recall == 1.0
    assert metrics.chart_value_recall == 1.0
    assert metrics.source_coordinate_validity == 1.0
    assert metrics.chunk_source_link_completeness == 1.0
    assert metrics.missing_content_rate == 0.0
    assert metrics.hosted_provider_calls == 0
