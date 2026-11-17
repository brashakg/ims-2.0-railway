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
_SNAPSHOT_FIELDS = (
    "permissions", "module_access", "discount_cap", "discount_cap_is_role_default"
)


ABORTED = "PERMISSIONS_UPDATE_ABORTED"
# Audit-row flag: applied only if the user document confirms it.
NEEDS_CONFIRMATION = "confirmed_on_user"
# User-document list of the permission audit rows whose write landed.
CONFIRMED_IDS_FIELD = "permission_log_ids"


def snapshot_of(user: Optional[dict]) -> dict:
    """Extract the permission-relevant snapshot from a user doc (or {} when the
    field is absent). Deterministic shape so prior/after diffs are clean."""
    user = user or {}
    return {
        "permissions": user.get("permissions") or {},
        "module_access": user.get("module_access") or {},
        "discount_cap": user.get("discount_cap"),
        # D93(3): whether that cap was the role standard (Revert restores it).
        "discount_cap_is_role_default": user.get("discount_cap_is_role_default"),
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
    extra: Optional[dict] = None,
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
    if action != ABORTED:
        # The row is written BEFORE the user write, so on its own it proves
        # nothing. The user write appends this row's log_id to the user's
        # ``permission_log_ids`` in the same atomic update; history treats the
        # row as applied only when the id is there (see find_permission_history).
        row[NEEDS_CONFIRMATION] = True
    row.update(extra or {})
    try:
        return audit_repo.create(row)
    except Exception:  # noqa: BLE001 - any failure => no-log-no-commit
        return None


def write_permission_abort(audit_repo, *, actor: dict, aborted: dict, reason: str):
    """The change logged in ``aborted`` never landed (the write lost its
    compare-and-set or failed). The audit trail is append-only, so record a
    second row saying so; history marks the first ``not_applied`` and revert
    refuses it. Before and after are both the unchanged state."""
    row = write_permission_audit(
        audit_repo,
        action=ABORTED,
        actor=actor,
        target_user_id=aborted.get("target_user_id"),
        prior=aborted.get("permissions_before") or {},
        after=aborted.get("permissions_before") or {},
        store_id=aborted.get("store_id"),
        note=f"Not applied ({reason}); nothing changed.",
        extra={"aborts_log_id": aborted.get("log_id")},
    )
    if row is None:
        print(f"[permission_audit] abort row NOT written for {aborted.get('log_id')}")
    return row


def find_permission_history(
    audit_repo, target_user_id: str, limit: int = 50, applied_ids=None
):
    """Return the permission-change audit rows for a user, newest first, each
    change that never landed marked ``not_applied``. None when the repo is
    unavailable or the read fails: an unreadable history is never shown as
    "no changes" (and Revert cannot pick from it).

    ``applied_ids`` is the user's ``permission_log_ids``. A row that needs
    confirmation and is not in it is ``not_applied``: its write failed, lost
    its compare-and-set, or never ran -- whether or not an ABORTED row could
    be written afterwards. Rows from before the confirmation existed keep the
    ABORTED-row rule."""
    if audit_repo is None:
        return None
    try:
        rows = audit_repo.find_many(
            {"entity_type": "user_permissions", "target_user_id": target_user_id},
            sort=[("timestamp", -1)],
            limit=limit,
            strict=True,
        )
    except Exception:  # noqa: BLE001
        return None
    dead = {r.get("aborts_log_id") for r in rows if r.get("action") == ABORTED}
    applied = set(applied_ids or [])
    for r in rows:
        if r.get("log_id") in dead or (
            r.get(NEEDS_CONFIRMATION) and r.get("log_id") not in applied
        ):
            r["not_applied"] = True
    return rows
