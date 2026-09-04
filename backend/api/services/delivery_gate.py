"""
IMS 2.0 - Delivery money gate (single source of truth)
======================================================
THE one implementation of the owner ruling (POS Wave 4): handing goods to the
customer with money still owed is a CREDIT DECISION. A manager may take it
directly; anyone else must carry a manager's PIN-approved CREDIT_DELIVERY
token, store-bound and bound to THIS order.

This logic was extracted VERBATIM from backend/api/routers/orders.py
(deliver_order's payment check + _gate_credit_delivery) so that every door
goods can physically leave through enforces the SAME rule:

  * orders.py POST /{id}/deliver (+ /deliver-with-payment) - the counter door
    (orders.py should call assert_handover_payment and delete its inline copy)
  * workshop.py PATCH /jobs/{id}/status -> DELIVERED  - the manager screen
  * labels.py  POST /jobs/{id}/scan-advance (PICKUP)  - the barcode pickup scan
  * lab_routing PICKUP station scan                    - via the shared scan
    gate in workshop.evaluate_scan_transition_gate
  * shipping.py POST /shipments (non-COD booking)      - the courier door

"One rule, two implementations" is this repo's dominant defect class - do NOT
copy these checks into a router; import and call them.

No emoji / non-ASCII (Windows cp1252). "Rs", never the rupee glyph.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# The manager set the owner's credit-delivery ruling names. Moved verbatim from
# orders.py _CREDIT_DELIVERY_MANAGER_ROLES.
CREDIT_DELIVERY_MANAGER_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)


def _resolve_db(db):
    """Raw MongoDB handle for the ApprovalEngine. Accepts a caller-supplied
    handle; otherwise resolves via dependencies (same pattern as the routers'
    _get_db helpers). Fail-soft -> None (the token consume then fails closed:
    an unverifiable token never authorises a credit delivery)."""
    if db is not None:
        return db
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def gate_credit_delivery(
    order: dict,
    approval_token: Optional[str],
    current_user: dict,
    db=None,
) -> None:
    """Owner ruling (POS Wave 4): handing goods over with money still owed is
    a credit decision. A manager may take it directly; anyone else must carry
    a manager's PIN-approved CREDIT_DELIVERY token, store-bound and bound to
    THIS order. Raises 403 otherwise. PURE GATE except the single-use token
    consume - no money math.

    Verbatim move of orders.py _gate_credit_delivery (the token is passed as a
    plain string instead of a request-body object so any door can call it)."""
    balance_due = float(order.get("balance_due") or 0.0)
    if balance_due <= 0.01:
        return
    roles = current_user.get("roles", [])
    if any(r in CREDIT_DELIVERY_MANAGER_ROLES for r in roles):
        return
    token = (approval_token or "").strip()
    if token:
        try:
            from .approvals import ApprovalEngine

            engine = ApprovalEngine(db=_resolve_db(db))
            res = engine.consume_approval(
                consumed_by=current_user.get("user_id") or "",
                action_type="CREDIT_DELIVERY",
                approval_token=token,
                expected_store_id=order.get("store_id"),
                expected_context={"order_id": order.get("order_id")},
            )
            if res.get("ok"):
                return
            logger.warning(
                "[DELIVERY-GATE] CREDIT_DELIVERY token refused for %s: %s",
                order.get("order_id"), res.get("error"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DELIVERY-GATE] CREDIT_DELIVERY consume failed: %s", exc)
    raise HTTPException(
        status_code=403,
        detail=(
            f"Rs {balance_due:,.2f} is still due on this order. Delivering "
            f"with a balance needs a manager (or a manager-approved "
            f"credit-delivery PIN token)."
        ),
    )


def assert_handover_payment(
    order: dict,
    *,
    approval_token: Optional[str],
    current_user: dict,
    db=None,
) -> None:
    """The FULL money side of a handover, exactly as the canonical counter
    door (orders.py deliver_order) enforces it:

      1. payment_status UNPAID -> 400 for EVERYONE (an order with zero payment
         on record never leaves; even a manager must first record at least a
         partial payment - or, for a courier dispatch, book it COD so the
         courier collects).
      2. balance still due -> manager, or a consumed CREDIT_DELIVERY token
         bound to this store + order (403 otherwise).

    Raises HTTPException; returns None when the handover is money-clear."""
    payment_status = order.get("payment_status", "UNPAID")
    if payment_status == "UNPAID":
        raise HTTPException(
            status_code=400,
            detail="Order must have at least partial payment before delivery",
        )
    gate_credit_delivery(order, approval_token, current_user, db=db)
