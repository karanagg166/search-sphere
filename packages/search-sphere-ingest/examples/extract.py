"""Run: uv run examples/extract.py public.pdf (prints metadata, not document content)."""

import sys

from search_sphere_ingest import DocumentPipeline

result = DocumentPipeline().ingest(sys.argv[1])
print(
    {
        "status": result.document.status,
        "pages": len(result.document.pages),
        "chunks": len(result.chunks),
    }
)
