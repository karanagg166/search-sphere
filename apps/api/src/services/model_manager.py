import asyncio
import ctypes
import gc
import os
import sys
import threading
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

DEFAULT_DENSE_MODEL = getattr(settings, "EMBEDDING_MODEL", "embed-english-light-v3.0")
DEFAULT_SPARSE_MODEL = "postgresql-tsvector"
DEFAULT_RERANKER_MODEL = "rerank-v3.5"
MAX_INFERENCE_BATCH_SIZE = 16

RERANKER_MODEL_ALIASES: dict[str, str] = {
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "rerank-v3.5",
    "ms-marco-MiniLM-L-6-v2": "rerank-v3.5",
    "Xenova/ms-marco-MiniLM-L-6-v2": "rerank-v3.5",
}



class ModelManager:
    """
    Centralized, thread-safe model lifecycle manager.

    Caches lightweight hosted adapters, manages bounded requests,
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

    def _adapter(self, key: str, factory, name: str):
        with self._sync_lock:
            if key not in self._models:
                self._models[key] = factory()
                self._model_metadata[key] = {"type": key.split(":")[0], "name": name, "hosted": key != "sparse:postgresql"}
                self._total_inferences[key] = 0
            return self._models[key]

    def get_dense_embedder(self, model_name: str | None = None) -> Any:
        from src.inference.cohere_gateway import EMBEDDING_MODEL
        from src.inference.sync_adapter import CohereSyncAdapter

        if model_name and model_name != EMBEDDING_MODEL:
            raise ValueError("The embedding model must match the pinned hosted index version")
        return self._adapter(f"dense:{EMBEDDING_MODEL}", CohereSyncAdapter, EMBEDDING_MODEL)

    def get_sparse_embedder(self, model_name: str | None = None) -> Any:
        from src.inference.sync_adapter import PostgresLexicalAdapter

        return self._adapter("sparse:postgresql", PostgresLexicalAdapter, "postgresql-tsvector")

    def get_reranker(self, model_name: str | None = None) -> Any:
        from src.inference.cohere_gateway import RERANK_MODEL
        from src.inference.sync_adapter import CohereSyncAdapter

        if model_name and RERANKER_MODEL_ALIASES.get(model_name, model_name) != RERANK_MODEL:
            raise ValueError("The reranker must match the pinned hosted model")
        return self._adapter(f"reranker:{RERANK_MODEL}", CohereSyncAdapter, RERANK_MODEL)

    def rerank_bounded(
        self,
        query: str,
        documents: list[str],
        model_name: str | None = None,
        batch_size: int = MAX_INFERENCE_BATCH_SIZE,
    ) -> list[float]:
        """
        Reranks query-document pairs in bounded batches using shared TextCrossEncoder singleton.
        """
        if not documents:
            return []
        reranker = self.get_reranker(model_name)
        scores: list[float] = []
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i : i + batch_size]
            if hasattr(reranker, "rerank"):
                batch_scores = list(reranker.rerank(query, batch_docs))
                scores.extend([float(s) for s in batch_scores])
            elif hasattr(reranker, "predict"):
                pairs = [(query, d) for d in batch_docs]
                batch_scores = reranker.predict(pairs, batch_size=batch_size, show_progress_bar=False)
                if hasattr(batch_scores, "tolist"):
                    scores.extend(batch_scores.tolist())
                else:
                    scores.extend([float(s) for s in batch_scores])
            elif callable(reranker):
                pairs = [(query, d) for d in batch_docs]
                batch_scores = reranker(pairs)
                scores.extend([float(s) for s in batch_scores])
        raw_name = model_name or getattr(settings, "RERANKER_MODEL_NAME", DEFAULT_RERANKER_MODEL)
        resolved_name = RERANKER_MODEL_ALIASES.get(raw_name, raw_name)
        key = f"reranker:{resolved_name}"
        if key in self._total_inferences:
            self._total_inferences[key] += len(documents)
        return scores

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
        try:
            from src.services.memory_guard import memory_guard

            memory_guard.invalidate_cache()
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
