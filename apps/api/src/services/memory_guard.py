import os
import resource
import subprocess
import sys
import time
from typing import Any

import structlog

from src.config import settings

logger = structlog.get_logger()

# Configurable container memory thresholds (MiB) based on 512 MiB Render Free Tier limit
DEFAULT_PLATFORM_LIMIT_MB = 512.0
NORMAL_THRESHOLD_MB = 320.0
MODERATE_THRESHOLD_MB = 380.0
HIGH_THRESHOLD_MB = 430.0
CRITICAL_THRESHOLD_MB = 465.0


class MemoryGuard:
    """
    Proactive Memory Guardrail and Metrics Provider.

    Inspects process RSS and Linux container cgroup memory limits.
    Classifies memory pressure states and provides backpressure signals
    to prevent container out-of-memory (OOM) termination on Render.
    """

    def __init__(
        self,
        platform_limit_mb: float = DEFAULT_PLATFORM_LIMIT_MB,
        bypass_in_test: bool = True,
    ) -> None:
        self.platform_limit_mb = platform_limit_mb
        self.bypass_in_test = bypass_in_test
        self._peak_memory_mb: float = 0.0
        self._last_read_time: float = 0.0
        self._cached_usage_mb: float = 0.0
        self._cached_metrics_snapshot: dict[str, float] = {}
        self._cache_ttl_sec: float = 0.25  # 250ms cache to avoid disk thrashing
        self._last_logged_pressure: str = "normal"
        self._cgroup_version: int | None = None
        self._is_container_constrained: bool = False
        self._detect_environment()

    def _detect_environment(self) -> None:
        """Detects whether running inside Linux cgroups (Render / Docker) and determines container limits."""
        if sys.platform.startswith("linux"):
            if os.path.exists("/sys/fs/cgroup/memory.current"):
                self._cgroup_version = 2
                self._detect_cgroup_v2_limit()
            elif os.path.exists("/sys/fs/cgroup/memory/memory.usage_in_bytes"):
                self._cgroup_version = 1
                self._detect_cgroup_v1_limit()
            else:
                self._cgroup_version = 0
        else:
            self._cgroup_version = 0

    def _detect_cgroup_v2_limit(self) -> None:
        """Reads cgroup v2 memory.max limit if configured."""
        try:
            with open("/sys/fs/cgroup/memory.max") as f:
                val = f.read().strip()
                if val and val != "max":
                    limit_bytes = int(val)
                    limit_mb = limit_bytes / (1024.0 * 1024.0)
                    if 50.0 <= limit_mb <= 16384.0:
                        self.platform_limit_mb = limit_mb
                        self._is_container_constrained = True
                        logger.info("Detected cgroup v2 memory limit", limit_mb=round(self.platform_limit_mb, 1))
        except Exception:
            pass

    def _detect_cgroup_v1_limit(self) -> None:
        """Reads cgroup v1 memory.limit_in_bytes if configured."""
        try:
            with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
                val = f.read().strip()
                if val:
                    limit_bytes = int(val)
                    limit_mb = limit_bytes / (1024.0 * 1024.0)
                    if 50.0 <= limit_mb <= 16384.0:
                        self.platform_limit_mb = limit_mb
                        self._is_container_constrained = True
                        logger.info("Detected cgroup v1 memory limit", limit_mb=round(self.platform_limit_mb, 1))
        except Exception:
            pass

    def _read_memory_snapshot(self) -> dict[str, float]:
        """Reads raw memory indicators across cgroups and process RSS."""
        raw_cgroup_mb = 0.0
        inactive_file_mb = 0.0
        working_set_mb = 0.0
        process_rss_mb = 0.0

        # 1. Process RSS
        if sys.platform.startswith("linux"):
            try:
                with open("/proc/self/statm") as f:
                    rss_pages = int(f.read().split()[1])
                page_size = os.sysconf("SC_PAGE_SIZE")
                process_rss_mb = (rss_pages * page_size) / (1024.0 * 1024.0)
            except Exception:
                pass
        if process_rss_mb <= 0.0:
            try:
                out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())])
                process_rss_mb = int(out.decode().strip()) / 1024.0
            except Exception:
                try:
                    ru = resource.getrusage(resource.RUSAGE_SELF)
                    scale = (1024.0 * 1024.0) if sys.platform == "darwin" else 1024.0
                    process_rss_mb = ru.ru_maxrss / scale
                except Exception:
                    process_rss_mb = 0.0

        # 2. Container cgroup v2
        if self._is_container_constrained and self._cgroup_version == 2:
            try:
                with open("/sys/fs/cgroup/memory.current") as f:
                    curr_bytes = int(f.read().strip())
                raw_cgroup_mb = curr_bytes / (1024.0 * 1024.0)

                stats: dict[str, int] = {}
                try:
                    with open("/sys/fs/cgroup/memory.stat") as sf:
                        for line in sf:
                            parts = line.split()
                            if len(parts) >= 2:
                                try:
                                    stats[parts[0]] = int(parts[1])
                                except ValueError:
                                    pass
                except Exception:
                    pass

                regular_file = max(0, stats.get("file", 0) - stats.get("shmem", 0))
                inactive_file_bytes = max(stats.get("inactive_file", 0), regular_file)
                inactive_file_mb = inactive_file_bytes / (1024.0 * 1024.0)
                working_set_bytes = max(0, curr_bytes - inactive_file_bytes)
                working_set_mb = working_set_bytes / (1024.0 * 1024.0)
            except Exception:
                pass

        # 3. Container cgroup v1
        elif self._is_container_constrained and self._cgroup_version == 1:
            try:
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
                    curr_bytes = int(f.read().strip())
                raw_cgroup_mb = curr_bytes / (1024.0 * 1024.0)

                stats = {}
                try:
                    with open("/sys/fs/cgroup/memory/memory.stat") as sf:
                        for line in sf:
                            parts = line.split()
                            if len(parts) >= 2:
                                try:
                                    stats[parts[0]] = int(parts[1])
                                except ValueError:
                                    pass
                except Exception:
                    pass

                total_cache = stats.get("total_cache", 0)
                total_shmem = stats.get("total_shmem", 0)
                regular_cache = max(0, total_cache - total_shmem)
                total_inactive = stats.get("total_inactive_file", stats.get("inactive_file", 0))
                inactive_file_bytes = max(total_inactive, regular_cache)
                inactive_file_mb = inactive_file_bytes / (1024.0 * 1024.0)
                working_set_bytes = max(0, curr_bytes - inactive_file_bytes)
                working_set_mb = working_set_bytes / (1024.0 * 1024.0)
            except Exception:
                pass

        # Fallback when not running in constrained cgroup container
        if raw_cgroup_mb <= 0.0:
            raw_cgroup_mb = process_rss_mb
        if working_set_mb <= 0.0:
            working_set_mb = process_rss_mb

        return {
            "raw_cgroup_mb": raw_cgroup_mb,
            "inactive_file_mb": inactive_file_mb,
            "estimated_working_set_mb": working_set_mb,
            "process_rss_mb": process_rss_mb,
        }

    def get_current_memory_mb(self, force_refresh: bool = False) -> float:
        """
        Returns memory consumption in MiB.
        Prioritizes container cgroup working set on Linux when constrained,
        falling back to process RSS on unconstrained hosts/VMs/macOS.
        """
        now = time.monotonic()
        if not force_refresh and (now - self._last_read_time) < self._cache_ttl_sec and self._cached_usage_mb > 0:
            return self._cached_usage_mb

        snapshot = self._read_memory_snapshot()
        self._cached_metrics_snapshot = snapshot
        usage_mb = snapshot["estimated_working_set_mb"] if snapshot["estimated_working_set_mb"] > 0 else snapshot["process_rss_mb"]

        self._cached_usage_mb = usage_mb
        self._last_read_time = now

        if usage_mb > self._peak_memory_mb:
            self._peak_memory_mb = usage_mb

        return usage_mb

    def invalidate_cache(self) -> None:
        """Forces subsequent memory queries to re-read system state immediately."""
        self._last_read_time = 0.0
        self._cached_usage_mb = 0.0
        self._cached_metrics_snapshot = {}

    def get_peak_memory_mb(self) -> float:
        """Returns highest observed memory usage in this process lifecycle."""
        current = self.get_current_memory_mb()
        if current > self._peak_memory_mb:
            self._peak_memory_mb = current
        return self._peak_memory_mb

    def _get_headroom_snapshot(self) -> tuple[float, float, float, float]:
        """
        Calculates conservative memory headroom.
        Returns (raw_cgroup_mb, inactive_file_mb, safe_headroom_mb, conservative_used_mb).
        Inactive file page cache is credited at 70% (retaining a 30% safety margin) so estimated
        reclaimable cache is NOT treated as 100% guaranteed free memory, while avoiding false
        'high' or 'critical' alarms when clean model weights (FastEmbed + cross-encoder) reside in RAM.
        """
        snapshot = self._cached_metrics_snapshot or {}
        current_mb = self.get_current_memory_mb()
        raw_mb = snapshot.get("raw_cgroup_mb", current_mb)
        inactive_mb = snapshot.get("inactive_file_mb", 0.0)
        # Raw container unallocated headroom
        container_headroom = max(0.0, self.platform_limit_mb - raw_mb)
        # Safe headroom: actual unallocated headroom + 70% of inactive page cache
        safe_headroom = max(0.0, container_headroom + (inactive_mb * 0.70))
        # Conservative memory used
        conservative_used = max(0.0, self.platform_limit_mb - safe_headroom)
        return raw_mb, inactive_mb, safe_headroom, conservative_used

    def get_pressure_level(self, force_refresh: bool = False) -> str:
        """
        Categorizes system memory pressure using conservative container headroom:
        - 'critical': working set >= 460 MiB or conservative used >= 475 MiB or safe headroom <= 20 MiB
        - 'high': working set >= 435 MiB or conservative used >= 450 MiB or safe headroom <= 35 MiB
        - 'moderate': working set >= 370 MiB or conservative used >= 370 MiB or safe headroom <= 110 MiB
        - 'normal': Below 370 MiB with ample safe container headroom (> 110 MiB)
        """
        mem_mb = self.get_current_memory_mb(force_refresh=force_refresh)
        _, _, safe_headroom, conservative_used = self._get_headroom_snapshot()

        if (
            mem_mb >= CRITICAL_THRESHOLD_MB
            or conservative_used >= 475.0
            or safe_headroom <= 20.0
        ):
            level = "critical"
        elif (
            mem_mb >= 435.0
            or conservative_used >= 450.0
            or safe_headroom <= 35.0
        ):
            level = "high"
        elif (
            mem_mb >= MODERATE_THRESHOLD_MB
            or conservative_used >= 370.0
            or safe_headroom <= 110.0
        ):
            level = "moderate"
        else:
            level = "normal"

        if level != self._last_logged_pressure:
            logger.warning(
                "Memory pressure level transitioned",
                previous_level=self._last_logged_pressure,
                current_level=level,
                current_memory_mb=round(mem_mb, 1),
                platform_limit_mb=round(self.platform_limit_mb, 1),
                safe_headroom_mb=round(safe_headroom, 1),
            )
            self._last_logged_pressure = level

        return level

    def is_heavy_workload_allowed(self) -> bool:
        """Returns True if actual container headroom permits launching a heavy background task (indexing, extraction)."""
        if self.bypass_in_test and getattr(settings, "ENVIRONMENT", "").lower() in ("test", "testing", "ci"):
            return True
        pressure = self.get_pressure_level()
        if pressure in ("high", "critical"):
            return False
        # Conservative check: require at least 30 MiB of safe container headroom
        _, _, safe_headroom, _ = self._get_headroom_snapshot()
        return safe_headroom >= 30.0

    def is_interactive_rag_allowed(self) -> bool:
        """Returns True if memory headroom permits serving an interactive RAG query."""
        if self.bypass_in_test and getattr(settings, "ENVIRONMENT", "").lower() in ("test", "testing", "ci"):
            return True
        pressure = self.get_pressure_level()
        if pressure == "critical":
            return False
        # Conservative check: require at least 15 MiB of safe container headroom
        _, _, safe_headroom, _ = self._get_headroom_snapshot()
        return safe_headroom >= 15.0

    def get_metrics(self, force_refresh: bool = False) -> dict[str, Any]:
        """Provides structured memory diagnostic metrics for monitoring and health endpoints."""
        current_mb = self.get_current_memory_mb(force_refresh=force_refresh)
        peak_mb = self.get_peak_memory_mb()
        pressure = self.get_pressure_level(force_refresh=force_refresh)

        snapshot = self._cached_metrics_snapshot or {}
        raw_cgroup_mb = snapshot.get("raw_cgroup_mb", current_mb)
        inactive_file_mb = snapshot.get("inactive_file_mb", 0.0)
        working_set_mb = snapshot.get("estimated_working_set_mb", current_mb)
        process_rss_mb = snapshot.get("process_rss_mb", current_mb)

        container_headroom_mb = max(0.0, self.platform_limit_mb - raw_cgroup_mb)
        working_set_headroom_mb = max(0.0, self.platform_limit_mb - working_set_mb)
        safe_headroom_mb = max(0.0, container_headroom_mb + (inactive_file_mb * 0.70))
        usage_pct = min(100.0, (current_mb / self.platform_limit_mb) * 100.0)

        return {
            "raw_cgroup_mb": round(raw_cgroup_mb, 2),
            "limit_mb": round(self.platform_limit_mb, 2),
            "inactive_file_mb": round(inactive_file_mb, 2),
            "estimated_working_set_mb": round(working_set_mb, 2),
            "process_rss_mb": round(process_rss_mb, 2),
            "pressure_level": pressure,
            "container_headroom_mb": round(container_headroom_mb, 2),
            "working_set_headroom_mb": round(working_set_headroom_mb, 2),
            "safe_headroom_mb": round(safe_headroom_mb, 2),
            "current_mb": round(current_mb, 2),
            "peak_mb": round(peak_mb, 2),
            "headroom_mb": round(container_headroom_mb, 2),
            "usage_percent": round(usage_pct, 1),
            "heavy_workload_allowed": self.is_heavy_workload_allowed(),
            "interactive_rag_allowed": self.is_interactive_rag_allowed(),
            "cgroup_version": self._cgroup_version,
        }


# Global Singleton
memory_guard = MemoryGuard()
