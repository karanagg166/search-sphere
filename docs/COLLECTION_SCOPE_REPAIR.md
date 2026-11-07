# Collection request bounds

Pre-implementation inventory: collection CRUD already checks client and tenant,
and the shared `ServiceContext.resolve_scope` rejects conflicting delegated scope
for documents and queries. `CollectionService` does not use that resolver: create,
get and delete ignore a bound `X-Collection-ID`; list ignores it in both rows and
counts. A request bound to collection A can therefore address metadata for B in the
same client/tenant. This is an authenticated service API scope gap. No production
record contents are needed to reproduce it.

Subject-delegated lists with no collection also fell through to the whole tenant
catalog. They must fail closed until the caller supplies its authorized collection.

Plan: reuse the existing scope resolver before collection-specific operations and
apply the bound collection predicate before list counts and pagination. Preserve
tenant-wide `collections:manage` operations when no collection is delegated. Add
real PostgreSQL API regressions for wrong-bound get/delete/create, list/count/
offset behavior, same-name client/tenant separation and continued authorized CRUD.
Use synthetic collection metadata only. No schema, provider or model change.

Collection metadata remains a client/tenant resource; document subject ownership
continues to be enforced by the document/query authorization paths. This repair
does not introduce a new subject-owned collection schema or change collection
deletion into document/object deletion.

The seven original conflicting/list cases reproduced the gap before the resolver
and SQL predicate repair; a further regression reproduced subject-only catalog
expansion. All nine new bound-catalog cases and the eight existing collection/
legacy-service regressions now pass against real PostgreSQL (17 total), with no
provider calls. Existing tenant-level administration and same-name client/tenant
separation still work. Ruff and whitespace checks pass. Production verification
must follow deployment; local regressions are not a production acceptance result.
