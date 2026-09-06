"""Ready / deliver / deliver-with-payment, plus the handover models.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from datetime import datetime
from fastapi import Depends, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional
from ..auth import get_current_user
from ...dependencies import (
    get_customer_repository,
    get_order_repository,
    validate_store_access,
)
from ._shared import (
    HANDOVER_ROLES,
    VALID_TRANSITIONS,
    _get_db,
    assert_no_active_rx_hold,
    logger,
    router,
    validate_status_transition,
)
from .pricing import PaymentCreate
from .payments import add_payment
from .release import _claim_order_status


@router.post("/{order_id}/ready")
async def mark_ready(order_id: str, current_user: dict = Depends(get_current_user)):
    """Mark order as ready for delivery"""
    # RBAC: previously ANY authenticated role could do this. HANDOVER_ROLES (not
    # POS_WRITE_ROLES) -- see its definition for why the front-desk CASHIER must
    # keep this.
    if not any(r in current_user.get("roles", []) for r in HANDOVER_ROLES):
        raise HTTPException(
            status_code=403, detail="Your role is not permitted to mark orders ready."
        )
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        # Clinical Rx FLAG-AND-HOLD: a held spectacle order (missing Rx) may not
        # advance to READY until an admin clears the hold (400 otherwise).
        assert_no_active_rx_hold(order)

        if not validate_status_transition(order.get("status", ""), "READY"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark as ready — current status is {order.get('status')}. Valid transitions: {', '.join(VALID_TRANSITIONS.get(order.get('status', ''), set()))}",
            )

        # PATIENT SAFETY: an order whose linked workshop job has no QC record may
        # not be advertised to the customer as ready. Imports the SAME predicate
        # the workshop gate uses -- never re-derive it here. Runs AFTER the
        # legality check so an order in the wrong status is told THAT, rather
        # than being handed a QC instruction it does not yet need.
        #
        # ORDERING IS LOAD-BEARING -- QC CHECK FIRST, THEN CLAIM THE STATUS, and
        # never the reverse. READY is EXACTLY the precondition the deliver guard
        # requires (see the claim's own comment below), so claiming first would
        # leave the order fully deliverable, with lens work never inspected, for
        # the entire interval between the claim and the check -- recreating the
        # hole this gate exists to close, out of a merge resolution. A failing
        # check would also force an unwind of a status already committed.
        # QC-first has no equivalent cost: if the claim then fails because the
        # order was cancelled concurrently, the only wasted work is a read. Its
        # residual race -- a NEW un-QC'd job attached between check and claim --
        # is caught at the deliver door, which re-runs this same predicate.
        # Defence in depth holds in this direction and not in the other.
        from ..workshop import assert_linked_job_qc_cleared

        assert_linked_job_qc_cleared(order)

        # READY is exactly the precondition the deliver guard requires, so a
        # resurrected order would deliver cleanly straight afterwards. Claim it.
        if _claim_order_status(
            repo,
            order_id,
            "READY",
            ("CONFIRMED", "PROCESSING"),
            current_user.get("user_id"),
        ):
            return {
                "order_id": order_id,
                "status": "READY",
                "message": "Order marked as ready",
            }

        raise HTTPException(status_code=500, detail="Failed to update order status")

    return {"order_id": order_id, "status": "READY"}


class HandoverDetails(BaseModel):
    """What the counter records at handover (owner spec: fit check, cleaning,
    who collected). All optional — the door stays backward-compatible with
    body-less callers."""

    picked_up_by_name: Optional[str] = Field(None, max_length=120)
    picked_up_by_phone: Optional[str] = Field(None, max_length=20)
    fit_check_done: Optional[bool] = None
    cleaned_and_cased: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=300)
    # STAFF side of the handover (owner 2026-09-02: "who is giving delivery to
    # the customer should also be logged"). Deliberately NOT the same thing as
    # picked_up_by_*, which is the CUSTOMER side -- who walked out with the bag.
    # One order can have both: delivered_by = Priya, picked_up_by = the
    # customer's brother. Left free rather than forced to the caller so the
    # counter can name the colleague who actually fitted and handed over;
    # `recorded_by` on the stored record is always the authenticated actor, so
    # the audit trail does not depend on what the client claimed here.
    delivered_by_id: Optional[str] = Field(None, max_length=64)
    delivered_by_name: Optional[str] = Field(None, max_length=120)


class DeliverRequest(BaseModel):
    handover: Optional[HandoverDetails] = None
    # CREDIT_DELIVERY approval token — required when balance_due > 0 and the
    # caller is not a manager (owner ruling: credit delivery is PIN-gated).
    approval_token: Optional[str] = None


@router.post("/{order_id}/deliver")
async def deliver_order(
    order_id: str,
    body: Optional[DeliverRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """Deliver order to customer"""
    # RBAC: previously ANY authenticated role could deliver. HANDOVER_ROLES (not
    # POS_WRITE_ROLES) -- see its definition for why the front-desk CASHIER must
    # keep this.
    if not any(r in current_user.get("roles", []) for r in HANDOVER_ROLES):
        raise HTTPException(
            status_code=403, detail="Your role is not permitted to deliver orders."
        )
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        # Clinical Rx FLAG-AND-HOLD: a held spectacle order (missing Rx) may not
        # be delivered until an admin clears the hold (400 otherwise).
        assert_no_active_rx_hold(order)

        if not validate_status_transition(order.get("status", ""), "DELIVERED"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot deliver — current status is {order.get('status')}. Must be READY.",
            )

        # PATIENT SAFETY: THIS is the handover door the counter actually uses --
        # payment and invoice live on the Orders screen, so its green "Mark
        # Delivered" is the likelier of the two. Gating only the Workshop screen
        # let a job the workshop gate was actively holding walk out in one click.
        # Imports the SAME predicate the workshop gate uses -- never re-derive it.
        # Runs AFTER the legality check so an order in the wrong status is told
        # THAT, rather than being handed a QC instruction it does not yet need.
        from ..workshop import assert_linked_job_qc_cleared

        assert_linked_job_qc_cleared(order)

        # Money side of the handover -- ONE implementation, shared with the
        # workshop / labels / lab-scan DELIVERED doors and non-COD shipping
        # (services.delivery_gate): UNPAID -> 400; balance still due -> a
        # manager, or a consumed CREDIT_DELIVERY token bound to this store and
        # this order. Those four doors used to let goods leave with no money
        # check at all, so the rule lives in one place now rather than being
        # re-typed per door and drifting.
        from ...services.delivery_gate import assert_handover_payment

        assert_handover_payment(
            order,
            approval_token=(body.approval_token if body else None),
            current_user=current_user,
            db=_get_db(),
        )

        # Handover record rides the atomic claim below (never stranded on a
        # lost race). Only what the counter actually filled in is stored.
        _claim_extra = None
        if body is not None and body.handover is not None:
            _h = {k: v for k, v in body.handover.model_dump().items() if v is not None}
            if _h:
                # Never leave "who handed this over" blank once a handover was
                # recorded: fall back to the signed-in actor. (A body-less
                # /deliver records no handover_record at all -- its actor is
                # already on status_updated_by / status_history.)
                for _k, _fallback in (
                    ("delivered_by_id", current_user.get("user_id")),
                    (
                        "delivered_by_name",
                        current_user.get("full_name") or current_user.get("username"),
                    ),
                ):
                    if not _h.get(_k) and _fallback:
                        _h[_k] = _fallback
                _claim_extra = {
                    "handover_record": {
                        **_h,
                        "recorded_by": current_user.get("user_id"),
                        "recorded_at": datetime.now().isoformat(),
                    }
                }

        # ATOMIC DELIVER CLAIM. update_status writes update_one({order_id}, ...)
        # with NO status precondition, and the window from the read above to
        # this write spans the store-access, Rx-hold, transition and payment
        # checks. A cancel that wins its own claim mid-window would then be
        # OVERWRITTEN back to DELIVERED here.
        #
        # That race is pre-existing, but THIS PR sharpens its consequence: cancel
        # now releases stock, so losing it leaves a frame that is physically in
        # the customer's bag reading AVAILABLE and re-sellable -- and on a pooled
        # ONLINE store that feeds a Shopify oversell. Before, the unit stayed
        # correctly SOLD and only loyalty was wrong.
        if not _claim_order_status(
            repo,
            order_id,
            "DELIVERED",
            "READY",
            current_user.get("user_id"),
            extra=_claim_extra,
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot deliver this order -- its status changed (it may "
                    "have just been cancelled). Refresh and try again."
                ),
            )
        if True:
            # CRM-9: Auto-trigger NPS survey on delivery (fail-soft — a survey
            # failure must NEVER block the delivery confirmation).
            try:
                from ...services.nps_trigger import trigger_nps_on_delivery

                await trigger_nps_on_delivery(order, current_user)
            except Exception as _nps_exc:
                logger.warning(
                    "[ORDERS] NPS auto-trigger failed (non-fatal): %s", _nps_exc
                )
            # Owner ruling (mockup sign-off): auto-WhatsApp on completion —
            # queue the ORDER_DELIVERED text (MEGAPHONE drains it; DISPATCH_MODE
            # off = queued only). Fail-soft: a messaging failure must never
            # block the handover.
            try:
                _cust_phone = order.get("customer_phone")
                if not _cust_phone and order.get("customer_id"):
                    _crepo = get_customer_repository()
                    _cdoc = (
                        _crepo.find_by_id(order.get("customer_id"))
                        if _crepo is not None
                        else None
                    )
                    _cust_phone = (_cdoc or {}).get("mobile") or (_cdoc or {}).get(
                        "phone"
                    )
                if _cust_phone:
                    from ...services.notification_service import (
                        send_notification as _queue_notification,
                    )
                    from ...services.print_identity import load_store as _load_store

                    _store_name = (_load_store(order.get("store_id")) or {}).get(
                        "name"
                    ) or "our store"
                    await _queue_notification(
                        store_id=order.get("store_id") or "",
                        customer_id=order.get("customer_id") or "",
                        customer_phone=_cust_phone,
                        customer_name=order.get("customer_name") or "Customer",
                        template_id="ORDER_DELIVERED",
                        channel="WHATSAPP",
                        variables={
                            "order_number": order.get("order_number") or order_id,
                            "store_name": _store_name,
                        },
                        category="SERVICE",
                        triggered_by=current_user.get("user_id") or "auto",
                        related_entity_type="order",
                        related_entity_id=order_id,
                    )
            except Exception as _ntf_exc:
                logger.warning(
                    "[ORDERS] delivered-notification queue failed (non-fatal): %s",
                    _ntf_exc,
                )
            return {
                "order_id": order_id,
                "status": "DELIVERED",
                "message": "Order delivered",
            }

        raise HTTPException(status_code=500, detail="Failed to deliver order")

    return {"order_id": order_id, "status": "DELIVERED"}


class DeliverWithPaymentRequest(BaseModel):
    """One counter action: collect the balance (optionally) and hand over."""

    payment: Optional[PaymentCreate] = None
    handover: Optional[HandoverDetails] = None
    approval_token: Optional[str] = None


@router.post("/{order_id}/deliver-with-payment")
async def deliver_with_payment(
    order_id: str,
    body: DeliverWithPaymentRequest,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Collect-and-deliver in ONE counter action (owner spec: staff forget
    the second step; the deliver modal never even showed the balance).

    DELEGATION, not duplication: this door CALLS the existing add_payment
    and deliver_order handlers, so every guard both doors carry (IDOR,
    idempotency, over-tender, credit-limit, voucher redeem, QC gate,
    Rx hold, atomic claim, credit-delivery gate) runs verbatim — there is
    no second implementation of either.

    Deliberately non-atomic (map ruling): if the payment records but the
    deliver claim then fails, the money stands on an undelivered order —
    balance_due is already recomputed, staff just retries the deliver.
    """
    payment_result = None
    if body.payment is not None:
        payment_result = await add_payment(
            order_id, body.payment, current_user, idempotency_key
        )
    deliver_result = await deliver_order(
        order_id,
        DeliverRequest(handover=body.handover, approval_token=body.approval_token),
        current_user,
    )
    out = dict(deliver_result)
    if payment_result is not None:
        out["payment"] = payment_result
    return out
