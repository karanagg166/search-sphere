import asyncio
import ctypes
import gc
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import structlog

from src.config import settings

logger = structlog.get_logger()

# Enforce single-thread execution for native libraries to prevent thread-pool memory blowup
for var in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    if var not in os.environ:
        os.environ[var] = "1"

DEFAULT_DENSE_MODEL = getattr(settings, "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_SPARSE_MODEL = "Qdrant/bm25"
DEFAULT_RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
MAX_INFERENCE_BATCH_SIZE = 16


class ModelManager:
    """
    Centralized, thread-safe model lifecycle manager.
    
    Prevents duplicate model loading, manages native thread pools,
    enforces bounded inference batch sizes, and provides controlled
    memory unloading for memory-constrained environments (512 MiB RAM limit).
    """

    _instance: "ModelManager | None" = None
    _class_lock = threading.Lock()

    def __init__(self) -> None:
        self._sync_lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None
        self._models: dict[str, Any] = {}
        self._model_metadata: dict[str, dict[str, Any]] = {}
        self._total_inferences: dict[str, int] = {}

    @classmethod
    def get_instance(cls) -> "ModelManager":
        """Get or initialize singleton instance of ModelManager."""
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _get_async_lock(self) -> asyncio.Lock:
        """Lazily initialize asyncio.Lock inside running event loop."""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    def _get_cache_path(self) -> str | None:
        """Resolve FastEmbed local cache directory with graceful fallback if path is uncreatable."""
        path = (
            getattr(settings, "FASTEMBED_CACHE_PATH", None)
            or os.environ.get("FASTEMBED_CACHE_PATH")
        )
        if path:
            try:
                p = Path(path)
                p.mkdir(parents=True, exist_ok=True)
                return str(p)
            except (OSError, PermissionError):
                logger.warning(
                    "Configured FASTEMBED_CACHE_PATH is inaccessible on host; falling back to default cache",
                    path=path,
                )
                return None
        return None

    # -------------------------------------------------------------------------
    # Model Retrieval (Thread-Safe Singletons)
    # -------------------------------------------------------------------------

    def get_dense_embedder(self, model_name: str | None = None) -> Any:
        """
        Lazily loads and returns the FastEmbed TextEmbedding singleton.
        Reused by both dense retrieval and semantic chunking.
        """
        resolved_name = model_name or DEFAULT_DENSE_MODEL
        if not resolved_name.startswith("sentence-transformers/") and resolved_name == "all-MiniLM-L6-v2":
            resolved_name = "sentence-transformers/all-MiniLM-L6-v2"

        key = f"dense:{resolved_name}"
        if key in self._models:
            return self._models[key]

        with self._sync_lock:
            if key in self._models:
                return self._models[key]

            cache_path = self._get_cache_path()
            logger.info("Initializing FastEmbed TextEmbedding", model_name=resolved_name, threads=1)
            t0 = time.perf_counter()

            try:
                from fastembed import TextEmbedding

                model = TextEmbedding(
                    model_name=resolved_name,
                    cache_dir=cache_path,
                    threads=1,
                )
                self._models[key] = model
                self._model_metadata[key] = {
                    "type": "dense",
                    "name": resolved_name,
                    "loaded_at": time.time(),
                    "init_duration_ms": (time.perf_counter() - t0) * 1000,
                }
                self._total_inferences[key] = 0
                logger.info(
                    "FastEmbed TextEmbedding initialized successfully",
                    model_name=resolved_name,
                    duration_ms=self._model_metadata[key]["init_duration_ms"],
                )
                return model
            except Exception as exc:
                logger.exception("Failed to load FastEmbed TextEmbedding", error=str(exc))
                raise

    def get_sparse_embedder(self, model_name: str | None = None) -> Any:
        """
        Lazily loads and returns the FastEmbed SparseTextEmbedding (BM25) singleton.
        """
        resolved_name = model_name or DEFAULT_SPARSE_MODEL
        key = f"sparse:{resolved_name}"
        if key in self._models:
            return self._models[key]

        with self._sync_lock:
            if key in self._models:
                return self._models[key]

            cache_path = self._get_cache_path()
            logger.info("Initializing FastEmbed SparseTextEmbedding", model_name=resolved_name, threads=1)
            t0 = time.perf_counter()

            try:
                from fastembed import SparseTextEmbedding

                model = SparseTextEmbedding(
                    model_name=resolved_name,
                    cache_dir=cache_path,
                    threads=1,
                )
                self._models[key] = model
                self._model_metadata[key] = {
                    "type": "sparse",
                    "name": resolved_name,
                    "loaded_at": time.time(),
                    "init_duration_ms": (time.perf_counter() - t0) * 1000,
                }
                self._total_inferences[key] = 0
                logger.info(
                    "FastEmbed SparseTextEmbedding initialized successfully",
                    model_name=resolved_name,
                    duration_ms=self._model_metadata[key]["init_duration_ms"],
                )
                return model
            except Exception as exc:
                logger.exception("Failed to load FastEmbed SparseTextEmbedding", error=str(exc))
                raise

    def get_reranker(self, model_name: str | None = None) -> Any:
        """
        Lazily loads and returns the FastEmbed TextCrossEncoder reranker singleton.
        """
        resolved_name = model_name or DEFAULT_RERANKER_MODEL
        key = f"reranker:{resolved_name}"
        if key in self._models:
            return self._models[key]

        with self._sync_lock:
            if key in self._models:
                return self._models[key]

            cache_path = self._get_cache_path()
            logger.info("Initializing FastEmbed TextCrossEncoder", model_name=resolved_name, threads=1)
            t0 = time.perf_counter()

            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                model = TextCrossEncoder(
                    model_name=resolved_name,
                    cache_dir=cache_path,
                    threads=1,
                )
                self._models[key] = model
                self._model_metadata[key] = {
                    "type": "reranker",
                    "name": resolved_name,
                    "loaded_at": time.time(),
                    "init_duration_ms": (time.perf_counter() - t0) * 1000,
                }
                self._total_inferences[key] = 0
                logger.info(
                    "FastEmbed TextCrossEncoder initialized successfully",
                    model_name=resolved_name,
                    duration_ms=self._model_metadata[key]["init_duration_ms"],
                )
                return model
            except Exception as exc:
                logger.exception("Failed to load FastEmbed TextCrossEncoder", error=str(exc))
                raise

    # -------------------------------------------------------------------------
    # Bounded Inference Helpers
    # -------------------------------------------------------------------------

    def embed_dense_bounded(
        self,
        texts: list[str],
        model_name: str | None = None,
        batch_size: int = MAX_INFERENCE_BATCH_SIZE,
    ) -> list[list[float]]:
        """
        Generates dense vector embeddings in bounded slices to control peak RAM.
        """
        if not texts:
            return []

        model = self.get_dense_embedder(model_name)
        key = f"dense:{model_name or DEFAULT_DENSE_MODEL}"

        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = list(model.embed(batch))
            for vec in embeddings:
                # Ensure plain python float lists to avoid retaining ONNX/numpy array references
                results.append(vec.tolist() if hasattr(vec, "tolist") else list(vec))

        if key in self._total_inferences:
            self._total_inferences[key] += len(texts)

        return results

    def embed_sparse_bounded(
        self,
        texts: list[str],
        model_name: str | None = None,
        batch_size: int = MAX_INFERENCE_BATCH_SIZE,
    ) -> list[Any]:
        """
        Generates sparse BM25 embeddings in bounded slices.
        """
        if not texts:
            return []

        model = self.get_sparse_embedder(model_name)
        key = f"sparse:{model_name or DEFAULT_SPARSE_MODEL}"

        results: list[Any] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            sparse_vecs = list(model.embed(batch))
            results.extend(sparse_vecs)

        if key in self._total_inferences:
            self._total_inferences[key] += len(texts)

        return results

    # -------------------------------------------------------------------------
    # Controlled Unloading & Memory Compaction
    # -------------------------------------------------------------------------

    def unload_model(self, key_prefix: str) -> bool:
        """
        Unloads models matching key_prefix (e.g. 'reranker' or 'dense') to shed memory.
        """
        with self._sync_lock:
            matched = [k for k in self._models if k.startswith(key_prefix)]
            if not matched:
                return False

            for k in matched:
                del self._models[k]
                self._model_metadata.pop(k, None)
                self._total_inferences.pop(k, None)
                logger.info("Unloaded model from memory", model_key=k)

            self.release_memory()
            return True

    def unload_all(self) -> None:
        """Clears all cached models from memory."""
        with self._sync_lock:
            self._models.clear()
            self._model_metadata.clear()
            self._total_inferences.clear()
            self.release_memory()

    @staticmethod
    def release_memory() -> int:
        """
        Forces Python garbage collection and invokes glibc malloc_trim
        on Linux to return freed heap pages back to the OS immediately.
        Returns the number of unreachable objects collected.
        """
        collected = gc.collect()
        if sys.platform.startswith("linux"):
            try:
                libc = ctypes.CDLL("libc.so.6")
                if hasattr(libc, "malloc_trim"):
                    libc.malloc_trim(0)
            except Exception:
                pass
        return collected

    def get_loaded_models_info(self) -> dict[str, Any]:
        """Returns diagnostic metadata about currently resident models."""
        return {
            "loaded_models": list(self._models.keys()),
            "metadata": dict(self._model_metadata),
            "inferences": dict(self._total_inferences),
        }


# Singleton access helper
model_manager = ModelManager.get_instance()
