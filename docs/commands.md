# Document intelligence commands

Use the normal repository at `/Users/karanagg/Desktop/Projects/search-sphere`.
No second checkout or runtime is required. Existing commands remain in Makefile.

```sh
cd packages/search-sphere-ingest
uv sync --locked --extra vision --extra opencv
uv run search-sphere-ingest /path/to/public.pdf --output /private/path/result.json
uv run --no-sync pytest -v
uv run --no-sync ruff check .
uv run --no-sync mypy src
uv build
```

From repository root:

```sh
docker build -f apps/api/Dockerfile -t ss-ingest:local .
docker compose -f docker-compose.yml config
python3 scripts/warm_monitor.py --state /private/state.json --config /private/budget.json
```

Do not enable optional warm monitoring until the shared workspace budget and
provider terms are verified. See FREE_TIER_RELIABILITY.md. Installation from a
wheel or pinned Git subdirectory is described in the package README.
