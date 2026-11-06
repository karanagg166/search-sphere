"""Production image gate; run inside the built container with --network none."""

import asyncio
import importlib.util
import json
import resource
import sys
from pathlib import Path

import httpx

FORBIDDEN_PACKAGES = (
    "torch", "transformers", "sentence_transformers", "fastembed", "pytesseract", "docling",
)
assert all(importlib.util.find_spec(name) is None for name in FORBIDDEN_PACKAGES), "Local inference packages found"
assert not any(Path("/app").rglob("*.onnx")), "ONNX model weights found"
assert not any(Path("/app").rglob("*.safetensors")), "Model weights found"
assert not Path("/app/.env").exists(), "Environment file copied into image"

from src.inference.policy import ProviderNotApprovedError
from src.main import app
from src.services.model_manager import model_manager

adapter = model_manager.get_dense_embedder()
try:
    adapter.embed(["PUBLIC SYNTHETIC IMAGE GATE"])
except ProviderNotApprovedError:
    pass
else:
    raise AssertionError("Missing scope must deny inference")
assert not any(name in sys.modules for name in FORBIDDEN_PACKAGES)


async def verify_health():
    # No lifespan/worker: DB readiness is verified by the separate PostgreSQL gate.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200 and response.json()["status"] == "ok"


asyncio.run(verify_health())
rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
assert rss_mb < 300, f"Image import/health peak RSS exceeds the 300 MiB pre-deploy gate: {rss_mb:.1f}"
print(json.dumps({"result": "PASS", "local_weights": False, "peak_rss_mb": round(rss_mb, 1)}))
