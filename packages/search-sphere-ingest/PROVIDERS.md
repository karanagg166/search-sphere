# Providers

Core dependencies are PyMuPDF, Pydantic, Pillow and tiktoken. The lockfile pins the
complete resolver graph, including optional environments. No model weights ship
in the wheel. `uv sync --locked` installs the core and development tools;
`--extra vision --extra opencv` enables the tested adapter environment. Docling is
separate: `--extra docling` resolves a much heavier layout/table CPU environment.

`Tokenizer.count(text)` must count the actual embedding model's tokens. The
default is `text-embedding-3-small:cl100k_base`. This does not select an embedding
API or spend money. Search Sphere injects its guarded Cohere `/v1/tokenize`
operation for `embed-english-light-v3.0`, retaining the current 384 dimensions.
Tokenization itself consumes provider quota; large documents can exhaust the
existing monthly budget and are retried only through the existing admission policy.

`GeminiVisionProvider` uses the Google GenAI SDK, JSON schema responses, bounded
output, a 30-second request timeout, no SDK retry loop, and explicit model IDs.
`gemini-3.5-flash-lite` is the candidate default, `gemini-3.8-flash` is a selectable
stronger model. Neither activates automatically or falls back to the other.
The adapter requires a SHA-256 fingerprint attesting that this exact key belongs
to a verified billing-disabled/free-tier project. Model listing alone does not
verify billing. Only public/synthetic classifications are supported by this adapter.
The local 10-call default/32-call ceiling and interval gate are per instance;
multi-instance hosts must supply a shared admission policy. This gate does not
replace Google's project-wide quotas and cannot detect a later billing upgrade.

```python
import os
from search_sphere_ingest import DocumentPipeline
from search_sphere_ingest.gemini import GeminiVisionProvider

# Set these only after checking the project is on the free tier, billing is
# disabled, and the document's data classification is approved for these terms.
vision = GeminiVisionProvider(
    os.environ["GEMINI_API_KEY"],
    approved_free_key_sha256=os.environ["GEMINI_FREE_KEY_SHA256"],
    classification="public",
)
try:
    assert vision.check_capability()
    document = DocumentPipeline(vision=vision).extract("public.pdf")
finally:
    vision.close()
```

Remote interpretation is PARTIAL until independent OCR/geometry evaluation exists.
Model-reported exactness is never accepted as verification. Diagram arrows are
not marked visible merely because the model says they exist. Prompt instructions
are fixed and uploaded image content is explicitly treated as untrusted evidence.
The adapter supplies no tools, retrieval URLs or code execution capabilities.

`TesseractOCRProvider("eng")` uses a discovered executable and installed language
data, a private temporary directory, a 15-second subprocess timeout, a single CPU
thread and bounded TSV. It maps word boxes back to the original source rectangle.
There is no claim of handwriting support. Alternative OCR implementations can
implement `OCRProvider`; detection and recognition must not be conflated.

`DoclingLayoutProvider(artifacts_path=..., memory_budget_mb=2048)` refuses the
512 MiB profile. It uses pre-fetched artifacts, accurate TableFormer, picture
classification, optional OCR, and no picture-description/local VLM pipeline.
2048 MiB is an admission floor, not a measured sufficiency guarantee. An
OCR-disabled scanned page always records an issue and gets a visual fallback if
the initial pass omitted it. No free Docling hosting endpoint is assumed.

`AssetStore.put(id, bytes, media_type)` receives opaque private crop IDs.
`ProgressSink` receives stage/page/region identity, never document text. A host
must add authorization, persistence, shared quotas and cancellation appropriate
to its own application. Third-party provider `name` should include model and
configuration identity when used for repeatable processing versions.

Unavailable: Grounding DINO, PaddleOCR variants, TrOCR, Cloud Vision, Granite
Vision and PP-Chart2Table do not have verified free account/hosting acceptance in
this implementation. They are not automatically downloaded or represented as
working production fallbacks.

## Existing hosted Cohere integration

Search Sphere's `HostedLayoutRecognizer` uses the approved `CohereSyncAdapter`;
its strict `PageLayout` schemas now live in this library. Native text requires no
VLM. Visual crops use `command-a-vision-07-2025`; retrieval keeps
`embed-english-light-v3.0` (384 dimensions, 512-token input), `rerank-v3.5` and
`command-a-03-2025`. All existing classification/key-fingerprint/quota policies
remain enforced. Trial approval covers public/synthetic fixtures; private uploads
must not bypass the existing sensitive-data policy. Library installation grants
no provider permission. The core has no application-specific Cohere credentials.
A host may inject its own approved `VisionProvider`/`OCRProvider`/`Tokenizer`.

The [Cohere trial documentation](https://docs.cohere.com/docs/rate-limits) states
1,000 calls/month; Search Sphere retains a 900-call application cap. Vision chat
is 20 requests/minute, rerank 10/minute, tokenize 100/minute, embed 2,000
inputs/minute in the published trial table checked 2026-11-14. Account availability
and remaining quota require actual live checks. Every exact tokenize request
counts toward protected admission. No paid fallback is enabled.
