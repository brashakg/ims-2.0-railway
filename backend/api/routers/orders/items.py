"""Add and remove an order line after creation.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import uuid
from typing import Annotated, Optional
from fastapi import Depends, HTTPException, Query
from ..auth import get_current_user
from ...dependencies import (
    get_order_repository,
    get_product_repository,
    validate_store_access,
)
from ._shared import (
    _compute_per_category_gst,
    bill_line_value,
    bills_round_off,
    logger,
    router,
)
from .pricing import (
    OrderItemCreate,
    _enforce_line_pricing,
    assert_stack_within_cap,
    combined_discount_pct,
)
from ...services import discount_approval
from .rx import _validate_order_line_rx
from .stock import (
    _assert_serialized_stock_available,
    _canonical_pid,
    _claim_units,
    _lens_reservation_key,
    _mark_units_sold,
    _release_claimed_units,
    _resolve_billable_product,
)
from .release import (
    _release_lens_lines,
    _release_line_units,
)


@router.post("/{order_id}/items")
async def add_order_item(
    order_id: str,
    item: OrderItemCreate,
    current_user: dict = Depends(get_current_user),
    # A discount over the seller's own limit (or a Rs 0 line): the request_id
    # of a manager-approved E4 discount_override approval for exactly this
    # change, spent once; request_approval=true raises that request from the
    # server's own figures instead (services/discount_approval -- the same rule
    # as order create).
    # Annotated so a direct call (tests, other doors) gets the plain default,
    # not a truthy Query() object.
    discount_approval_id: Annotated[Optional[str], Query(max_length=64)] = None,
    request_approval: Annotated[bool, Query()] = False,
    approval_note: Annotated[Optional[str], Query(max_length=300)] = None,
):
    """Add item to order (only DRAFT orders)"""
    # BUG-119/BUG-118: real price-integrity validation is below (after the DRAFT
    # check) using the catalog MRP/offer/cost -- the OrderItemCreate model carries
    # no mrp/offer_price field, so the old getattr(item,"mrp",0) guard never fired.

    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only add items to DRAFT orders"
            )

        # ONE product resolution for this door, via THE shared billing
        # resolver (_resolve_billable_product) -- the same lookup + refusal
        # policy create_order uses. This door previously resolved narrowly
        # (find_by_id only), so a product referenced by SKU or Mongo _id
        # missed and the MRP ceiling, cost floor, HQ-offer rule, category/
        # brand caps and reason requirement ALL silently no-op'd -- and a
        # catalog-only product create_order refuses was billed outright.
        _pid = item.product_id or ""
        _pr = get_product_repository()
        product = None
        if _pr is not None:
            try:
                product = _resolve_billable_product(_pr, _pid, item.product_name or "")
            except HTTPException:
                # A MISSING or catalog-only product is a refusal, and stays one.
                raise
            except Exception:  # noqa: BLE001 -- fail CLOSED, never open
                # The repository itself failed (not "not found"). The old door
                # degraded to a no-doc line here, and the shared gate below is
                # built for exactly that: with no master it caps on the ROLE
                # and the client-named LUXURY brand, so a Cartier line still
                # meets the 2% floor and a mass line still meets the role cap.
                # Letting the exception escape instead turned a flaky product
                # read into a 500 on the till -- and skipped the cap entirely.
                product = None
        if product is not None:
            # One id rule with create (_canonical_pid): a product named by SKU
            # or _id is gated, claimed and stored under its product_id.
            _pid = _canonical_pid(_pr, _pid)

        # BUG-005 / BUG-006 (patient-safety): same Rx-power validation +
        # Rx-required check the create path runs, for a line appended to a DRAFT
        # order. Validation only -- no pricing/GST change. Uses the order's
        # customer so an expired / cross-customer / missing Rx is caught here too.
        # SECURITY (Rx-item_type spoof): the resolved PRODUCT MASTER keys the
        # Rx-required decision off the canonical item_type / category, not the
        # client-supplied item_type; virtual lines have no doc -> client fallback.
        _validate_order_line_rx(
            item,
            order.get("customer_id") or order.get("customerId") or "",
            current_user,
            product_doc=product,
        )

        # Per-line price integrity + discount caps: THE shared gate
        # (_enforce_line_pricing), identical to create_order's. Ceiling /
        # cost floor / HQ-offer rule / category+luxury-brand cap / reason
        # requirement -- one implementation, never forked again.
        _is_admin = any(
            r in current_user.get("roles", []) for r in ("SUPERADMIN", "ADMIN")
        )
        _line_gate = _enforce_line_pricing(
            item, product, is_admin=_is_admin, user=current_user
        )
        _cap = _line_gate["cap"]
        _eff_disc = _line_gate["eff_disc"]
        _loyalty_eff = _line_gate["loyalty_eff"]
        # Fcostfloor (chair P1): raw catalog cost for THIS line; stamped as
        # cost_at_sale below and fed to the floor pass. None (virtual id /
        # no product repo / no cost_price) keeps the line fail-open.
        _cost = _line_gate["cost"]

        # STACKING CAP -- parity with create_order, same shared raiser. The bill
        # discount ALREADY on this DRAFT multiplies with the new line's own
        # discount, so a line that clears its cap can still put the ticket over
        # it. Without this, setting a 10% bill discount, saving, and THEN adding
        # a 10% line was a 19% sale that neither cap saw.
        if not _is_admin:
            assert_stack_within_cap(
                item.product_name or _pid or "this line",
                _eff_disc,
                float(order.get("cart_discount_percent") or 0.0),
                _cap,
            )

        # (The manual-discount-needs-a-reason rule now lives in the shared
        # _enforce_line_pricing gate above -- one copy, no more lockstep.)

        # Calculate item totals
        discount_amount, item_subtotal = bill_line_value(
            item.unit_price, item.quantity, item.discount_percent
        )

        item_data = {
            "item_id": str(uuid.uuid4()),
            "item_type": item.item_type,
            "product_id": _pid,
            # Parity with create_order's line shape (chair P1): the name makes
            # the floor 400 actionable; cost_at_sale freezes COGS exactly like
            # create does (None when unknown -> floor fails open on this line).
            "product_name": item.product_name,
            "cost_at_sale": _cost,
            # Parity with create_order's line stamps: the master's brand (the
            # luxury-cap key), the master's discount TIER, and the catalog MRP
            # at sale time. Deliberately still NOT `category` (GST resolution,
            # see the F15 note below).
            "brand": (product or {}).get("brand") or getattr(item, "brand", None),
            "discount_category": (product or {}).get("discount_category"),
            "mrp": _line_gate["mrp"],
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "discount_percent": item.discount_percent,
            "effective_discount_percent": round(_loyalty_eff, 4),
            "discount_amount": discount_amount,
            # The guard above VALIDATED these; persist them or the audit
            # trail records a demanded-but-dropped reason (panel round 3).
            "discount_reason": item.discount_reason,
            # Server-resolved below (never the request's): see _approver.
            "discount_approved_by": None,
            "item_total": item_subtotal,
            "prescription_id": item.prescription_id,
            "lens_options": item.lens_options,
            # F15: the stock/lens identity of the line. WITHOUT these the
            # appended line could not be reserved (a LENS line had no cell
            # coordinates to reserve) nor released on cancel, and an explicitly
            # scanned unit lost its lineage. Deliberately does NOT add
            # `category` -- that feeds _compute_per_category_gst and would
            # change this path's tax resolution.
            "lens_details": item.lens_details,
            "stock_id": getattr(item, "stock_id", None),
            "lens_line_id": getattr(item, "lens_line_id", None),
            "sph": getattr(item, "sph", None),
            "cyl": getattr(item, "cyl", None),
            "add": getattr(item, "add", None),
            "axis": getattr(item, "axis", None),
        }

        # Add item and recalculate totals — preserves per-category GST
        # (Phase 6.15 fix) instead of stamping the order's old flat
        # tax_rate. Mirrors the frontend's getGrandTotal exactly.
        _existing_items = order.get("items", [])
        # The lens-reservation key for the appended line is its OWN item_id
        # (uuid4, above) -- never a positional or max+1 index, both of which can
        # be reused by a later append after a delete. `line_index` is still
        # stamped for continuity with orders written by the older code.
        item_data["line_index"] = len(_existing_items)
        items = _existing_items + [item_data]
        cart_discount_percent = order.get("cart_discount_percent", 0) or 0
        gst = _compute_per_category_gst(items, cart_discount_percent, rounded=bills_round_off(order))
        grand_total = gst["grand_total"]

        # Fcostfloor (chair P1): the add-items path must honor the SAME
        # post-discount cost+pct% floor as create_order -- it mirrored every
        # legacy guard but skipped the floor, so a cap-legal line that nets
        # below cost*(1+pct/100) could be appended to a clean DRAFT order.
        # Validate the COMBINED line list on the just-recomputed taxable
        # finals BEFORE persisting: a 400 here leaves the order untouched.
        # Owner rev 2 (discounted sales only): the cart-discount presence is
        # derived from the persisted order doc's cart_discount fields.
        from ...services.cost_floor import enforce_cost_floor

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        enforce_cost_floor(
            items,
            {_pid: _cost},
            order.get("store_id"),
            order_has_cart_discount=bool(
                _f(cart_discount_percent) > 0
                or _f(order.get("cart_discount_amount")) > 0
            ),
            user=current_user,
        )

        # The seller's ROLE limit on the line's stacked figure, the Rs 0 rule,
        # and who is recorded as approving them: THE rule order create uses.
        # After every hard limit above, before anything is reserved below. The
        # line is a Rs 0 change when it is a giveaway line itself (100% off, or
        # Rs 0 with no MRP) or it leaves the draft at Rs 0.
        _cart_pct = float(cart_discount_percent or 0.0)
        _zero = discount_approval.is_full_line(item_data) or round(float(grand_total), 2) <= 0.0
        _zero_reason = (
            discount_approval.zero_total_reason(order.get("cart_discount_reason"), [item_data])
            if _zero
            else None
        )
        _bill = discount_approval.bill_of(
            store_id=order.get("store_id"),
            lines=[
                {
                    "product_id": _pid,
                    "product_name": (product or {}).get("name") or item.product_name,
                    "name_from_till": product is None,
                    "quantity": item.quantity,
                    "mrp": _line_gate["mrp"],
                    "unit_price": item.unit_price,
                    "discount_percent": item.discount_percent,
                    "combined_percent": combined_discount_pct(_eff_disc, _cart_pct),
                }
            ],
            cart_discount_percent=_cart_pct,
            discount_rupees=discount_amount + item_subtotal * _cart_pct / 100.0,
            bill_before=_f(order.get("grand_total")),
            bill_after=grand_total,
            zero_total=_zero,
            order_id=order_id,
            change="add",
            customer_id=order.get("customer_id"),
            customer_name=order.get("billed_to_member_name") or order.get("customer_name"),
        )
        if request_approval:
            return discount_approval.ask_manager(current_user, _bill, approval_note)
        _approver = discount_approval.resolve_discount_approver(
            current_user, _bill, discount_approval_id
        )
        if _eff_disc > 1e-9 or _zero:
            item_data["discount_approved_by"] = (_approver or {}).get("user_id")
            item_data["discount_approved_by_name"] = (_approver or {}).get("name")

        # ------------------------------------------------------------------
        # F15 (P3 STOCK): RESERVE THE APPENDED LINE, exactly like create_order.
        # This path enforced pricing/GST/caps/floor but never touched stock:
        # a frame added to a DRAFT order stayed AVAILABLE (double-sellable by
        # the next customer) and an added LENS never reserved its power-grid
        # cell. Same order of operations as create: availability assert ->
        # lens reserve (BEFORE persist, so a 409 leaves the order untouched)
        # -> persist -> mark serialized units SOLD (after persist, fail-soft).
        # ------------------------------------------------------------------
        _store_id = order.get("store_id")
        _assert_serialized_stock_available([item_data], _store_id)

        from ...services.lens_stock_hook import (
            reserve_for_order_item,
            release_for_cancel,
        )

        _line_idx = _lens_reservation_key(item_data, item_data["line_index"])
        try:
            await reserve_for_order_item(
                order_item=item_data,
                order_id=order_id,
                line_index=_line_idx,
                store_id=_store_id or "",
                user=current_user,
            )
        except HTTPException as exc:
            # Insufficient lens stock (409) -- compensating release for this one
            # line, then re-raise so POS shows "available=N". The order doc has
            # NOT been touched.
            if exc.status_code == 409:
                try:
                    await release_for_cancel(
                        order_item=item_data,
                        order_id=order_id,
                        line_index=_line_idx,
                        store_id=_store_id or "",
                        user=current_user,
                    )
                except Exception as rb_exc:  # noqa: BLE001
                    logger.warning(
                        "[LENS_HOOK] add-item compensating release failed "
                        "(order %s line %s): %s",
                        order_id,
                        _line_idx,
                        rb_exc,
                    )
            raise
        except Exception as exc:  # noqa: BLE001
            # Transient hook failure -- never block adding the line.
            logger.warning(
                "[LENS_HOOK] add-item reserve fail-soft (order %s): %s",
                order_id,
                exc,
            )

        # Every unit the line takes is claimed before the save, as at create
        # (a 409 here leaves the order untouched; a scanned line reserves no
        # lens cell).
        claimed = _claim_units(order_id, [item_data], _store_id)
        try:
            # Spend the manager's approval (if one lifted the limit) last thing
            # before the save -- atomic, single use, as at create. ANY failure
            # here gives back the claimed unit and the lens cell.
            discount_approval.spend(_approver, current_user, _store_id)
        except Exception:
            _release_claimed_units(order_id, claimed)
            try:
                await release_for_cancel(
                    order_item=item_data,
                    order_id=order_id,
                    line_index=_line_idx,
                    store_id=_store_id or "",
                    user=current_user,
                )
            except Exception:  # noqa: BLE001
                pass  # fail-soft compensating action
            raise
        try:
            persisted = repo.update(
                order_id,
                {
                    "items": items,
                    "subtotal": gst["subtotal"],
                    "cart_discount_amount": gst["cart_discount_amount"],
                    "tax_rate": gst["dominant_rate"],
                    "tax_amount": gst["tax"],
                    "total_discount": gst["total_discount"],
                    "grand_total": grand_total,
                    "round_off": gst["round_off"],
                    "balance_due": grand_total - order.get("amount_paid", 0),
                    **(
                        {
                            "zero_total": True,
                            "zero_total_approved_by": (_approver or {}).get("user_id"),
                            "zero_total_approved_by_name": (_approver or {}).get("name"),
                        }
                        if _zero
                        else {}
                    ),
                },
            )
        except Exception as upd_exc:  # noqa: BLE001
            persisted = False
            logger.error(
                "[ORDERS] add-item persist failed for %s: %s", order_id, upd_exc
            )
        if not persisted:
            # The line never landed on the order -> give the lens cell back so
            # the reservation cannot leak against a line that does not exist.
            # The units and the approval go back only once it is confirmed the
            # line did NOT land (the write may have landed before the error).
            def _line_was_saved() -> bool:
                return any(
                    ln.get("item_id") == item_data["item_id"]
                    for ln in (repo.find_by_id(order_id) or {}).get("items", [])
                )

            _release_claimed_units(order_id, claimed, _line_was_saved)
            discount_approval.give_back(_approver, current_user, _line_was_saved)
            try:
                await release_for_cancel(
                    order_item=item_data,
                    order_id=order_id,
                    line_index=_line_idx,
                    store_id=_store_id or "",
                    user=current_user,
                )
            except Exception:  # noqa: BLE001
                pass  # fail-soft compensating action
            raise HTTPException(status_code=500, detail="Failed to add item to order")

        if _zero:
            discount_approval.audit_zero_total(
                order_id=order_id,
                store_id=_store_id,
                user_id=current_user.get("user_id"),
                approver=_approver,
                reason=_zero_reason,
                details={"grand_total": grand_total, "door": "add_line",
                         "item_id": item_data["item_id"]},
            )

        # Serialized units -> SOLD with this order_id stamped (fail-soft: a
        # stock-side failure must never lose the line the user just added).
        try:
            _mark_units_sold(order_id, [item_data], _store_id, claimed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[STOCK] add-item mark_units_sold failed: %s", exc)

        # IMS = inventory MASTER: the unit just left this shop's shelf, so its
        # Shopify location must stop selling it now -- the same fail-soft,
        # fire-and-forget call the create-order door makes. This route is a
        # door too (an API client adding a line to a DRAFT order); without it
        # the website kept the pre-sale number until the next scheduled pass.
        try:
            from ...services.online_stock_writeback import writeback_after_sale

            writeback_after_sale(None, [item_data], _store_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[STOCK] online write-back skipped: %s", exc)

        return {"message": "Item added to order", "item_id": item_data["item_id"]}

    return {"message": "Item added to order"}


@router.delete("/{order_id}/items/{item_id}")
async def remove_order_item(
    order_id: str,
    item_id: str,
    current_user: dict = Depends(get_current_user),
    # Deleting a line can leave a DRAFT at Rs 0: that needs the same manager
    # sign-off as a Rs 0 sale (services/discount_approval), carried here.
    # Annotated so a direct call (tests, other doors) gets the plain default,
    # not a truthy Query() object.
    discount_approval_id: Annotated[Optional[str], Query(max_length=64)] = None,
    request_approval: Annotated[bool, Query()] = False,
    approval_note: Annotated[Optional[str], Query(max_length=300)] = None,
    # The written reason for taking the draft to Rs 0 (the C-4 rule): needed
    # when the draft carries none of its own; still refused without one.
    reason: Annotated[Optional[str], Query(max_length=200)] = None,
):
    """Remove item from order (only DRAFT orders)"""
    repo = get_order_repository()

    if repo is not None:
        order = repo.find_by_id(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # IDOR guard: mirror GET /{order_id} -- only act on an order in a store
        # the caller can access (403 otherwise; SUPERADMIN/ADMIN pass through).
        validate_store_access(order.get("store_id"), current_user)

        if order.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Can only remove items from DRAFT orders"
            )

        _all_items = order.get("items", [])
        items = [i for i in _all_items if i.get("item_id") != item_id]
        if len(items) == len(_all_items):
            raise HTTPException(status_code=404, detail="Item not found in order")

        _removed_pos, _removed = next(
            (
                (pos, line)
                for pos, line in enumerate(_all_items)
                if line.get("item_id") == item_id
            ),
            (0, {}),
        )

        # Recalculate totals (per-category GST, mirrors create_order).
        cart_discount_percent = order.get("cart_discount_percent", 0) or 0
        gst = _compute_per_category_gst(items, cart_discount_percent, rounded=bills_round_off(order))
        grand_total = gst["grand_total"]

        # C-4 on the delete door: a deletion that leaves the draft at Rs 0 is a
        # Rs 0 sale reached in two steps -- the same reason + MANAGER sign-off +
        # ORDER_ZERO_TOTAL_APPROVED row as create. The surviving lines'
        # discounts were approved when they were added, so only the Rs 0
        # sign-off is asked for here (their combined % is not re-measured).
        try:
            _was_paid = round(float(order.get("grand_total") or 0.0), 2) > 0.0
        except (TypeError, ValueError):
            _was_paid = True
        _zero = bool(items) and _was_paid and round(float(grand_total), 2) <= 0.0
        _approver = None
        _zero_reason = None
        if _zero:
            _zero_reason = discount_approval.zero_total_reason(
                reason if (reason or "").strip() else order.get("cart_discount_reason"),
                [ln for ln in items if discount_approval.is_full_line(ln)],
            )
            _bill = discount_approval.bill_of(
                store_id=order.get("store_id"),
                lines=[
                    {
                        "product_id": ln.get("product_id"),
                        "product_name": ln.get("product_name"),
                        "quantity": ln.get("quantity"),
                        "mrp": ln.get("mrp"),
                        "unit_price": ln.get("unit_price"),
                        "discount_percent": ln.get("discount_percent"),
                        "combined_percent": 0.0,
                    }
                    for ln in items
                ],
                cart_discount_percent=float(cart_discount_percent or 0.0),
                discount_rupees=gst["total_discount"],
                bill_before=float(order.get("grand_total") or 0.0),
                bill_after=grand_total,
                zero_total=True,
                order_id=order_id,
                change=f"remove:{item_id}",
                customer_id=order.get("customer_id"),
                customer_name=order.get("billed_to_member_name") or order.get("customer_name"),
            )
            if request_approval:
                return discount_approval.ask_manager(current_user, _bill, approval_note)
            _approver = discount_approval.resolve_discount_approver(
                current_user, _bill, discount_approval_id
            )
            discount_approval.spend(_approver, current_user, order.get("store_id"))

        # PERSIST FIRST, THEN RELEASE (panel must-fix 6). This used to release
        # the stock BEFORE the write and discard repo.update's return value --
        # and base_repository.update swallows exceptions and returns False, so a
        # failed persist answered 200 "Item removed" with the line STILL BILLED
        # to the customer AND its frame back on the sellable shelf. Releasing
        # only after a CONFIRMED write means the worst case is the mirror of the
        # one we can actually recover from: the line is gone and the stock is
        # still SOLD, which the cancel/re-run path can reclaim.
        try:
            persisted = repo.update(
                order_id,
                {
                    "items": items,
                    "subtotal": gst["subtotal"],
                    "cart_discount_amount": gst["cart_discount_amount"],
                    "tax_rate": gst["dominant_rate"],
                    "tax_amount": gst["tax"],
                    "total_discount": gst["total_discount"],
                    "grand_total": grand_total,
                    "round_off": gst["round_off"],
                    "balance_due": grand_total - order.get("amount_paid", 0),
                    **(
                        {
                            "zero_total": True,
                            "zero_total_approved_by": (_approver or {}).get("user_id"),
                            "zero_total_approved_by_name": (_approver or {}).get("name"),
                        }
                        if _zero
                        else {}
                    ),
                },
            )
        except Exception as upd_exc:  # noqa: BLE001
            persisted = False
            logger.error(
                "[ORDERS] item-remove persist failed for %s/%s: %s",
                order_id,
                item_id,
                upd_exc,
            )
        if not persisted:
            # Nothing was removed -> release NOTHING. The line is still billed
            # and its unit must stay SOLD to match. A spent Rs 0 approval goes
            # back only once it is confirmed the line is still on the order.
            discount_approval.give_back(
                _approver,
                current_user,
                lambda: not any(
                    ln.get("item_id") == item_id
                    for ln in (repo.find_by_id(order_id) or {}).get("items", [])
                ),
            )
            raise HTTPException(
                status_code=500, detail="Failed to remove item from order"
            )
        if _zero:
            discount_approval.audit_zero_total(
                order_id=order_id,
                store_id=order.get("store_id"),
                user_id=current_user.get("user_id"),
                approver=_approver,
                reason=_zero_reason,
                details={"grand_total": grand_total, "door": "remove_line",
                         "item_id": item_id},
            )

        # F15 (symmetry): the removed line's stock must go BACK. create_order /
        # add_order_item flip serialized units SOLD and reserve lens cells at the
        # moment the line is added, so deleting the line without releasing them
        # would strand a sellable frame as SOLD forever and leak the lens cell --
        # the same permanent-loss bug F3 fixes for cancel.
        try:
            from ...services.lens_stock_hook import release_for_cancel

            await _release_lens_lines(
                [_removed],
                order_id=order_id,
                store_id=order.get("store_id") or "",
                user=current_user,
                release=release_for_cancel,
                # The removed line's TRUE position in the pre-removal order.
                # Legacy reservations are keyed on exactly this number, so
                # defaulting to 0 released the wrong cell (or someone else's).
                positions=[_removed_pos],
            )
        except Exception as rel_exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] release on item-remove failed (order %s item %s): %s",
                order_id,
                item_id,
                rel_exc,
            )

        _freed_units, _restock_failed = _release_line_units(
            order_id,
            item_id,
            _removed,
            surviving_lines=items,
            store_id=order.get("store_id"),
        )
        if _restock_failed:
            # PERSIST the failure so it is discoverable, mirroring what cancel
            # does. Previously `incomplete` reached only a log line and the
            # endpoint answered 200 "Item removed" regardless.
            try:
                repo.update(
                    order_id,
                    {
                        "line_remove_stock_release_failed": True,
                        "line_remove_stock_failed_item_id": item_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[ORDERS] could not stamp item-remove restock failure "
                    "(order %s item %s): %s",
                    order_id,
                    item_id,
                    exc,
                )

        # Audit alert (May 2026) — flag the deleted item even on DRAFT
        # so the audit trail is complete; severity HIGH for DRAFT.
        try:
            from ...services.audit_alerts import alert_item_deleted
            import asyncio as _aio

            removed = next(
                (i for i in order.get("items", []) if i.get("item_id") == item_id),
                {"item_id": item_id},
            )
            _aio.create_task(
                alert_item_deleted(
                    order_id,
                    item_id,
                    item_data=removed,
                    user_id=current_user.get("user_id"),
                    order_status=order.get("status", "DRAFT"),
                )
            )
        except Exception:
            pass

        return {
            "message": "Item removed from order",
            "stock_units_released": len(_freed_units),
            "stock_release_failed": _restock_failed,
        }

    return {"message": "Item removed from order"}
