"""Tenant- and role-scoped memory access.

The backend remains deliberately small: storage engines own indexing and
consolidation, while this adapter owns the authorization boundary and refuses
to query an unscoped backend.
"""

from typing import Any, Dict


ROLE_RANK = {"member": 1, "admin": 2, "owner": 3}

class MultiTenantMemory:
    """Authorization adapter for a backend that supports scoped retrieval."""

    def __init__(self, memory_backend=None):
        self.memory_backend = memory_backend

    async def get_for_tenant(self, tenant_id: str, query: str, user_role: str) -> Dict[str, Any]:
        tenant_id = str(tenant_id or "").strip()
        query = str(query or "").strip()
        role = str(user_role or "").strip().lower()
        if not tenant_id or not query or role not in ROLE_RANK:
            return {"status": "rejected", "results": [], "reason": "tenant, query, and a supported role are required"}
        if self.memory_backend is None:
            return {"status": "unavailable", "results": [], "reason": "memory backend is not configured"}

        scoped_search = getattr(self.memory_backend, "search_scoped", None)
        if scoped_search is None:
            return {"status": "unavailable", "results": [], "reason": "memory backend does not support scoped retrieval"}
        raw = scoped_search(query=query, tenant_id=tenant_id, user_role=role)
        if hasattr(raw, "__await__"):
            raw = await raw
        records = raw if isinstance(raw, list) else raw.get("results", []) if isinstance(raw, dict) else []
        visible = []
        for record in records:
            if not isinstance(record, dict):
                continue
            record_tenant = str(record.get("tenant_id") or record.get("organization_id") or "")
            minimum_role = str(record.get("minimum_role") or "member").lower()
            if record_tenant != tenant_id or ROLE_RANK.get(role, 0) < ROLE_RANK.get(minimum_role, 99):
                continue
            visible.append(record)
        return {"status": "ok", "tenant_id": tenant_id, "role": role, "results": visible}

    async def run_consolidation(self, tenant_id: str):
        tenant_id = str(tenant_id or "").strip()
        if not tenant_id:
            return {"status": "rejected", "tenant_id": tenant_id, "merged": 0, "deleted": 0}
        consolidate = getattr(self.memory_backend, "consolidate_scoped", None) if self.memory_backend else None
        if consolidate is None:
            return {"status": "unavailable", "tenant_id": tenant_id, "merged": 0, "deleted": 0}
        result = consolidate(tenant_id=tenant_id)
        if hasattr(result, "__await__"):
            result = await result
        payload = result if isinstance(result, dict) else {}
        return {
            "status": "ok",
            "tenant_id": tenant_id,
            "merged": int(payload.get("merged", 0) or 0),
            "deleted": int(payload.get("deleted", 0) or 0),
        }