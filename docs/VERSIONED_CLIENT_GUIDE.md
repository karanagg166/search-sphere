# Integrating a server application through `/api/v1`

Use HTTPS requests to `https://search-sphere-api.onrender.com/api/v1` from your
application server. Consumers do not import Search Sphere's Python modules, read
its tables, access its bucket directly or receive a Supabase secret. `/openapi.json`
describes the deployed request/response schemas. The compatibility medical routes
are documented separately; new clients should use this versioned surface.

## Register and delegate

An operator registers a separate service client for each application, with explicit
tenant IDs and only its required scopes. Use the dry-run-first
[bootstrap command](CLIENT_BOOTSTRAP.md), or an independent `clients:manage` platform
credential with `POST /api/v1/clients`. For example, the registration body is:

```json
{
  "client_id": "future_app",
  "name": "Synthetic integration example",
  "authorized_tenants": ["synthetic_tenant"],
  "allowed_scopes": [
    "documents:read", "documents:write", "documents:delete",
    "collections:manage", "search:execute", "answers:generate",
    "conversations:read", "conversations:write"
  ]
}
```

The registration/rotation response reveals the key once. Store it in a server-only
secret variable; omit the key/response from logs, browser bundles and CI artifacts.
Do not grant a consuming application `clients:manage` or wildcard scopes/tenants.
An example direct operator bootstrap (the key and SQL credential come from its
private environment) is:

```sh
python scripts/register_service_client.py \
  --client-id future_app --name 'Synthetic integration example' \
  --tenant synthetic_tenant \
  --scope documents:read --scope documents:write --scope documents:delete \
  --scope collections:manage --scope search:execute --scope answers:generate \
  --scope conversations:read --scope conversations:write
```

Review the dry-run result before adding `--apply`. Key rotation requires updating
the consumer's secret; revocation denies subsequent operations and ongoing inference
reauthorization. Existing bootstrap identities require explicit replacement, and
revoked identities cannot be reactivated by the bootstrap command.

The consumer first authenticates its person and authorizes the target resource.
Quick-Clinic owns doctor/patient assignments; ExamArena owns school enrollment and
class membership. Derive the following headers in server code after those checks:

| Header | Meaning |
| --- | --- |
| `Authorization: Bearer <server credential>` | Registered application and granted operations |
| `X-Tenant-ID: synthetic_tenant` | An explicit tenant allowed by that credential |
| `X-Actor-ID: synthetic_actor` | The authenticated person; required for conversations |
| `X-Collection-ID: synthetic_collection` | The authorized collection, when applicable |
| `X-Subject-ID: synthetic_subject` | The authorized evidence subject, when applicable |

Search Sphere checks client/tenant/collection/subject scope on every operation and
actor ownership on conversations. A body/query scope cannot override a conflicting
header. The credential authorizes the consumer to delegate these identities; it
does not establish the consumer's doctor assignment or enrollment rules. Never
forward unverified browser-supplied scope headers. Recheck consumer access before
returning evidence, including when membership changes during a request.

For collection metadata, `X-Collection-ID` bounds create/get/delete to that exact
collection and filters list rows/counts before pagination. A conflicting body/path
returns 403. A subject-scoped collection list must also delegate its authorized
collection; omitting that bound is rejected. Tenant-wide `collections:manage`
listing remains available to an authorized server request without subject or
collection bounds. Collection metadata is client/tenant-owned; document subject
ownership is rechecked separately on document and evidence operations.

Inference eligibility is separate: an operator must review classification and add
an expiring, exact-scope `inference_data_policies` approval. An upload's metadata
cannot approve itself. Missing approval defaults to confidential (medical for
Quick-Clinic), which the current Cohere trial cannot process. Use public/synthetic
fixtures to onboard; see [the provider policy](COHERE_INFERENCE.md). Do not reclassify
unknown documents just to make a provider error disappear.

## Upload, register and poll

Persist a caller-generated document ID and an ownership/provenance receipt before
uploading, so interrupted runs can reconcile their own objects. All requests below
carry the same verified delegation headers.

| Step | Request | Required result |
| --- | --- | --- |
| Create collection | `POST /collections` with `{"collection_id":"synthetic_collection","name":"Synthetic fixtures"}` | 201, or verify the existing owned collection after a conflict |
| Upload original | `POST /documents/upload`, multipart `file`, `document_id=fixture_001`, `collection_id=synthetic_collection` | 201 with private `storage_key`, validated `mime_type` and `file_size`; do not manufacture these values |
| Register | `POST /documents` with the body below | 202; durable PostgreSQL job, idempotent registration for the same identity/metadata |
| Poll | `GET /documents/fixture_001` | `QUEUED` → `PROCESSING` → `READY`, or terminal `FAILED` |

```json
{
  "external_document_id": "fixture_001",
  "storage_key": "<exact value returned by upload>",
  "file_name": "synthetic-fixture.pdf",
  "mime_type": "application/pdf",
  "file_size": 1234,
  "collection_id": "synthetic_collection",
  "owner_subject_id": "synthetic_subject",
  "document_type": "GENERAL"
}
```

