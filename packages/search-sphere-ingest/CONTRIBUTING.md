# Contributing

Use uv and Python >=3.12. The checked-in uv.lock reproduces the tested dependency
environment; do not install model extras into a small production API image.
Use pnpm for the repository's existing frontend. Follow root AGENTS.md and the
explicit task scope. Keep modules focused and changes reviewable.

New behavior needs a meaningful regression test. Use synthetic public fixtures
with known text, boxes and values. Never use real clinical records in tests or
logs. Add real-input/provider evidence separately from mock transport tests and
label unavailable capabilities honestly. Any new optional model needs its exact
license, measured memory, provider cost/eligibility evidence and quality benchmark.

Never change model/dimensions/vector providers or rebuild records implicitly.
Keep content objects storage independent. Validate external provider JSON, preserve
original objects, and fail explicitly rather than silently truncating evidence.
New persistence adapters must enforce tenant isolation, immutable originals and
safe cleanup; core namespace parameters are not a replacement for authentication.

Before proposing a release, run TESTING.md and existing backend/PostgreSQL/frontend
checks. Build/install the wheel independently, evaluate the production image under
its memory ceiling, then verify the actual deployed commit. Publishing to PyPI,
commits, pushes, merges and production changes follow the owner's authorization
and repository release gates. No credentials are bundled with examples.
