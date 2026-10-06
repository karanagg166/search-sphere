from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import HTTPException, status


@dataclass(frozen=True)
class ServiceContext:
    """
    Authoritative identity and tenancy context passed through all RAG layers.

    Enforces separation between:
    - client_id: The authenticated external application (e.g. quick_clinic, exam_arena, search_sphere)
    - tenant_id: The authorized organizational boundary (e.g. clinic_12, school_99, or user workspace)
    - subject_id: Optional end-user/subject within the tenant (e.g. patient_123, student_456)
    - collection_id: Optional logical document collection / knowledge base
    - scopes: Set of permissions granted to this caller (e.g. documents:write, search:execute)
    """

    client_id: str
    tenant_id: str
    subject_id: str | None = None
    collection_id: str | None = None
    scopes: set[str] = field(default_factory=set)
    client_name: str | None = None
    is_service_client: bool = True
    user_id: str | None = None  # Populated for native Search-Sphere users

    def has_scope(self, scope: str) -> bool:
        """Checks if the context possesses the requested scope or wildcard '*'."""
        return "*" in self.scopes or scope in self.scopes

    def require_scope(self, scope: str) -> None:
        """Enforces that the caller possesses the requested scope, raising 403 Forbidden otherwise."""
        if not self.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: caller lacks required scope '{scope}'.",
            )

    def to_qdrant_filter(
        self,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Generates server-side mandatory Qdrant match filters for vector isolation.
        Caller-supplied query metadata filters can never loosen these mandatory keys.
        """
        filters: dict[str, Any] = {
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
        }
        resolved = self.resolve_scope(collection_id, owner_subject_id)
        eff_collection = resolved.collection_id
        if eff_collection:
            filters["collection_id"] = eff_collection
        eff_subject = resolved.subject_id
        if eff_subject:
            filters["owner_subject_id"] = eff_subject
        return filters

    def resolve_scope(self, collection_id: str | None = None, owner_subject_id: str | None = None) -> "ServiceContext":
        for supplied, bound in ((collection_id, self.collection_id), (owner_subject_id, self.subject_id)):
            if supplied is not None and bound is not None and supplied != bound:
                raise HTTPException(status_code=403, detail="Requested scope conflicts with authenticated request scope.")
        return replace(self, collection_id=self.collection_id or collection_id, subject_id=self.subject_id or owner_subject_id)
