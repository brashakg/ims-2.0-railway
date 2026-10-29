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

A COD courier booking is exempt from the gate above because the courier
collects instead - cod_collectable is the other half of that bargain (what it
must be told to collect). order_balance_due underneath is the ONE reading of
what an order owes; orders.py add_payment (the counter) calls it too, so the
two doors can no longer refuse the same order for opposite reasons.

"One rule, two implementations" is this repo's dominant defect class - do NOT
copy these checks into a router; import and call them.

No emoji / non-ASCII (Windows cp1252). "Rs", never the rupee glyph.
"""

from __future__ import annotations

import logging
import math
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
    THIS order. After authorisation, the shared credit-limit reservation records
    the unpaid balance as an actual CREDIT promise before goods can leave.

    Verbatim move of orders.py _gate_credit_delivery (the token is passed as a
    plain string instead of a request-body object so any door can call it).
    The balance is read through order_balance_due below - the same reading the
    counter and the COD door use. An inline float(order.get("balance_due"))
    here read a balance-less legacy row as Rs 0.00 and let a cashier Prepaid-
    ship an order owing the whole bill."""
    balance_due = order_balance_due(order)
    if balance_due is None:
        raise HTTPException(
            status_code=400,
            detail="The outstanding balance is not recorded. Verify this order's payments before handover.",
        )
    if balance_due <= 0.01:
        return
    roles = current_user.get("roles", [])
    if any(r in CREDIT_DELIVERY_MANAGER_ROLES for r in roles):
        from .credit_limit import record_handover_credit

        record_handover_credit(order, current_user, _resolve_db(db))
        return
    token = (approval_token or "").strip()
    if token:
        approved = False
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
            approved = bool(res.get("ok"))
            if not approved:
                logger.warning(
                    "[DELIVERY-GATE] CREDIT_DELIVERY token refused for %s: %s",
                    order.get("order_id"), res.get("error"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[DELIVERY-GATE] CREDIT_DELIVERY consume failed: %s", exc)
        if approved:
            # Keep the write outside the token engine's exception wrapper.
            from .credit_limit import record_handover_credit

            record_handover_credit(order, current_user, _resolve_db(db))
            return
    raise HTTPException(
        status_code=403,
        detail=(
            f"Rs {balance_due:,.2f} is still due on this order. Delivering "
            f"with a balance needs a manager (or a manager-approved "
            f"credit-delivery PIN token)."
        ),
    )


def refuse_handover_of_refunded(order: dict) -> None:
    """409 when the order is cancelled or its advance was refunded: the goods
    must not go out with the money already back."""
    status = str((order or {}).get("status") or "").strip().upper()
    if status not in ("CANCELLED", "CANCELED") and (order or {}).get("advance_refund_id"):
        # A claim whose press died before writing anything is freed (THE
        # stale-claim rule), never a handover lock for good.
        from ..routers.returns import release_stale_advance_claim

        order = release_stale_advance_claim(order)
    if status in ("CANCELLED", "CANCELED") or (order or {}).get("advance_refund_id"):
        raise HTTPException(
            status_code=409,
            detail=(
                "This order is cancelled or its advance was refunded, so it "
                "cannot be handed over or shipped."
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

    0. a CANCELLED order, or one whose advance was refunded through
       /returns/advance-refund (advance_refund_id, on its way to CANCELLED),
       never leaves: the customer has the money back (PR #1264). Every
       handover door routes here -- the counter deliver, shipping, the
       workshop PUT status and the PICKUP scan.

    Raises HTTPException; returns None when the handover is money-clear."""
    refuse_handover_of_refunded(order)
    payment_status = order.get("payment_status", "UNPAID")
    if payment_status == "UNPAID":
        raise HTTPException(
            status_code=400,
            detail="Order must have at least partial payment before delivery",
        )
    gate_credit_delivery(order, approval_token, current_user, db=db)


def _as_amount(raw, field: str) -> float:
    """Money off an order doc as a float, or a 400. Legacy/imported rows carry
    formatted strings ("2,000.00"); float() raised ValueError on those and the
    caller answered a shop question with a 500. float() also accepts "nan" /
    "inf", which are not amounts either."""
    try:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("not a finite amount")
        return value
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "AMOUNT_NOT_A_NUMBER",
                "message": (
                    f"This order stores {field} as '{raw}', which is not an "
                    f"amount. Fix the order before shipping or billing it."
                ),
            },
        ) from exc


def order_balance_due(order: dict) -> Optional[float]:
    """What this order still OWES - the ONE reading every money door uses.

    The convention is the counter's (orders.py add_payment): an order doc with
    NO balance_due key at all is a legacy / freshly-imported row on which
    nothing has been paid, so the whole bill is still due. Reading that same
    order as "Rs 0.00 due" - which the courier door used to - made the two
    doors refuse the same order for opposite reasons.

    Returns None when there is genuinely no figure to read (an explicit
    null/blank balance, or neither field present), so a caller can say "no
    balance recorded on this order" instead of asserting Rs 0.00. Raises 400
    when a stored value is not a number."""
    field = "balance_due" if "balance_due" in order else "grand_total"
    raw = order.get(field)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return _as_amount(raw, field)


def cod_collectable(order: dict) -> float:
    """The amount a COD courier is told to collect: the order's balance_due,
    the SERVER figure (grand_total - amount_paid, maintained by
    OrderRepository.add_payment and the online ingest) - never a number the
    booking request supplies.

    This is the other half of the COD exemption in assert_handover_payment: a
    COD booking skips the counter money gate ONLY because the courier
    collects, and that is honest only if the courier is told what is still
    OWED. It used to be told the whole bill, so a customer who paid a deposit
    at the counter was asked to pay it again at the door.

    Refuses (400, with a stable `code`) rather than guessing:
      * COD_NO_BALANCE_RECORDED - the order carries no balance figure at all.
      * COD_NOTHING_TO_COLLECT  - fully paid; there is nothing to collect, so
        book it Prepaid. Silently flipping the method would change what the
        customer was told at the counter and what the courier charges the
        shop, so the caller is told to choose.
      * COD_BALANCE_EXCEEDS_BILL - a corrupt order nobody should ship on.
      * COD_ABOVE_SHOPIFY_TOTAL  - an online order flagged MISMATCH whose
        balance is above what the buyer owes on Shopify's total."""
    balance_due = order_balance_due(order)
    if balance_due is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "COD_NO_BALANCE_RECORDED",
                "message": (
                    "There is no balance recorded on this order, so there is "
                    "nothing to tell the courier to collect. Record the bill, "
                    "or book it Prepaid."
                ),
            },
        )
    grand_total = _as_amount(order.get("grand_total") or 0.0, "grand_total")
    if balance_due <= 0.01:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "COD_NOTHING_TO_COLLECT",
                "message": (
                    f"This order records Rs {balance_due:,.2f} due - nothing "
                    f"for the courier to collect. Book it Prepaid, not COD."
                ),
            },
        )
    if balance_due > grand_total + 0.01:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "COD_BALANCE_EXCEEDS_BILL",
                "message": (
                    f"Balance due Rs {balance_due:,.2f} exceeds the bill "
                    f"Rs {grand_total:,.2f} - fix the order before shipping "
                    f"it COD."
                ),
            },
        )
    # An online order whose IMS bill differs from what Shopify charged
    # (shopify_ingest._totals_check; an admin task is raised): the courier
    # never collects more than the buyer still owes on Shopify's own total.
    check = order.get("shopify_totals_check") or {}
    if check.get("status") == "MISMATCH":
        paid = _as_amount(order.get("amount_paid") or 0.0, "amount_paid")
        owed = round(_as_amount(check.get("shopify_total"), "shopify_total") - paid, 2)
        if balance_due > owed + 0.01:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "COD_ABOVE_SHOPIFY_TOTAL",
                    "message": (
                        f"The IMS balance Rs {balance_due:,.2f} is more than the "
                        f"buyer owes on Shopify (Rs {owed:,.2f}). An admin must "
                        f"correct this order's total before it ships COD."
                    ),
                },
            )
    return round(balance_due, 2)
