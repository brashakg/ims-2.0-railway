"""Shared GST + payment-mapping helpers for ONLINE (Shopify) orders.

Leaf module -- imports NOTHING from the rest of the app -- so every layer can
import it without any circular-dependency risk: the services layer
(shopify_ingest, online_order_mapper) and the routers layer (finance, reports).

It exists to remove two duplications that had drifted apart:

  * ``order_interstate_flag`` -- the OS-008 "prefer the order's OWN persisted
    interstate flag" gate, previously copy-pasted verbatim into
    finance._order_is_interstate and reports._order_is_interstate. Only the GATE
    is shared; each caller keeps its own state-comparison FALLBACK because the
    two fallbacks are DELIBERATELY different (finance normalizes both states to
    their GST numeric code -- Jharkhand == JH == 20 -- while reports does a raw
    case-insensitive string compare) and each must stay byte-identical to its
    file's prior rule. Sharing the fallback too would silently change one of
    them.

  * ``PAYMENT_STATUS_MAP`` -- the single Shopify ``financial_status`` -> IMS
    ``payment_status`` vocabulary, previously defined only inside
    online_order_mapper. The shopify_ingest create path imported it with a
    two-entry emergency fallback, so a degraded import (mapper load failure)
    would mis-book a voided/refunded create as UNPAID while the sync path booked
    CANCELLED/REFUNDED. One source of truth here makes both paths agree.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Shopify ``financial_status`` -> canonical IMS ``payment_status``.
# (Shopify: pending | authorized | partially_paid | paid | partially_refunded |
#  refunded | voided.)  Anything unknown -> UNPAID (safe: shows as a receivable).
# NOTE: this is the LIVE vocabulary. The HISTORICAL import intentionally uses a
# DIFFERENT map (shopify_ingest._HIST_PAYMENT_STATUS_MAP: partially_paid -> PAID)
# because a settled back-catalogue order carries no open balance -- do not merge
# the two.
PAYMENT_STATUS_MAP: Dict[str, str] = {
    "paid": "PAID",
    "partially_paid": "PARTIAL",
    "authorized": "UNPAID",
    "pending": "UNPAID",
    "voided": "CANCELLED",
    "refunded": "REFUNDED",
    "partially_refunded": "PARTIAL_REFUND",
}


def order_interstate_flag(order: Dict[str, Any]) -> Optional[bool]:
    """Return the order doc's OWN persisted inter-state (IGST) flag when it is a
    DEFINITIVE bool, else ``None`` (the caller then applies its own store-vs-
    customer state fallback).

    This is the OS-008 flag-preference gate: online (Shopify) orders stamp
    ``interstate`` at ingest from the buyer's DELIVERY address via the shared
    ``_build_invoice_gst_split`` the POS invoice also uses, while their buyer
    customer records are minted stateless -- so recomputing the split from
    ``customers.state`` misfiled every inter-state online sale as CGST/SGST even
    though the minted invoice said IGST.

    The flag is trusted ONLY when ``isinstance(..., bool)``. A legacy / POS doc
    without the key (``order.get`` -> ``None``), or a stray non-bool value,
    yields ``None`` so the caller's state fallback still applies (and later
    self-heals once the flag is stamped). Crucially this also means an ABSENT
    flag is treated as "unknown, use the fallback" -- NOT as ``False`` -- so a
    failed GST split at ingest (which now omits the key entirely) never freezes
    an order into a wrong intra-state classification.
    """
    flag = order.get("interstate")
    return flag if isinstance(flag, bool) else None
