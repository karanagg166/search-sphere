from unittest.mock import patch

from src.services.memory_guard import MemoryGuard


def test_497_mib_raw_usage_rejects_heavy_workload_despite_page_cache():
    """
    At approximately 497 MiB raw container usage in a 512 MiB limit container,
    large new workloads must NOT be admitted solely because estimated reclaimable
    page cache appears available.
    """
    guard = MemoryGuard(platform_limit_mb=512.0, bypass_in_test=False)

    # Simulated snapshot where raw container usage is 497 MiB (only 15 MiB raw unallocated headroom),
    # but 120 MiB is reported as inactive file page cache (giving a false sense of 99 MiB 'safe headroom')
    snapshot_497mb = {
        "raw_cgroup_mb": 497.0,
        "inactive_file_mb": 120.0,
        "estimated_working_set_mb": 377.0,
        "process_rss_mb": 350.0,
    }

    with patch.object(guard, "_read_memory_snapshot", return_value=snapshot_497mb):
        guard.invalidate_cache()
        metrics = guard.get_metrics()

        assert metrics["raw_cgroup_mb"] == 497.0
        assert metrics["limit_mb"] == 512.0
        assert metrics["container_headroom_mb"] == 15.0

        # Memory pressure must escalate to critical because raw container headroom is <= 16 MiB
        pressure = guard.get_pressure_level()
        assert pressure == "critical"

        # Heavy workloads (extraction, chunking, embedding) MUST NOT be admitted
        assert guard.is_heavy_workload_allowed() is False

        # Interactive RAG queries must also be guarded against OOM termination
        assert guard.is_interactive_rag_allowed() is False


def test_distinguishes_raw_container_from_working_set_telemetry():
    """Verify that get_metrics cleanly distinguishes raw container cgroup, working set, and process RSS."""
    guard = MemoryGuard(platform_limit_mb=512.0, bypass_in_test=False)

    snapshot = {
        "raw_cgroup_mb": 450.0,
        "inactive_file_mb": 90.0,
        "estimated_working_set_mb": 360.0,
        "process_rss_mb": 260.0,
    }

    with patch.object(guard, "_read_memory_snapshot", return_value=snapshot):
        guard.invalidate_cache()
        metrics = guard.get_metrics()

        assert metrics["raw_cgroup_mb"] == 450.0
        assert metrics["estimated_working_set_mb"] == 360.0
        assert metrics["process_rss_mb"] == 260.0
        assert metrics["inactive_file_mb"] == 90.0
        assert metrics["container_headroom_mb"] == 62.0
        assert metrics["working_set_headroom_mb"] == 152.0
        # Safe headroom: 62.0 + (90.0 * 0.70) = 125.0
        assert metrics["safe_headroom_mb"] == 125.0


def test_conservative_workload_specific_admission_thresholds():
    """Verify tiered admission limits for heavy workloads vs interactive queries."""
    guard = MemoryGuard(platform_limit_mb=512.0, bypass_in_test=False)

    # Case A: Ample raw headroom (raw=400 MiB, headroom=112 MiB) -> Both allowed
    snapshot_ample = {
        "raw_cgroup_mb": 400.0,
        "inactive_file_mb": 50.0,
        "estimated_working_set_mb": 350.0,
        "process_rss_mb": 250.0,
    }
    with patch.object(guard, "_read_memory_snapshot", return_value=snapshot_ample):
        guard.invalidate_cache()
        assert guard.get_pressure_level() == "normal"
        assert guard.is_heavy_workload_allowed() is True
        assert guard.is_interactive_rag_allowed() is True

    # Case B: Marginal raw headroom (raw=489 MiB, raw headroom=23 MiB) -> Heavy blocked, interactive allowed
    snapshot_marginal = {
        "raw_cgroup_mb": 489.0,
        "inactive_file_mb": 80.0,
        "estimated_working_set_mb": 409.0,
        "process_rss_mb": 320.0,
    }
    with patch.object(guard, "_read_memory_snapshot", return_value=snapshot_marginal):
        guard.invalidate_cache()
        # Raw headroom is 23 MiB (< 25 MiB required for heavy)
        assert guard.is_heavy_workload_allowed() is False
        assert guard.is_interactive_rag_allowed() is True
