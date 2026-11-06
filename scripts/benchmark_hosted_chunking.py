"""Opt-in public-synthetic chunk benchmark. Never reads uploaded documents."""

import asyncio
import json
import os
import time
from urllib.parse import urlsplit

from src.config import settings
from src.db import engine
from src.inference.cohere_gateway import EMBEDDING_MODEL, CohereGateway
from src.inference.policy import inference_scope
from src.inference.sync_adapter import CohereSyncAdapter
from src.processing.chunking.document_chunker import DocumentChunker
from src.processing.chunking.hosted_token_limits import fit_hosted_chunks
from src.processing.chunking.semantic_splitter import SemanticEmbedder, SemanticSplitter
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.models.document import CleanedBlock, CleanedDocument, CleanedPage


def public_fixture():
    sections = [
        ("Maternity leave", "SYNTHETIC MAT-104 gives eligible interns twelve weeks of maternity leave. Marker LEAVE-271.",
         "Eligibility starts on the first working day. Submit the leave request to the synthetic people team. The employee keeps the same role during leave. A manager records the requested dates and confirms the policy number. This public example contains no real company policy or employee information."),
        ("Expense reimbursement", "SYNTHETIC EXP-208 requires expense receipts within thirty days. Marker EXPENSE-842.",
         "The expense reviewer checks the date and the supplied receipt identifier. The requester enters the amount and currency in separate fields. Duplicate receipt identifiers require review. A manager confirms the approved total before reimbursement. This is a fictional training example and contains no financial account information."),
        ("Security incident reporting", "SYNTHETIC SEC-901 requires reporting a lost device within seven minutes. Marker SECURITY-516.",
         "Use the fictional incident form and enter the asset identifier. The security reviewer records a ticket number and the time of the report. The asset team confirms the device status. The example includes no access tokens, passwords, or live contact information. All device identifiers in this benchmark are fictional."),
        ("Geometry course", "SYNTHETIC MATH-305 teaches that a right triangle with sides three and four has hypotenuse five. Marker GEOMETRY-693.",
         "Square each of the two shorter sides and add the results. The sum equals the square of the longest side. Keep the side units consistent before computing. The worked example uses small positive integers and no student information. This public fixture is a fictional class note for retrieval testing."),
    ]
    blocks = []
    labels = []
    for index, (heading, fact, explanation) in enumerate(sections):
        blocks.append(CleanedBlock("text", f"## {heading}", {"block_id": f"public-heading-{index}", "page_number": 1}))
        for part in range(3):
            blocks.append(CleanedBlock("text", f"{fact} {explanation} Example section {index + 1}, paragraph {part + 1}.", {
                "block_id": f"public-section-{index}-{part}", "page_number": 1,
            }))
        labels.append({"query": fact.split(" Marker ")[0], "marker": fact.split("Marker ")[1].rstrip(".")})
    return CleanedDocument([CleanedPage(1, blocks)]), labels


def cosine(left, right):
    return sum(a * b for a, b in zip(left, right, strict=True))


async def run():
    assert os.environ.get("RUN_HOSTED_CHUNK_BENCHMARK") == "1", "Explicit public-fixture benchmark opt-in required"
    database = urlsplit(settings.DATABASE_URL)
    assert settings.ENVIRONMENT == "test" and database.hostname in {"localhost", "127.0.0.1", "host.docker.internal", "postgres"}
    assert database.path in {"/ss_test", "/search_sphere_test"}, "Only an isolated synthetic budget database is permitted"
    source, labels = public_fixture()
    gateway = CohereGateway()
    bridge = CohereSyncAdapter(gateway)
    semantic = SemanticSplitter(embedder=SemanticEmbedder(model=bridge), distance_threshold=0.5, percentile_threshold=80)
    results = []
    stress_results = []
    with inference_scope("synthetic", "public-chunk-benchmark", "public-chunk-benchmark"):
        queries = await gateway.embed([label["query"] for label in labels], query=True)
        for target, maximum in [(500, 700), (350, 400), (400, 480)]:
            started = time.monotonic()
            chunker = DocumentChunker(semantic_splitter=semantic, target_tokens=target, max_tokens=maximum, overlap_tokens=60)
            coarse = await asyncio.to_thread(chunker.chunk_document, source)
            counts = [await gateway.token_count(chunk.content, background=True) for chunk in coarse.chunks]
            fitted = await fit_hosted_chunks(coarse, gateway)
            embedded = await asyncio.to_thread(DenseEmbedder(model=bridge).embed_document, fitted)
            correct = sum(
                label["marker"] in max(embedded.chunks, key=lambda chunk: cosine(query, chunk.embedding)).content
                for query, label in zip(queries, labels, strict=True)
            )
            results.append({
                "target_coarse_tokens": target, "max_coarse_tokens": maximum, "overlap_coarse_tokens": 60,
                "distance_threshold": 0.5, "percentile": 80,
                "coarse_chunks": len(coarse.chunks), "coarse_max_actual_tokens": max(counts),
                "coarse_over_512": sum(count > 512 for count in counts),
                "fitted_chunks": len(fitted.chunks), "fitted_max_actual_tokens": max(chunk.token_count for chunk in fitted.chunks),
                "dense_marker_recall_at_1": correct / len(labels), "query_count": len(labels),
                "elapsed_seconds": round(time.monotonic() - started, 2),
            })
        stress_text = "PUBLIC SYNTHETIC TOKEN BOUNDARY FIXTURE. " + "alpha " * 950 + "PUBLIC-END-MARKER-331"
        stress = CleanedDocument([CleanedPage(1, [CleanedBlock("text", stress_text, {"block_id": "public-token-boundary"})])])
        for target, maximum in [(500, 700), (350, 400), (400, 480)]:
            coarse = await asyncio.to_thread(DocumentChunker(
                semantic_splitter=semantic, target_tokens=target, max_tokens=maximum, overlap_tokens=60,
            ).chunk_document, stress)
            counts = [await gateway.token_count(chunk.content, background=True) for chunk in coarse.chunks]
            fitted = await fit_hosted_chunks(coarse, gateway)
            assert any("PUBLIC-END-MARKER-331" in chunk.content for chunk in fitted.chunks)
            stress_results.append({
                "target_coarse_tokens": target, "max_coarse_tokens": maximum,
                "coarse_chunks": len(coarse.chunks), "coarse_max_actual_tokens": max(counts),
                "coarse_over_512": sum(count > 512 for count in counts),
                "fitted_chunks": len(fitted.chunks), "fitted_max_actual_tokens": max(chunk.token_count for chunk in fitted.chunks),
            })
    await engine.dispose()
    print(json.dumps({
        "embedding_model": EMBEDDING_MODEL, "fixture": "public-synthetic-four-topic-policy-and-class-notes",
        "scope": "chunking-and-dense-retrieval-only", "results": results, "token_boundary_stress": stress_results,
        "latency_note": "Sequential runs share the request token-count cache and provider warmup; this is not a P95 comparison.",
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
