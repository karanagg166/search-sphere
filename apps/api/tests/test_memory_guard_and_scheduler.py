import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.services.durable_processor import DurableDocumentProcessor, get_durable_processor
from src.services.memory_guard import MemoryGuard, memory_guard
from src.services.model_manager import ModelManager, model_manager
from src.services.workload_scheduler import WorkloadOverloadError, WorkloadScheduler, workload_scheduler


class TestModelManager:
    """Verifies centralized, thread-safe model caching and bounded inference."""

    def test_singleton_model_instances(self):
        # Dense embedder singleton check
        dense_1 = model_manager.get_dense_embedder()
        dense_2 = model_manager.get_dense_embedder()
        assert dense_1 is dense_2

        # Sparse embedder singleton check
        sparse_1 = model_manager.get_sparse_embedder()
        sparse_2 = model_manager.get_sparse_embedder()
        assert sparse_1 is sparse_2

        # Reranker singleton check
        reranker_1 = model_manager.get_reranker()
        reranker_2 = model_manager.get_reranker()
        assert reranker_1 is reranker_2

    def test_embed_dense_bounded(self):
        texts = [f"Sample sentence number {i} for bounded embedding test." for i in range(25)]
        
        # Test bounded batch execution with batch_size=8
        embeddings = model_manager.embed_dense_bounded(texts, batch_size=8)
        assert len(embeddings) == 25
        assert len(embeddings[0]) == 384  # MiniLM dimension

    def test_embed_sparse_bounded(self):
        texts = [f"Sample query text {i} for sparse BM25 test." for i in range(10)]
        
        sparse_vectors = model_manager.embed_sparse_bounded(texts, batch_size=4)
        assert len(sparse_vectors) == 10
        assert hasattr(sparse_vectors[0], "indices")
        assert hasattr(sparse_vectors[0], "values")

    def test_release_memory(self):
        released = model_manager.release_memory()
        assert isinstance(released, int)
        assert released >= 0


class TestMemoryGuard:
    """Verifies memory measurement and tiered watermark thresholds."""

    def test_memory_status_structure(self):
        metrics = memory_guard.get_metrics()

        assert "current_mb" in metrics
        assert "peak_mb" in metrics
        assert "limit_mb" in metrics
        assert "headroom_mb" in metrics
        assert "usage_percent" in metrics
        assert "pressure_level" in metrics
        assert "heavy_workload_allowed" in metrics
        assert "interactive_rag_allowed" in metrics
        assert metrics["limit_mb"] == 512.0

    @pytest.mark.parametrize(
        "simulated_mb,expected_pressure,can_rag,can_heavy",
        [
            (200.0, "normal", True, True),
            (390.0, "moderate", True, True),
            (435.0, "high", True, False),
            (470.0, "critical", False, False),
        ],
    )
    def test_watermark_thresholds(self, simulated_mb, expected_pressure, can_rag, can_heavy):
        guard = MemoryGuard(platform_limit_mb=512.0)

        with patch.object(guard, "get_current_memory_mb", return_value=simulated_mb):
            pressure = guard.get_pressure_level()
            assert pressure == expected_pressure
            assert guard.is_interactive_rag_allowed() == can_rag
            assert guard.is_heavy_workload_allowed() == can_heavy


