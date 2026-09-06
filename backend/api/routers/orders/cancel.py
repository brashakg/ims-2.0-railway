"""POST /orders/{order_id}/cancel.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from fastapi import Depends, HTTPException, Query
from typing import Any, Dict, List
from ..auth import get_current_user
from ...dependencies import (
    get_order_repository,
    get_stock_repository,
    validate_store_access,
)
from ._shared import (
    POS_WRITE_ROLES,
    logger,
    router,
)
from .release import (
    _claim_order_for_cancel,
    _release_lens_lines,
)


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    reason: str = Query(..., min_length=10),
    current_user: dict = Depends(get_current_user),
):
    """Cancel order"""
    # RBAC: cancelling a sale is a POS-tier action, same tier as order
    # create/confirm/deliver. Previously ANY authenticated role (ACCOUNTANT,
    # OPTOMETRIST, WORKSHOP_STAFF, ...) could cancel any order.
    if not any(r in current_user.get("roles", []) for r in POS_WRITE_ROLES):
        raise HTTPException(
            status_code=403, detail="Your role is not permitted to cancel orders."
        )
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") == "DELIVERED":
            raise HTTPException(
                status_code=400, detail="Cannot cancel delivered orders"
            )

        # ------------------------------------------------------------------
        # ATOMIC SINGLE-SHOT CANCEL (panel must-fix 3). find_by_id + status
        # check + update is check-then-act: two concurrent cancels of the same
        # order BOTH passed the check and BOTH ran the stock + loyalty undo.
        # The claim below is ONE guarded find_one_and_update whose filter
        # excludes CANCELLED/DELIVERED, so exactly one caller can flip the
        # order -- and only that caller runs the undo.
        #
        # RE-RUN PATH (panel must-fix 4): an order already CANCELLED whose
        # previous undo did not finish (cancel_stock_release_failed /
        # loyalty_reversal_failed) is allowed BACK IN to retry, because both
        # undos are idempotent by construction. Without this, a partial restock
        # was permanent: the 400 below refused every retry forever.
        # ------------------------------------------------------------------
        claimed = _claim_order_for_cancel(repo, order_id, reason, current_user)
        is_retry = False
        if claimed is None:
            fresh = repo.find_by_id(order_id) or {}
            if fresh.get("status") != "CANCELLED":
                if fresh and fresh.get("status") != "DELIVERED":
                    # Still cancellable -> we did not lose a race, the STATUS
                    # WRITE ITSELF failed. Fail loudly; the order is untouched
                    # and no stock/loyalty undo has run.
                    raise HTTPException(
                        status_code=500,
                        detail="Could not cancel the order -- please retry.",
                    )
                # Lost the race to a DELIVER (or the doc vanished).
                raise HTTPException(
                    status_code=400,
                    detail="Cannot cancel this order -- its status changed.",
                )
            # RETRY DOOR ON GROUND TRUTH, not on a flag. The failure stamp goes
            # through base_repository.update, which swallows exceptions and
            # returns False -- so the very Mongo blip that fails the stock
            # release ALSO fails the stamp. Gating the retry on the flag alone
            # meant the doc carried no marker, the door never opened, and the
            # operator log told staff to re-POST a cancel that 400s forever with
            # units still SOLD. stock_units still SOLD against this order is the
            # fact that cannot be lost.
            _still_sold = 0
            try:
                _sr = get_stock_repository()
                _counter = getattr(_sr, "count_sold_units_for_order", None)
                if callable(_counter):
                    _still_sold = int(_counter(order_id) or 0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ORDERS] retry-door ground-truth check failed for %s: %s",
                    order_id,
                    exc,
                )
            if not (
                _still_sold
                or fresh.get("cancel_stock_release_failed")
                or fresh.get("loyalty_reversal_failed")
                # A doc that never received the stamp at all is also suspect --
                # a completed cancel always writes this key.
                or "cancel_stock_released" not in fresh
            ):
                raise HTTPException(
                    status_code=400, detail="Order is already cancelled"
                )
            # Already cancelled WITH an unfinished undo -> retry it.
            is_retry = True
            order = fresh
            logger.warning(
                "[ORDERS] re-running the cancel undo for %s (still_sold=%s "
                "stock_failed=%s loyalty_failed=%s)",
                order_id,
                _still_sold,
                fresh.get("cancel_stock_release_failed"),
                fresh.get("loyalty_reversal_failed"),
            )
        else:
            order = claimed

        # Branch B' sub-PR 4 -- release any lens-stock reservations on
        # the cancelled order so the cells return to AVAILABLE. Fully
        # fail-soft: a release that can't go through (commit already
        # happened, lens already cut) is logged but never blocks the
        # cancel response.
        try:
            from ...services.lens_stock_hook import release_for_cancel

            items_for_release = await _release_lens_lines(
                order.get("items") or [],
                order_id=order_id,
                store_id=order.get("store_id") or "",
                user=current_user,
                release=release_for_cancel,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] cancel release outer error %s: %s",
                order_id,
                exc,
            )

        # F3 (P1 STOCK) -- put the SERIALIZED units back on the shelf.
        # _mark_units_sold flips frame/sunglass stock_units to SOLD at CREATE
        # (even for a DRAFT), and cancel used to release ONLY the lens cells
        # above: every cancellation therefore removed a sellable frame from
        # AVAILABLE *permanently*. The repo helper is an ATOMIC guarded update
        # per unit (status=="SOLD" AND order_id==this order) that clears the
        # order_id in the same write, so it is idempotent by construction -- a
        # retried / double cancel reactivates nothing. Fail-soft (a stock write
        # must never break the cancel) but LOUD, and the outcome is stamped on
        # the order so reconciliation can find failures.
        stock_released: List[str] = []
        stock_release_failed = False
        stock_still_sold = 0
        try:
            stock_repo = get_stock_repository()
            if stock_repo is not None and hasattr(
                stock_repo, "release_sold_units_for_order"
            ):
                result = stock_repo.release_sold_units_for_order(order_id)
                # The helper reports PARTIAL completion explicitly (must-fix 4):
                # a mid-loop write failure used to return a short list that was
                # indistinguishable from a clean run, so the API said "cancelled"
                # while units stayed SOLD against a CANCELLED order -- and the
                # already-cancelled 400 then refused every retry.
                stock_released = list(getattr(result, "released", result) or [])
                stock_release_failed = bool(getattr(result, "incomplete", False))
                # Ground truth beats the return value: anything STILL SOLD
                # against this order after the sweep is stranded stock.
                counter = getattr(stock_repo, "count_sold_units_for_order", None)
                if callable(counter):
                    stock_still_sold = int(counter(order_id) or 0)
                    if stock_still_sold:
                        stock_release_failed = True
                logger.info(
                    "[STOCK] cancel %s reactivated %d serialized unit(s): %s "
                    "(still SOLD: %d)",
                    order_id,
                    len(stock_released),
                    stock_released,
                    stock_still_sold,
                )
                if stock_release_failed:
                    logger.error(
                        "[STOCK] CANCEL RESTOCK INCOMPLETE for order %s -- %d "
                        "unit(s) still SOLD against a CANCELLED order. Re-POST "
                        "the cancel to retry (the release is idempotent).",
                        order_id,
                        stock_still_sold,
                    )
        except Exception as exc:  # noqa: BLE001
            stock_release_failed = True
            logger.error(
                "[STOCK] CANCEL RESTOCK FAILED for order %s: %s -- units may be "
                "stranded SOLD against a cancelled order",
                order_id,
                exc,
            )

        # F4 (P1 MONEY) -- reverse loyalty on the cancelled order: claw back the
        # points EARNED at create (unfunded redeemable value + a farm-and-cancel
        # vector) and RESTORE any points the customer REDEEMED against it
        # (otherwise the cancel silently burns their balance). Idempotent on the
        # order id inside reverse_for_cancel. Fail-soft + stamped, mirroring how
        # returns.py records loyalty_reversal_failed.
        loyalty_reversal_failed = False
        loyalty_reversal: Dict[str, Any] = {}
        try:
            from ..loyalty import reverse_for_cancel

            loyalty_reversal = (
                reverse_for_cancel(order_id, order.get("customer_id")) or {}
            )
            if loyalty_reversal.get("ok"):
                logger.info(
                    "[ORDERS] loyalty reversed on cancel %s: clawed=%s restored=%s",
                    order_id,
                    loyalty_reversal.get("earned_clawed", 0),
                    loyalty_reversal.get("redeemed_restored", 0),
                )
            elif loyalty_reversal.get("reason") not in (
                "missing_ids",
                "loyalty_db_unavailable",
            ):
                loyalty_reversal_failed = True
                logger.error(
                    "[ORDERS] loyalty reversal FAILED on cancel %s: %s",
                    order_id,
                    loyalty_reversal.get("reason"),
                )
        except Exception as exc:  # noqa: BLE001
            loyalty_reversal_failed = True
            logger.error(
                "[ORDERS] loyalty reversal exception on cancel %s: %s", order_id, exc
            )

        # Stamp the reversal outcome on the order doc so a failed clawback /
        # restock is discoverable by reconciliation instead of living only in
        # the logs. Fail-soft: the cancel itself already succeeded.
        _stamp_ok = False
        try:
            _stamp_ok = repo.update(
                order_id,
                {
                    "cancel_stock_released": stock_released,
                    "cancel_stock_release_failed": stock_release_failed,
                    "cancel_stock_units_still_sold": stock_still_sold,
                    "loyalty_reversal_failed": loyalty_reversal_failed,
                    "loyalty_reversal": {
                        k: v
                        for k, v in loyalty_reversal.items()
                        if k
                        in (
                            "ok",
                            "reason",
                            "earned_clawed",
                            "redeemed_restored",
                            "net_delta",
                            "already_reversed",
                            "txn_id",
                        )
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ORDERS] cancel reconciliation stamp skipped for %s: %s",
                order_id,
                exc,
            )
        if not _stamp_ok:
            # GATE THE STAMP. base_repository.update swallows exceptions and
            # returns False, and this return value used to be discarded -- so
            # the blip that failed the release also lost its own failure marker.
            # One bounded retry, then a loud log carrying everything a human
            # needs, because the doc may hold nothing.
            try:
                _stamp_ok = repo.update(
                    order_id,
                    {
                        "cancel_stock_release_failed": stock_release_failed,
                        "cancel_stock_units_still_sold": stock_still_sold,
                        "loyalty_reversal_failed": loyalty_reversal_failed,
                    },
                )
            except Exception:  # noqa: BLE001
                _stamp_ok = False
            if not _stamp_ok:
                logger.error(
                    "[ORDERS] CANCEL RECONCILIATION STAMP LOST for %s -- "
                    "stock_release_failed=%s still_sold=%s loyalty_failed=%s "
                    "released=%s. The order doc carries NO marker; the retry "
                    "door falls back to the stock_units ground truth.",
                    order_id,
                    stock_release_failed,
                    stock_still_sold,
                    loyalty_reversal_failed,
                    stock_released,
                )

        # Audit alert (May 2026) — every cancellation is CRITICAL severity
        try:
            from ...services.audit_alerts import alert_order_cancelled
            import asyncio as _aio

            _aio.create_task(
                alert_order_cancelled(
                    order_id,
                    before=order,
                    user_id=current_user.get("user_id"),
                    reason=reason,
                )
            )
        except Exception:
            pass

        return {
            "order_id": order_id,
            "status": "CANCELLED",
            "message": ("Cancel undo re-run" if is_retry else "Order cancelled"),
            "stock_units_released": len(stock_released),
            "stock_release_failed": stock_release_failed,
            "stock_units_still_sold": stock_still_sold,
            "loyalty_reversal_failed": loyalty_reversal_failed,
        }

    return {"order_id": order_id, "status": "CANCELLED"}
