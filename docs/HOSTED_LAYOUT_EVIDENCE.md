# Hosted layout, chart evidence, and private crops

The canonical PostgreSQL ingestion pipeline uses PyMuPDF for native text, grid tables,
coordinates, and deterministic reading order. Image/scanned areas and remaining vector
graphics clusters are rendered at bounded resolution and sent to the approved hosted
Cohere vision model. Native table regions are excluded. Low-contrast grayscale scans
may use deterministic contrast/denoising preprocessing; chart colors are retained.

The hosted response is untrusted JSON. A strict schema accepts only text, image,
table, and chart regions, finite in-bounds rectangles, rectangular table rows, optional
validated column geometry, and explicit chart readability enums. Unknown keys and
verification claims, overlapping regions, and malformed/truncated responses fail closed.
The image's text is evidence and cannot supply tool or system instructions.

Text retains visible headings/paragraphs/lists. An image keeps OCR and its generated
description in one logical block and associates both with its private source crop.
Tables retain full rows/headers/units/captions separately from row-group chunks.
Continuations merge only when explicit column geometry, headers, margins, captions,
and page adjacency agree. Missing geometry prevents automatic merging.

Chart evidence retains title, axes, units, scale, legend, series, and visible values.
Unreadable values become null. Readability/approximation flags remain attached to
individual points. Every hosted point is server-marked `verified=false`; a model
cannot self-authorize exact comparisons. Small charts remain one logical chunk;
large charts split only between points with common context and stable chart ID.
Generated/unreviewed OCR, captions, and chart estimates do not enter the exact
clinical observation lookup path. Exact chart-comparison support awaits a reviewed
value source; it is not claimed by this ingestion slice.

PDF-region and image-coordinate provenance retains page, source hash, original order,
region rectangle, and extraction method. Private PNG crops are limited to 8 MiB per
document, 32 hosted regions, and 4 MiB per hosted image. Before upload, the worker
persists a `document_deletions` row of kind `asset` in `STAGED` state under its live
job fence. Publication changes selected crop intents to `PUBLISHED` atomically with
evidence/vectors; failed or interrupted work leaves recoverable staged intents.
Crops are never publicly served. Live document authorization is required before
`/documents/{id}/evidence/{evidence_id}/crop` or the corresponding `/api/v1` route
returns a five-minute signed URL.

Cleanup retains live staged crops, recovers orphaned crops after their ingestion
lease ends, and removes old crops after successful reindexing. Deletion fences the
job and queues all associated crops; a 180-second delay covers an in-flight bounded
Storage upload before cleanup verification. The cleanup receipt stays pending until
original object, physical vectors, and every derived crop are verified gone. The
smoke gate polls valid pending receipts for at most 240 seconds, and fails immediately
on HTTP errors, malformed receipts, or nonzero vectors. No provider is involved in
cleanup. Existing signed URLs remain subject to object removal or their initial
expiry during a Storage outage.

Verification includes strict-response and source-value unit tests plus real
PostgreSQL crop publication, reindex, deletion, orphan recovery, and live-lease
protection. Public synthetic live probes on 2026-11-06 preserved three table rows
and values in 3.90 seconds and two labeled bar-chart values in 5.34 seconds. Both
retained review status; chart exact comparisons remained disabled. These small
fixtures establish model availability and contract behavior, not general OCR/chart
accuracy or production P95 latency. Broader labeled evaluation remains open.
