# Package agent rules

- This directory is the independent `search-sphere-ingest` distribution.
- Use uv; maintain uv.lock. Use a src layout and Pydantic schemas at boundaries.
- Core must not import Search Sphere's `src`, DB models, auth, routes or vector store.
- No local generative models. Optional CPU layout/OCR dependencies require measured
  resources; never enable them in the free Render image by assumption.
- Never turn missing/uncertain OCR, figure boundaries, values or arrows into success.
- Original bytes and coordinate transformations must remain accessible.
- Every chunk must identify its complete source object; preserve relationships.
- Exact tokenizer budgeting must match the embedding provider; no silent truncation.
- Run package Ruff, strict typing, tests, build/install and runtime audit. Changes to
  application integration also need its PostgreSQL/security and backend regressions.
- Mock SDK tests establish contracts only. Record real inference evidence separately.
- Use only synthetic fixtures; do not log private text, provider bodies or secrets.
- Do not publish, commit, push or change production resources without authorization.
- Do not mark the master release accepted until the documented live gates pass.
