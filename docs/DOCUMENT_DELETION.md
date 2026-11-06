# Document deletion and cleanup receipts

PostgreSQL owns document visibility and cleanup state. `document_deletions` is a
content-free tombstone: client, tenant, collection, subject, original object key,
attempts, lease, and safe failure code. It contains no extracted passages or secrets.
Migration 015 enables RLS; browser roles have no grants inherited from the backend
schema. The private Data API surface remains closed.

Deleting a registered document locks its lifecycle identity, fences its ingestion
job, removes scoped pgvector chunks and structured observations, and deletes live
metadata/content in one SQL transaction. Publication takes the same job lock first,
so an old ingestion attempt cannot republish deleted evidence. A database failure
rolls back revocation and vector deletion together.

The API then attempts private object deletion with a bounded timeout. The worker
checks object absence explicitly after deletion. Storage errors, including 401,
403, 429, and 503, never mean absence. Failures retain a pending tombstone and retry
with exponential delay capped at one hour. Each attempt has a 90-second lease and
45-second object-operation deadline. A process restart recovers expired attempts;
concurrent processes use `FOR UPDATE SKIP LOCKED`. Cleanup requires no inference.
The canonical API-image worker handles cleanup and ingestion independently of API
requests. No additional paid service is provisioned.

Delete responses accept logical deletion; physical completion is a separate check:

- Native: `GET /documents/{id}/cleanup`, authenticated as the original owner.
- Versioned API: `GET /api/v1/documents/{external_id}/cleanup`, with a server-only
  client credential, tenant, and delegated collection/subject scope.
- Medical compatibility: `GET /internal/medical-documents/{id}/cleanup`, with the
  same delegated patient scope used by the document operation.

Receipts return `document_id`, `status` (`pending` or `complete`), a fresh scoped
`vectors_remaining` count, verified `object_deleted`, attempts, and safe error code.
They expose neither the object key nor document text. A completed receipt requires
zero physical vectors and verified object deletion. Repeated deletes reuse the
same tombstone. Deleted identities cannot be uploaded or registered again by delayed
delivery; a new document needs a new identity. Registered object bytes cannot be
silently overwritten; versioned replacement is a separate remaining rollout phase.

Both medical delete routes preserve their response contracts. Index deletion also
queues private object cleanup; the legacy subsequent object delete is idempotent.
Deleting an uploaded but unregistered medical object creates the same cleanup
record. Tombstones block new signed URLs and reuploads of deleted medical identities.
Existing signed URLs rely on private object deletion or their original expiry; a
Storage outage can delay physical removal, which remains visible as `pending`.

Verification uses a dedicated synthetic PostgreSQL/pgvector database: stale worker
publication, vector/metadata rollback, duplicate client/document IDs across tenants,
wrong-subject receipt access, delayed registration after deletion, concurrent cleanup
claims, storage outage, and expired-process recovery. API regressions cover all three
route families; HTTP storage tests cover HEAD/range absence and failure behavior.
