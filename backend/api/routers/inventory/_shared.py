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
from ...services.reorder_policy import auto_reorder_disabled as _reorder_disabled

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


# Broad "this order represents a real sale" status set (both cases seen in DB).
# Defined here — before its first use in get_non_moving_stock — so it is
# unambiguously initialised before ANY call-site (including the functions below
# that also reference it: get_sell_through_analysis, get_overstock_analysis,
# get_stock_alerts, _aggregate_sales_by_barcode).
_SOLD_STATUSES = [
    "DELIVERED",
    "delivered",
    "Delivered",
    "COMPLETED",
    "completed",
    "Completed",
    "PAID",
    "paid",
    "Paid",
    "FULFILLED",
    "fulfilled",
    "Fulfilled",
]
