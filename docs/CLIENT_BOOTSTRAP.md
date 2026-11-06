# Server client bootstrap and administrative scope

Quick-Clinic credentials authorize medical integration operations in its delegated
tenant. They cannot register, list, rotate or revoke platform service clients. An
existing `quick_clinic` database profile can narrow or revoke the compatibility key.

An operator with backend SQL access bootstraps an independent credential with only
`clients:manage` and tenant `_platform`. `scripts/register_service_client.py` reads
`DATABASE_URL` and `SEARCH_SPHERE_BOOTSTRAP_API_KEY` from the operator environment;
keep both in the existing secret manager or an ignored, private environment file.
Generate a high-entropy key, never put it in command arguments, and retain it only
on the server. No key is printed by this script.

```sh
python scripts/register_service_client.py \
  --client-id platform_release --name 'Release client administration' \
  --tenant _platform --scope clients:manage
```

The default is a dry run. Review the scopes and tenants, then repeat with `--apply`.
Existing identities require `--replace-existing`; revoked identities are refused.
The script never changes `alembic_version`. The historical hardcoded migration stamp,
machine-specific environment paths and wildcard tenant provisioning were removed.

Use that independent credential with `Authorization: Bearer …` and
`X-Tenant-ID: _platform` for `/api/v1/clients`. Register each application with its
actual tenants and only the document, collection, search and answer scopes it needs.
Do not give consuming applications `clients:manage`. Issue their keys to server
environment variables, never browser bundles. The client response reveals a new key
once; store it without placing the response in logs or CI artifacts.

Quick-Clinic continues to authorize doctor-to-patient access; ExamArena continues
to authorize school enrollment and class access. They delegate the resulting tenant,
collection and subject in each server request, which Search Sphere validates.
The full future-client upload/poll/search/delete guide is tracked separately in the
rollout ledger while durable ingestion and release journeys are completed.

## Delegated conversation ownership

`X-Actor-ID` identifies the authenticated person making the request. It is distinct
from `X-Subject-ID`, which identifies the patient or other evidence subject. The
consumer derives both after its own authorization checks and sends them only from
its server using the scoped service credential. Browsers cannot self-assert these
headers to Search Sphere without that credential.

Register a conversation with `POST /api/v1/conversations`, the `conversations:write`
scope, `X-Tenant-ID`, `X-Actor-ID`, and the selected collection/subject headers. The
optional JSON `external_reference` makes registration idempotent within exactly
that client, tenant, actor, collection, and subject. The response supplies an internal
`id`. Retain it on the consumer server. Different actors/patients/collections receive
separate conversations even if their external references happen to be identical.

`GET /api/v1/conversations/{id}` and `/{id}/messages?limit=50&offset=0` require
`conversations:read` and the same complete delegation. The message view reauthorizes
source evidence and withholds an old answer when its source changed or was removed.
`DELETE /api/v1/conversations/{id}` requires `conversations:write` and deletes its
messages. An omitted actor is rejected; an out-of-scope ID returns 404.

The `conversation_id` field on versioned search/answer requests is checked before
query processing starts, including SSE. The storage/authorization foundation is
implemented; wiring complete turn persistence, selective context, routing and memory
into every delegated answer route is the following rollout phase. Until then, clients
must not treat this foundation as a completed multi-turn answer contract.