`file_size` above is illustrative; use the actual upload result. The original stays
private. Use `GET /documents/fixture_001/signed-url` only after authorizing the
download in the consumer; signed links expire and must not be logged.

Poll with a bounded deadline/backoff and show actual upstream state. `FAILED` is
terminal, not another spelling of processing. `job` includes `stage`, `attempts`,
`max_attempts`, `generation`, `queue_age_seconds` and a safe `error_code`. An HTTP
outage is not successful indexing. Surface a queued document whose age exceeds
the consumer's deadline; do not silently wait forever or repeatedly register it.
List endpoints use `limit` and `offset`, never a `page` parameter.

Registration and read paths accept the caller's external ID. Some document reads
also accept the internal row ID returned as `id`; retain `external_document_id` for
search result mapping and cleanup receipts. Never use a filename as ownership proof.
Uploading bytes over an already registered ID returns 409. Changed-file replacement
and retained historical evidence remain open rollout work; do not overwrite its
Storage object or claim atomic replacement through this upload endpoint. Existing
immutable originals can be explicitly reindexed using registration `reindex: true`;
a failed reindex preserves the current published evidence. Use bounded operator
reindexing rather than repeatedly retrying a failed client request.

## Converse, retrieve and answer

Create with `POST /conversations` and body
`{"external_reference":"consumer-thread-001","collection_id":"synthetic_collection","owner_subject_id":"synthetic_subject"}`.
Store the returned internal conversation `id` on the consumer server. Registration
is idempotent within exactly the same client, tenant, actor, collection and subject;
another actor or scope cannot attach to that conversation.

`POST /search` accepts `query`, optional `conversation_id`, `collection_id`,
`owner_subject_id` and `limit`. `POST /answers` accepts those fields and returns
`answer`, `citations`, `route`, `requires_history`, `retrieval_query`, `rewritten`
and the saved `message_id`. For example:

```json
{
  "query": "What verification code does the synthetic fixture contain?",
  "conversation_id": "<registered internal conversation id>",
  "collection_id": "synthetic_collection",
  "owner_subject_id": "synthetic_subject",
  "limit": 5
}
```

Support the validated `DIRECT`, `RAG`, `GENERAL_LLM`, `TOOL_API` and `CLARIFICATION`
routes. General inference and optional tool routes require server policy; clients
cannot enable them with prompts. `RAG` insufficient evidence is distinct from an
inference failure. Render only verified evidence belonging to the consumer's current
authorized catalog. Citations carry citation number, external document ID, chunk ID,
version ID, page and supporting text. Preserve those references and display page
citations; do not invent a citation when the response has none. Exact clinical values
belong on the compatible structured medical path.

`POST /answers/stream` uses the same JSON request and `Accept: text/event-stream`.
Versioned SSE uses `data:` JSON frames with an `event` field (`metadata`, `token`,
`citations`, `done`, or `error`) followed by `data: [DONE]`. Token text is in `text`;
the citations frame contains a `citations` array. Require the `done` event and
terminal sentinel; `[DONE]` after an error is not an answer. Validate the citations
frame and completed answer before marking success. Standalone native routes use
named SSE events, a different wire format. Never replay generation automatically
after an ambiguous disconnect; a completed turn might already have been saved.

Read history with `GET /conversations/{id}/messages?limit=50&offset=0`. History and
compact memory are isolated by actor and scope. The server selectively loads and
reauthorizes context, and withholds stale/removed evidence. Client-supplied
`system_prompt` and `conversation_history` are ignored compatibility fields.

## Errors and deletion

Treat 401/403 as authentication/scope denial, 404 as an unavailable scoped resource,
409 as an identity conflict, and 422 as an invalid request. HTTP 503 with
`error_code=provider_not_approved` requires an eligibility review; retrying does not
grant approval. `provider_quota`, `provider_rate_limit`, `provider_timeout`,
`provider_invalid_response` and `provider_unavailable` have distinct safe codes.
Streaming failures may arrive in an HTTP-200 error frame. Preserve the actual
failure and bound retries; provider failure must not appear as an empty successful
answer. Do not log raw upstream bodies, credentials, record contents or questions.

`DELETE /documents/fixture_001` immediately revokes access and queues durable
physical cleanup. Poll `GET /documents/fixture_001/cleanup` under the same complete
scope until `status=complete`, `vectors_remaining=0`,
`derived_objects_remaining=0` and `object_deleted=true`. A 404 from document search
or any 503 from the receipt cannot prove physical cleanup. Retain owned receipts
and retry idempotently after interruption; do not delete someone else's fixture.
`DELETE /conversations/{id}` removes its messages and compact memory. Delete a
temporary collection only after its owned documents have been cleaned.

Before enabling an integration, run a synthetic upload → READY → unique-marker
retrieval → cited answer/SSE completion → verified cleanup journey through the
consumer's authenticated API. Seed a second patient/school/class and assert its
evidence is inaccessible. Required CI must fail when credentials or assertions are
missing. Deployment contract checks alone do not establish this journey.
