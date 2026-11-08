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
# Native receipt integrity

Fresh standalone smoke manifests use format 2. The protected fixture key signs
the full manifest, binding document/conversation IDs, owner, signed names, cleanup
states and timestamps. Reads reject changed signatures, nonprivate files, links,
nonregular files and oversized manifests. Writes use a private exclusive temporary
file, fsync and atomic replacement; a failed write preserves the prior pending
receipt. The bounded manifest refuses overflow rather than discarding unresolved
entries. Account emails and credentials are never persisted.

Unsigned format-1 files cannot be silently trusted or reset. Trusted historical
artifact admission, validated legacy reconciliation, cross-run restore and actual
reverification of restored pending receipts remain follow-up work. This integrity
primitive alone does not establish recovery across fresh GitHub runners. Existing
signed filename/title catalog discovery still handles a lost upload response when
the source remains listed.

The smoke controller now replays signed pending document/conversation IDs after
authenticating their recorded owner, before catalog discovery or fresh uploads.
A missing catalog row leads to scoped physical cleanup verification, never an
assumed success. A live row must still match the signed identity/name/owner.
Unavailable metadata or receipts, including 503, fail the gate and preserve pending
provenance. Catalog discovery itself persists receipts before deletion, closing the
interruption window between revocation and physical cleanup. Rediscovered sources
require fresh verification even if an older receipt was complete. An initial
private signed manifest is persisted before health checks. Trusted GitHub artifact
restoration onto a fresh runner and legacy format reconciliation remain separate
open requirements.

Legacy recovery preparation uses `services/release_scope_audit.py`. It verifies
the dedicated synthetic owner's identity using the protected ownership key and
performs only aggregate SELECTs. It refuses outstanding native documents, threads,
vectors, active jobs, pending object/crop cleanup, structured evidence or published
versions. The private Storage catalog must contain no original under the exact
owner prefix and no original/crop belonging to its retained tombstones; orphan
uploads are included. Unrelated users and objects are neither read nor changed.
Only the dedicated owner ID and zero counts can be returned. The caller must hold
a bounded repeatable-read, read-only transaction and serialize fixture journeys.

Twelve real PostgreSQL/pgvector cases verify scope isolation, SELECT-only execution,
changed/revoked ownership, outstanding work, public buckets and orphan originals or
crops. This is an audit primitive: a trusted operator CLI/workflow, signed legacy
reconciliation baseline and fresh-runner restoration still need implementation and
live execution. It does not itself authorize skipping any missing receipt.

The separate `native-fixture-reconciliation.yml` operator workflow runs only as a
main dispatch in the existing protected release environment, sharing the native
smoke concurrency group. It requires successful exact main CI for its tooling and
the serving revision, verifies live health/readiness, and validates the approved
Supabase SQL target. The database transaction is repeatable-read and read-only,
with 15-second statement and 5-second lock deadlines. There are no model calls,
API mutations, seed operations, migrations or cleanup writes.

Only a successful empty-scope audit can produce `baseline.json` in a private
exclusive runner path. Its signature binds the synthetic owner, project, database
target fingerprint, tooling/API SHAs, run cutoff, timestamp and all eight zero
counts. The required artifact is retained for thirty days. Unknown fields, altered
identity, changed signatures, partial/nonzero/Boolean counts and wrong keys fail
verification. Thirty offline admission/proof cases and the existing receipt
group pass (133 focused cases total); real PostgreSQL audit coverage is separate.
No baseline has been executed or installed by adding this workflow. Trusted
artifact origin validation and fresh-runner restore remain the next integration.

Before choosing a historical cutoff the operator checks every active predecessor
state for both native release workflows. A queued/active older run could otherwise
upload after the empty-scope snapshot. Incomplete or oversized metadata fails
closed; future queued run IDs remain outside the cutoff and the shared smoke lock
holds their mutations until the audit finishes. The serving revision is rechecked
before persisting the proof. The supported status filters are documented in
[GitHub's workflow-run API](https://docs.github.com/en/rest/actions/workflow-runs).
# Native runner receipt restoration

Both native post-deployment paths restore signed cleanup receipts before any new
upload. They require a protected main dispatch, exact successful main CI for the
workflow source and serving API, and a signed read-only operator baseline. The
baseline's workflow identity, source revision, run ID, timestamp, fixed synthetic
owner/project and zero SQL/Storage counts must agree. The successful operator run
[42472824150](https://github.com/karanagg166/search-sphere/actions/runs/42472824150)
provides the first historical reconciliation proof; it does not approve ordinary
accounts or sensitive documents.

Complete bounded main history is inspected for both native workflows. A run with
an old ID that was rerun after the baseline still requires its receipts. Active
predecessors block new mutation. Missing receipts are acceptable only when the
exact upload step is confirmed skipped; expired, unsigned, conflicting or
oversized receipts require a new read-only reconciliation. A rerun whose run ID
predates the baseline must be replaced with a fresh dispatch.

Artifacts are downloaded through authenticated GitHub CLI redirects, with a
45-second download deadline, 512 KiB archive bound and 256 KiB JSON bound. A
single regular expected entry is read in memory; traversal, links, extra entries,
duplicate JSON fields and malformed signatures are rejected. The private atomic
manifest retains every signed pending document/thread, up to 128 resources.
Pending state wins over later copied completion rows. The authenticated smoke
then independently rechecks physical cleanup, including IDs no longer listed.

History must remain below 1,000 runs per workflow, and artifacts are retained for
30 days. Bounds fail closed. To establish a fresh baseline after an expired or
legacy receipt, wait for prior native attempts to finish, then dispatch
`native-fixture-reconciliation.yml` on main with the exact serving SHA. Its
read-only dedicated-scope audit must pass before a new native smoke dispatch.
An audit that finds leftovers remains open for scoped ownership reconciliation;
it must never be bypassed by deleting journal entries.

Local validation covers 177 transport, provenance, admission, historical rerun,
receipt replay and smoke cases. The new restoration path still requires its own
successful trusted live workflow; local success does not mark the rollout complete.

