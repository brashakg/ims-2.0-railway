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

        # Carry any Rx powers the online channel captured (Shopify line-item
        # properties / a PIM mapping may attach sph/cyl/add/axis to a lens line).
        # These feed the clinical FLAG & HOLD evaluation; absent on a plain
        # frame/sunglass line, where they stay None.
        rx_powers = _extract_line_rx(line)

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
                # Clinical FLAG & HOLD inputs (None on non-lens lines).
                "prescription_id": _line_rx_id(line),
                "sph": rx_powers.get("sph"),
                "cyl": rx_powers.get("cyl"),
                "add": rx_powers.get("add"),
                "axis": rx_powers.get("axis"),
            }
        )
    return items


def _line_rx_id(line: Dict[str, Any]) -> Optional[str]:
    """Pull a prescription id off a Shopify line item, from the common spots a
    PIM / app maps it: a top-level field, or a line-item `properties` entry
    (Shopify's standard custom-attribute container)."""
    direct = line.get("prescription_id") or line.get("prescriptionId") or line.get("rx_id")
    if direct:
        return str(direct).strip() or None
    for prop in line.get("properties") or []:
        if isinstance(prop, dict):
            name = str(prop.get("name") or "").strip().lower().replace(" ", "_")
            if name in ("prescription_id", "prescriptionid", "rx_id", "rx"):
                val = str(prop.get("value") or "").strip()
                if val:
                    return val
    return None


def _extract_line_rx(line: Dict[str, Any]) -> Dict[str, Any]:
    """Extract sph/cyl/add/axis powers from a Shopify line item -- from top-level
    keys or from the line-item `properties` custom attributes. Returns a dict with
    None for any power not present (a plain frame line yields all-None)."""
    out: Dict[str, Any] = {"sph": None, "cyl": None, "add": None, "axis": None}
    for k in out:
        if line.get(k) not in (None, ""):
            out[k] = line.get(k)
    for prop in line.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name") or "").strip().lower().replace(" ", "_")
        if name in out and out[name] in (None, "") and prop.get("value") not in (None, ""):
            out[name] = prop.get("value")
    return out


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


def _fallback_enabled() -> bool:
    """Multi-store fulfillment fallback (owner 2026-07-05): when the preferred
    online fulfillment store can't cover a line, claim the units from whichever
    OTHER store actually holds them. Default ON; set
    ONLINE_FULFILLMENT_FALLBACK=off to pin claims to the preferred store only."""
    import os

    return (os.getenv("ONLINE_FULFILLMENT_FALLBACK") or "on").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def _available_stores_for_product(db, product_id: str) -> List[str]:
    """Store ids holding AVAILABLE serialized units of this product, most stock
    first. Fail-soft -> []. Deterministic tie-break on store_id so re-ingests
    behave identically."""
    try:
        coll = (
            db.get_collection("stock_units")
            if hasattr(db, "get_collection")
            else db["stock_units"]
        )
        rows = list(
            coll.aggregate(
                [
                    {"$match": {"product_id": product_id, "status": "AVAILABLE"}},
                    {"$group": {"_id": "$store_id", "n": {"$sum": 1}}},
                    {"$sort": {"n": -1, "_id": 1}},
                ]
            )
        )
        return [str(r.get("_id")) for r in rows if r.get("_id")]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOPIFY_INGEST] fallback store lookup failed for %s: %s",
            product_id,
            exc,
        )
        return []


def _claim_units_multistore(
    db,
    order_id: str,
    decrement_items: List[Dict[str, Any]],
    preferred_store: str,
):
    """Claim sold units for an online order, preferring `preferred_store` and
    falling back per line to whichever other store holds AVAILABLE units
    (largest holding first). Returns (claimed_total, breakdown) where breakdown
    rows are {product_id, store_id, qty}. Reuses the atomic FIFO claim in
    orders._mark_units_sold, so two concurrent orders can never grab the same
    unit even across the fallback path."""
    from ..routers.orders import _mark_units_sold

    breakdown: List[Dict[str, Any]] = []
    claimed_total = 0
    for line in decrement_items:
        pid = line.get("product_id") or ""
        qty = int(line.get("quantity") or 1)
        if not pid or qty < 1:
            continue
        remaining = qty
        stores: List[str] = [preferred_store] if preferred_store else []
        if _fallback_enabled():
            for s in _available_stores_for_product(db, pid):
                if s not in stores:
                    stores.append(s)
        for s in stores:
            if remaining <= 0:
                break
            marked = _mark_units_sold(order_id, [{**line, "quantity": remaining}], s)
            if marked:
                claimed_total += len(marked)
                breakdown.append(
                    {"product_id": pid, "store_id": s, "qty": len(marked)}
                )
                remaining -= len(marked)
    return claimed_total, breakdown


def _raise_fallback_ship_tasks(
    db,
    order_id: str,
    order_ref: str,
    breakdown: List[Dict[str, Any]],
    preferred: str,
) -> None:
    """One task per NON-preferred store that fulfilled units, so its staff know
    to ship them (deduped per order+store). Fail-soft side channel."""
    stores = sorted(
        {
            str(r["store_id"])
            for r in breakdown
            if r.get("store_id") and str(r["store_id"]) != (preferred or "")
        }
    )
    if not stores:
        return
    try:
        from ..dependencies import get_task_repository
        from .task_triggers import create_system_task

        repo = get_task_repository()
        for s in stores:
            lines = [r for r in breakdown if str(r.get("store_id")) == s]
            units = sum(int(r.get("qty") or 0) for r in lines)
            create_system_task(
                repo,
                title=f"Online order {order_ref}: ship {units} unit(s) from your store",
                description=(
                    "The preferred online fulfillment store did not have stock, so "
                    f"{units} unit(s) of this paid online order were allocated from "
                    "your store. Pack and hand them to dispatch. Products: "
                    + ", ".join(str(r.get("product_id")) for r in lines)
                ),
                priority="P2",
                category="ONLINE_ORDER",
                store_id=s,
                dedupe_ref=f"online_fallback_ship:{order_id}:{s}",
                extra={"link": "/orders", "payload": {"order_id": order_id}},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOPIFY_INGEST] fallback ship task skipped for %s: %s", order_id, exc
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


