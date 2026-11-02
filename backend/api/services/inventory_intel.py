"""
IMS 2.0 - Inventory intelligence
================================
Pure decision logic (no DB) for:
  * staff stock accountability: attribute a completed count's shrinkage to the
    store's assigned stock custodian.

Kept pure so it's unit-tested directly; the router supplies the data.
Transfer suggestions (Rebalance) are the Reorder list's: reorder_policy.
share_spares over services/reorder_list rows -- one rule for spare pieces.
"""

from __future__ import annotations

from typing import Any, Dict, List


def shrinkage_by_custodian(
    counts: List[Dict[str, Any]], custodians: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Attribute each completed count's shrinkage to its store's custodian.

    - counts: [{store_id, audit_number?, shrinkage_percentage, completed_at}]
    - custodians: {store_id: {staff_id, staff_name}}
    Returns rows with the responsible custodian filled in (None when unassigned).
    """
    out: List[Dict[str, Any]] = []
    for c in counts or []:
        cust = custodians.get(c.get("store_id")) or {}
        out.append(
            {
                "store_id": c.get("store_id"),
                "audit_number": c.get("audit_number"),
                "shrinkage_percentage": c.get("shrinkage_percentage", 0),
                "completed_at": c.get("completed_at"),
                "custodian_id": cust.get("staff_id"),
                "custodian_name": cust.get("staff_name"),
            }
        )
    return out
