# Search Sphere: Live post-deployment smoke tests

The GitHub Actions workflow .github/workflows/post-deploy-smoke.yml runs after successful Search Sphere CI on main for changes to deployed application files. It can also be run manually on main. The runner uses the Python standard library, with a 10-minute job timeout.

## Live checks (against real production)

- Render GET /health: HTTP 200, JSON status=ok, valid deployed git_commit. The deployed backend commit must match the expected main commit, or be its ancestor with zero changes to apps/api or render.yaml in between. This rejects false greens from an older surviving Render process.
- Render process memory below 420 MiB, and memory pressure not high/critical.
- Render GET /ready: HTTP 200, status=ready, database=connected, and matching live SHA. The existing endpoint executes a real PostgreSQL SELECT 1.
- Render GET /health/memory: HTTP 200, status=ok, and populated scheduler metrics.
- OPTIONS /search: CORS preflight accepts https://search-sphere-rose.vercel.app and POST with Authorization and Content-Type.
- Anonymous GET /documents and POST /search must return HTTP 401 or 403; neither changes any data.
- Vercel production homepage returns HTTP 200.
- Optional one real authenticated semantic POST /search against a dedicated synthetic test document.
- Optional one real grounded POST /answer, only when explicitly enabled on a manual run. This may consume LLM API quota.
- Final health and memory check after requests.

The job does not use real user conversations, create or delete customer data, submit payments, or run stress tests on production.

## Enable real authenticated RAG verification

Create a dedicated non-admin test user and upload/index a harmless synthetic document owned by that user, with text such as:

    The unique CI verification code is SS-SYNTHETIC-SMOKE-42.

Configure three repository Actions secrets under Settings > Secrets and variables > Actions:

- PROD_SMOKE_BEARER_TOKEN: an appropriately scoped **test-only** user bearer token. Never use a Supabase service-role key, administrator token, or actual user credential.
- PROD_SMOKE_DOCUMENT_ID: the ID of the synthetic indexed document.
- PROD_SMOKE_EXPECTED_MARKER: the unique marker text, e.g. SS-SYNTHETIC-SMOKE-42.

A configured workflow sends the question "What is the unique CI verification code in the synthetic test document?" to POST /search, scoped to the synthetic document. It requires an actual nonempty response containing the expected source ID and marker. Real customer data is never used.

A token can expire; use short-lived scoped credentials with a safe rotation/renewal process. Do not create a never-expiring administrator JWT.

If all three secrets are missing, the workflow emits an explicit NOT RUN RAG warning: this is **not full RAG acceptance**. If only a subset is configured, the check fails. Set require_rag=true on workflow_dispatch to fail when the full fixture is missing.

The optional run_answer=true input calls POST /answer once and verifies a nonempty response with a citation to the synthetic document. This can consume paid/limited LLM quota. Do not run it automatically on each push.

## CI/resource controls

- Triggered by successful CI on main; no additional full test suite per PR.
- Automatic runs skip documentation-only and workflow-only changes.
- Superseded runs are canceled.
- Up to four minutes of bounded Render readiness polling; stale backend SHA is never accepted.
- GitHub Summary distinguishes PASS, FAIL, and NOT RUN checks.
- No test secrets printed in logs.
- A simple smoke cannot prove the service withstands sustained 512 MiB load: separately test memory contention in an isolated constrained container and monitor Render OOM events.

## Separate checks and limitations

- Vercel frontend HTTP 200 does not verify the frontend deployed SHA; inspect the Vercel production deployment's commit separately.
- Cross-project keep-alive tests are independent. Their Exam Arena worker heartbeat failures must be resolved in ExamArena rather than being counted as a passing Search Sphere release.
- A green workflow without configured synthetic credentials means core production health/contract checks passed, NOT that semantic retrieval or generation passed.