# ---------------------------------------------------------------------------
# HISTORICAL import support (scripts/import_shopify_order_history.py).
# A historical import replays the FULL Shopify order history as settled IMS
# orders so finance / GST / reports see the online back-catalogue. It reuses
# THIS exact create path (line mapping + inclusive GST split + place-of-supply
# machinery) but, via `historical=True`, MUST NOT fire any live side effect:
# no Rx flag-and-hold, no stock decrement, no oversell write-back, no task
# creation, no loyalty earn, no messaging -- and no fresh per-FY invoice serial
# (a back-dated order can never consume the CURRENT financial year's consecutive
# sequence -- CGST Rule 46(b)). The order lands in a terminal status with its
# real Shopify order date preserved. Default False -> the live webhook path is
# byte-identical to before.
# ---------------------------------------------------------------------------

# Shopify financial_status -> IMS payment_status for a SETTLED historical order.
# NOTE: partially_paid -> PAID (not PARTIAL). A historical import books the sale as
# fully SETTLED (amount_paid == grand_total, balance_due == 0, one settled payment
# row) so it never fabricates a phantom years-old receivable; labeling it PARTIAL
# while balance_due == 0 was internally inconsistent (a PARTIAL implies an open
# balance). The tiny uncollected remainder is treated as collected/written off at
# import time. (partially_refunded / refunded describe REFUND state, not payment,
# and net via synthesized GST credit notes -- see _book_historical_refund_credit_notes.)
_HIST_PAYMENT_STATUS_MAP = {
    "paid": "PAID",
    "partially_paid": "PAID",
    "partially_refunded": "PARTIAL_REFUND",
    "refunded": "REFUNDED",
}

# Shopify fulfillment_status -> IMS fulfillment_status (historical assumes shipped).
_HIST_FULFILLMENT_MAP = {
    "fulfilled": "FULFILLED",
    "partial": "PARTIAL",
    "restocked": "RESTOCKED",
}


def _order_datetime(payload: Dict[str, Any]) -> datetime:
    """The real order timestamp for a HISTORICAL import: Shopify `created_at`
    (fallback `processed_at` / `updated_at`), parsed to an aware UTC datetime.
    Falls back to now() only when none is parseable, so a historical order never
    silently loses its accounting period. Never raises."""
    for key in ("created_at", "processed_at", "updated_at"):
        raw = payload.get(key)
        if not raw:
            continue
        try:
            s = str(raw).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001 -- unparseable date -> try the next key
            continue
    return datetime.now(timezone.utc)


