"""POST /orders/{order_id}/payments -- tenders, store credit, loyalty, EMI.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from api.utils.event_time import utc_now_iso

import re
import uuid
from datetime import datetime
from fastapi import Depends, HTTPException, Header
from typing import Optional
from ..auth import get_current_user
from ...dependencies import (
    get_customer_repository,
    get_order_repository,
    validate_store_access,
)
from ...services import cash_denominations as cash_denom
from ...services.policy_registry import resolve_emi_annual_rate as _emi_annual_rate
from ._shared import (
    refuse_if_advance_refunded,
    LOYALTY_TENDER_ROLES,
    PAYMENT_ROLES,
    logger,
    router,
)
from .pricing import (
    PaymentCreate,
    PaymentMethod,
)
from .numbering import build_emi_schedule
from .workshop import _ensure_workshop_job_for_order
from .release import _claim_order_status


def _claim_loyalty_redemption(order: dict, amount: float, payment_id: str, idem_key: str = ""):
    """A LOYALTY tender must be backed by REAL points. POST /loyalty/redeem does
    the atomic points debit (refusing a customer who lacks the points) and writes
    a REDEEM ledger row carrying this order_id. This claims one unused such row
    for THIS order and THIS order's customer, worth exactly this amount, by
    compare-and-set -- so a tender with no burn behind it is refused, and one
    burn backs at most one tender. Returns (claimed txn_id, None), or
    (None, twin payment_id) when an identical request (same Idempotency-Key)
    already claimed the burn -- the caller replays it instead of refusing."""
    from ...dependencies import get_loyalty_transaction_repository

    order_id = order.get("order_id")
    customer_id = str(order.get("customer_id") or "")
    if not customer_id or customer_id.startswith("walkin-"):
        raise HTTPException(
            status_code=400,
            detail="Loyalty: this order has no customer account, so it cannot be paid with points.",
        )
    txns = get_loyalty_transaction_repository()
    if txns is None:
        raise HTTPException(
            status_code=503,
            detail="Loyalty points cannot be checked just now - nothing was recorded. Try again.",
        )
    unused = txns.collection.find(
        {"order_id": order_id, "customer_id": customer_id, "type": "REDEEM", "tender_payment_id": None}
    )
    # A burn an old (pre-claim) LOYALTY payment on this order already names
    # ("<pts>pts txn <id>") paid once: never again, backfilled or not.
    named = {
        m.group(1)
        for p in order.get("payments") or []
        if str((p or {}).get("method") or "").upper() == "LOYALTY"
        for m in [re.search(r"\btxn\s+(\S+)", str((p or {}).get("reference") or ""))]
        if m
    }
    for row in unused:
        if row.get("txn_id") in named:
            continue
        if abs(float(row.get("rupee_value") or 0) - float(amount)) > 0.01:
            continue
        claim = {"tender_payment_id": payment_id}
        if idem_key:
            claim["tender_idempotency_key"] = idem_key
        if txns.update_if(row["txn_id"], {"tender_payment_id": None}, claim):
            return row["txn_id"], None
    if idem_key:
        twin = txns.collection.find_one(
            {"order_id": order_id, "type": "REDEEM", "tender_idempotency_key": idem_key}
        )
        if twin and twin.get("tender_payment_id"):
            return None, twin["tender_payment_id"]
    raise HTTPException(
        status_code=400,
        detail=(
            f"Not enough loyalty points: no points worth Rs {float(amount):.2f} were "
            "redeemed for this bill, so it cannot be paid by loyalty. Redeem the "
            "customer's points first. Nothing was recorded."
        ),
    )


def _release_loyalty_redemption(txn_id: str, payment_id: str) -> None:
    """Undo a claim whose payment row never landed, so the burn stays spendable."""
    from ...dependencies import get_loyalty_transaction_repository

    try:
        get_loyalty_transaction_repository().update_if(
            txn_id,
            {"tender_payment_id": payment_id},
            {"tender_payment_id": None, "tender_idempotency_key": None},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[ORDERS] loyalty claim %s NOT released: %s", txn_id, exc)


@router.post("/{order_id}/payments")
async def add_payment(
    order_id: str,
    payment: PaymentCreate,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Add payment to order.

    POS-14: supports an optional ``Idempotency-Key`` header. When a non-empty
    key is supplied and a payment with that key already exists on this order,
    the EXISTING payment_id is returned without recording a duplicate. This
    makes a double-clicked "Pay" button safe. The key is stamped on the payment
    row and looked up before any write. Fail-soft: if the lookup fails, the
    normal (non-idempotent) path runs.
    """
    # RBAC: taking money on a bill is a till action. This door was open to ANY
    # logged-in role, so a workshop or catalogue login could mark a bill paid.
    if not any(r in current_user.get("roles", []) for r in PAYMENT_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Your role is not permitted to take payments on a bill.",
        )
    repo = get_order_repository()

    if repo is not None:
        # POS-14: idempotency guard — look for an existing payment with this key
        # on the same order before recording another. Fail-soft.
        # isinstance guard: a direct (non-HTTP) call leaves the Header(...) default
        # object in idempotency_key, which has no .strip(); treat it as absent.
        idem_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if idem_key:
            try:
                order_doc = repo.find_by_id(order_id)
                if order_doc:
                    # IDOR guard: the replay must not leak another store's
                    # payment row -- same store check as the main path below.
                    validate_store_access(order_doc.get("store_id"), current_user)
                    for existing_pmt in order_doc.get("payments") or []:
                        if existing_pmt.get("idempotency_key") == idem_key:
                            return {
                                "payment_id": existing_pmt.get("payment_id"),
                                "message": "Payment recorded",
                                "amount": existing_pmt.get("amount"),
                                "order_status": order_doc.get("status", "DRAFT"),
                                "payment_status": order_doc.get(
                                    "payment_status", "UNPAID"
                                ),
                                "_idempotent_replay": True,
                            }
            except HTTPException:
                raise
            except Exception as _idem_exc:  # noqa: BLE001
                logger.warning(
                    "[ORDERS] payment idempotency lookup skipped: %s", _idem_exc
                )
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") == "CANCELLED":
            raise HTTPException(
                status_code=400, detail="Cannot add payment to cancelled order"
            )
        refuse_if_advance_refunded(order, "take a payment")

        # ONE reading of what an order owes, shared with the courier door
        # (services.delivery_gate.order_balance_due). The fallback below used
        # to live here alone, so the shipping gate read a balance-less order
        # as Rs 0.00 while this door read the whole bill - contradictory
        # refusals on the same order. A stored non-number now 400s instead of
        # TypeError-ing through the comparison below.
        from ...services.delivery_gate import order_balance_due

        balance_due = order_balance_due(order) or 0
        # C-9: a CREDIT tender is a pay-later PROMISE, not cash collected, so it
        # is exempt from the over-tender block -- matching OrderRepository.
        # add_payment (which excludes CREDIT from its cash-collected/over-tender
        # math). A real-money tender (CASH/UPI/CARD/etc.) still cannot exceed the
        # balance due.
        if payment.method != PaymentMethod.CREDIT and payment.amount > balance_due:
            raise HTTPException(
                status_code=400,
                detail=f"Payment amount exceeds balance due (Rs {balance_due})",
            )

        # Gift voucher: REDEEM (decrement the card) before recording the
        # payment, so an abandoned sale never burns a card and there is no
        # client-side double-spend. The atomic redeem is the single source
        # of truth for spend rules + concurrency safety (see vouchers.py).
        # On failure nothing is recorded; on success we fall through to the
        # normal payment-recording path unchanged.
        if payment.method == PaymentMethod.GIFT_VOUCHER:
            from ..vouchers import redeem_voucher_atomic

            voucher_code = (payment.voucher_code or payment.reference or "").strip()
            if not voucher_code:
                raise HTTPException(
                    status_code=400,
                    detail="Voucher: a voucher code is required for GIFT_VOUCHER payments",
                )
            from ...dependencies import get_seeded_db

            result = redeem_voucher_atomic(
                get_seeded_db(),
                voucher_code,
                payment.amount,
                order_id,
                current_user.get("user_id"),
            )
            if not result.get("ok"):
                raise HTTPException(
                    status_code=400, detail=f"Voucher: {result.get('reason')}"
                )

        # Store credit: REDEEM atomically BEFORE recording the payment -- the
        # same shape and the same single implementation the /store-credit/redeem
        # route uses. The customer comes from the ORDER, so a request body
        # cannot spend somebody else's credit. Raises 400 (insufficient, or a
        # walk-in with no account) or 503 (no atomic path available); nothing is
        # recorded on failure. Debit-before-record is deliberate: the reverse
        # can mint a payment row backed by nothing, which is undetectable, while
        # this direction leaves a ledger row carrying ref=order_id, so a spend
        # that failed to record is still provable.
        if payment.method == PaymentMethod.STORE_CREDIT:
            credit_customer_id = str(order.get("customer_id") or "")
            if not credit_customer_id or credit_customer_id.startswith("walkin-"):
                raise HTTPException(
                    status_code=400,
                    detail="Store credit: this order has no customer account to redeem from",
                )
            from ..customers import redeem_store_credit_atomic

            redeem_store_credit_atomic(
                credit_customer_id,
                payment.amount,
                reason=f"Redeemed at POS against order {order_id}",
                ref=order_id,
                store_id=current_user.get("active_store_id"),
                user_id=current_user.get("user_id"),
            )

        # Loyalty: the points must have been really redeemed for THIS bill.
        payment_id = str(uuid.uuid4())
        loyalty_txn_id = None
        if payment.method == PaymentMethod.LOYALTY:
            if not any(r in current_user.get("roles", []) for r in LOYALTY_TENDER_ROLES):
                raise HTTPException(
                    status_code=403,
                    detail="Loyalty points are taken at the till only - your role cannot record a loyalty payment.",
                )
            loyalty_txn_id, twin_payment_id = _claim_loyalty_redemption(
                order, payment.amount, payment_id, idem_key
            )
            if twin_payment_id:
                # The same request is already recording this tender: replay it.
                return {
                    "payment_id": twin_payment_id,
                    "message": "Payment recorded",
                    "amount": payment.amount,
                    "order_status": order.get("status", "DRAFT"),
                    "payment_status": order.get("payment_status", "UNPAID"),
                    "_idempotent_replay": True,
                }

        # EMI validation and interest calculation
        emi_details = None
        if payment.method == PaymentMethod.EMI:
            if not payment.emi_months:
                raise HTTPException(
                    status_code=400,
                    detail="EMI tenure (emi_months) is required for EMI payments",
                )
            # Configurable EMI rate from the policy matrix (store > entity >
            # global > registry default 12.0). Replaces the old read of a
            # `settings` collection `emi_config` row that NOTHING ever wrote:
            # the policy key is owner-editable in Settings and is the SAME
            # key the POS payment screen quotes from, so screen == charge.
            emi_annual_rate = _emi_annual_rate(current_user.get("active_store_id"))

            # POS-2 + P3-C: use emi_principal (financed balance) when the
            # caller provides it; fall back to payment.amount for backward
            # compat. This lets the POS record the down-payment in `amount`
            # (which reduces balance_due correctly) while the schedule
            # reflects the full loan amount (order_total - down_payment).
            schedule_principal = payment.emi_principal or payment.amount
            emi_details = build_emi_schedule(
                principal=schedule_principal,
                annual_rate=emi_annual_rate,
                months=payment.emi_months,
            )
            emi_details["provider"] = payment.emi_provider or "STORE"
            # Record the down-payment separately so the full EMI picture is
            # on the order document alongside the financed balance.
            if payment.emi_principal and payment.emi_principal != payment.amount:
                emi_details["down_payment"] = round(float(payment.amount), 2)
                emi_details["financed_amount"] = round(float(payment.emi_principal), 2)

        payment_data = {
            "payment_id": payment_id,
            "method": payment.method.value,
            "amount": payment.amount,
            "reference": payment.reference,
            "received_by": current_user.get("user_id"),
            "received_at": utc_now_iso(),
            # POS-14: persist the idempotency key on the row so a duplicate POST
            # with the same key is caught by the guard at the top of this handler.
            "idempotency_key": (idem_key or None),
        }
        if emi_details:
            payment_data["emi_details"] = emi_details
        if loyalty_txn_id:
            payment_data["loyalty_txn_id"] = loyalty_txn_id

        # Denominated cash accountability. ADDITIVE and CASH-only: these keys
        # ride alongside `amount`, which stays the single source of truth for
        # amount_paid, balance_due, payment_status, GST and every export.
        # add_payment recomputes the ladder from `amount` + `method` alone, so
        # nothing below can move it. A cashier who entered nothing produces
        # NOT_CAPTURED blocks -- never a fabricated zero, never a blocked sale.
        if payment.method == PaymentMethod.CASH:
            payment_data.update(
                cash_denom.cash_leg_record(
                    tendered=payment.cash_tendered,
                    change=payment.cash_change,
                    tendered_amount_paisa=(
                        None
                        if payment.tendered_amount is None
                        else cash_denom.rupees_to_paisa(payment.tendered_amount)
                    ),
                    change_amount_paisa=(
                        None
                        if payment.change_amount is None
                        else cash_denom.rupees_to_paisa(payment.change_amount)
                    ),
                    amount_paisa=cash_denom.rupees_to_paisa(payment.amount),
                    actor=current_user,
                )
            )

        try:
            if payment.method == PaymentMethod.CREDIT:
                from ...services.credit_limit import record_credit

                recorded = record_credit(repo, order, payment_data, get_customer_repository())
            else:
                recorded = repo.add_payment(order_id, payment_data)
        except BaseException:
            if loyalty_txn_id:
                _release_loyalty_redemption(loyalty_txn_id, payment_id)
            raise
        if not recorded and loyalty_txn_id:
            _release_loyalty_redemption(loyalty_txn_id, payment_id)
        if not recorded:
            # A refund-advance claim that landed mid-write: say so, not "failed".
            refuse_if_advance_refunded(repo.find_by_id(order_id) or {}, "take a payment")
        if recorded:
            # Auto-confirm DRAFT orders when first payment is received
            # This fixes the "stuck in DRAFT+PARTIAL" lifecycle issue
            refreshed = repo.find_by_id(order_id)
            if idem_key and refreshed:
                saved = next((p for p in refreshed.get("payments") or []
                              if p.get("idempotency_key") == idem_key), None)
                if saved and saved.get("payment_id") != payment_data["payment_id"]:
                    if loyalty_txn_id:  # the other request's row stands, not ours
                        _release_loyalty_redemption(loyalty_txn_id, payment_id)
                    return {
                        "payment_id": saved.get("payment_id"), "message": "Payment recorded",
                        "amount": saved.get("amount"), "order_status": refreshed.get("status", "DRAFT"),
                        "payment_status": refreshed.get("payment_status", "UNPAID"),
                        "_idempotent_replay": True,
                    }
            auto_confirmed = False
            workshop_job_id = None
            if refreshed and refreshed.get("status") == "DRAFT":
                # Same guarded claim as /confirm -- the auto-confirm door is
                # not exempt from a concurrent cancel.
                if not _claim_order_status(
                    repo,
                    order_id,
                    "CONFIRMED",
                    ("DRAFT",),
                    current_user.get("user_id"),
                ):
                    logger.warning(
                        "[ORDERS] auto-confirm on payment skipped for %s -- "
                        "its status changed (likely cancelled)",
                        order_id,
                    )
                    refreshed = repo.find_by_id(order_id) or refreshed
                    return {
                        "payment_id": payment_data["payment_id"],
                        "message": "Payment recorded",
                        "amount": payment.amount,
                        "workshop_job_id": None,
                        "order_status": (refreshed or {}).get("status", "DRAFT"),
                        "payment_status": (refreshed or {}).get(
                            "payment_status", "PARTIAL"
                        ),
                    }
                auto_confirmed = True
                # F16: this auto-confirm bypassed confirm_order, so the workshop
                # safety-net never ran and a paid spectacle order NEVER reached
                # the lab queue -- the job simply did not exist. Run the exact
                # same idempotent, fail-soft helper confirm_order runs (it skips
                # non-fitting orders, never duplicates an existing job, and never
                # raises), so both confirm paths behave identically.
                try:
                    workshop_job_id = _ensure_workshop_job_for_order(
                        refreshed, current_user.get("user_id")
                    )
                except Exception as exc:  # noqa: BLE001 -- must never block a payment
                    logger.warning(
                        "[ORDERS] workshop auto-link skipped on payment "
                        "auto-confirm for %s: %s",
                        order_id,
                        exc,
                    )

            return {
                "payment_id": payment_data["payment_id"],
                "message": "Payment recorded"
                + (" — order auto-confirmed" if auto_confirmed else ""),
                "amount": payment.amount,
                "workshop_job_id": workshop_job_id,
                "order_status": (
                    "CONFIRMED"
                    if auto_confirmed
                    else refreshed.get("status") if refreshed else "DRAFT"
                ),
                "payment_status": (
                    refreshed.get("payment_status") if refreshed else "PARTIAL"
                ),
            }

        raise HTTPException(status_code=500, detail="Failed to add payment")

    if payment.method == PaymentMethod.CREDIT:
        raise HTTPException(503, "Credit could not be verified or recorded. Please retry.")
    return {"payment_id": str(uuid.uuid4()), "message": "Payment recorded"}
