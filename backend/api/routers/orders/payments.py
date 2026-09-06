"""POST /orders/{order_id}/payments -- tenders, store credit, loyalty, EMI.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

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

        # POS-4: credit-limit (khata) guard.
        # When a CREDIT tender is used, enforce the per-customer credit limit.
        # A limit of 0 means unlimited. Fail-soft: if we cannot read the
        # customer record the check is skipped (behaviour-preserving).
        if payment.method == PaymentMethod.CREDIT:
            customer_id_for_limit = order.get("customer_id")
            if customer_id_for_limit and not customer_id_for_limit.startswith(
                "walkin-"
            ):
                try:
                    from ..customers import _ar_outstanding

                    customer_repo = get_customer_repository()
                    customer_doc = (
                        customer_repo.find_by_id(customer_id_for_limit)
                        if customer_repo is not None
                        else None
                    )
                    credit_limit = float((customer_doc or {}).get("credit_limit") or 0)
                    if credit_limit > 0:
                        ar_now = _ar_outstanding(customer_id_for_limit, customer_doc)
                        if ar_now + payment.amount > credit_limit:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"Credit limit exceeded: customer limit is "
                                    f"Rs {credit_limit:.2f}, current AR outstanding "
                                    f"Rs {ar_now:.2f}. Adding Rs {payment.amount:.2f} "
                                    f"would exceed by Rs "
                                    f"{(ar_now + payment.amount - credit_limit):.2f}."
                                ),
                            )
                except HTTPException:
                    raise
                except Exception as _exc:  # noqa: BLE001
                    logger.warning("[ORDERS] credit-limit check skipped: %s", _exc)

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
            "payment_id": str(uuid.uuid4()),
            "method": payment.method.value,
            "amount": payment.amount,
            "reference": payment.reference,
            "received_by": current_user.get("user_id"),
            "received_at": datetime.now().isoformat(),
            # POS-14: persist the idempotency key on the row so a duplicate POST
            # with the same key is caught by the guard at the top of this handler.
            "idempotency_key": (idem_key or None),
        }
        if emi_details:
            payment_data["emi_details"] = emi_details

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

        if repo.add_payment(order_id, payment_data):
            # Auto-confirm DRAFT orders when first payment is received
            # This fixes the "stuck in DRAFT+PARTIAL" lifecycle issue
            refreshed = repo.find_by_id(order_id)
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

    return {"payment_id": str(uuid.uuid4()), "message": "Payment recorded"}
