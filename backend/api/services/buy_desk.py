"""
IMS 2.0 - Hub "Buy Desk" rows assembler (the owner's one-screen headline).

Read-only. Per catalogued product it answers the four questions the Buy Desk
table asks: is the catalog DONE, what's its online-store state, how much is on
hand + already on order, and how many should I buy. The buy signal is netted
against open POs so the operator never double-orders.

Pure assembly (build_row / buy_signal) + thin lookups; reuses the canonical
engines for the catalog/ecom truth (product_master.catalog_readiness,
online_catalog.stamp_online_state -- the Catalog screen's verdict, push gate
included) and self-contained aggregations for stock / open-PO
/ sales-velocity so a missing sub-signal degrades that ONE field to a safe default
rather than failing the row. No emoji (Windows cp1252). No writes.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from .reorder_policy import auto_reorder_disabled

logger = logging.getLogger("ims.buy_desk")

# ecom_state values (frozen interface -- the FE column keys on these).
ECOM_NOT_LISTED = "NOT_LISTED"
ECOM_STAGED = "STAGED"
ECOM_LIVE = "LIVE"
ECOM_PUSH_LOCKED = "PUSH_LOCKED"

# Default reorder horizon (days of cover the buy signal targets) when the store
# has no configured lead time. Conservative two weeks.
DEFAULT_LEAD_DAYS = 14


def buy_signal(
    velocity_per_day: Optional[float],
    on_hand: int,
    on_order: int,
    lead_days: int = DEFAULT_LEAD_DAYS,
) -> Optional[int]:
    """Suggested order qty = ceil(velocity * lead_days) - on_hand - on_order,
    floored at 0. Netting against on_order is the whole point -- never double-order
    what a PO already covers. Returns None when there is no velocity signal yet
    (no sales history) so the FE shows "-" instead of a misleading 0."""
    if velocity_per_day is None or velocity_per_day <= 0:
        return None
    need = math.ceil(velocity_per_day * max(1, int(lead_days)))
    suggested = need - max(0, int(on_hand)) - max(0, int(on_order))
    return suggested if suggested > 0 else 0


def ecom_state(online: Optional[str]) -> str:
    """The Buy Desk chip from the ONE online verdict
    (online_catalog.product_online_state, stamped on the row as `online` by
    stamp_online_state from the twin the push reads, push gate included):
    a listing on Shopify -> LIVE, even when the gate now refuses it (the row's
    note says its price and images no longer sync; it is still selling);
    refused and not live -> PUSH_LOCKED ("Not for website"); queued for a push
    -> STAGED; otherwise NOT_LISTED."""
    if online in ("LIVE", "DELIST_FAILED"):
        return ECOM_LIVE
    if online == "NOT_FOR_WEBSITE":
        return ECOM_PUSH_LOCKED
    if online == "QUEUED":
        return ECOM_STAGED
    return ECOM_NOT_LISTED


def build_row(
    product: Dict[str, Any],
    *,
    readiness: Dict[str, Any],
    on_hand: int,
    on_order: int,
    velocity_per_day: Optional[float],
    lead_days: int = DEFAULT_LEAD_DAYS,
) -> Dict[str, Any]:
    """Assemble one Buy Desk row (pure). `readiness` is the
    product_master.catalog_readiness() dict for this product; `online` /
    `online_note` are stamped on it by online_catalog.stamp_online_state."""
    attrs = product.get("attributes") or {}
    return {
        "product_id": product.get("product_id"),
        "sku": product.get("sku"),
        "name": product.get("name") or attrs.get("name"),
        "brand": product.get("brand") or attrs.get("brand_name"),
        "category": product.get("category"),
        "catalog_status": product.get("catalog_status"),
        "readiness": {
            "complete": bool(readiness.get("complete")),
            "missing": readiness.get("missing") or [],
            "blockers": readiness.get("blockers") or [],
            "purchasable": bool(readiness.get("purchasable")),
        },
        "ecom_state": ecom_state(product.get("online")),
        "ecom_note": product.get("online_note") or None,
        "on_hand": int(on_hand or 0),
        "on_order": int(on_order or 0),
        # Owner decision (2026-07-04): reorder_quantity <= 0 (the new -1
        # default) disables auto-reorder -- the Buy Desk shows "-" (None)
        # instead of a suggested qty. See api/services/reorder_policy.py.
        "buy_signal": (
            None
            if auto_reorder_disabled(product)
            else buy_signal(velocity_per_day, on_hand, on_order, lead_days)
        ),
        "purchasable": bool(readiness.get("purchasable")),
        # Additive (procurement Phase 1): the product's preferred vendor, when
        # set — the draft-PO modal preselects it (fail-soft to manual pick).
        # Same field the demand-forecast PO generator groups by (vendors.py).
        "preferred_vendor_id": product.get("preferred_vendor_id") or None,
        # The product's own GST identity, so the Buy Desk's quick-draft PO can
        # preview the rate that will ACTUALLY be charged instead of opening
        # every line at a flat 18% (frames, spectacle lenses and contact lenses
        # are all 5%). The server still resolves the stored rate from the HSN.
        "hsn_code": product.get("hsn_code") or None,
        "gst_rate": product.get("gst_rate"),
    }
