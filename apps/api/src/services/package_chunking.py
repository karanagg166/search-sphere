"""Use the independent chunker on existing PyMuPDF/Cohere extraction objects."""

from dataclasses import asdict, replace

from search_sphere_ingest import DocumentPipeline, PipelineConfig
from search_sphere_ingest.normalization import normalize_blocks
from search_sphere_ingest.providers import CallableTokenizer

from src.inference.cohere_gateway import EMBEDDING_MODEL
from src.inference.sync_adapter import CohereSyncAdapter
from src.processing.models.document import ChunkedDocument, DocumentChunk


def chunk_extracted(document, *, manifest_target=None):
    pages = [asdict(page) for page in document.pages]
    checksum = next((block.provenance.get("document_sha256", "") for page in document.pages for block in page.blocks), "")
    title = next((block.provenance.get("document_title", "") for page in document.pages for block in page.blocks
                  if block.provenance.get("document_title")), "")
    normalized = normalize_blocks(pages, checksum=checksum, title=title)
    if manifest_target is not None:
        objects = {item.sources[0].region_id: item for item in normalized.objects}
        manifest_written = False
        for page in manifest_target.pages:
            for index, block in enumerate(page.blocks):
                item = objects.get(block.provenance.get("block_id"))
                if item is None:
                    continue
                structured = {**block.structured, "kind": item.kind, "evidence_id": item.object_id,
                              "normalized": item.model_dump(mode="json"), "pipeline_version": normalized.pipeline_version,
                              "text_offsets": "normalized_paragraph"}
                structured["extraction_status"] = "PARTIAL" if block.structured.get("review_required") else "COMPLETED"
                structured.setdefault("sources", item.model_dump(mode="json")["sources"])
                if item.kind == "table":
                    structured["cells"] = item.model_dump(mode="json")["cells"]
                    structured["segments"] = [
                        {"page_number": segment["page_number"], "data": candidate["structured"],
                         "provenance": candidate["provenance"]}
                        for segment in pages for candidate in segment["blocks"]
                        if candidate["block_type"] == "table" and candidate["structured"].get("evidence_id") == item.object_id
                    ]
                structured[item.kind + "_id"] = item.object_id
                if not manifest_written:
                    structured["document_manifest"] = normalized.model_dump(mode="json", exclude={"objects"})
                    manifest_written = True
                page.blocks[index] = replace(block, structured=structured)
    adapter = CohereSyncAdapter()
    tokenizer = CallableTokenizer(EMBEDDING_MODEL, lambda text: adapter.run(lambda: adapter.gateway.token_count(text, background=True)))
    chunks = DocumentPipeline(PipelineConfig(target_tokens=380, max_tokens=480), tokenizer=tokenizer).chunk(normalized)
    by_region = {block.provenance.get("block_id"): block for page in document.pages for block in page.blocks}
    originals = {item.object_id: by_region[item.sources[0].region_id].provenance for item in normalized.objects if item.sources[0].region_id in by_region}
    reviews = {item.object_id: bool(by_region[item.sources[0].region_id].structured.get("review_required")) for item in normalized.objects if item.sources[0].region_id in by_region}
    output = []
    for index, chunk in enumerate(chunks):
        numbers = sorted({ref.page_number for ref in chunk.sources})
        provenance = [{**originals.get(identity, {}), "evidence_id": identity,
                       "source_object_id": identity, "chunk_id": chunk.chunk_id,
                       "pipeline_version": normalized.pipeline_version, "heading_path": chunk.heading_path,
                       "sentence_ids": chunk.sentence_ids, "paragraph_ids": chunk.paragraph_ids,
                       "sources": [ref.model_dump(mode="json") for ref in chunk.sources],
                       "row_start": chunk.row_start, "row_end": chunk.row_end,
                       "structured_kind": chunk.kind, "review_required": reviews.get(identity, False) or chunk.kind in {"chart", "diagram", "image"}}
                      for identity in chunk.source_object_ids]
        output.append(DocumentChunk(index, chunk.text, chunk.token_count, numbers[0], numbers[-1], numbers, [chunk.kind], provenance))
    return ChunkedDocument(output)
