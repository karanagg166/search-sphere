# Changelog

## 0.3.0 — 2026-11-15

- Fixed real-world PDF stream filter parsing (supporting standard filter arrays and safe
  PDF filters), separated `/Image` stream decompression bounds from text/content zip-bomb
  limits, and recorded `repaired_pdf_requires_review` as `PARTIAL` instead of crashing.
- Capped `native_vector_paths` at 1,000 per region with `native_vector_paths_truncated`
  uncertainty instead of failing on complex vector figures.
- Added `strategy="lines"` fallback in `native_tables` with false-positive guards,
  reconstructed merged-cell spans (`row_span`/`column_span`) with `PARTIAL` status,
  and relaxed cross-page `column_edges` tolerance to `0.025` for duplex margins.
- Improved `reading_order` with 2pt overlap tolerance and bold-heading detection.
- Added `estimate_skew_degrees` in `panels.py`, expanded `propose_panels` contour limit
  to 32, and added >4MP downscaling in `TesseractOCRProvider` up to 16MP.
- Optimized `Chunker._visual` to pack chart/diagram details up to `target_tokens`/`max_tokens`
  and split text-heavy OCR into sentence-aware subchunks.
- Added `evaluation/real_world_eval.py` and `evaluation/corpus/` (including arXiv CC-BY
  `1706.03762` pages 1-8, multi-column, English+Hindi, rotated/skewed, merged-cell, and
  local OCR/CV benchmarks).

## 0.2.0 — 2026-11-15

- Improved multi-column `reading_order` to peel off short title/footer bands before
  vertical column partitioning (`reading_order_accuracy` 1.00 on gold multi-column fixture).
- Extended OpenCV `propose_panels` to detect horizontal full-width whitespace gutters and
  2x2 grids in addition to vertical gutters while preserving parent geometry.
- Lowered cross-page table continuation bottom-of-page threshold from `0.80` to `0.72`
  to support tables above page footers while preserving strict column-edge and header checks.
- Added reproducible `evaluation/gold_benchmark.py` and `tests/test_gold_evaluation.py`
  covering CER/WER, reading order, table cells, cross-page merge precision/recall,
  multi-axis panel detection, chart/diagram/image chunking, and source-link completeness.
- Added `[project.urls]` metadata in `pyproject.toml` and verified clean-venv wheel
  installation with custom tokenizer injection.

## 0.1.0 — unreleased

- Independent typed PDF extraction/chunking package, public sync/async API and CLI.
- Native preparation, source coordinates, sections, sentence offsets and table cells.
- Conservative table continuation with whole-object provenance and row chunking.
- Optional Docling, GenAI, OpenCV and Tesseract adapters; explicit partial outcomes.
- Private immutable local result storage and isolated Linux extraction worker.
- Opt-in Search Sphere bridge using existing scoped evidence, tokenizer and indexing.
- Gold native evaluation, regression tests, package CI and bounded container checks.
- Require Pillow 13 to remove vulnerabilities in the previously selected 12.x range.

Not published or accepted for the complete production document-intelligence scope.

- Integrated the existing guarded Cohere extraction path with shared normalization
  and specialized chunking; no alternative application ingestion engine.
- Added standalone image APIs, strict diagram node/edge/label schemas, OCR region
  language/style and merged-cell input; uncertain arrows/values stay unverified.
- Preserved parent crops when hosted panel separation is unresolved; bounded
  OpenCV proposals run on at most 1400-pixel sides and map to original geometry.
- Added private immutable local crop storage, source JSONB links and deletion tests.
- Added bounded database recovery, worker poll backoff, safe frontend read retries
  and a default-disabled shared-budget monitor.
