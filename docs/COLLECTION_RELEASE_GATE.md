# Deployed collection scope gate

Before implementation, PostgreSQL API regressions cover the catalog request-bound
repair, while the production smoke exercises native upload/answers and document
cleanup. No deployed gate yet checks collection-bound CRUD or subject-only listing.

Plan: add a separate trusted-main post-deployment workflow with its own narrowly
scoped server key. The key can manage collections only in a dedicated synthetic
tenant; it cannot read documents, infer, or manage clients. Reuse the existing
health/revision HTTP checks. Create signed, empty synthetic catalogs, verify bound
list/count/pagination, reject conflicting get/create/delete and subject-only lists,
and prove foreign-client/tenant denial. Delete only signature-owned empty catalogs
and verify metadata is gone. No document upload or inference is required.

Signed logical collection IDs let interrupted runs find their own metadata on the
next attempt without retaining keys or content in artifacts. Fail on missing
credentials, wrong revisions, failed assertions, 503 or unverified cleanup. Keep
this post-deploy event independent from pre-deploy CI and native RAG proof; it
cannot substitute for any application's full authenticated evidence journey.

`collection-scope-smoke.yml` and `scripts/collection_scope_smoke.py` implement this
gate. The protected `RELEASE_SCOPE_API_KEY` belongs to `release_scope_checks`, has
only `collections:manage`, and permits only the identically named synthetic tenant.
Its dry run was reviewed before creation through the existing SQL bootstrap. The
key is private and configured only in the protected release environment; no provider
policy or inference permission was granted. Existing `RELEASE_SMOKE_KEY` signs
logical collection IDs so a lost create response is recoverable by listing.

Thirteen new offline cases cover scope failures, least-privilege denial, a lost
create response, unverified 503 cleanup, next-run recovery, ownership separation and
missing credentials. The combined scope/native-smoke/release suite passes 94 cases;
Ruff and whitespace checks pass. The live scope result remains open until the
catalog repair and this gate are reviewed, deployed and run from protected main.
