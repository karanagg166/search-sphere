# Permission-aware retrieval cache

`retrieval/permission_cache.py` wraps the shared reranked hybrid retriever used by
native and versioned document search/answers. PostgreSQL `retrieval_cache`
(migration 020, RLS enabled, no browser grants) stores only signed point IDs, chunk
row IDs, content hashes and scores. It stores no queries, passages, answers or
conversation text. Generation still runs with fresh, authorized evidence.

The HMAC key binds the query/options, client, tenant, authenticated actor, resource
scope, operation, conversation, selected message hashes, routing policy, data
classification, provider eligibility and pinned embedding/rerank models. Delegated
calls without an actor skip caching. Native calls use the authenticated user.
Changing the signing secret invalidates prior entries.

A bounded manifest of authorized chunk identities, source update times and version
IDs binds the searchable dataset. Reindexing creates new row IDs. Adding or removing
evidence changes the key, including for cached empty results. Logical version IDs
are included when present; complete document replacement is a separate rollout item.
Scopes above 10,000 chunks use uncached retrieval rather than build an unbounded key.

Every hit checks current account/service permissions, conversation access, message
hashes, classification and provider eligibility, then re-fetches source rows using
the authoritative scoped SQL query. Content/row hashes and the current manifest
must agree. Candidates undergo the same credential screening and final permission
check as uncached retrieval. Cached references cannot revive revoked evidence.

Publication and deletion evict the entire affected client/tenant cache in their SQL
transaction. Rollback preserves the old entries. Other scope/policy changes make
entries unusable through key changes or fresh authorization. Entries expire after
15 minutes; expired rows are removed on writes or attempted reuse. An advisory lock
serializes insertion/eviction to enforce a global 128-entry bound. Each entry is at
most 16 KiB and 20 references (roughly 2 MiB payload ceiling, excluding SQL overhead).
No Redis service, model, extra infrastructure or inference is required for caching.

`scripts/tests/test_retrieval_cache_postgres.py` verifies a repeat search invokes
the hybrid stage and reranker once across two requests, while source text is fetched
on both requests. This demonstrates call avoidance with synthetic adapters, not a
measurement of production latency or provider capacity. Tests also exercise actor
and conversation isolation, context changes, source mutation, newly indexed chunks,
account/client/provider revocation, trial approval, tampering, expiration, deletion,
rollback and concurrent capacity bounds against real PostgreSQL/pgvector.
