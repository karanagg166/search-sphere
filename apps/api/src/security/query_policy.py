"""Server-owned query policy authority, independent of application service imports."""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from src.models.query_policy import QueryPolicy


@dataclass(frozen=True)
class RoutingPolicy:
    general_enabled: bool = False
    semantic_routing_enabled: bool = False
    allowed_tools: tuple[str, ...] = ()

    def fingerprint(self):
        return hashlib.sha256(json.dumps([self.general_enabled, self.semantic_routing_enabled, sorted(self.allowed_tools)]).encode()).hexdigest()


async def read_policy(db, context):
    row = (await db.execute(select(QueryPolicy).where(QueryPolicy.client_id == context.client_id,
        QueryPolicy.tenant_id == context.tenant_id))).scalar_one_or_none()
    return RoutingPolicy(bool(row and row.general_enabled), bool(row and row.semantic_routing_enabled),
                         tuple(row.allowed_tools or []) if row else ())