def _to_naive_utc(raw: Any) -> Optional[datetime]:
    """Parse a Shopify ISO timestamp to a NAIVE-UTC datetime (tzinfo stripped),
    the exact shape orders persist `created_at` in so it compares apples-to-apples
    with every finance/GST datetime window. Returns None when unparseable."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc).replace(tzinfo=None) if raw.tzinfo else raw
    try:
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def _historical_overrides(payload: Dict[str, Any], grand_total: float) -> Dict[str, Any]:
    """The order-doc fields a HISTORICAL import overlays on the shared create
    shape: a terminal lifecycle status, a SETTLED payment (Shopify collected the
    money at sale time -> no phantom receivable), NO Rx / fulfillment hold (long
    dispensed), and the traceability markers. Tax math is untouched (reused)."""
    fin = str(payload.get("financial_status") or "").strip().lower()
    ful = str(payload.get("fulfillment_status") or "").strip().lower()
    if payload.get("cancelled_at"):
        status = "CANCELLED"
    elif fin == "refunded":
        status = "REFUNDED"
    else:
        status = "DELIVERED"
    return {
        "status": status,
        "payment_status": _HIST_PAYMENT_STATUS_MAP.get(fin, "PAID"),
        "fulfillment_status": _HIST_FULFILLMENT_MAP.get(ful, "FULFILLED"),
        # Settled OUTSIDE IMS at sale time -> full amount paid, zero balance so AR /
        # cash reports are never polluted with a fake receivable for an old order.
        "amount_paid": grand_total,
        "balance_due": 0.0,
        # A years-old sale is long dispensed: never Rx-hold or fulfillment-hold it.
        "rx_pending": False,
        "rx_hold_reasons": [],
        "rx_hold_reason": "",
        "fulfillment_hold": False,
        # Reversible + traceable, and protects the imported status from a late
        # Shopify webhook (see _sync_existing_order_status historical guard).
        "historical": True,
        "import_source": "shopify_order_history",
        # A single SETTLED payment row: the money was collected OUTSIDE IMS at sale
        # time (Shopify), so /gst/cross-check payments_collected (which sums
        # order.payments[].amount) stays COHERENT with sales_grand for back-months
        # instead of reading ~0 against a large books figure. This mirrors real
        # accounting: the full sale was collected, and any refund is booked
        # SEPARATELY as a GST credit note (see _book_historical_refund_credit_notes),
        # exactly like a live sale followed by a later refund.
        "payments": [
            {
                "payment_id": str(uuid.uuid4()),
                "method": "SHOPIFY",
                "amount": round(float(grand_total), 2),
                "reference": f"shopify:{payload.get('id')}",
                "status": "SETTLED",
                "settled_outside_ims": True,
                "received_by": "SYSTEM_SHOPIFY_HISTORY_IMPORT",
                "received_at": (
                    (_to_naive_utc(payload.get("created_at")) or _order_datetime(payload)
                     .astimezone(timezone.utc).replace(tzinfo=None)).isoformat()
                ),
            }
        ],
    }


def _refund_credit_note_date_iso(refund: Dict[str, Any], order_payload: Dict[str, Any]) -> str:
    """The ISO-STRING date a synthesized historical credit note is stamped with:
    the refund's own processed_at / created_at (so the CDNR is reported in the
    period the refund was ISSUED -- correct GST credit-note timing), falling back to
    the order date. Returned as a naive-UTC ISO STRING because the GSTR-1 CDNR query
    (reports.py) and store_credit_ledger.make_entry both bound / write
    credit_note_ledger.created_at as STRINGS."""
    for raw in (refund.get("processed_at"), refund.get("created_at")):
        dt = _to_naive_utc(raw)
        if dt is not None:
            return dt.isoformat()
    return (
        _order_datetime(order_payload).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    )


def _ensure_unique_hist_cn_ref_index(db) -> None:
    """Concurrency backstop for the historical CN booking: a UNIQUE PARTIAL index
    on credit_note_ledger.ref scoped to THIS import's rows (historical=True).

    Closes the find-then-insert race between the create-branch booking and a
    concurrent duplicate-path heal (parallel apply runs / the E11000 loser): the
    racing loser's insert trips the index and is treated as a DUPLICATE, never
    booked twice.

    Deliberately NARROWER than an all-string-refs unique index: live writers can
    legitimately reuse a ref (the manual store-credit endpoint persists a
    caller-supplied body.ref -- customers.py), so a blanket unique index on ref
    would start rejecting live inserts. Historical CN refs
    ('SHOPIFY-HIST-REFUND-<refund id>') are unique by construction and every
    historical CN row stamps historical=True, so the partial filter pins exactly
    the racing writes. Idempotent + fail-soft: an index-build failure (e.g.
    unexpected pre-existing duplicate rows in prod) logs and skips -- booking
    then proceeds with the find-then-insert guard exactly as before, never
    crashing the import."""
    try:
        coll = db.get_collection("credit_note_ledger")
        if coll is None:
            return
        coll.create_index(
            "ref",
            name="uniq_hist_cn_ref",
            unique=True,
            partialFilterExpression={
                "ref": {"$type": "string"},
                "historical": True,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- backstop only; never block booking
        logger.warning(
            "[SHOPIFY_INGEST] uniq_hist_cn_ref index ensure failed (continuing "
            "with find-then-insert dedupe): %s",
            exc,
        )


def _hist_cn_note_number(refund_id: str) -> str:
    """Short DETERMINISTIC GSTN-legal note number (<=16 chars, charset [A-Z0-9-])
    for a synthesized historical credit note: 'CNH-' + the alphanumeric tail of
    the Shopify refund id. Stored in a DEDICATED field (``note_number``) that the
    GSTR-1 CDNR builder prefers -- the internal idempotency ``ref``
    ('SHOPIFY-HIST-REFUND-<id>') is NEVER changed."""
    tail = "".join(ch for ch in str(refund_id) if ch.isalnum())[-12:].upper()
    return f"CNH-{tail}" if tail else "CNH-0"


def _book_historical_refund_credit_notes(
    db, payload: Dict[str, Any], order_doc: Dict[str, Any]
) -> Dict[str, Any]:
    """Synthesize GST CREDIT NOTES for a refunded / partially_refunded HISTORICAL
    order so the imported refund NETS the imported sale in GSTR-1 (CDNR) instead of
    leaving the full sale as overstated output tax + revenue.

    NO NEW TAX MATH -- every rupee of taxable/tax is recovered from the ORIGINAL
    order line and backed out through the SAME machinery the live Shopify-refund
    path uses (shopify_refund._build_return_lines -> returns._priced_return_lines ->
    returns_engine.gst_breakup_lines). The reversal is written to
    `credit_note_ledger` (the CDNR source reports.py reads) with:
      * created_at as an ISO STRING (the CDNR query + the ledger both use strings);
      * delta 0.0 / settlement EXTERNAL -- the money went back OUTSIDE IMS years ago,
        so the CDNR row is written for the GST reversal but NO redeemable store
        credit is minted (mirrors the live card-refund bump_balance=False shape);
      * a GSTN-legal ``note_number`` ('CNH-<id tail>', <=16 chars) for the CDNR
        filing (the internal ref stays the long idempotency key).

    AMOUNT RECONCILIATION (mirrors the live path's _shopify_refunded_amount +
    DISCREPANCY routing): the billed line gross may differ from what Shopify
    ACTUALLY refunded (partial / goodwill / restock-without-refund). When they
    disagree beyond _AMOUNT_EPS the credit note is PROPORTIONALLY SCALED to the
    actual refunded amount (pure arithmetic on the already-derived split -- no
    re-derived GST; ``billed_gross`` records the pre-scale figure), and a refund
    Shopify shows NO money for is reported as residual, never auto-posted. The
    billed gross is never silently over-posted.

    A minimal `returns` doc stamped with each Shopify refund id is ALSO written so a
    FUTURE real refund webhook for the same refund is recognised as already-credited
    by shopify_refund._refund_already_processed (idempotent), rather than
    double-crediting.

    Coverage: a `partially_refunded` order books one credit note per refund entry
    (from its refund_line_items). A fully `refunded` order with no per-line refund
    detail -- refunds[] missing OR every refund amount-only/unmappable (the normal
    Shopify shape for 'refund custom amount' / shipping-only refunds) -- reverses
    the WHOLE order (still via the reused pricing, reconciled against the total
    actually refunded, idempotent on the ``orderfull:<id>`` pseudo id) so a full
    refund can never book as full revenue. Amount-only refunds on a
    partially_refunded order are counted in `unmapped_refunds` (reported, never
    fabricated).

    Idempotent + fail-soft: an already-booked ref/refund-id is skipped; any error is
    swallowed (the order is already booked). Returns a small summary dict."""
    summary: Dict[str, Any] = {
        "credit_notes": 0,
        "gross": 0.0,
        "tax": 0.0,
        "unmapped_refunds": 0,
        # CN bookings that FAILED (pricing exception / ledger insert error) --
        # distinct from unmapped residuals: these are healable by a re-run (the
        # duplicate path re-invokes this idempotent function).
        "cn_failed": 0,
        "cn_failed_refund_ids": [],
        # Refunds whose CN was ALREADY booked (by a prior run or a concurrent
        # racer). Non-zero GATES OFF the whole-order fallback: the prior run's
        # per-refund booking decision and residual policy stand -- re-firing the
        # fallback would stack a whole-order CN on top of existing per-refund
        # CNs (reversal exceeding the invoice).
        "duplicate_refunds": 0,
    }
    fin = str(payload.get("financial_status") or "").strip().lower()
    if fin not in ("refunded", "partially_refunded"):
        return summary

    refunds = payload.get("refunds")
    if not isinstance(refunds, list) or not refunds:
        # No per-refund detail. A FULL refund still must net the whole sale, so
        # synthesize a single whole-order reversal; a partial with no detail can't
        # be line-mapped -> report it as residual (never fabricate a split).
        if fin == "refunded":
            refunds = [
                {"id": f"orderfull:{payload.get('id')}", "_reverse_whole_order": True}
            ]
        else:
            summary["unmapped_refunds"] = 1
            return summary

    try:
        ledger = db.get_collection("credit_note_ledger")
    except Exception:  # noqa: BLE001
        ledger = None
    if ledger is None:
        return summary
    try:
        returns_coll = db.get_collection("returns")
    except Exception:  # noqa: BLE001
        returns_coll = None

    try:
        from ..routers.returns import ReturnLine, _priced_return_lines
        from . import returns_engine as engine
        from . import shopify_refund
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_INGEST] historical refund machinery import failed: %s", exc)
        return summary

    # Concurrency backstops (idempotent, fail-soft): the unique partial index on
    # historical CN refs closes the create-vs-heal booking race, and the returns
    # uniq_shopify_refund_id index (normally built lazily by the LIVE refund
    # path only) gives the historical stamps the same insert-level guarantee.
    _ensure_unique_hist_cn_ref_index(db)
    try:
        shopify_refund._ensure_unique_refund_index(
            db, shopify_refund._RETURNS_COLLECTION
        )
    except Exception:  # noqa: BLE001 -- backstop only
        pass

    customer_id = order_doc.get("customer_id")
    billing_store = order_doc.get("store_id")
    order_items = [i for i in (order_doc.get("items") or []) if isinstance(i, dict)]
    unmapped_real_ids: List[str] = []

    def _stamp_returns_doc(
        refund_id: str, gross: float, gst_view: Dict[str, Any], cn_date: str,
        covered_by: Optional[str] = None,
    ) -> None:
        """Minimal returns doc stamped with the refund id so a FUTURE real refund
        webhook for this same refund is recognised as already-credited."""
        if returns_coll is None:
            return
        try:
            doc = {
                "return_id": f"RET-HIST-{refund_id}",
                "shopify_refund_id": refund_id,
                "order_id": order_doc.get("order_id"),
                "order_number": order_doc.get("order_number"),
                "customer_id": customer_id,
                "store_id": billing_store,
                "return_type": "CREDIT_NOTE",
                "source": "shopify_order_history",
                "channel": "ONLINE",
                "returned_value": round(gross, 2),
                "gross_refund": round(gross, 2),
                "net_refund": round(gross, 2),
                "gst_breakup": gst_view,
                "credit_note_issued": True,
                "settled_externally": True,
                "status": "COMPLETED",
                "historical": True,
                "created_at": cn_date,
            }
            if covered_by:
                doc["covered_by_ref"] = covered_by
            returns_coll.insert_one(doc)
        except Exception:  # noqa: BLE001
            pass

    def _process(refund: Dict[str, Any]) -> str:
        """Book ONE refund's credit note. Returns 'booked' | 'duplicate' |
        'unmapped' | 'skipped' (idempotency, mapping and reconciliation applied)."""
        refund_id = str(refund.get("id") or "").strip()
        if not refund_id:
            return "skipped"
        ref = f"SHOPIFY-HIST-REFUND-{refund_id}"

        # Idempotency: skip if a credit note for this ref OR a returns doc for this
        # refund id already exists (re-run of the import, or a webhook handled it).
        try:
            if ledger.find_one({"ref": ref}) is not None:
                return "duplicate"
        except Exception:  # noqa: BLE001
            pass
        try:
            if returns_coll is not None and (
                returns_coll.find_one({"shopify_refund_id": refund_id}) is not None
            ):
                return "duplicate"
        except Exception:  # noqa: BLE001
            pass

        # Build return lines: ALL order lines for a whole-order full-refund reversal,
        # else the refund's own refund_line_items (reusing the live refund mapper).
        if refund.get("_reverse_whole_order"):
            return_lines = [
                ReturnLine(
                    order_item_id=str(it.get("item_id")) or None,
                    product_id=(
                        str(it.get("ims_product_id") or it.get("product_id") or "")
                        or None
                    ),
                    product_name=str(it.get("product_name") or ""),
                    sku=str(it.get("sku") or ""),
                    return_qty=float(it.get("quantity") or 1),
                    unit_price=0.0,
                    condition="GOOD",
                    restock=False,
                    reason="Shopify refund (historical import)",
                )
                for it in order_items
                if float(it.get("quantity") or 0) > 0
            ]
        else:
            try:
                return_lines = shopify_refund._build_return_lines(refund, order_doc)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[SHOPIFY_INGEST] historical refund %s line-build failed: %s",
                    refund_id, exc,
                )
                return_lines = []

        if not return_lines:
            summary["unmapped_refunds"] += 1
            unmapped_real_ids.append(refund_id)
            logger.info(
                "[SHOPIFY_INGEST] historical refund %s on order %s not line-mappable "
                "-- credit note skipped (residual reported)",
                refund_id, order_doc.get("order_id"),
            )
            return "unmapped"

        try:
            priced = _priced_return_lines(return_lines, order_doc)
            gross = engine.returned_value(priced)
            gst_view = engine.gst_breakup_lines(priced)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SHOPIFY_INGEST] historical refund %s pricing failed: %s", refund_id, exc
            )
            return "skipped"
        if gross <= 0:
            # Order lines carried no usable billed value -> nothing derivable;
            # count the residual (consistent with the unmappable branch above).
            # Appended to unmapped_real_ids so the whole-order fallback COVERS it
            # (stamped into returns -> a webhook redelivery dedupes instead of
            # double-crediting on top of the whole-order reversal).
            summary["unmapped_refunds"] += 1
            unmapped_real_ids.append(refund_id)
            return "unmapped"

        # AMOUNT RECONCILIATION against what Shopify ACTUALLY refunded (reuses the
        # live helper). The whole-order fallback carries the aggregate in
        # _actual_refunded_total; a per-refund entry is read from its own payload.
        if "_actual_refunded_total" in refund:
            actual = refund.get("_actual_refunded_total")
        else:
            actual = shopify_refund._shopify_refunded_amount(refund)
        billed_gross = gross
        reconciled = False
        clamped = False
        excess = 0.0
        if actual is not None and abs(actual - gross) > shopify_refund._AMOUNT_EPS:
            if actual <= 0:
                # Shopify shows NO money actually refunded (restock-without-refund
                # event) -> there is no output-tax reversal to book; report the
                # residual instead of fabricating a credit note. Appended to
                # unmapped_real_ids for the same covered-stamp reason as gross<=0.
                summary["unmapped_refunds"] += 1
                unmapped_real_ids.append(refund_id)
                logger.info(
                    "[SHOPIFY_INGEST] historical refund %s on order %s shows zero "
                    "refunded amount vs billed Rs %.2f -- credit note skipped "
                    "(residual reported)",
                    refund_id, order_doc.get("order_id"), gross,
                )
                return "unmapped"
            if actual > gross:
                # CLAMP AT BILLED GROSS: a credit note can never exceed the
                # original invoice value. Shopify's refunded total INCLUDES
                # shipping/goodwill amounts the import never booked as sale
                # revenue (line items only), so scaling UP would reverse output
                # tax that was never output. Post the FULL billed reversal and
                # surface the excess as an unreconciled residual for the
                # operator/accountant.
                excess = round(actual - gross, 2)
                reconciled = True
                clamped = True
                logger.warning(
                    "[SHOPIFY_INGEST] historical refund %s on order %s: Shopify "
                    "refunded Rs %.2f > billed Rs %.2f -- credit note CLAMPED at "
                    "the billed gross; excess Rs %.2f (shipping/goodwill never "
                    "booked as sale) left un-reversed",
                    refund_id, order_doc.get("order_id"), actual, gross, excess,
                )
            else:
                # Proportionally scale DOWN to the ACTUAL refunded amount (pure
                # arithmetic on the already-derived split -- never re-derive GST;
                # never silently over-post the billed gross).
                factor = actual / gross
                scaled_taxable = round(
                    float(gst_view.get("taxable") or 0.0) * factor, 2
                )
                gross = round(actual, 2)
                gst_view = dict(gst_view)
                gst_view["gross"] = gross
                gst_view["taxable"] = scaled_taxable
                gst_view["tax"] = round(gross - scaled_taxable, 2)
                reconciled = True
                logger.info(
                    "[SHOPIFY_INGEST] historical refund %s on order %s reconciled: "
                    "billed Rs %.2f -> actual Rs %.2f (credit note scaled)",
                    refund_id, order_doc.get("order_id"), billed_gross, gross,
                )

        cn_date = _refund_credit_note_date_iso(refund, payload)
        cn_tax = round(float(gst_view.get("tax") or 0.0), 2)
        entry = {
            "entry_id": str(uuid.uuid4()),
            "customer_id": customer_id,
            "type": "ISSUED",
            "amount": round(gross, 2),
            # delta 0: settled OUTSIDE IMS (money returned via Shopify long ago) ->
            # the CDNR row exists for the GST reversal but mints NO store credit.
            "delta": 0.0,
            "balance_after": None,
            "settlement": "EXTERNAL",
            "reason": (
                f"Shopify refund {refund_id} for order "
                f"{order_doc.get('order_id')} (historical import)"
            ),
            "ref": ref,
            # DEDICATED GSTN-legal CDNR note number (<=16 chars) -- the GSTR-1
            # CDNR builder prefers it; the long ref stays the idempotency key.
            "note_number": _hist_cn_note_number(refund_id),
            "store_id": billing_store,
            "created_by": "SYSTEM_SHOPIFY_HISTORY_IMPORT",
            "created_at": cn_date,
            "gross_refund": round(gross, 2),
            "net_refund": round(gross, 2),
            "taxable": round(float(gst_view.get("taxable") or 0.0), 2),
            "tax": cn_tax,
            "gst_rate": gst_view.get("gst_rate"),
            "shopify_refund_id": refund_id,
            "channel": "ONLINE",
            "historical": True,
        }
        if reconciled:
            entry["billed_gross"] = round(billed_gross, 2)
            entry["reconciliation"] = (
                "CLAMPED_AT_BILLED_GROSS" if clamped else "SCALED_TO_SHOPIFY_REFUNDED"
            )
            if clamped:
                # The shipping/goodwill amount Shopify refunded ON TOP of the
                # booked sale -- deliberately NOT reversed (never booked as
                # output tax); recorded for accountant reconciliation.
                entry["unreconciled_excess"] = excess
        try:
            ledger.insert_one(dict(entry))
        except Exception as exc:  # noqa: BLE001
            if shopify_refund._is_dup_key(exc):
                # A concurrent run (create-branch booking vs duplicate-path
                # heal, or two parallel heals) won the unique-index insert for
                # this ref -- this is a DUPLICATE, not a failure: never counted
                # as cn_failed, never told to 're-run to heal'.
                logger.info(
                    "[SHOPIFY_INGEST] historical credit note for %s already "
                    "inserted by a concurrent run -- treated as duplicate",
                    refund_id,
                )
                return "duplicate"
            logger.warning(
                "[SHOPIFY_INGEST] historical credit note insert failed for %s: %s",
                refund_id, exc,
            )
            return "skipped"

        _stamp_returns_doc(refund_id, gross, gst_view, cn_date)

        summary["credit_notes"] += 1
        summary["gross"] = round(summary["gross"] + gross, 2)
        summary["tax"] = round(summary["tax"] + cn_tax, 2)
        return "booked"

    def _record_failed(refund: Dict[str, Any]) -> None:
        summary["cn_failed"] += 1
        rid = str(refund.get("id") or "?").strip() or "?"
        if len(summary["cn_failed_refund_ids"]) < 20:
            summary["cn_failed_refund_ids"].append(rid)

    for refund in refunds:
        if not isinstance(refund, dict):
            continue
        status = _process(refund)
        if status == "skipped":
            # Pricing exception / ledger insert failure -- NOT a residual: the
            # booking is idempotent, so a re-run of the import heals it (the
            # duplicate ingest path re-invokes this function).
            _record_failed(refund)
        elif status == "duplicate":
            # Booked by a PRIOR run (or a concurrent racer). Tracked so the
            # whole-order fallback below never fires on a heal re-run and
            # stacks a whole-order CN on top of existing per-refund CNs.
            summary["duplicate_refunds"] += 1

    # TERMINALLY fully-refunded order whose refunds[] exist but NONE produced a
    # line-mapped credit note (all amount-only / unmappable -- Shopify's 'refund
    # custom amount' shape): fall through to the WHOLE-ORDER reversal (same
    # orderfull:<id> idempotency key as the missing-refunds[] branch), reconciled
    # against the TOTAL Shopify actually refunded. Without this the order stays
    # status=REFUNDED in GSTR-1 with the full sale as output tax and NO CDNR.
    if (
        fin == "refunded"
        and summary["credit_notes"] == 0
        # HEAL-RERUN GUARD: any refund on this order ALREADY booked by a prior
        # run (or a concurrent racer) means that run's per-refund booking
        # decision and residual policy stand -- firing the fallback here would
        # stack a whole-order CN on top of the existing per-refund CN(s),
        # reversing more than the original invoice (GST-illegal).
        and summary["duplicate_refunds"] == 0
        # FAILED-BOOKING GUARD (mirror of the heal-rerun guard above): a FAILED
        # per-refund booking (transient pricing/ledger error) is promised
        # heal-on-re-run -- the whole-order fallback must not pre-empt that in
        # the SAME run. Without this, run 1 books the whole-order CN at the
        # full actual total (which sums the failed refund too) and the run-2
        # heal then stacks the per-refund CN on top of it (reversal > invoice).
        and summary["cn_failed"] == 0
        and unmapped_real_ids
        and order_items
    ):
        actual_total = 0.0
        actual_found = False
        latest_dt: Optional[datetime] = None
        for r in refunds:
            if not isinstance(r, dict):
                continue
            amt = shopify_refund._shopify_refunded_amount(r)
            if amt is not None:
                actual_total += amt
                actual_found = True
            for raw in (r.get("processed_at"), r.get("created_at")):
                dt = _to_naive_utc(raw)
                if dt is not None:
                    if latest_dt is None or dt > latest_dt:
                        latest_dt = dt
                    break
        synthetic: Dict[str, Any] = {
            "id": f"orderfull:{payload.get('id')}",
            "_reverse_whole_order": True,
            "_actual_refunded_total": (
                round(actual_total, 2) if actual_found else None
            ),
        }
        if latest_dt is not None:
            # CDNR period = when the refund was ISSUED, not the sale date.
            synthetic["processed_at"] = latest_dt.isoformat()
        covered = list(unmapped_real_ids)
        fb_status = _process(synthetic)
        if fb_status == "skipped":
            _record_failed(synthetic)
        if fb_status in ("booked", "duplicate"):
            # 'booked': fresh whole-order reversal. 'duplicate': a PRIOR run
            # already booked the whole-order CN but its covered-stamps may have
            # silently failed (fail-soft insert) -- re-running the stamp block
            # here makes a stamp failure HEALABLE on re-run (the per-id
            # existence pre-check keeps it idempotent). Either way the covered
            # refunds are netted by a whole-order CN, so they are no longer an
            # unreported residual.
            if fb_status == "booked":
                summary["whole_order_fallback"] = True
            summary["unmapped_refunds"] = max(
                0, summary["unmapped_refunds"] - len(covered)
            )
            # Stamp each real refund id so a future webhook redelivery dedupes
            # instead of double-crediting on top of the whole-order reversal.
            cn_date = _refund_credit_note_date_iso(synthetic, payload)
            for rid in covered:
                try:
                    if returns_coll is not None and (
                        returns_coll.find_one({"shopify_refund_id": rid}) is not None
                    ):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                _stamp_returns_doc(
                    rid, 0.0, {},
                    cn_date,
                    covered_by=f"SHOPIFY-HIST-REFUND-orderfull:{payload.get('id')}",
                )

    return summary


def _heal_historical_refund_credit_notes(
    db, payload: Dict[str, Any], existing: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """DUPLICATE-path self-heal for a HISTORICAL re-run: re-invoke the IDEMPOTENT
    credit-note synthesizer against the EXISTING order doc, so a partial failure
    on a prior apply run (order inserted, CN booking failed on a transient error)
    is repaired by simply re-running the import -- without this, the duplicate
    guard fires before the create-branch CN booking and the missing GST reversal
    is permanently unrecoverable.

    ONLY heals orders THIS import created (import_source=shopify_order_history):
    refunds against a genuinely-live webhook-created order belong to the
    shopify_refund accountant-review path and must NEVER be auto-posted here
    (a queued PENDING review + a historical auto-post would double-reverse).
    Already-booked refunds return 'duplicate' inside the synthesizer, so a
    healthy re-run books nothing. Fail-soft -> None."""
    if not isinstance(existing, dict):
        return None
    if existing.get("import_source") != "shopify_order_history":
        return None
    try:
        return _book_historical_refund_credit_notes(db, payload, existing)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOPIFY_INGEST] duplicate-path credit-note heal failed soft: %s", exc
        )
        return None


def ingest_shopify_order(
    db,
    payload: Dict[str, Any],
    webhook_id: Optional[str] = None,
    topic: Optional[str] = None,
    historical: bool = False,
) -> Dict[str, Any]:
    """Idempotently turn a Shopify `orders/create` payload into an IMS order +
    GST tax invoice. PURE of network I/O; operates only on the payload.

    `historical=True` (default False) replays a SETTLED back-catalogue order for
    finance/GST visibility: it reuses every bit of the line/GST math below but
    skips ALL live side effects (Rx hold, stock decrement, oversell write-back,
    task creation) and mints NO fresh per-FY invoice serial, landing the order in
    a terminal status with its real Shopify date. See the section header above.

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
        result = {
            "status": "duplicate",
            "order_id": existing.get("order_id"),
            "invoice_number": existing.get("invoice_number"),
            "shopify_order_id": shopify_order_id,
        }
        if historical:
            # SELF-HEAL: a prior apply run may have inserted the order but
            # failed the (fail-soft) credit-note booking -- re-invoke the
            # IDEMPOTENT synthesizer so a re-run repairs the missing GST
            # reversal instead of it being permanently unrecoverable.
            refund_cn = _heal_historical_refund_credit_notes(db, payload, existing)
            if refund_cn is not None:
                result["refund_credit_notes"] = refund_cn
        return result

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

    # --- Clinical compliance: FLAG & HOLD spectacle-lens lines missing a valid Rx
    # A paid online sale is NEVER refused, but a prescription-lens line with no
    # valid customer-matching non-expired Rx (or an out-of-range power) is flagged
    # + held + a follow-up task raised so the store collects the Rx before
    # dispensing. Contacts / frames / sunglasses are EXEMPT. Fail-soft: any error
    # leaves the order un-held rather than blocking the booked sale.
    rx_eval: Dict[str, Any] = {"rx_pending": False, "reasons": [], "lines": [], "detail": ""}
    # HISTORICAL import: a years-old, already-dispensed order is NEVER Rx-held, so
    # the flag-and-hold evaluation (and its follow-up task, below) is skipped whole.
    if not historical:
        try:
            from .online_rx_hold import evaluate_rx_hold

            # Prefer a pre-resolved IMS customer id (stamped by online_order_mapper)
            # so the Rx match uses the IMS customer's prescriptions; fall back to the
            # raw Shopify customer id (won't match IMS Rx -> safely over-holds).
            rx_customer_id = (
                str(payload.get("_ims_customer_id") or "").strip()
                or str(payload.get("customer", {}).get("id") or "")
                or None
            )
            rx_eval = evaluate_rx_hold(db, items, rx_customer_id)
        except Exception as exc:  # noqa: BLE001 - compliance check must never block a sale
            logger.warning("[SHOPIFY_INGEST] rx hold evaluation skipped: %s", exc)

    # Order timestamp: the live path stamps NOW; a HISTORICAL import preserves the
    # real Shopify order date so the sale lands in its true accounting period.
    #
    # Persist created_at / updated_at / invoice_date as NAIVE-UTC BSON DATETIMES,
    # never ISO strings. Every date-windowed finance/GST query bounds created_at
    # with naive-UTC datetimes (finance._parse_range_dt / _apply_created_at_range,
    # reports.ist_day_start_utc); MongoDB type-bracketing means a Date-typed
    # $gte/$lt range NEVER matches a STRING field, so a string-dated order is
    # silently invisible to GSTR-1/3B, the GST cross-check, GST reconciliation, P&L
    # and cash-flow -- the exact reason the online back-catalogue (and the live
    # online orders) failed to appear. Storing a datetime makes online orders
    # type-consistent with the offline POS (BaseRepository._add_timestamps also
    # stamps a naive datetime). Applies to BOTH the live webhook and the import.
    now = _order_datetime(payload) if historical else datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc).replace(tzinfo=None)
    order_id = str(uuid.uuid4())

    # Allocate the GST invoice serial (consecutive per store + FY) the same way
    # POS does -- via the order repository's atomic counter. Best-effort: a
    # DB-less repo falls back to a time-derived unique serial inside
    # next_invoice_number, so we always get a usable, FY-labelled number.
    #
    # HISTORICAL import: NO serial is minted. A back-dated order must never consume
    # the CURRENT financial year's consecutive invoice sequence (CGST Rule 46(b)) --
    # that would corrupt the live serial run. invoice_number stays None; GST
    # reconciliation falls back to order_number and flags has_invoice_number=False.
    invoice_number: Optional[str] = None
    if not historical:
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
        "invoice_date": now,
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
        # CLINICAL FLAG & HOLD: a prescription-lens line without a valid Rx (or an
        # out-of-range power) marks the order rx_pending + fulfillment_hold so it
        # is NOT auto-dispensed. The invoice / payment are unaffected (paid sale).
        "rx_pending": rx_eval.get("rx_pending", False),
        "rx_hold_reasons": rx_eval.get("reasons", []),
        "rx_hold_reason": rx_eval.get("detail", ""),
        "fulfillment_hold": rx_eval.get("rx_pending", False),
        "place_of_supply": gst_split.get("place_of_supply", buyer_state),
        "place_of_supply_assumed": gst_split.get("place_of_supply_assumed", False),
        "interstate": gst_split.get("interstate", False),
        "tax_summary": gst_split.get("rows", []),
        "tax_totals": gst_split.get("totals", {}),
        "payments": [],
        "created_at": now,
        "updated_at": now,
    }

    # HISTORICAL import: overlay the terminal status + settled payment + traceability
    # markers on the shared create shape (the real order dates are already preserved
    # via `now`; the tax lines / GST split above are reused untouched).
    if historical:
        order_doc.update(_historical_overrides(payload, grand_total))

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
            result = {
                "status": "duplicate",
                "order_id": (existing or {}).get("order_id", order_id),
                "invoice_number": (existing or {}).get(
                    "invoice_number", invoice_number
                ),
                "shopify_order_id": shopify_order_id,
            }
            if historical:
                # SELF-HEAL (see the Layer-1 duplicate guard): re-invoke the
                # idempotent CN synthesizer for the racing winner's order doc.
                refund_cn = _heal_historical_refund_credit_notes(
                    db, payload, existing
                )
                if refund_cn is not None:
                    result["refund_credit_notes"] = refund_cn
            return result
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

    # HISTORICAL import: a settled back-catalogue order has NO live side effects --
    # the units shipped long ago. Skip the Rx-hold task, the stock decrement, the
    # oversell write-back, and the fallback ship tasks entirely. The ONE bit of
    # bookkeeping a settled historical order still needs is the GST reversal for any
    # refund it carries -- synthesized as a credit note (CDNR) so a refunded sale
    # nets in GSTR-1 instead of counting as full output tax. Then return so nothing
    # downstream (loyalty / messaging / inventory) is ever touched.
    if historical:
        refund_cn = _book_historical_refund_credit_notes(db, payload, order_doc)
        return {
            "status": "created",
            "order_id": order_id,
            "invoice_number": None,
            "shopify_order_id": shopify_order_id,
            "interstate": order_doc["interstate"],
            "place_of_supply": order_doc["place_of_supply"],
            "grand_total": grand_total,
            "rx_pending": False,
            "historical": True,
            "refund_credit_notes": refund_cn,
        }

    # CLINICAL FLAG & HOLD follow-up task. Raised ONLY on a fresh create (the
    # duplicate / replay guards above return before this point), so re-ingesting
    # the same Shopify order never raises a second task. Idempotent at the task
    # layer too (guards on order_id + task_type). Fail-soft.
    if rx_eval.get("rx_pending"):
        try:
            from .online_rx_hold import raise_rx_hold_task

            raise_rx_hold_task(
                db,
                order_id=order_id,
                order_ref=order_doc.get("order_number") or shopify_order_id,
                store_id=_online_fulfillment_store_id(payload),
                channel="shopify",
                evaluation=rx_eval,
            )
        except Exception as exc:  # noqa: BLE001 - task is a side-channel, fail-soft
            logger.warning(
                "[SHOPIFY_INGEST] rx hold task skipped for %s: %s", order_id, exc
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
    fulfilled_stores: List[str] = []
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
            expected = sum(int(it.get("quantity") or 1) for it in decrement_items)
            # Owner 2026-07-05: multi-store fulfillment. Prefer the configured
            # fulfillment store, then claim any shortfall from whichever other
            # store actually holds the units (see _claim_units_multistore).
            claimed, breakdown = _claim_units_multistore(
                db, order_id, decrement_items, fulfillment_store
            )
            fulfilled_stores.extend(sorted({str(r["store_id"]) for r in breakdown}))
            if breakdown:
                try:
                    coll = (
                        db.get_collection("orders")
                        if hasattr(db, "get_collection")
                        else db["orders"]
                    )
                    coll.update_one(
                        {"order_id": order_id},
                        {
                            "$set": {
                                "fulfillment_breakdown": breakdown,
                                "fulfillment_stores": fulfilled_stores,
                            }
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[SHOPIFY_INGEST] breakdown persist skipped for %s: %s",
                        order_id,
                        exc,
                    )
                _raise_fallback_ship_tasks(
                    db,
                    order_id,
                    order_doc.get("order_number") or shopify_order_id,
                    breakdown,
                    fulfillment_store,
                )
            # FAIL LOUD on an under-claim: we booked a paid online order but could
            # NOT decrement every serialized unit (no store in the chain had
            # enough physical on-hand). The invoice stands (Shopify took payment)
            # but this is an oversell that needs operator action -- record it
            # loudly so the sync-health tile + Sentry surface it instead of it
            # slipping by as a warning.
            if claimed < expected:
                _record_stock_miss(
                    db,
                    order_id,
                    fulfillment_store,
                    "under_claim",
                    {
                        "expected": expected,
                        "claimed": claimed,
                        "stores_tried": fulfilled_stores or [fulfillment_store],
                    },
                )
    except Exception as exc:  # noqa: BLE001
        _record_stock_miss(db, order_id, fulfillment_store, "exception", str(exc))
    try:
        from .online_stock_writeback import writeback_after_sale

        # ONE write-back for the whole order: the pushed quantity is the POOLED
        # all-store on-hand (store_id is context only), so per-store pushes
        # would be identical writes racing each other. The preferred
        # fulfillment store rides along purely as logging context.
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
        "rx_pending": order_doc["rx_pending"],
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
