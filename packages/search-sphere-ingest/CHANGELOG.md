# Changelog

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
