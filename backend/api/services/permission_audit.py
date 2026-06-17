"""
IMS 2.0 - Per-user permission change audit (no-log-no-commit) + revert
======================================================================

Helpers for the per-user permissions layer's audit + rollback (council ruling
sec.2). Every override change (create_user with overrides, update_user touching
permissions/module_access/discount_cap, and revert) writes ONE immutable
``audit_logs`` row carrying the FULL prior + after permission snapshot, via the
tamper-evident hash chain (AuditRepository.create -> append_audit_entry).

NO-LOG-NO-COMMIT: ``write_permission_audit`` returns the created row or None.
The caller MUST treat a None as a hard failure and refuse the DB write -- the
permission change does not commit if its audit row could not be written. (This
is stricter than the fire-and-forget audit elsewhere; permission changes are a
control surface that must be reconstructable.)

The snapshot captures the three override-bearing fields only (permissions,
module_access, discount_cap) so the timeline diff is focused and a REVERT can
re-apply a prior state precisely. Revert re-runs through the SAME escalation
guard + sanitize at the router; this module only records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


# The override-bearing fields a permission snapshot captures.
_SNAPSHOT_FIELDS = ("permissions", "module_access", "discount_cap")


def snapshot_of(user: Optional[dict]) -> dict:
    """Extract the permission-relevant snapshot from a user doc (or {} when the
    field is absent). Deterministic shape so prior/after diffs are clean."""
    user = user or {}
    return {
        "permissions": user.get("permissions") or {},
        "module_access": user.get("module_access") or {},
        "discount_cap": user.get("discount_cap"),
    }


def write_permission_audit(
    audit_repo,
    *,
    action: str,
    actor: dict,
    target_user_id: str,
    prior: dict,
    after: dict,
    store_id: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[dict]:
    """Append ONE hash-chained audit row for a permission change.

    Returns the created row, or None on failure. The caller enforces
    no-log-no-commit: a None MUST abort the permission write.

    ``action`` is one of PERMISSIONS_CREATE / PERMISSIONS_UPDATE /
    PERMISSIONS_REVERT. ``prior``/``after`` are ``snapshot_of`` dicts.
    """
    if audit_repo is None:
        return None
    row = {
        "action": action,
        "entity_type": "user_permissions",
        "entity_id": target_user_id,
        "user_id": actor.get("user_id"),
        "actor_username": actor.get("username"),
        "actor_roles": actor.get("roles", []),
        "target_user_id": target_user_id,
        "store_id": store_id,
        "severity": "INFO",
        "timestamp": datetime.now(),
        # The FULL prior + after permission snapshot -- the rollback source.
        "permissions_before": prior,
        "permissions_after": after,
    }
    if note:
        row["note"] = note
    try:
        return audit_repo.create(row)
    except Exception:  # noqa: BLE001 - any failure => no-log-no-commit
        return None


def find_permission_history(audit_repo, target_user_id: str, limit: int = 50):
    """Return the permission-change audit rows for a user, newest first. Empty
    list when the repo is unavailable (fail-soft read)."""
    if audit_repo is None:
        return []
    try:
        return audit_repo.find_many(
            {"entity_type": "user_permissions", "target_user_id": target_user_id},
            sort=[("timestamp", -1)],
            limit=limit,
        )
    except Exception:  # noqa: BLE001
        return []
