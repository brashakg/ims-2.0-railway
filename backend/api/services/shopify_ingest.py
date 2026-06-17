"""
IMS 2.0 - Shopify online-order ingestion (IMS = GST invoice system-of-record)
=============================================================================
Council B10 (owner-approved). Shopify CANNOT issue an India-GST-compliant tax
invoice, so for every online order placed on the seller's OWN store
(bettervision.in, Shopify), IMS becomes the invoice document-of-record:

  * Shopify's order confirmation is treated as a RECEIPT.
  * IMS mints the GST TAX INVOICE -- consecutive serial per (store, FY),
    per-line HSN + taxable + tax, IGST vs CGST+SGST chosen by place of supply
    (buyer DELIVERY state vs the supplying store/entity GSTIN state) -- exactly
    the rule the offline POS already uses (orders.py _build_invoice_gst_split).

This module is a PURE, GATED, NETWORK-FREE pipeline: it operates on a Shopify
order payload dict (the webhook body that the signed `/webhooks/shopify`
receiver already verified + persisted). It makes NO live Shopify call. With no
DB / no integration configured it SIMULATES and never crashes.

NO TCS / NO GSTR-8: bettervision.in is the seller's own D2C store, not an
e-commerce marketplace facilitating other people's goods, so Section 52 TCS /
GSTR-8 does not apply. We deliberately build no TCS logic here.

Idempotency (a replayed webhook must NEVER create a second order or double-count
revenue) is enforced at TWO layers:

  1. Shopify ORDER id -- a guarded `find_one` on `orders.shopify_order_id`. If an
     IMS order already exists for this Shopify order, we return it unchanged.
  2. Shopify WEBHOOK id (X-Shopify-Webhook-Id) -- recorded in
     `shopify_webhook_dedupe` with a unique `_id`; a replay of the same delivery
     short-circuits before any work. Best-effort (TTL-expired), the order-id
     guard is the hard backstop.

The created IMS order doc is tagged `channel="ONLINE"` so finance / P&L can
count it exactly once and tell online from in-store revenue.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shopify product_type / title -> IMS GST category hint.
# Shopify line items don't carry an HSN, and the buyer's payload can't be
# trusted for tax nature, so we resolve the rate in priority order:
#   1. the matching IMS product master (by SKU) -> its real hsn_code + category
#      (authoritative -- that's the same SKU the catalog/POS bills)
#   2. a category hint mapped from Shopify product_type
#   3. the optical-dominant DEFAULT inside resolve_gst_rate (5%)
# The hint keys below are matched case-insensitively as substrings so
# "Sunglasses", "SUNGLASS", "Polarized Sunglasses" all map to SUNGLASS (18%).
# ---------------------------------------------------------------------------
_PRODUCT_TYPE_HINTS = [
    ("sunglass", "SUNGLASS"),
    ("frame", "FRAME"),
    ("contact", "CONTACT_LENS"),
    ("reading", "READING_GLASSES"),
    ("lens", "OPTICAL_LENS"),
    ("watch", "WATCH"),
    ("accessor", "ACCESSORIES"),
    ("case", "ACCESSORIES"),
    ("solution", "ACCESSORIES"),
]

_DEDUPE_COLLECTION = "shopify_webhook_dedupe"
# 30-day TTL on the webhook-id dedupe rows -- well past Shopify's ~48h retry
# window, so a legitimate late retry is still caught, but the collection
# can't grow unbounded.
_DEDUPE_TTL_SECONDS = 30 * 24 * 3600


def _category_hint_from_shopify(line: Dict[str, Any]) -> str:
    """Best-effort IMS GST category from a Shopify line item's product_type /
    title. Returns '' when nothing matches (resolve_gst_rate then applies the
    optical-dominant default)."""
    haystack = " ".join(
        str(line.get(k) or "")
        for k in ("product_type", "title", "name", "variant_title")
    ).lower()
    for needle, category in _PRODUCT_TYPE_HINTS:
        if needle in haystack:
            return category
    return ""


def _resolve_line_tax_basis(line: Dict[str, Any], product_repo) -> Dict[str, Any]:
    """Resolve (hsn_code, category, gst_rate) for one Shopify line item.

    Authoritative path: match the IMS product master by SKU -> use its stored
    hsn_code + category. Fallback: a category hint from Shopify product_type.
    resolve_gst_rate() layers the SUPERADMIN-editable HSN->GST master over the
    static GST 2.0 table and never raises.
    """
    from .gst_rates import resolve_gst_rate

    hsn: Optional[str] = None
    category = ""
    ims_product_id: Optional[str] = None

    sku = str(line.get("sku") or "").strip()
    if sku and product_repo is not None:
        try:
            prod = product_repo.find_by_sku(sku)
            if prod:
                hsn = prod.get("hsn_code") or prod.get("hsn") or None
                category = prod.get("category") or ""
                # Capture the IMS product_id (join via SKU) so the online-sale
                # stock decrement can FIFO-claim the right serialized units.
                ims_product_id = prod.get("product_id") or prod.get("id")
        except Exception:  # noqa: BLE001 - product lookup is best-effort
            pass

    if not hsn and not category:
        category = _category_hint_from_shopify(line)

    rate = resolve_gst_rate(hsn_code=hsn, category=category)
    return {
        "hsn_code": hsn,
        "category": category,
        "gst_rate": rate,
        "ims_product_id": ims_product_id,
    }


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _delivery_state(payload: Dict[str, Any]) -> str:
    """The buyer's place-of-supply state for an online order: the SHIPPING
    address province/code (delivery), falling back to billing. Returns the raw
    province string / code; orders._invoice_state_code normalizes it to a
    2-digit GST code."""
    for key in ("shipping_address", "billing_address"):
        addr = payload.get(key)
        if isinstance(addr, dict):
            cand = (
                addr.get("province_code") or addr.get("province") or addr.get("state")
            )
            if cand:
                return str(cand)
    return ""


def _map_line_items(payload: Dict[str, Any], product_repo) -> List[Dict[str, Any]]:
    """Map Shopify line_items -> IMS order item dicts with per-line HSN + GST
    rate + taxable + tax. Online prices are GST-INCLUSIVE (Shopify shows the
    all-in price the customer paid), so we extract tax from within the line
    gross: taxable = gross/(1+rate); tax = gross - taxable -- identical to the
    POS inclusive mode (orders._compute_per_category_gst)."""
    items: List[Dict[str, Any]] = []
    for line in payload.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        qty = _f(line.get("quantity"), 1.0) or 1.0
        unit_price = _f(line.get("price"))
        # Shopify line-level discount (total_discount applies to the whole line).
        line_discount = _f(line.get("total_discount"))
        line_gross = round(unit_price * qty - line_discount, 2)
        if line_gross < 0:
            line_gross = 0.0

        basis = _resolve_line_tax_basis(line, product_repo)
        rate = basis["gst_rate"]
        # Inclusive extraction (online price already includes GST).
        taxable = round(line_gross / (1.0 + rate / 100.0), 2)
        tax = round(line_gross - taxable, 2)

        items.append(
            {
                "item_id": str(uuid.uuid4()),
                "item_type": basis["category"] or "FRAME",
                "product_id": str(line.get("product_id") or "") or None,
                "product_name": line.get("title") or line.get("name") or "",
                "sku": line.get("sku") or "",
                "quantity": int(qty) if qty == int(qty) else qty,
                "unit_price": unit_price,
                "discount_percent": 0.0,
                "discount_amount": line_discount,
                "item_total": line_gross,
                "category": basis["category"],
                "hsn_code": basis["hsn_code"],
                "gst_rate": rate,
                "taxable_value": taxable,
                "tax_amount": tax,
                # IMS product_id (resolved via SKU) -- the join key the online
                # stock decrement claims serialized units by (the Shopify
                # product_id below is NOT the IMS one).
                "ims_product_id": basis.get("ims_product_id"),
                # Shopify identifiers kept for traceability / future reconcile.
                "shopify_product_id": str(line.get("product_id") or "") or None,
                "shopify_line_item_id": line.get("id"),
                "shopify_variant_id": line.get("variant_id"),
            }
        )
    return items


def _online_store_id(payload: Dict[str, Any]) -> str:
    """Store the online channel bills under. Configurable via the shopify
    integration config (`online_store_id`); defaults to a stable virtual store
    so the invoice serial counter + reports have a consistent bucket."""
    import os

    return (
        str(payload.get("_ims_online_store_id") or "").strip()
        or os.getenv("ONLINE_STORE_ID", "").strip()
        or "BV-ONLINE-01"
    )


def _online_fulfillment_store_id(payload: Dict[str, Any]) -> str:
    """The PHYSICAL store an online order draws stock from (the online billing
    store above is a virtual bucket with no serialized stock_units). The decrement
    + Shopify write-back claim units at THIS store. Configurable via
    ONLINE_FULFILLMENT_STORE_ID (env) / the shopify integration config; falls
    back to the online billing store (so a single-store setup works by pointing
    ONLINE_STORE_ID at the real fulfilling store). Empty -> no decrement (logged)."""
    import os

    return (
        str(payload.get("_ims_fulfillment_store_id") or "").strip()
        or os.getenv("ONLINE_FULFILLMENT_STORE_ID", "").strip()
        or _online_store_id(payload)
    )


def _record_stock_miss(db, order_id, store_id, reason, detail=None) -> None:
    """Fail-LOUD record of an online stock-decrement miss (an oversell: a paid
    online order whose physical units could not all be claimed). Logs at ERROR
    (so Sentry captures it) and writes an `online_stock_miss` doc that the
    sync-health tile surfaces for operator follow-up. Itself fully fail-soft --
    recording the miss must never raise out of ingestion (the invoice is already
    booked). reason: 'under_claim' | 'exception'."""
    logger.error(
        "[SHOPIFY_INGEST] ONLINE STOCK MISS order=%s store=%s reason=%s detail=%s",
        order_id,
        store_id,
        reason,
        detail,
    )
    try:
        if db is None:
            return
        coll = (
            db.get_collection("online_stock_miss")
            if hasattr(db, "get_collection")
            else db["online_stock_miss"]
        )
        if coll is None:
            return
        coll.insert_one(
            {
                "order_id": order_id,
                "store_id": store_id,
                "reason": reason,
                "detail": detail,
                "resolved": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOPIFY_INGEST] could not record stock miss for %s: %s", order_id, exc
        )


def _webhook_already_seen(db, webhook_id: Optional[str]) -> bool:
    """Layer-2 dedupe: record + detect a replayed X-Shopify-Webhook-Id.

    Returns True if this exact webhook delivery was already ingested. Uses an
    insert against a unique `_id`; a DuplicateKeyError means "seen before".
    Best-effort: any DB error returns False (the order-id guard still protects
    against a double-create), so a flaky dedupe collection never blocks a real
    order or crashes ingestion.
    """
    if not webhook_id or db is None:
        return False
    try:
        coll = db.get_collection(_DEDUPE_COLLECTION)
    except Exception:  # noqa: BLE001
        return False
    if coll is None:
        return False
    try:
        coll.create_index("created_at", expireAfterSeconds=_DEDUPE_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    try:
        coll.insert_one(
            {
                "_id": f"shopify:{webhook_id}",
                "webhook_id": webhook_id,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return False
    except Exception as exc:  # noqa: BLE001
        # DuplicateKeyError (or e11000) => replay. Any other error: treat as
        # not-seen and let the order-id guard decide.
        msg = str(exc).lower()
        if "duplicate key" in msg or "e11000" in msg:
            return True
        return False


def ingest_shopify_order(
    db,
    payload: Dict[str, Any],
    webhook_id: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotently turn a Shopify `orders/create` payload into an IMS order +
    GST tax invoice. PURE of network I/O; operates only on the payload.

    Returns a structured result dict (never raises on the normal paths):
      {"status": "created"|"duplicate"|"replayed"|"simulated"|"ignored"|"error",
       "order_id": <ims order id or None>,
       "invoice_number": <serial or None>,
       "shopify_order_id": <str>,
       "interstate": <bool>, "place_of_supply": <code>}

    Fail-soft: DB absent -> "simulated"; an empty / non-order payload ->
    "ignored". Both the Shopify-order-id guard and the webhook-id guard ensure a
    replay yields exactly ONE order + ONE invoice.
    """
    payload = payload if isinstance(payload, dict) else {}

    # Only ingest the order-create topic. Other Shopify topics (products/update,
    # etc.) are handled elsewhere / ignored here.
    if topic and str(topic).strip().lower() not in ("orders/create", "orders/paid"):
        return {"status": "ignored", "reason": f"topic:{topic}"}

    shopify_order_id = str(payload.get("id") or payload.get("order_id") or "").strip()
    if not shopify_order_id or not (payload.get("line_items")):
        return {"status": "ignored", "reason": "no_order_id_or_line_items"}

    # No DB -> SIMULATE (compute the result shape but persist nothing). Keeps the
    # caller's contract identical whether or not Mongo is reachable.
    if db is None:
        return {
            "status": "simulated",
            "order_id": None,
            "invoice_number": None,
            "shopify_order_id": shopify_order_id,
        }

    try:
        orders_coll = db.get_collection("orders")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_INGEST] orders collection unavailable: %s", exc)
        return {"status": "simulated", "shopify_order_id": shopify_order_id}
    if orders_coll is None:
        return {"status": "simulated", "shopify_order_id": shopify_order_id}

    # --- Layer 2: webhook-id replay guard (cheap, best-effort) --------------
    if _webhook_already_seen(db, webhook_id):
        existing = None
        try:
            existing = orders_coll.find_one({"shopify_order_id": shopify_order_id})
        except Exception:  # noqa: BLE001
            existing = None
        return {
            "status": "replayed",
            "order_id": (existing or {}).get("order_id"),
            "invoice_number": (existing or {}).get("invoice_number"),
            "shopify_order_id": shopify_order_id,
        }

    # --- Layer 1: Shopify-order-id guard (hard backstop) --------------------
    try:
        existing = orders_coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_INGEST] dedupe lookup failed: %s", exc)
        existing = None
    if existing:
        return {
            "status": "duplicate",
            "order_id": existing.get("order_id"),
            "invoice_number": existing.get("invoice_number"),
            "shopify_order_id": shopify_order_id,
        }

    # --- Build the IMS order ------------------------------------------------
    product_repo = None
    try:
        from ..dependencies import get_product_repository

        product_repo = get_product_repository()
    except Exception:  # noqa: BLE001
        product_repo = None

    items = _map_line_items(payload, product_repo)
    if not items:
        return {"status": "ignored", "reason": "no_mappable_line_items"}

    taxable = round(sum(_f(i.get("taxable_value")) for i in items), 2)
    tax = round(sum(_f(i.get("tax_amount")) for i in items), 2)
    grand_total = round(taxable + tax, 2)
    subtotal = round(sum(_f(i.get("item_total")) for i in items), 2)

    store_id = _online_store_id(payload)

    # Place-of-supply split (IGST vs CGST+SGST) reusing the SAME offline logic.
    store_doc: Optional[Dict[str, Any]] = None
    try:
        from ..dependencies import get_store_repository

        store_repo = get_store_repository()
        if store_repo is not None:
            store_doc = store_repo.find_by_id(store_id)
    except Exception:  # noqa: BLE001
        store_doc = None

    # Synthesize a customer-shaped dict carrying the buyer's delivery state so
    # the shared splitter resolves the place of supply.
    cust = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    buyer_state = _delivery_state(payload)
    customer_shim = {
        "gstin": (payload.get("customer_gstin") or (cust.get("gstin") if cust else "")),
        "billing_address": {"state": buyer_state, "state_code": buyer_state},
        "state": buyer_state,
    }

    gst_split: Dict[str, Any] = {}
    try:
        from ..routers.orders import _build_invoice_gst_split

        gst_split = _build_invoice_gst_split(items, store_doc, customer_shim)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_INGEST] gst split failed (continuing): %s", exc)
        gst_split = {}

    customer_name = ""
    if cust:
        customer_name = (
            " ".join(
                str(p) for p in (cust.get("first_name"), cust.get("last_name")) if p
            ).strip()
            or cust.get("email")
            or ""
        )
    customer_name = (
        customer_name
        or payload.get("email")
        or payload.get("contact_email")
        or "Online Customer"
    )

    now = datetime.now(timezone.utc)
    order_id = str(uuid.uuid4())

    # Allocate the GST invoice serial (consecutive per store + FY) the same way
    # POS does -- via the order repository's atomic counter. Best-effort: a
    # DB-less repo falls back to a time-derived unique serial inside
    # next_invoice_number, so we always get a usable, FY-labelled number.
    invoice_number: Optional[str] = None
    try:
        from ..dependencies import get_order_repository

        order_repo = get_order_repository()
        if order_repo is not None:
            try:
                order_repo.ensure_invoice_index()
            except Exception:  # noqa: BLE001
                pass
            invoice_number = order_repo.next_invoice_number(store_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_INGEST] invoice number alloc failed: %s", exc)
        invoice_number = None

    order_doc = {
        "order_id": order_id,
        "_id": order_id,
        "order_number": f"ONL-{shopify_order_id}",
        # CHANNEL tag: finance/P&L counts this once + distinguishes online.
        "channel": "ONLINE",
        "source": "shopify",
        "shopify_order_id": shopify_order_id,
        "shopify_order_name": payload.get("name"),  # e.g. "#1001"
        "store_id": store_id,
        "customer_id": str(payload.get("customer", {}).get("id") or "") or None,
        "customer_name": customer_name,
        "customer_phone": (cust.get("phone") if cust else "")
        or payload.get("phone")
        or "",
        "items": items,
        "subtotal": subtotal,
        "tax_rate": (max((i.get("gst_rate") or 0.0) for i in items) if items else 0.0),
        "tax_amount": tax,
        "total_discount": round(sum(_f(i.get("discount_amount")) for i in items), 2),
        "grand_total": grand_total,
        # Online prices are GST-inclusive (Shopify shows the all-in price paid).
        "pricing_model": "inclusive",
        # Shopify confirmation is the RECEIPT; the IMS invoice is the tax doc.
        "invoice_number": invoice_number,
        "invoice_date": now.isoformat(),
        # Shopify already collected payment -> the online order is PAID on
        # ingestion (its financial_status is typically "paid").
        "amount_paid": (
            grand_total
            if str(payload.get("financial_status") or "").lower()
            in ("paid", "partially_paid")
            else 0.0
        ),
        "balance_due": (
            0.0
            if str(payload.get("financial_status") or "").lower() == "paid"
            else grand_total
        ),
        "payment_status": (
            "PAID"
            if str(payload.get("financial_status") or "").lower() == "paid"
            else "UNPAID"
        ),
        "status": "CONFIRMED",
        "place_of_supply": gst_split.get("place_of_supply", buyer_state),
        "place_of_supply_assumed": gst_split.get("place_of_supply_assumed", False),
        "interstate": gst_split.get("interstate", False),
        "tax_summary": gst_split.get("rows", []),
        "tax_totals": gst_split.get("totals", {}),
        "payments": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        orders_coll.insert_one(dict(order_doc))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        # A concurrent ingest of the same Shopify order tripped a unique index
        # -> treat as a duplicate (idempotent), not an error.
        if "duplicate key" in msg or "e11000" in msg:
            existing = None
            try:
                existing = orders_coll.find_one({"shopify_order_id": shopify_order_id})
            except Exception:  # noqa: BLE001
                existing = None
            return {
                "status": "duplicate",
                "order_id": (existing or {}).get("order_id", order_id),
                "invoice_number": (existing or {}).get(
                    "invoice_number", invoice_number
                ),
                "shopify_order_id": shopify_order_id,
            }
        logger.error("[SHOPIFY_INGEST] order insert failed: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "shopify_order_id": shopify_order_id,
        }

    logger.info(
        "[SHOPIFY_INGEST] online order ingested shopify_id=%s ims_order=%s "
        "invoice=%s interstate=%s",
        shopify_order_id,
        order_id,
        invoice_number,
        order_doc["interstate"],
    )

    # ONLINE-SALE STOCK DECREMENT (oversell fix). An online order previously
    # booked the GST invoice but NEVER reduced physical stock -> a walk-in and an
    # online buyer could sell the SAME serialized unit. Now we (1) FIFO-claim the
    # sold serialized units at the online FULFILLMENT store (reusing the POS
    # _mark_units_sold atomic-claim path), then (2) push the reduced available
    # qty to Shopify so the online listing can't oversell either. BOTH fail-soft:
    # Shopify already took payment, so a stock-side error must NEVER raise out of
    # ingestion -- the invoice is booked regardless and the reconcile sweep
    # (online_sync_health) catches any miss.
    fulfillment_store = _online_fulfillment_store_id(payload)
    try:
        # _mark_units_sold claims by IMS product_id; map each line's resolved
        # ims_product_id onto product_id (the Shopify product_id is NOT the IMS
        # one). Lines with no IMS match are skipped -- not our serialized stock.
        decrement_items = [
            {**it, "product_id": it.get("ims_product_id")}
            for it in items
            if it.get("ims_product_id")
        ]
        if decrement_items:
            from ..routers.orders import _mark_units_sold

            expected = sum(int(it.get("quantity") or 1) for it in decrement_items)
            marked = _mark_units_sold(order_id, decrement_items, fulfillment_store)
            # FAIL LOUD on an under-claim: we booked a paid online order but could
            # NOT decrement every serialized unit (out of physical on-hand at the
            # fulfillment store, or it has no stock_units at all). The invoice
            # stands (Shopify took payment) but this is an oversell that needs
            # operator action -- record it loudly so the sync-health tile + Sentry
            # surface it instead of it slipping by as a warning.
            if len(marked) < expected:
                _record_stock_miss(
                    db,
                    order_id,
                    fulfillment_store,
                    "under_claim",
                    {"expected": expected, "claimed": len(marked)},
                )
    except Exception as exc:  # noqa: BLE001
        _record_stock_miss(db, order_id, fulfillment_store, "exception", str(exc))
    try:
        from .online_stock_writeback import writeback_after_sale

        writeback_after_sale(db, items, fulfillment_store)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOPIFY_INGEST] online stock writeback skipped for %s: %s",
            order_id,
            exc,
        )

    return {
        "status": "created",
        "order_id": order_id,
        "invoice_number": invoice_number,
        "shopify_order_id": shopify_order_id,
        "interstate": order_doc["interstate"],
        "place_of_supply": order_doc["place_of_supply"],
        "grand_total": grand_total,
    }


def ensure_shopify_order_index(db) -> None:
    """Best-effort UNIQUE partial index on `orders.shopify_order_id` so a
    double-create is physically impossible even under a race. Idempotent;
    never raises. Only indexes docs that actually carry the field (legacy /
    offline orders without it are unaffected)."""
    if db is None:
        return
    try:
        coll = db.get_collection("orders")
        if coll is None:
            return
        coll.create_index(
            "shopify_order_id",
            unique=True,
            partialFilterExpression={"shopify_order_id": {"$type": "string"}},
            name="uniq_shopify_order_id",
        )
    except Exception:  # noqa: BLE001
        logger.debug("shopify_order_id index create skipped", exc_info=True)
