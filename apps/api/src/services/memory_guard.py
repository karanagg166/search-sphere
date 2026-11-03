import os
import resource
import subprocess
import sys
import time
from typing import Any

import structlog

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

    def __init__(self, platform_limit_mb: float = DEFAULT_PLATFORM_LIMIT_MB) -> None:
        self.platform_limit_mb = platform_limit_mb
        self._peak_memory_mb: float = 0.0
        self._last_read_time: float = 0.0
        self._cached_usage_mb: float = 0.0
        self._cache_ttl_sec: float = 0.25  # 250ms cache to avoid disk thrashing
        self._last_logged_pressure: str = "normal"
        self._cgroup_version: int | None = None
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
                        logger.info("Detected cgroup v1 memory limit", limit_mb=round(self.platform_limit_mb, 1))
        except Exception:
            pass

    def get_current_memory_mb(self) -> float:
        """
        Returns the most accurate memory consumption in MiB.
        Prioritizes container cgroup current memory usage on Linux,
        falling back to process RSS.
        """
        now = time.monotonic()
        if (now - self._last_read_time) < self._cache_ttl_sec and self._cached_usage_mb > 0:
            return self._cached_usage_mb

        usage_mb = 0.0

        # 1. Container cgroup v2 (Modern Docker / Render)
        if self._cgroup_version == 2:
            try:
                with open("/sys/fs/cgroup/memory.current") as f:
                    usage_mb = int(f.read().strip()) / (1024.0 * 1024.0)
            except Exception:
                usage_mb = 0.0

        # 2. Container cgroup v1
        if usage_mb <= 0.0 and self._cgroup_version == 1:
            try:
                with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
                    usage_mb = int(f.read().strip()) / (1024.0 * 1024.0)
            except Exception:
                usage_mb = 0.0

        # 3. Process RSS via /proc/self/statm (Linux host / container fallback)
        if usage_mb <= 0.0 and sys.platform.startswith("linux"):
            try:
                with open("/proc/self/statm") as f:
                    rss_pages = int(f.read().split()[1])
                page_size = os.sysconf("SC_PAGE_SIZE")
                usage_mb = (rss_pages * page_size) / (1024.0 * 1024.0)
            except Exception:
                pass

        # 4. macOS / BSD process RSS fallback
        if usage_mb <= 0.0:
            try:
                out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())])
                usage_mb = int(out.decode().strip()) / 1024.0
            except Exception:
                # Last resort: maxrss from resource
                ru = resource.getrusage(resource.RUSAGE_SELF)
                scale = (1024.0 * 1024.0) if sys.platform == "darwin" else 1024.0
                usage_mb = ru.ru_maxrss / scale

        self._cached_usage_mb = usage_mb
        self._last_read_time = now

        if usage_mb > self._peak_memory_mb:
            self._peak_memory_mb = usage_mb

        return usage_mb

    def get_peak_memory_mb(self) -> float:
        """Returns highest observed memory usage in this process lifecycle."""
        current = self.get_current_memory_mb()
        if current > self._peak_memory_mb:
            self._peak_memory_mb = current
        return self._peak_memory_mb

    def get_pressure_level(self) -> str:
        """
        Categorizes system memory pressure:
        - 'normal': Below 320 MiB
        - 'moderate': 320 - 380 MiB
        - 'high': 380 - 430 MiB
        - 'critical': Above 430 MiB
        """
        mem_mb = self.get_current_memory_mb()
        if mem_mb >= CRITICAL_THRESHOLD_MB or mem_mb >= (self.platform_limit_mb * 0.90):
            level = "critical"
        elif mem_mb >= HIGH_THRESHOLD_MB or mem_mb >= (self.platform_limit_mb * 0.82):
            level = "high"
        elif mem_mb >= MODERATE_THRESHOLD_MB or mem_mb >= (self.platform_limit_mb * 0.72):
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
                headroom_mb=round(self.platform_limit_mb - mem_mb, 1),
            )
            self._last_logged_pressure = level

        return level

    def is_heavy_workload_allowed(self) -> bool:
        """Returns True if memory headroom permits launching a heavy background task (indexing, extraction)."""
        pressure = self.get_pressure_level()
        return pressure in ("normal", "moderate")

    def is_interactive_rag_allowed(self) -> bool:
        """Returns True if memory headroom permits serving an interactive RAG query."""
        pressure = self.get_pressure_level()
        return pressure != "critical"

    def get_metrics(self) -> dict[str, Any]:
        """Provides structured memory diagnostic metrics for monitoring and health endpoints."""
        current_mb = self.get_current_memory_mb()
        peak_mb = self.get_peak_memory_mb()
        pressure = self.get_pressure_level()
        headroom_mb = max(0.0, self.platform_limit_mb - current_mb)
        usage_pct = min(100.0, (current_mb / self.platform_limit_mb) * 100.0)

        return {
            "current_mb": round(current_mb, 2),
            "peak_mb": round(peak_mb, 2),
            "limit_mb": round(self.platform_limit_mb, 2),
            "headroom_mb": round(headroom_mb, 2),
            "usage_percent": round(usage_pct, 1),
            "pressure_level": pressure,
            "heavy_workload_allowed": self.is_heavy_workload_allowed(),
            "interactive_rag_allowed": self.is_interactive_rag_allowed(),
            "cgroup_version": self._cgroup_version,
        }


# Global Singleton
memory_guard = MemoryGuard()
