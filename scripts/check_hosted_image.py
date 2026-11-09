"""Production image gate; run inside the built container with --network none."""

import asyncio
import importlib.util
import json
import resource
import sys
from pathlib import Path

import httpx

FORBIDDEN_PACKAGES = (
    "torch", "transformers", "sentence_transformers", "fastembed", "pytesseract", "docling", "qdrant_client", "dramatiq", "pika", "redis",
)
assert all(importlib.util.find_spec(name) is None for name in FORBIDDEN_PACKAGES), "Local inference packages found"
assert not any(Path("/app").rglob("*.onnx")), "ONNX model weights found"
assert not any(Path("/app").rglob("*.safetensors")), "Model weights found"
assert not Path("/app/.env").exists(), "Environment file copied into image"

from src.inference.policy import ProviderNotApprovedError
from src.main import app
from src.processing.chunking.token_counter import TokenCounter
from src.services.model_manager import model_manager

adapter = model_manager.get_dense_embedder()
try:
    adapter.embed(["PUBLIC SYNTHETIC IMAGE GATE"])
except ProviderNotApprovedError:
    pass
else:
    raise AssertionError("Missing scope must deny inference")
assert not any(name in sys.modules for name in FORBIDDEN_PACKAGES)
assert not any(name.startswith(("src.routers.v1", "src.routers.internal_medical", "src.vector_store.qdrant_store", "src.processing.extraction.medical_observation_extractor")) for name in sys.modules), "Retired client adapters loaded"
assert not any(path.startswith(("/api/v1", "/internal/medical")) for path in app.openapi()["paths"])
assert not Path("/app/legacy_test_app.py").exists(), "Test-only client app copied into image"
assert not Path("/app/src/routers/v1").exists(), "Retired client routes copied into image"
assert not list(Path("/app/src/routers").glob("internal_medical*.py")), "Medical routes copied into image"
assert not Path("/app/src/vector_store/qdrant_store.py").exists(), "Legacy vector backend copied into image"
assert TokenCounter().count("PUBLIC SYNTHETIC OFFLINE TOKENIZER") > 0


async def verify_health():
    # No lifespan/worker: DB readiness is verified by the separate PostgreSQL gate.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200 and response.json()["status"] == "ok"


asyncio.run(verify_health())
rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
assert rss_mb < 300, f"Image import/health peak RSS exceeds the 300 MiB pre-deploy gate: {rss_mb:.1f}"
print(json.dumps({"result": "PASS", "local_weights": False, "peak_rss_mb": round(rss_mb, 1)}))