class TestWorkloadScheduler:
    """Verifies bounded concurrency, admission control, and 429/503 backpressure."""

    @pytest.mark.asyncio
    async def test_interactive_rag_scope_normal_admission(self):
        scheduler = WorkloadScheduler(max_rag_queries=2, max_heavy_jobs=1)

        with patch("src.services.workload_scheduler.memory_guard.is_interactive_rag_allowed", return_value=True):
            async with scheduler.interactive_rag_scope():
                assert scheduler.active_rag_queries == 1

        assert scheduler.active_rag_queries == 0

    @pytest.mark.asyncio
    async def test_interactive_rag_scope_critical_pressure_rejection(self):
        scheduler = WorkloadScheduler(max_rag_queries=2, max_heavy_jobs=1)

        # Under CRITICAL pressure, interactive RAG request must be rejected with 429 and Retry-After
        with patch("src.services.workload_scheduler.memory_guard.is_interactive_rag_allowed", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                async with scheduler.interactive_rag_scope():
                    pass

            assert exc_info.value.status_code == 429
            assert exc_info.value.headers.get("Retry-After") == "5"

    @pytest.mark.asyncio
    async def test_heavy_job_scope_high_pressure_rejection(self):
        scheduler = WorkloadScheduler(max_rag_queries=2, max_heavy_jobs=1)

        # Under HIGH pressure, heavy background job must be rejected with 503 and Retry-After
        with patch("src.services.workload_scheduler.memory_guard.is_heavy_workload_allowed", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                async with scheduler.heavy_job_scope(timeout=0.1):
                    pass

            assert exc_info.value.status_code == 503
            assert exc_info.value.headers.get("Retry-After") == "10"

    @pytest.mark.asyncio
    async def test_rag_concurrency_bounded_to_max(self):
        scheduler = WorkloadScheduler(max_rag_queries=2, max_heavy_jobs=1)

        with patch("src.services.workload_scheduler.memory_guard.is_interactive_rag_allowed", return_value=True):
            async with scheduler.interactive_rag_scope():
                async with scheduler.interactive_rag_scope():
                    # Both slots are now occupied (2 active)
                    assert scheduler.active_rag_queries == 2

                    # A 3rd request with timeout 0.1s must time out and return 429
                    with pytest.raises(HTTPException) as exc_info:
                        async with scheduler.interactive_rag_scope(timeout=0.1):
                            pass

                    assert exc_info.value.status_code == 429
                    assert exc_info.value.headers.get("Retry-After") == "3"

    @pytest.mark.asyncio
    async def test_heavy_concurrency_bounded_to_max(self):
        scheduler = WorkloadScheduler(max_rag_queries=2, max_heavy_jobs=1)

        with patch("src.services.workload_scheduler.memory_guard.is_heavy_workload_allowed", return_value=True):
            async with scheduler.heavy_job_scope(timeout=1.0):
                assert scheduler.active_heavy_jobs == 1

                # A 2nd heavy job with timeout 0.1s must time out and return 503
                with pytest.raises(HTTPException) as exc_info:
                    async with scheduler.heavy_job_scope(timeout=0.1):
                        pass

                assert exc_info.value.status_code == 503
                assert exc_info.value.headers.get("Retry-After") == "10"


class TestDurableDocumentProcessor:
    """Verifies atomic leasing, restart recovery, and error classification."""

    def test_processor_singleton(self):
        proc_1 = get_durable_processor()
        proc_2 = get_durable_processor()
        assert proc_1 is proc_2

    @pytest.mark.asyncio
    async def test_recover_stranded_jobs(self):
        processor = DurableDocumentProcessor()
        mock_doc_1 = MagicMock()
        mock_doc_1.id = "doc-123"
        mock_doc_1.status = "proc:lease1:1"

        mock_doc_2 = MagicMock()
        mock_doc_2.id = "doc-456"
        mock_doc_2.status = "proc:lease2:3"  # Exceeded max retries (3)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_doc_1, mock_doc_2]
        mock_session.execute.return_value = mock_result

        with patch("src.services.durable_processor.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            recovered = await processor.recover_stranded_jobs()

            assert recovered == ["doc-123"]
            assert mock_doc_1.status == "uploaded:retry:2"
            assert mock_doc_2.status == "failed:max_retries_exceeded"
            assert mock_session.commit.called


class TestHealthMemoryEndpoint:
    """Verifies /health/memory observability endpoint."""

    @pytest.mark.asyncio
    async def test_health_memory_endpoint_returns_metrics(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/memory")
            assert response.status_code == 200
            data = response.json()

            assert "status" in data
            assert data["status"] in ("ok", "under_pressure")
            assert "scheduler" in data
            assert "memory" in data["scheduler"]
            assert "current_mb" in data["scheduler"]["memory"]
            assert "limit_mb" in data["scheduler"]["memory"]
            assert "pressure_level" in data["scheduler"]["memory"]
            assert "models" in data
