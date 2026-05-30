"""
IMS 2.0 - Audit trail integrity API
===================================
SYSTEM_INTENT 10: the audit trail must be immutable, even for Superadmin.

This router is intentionally READ-ONLY. The canonical human-action audit
collection (`audit_logs`) is APPEND-ONLY: there is no PUT/PATCH/DELETE route
for audit rows anywhere in the API, and the AuditRepository exposes no mutation
path. The only writes happen through the hash-chained append helper
(database/repositories/audit_chain.py).

Exposed here:
    GET /api/v1/audit/verify   SUPERADMIN-only chain integrity check

No mutation route is registered (see the note below the verify handler), so the
audit trail stays append-only by construction and the hash-chain makes any
out-of-band edit detectable.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from .auth import get_current_user
from ..dependencies import get_audit_repository

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_superadmin(current_user: dict) -> None:
    """SUPERADMIN gate. Audit integrity is a CEO-level control surface."""
    roles = current_user.get("roles", []) if current_user else []
    if "SUPERADMIN" not in roles:
        raise HTTPException(status_code=403, detail="Superadmin access required")


@router.get("/verify")
async def verify_audit_trail(current_user: dict = Depends(get_current_user)):
    """Walk the audit hash-chain and report whether it is intact.

    Recomputes every row's entry_hash in seq order and confirms each row's
    prev_hash matches the previous row's entry_hash. The first row that fails
    either check is reported by its seq.

    Returns:
        {
          "intact": bool,                # True if the whole chain verifies
          "broken_at_seq": int | null,   # seq of the first tampered/broken row
          "entries_checked": int         # chained rows walked
        }

    Fail-soft: with no DB connected, returns an intact empty result (0 entries)
    rather than erroring -- there is nothing to tamper with yet.
    """
    _require_superadmin(current_user)

    audit_repo = get_audit_repository()
    if audit_repo is None:
        return {"intact": True, "broken_at_seq": None, "entries_checked": 0}

    from database.repositories.audit_chain import verify_chain

    return verify_chain(audit_repo.collection)


# ---------------------------------------------------------------------------
# APPEND-ONLY (SYSTEM_INTENT 10) -- intentionally NO mutation routes
# ---------------------------------------------------------------------------
# This router deliberately registers no PUT / PATCH / DELETE for `audit_logs`.
# A survey of every router confirmed none exists elsewhere either, so the audit
# trail is append-only by construction: rows can be created (hash-chained) and
# read, never edited or removed -- not even by Superadmin. Do NOT add a
# mutation handler here; tampering must remain impossible via the API, and the
# hash-chain (GET /verify) makes any out-of-band edit detectable.
