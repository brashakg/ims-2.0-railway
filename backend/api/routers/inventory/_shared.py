"""Inventory package - shared imports, router, logger and role/status constants."""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, List, Optional, Dict
from datetime import date, datetime, timedelta, timezone
import hashlib
import uuid
import logging

from ..auth import get_current_user, require_roles
from ...services import power_grid
from ...services import barcode as barcode_svc
from ...services.online_order_status import SALE_DONE_ANY_CASE
from ...services.reorder_policy import (
    auto_reorder_disabled as _reorder_disabled,
    discontinued as _discontinued,
)

# F9 / W1.4 / OS-006: the ONE backend detector for "is this store ONLINE?"
# (store_type == ONLINE, e.g. BV-ONLINE-01 / WO-ONLINE-01). Reused verbatim from
# the POS / PO / GRN / till guards -- do NOT add a second detector here.
from ...services.stores_util import is_online_store

# E3: the shared decision of "is this unit on hand?" -- see the block comment
# further down for exactly which readers use it, which do not, and why it may
# never be re-typed here as a status list. `is_on_hand` / `canonical_state` are
# the same rule for a unit already read into Python (the ledger groups BY
# status, so it cannot use the $match form).
from ...services.item_events import (
    on_hand_match as _on_hand_status_clause,
    canonical_state,
    is_on_hand,
    StockState,
)
from ...utils.ist import ist_date_str
from ...dependencies import (
    get_stock_repository,
    get_product_repository,
    get_audit_repository,
    validate_store_access,
    can_access_store_scoped,
    resolve_store_scope,
)

# The package name, so every record keeps the exact logger name the flat
# module emitted ("api.routers.inventory"); tests and caplog key on it.
logger = logging.getLogger(__package__)
router = APIRouter()

# Stock-manager roles permitted to drive the defective-unit quarantine lifecycle
# (mark / lift / print label). SUPERADMIN auto-passes via require_roles. This is
# DELIBERATELY narrower than _INVENTORY_ROLES -- a quarantine is a physical
# control decision (pull a defective unit off the sellable floor), reserved for
# the manager ladder, not catalog/workshop staff.
_STOCK_MANAGER_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)


# OWNER RULING 2026-08-25 (#8): taking stock off the books as a LOSS (a count
# write-off, a unit marked damaged or lost, a quantity adjusted down) is ADMIN
# / SUPERADMIN only, at every value. A store manager sends it for write-off
# instead. The ONE check every such door asks, so no door can drift.
WRITE_OFF_ROLES = ("ADMIN", "SUPERADMIN")


def is_write_off_admin(current_user: dict) -> bool:
    roles = {str(r).upper() for r in (current_user or {}).get("roles", []) or []}
    return bool(roles & set(WRITE_OFF_ROLES))


_DEAD_ORDER_STATUSES = {"CANCELLED", "CANCELED", "VOID", "VOIDED", "DRAFT"}


def _qty(value) -> int:
    """An order line's quantity as a whole number ('2.0', 2.0 and 2 alike);
    a missing one is a single piece."""
    try:
        return int(float(value)) if value not in (None, "") else 1
    except (TypeError, ValueError):
        return 1


def refuse_unless_real_order(db, order_id, unit: dict, current_user: dict) -> None:
    """Marking a unit SOLD by hand takes it off the shelf. Without a real
    order for it that is a write-off wearing a sale's clothes, so a non-admin
    must name a live order of THIS shop that carries THIS unit's product."""
    if is_write_off_admin(current_user):
        return
    order = None
    if order_id and db is not None:
        oid = str(order_id).strip()
        order = db.get_collection("orders").find_one(
            {"$or": [{"order_id": oid}, {"order_number": oid}, {"_id": oid}]}
        )
    pid = str(unit.get("product_id") or "")
    line_qty = sum(
        _qty(i.get("quantity"))
        for i in ((order or {}).get("items") or [])
        if str(i.get("product_id") or "") == pid
    )
    ok = bool(order) and line_qty > 0 and (
        str(order.get("status") or "").strip().upper() not in _DEAD_ORDER_STATUSES
        and order.get("store_id") == unit.get("store_id")
    )
    if ok:
        # ONE ORDER LINE, ONE USE PER PIECE (S1 review round 2): the same
        # order cannot be named for more pieces of this product than its
        # line sold. Pieces already sold against it: units stamped with the
        # order, plus ledger SELL events naming it -- under ANY of the
        # order's names (round 3: by number once and by id the next time
        # used to count as two different orders).
        #
        # ponytail: check-then-act -- two mark-solds landing in the same
        # instant can both read "one piece left" and both pass. Rare for a
        # hand action; a per-order claim counter closes it if it ever happens.
        names = list(
            {str(order_id).strip(), order.get("order_id"), order.get("order_number"),
             str(order.get("_id")) if order.get("_id") is not None else None}
            - {None, ""}
        )
        sold = {
            str(u.get("stock_id") or u.get("_id"))
            for u in db.get_collection("stock_units").find(
                {"product_id": pid, "order_id": {"$in": names}},
                {"stock_id": 1},
            )
        } | {
            str(e.get("stock_id"))
            for e in db.get_collection("item_events").find(
                {"event_type": "sell", "product_id": pid, "source_id": {"$in": names}},
                {"stock_id": 1},
            )
        }
        sold.discard(str(unit.get("stock_id") or unit.get("_id")))
        ok = len(sold) < line_qty
    if not ok:
        raise HTTPException(
            status_code=403,
            detail=(
                "Name the live order this piece was sold on (one that still has "
                "this product left to hand over). Without one, only an admin can "
                "take it off the books."
            ),
        )


def refuse_write_off_unless_admin(current_user: dict, what: str) -> None:
    """403 unless the caller may write stock off (WRITE_OFF_ROLES)."""
    if not is_write_off_admin(current_user):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Only an admin can {what}. Send it to the admin for write-off "
                "instead."
            ),
        )

# The free-string status value a quarantined unit carries. NOT an enum / schema
# change (CORRECTIONS P0-6): stock_units.status is a free string, and every
# on-hand / sellable rollup uses an explicit AVAILABLE/RESERVED allowlist, so a
# QUARANTINED unit is excluded from POS, transfers and blind-count simply by not
# being in any allowlist.
STOCK_STATUS_QUARANTINED = "QUARANTINED"

# Allowed quarantine reasons (free-text fallback OTHER + notes). Kept here so the
# endpoint validates the dropdown the frontend shows.
_QUARANTINE_REASONS = {
    "DEFECTIVE",
    "SCRATCHED",
    "CUSTOMER_RETURN_DAMAGED",
    "QC_FAILED_WORKSHOP",
    "RECEIVED_DAMAGED",
    "OTHER",
}

# Roles permitted to mutate stock (add / count / scan / transfer / serials).
# Mirrors the inventory page route guard — the broadest role set any inventory
# write is reachable from in the UI — so this is zero-regression while still
# blocking the non-inventory roles (SALES_STAFF, SALES_CASHIER, CASHIER,
# OPTOMETRIST, ACCOUNTANT) from stock mutations. SUPERADMIN auto-passes.
_INVENTORY_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CATALOG_MANAGER",
    "WORKSHOP_STAFF",
)


# "This order is a real sale": the ONE set in online_order_status (any case,
# so imported history counts; SHIPPED, so a fulfilled online sale does).
_SOLD_STATUSES = SALE_DONE_ANY_CASE
