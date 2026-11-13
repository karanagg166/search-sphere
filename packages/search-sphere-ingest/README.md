# search-sphere-ingest 0.1.0

An independent Python library for bounded PDF/image extraction and source-aware chunking.
Native text and ruled tables run without API keys. Missing visual providers produce
explicit partial results. This is an initial release candidate, not an accepted production release.
The distribution uses AGPL-3.0-or-later to remain compatible with PyMuPDF's
open-source license; review LICENSE and THIRD_PARTY.md before redistribution.

```sh
uv sync --locked
uv run search-sphere-ingest sample.pdf --output result.json
```

```python
from search_sphere_ingest import DocumentPipeline

pipeline = DocumentPipeline()
document = pipeline.extract("sample.pdf")
chunks = pipeline.chunk(document)
result = pipeline.ingest("sample.pdf")
assert all(chunk.source_object_ids for chunk in chunks)
```

The default tokenizer is the real `cl100k_base` tokenizer for
`text-embedding-3-small`; no embeddings are generated. Inject a `Tokenizer` for a
different embedding model. The Search Sphere adapter uses Cohere's actual token
endpoint and preserves the existing embedding dimensions.

The CLI writes a new private file, refuses overwrites, and exits 2 for partial
extraction. It does not upload anything. Keep the original PDF: source references
use its SHA-256, page and unrotated coordinates. Inject an `AssetStore` for private
persistent crops. See [ARCHITECTURE.md](ARCHITECTURE.md), [PIPELINES.md](PIPELINES.md),
[PROVIDERS.md](PROVIDERS.md), [FREE_TIER.md](FREE_TIER.md), and [TESTING.md](TESTING.md).

Install independently from a reviewed Git commit (replace REVISION):

```sh
uv add "search-sphere-ingest @ git+https://github.com/karanagg166/search-sphere.git@REVISION#subdirectory=packages/search-sphere-ingest"
# Or from the built wheel:
uv add /path/to/search_sphere_ingest-0.1.0-py3-none-any.whl
```

`pipeline.extract_image("public.png")` and `await pipeline.aextract_image(...)`
share the same normalized schema. Images without configured visual/OCR capability
return PARTIAL and retain source coordinates; no invented transcription is returned.
Inject `DirectoryAssetStore` for private local crops, or an application-owned
persistent asset adapter. Core native extraction sends no network requests;
initial tiktoken vocabulary acquisition may require internet unless cached.
