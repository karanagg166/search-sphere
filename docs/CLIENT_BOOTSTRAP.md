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
