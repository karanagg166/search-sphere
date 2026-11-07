# Bounded recovery of published documents without usable vectors

Inventory before implementation: `services/ingestion_recovery.py` adopts bounded
legacy pending work, preserving live leases. It deliberately does not reconcile
documents already marked `indexed`/`READY`. `scripts/backfill_pgvector_chunks.py`
predates the hosted-only runtime, has broad source selection and can perform model
processing in the operator process; it is superseded for this rollout.

`security/query_access.py` defines usable vectors using authoritative source
identity, tenant/collection/subject, pinned embedding model and current publication
version. Counting all vectors by external document ID cannot establish that a
particular owner/school/patient has a usable index. `load_ingestion_source` already
verifies current source identity, owner/client and server-owned classification.
The PostgreSQL queue already provides duplicate fencing, restart recovery and
bounded retries, so recovery should enqueue that worker rather than create another
processor.

Plan: inspect at most 20 published documents in one explicit exact scope, without
reading record text or vector values. Produce a private, expiring dry-run receipt
containing IDs, safe reason codes and state fingerprints. Exclude working indexes,
live/pending jobs, deleted identities and unapproved provider scopes. Applying the
reviewed receipt must recheck source state, vector absence, policy and job state
under the existing identity/job/document lock order. Only then reset the durable
job; duplicate or stale applications must not restart it again. The worker still
checks private original availability and current authorization before inference.
No model inference or original download runs in the recovery command. No migration
is needed. Verify scope isolation, changed policies/versions, concurrent apply,
terminal-job adoption and restored retrieval using real PostgreSQL synthetic data.

## Operator flow

Run from the canonical API environment with backend SQL access and the existing
server-only Cohere trial approval configured. No credentials belong in arguments.
The command does not call the provider, reserve inference quota, download originals,
or load local models. Missing approval is reported as `provider_not_approved` and
cannot be applied. The worker later reauthorizes and performs the actual ingestion.

```sh
PYTHONPATH=apps/api python scripts/recover_missing_vectors.py \
  --kind standalone --client search_sphere --tenant '<owner-user-id>' \
  --limit 10 --output /private/recovery.json

PYTHONPATH=apps/api python scripts/recover_missing_vectors.py \
  --kind external --client exam_arena --tenant '<school-tenant-id>' \
  --collection '<collection-id>' --subject '<owner-subject-id>' \
  --limit 10 --output /private/recovery.json
```

An omitted external collection/subject binds exactly SQL NULL, rather than all
collections/subjects. Native recovery always binds the specified owner as subject.
Only published sources without vectors matching the current authority/model/version
are selected. Old-model or stale-version vectors do not establish a usable index.
Each batch is at most 20; `next_after_id` in the receipt and `--after-id` allow an
explicit subsequent dry run without silently scanning the tenant.

The receipt file is created with mode 0600, cannot overwrite a file or follow a
symlink, and must stay outside version control and CI artifacts. It contains scoped
IDs, action codes and state fingerprints; original paths, filenames, passages,
vectors, passwords and keys are omitted. Console output gives counts and the
receipt digest. Review the private receipt before applying within 15 minutes:

```sh
PYTHONPATH=apps/api python scripts/recover_missing_vectors.py \
  --apply /private/recovery.json --digest '<reviewed-digest>'
```

Apply rechecks the exact scope, current source/version/policy and vector count under
identity/job/document locks. Changed/removed sources, restored indexes, revoked
approvals and pending/live jobs are skipped with explicit safe reasons. Approved
sources reset one durable generation; duplicate/concurrent receipt application
cannot reset it again. The unusable old count is cleared so normal queued/processing
states are visible. Cache invalidation shares the transaction. A missing original
or a later provider failure follows the worker's existing bounded failure path.

The current tests use real PostgreSQL/pgvector and synthetic provider adapters to
prove restoration, exact tenant/collection/subject isolation, duplicate apply,
revocation, changed versions, deleted sources, old-model handling and bounded paging.
Receipt tests cover private permissions, overwrites, symlinks and size limits. This
tool establishes a safe recovery mechanism; production scope-specific results must
be recorded separately, without claiming that unapproved private documents recovered.
