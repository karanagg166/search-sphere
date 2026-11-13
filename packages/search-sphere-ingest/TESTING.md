# Testing and acceptance

From this package directory, with Python 3.12 and uv:

```sh
uv sync --locked --extra vision --extra opencv
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
uv run --no-sync pytest -v
uv build
uv export --locked --no-dev --extra vision --extra opencv --no-emit-project -o /tmp/ingest-runtime.txt > /dev/null
uv run --no-sync pip-audit --no-deps -r /tmp/ingest-runtime.txt
```

The main CI includes an independent wheel installation and actual native/CV
extraction inside the canonical production image with network disabled and a
512 MiB cgroup. Run the same evaluation from repository root:

```sh
docker build -f apps/api/Dockerfile -t ss-ingest:local .
docker run --rm --network none --memory 512m --cpus 1 \
  -v "$PWD/packages/search-sphere-ingest/evaluation/container_acceptance.py:/acceptance.py:ro" \
  ss-ingest:local python /acceptance.py
```

Synthetic gold fixtures generate native/multilingual/rotated/encrypted/malformed
PDFs, a real cross-page ruled table, a raster scan, two panels and a printed
English OCR image. Optional Tesseract tests use the actual installed executable
and eng traineddata. Gemini SDK responses and hosted Cohere schemas use controlled
transports to test validation, graph integrity, rate limits and uncertainty;
these are not remote model accuracy or availability evidence.

Local evidence on 2026-11-14:

- Package Ruff, format, strict mypy, build and independent Python 3.12 installation
  passed. 41 package tests passed, including real English OCR and native CJK text.
- Gold: 30/30 exact native sentences, 8/8 table values, one logical cross-page
  table, 31/31 source-linked chunks; missing visual provider yields PARTIAL.
- Real native/16 MP CV workload in 512 MiB Linux ARM64: peak 340,459,520 bytes.
  API import/private-native-search gate: 263.0 MiB. API plus 16 MP raster/CV:
  330.8 MiB; hosted response is synthetic and establishes resource behavior only.
- Backend: 362 passed, one isolated-live test skipped in an isolated 512 MiB
  Linux container. PostgreSQL/pgvector: 57 recovery/publication/security/cleanup
  regressions passed. Targeted schema/readiness/monitor/release tests: 49 passed.
- Frontend: 70 tests, typecheck/build passed; four existing lint warnings.
- Locked core/vision/OpenCV runtime: no known audited vulnerabilities.

Tests use disposable PostgreSQL and synthetic fixtures. Failed timing checks
between the macOS and Docker clocks were rerun inside Linux and passed; lease
assertions were not changed. Local dotenv files are excluded from test containers.
The macOS private-search address-space behavior and Rosetta mmap failure are not
worked around by weakening native parser security. Local ARM64 measurements do
not prove native AMD64 or deployed memory; CI/deployment acceptance is separate.

The independent `extract_isolated` API bounds native parsing in a child process.
Thread-based async convenience cannot terminate a running native thread on await
cancellation. The existing app uses its worker thread and container limits, not
per-page durable checkpoints. Expired attempts restart safely through the
application queue and generation fencing.

Live acceptance must separately verify a pinned deployment and actual synthetic
upload, extraction, embeddings, retrieval, grounded answer, citations, deletion
and physical cleanup. The [release report](../../docs/DOCUMENT_INTELLIGENCE_RELEASE.md)
tracks those checks. Handwriting, broad multilingual OCR, chart numerical
accuracy, independent arrow visibility, Docling and optional model resource
benchmarks remain unaccepted. No release acceptance is inferred from mocks.

Main-commit acceptance: `19c8f56be2d7044d123955e196f5536835ab0b30` passed
[CI 43497998763](https://github.com/karanagg166/search-sphere/actions/runs/43497998763),
including native AMD64 image gates, 41 package tests, 364 backend tests (one
skip), 196 durable PostgreSQL tests, 96 worker tests and 70 frontend tests.
See the repository release report for actual deployment and separate live results.

Actual deployed hosted evaluation also passed:
[run 43502742815](https://github.com/karanagg166/search-sphere/actions/runs/43502742815)
returned one chart and one diagram with 2/2 numeric labels, 3/3 node labels and
2/2 directed relations, preserving unverified/review-required flags. The
[real RAG journey](https://github.com/karanagg166/search-sphere/actions/runs/43502734805)
and physical fixture cleanup passed. This small fixture is not broad visual
accuracy acceptance. Both live runs used the existing approved Cohere policy.
