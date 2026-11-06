"""SUPERADMIN order edit and invoice change, with the audit writer.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from api.utils.event_time import utc_now_iso

from database.repositories.order_repository import (
    InvoiceNumberError,
    derive_bill_type,
    order_mint_problem,
    next_document_number,
)
from contextlib import asynccontextmanager
from datetime import datetime
from functools import partial
from fastapi import Depends, HTTPException
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
from ..auth import get_current_user
from ...dependencies import (
    get_order_repository,
    get_product_repository,
    get_stock_repository,
    validate_store_access,
)
from ._shared import (
    _compute_per_category_gst,
    _get_db,
    bills_round_off,
    logger,
    router,
)
from .models import (
    SuperadminInvoiceChange,
    SuperadminOrderEdit,
)
from .release import CANCELLABLE_STATUSES, _release_lens_lines, _release_line_units
from .stock import (
    _assert_goods_name_a_product,
    _assert_line_type_matches_master,
    _assert_serialized_stock_available,
    _canonical_pid,
    _claim_units,
    _release_claimed_units,
    _resolve_billable_product,
    _stock_class,
    _takes_serialized_stock,
)
from ...services.lens_stock_hook import _extract_cell_key, _is_lens_item, takes_lens_cell


# ============================================================================
# Build item #16 - SUPERADMIN post-creation order edit (revenue/GST/audit)
# ============================================================================
def _require_superadmin(current_user: dict) -> None:
    """Gate the post-creation order/invoice edit (build item #16) to SUPERADMIN
    OR ADMIN (owner decision 2026-06-19: admins may also edit a created
    order/invoice -- still a privileged, fully-audited override). Raises 403 for
    every other role. (Name kept for call-site stability; it now allows ADMIN.)"""
    roles = current_user.get("roles") or []
    if "SUPERADMIN" not in roles and "ADMIN" not in roles:
        raise HTTPException(
            status_code=403,
            detail="Only an ADMIN or SUPERADMIN may edit an order after it is created.",
        )


def _write_order_edit_audit(
    *,
    action: str,
    order: dict,
    before: dict,
    after: dict,
    reason: str,
    user_id: Optional[str],
    extra: Optional[dict] = None,
) -> bool:
    """Write the SYNCHRONOUS, immutable hash-chained audit row for a
    SUPERADMIN order/invoice change BEFORE the endpoint returns.

    Unlike the fire-and-forget alert on the DRAFT PUT path, this is a blocking
    write (revenue/GST/audit-critical, SYSTEM_INTENT 10 "Audit Everything"):
    the before/after snapshots + the human reason are committed to the
    append-only ``audit_logs`` chain (audit_chain.append_audit_entry) so the
    change is tamper-evident at GET /api/v1/audit/verify. We use the chained
    fields the hash commits to: before_state / after_state / diff / detail.
    Returns True on a chained write; False if no audit repo (DB-less).
    """
    from ...dependencies import get_audit_repository
    from ...services.order_superadmin_edit import build_money_diff

    audit = get_audit_repository()
    if audit is None:
        return False
    row = {
        "action": action,
        "entity_type": "order",
        "entity_id": order.get("order_id"),
        "store_id": order.get("store_id"),
        "user_id": user_id,
        "severity": "WARNING",
        "detail": reason,
        "before_state": before,
        "after_state": after,
        "diff": build_money_diff(before, after),
        "timestamp": utc_now_iso(),
        "created_at": utc_now_iso(),
    }
    if extra:
        row["context"] = extra
    audit.create(row)
    return True


def _same_kind(line: dict, stored: dict) -> bool:
    """Same kind of stock? An unreadable type (an old line with none) is a
    shelf item, as _takes_serialized_stock has it."""
    return (_stock_class(line.get("item_type")) or "serialized") == (
        _stock_class(stored.get("item_type")) or "serialized"
    )


# What names a line's lens cell, and what else a correction keeps of the stored
# line: the edit screen sends none of these back.
_CELL_FIELDS = ("lens_line_id", "sph", "cyl", "add", "lens_options")
_HELD_FIELDS = ("stock_id", "line_index", "axis", "lens_details", "prescription_id")

# The remove-item door's failure flag, and the token of a settle still to run.
_RELEASE_FAILED = "line_remove_stock_release_failed"
_RELEASE_FAILED_ITEM = "line_remove_stock_failed_item_id"
_SETTLE_TOKEN = "line_remove_settle_token"


def _same_product(line: dict, stored: dict) -> bool:
    return (line.get("product_id") or "") == (stored.get("product_id") or "") and _same_kind(
        line, stored
    )


def _same_cell(line: dict, stored: dict) -> bool:
    """A lens line sent with no power still bills the stored cell; any power it
    does send may name another."""
    sent = {k: line[k] for k in _CELL_FIELDS if line.get(k) is not None}
    return _extract_cell_key({**stored, **sent}) == _extract_cell_key(stored)


def _same_goods(line: dict, stored: dict) -> bool:
    """Is this edited line still the stored line's physical goods? Same product,
    same kind of stock, not a different named unit, not a different lens cell.
    Anything else is a replacement: the stored line's stock goes back, the new
    line takes its own."""
    sid = line.get("stock_id")
    return (
        _same_product(line, stored)
        and (not sid or sid == stored.get("stock_id"))
        and _same_cell(line, stored)
    )


def _pair_with_stored(payloads: list, existing_items: list) -> Tuple[list, list]:
    """The stored line each incoming line corrects, one to one, and the stored
    lines nothing corrects (removed). By item_id, then -- for a line sent with
    none, or a legacy line that has none -- the same goods: a stored line sent
    twice corrects once, so its copy is a NEW line and needs its own unit."""
    unclaimed = list(existing_items or [])

    def take(match) -> Optional[dict]:
        for pos, stored in enumerate(unclaimed):
            if match(stored):
                return unclaimed.pop(pos)
        return None

    paired = [
        take(lambda s, p=p: s.get("item_id") == p.get("item_id")) if p.get("item_id") else None
        for p in payloads
    ]
    for at, payload in enumerate(payloads):
        if paired[at] is None and (_takes_serialized_stock(payload) or _is_lens_item(payload)):
            paired[at] = take(lambda s, p=payload: _same_goods(p, s))
    return paired, unclaimed


def _rebuilt_items_or_existing(
    body_items, existing_items: list, editor: Optional[dict] = None
) -> Tuple[list, List[dict], List[dict]]:
    """Resolve the corrected line set for an edit. When the caller supplies
    ``items`` it REPLACES the whole set (each normalised via rebuild_edited_line
    so item_total/discount are recomputed server-side); when omitted, the
    existing lines are kept (a customer-only / cart-discount-only edit).

    ``editor`` -- the admin making the edit: a line whose discount or price
    the edit CHANGES (or a new line) is now approved by them; an untouched
    line keeps the approver the server stamped at sale.

    Returns (lines, added, removed): what the edit does to the shelf. ``added``
    are the goods it newly bills (they meet _gate_added_lines and take units and
    lens cells), ``removed`` the goods it stops billing. A correction of an
    unchanged line is in neither, keeps the stock it holds and answers to no
    stock or master rule -- the bill may be corrected long after the product was
    archived. A lowered quantity's ``removed`` entry names its line in ``part_of``."""
    from ...services.order_superadmin_edit import rebuild_edited_line
    from ...services.discount_approval import who

    if body_items is None:
        return [dict(it) for it in (existing_items or [])], [], []
    # SuperadminEditLine carries no discount_reason/discount_approved_by
    # (Pydantic strips unknown keys), so merge them from the STORED line by
    # item_id — an edit must not wipe the accountability trail. The approver
    # is only ever the one the server stamped at sale
    # (services/discount_approval); this door never takes one from a client.
    payloads = [
        line.model_dump() if hasattr(line, "model_dump") else dict(line)
        for line in body_items
    ]
    paired, removed = _pair_with_stored(payloads, existing_items)
    rebuilt: List[dict] = []
    added: List[dict] = []
    for payload, stored in zip(payloads, paired):
        stored = stored or {}
        # A line sent without its product still bills the stored one: leaving
        # the field out is not a swap, and must not free the unit it holds.
        if stored and not payload.get("product_id") and _same_kind(payload, stored):
            payload["product_id"] = stored.get("product_id")
        same = bool(stored) and _same_goods(payload, stored)
        carried = ["discount_reason", "discount_approved_by", "discount_approved_by_name"]
        if same:
            carried += _HELD_FIELDS
        if stored and _same_product(payload, stored):
            carried += _CELL_FIELDS  # a new power of this lens: the rest of its cell
        for _k in carried:
            if payload.get(_k) is None and stored.get(_k) is not None:
                payload[_k] = stored.get(_k)
        # A replacement is a new line with a fresh id: its reservation key must
        # never be the stored line's, which is about to carry a #release row.
        payload["item_id"] = stored.get("item_id") if same else None
        line = rebuild_edited_line(payload)
        if editor is not None and _discount_changed(line, stored):
            # The admin changed what this line sells for: they answer for it.
            approver = who(editor)
            line["discount_approved_by"] = approver["user_id"]
            line["discount_approved_by_name"] = approver["name"]
        rebuilt.append(line)
        if not same:
            added.append(line)
            if stored:
                removed.append(stored)
            continue
        extra = line["quantity"] - int(stored.get("quantity") or 1)
        if extra > 0:
            # The line keeps its unit; only the extra pieces are new, under their
            # own lens key (the line's key already holds its reserve row). The
            # line's release and commit run on its whole quantity, so they cover
            # these too. ponytail: a line that never held a reservation (a soft
            # failure at sale) or is already cut never releases them; the cell
            # then reads low, never oversold, until the Power Grid is corrected.
            added.append({**line, "quantity": extra, "stock_id": None, "item_id": str(uuid4())})
        elif extra < 0:
            removed.append({**stored, "quantity": -extra, "stock_id": None, "part_of": stored})
    if not rebuilt:
        raise HTTPException(
            status_code=400, detail="An edited order must keep at least one item."
        )
    return rebuilt, added, removed


def _gate_added_lines(added: List[dict], store_id: Optional[str]) -> None:
    """Goods an edit newly bills meet the till's rules (L2-STOCK-F01/F02, L1-F39):
    a named product (only a true service may be free text), a billable,
    unarchived master, the master's stock type, and units on this shop's shelf.
    Read-only; _take_added_stock takes the units and the lens cells. The caps
    and floors are not re-applied: price stays the admin's, this door's audited
    override. An edit that adds nothing touches neither the product table nor
    the shelf."""
    if not added:
        return
    product_repo = get_product_repository()
    for line in added:
        pid = line.get("product_id") or ""
        label = line.get("product_name") or pid
        _assert_goods_name_a_product(line.get("item_type"), line.get("category"), pid, label)
        if product_repo is None:
            continue
        product = _resolve_billable_product(product_repo, pid, label)
        _assert_line_type_matches_master(line.get("item_type"), product, label)
        if product is not None:
            line["product_id"] = _canonical_pid(product_repo, pid)
    _assert_serialized_stock_available(added, store_id)


async def _give_stock_back(order: dict, claimed: dict, cells: List[dict], user: dict) -> None:
    """Undo _take_added_stock for an edit that was not saved. The hook's release
    is fail-soft and each line's key is its own, so no legacy key is tried."""
    from ...services.lens_stock_hook import release_for_cancel

    _release_claimed_units(order.get("order_id"), claimed)
    for line in cells:
        await release_for_cancel(
            order_item=line,
            order_id=order.get("order_id"),
            line_index=line["item_id"],
            store_id=order.get("store_id") or "",
            user=user,
        )


async def _take_added_stock(
    order: dict, added: List[dict], user: dict
) -> Tuple[dict, List[dict], bool]:
    """Take what the edit newly bills BEFORE it is saved, in the till's order:
    lens cells (a 409 here is the lens stock gate), then units. Anything refused
    gives back what this call took and leaves the order untouched. Returns
    (units claimed, lens lines reserved, did a reserve fail soft)."""
    from ...services.lens_stock_hook import reserve_for_order_item

    cells: List[dict] = []
    soft_failed = False
    try:
        for line in added:
            rec = await reserve_for_order_item(
                order_item=line,
                order_id=order.get("order_id"),
                line_index=line["item_id"],
                store_id=order.get("store_id") or "",
                user=user,
            )
            if rec is not None:
                cells.append(line)
                soft_failed = soft_failed or rec.get("status") == "failed"
        claimed = _claim_units(order.get("order_id"), added, order.get("store_id"))
    except Exception as exc:
        await _give_stock_back(order, {}, cells, user)
        _flag_claims_unaccounted(order, added, exc)
        raise
    return claimed, cells, soft_failed


@asynccontextmanager
async def _stock_back_unless_saved(
    order: dict, claimed: dict, cells: List[dict], user: dict, repo, field: str, value, settle
):
    """The save, and what follows it. `field` == `value` on the stored order
    proves the write landed: what the edit took stays and `settle` runs, even
    though only the reply was lost, because no retry can do it later (the stored
    lines already match the body). Otherwise what the edit took goes back. An
    order that cannot be read keeps it too (fail closed); _settle_owed left the
    bill flagged if that save landed."""
    try:
        yield
    except Exception:
        try:
            stored = repo.find_by_id(order.get("order_id"))
        except Exception:  # noqa: BLE001 -- the read's fault must not hide the save's
            stored = None
        if stored is None:
            logger.warning("[STOCK] could not tell if order %s was saved", order.get("order_id"))
        elif stored.get(field) != value:
            await _give_stock_back(order, claimed, cells, user)
        else:
            await settle()
        raise
    await settle()


def _settle_owed(order: dict, removed: List[dict]) -> Dict[str, Any]:
    """What a save that drops stock writes with itself: the bill reads as owing
    it until the settle says otherwise, so a settle that never ran (the server
    died, the order could not be read back) is not silent. The token is what
    lets that settle run only once. Only a bill the counter may still cancel
    gives stock back (the cancel door's own rule): on any other, a parcel or the
    customer holds it, and only a return puts one back."""
    if not removed or str(order.get("status") or "").upper() not in CANCELLABLE_STATUSES:
        return {}
    owed: Dict[str, Any] = {_SETTLE_TOKEN: uuid4().hex}
    if not order.get(_RELEASE_FAILED):
        owed[_RELEASE_FAILED] = True
        owed[_RELEASE_FAILED_ITEM] = removed[0].get("item_id")
    return owed


def _claim_settle(repo, order_id: str, token: str) -> bool:
    """Take the save's token: a second run finds it gone and moves nothing."""
    try:
        return repo.update_if(order_id, {_SETTLE_TOKEN: token}, {}, unset=[_SETTLE_TOKEN])
    except Exception as exc:  # noqa: BLE001 -- landed or not, this run holds it
        logger.warning("[STOCK] settle claim unclear (order %s): %s", order_id, exc)
        return True


def _flag_release_failed(repo, order_id: str, item_id) -> None:
    if not repo.update(order_id, {_RELEASE_FAILED: True, _RELEASE_FAILED_ITEM: item_id}):
        logger.error("[STOCK] could not flag order %s: %s not given back", order_id, item_id)


def _flag_claims_unaccounted(order: dict, added: List[dict], exc: Exception) -> None:
    """A claim that wrote but lost its reply reads as a refusal, and on a saved
    bill the unit must stay: nothing tells it from a second press's claim. It is
    SOLD here with no line behind it, so the bill says so. A first-available
    claim's fault (503) names no unit; a plain refusal took nothing."""
    order_id = order.get("order_id")
    held = {ln.get("stock_id") for ln in order.get("items") or []}
    try:
        stock_repo = get_stock_repository()
        stray = [
            sid
            for sid in {ln.get("stock_id") for ln in added} - held - {None, ""}
            if (stock_repo.find_by_id(sid) or {}).get("order_id") == order_id
        ]
    except Exception as read_exc:  # noqa: BLE001 -- the refusal stands
        logger.warning("[STOCK] could not check the claims of order %s: %s", order_id, read_exc)
        stray = []
    if not stray and getattr(exc, "status_code", None) == 503:
        stray = [ln.get("item_id") for ln in added if _takes_serialized_stock(ln)]
    if stray:
        _flag_release_failed(get_order_repository(), order_id, stray[0])


async def _settle_removed_lines(
    order: dict, update_data: dict, added: List[dict], removed: List[dict],
    repo, user: dict,
) -> None:
    """After the edit landed: the goods it stopped billing go back on the shelf
    (the remove-item door's own release; anything that falls short is flagged on
    the order) and the website hears of every piece that moved. Runs once per
    save: a first-available unit has no name a second run could miss."""
    order_id, store_id = order.get("order_id"), order.get("store_id")
    token = update_data.get(_SETTLE_TOKEN)
    if token and not _claim_settle(repo, order_id, token):
        return
    moved = list(added)
    if token:
        from ...services.lens_stock_hook import release_for_cancel

        moved += removed
        failed: List[Optional[str]] = []

        async def release(*, order_item, order_id, line_index, store_id, user, **part) -> None:
            rec = await release_for_cancel(
                order_item=order_item, order_id=order_id, line_index=line_index,
                store_id=store_id, user=user, **part,
            )
            if (rec or {}).get("status") == "failed":
                failed.append(order_item.get("item_id"))

        lines = order.get("items") or []
        whole = [(pos, ln) for pos, ln in enumerate(lines) if any(ln is r for r in removed)]
        parts = [
            (pos, r) for pos, ln in enumerate(lines) for r in removed if r.get("part_of") is ln
        ]
        try:
            await _release_lens_lines(
                [ln for _pos, ln in whole],
                order_id=order_id,
                store_id=store_id or "",
                user=user,
                release=release,
                positions=[pos for pos, _ln in whole],
            )
            for pos, part in parts:
                await _release_lens_lines(
                    [part],
                    order_id=order_id,
                    store_id=store_id or "",
                    user=user,
                    release=partial(release, part=token),
                    positions=[pos],
                )
        except Exception as exc:  # noqa: BLE001 -- the edit stands
            logger.warning("[LENS_HOOK] release on edit failed (order %s): %s", order_id, exc)
            failed.append(removed[0].get("item_id"))
        for line in removed:
            item_id = line.get("item_id")
            _freed, short = _release_line_units(
                order_id, item_id, line, surviving_lines=update_data["items"], store_id=store_id
            )
            if short:
                failed.append(item_id)
        if failed:
            _flag_release_failed(repo, order_id, failed[0])
        elif not order.get(_RELEASE_FAILED):
            try:
                repo.update_if(order_id, {}, {}, unset=[_RELEASE_FAILED, _RELEASE_FAILED_ITEM])
            except Exception as exc:  # noqa: BLE001 -- a false alarm, never a lost unit
                logger.warning("[STOCK] flag not cleared (order %s): %s", order_id, exc)
    if moved:
        from ...services.online_stock_writeback import writeback_after_sale

        writeback_after_sale(None, moved, store_id)


async def _settle_saved_edit(
    order: dict, update_data: dict, added: List[dict], removed: List[dict],
    repo, user: dict,
) -> None:
    """Everything that follows a landed edit, on both doors. Never raises: a
    lost reply's own error is the one the caller sees."""
    await _settle_removed_lines(order, update_data, added, removed, repo, user)
    # Owner hard rule: an edit that raises discounts must not leave the
    # create-time loyalty earn standing. Delta-claw, fail-soft.
    try:
        from ..loyalty import regate_earn_after_edit

        regate_earn_after_edit({**order, **update_data}, user_id=user.get("user_id"))
    except Exception:
        pass  # loyalty must never block a superadmin edit


def _discount_changed(line: dict, stored: dict) -> bool:
    """Did the edit change what this line is discounted by (its % or its
    price)? A new line counts as changed."""
    if not stored:
        return True
    try:
        return (
            abs(float(line.get("discount_percent") or 0) - float(stored.get("discount_percent") or 0)) > 1e-9
            or abs(float(line.get("unit_price") or 0) - float(stored.get("unit_price") or 0)) > 1e-9
        )
    except (TypeError, ValueError):
        return True


def _cart_approver_fields(body_pct, order: dict, editor: dict) -> Dict[str, Any]:
    """An edit that changes the bill discount is approved by the editing admin."""
    if body_pct is None:
        return {}
    if abs(float(body_pct) - float(order.get("cart_discount_percent") or 0.0)) <= 1e-9:
        return {}
    from ...services.discount_approval import who

    a = who(editor) if float(body_pct) > 0 else {"user_id": None, "name": None}
    return {"cart_discount_approved_by": a["user_id"], "cart_discount_approved_by_name": a["name"]}


@router.put("/{order_id}/superadmin-edit")
async def superadmin_edit_order(
    order_id: str,
    body: SuperadminOrderEdit,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only PRE-INVOICE order edit (build item #16, part 1).

    Allowed when the order is CONFIRMED / PROCESSING / READY and NO tax invoice
    has been issued yet. Edits the item set (qty / unit_price / discount /
    add / remove), the order-level cart discount, and/or the customer, then
    RECOMPUTES per-category GST + grand_total via the canonical
    ``_compute_per_category_gst`` (so the edit bills EXACTLY like create/add/
    remove). A non-empty ``reason`` is mandatory and a synchronous immutable
    audit row (before/after/diff) is written BEFORE returning. Guards:
      * RBAC -> 403 non-SUPERADMIN;
      * period lock -> 423 (cannot edit into a closed accounting month);
      * an already-invoiced order -> 409 (use /superadmin-invoice-change);
      * DRAFT -> 400 (use the ordinary PUT /{order_id} edit);
      * terminal DELIVERED / CANCELLED -> 400;
      * a line the edit adds or replaces meets the till's master / type /
        archive / stock rules and takes its unit or lens cell (400 / 409); a
        line it drops gives them back; a correction of an unchanged line keeps
        them.
    """
    _require_superadmin(current_user)
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")

    from ...services.order_superadmin_edit import (
        PRE_INVOICE_EDITABLE_STATUSES,
        recompute_totals,
        order_money_snapshot,
    )

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    validate_store_access(order.get("store_id"), current_user)

    status = str(order.get("status") or "").upper()
    if status == "DRAFT":
        raise HTTPException(
            status_code=400,
            detail="A DRAFT order is edited via PUT /orders/{id}, not this endpoint.",
        )
    if order.get("invoice_number"):
        raise HTTPException(
            status_code=409,
            detail=(
                "A tax invoice has been issued for this order. Use "
                "/orders/{id}/superadmin-invoice-change (revised invoice or "
                "credit/debit note) -- an issued invoice must never be mutated."
            ),
        )
    if status not in PRE_INVOICE_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order status {status or 'UNKNOWN'} is not editable. Only "
                f"{', '.join(PRE_INVOICE_EDITABLE_STATUSES)} orders without an "
                f"invoice can be edited."
            ),
        )

    # Period lock: cannot edit money into a closed accounting month (423).
    db = _get_db()
    if db is not None:
        from ..finance import check_period_locked
        from ...utils.ist import ist_today

        check_period_locked(db, ist_today())

    before = order_money_snapshot(order)

    # Resolve corrected lines + cart discount + customer.
    new_items, added, removed = _rebuilt_items_or_existing(
        body.items, order.get("items"), current_user
    )
    _gate_added_lines(added, order.get("store_id"))
    cart_discount_pct = (
        body.cart_discount_percent
        if body.cart_discount_percent is not None
        else float(order.get("cart_discount_percent") or 0.0)
    )
    # An order billed before the round-off rule keeps its exact paise total.
    gst = recompute_totals(
        new_items, cart_discount_pct, _compute_per_category_gst,
        rounded=bills_round_off(order),
    )

    update_data: Dict[str, Any] = {
        "items": new_items,
        "subtotal": gst["subtotal"],
        "cart_discount_percent": max(0.0, min(100.0, cart_discount_pct or 0.0)),
        "cart_discount_amount": gst["cart_discount_amount"],
        "tax_rate": gst["dominant_rate"],
        "tax_amount": gst["tax"],
        "total_discount": gst["total_discount"],
        "grand_total": gst["grand_total"],
        "round_off": gst["round_off"],
        "pricing_model": gst.get("pricing_model", "inclusive"),
        "updated_by": current_user.get("user_id"),
        "superadmin_edited": True,
        "superadmin_edit_reason": body.reason,
        "superadmin_edited_at": utc_now_iso(),
    }
    if body.cart_discount_reason is not None:
        update_data["cart_discount_reason"] = body.cart_discount_reason
    update_data.update(_cart_approver_fields(body.cart_discount_percent, order, current_user))
    if body.notes is not None:
        update_data["notes"] = body.notes
    if body.customer_id is not None:
        update_data["customer_id"] = body.customer_id
    if body.customer_name is not None:
        update_data["customer_name"] = body.customer_name

    # Recompute the receivable so a changed grand_total flows to balance_due /
    # payment_status (a SUPERADMIN edit can raise or lower what is owed).
    amount_paid = float(order.get("amount_paid") or 0.0)
    balance_due = round(gst["grand_total"] - amount_paid, 2)
    update_data["balance_due"] = max(0.0, balance_due)
    if balance_due <= 0.01:
        update_data["payment_status"] = "PAID"
    elif amount_paid > 0:
        update_data["payment_status"] = "PARTIAL"
    else:
        update_data["payment_status"] = "UNPAID"
    update_data["bill_type"] = derive_bill_type(update_data["payment_status"])

    after_order = dict(order)
    after_order.update(update_data)
    after = order_money_snapshot(after_order)

    # What the edit newly bills is taken before it is saved, as at the till: a
    # 409 here leaves the order untouched.
    claimed, cells, soft_failed = await _take_added_stock(order, added, current_user)
    if soft_failed:
        update_data["lens_reserve_failed"] = True
    update_data.update(_settle_owed(order, removed))
    async with _stock_back_unless_saved(
        order, claimed, cells, current_user, repo,
        "superadmin_edited_at", update_data["superadmin_edited_at"],
        partial(_settle_saved_edit, order, update_data, added, removed, repo, current_user),
    ):
        # SYNCHRONOUS immutable audit BEFORE persisting/returning.
        _write_order_edit_audit(
            action="ORDER_SUPERADMIN_EDIT",
            order=order,
            before=before,
            after=after,
            reason=body.reason,
            user_id=current_user.get("user_id"),
        )

        # Compare-and-set on what this edit read: still no tax invoice, no
        # refund-advance claim, the same status, the same lines. A counter
        # print that minted the invoice in between wins -- the issued number
        # never carries lines it did not print (that change goes on a credit /
        # debit note). A second edit of the same lines loses here too, so a
        # double press takes no second unit.
        if not repo.update_if(
            order_id,
            {"invoice_number": {"$in": [None, ""]},
             "advance_refund_id": order.get("advance_refund_id"),
             "status": order.get("status"),
             "items": order.get("items")},
            update_data,
        ):
            _write_order_edit_audit(
                action="ORDER_SUPERADMIN_EDIT_REFUSED",
                order=order,
                before=before,
                after=before,
                reason="edit refused: the order changed (invoiced, refunded or "
                "re-statused) while it was being edited",
                user_id=current_user.get("user_id"),
            )
            raise HTTPException(
                status_code=409,
                detail="This order changed while it was being edited (it may have been "
                "invoiced) - reload it and check.",
            )

    return {
        "order_id": order_id,
        "message": "Order edited",
        "grand_total": gst["grand_total"],
        "tax_amount": gst["tax"],
        "balance_due": update_data["balance_due"],
        "before": before,
        "after": after,
        "audit_note": ("An immutable audit entry recording this change was written."),
    }


@router.put("/{order_id}/superadmin-invoice-change")
async def superadmin_invoice_change(
    order_id: str,
    body: SuperadminInvoiceChange,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only POST-INVOICE correction (build item #16, part 2).

    For an order that ALREADY carries a GST tax invoice. An issued tax invoice
    is immutable (Rule 46) -- it is NEVER silently mutated. The SUPERADMIN
    chooses ``mode`` (owner decision = support BOTH):

      * REVISED_INVOICE -- allocate a NEW invoice serial for the corrected
        order, mark the ORIGINAL superseded/void with a pointer to the new
        serial, persist the corrected lines/totals, and link original<->revised
        (``revised_invoices``).
      * CREDIT_NOTE -- compute the money delta (grand_total down -> CREDIT note,
        up -> DEBIT note), issue a note for the DELTA linked to the original
        invoice (the customer-facing credit-note ledger so POS-redeem / the
        customer card see it), and leave the ORIGINAL invoice + order totals
        intact.

    Both paths: RBAC 403 non-SUPERADMIN, mandatory reason, period-lock 423, and
    a synchronous immutable before/after/diff audit row.
    """
    _require_superadmin(current_user)
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")

    from ...services.order_superadmin_edit import (
        recompute_totals,
        order_money_snapshot,
        compute_invoice_delta,
        build_credit_note_doc,
        note_by_rate,
        build_revised_invoice_doc,
    )

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    validate_store_access(order.get("store_id"), current_user)

    original_invoice = order.get("invoice_number")
    if not original_invoice:
        raise HTTPException(
            status_code=409,
            detail=(
                "No tax invoice has been issued for this order yet. Use "
                "/orders/{id}/superadmin-edit for a pre-invoice edit."
            ),
        )

    # Period lock: a post-issue correction is a financial posting (423).
    db = _get_db()
    if db is not None:
        from ..finance import check_period_locked
        from ...utils.ist import ist_today

        check_period_locked(db, ist_today())

    before = order_money_snapshot(order)
    new_items, added, removed = _rebuilt_items_or_existing(
        body.items, order.get("items"), current_user
    )
    _gate_added_lines(added, order.get("store_id"))
    if body.mode != "REVISED_INVOICE" and any(
        _takes_serialized_stock(ln) or takes_lens_cell(ln) for ln in added
    ):
        # A note changes no order line, so no unit or cell could ever answer to it.
        raise HTTPException(
            status_code=409,
            detail=(
                "A credit / debit note moves no stock, so it cannot bill new "
                "goods. Use a revised invoice, or a new bill for them."
            ),
        )
    cart_discount_pct = (
        body.cart_discount_percent
        if body.cart_discount_percent is not None
        else float(order.get("cart_discount_percent") or 0.0)
    )
    # An order billed before the round-off rule keeps its exact paise total.
    gst = recompute_totals(
        new_items, cart_discount_pct, _compute_per_category_gst,
        rounded=bills_round_off(order),
    )

    # The "after" money picture the correction targets.
    corrected = dict(order)
    corrected.update(
        {
            "items": new_items,
            "subtotal": gst["subtotal"],
            "cart_discount_percent": max(0.0, min(100.0, cart_discount_pct or 0.0)),
            "cart_discount_amount": gst["cart_discount_amount"],
            "tax_rate": gst["dominant_rate"],
            "tax_amount": gst["tax"],
            "total_discount": gst["total_discount"],
            "grand_total": gst["grand_total"],
            "round_off": gst["round_off"],
            **_cart_approver_fields(body.cart_discount_percent, order, current_user),
        }
    )
    if body.customer_id is not None:
        corrected["customer_id"] = body.customer_id
    if body.customer_name is not None:
        corrected["customer_name"] = body.customer_name
    after = order_money_snapshot(corrected)

    store_doc = None
    try:
        if db is not None:
            store_doc = db.get_collection("stores").find_one(
                {"store_id": order.get("store_id")}
            )
    except Exception:  # noqa: BLE001
        store_doc = None

    if body.mode == "REVISED_INVOICE":
        # Allocate a fresh GST serial for the revised invoice.
        # A historical import or a CANCELLED order never takes a number.
        problem = order_mint_problem(order)
        if problem:
            raise HTTPException(
                status_code=400, detail=f"Cannot issue a revised invoice: {problem}"
            )
        # GST batch 5: a revised invoice replaces the original in GSTR-1, so
        # it is issued only inside the original's own month (cancel and
        # re-issue before the return is filed). After that month the original
        # stands as filed; the change goes on a credit / debit note.
        from ...utils.event_time import parse_event_utc_naive
        from ...utils.ist import ist_date_str, ist_today

        issued = parse_event_utc_naive(order.get("invoice_date") or order.get("created_at"))
        if issued is None or ist_date_str(issued)[:7] != ist_today().isoformat()[:7]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Invoice {original_invoice} was issued in an earlier month and is "
                    "already in that month's GSTR-1. Use the credit/debit note mode "
                    "for this change."
                ),
            )
        customer_doc = None
        try:
            if db is not None:
                customer_doc = db.get_collection("customers").find_one(
                    {"customer_id": body.customer_id or order.get("customer_id")}
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail="Cannot issue a revised invoice: the customer could not be read"
            ) from exc
        from .invoices import invoice_mint_stamp

        claimed: dict = {}
        cells: List[dict] = []
        soft_failed = False
        try:
            mint_stamp = invoice_mint_stamp(order, customer_doc, store_doc)
            # Before the number is minted, so a lost unit burns no serial.
            claimed, cells, soft_failed = await _take_added_stock(order, added, current_user)
            new_invoice_number = repo.next_invoice_number(
                order.get("store_id"), store_doc=store_doc
            )
        except InvoiceNumberError as exc:
            await _give_stock_back(order, claimed, cells, current_user)
            raise HTTPException(
                status_code=400, detail=f"Cannot issue a revised invoice: {exc}"
            ) from exc
        except Exception:
            await _give_stock_back(order, claimed, cells, current_user)
            raise
        revised_doc = build_revised_invoice_doc(
            order=order,
            new_invoice_number=new_invoice_number,
            before=before,
            after=after,
            reason=body.reason,
            user_id=current_user.get("user_id"),
        )
        # Persist corrected order under the NEW serial; the OLD serial is kept
        # on the doc for traceability + marked superseded.
        update_data: Dict[str, Any] = {
            "items": new_items,
            "subtotal": gst["subtotal"],
            "cart_discount_percent": corrected["cart_discount_percent"],
            "cart_discount_amount": gst["cart_discount_amount"],
            "tax_rate": gst["dominant_rate"],
            "tax_amount": gst["tax"],
            "total_discount": gst["total_discount"],
            "grand_total": gst["grand_total"],
            "round_off": gst["round_off"],
            "pricing_model": gst.get("pricing_model", "inclusive"),
            "invoice_number": new_invoice_number,
            "invoice_date": datetime.now(),
            **mint_stamp,
            "superseded_invoice_number": original_invoice,
            "invoice_revision_id": revised_doc["revision_id"],
            "superadmin_edited": True,
            "superadmin_edit_reason": body.reason,
            "superadmin_edited_at": utc_now_iso(),
            "updated_by": current_user.get("user_id"),
        }
        if body.customer_id is not None:
            update_data["customer_id"] = body.customer_id
        if body.customer_name is not None:
            update_data["customer_name"] = body.customer_name
        update_data.update(
            _cart_approver_fields(body.cart_discount_percent, order, current_user)
        )
        # Recompute the receivable against the revised grand_total.
        amount_paid = float(order.get("amount_paid") or 0.0)
        balance_due = round(gst["grand_total"] - amount_paid, 2)
        update_data["balance_due"] = max(0.0, balance_due)
        update_data["payment_status"] = (
            "PAID"
            if balance_due <= 0.01
            else ("PARTIAL" if amount_paid > 0 else "UNPAID")
        )
        update_data["bill_type"] = derive_bill_type(update_data["payment_status"])
        if soft_failed:
            update_data["lens_reserve_failed"] = True
        update_data.update(_settle_owed(order, removed))

        # The settle's earn re-gate is the pre-invoice door's: the CREDIT_NOTE
        # mode leaves the order's lines intact, so only REVISED needs it.
        async with _stock_back_unless_saved(
            order, claimed, cells, current_user, repo, "invoice_number", new_invoice_number,
            partial(_settle_saved_edit, order, update_data, added, removed, repo, current_user),
        ):
            # SYNCHRONOUS immutable audit BEFORE persisting.
            _write_order_edit_audit(
                action="ORDER_INVOICE_REVISED",
                order=order,
                before=before,
                after=after,
                reason=body.reason,
                user_id=current_user.get("user_id"),
                extra={
                    "original_invoice_number": original_invoice,
                    "revised_invoice_number": new_invoice_number,
                    "revision_id": revised_doc["revision_id"],
                },
            )

            # Persist the revised-invoice link record (best-effort, fail-soft).
            if db is not None:
                try:
                    db.get_collection("revised_invoices").insert_one(dict(revised_doc))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[ORDERS] revised_invoices insert failed: %s", exc)

            # Compare-and-set on the number this request superseded: of two
            # presses only one replaces it -- never two revised numbers printed.
            # ...and still uncancelled with no refund-advance claim (set_invoice's
            # guard): a revised serial never lands on an order a credit note
            # reversed in between.
            if not repo.update_if(
                order_id,
                {"invoice_number": original_invoice, "advance_refund_id": None,
                 "status": order.get("status")},
                update_data,
            ):
                # The serial this press minted is never printed: record the gap
                # (Rule 46(b) -- every serial accounted for) next to the audit row
                # above that announced it, and void its link record.
                _write_order_edit_audit(
                    action="ORDER_INVOICE_REVISION_REFUSED",
                    order=order,
                    before=before,
                    after=before,
                    reason="revision refused, serial unused: another request "
                    "revised this invoice first",
                    user_id=current_user.get("user_id"),
                    extra={
                        "original_invoice_number": original_invoice,
                        "unused_invoice_number": new_invoice_number,
                        "revision_id": revised_doc["revision_id"],
                    },
                )
                if db is not None:
                    try:
                        db.get_collection("revised_invoices").update_one(
                            {"revision_id": revised_doc["revision_id"]},
                            {"$set": {"status": "REFUSED_SERIAL_UNUSED"}},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[ORDERS] revised_invoices void failed: %s", exc)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This invoice was already revised by another request -- "
                        "reload the order"
                    ),
                )

        return {
            "order_id": order_id,
            "mode": "REVISED_INVOICE",
            "message": "Revised invoice issued; original invoice superseded.",
            "original_invoice_number": original_invoice,
            "revised_invoice_number": new_invoice_number,
            "revision_id": revised_doc["revision_id"],
            "grand_total": gst["grand_total"],
            "balance_due": update_data["balance_due"],
            "audit_note": "An immutable audit entry recording this change was written.",
        }

    # mode == CREDIT_NOTE  -> issue a credit (reduction) / debit (increase) note
    # for the DELTA, linked to the ORIGINAL invoice. The original invoice +
    # order totals are LEFT INTACT.
    # A cancelled order, or one whose advance the refund door holds, takes no
    # delta note: the door's whole-invoice note already reverses it. Claim the
    # order (compare-and-set on the door's claim) BEFORE the note: the door's
    # own claim requires this marker unset, so of the two exactly one wins.
    if order.get("advance_refund_id"):
        from ..returns import release_stale_advance_claim as _free

        order = _free(order)
    if str(order.get("status") or "").upper() in ("CANCELLED", "CANCELED") or order.get("advance_refund_id"):
        raise HTTPException(
            status_code=409,
            detail="This order is cancelled or its advance was refunded, so no credit note can be issued on it.",
        )
    delta = compute_invoice_delta(before, after)
    if delta["direction"] == "NONE":
        raise HTTPException(
            status_code=400,
            detail=(
                "The corrected order has the same grand total as the original; "
                "there is no delta to issue a credit/debit note for."
            ),
        )

    from ..returns import (
        CreditNotConfirmed,
        _credit_note_window_closed,
        _issue_store_credit,
        _record_unused_credit_note,
    )

    is_cn = delta["note_type"] == "CREDIT_NOTE"
    # One credit line per order and correction: a re-press (or a replay) of
    # the SAME delta resumes that line, never a second note and credit.
    sacn_id = (f"{order_id}:sacn:{int(round(abs(float(delta.get('abs_delta') or 0)) * 100))}"
               f":{int(round(float(after.get('grand_total') or 0) * 100))}")
    if is_cn:
        closed = _credit_note_window_closed(order)
        if closed:
            raise HTTPException(status_code=400, detail=closed)
        from ..returns import _ledger_coll as _rlc

        _ledger = _rlc()
        try:
            in_flight = _ledger is not None and _ledger.find_one(
                {"order_id": order_id, "type": "ISSUING"}) is not None
            # Every attempt at this correction: the fixed id and its retries
            # (<id>:r<n>), read over the WHOLE prefix.
            attempts = [r for r in (_ledger.find({"order_id": order_id}) if _ledger is not None else [])
                        if str(r.get("entry_id") or "") == sacn_id
                        or str(r.get("entry_id") or "").startswith(f"{sacn_id}:r")]
            done = any(r.get("type") in ("ISSUED", "ADJUSTED") for r in attempts)
            voided = sum(1 for r in attempts if r.get("type") == "VOID")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="Could not check this order's credit notes - try again.") from exc
        # A VOIDed attempt (its credit never landed, its number written off)
        # never blocks the correction: the next press posts a new attempt.
        if voided:
            sacn_id = f"{sacn_id}:r{voided}"
        if in_flight:
            raise HTTPException(status_code=409, detail=(
                "A credit on this order is still being confirmed - it settles by itself within 10 "
                "minutes. Do NOT issue another note; check the customer's balance then."))
        if done:
            raise HTTPException(status_code=409, detail=(
                "This exact correction was already issued as a credit note on this order - nothing "
                "was issued again."))
    marker = utc_now_iso()
    if is_cn and not repo.update_if(
        order_id, {"advance_refund_id": None, "status": order.get("status")},
        {"post_invoice_credit_note_at": marker},
    ):
        raise HTTPException(status_code=409, detail="This order changed - reload it and check.")

    def _release_marker():
        # Every failure before the credit lands gives the order back
        # (compare-and-set on THIS press's own stamp).
        if is_cn:
            repo.update_if(order_id, {"post_invoice_credit_note_at": marker},
                           {"post_invoice_credit_note_at": None})

    # Rule 53 (owner 2026-10-18): the note's number from the sale shop's
    # credit-note (C) or debit-note (D) series -- refused, never guessed,
    # before anything is written.
    try:
        note_number = next_document_number(
            db,
            order.get("store_id"),
            "D" if delta["note_type"] == "DEBIT_NOTE" else "C",
            store_doc=store_doc,
        )
    except InvoiceNumberError as exc:
        _release_marker()
        raise HTTPException(
            status_code=400, detail=f"Cannot issue the note: {exc}"
        ) from exc
    except Exception:
        _release_marker()
        raise
    note_doc = build_credit_note_doc(
        order=order,
        delta=delta,
        reason=body.reason,
        user_id=current_user.get("user_id"),
        note_number=note_number,
    )
    # GSTR-1 files the note per rate (a 5% + 18% correction never at its
    # blended 12% / 14%): the change per rate, as a positive amount the note
    # carries -- after less before for a debit note, before less after for a
    # credit note (whose ledger row carries it: _issue_store_credit by_rate).
    note_doc["by_rate"] = {
        k: {"taxable": round(-v["taxable"], 2), "tax": round(-v["tax"], 2)} if is_cn else v
        for k, v in note_by_rate(order, corrected).items()
    }

    # SYNCHRONOUS immutable audit BEFORE issuing the note.
    _write_order_edit_audit(
        action="ORDER_INVOICE_CREDIT_NOTE",
        order=order,
        before=before,
        after=after,
        reason=body.reason,
        user_id=current_user.get("user_id"),
        extra={
            "note_number": note_doc["note_number"],
            "note_type": note_doc["note_type"],
            "amount": note_doc["amount"],
            "original_invoice_number": original_invoice,
        },
    )

    # A CREDIT note IS its store-credit ledger row (the one GSTR-1 files),
    # posted through THE posting door FIRST: the note is only reported issued
    # once the credit landed. A refusal gives the order back and writes the
    # number off (unless a ledger row owns it: then that row's own lifecycle
    # does). A DEBIT note (customer owes more) is recorded for the accountant
    # but is NOT store credit.
    _outcome: dict = {}
    if is_cn:
        try:
            credit = _issue_store_credit(
                order.get("customer_id"),
                note_doc["amount"],
                reason=f"Credit note {note_doc['note_number']} "
                f"(order edit on invoice {original_invoice})",
                ref=note_doc["note_number"],
                current_user=current_user,
                # GSTR-1 CDNR tax reversal (PR #945 P3): the note's ALREADY-
                # COMPUTED taxable / tax split -- never recomputed here.
                taxable=note_doc["taxable_amount"],
                tax=note_doc["tax_amount"],
                by_rate=note_doc["by_rate"],
                # GSTR-1 CDNR head consistency: reverse under the SAME head the
                # parent invoice filed under (bool-gated; POS parents carry no
                # flag and keep the legacy state-compare fallback).
                interstate=(
                    order.get("interstate")
                    if isinstance(order.get("interstate"), bool)
                    else None
                ),
                # The SALE's store (its GSTIN files the note), never the
                # SUPERADMIN's store picker -- the C-number is minted there too.
                store_id=order.get("store_id"),
                # No returns doc behind this note: the row names its parent.
                order_id=order.get("order_id"),
                note_number=note_doc["note_number"],
                entry_id=sacn_id,
                outcome=_outcome,
            )
        except CreditNotConfirmed:
            # It may already be on the balance: the order stays claimed (its
            # marker heals once the line settles), the number stays with the
            # line, and the reconciler settles it. Never 'nothing given'.
            raise HTTPException(
                status_code=503,
                detail=("The credit note could not be confirmed just now - the credit may already be on "
                        "the customer's balance. Do NOT issue it again: it settles by itself within 10 "
                        "minutes. Check the customer's store-credit balance then."),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[ORDERS] credit note %s not posted: %s", note_doc["note_number"], exc)
            # No line written: a refusal. A line (or no answer): its owner is
            # the reconciler -- never 'nothing given'.
            try:
                _line = _ledger.find_one({"entry_id": sacn_id}) if _ledger is not None else None
            except Exception:  # noqa: BLE001
                _line = True
            if _line is not None:
                raise HTTPException(status_code=503, detail=(
                    "The credit note could not be confirmed just now - the credit may already be on "
                    "the customer's balance. Do NOT issue it again: it settles by itself within 10 "
                    "minutes. Check the customer's store-credit balance then.")) from exc
            credit, _outcome["not_written"] = None, True
        if credit is None:
            _release_marker()
            # Written off only when no line carrying it was written (a VOID
            # that stood wrote its own number off).
            if _outcome.get("not_written"):
                _record_unused_credit_note(note_doc["note_number"], order, note_doc["note_number"],
                                           current_user, "superadmin credit note not posted")
            raise HTTPException(
                status_code=503,
                detail=(
                    "The credit note could not be issued - the customer's store credit could not "
                    "take it, so nothing was given. Check the customer record, then try again."
                ),
            )
    if is_cn and credit.get("resumed"):
        # Another press issued this correction first: its line, as stored, is
        # the note (this press's number was written off). No second note doc.
        # This press's own number was written off: never reported as issued.
        number = credit.get("note_number")
        return {
            "order_id": order_id,
            "mode": "CREDIT_NOTE",
            "message": (f"CREDIT_NOTE {number} was already issued for this correction; nothing was issued again."
                        if number else
                        "This correction was already issued by another press; its note number could not be "
                        "read just now - check the customer's credit notes. Nothing was issued again."),
            "note_number": number,
            "note_type": "CREDIT_NOTE",
            "amount": credit.get("net_refund") or credit.get("amount"),
            "original_invoice_number": original_invoice,
            "delta": delta,
            "audit_note": "An immutable audit entry recording this change was written.",
        }
    if db is not None:
        try:
            coll_name = "credit_note_ledger" if is_cn else "debit_note_ledger"
            db.get_collection(coll_name).insert_one(dict(note_doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] note document insert failed: %s", exc)

    return {
        "order_id": order_id,
        "mode": "CREDIT_NOTE",
        "message": (
            f"{note_doc['note_type']} {note_doc['note_number']} issued for the "
            f"delta; original invoice {original_invoice} left intact."
        ),
        "note_number": note_doc["note_number"],
        "note_type": note_doc["note_type"],
        "amount": note_doc["amount"],
        "original_invoice_number": original_invoice,
        "delta": delta,
        "audit_note": "An immutable audit entry recording this change was written.",
    }
