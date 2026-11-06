# Published index provenance

Migration 021 adds `document_versions` and a nullable active-version pointer on
native and delegated documents. The catalog has RLS enabled without browser grants.
It records the original private object identity, actual downloaded-byte SHA-256,
embedding model, chunk count and successful job generation. Each successful
publication has a new UUID and monotonic number under the existing job lock.

The pipeline computes the byte hash before parsing. A successful publication writes
the catalog, tags every new vector with its version/source hash, sets the active
pointer, invalidates retrieval reuse and completes the job in one transaction.
A failed insert rolls back the new version and leaves the previous index/pointer
searchable. Re-delivering a completed lease cannot create another version. Structured
evidence carries the publication version in its data. Historical metadata is not
invented: legacy documents remain without a version until a successful publication;
injected test processors without original bytes report an unknown hash.

Dense, lexical and candidate authorization accept only the active version. Each
provider authorization check also compares the selected evidence's version. Native,
versioned and compatible medical search/answer citations expose version IDs where
available. Conversation references and compact memory carry version and message IDs.
An old citation cannot silently bind to the same chunk position in a new publication,
even when the text is identical. Stale historical answers are currently withheld.

Authorized metadata listings use `GET /documents/{id}/versions` and
`GET /api/v1/documents/{id}/versions`, with `limit` (1–100) and `offset` pagination.
The parent document's normal authorization applies; private object keys are omitted.
Deletion revokes the document/evidence and removes its version catalog in the same
transaction, before the existing durable object cleanup.

This phase records successful index publications of the currently registered object.
The changed-file upload contract, independent staged originals, retained historical
evidence and bounded reindex/cutover controller are still open. It does not permit
overwriting a registered object's bytes. Those changes need additional storage-intent
and version lifecycle work before the replacement feature can be marked complete.

Real PostgreSQL/pgvector regressions cover original-byte hashing, vector/active
identity, publication rollback and continued old-index retrieval, new-version
numbering, stale-citation refusal, dense/lexical exclusion of stale versions,
duplicate delivery, authorized paginated metadata and deletion. Existing generic
HTTP ingestion also verifies the delegated version listing after publication.
