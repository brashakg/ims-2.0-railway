"""
IMS 2.0 - Hub "Buy Desk" rows assembler (the owner's one-screen headline).

Read-only. Per catalogued product it answers the four questions the Buy Desk
table asks: is the catalog DONE, what's its online-store state, how much is on
hand + already on order, and how many should I buy. The buy signal is netted
against open POs so the operator never double-orders.

Pure assembly (build_row / buy_signal) + thin lookups; reuses the canonical
engines for the catalog/ecom truth (product_master.catalog_readiness,
shopify_push.push_lock_reason) and self-contained aggregations for stock / open-PO
/ sales-velocity so a missing sub-signal degrades that ONE field to a safe default
rather than failing the row. No emoji (Windows cp1252). No writes.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

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


def ecom_state(product: Dict[str, Any], push_locked: bool) -> str:
    """Honest online-store state. PUSH_LOCKED wins (a locked brand can never be
    pushed). Else derive from the product's ecom sub-doc: a live Shopify gid ->
    LIVE; a staged/listed intent -> STAGED; otherwise NOT_LISTED."""
    if push_locked:
        return ECOM_PUSH_LOCKED
    ecom = product.get("ecom") or {}
    if ecom.get("shopify_product_id"):
        return ECOM_LIVE
    if (
        ecom.get("listed")
        or ecom.get("staged")
        or str(ecom.get("status") or "").upper()
        in (
            "STAGED",
            "LISTED",
        )
    ):
        return ECOM_STAGED
    return ECOM_NOT_LISTED


def build_row(
    product: Dict[str, Any],
    *,
    readiness: Dict[str, Any],
    push_locked: bool,
    on_hand: int,
    on_order: int,
    velocity_per_day: Optional[float],
    lead_days: int = DEFAULT_LEAD_DAYS,
) -> Dict[str, Any]:
    """Assemble one Buy Desk row (pure). `readiness` is the
    product_master.catalog_readiness() dict for this product."""
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
        "ecom_state": ecom_state(product, push_locked),
        "on_hand": int(on_hand or 0),
        "on_order": int(on_order or 0),
        "buy_signal": buy_signal(velocity_per_day, on_hand, on_order, lead_days),
        "purchasable": bool(readiness.get("purchasable")),
    }
