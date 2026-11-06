# Pinned backend release

The trusted-main `release.yml` workflow has three separate phases, each using the
`search-sphere-release` environment restricted to main. Dispatch it with a full
lowercase main SHA after that exact main revision's four required CI jobs pass.
The existing CI runs migrations and ingestion/scope/recovery tests on real
PostgreSQL/pgvector, plus consumer-independent API and hosted-image checks.

1. Admission verifies the exact main CI result, authorized Render workspace/service,
   existing **free** plan, disabled automatic deployment, approved trial-key
   fingerprint, pinned hosted models, public/synthetic data policy and quota cap.
   The selected revision's production migrations run before deployment. Captured
   driver output is withheld; SQL verifies the resulting head, effective browser
   privileges, RLS and private `documents` bucket without selecting record contents.
2. Deployment targets the existing service with an explicit `commitId`. It verifies
   Render's reported commit and the serving `/health` revision. No new service,
   instance or paid plan is created. Read-only platform requests retry bounded
   transient failures; a deploy POST is not repeated after an ambiguous failure.
3. The standalone authenticated synthetic journey runs only after the deployment
   succeeds. It requires protected credentials, checks upload/indexing/unique-marker
   retrieval/grounded citations/SSE completion, then scoped physical cleanup. Signed
   fixture receipts are retained for interrupted-run recovery.

Protected environment secrets are `RELEASE_RENDER_API_KEY`, `RELEASE_DATABASE_URL`,
`RELEASE_COHERE_TRIAL_SHA256`, and the existing `RELEASE_SMOKE_EMAIL`,
`RELEASE_SMOKE_PASSWORD`, `RELEASE_SMOKE_KEY`. Values stay in the secret manager.
The pre-deployment phase provisions only the dedicated signed synthetic account and
its exact user/tenant provider policy. It refuses an existing unrelated, changed or
revoked identity/policy. Approval expires after 30 days and is refreshed only by a
trusted release. It never selects stored password hashes. Ordinary medical/company
content remains ineligible for the trial provider.

`scripts/release_gate.py` targets fixed approved resources. It does not alter Render
configuration. The migration runtime is selected from the admitted release, while
the admission/controller script is preserved from trusted main. A rollback whose
migration head cannot be verified is refused instead of discarding schema history.

This workflow proves only the standalone journey. Quick-Clinic and ExamArena still
need their seeded authenticated journeys; their earlier deployment-contract jobs
are not substitutes. Cold-start/restart and provider-failure production cases,
complete version replacement/cutover, labeled evaluation, and remaining provider/
free-tier account verification stay open in `ROLLOUT_REQUIREMENTS.md`. Dispatching
or passing this workflow alone does not mark the overall rollout complete.

API references: [Render pinned deployment](https://api-docs.render.com/reference/create-deploy),
[service environment updates](https://api-docs.render.com/reference/update-env-var).
