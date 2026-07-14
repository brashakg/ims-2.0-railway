"""
IMS 2.0 - Shopify REFUND -> GST credit note + restock  (BVI-retirement phase 0)
================================================================================
THE GAP this closes: IMS ingests Shopify ORDERS (shopify_ingest / online_order_
mapper) and mints the GST tax invoice, but NOTHING has ever handled a Shopify
`refunds/create` webhook. BVI handled refunds today; when BVI is retired, a
Shopify refund would silently produce NO GST output-tax reversal (a compliance
gap) and NO stock restock (an oversell / lost-inventory gap). This module fills
that in, REUSING the in-store return machinery rather than reinventing it.

WHAT A SHOPIFY REFUND PRODUCES IN IMS (mirrors an in-store return exactly):
  (a) a GST CREDIT NOTE against the original online order's invoice -- an output
      tax REVERSAL. We do NOT invent GST math: the gross the customer paid for a
      refunded unit is recovered from the ORIGINAL IMS order line via
      returns.py `_priced_return_lines` (the same (taxable_value+tax_amount)/qty
      resolution the till uses), and the tax is backed OUT of that gross with
      returns_engine.gst_breakup_lines PER LINE (exact for mixed GST rates) --
      the identical credit-note reversal an in-store CREDIT_NOTE runs. When a
      customer is resolved, the credit note is recorded to `credit_note_ledger`
      via returns.py `_issue_store_credit` (the SAME ledger the GSTR-1 CDNR
      report reads) UNDER THE ORIGINAL ORDER'S STORE, stamped with the real
      taxable/tax split, so the reversal flows into GST reporting under the right
      GSTIN exactly like a counter credit note.
  (b) a STOCK RESTOCK of the refunded serialized units back to the FULFILLING
      store, reusing returns.py `_restock_good_items` (re-activate the original
      SOLD unit, else mint a fresh AVAILABLE one) -- the same restock path a
      counter return runs. Shopify's per-line restock_type is honoured.

AMOUNT RECONCILIATION: the credit note is computed from the ORIGINAL billed
gross, but Shopify may have refunded a DIFFERENT amount (a partial / goodwill
refund). We read the amount Shopify actually refunded from the payload and, when
it differs from the computed gross beyond a small epsilon, force the review-queue
row to DISCREPANCY and NEVER auto-post -- an accountant reconciles.

NO DOUBLE BENEFIT ON CARD REFUNDS: when the payload shows the money already went
back via a payment gateway, the credit note is booked WITHOUT bumping the
customer's redeemable store credit (the CDNR ledger row is still written for the
GST reversal). Only a genuine store-credit settlement bumps the balance.

IDEMPOTENT on the Shopify refund id (CLAIM-FIRST): the AUTO path INSERTS the
`returns` doc stamped with the refund id (backed by a unique partial index)
BEFORE issuing credit / restock, so a redelivery mid-flight hits the unique index
(-> duplicate) rather than double-crediting. A prior review-queue row also blocks
a replay -- EXCEPT an UNMATCHED row (order arrived after the refund), which stays
reprocessable once the order is ingested.

REFUND POLICY (safe for a LIVE business):
  * DEFAULT = ACCOUNTANT REVIEW QUEUE. We do NOT auto-post financial entries. The
    computed credit note + proposed restock are written to `shopify_refund_review`
    as a PENDING item for an accountant to CONFIRM (see the consumer router
    routers/online_store_refund_reviews.py). Nothing hits the ledger or stock.
  * AUTO (opt-in, DARK by default) = auto-credit-note + auto-restock. Gated behind
    `SHOPIFY_REFUND_AUTO` (env) OR the shopify integration config flag
    `refund_auto`.
  BOTH code paths are built; the default is the queue. See `_refund_auto_enabled`.

FAIL-SOFT, end to end: a bad/partial payload, an unresolved order, or a DB error
yields a logged, structured SKIP/QUEUE result and NEVER raises (the NEXUS drain
loop must keep ticking).

PUBLIC API:
    handle_shopify_refund(db, payload, *, webhook_id=None, topic=None) -> dict
    post_from_review(db, review) -> dict   (used by the accountant consumer route)
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REVIEW_COLLECTION = "shopify_refund_review"
_RETURNS_COLLECTION = "returns"

# Rupee tolerance when reconciling the computed credit-note gross against what
# Shopify actually refunded. Absorbs GST rounding dust (a paisa or two on a
# multi-line refund) while still catching a real mismatch (a Rs 200 goodwill
# refund vs a Rs 7,000 computed credit note).
_AMOUNT_EPS = 1.0

# Shopify per-line restock intent -> should this returned unit go back on the
# shelf. "no_restock" is an explicit hold; everything else (return / cancel /
# legacy_restock) restocks. Absent -> defer to the refund-level `restock` flag.
_RESTOCK_TYPES_ON = {"return", "cancel", "legacy_restock"}

# Gateways that mean the money did NOT go back to an external card/UPI/wallet
# (so a store-credit bump is legitimate). Anything else = money already returned.
_NON_EXTERNAL_GATEWAYS = {"manual", "store_credit", "store-credit", "gift_card", "gift-card"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_dup_key(exc: Exception) -> bool:
    """True when an insert failed on a unique index (pymongo DuplicateKeyError or
    the in-memory test emulator's equivalent). Portable across both."""
    name = type(exc).__name__
    if name == "DuplicateKeyError":
        return True
    msg = str(exc).lower()
    return "e11000" in msg or "duplicate key" in msg


def _ensure_unique_refund_index(db, collection: str) -> None:
    """Lazily create a UNIQUE PARTIAL index on `<collection>.shopify_refund_id`
    (only over docs that carry a string refund id -- legacy / in-store rows with
    no refund id are unaffected). Built the same idempotent way webhooks.py builds
    uniq_webhook_event_id. Fail-soft: never raises."""
    if db is None:
        return
    try:
        coll = db.get_collection(collection)
        if coll is None:
            return
        coll.create_index(
            "shopify_refund_id",
            unique=True,
            partialFilterExpression={"shopify_refund_id": {"$type": "string"}},
            name="uniq_shopify_refund_id",
        )
    except Exception:  # noqa: BLE001 -- index build must never break the drain
        logger.debug("[SHOPIFY_REFUND] index build skipped for %s", collection, exc_info=True)


def _refund_auto_enabled(db) -> bool:
    """Full auto-credit-note + auto-restock posture. DEFAULT OFF (safe for a live
    business -- do NOT auto-post financial entries). Turned ON only by an explicit
    opt-in: the `SHOPIFY_REFUND_AUTO` env flag, OR the shopify integration config
    flag `refund_auto`. Anything else -> False (route to the accountant queue).
    Fail-soft: any read error -> False (the safe default)."""
    raw = (os.getenv("SHOPIFY_REFUND_AUTO") or "").strip().lower()
    if raw in ("1", "on", "true", "yes"):
        return True
    if raw in ("0", "off", "false", "no"):
        return False
    # Env unset -> consult the shopify integration config flag.
    if db is not None:
        try:
            integ = db.get_collection("integrations")
            if integ is not None:
                doc = integ.find_one({"type": "shopify"})
                cfg = (doc or {}).get("config") or {}
                val = cfg.get("refund_auto")
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    return val.strip().lower() in ("1", "on", "true", "yes")
        except Exception:  # noqa: BLE001 -- config read must never raise
            logger.debug("[SHOPIFY_REFUND] refund_auto config read failed", exc_info=True)
    return False


def _find_ims_order(db, shopify_order_id: str) -> Optional[Dict[str, Any]]:
    """The canonical IMS order minted for this Shopify order (shopify_ingest).
    Fail-soft -> None."""
    if db is None or not shopify_order_id:
        return None
    try:
        coll = db.get_collection("orders")
        if coll is None:
            return None
        return coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_REFUND] order lookup failed", exc_info=True)
        return None


def _refund_already_processed(db, refund_id: str) -> bool:
    """Idempotency guard on the Shopify refund id -- a re-delivered webhook must
    not double-credit or double-restock. True when a prior `returns` credit-note
    doc OR a prior review-queue row (ANY status EXCEPT UNMATCHED) already carries
    this refund id.

    An UNMATCHED review row is DELIBERATELY not treated as processed: it means
    the refund arrived before its order was ingested, so once the order exists a
    redelivery (or a re-drive) must be able to reprocess it. Fail-soft -> False
    (the caller then proceeds; a stray double is preferable to dropping a real
    refund)."""
    if db is None or not refund_id:
        return False
    try:
        returns_coll = db.get_collection(_RETURNS_COLLECTION)
        if returns_coll is not None and returns_coll.find_one(
            {"shopify_refund_id": refund_id}
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        review_coll = db.get_collection(_REVIEW_COLLECTION)
        if review_coll is not None:
            row = review_coll.find_one({"shopify_refund_id": refund_id})
            if row is not None and _norm(row.get("status")).upper() != "UNMATCHED":
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _supersede_unmatched(db, refund_id: str) -> None:
    """Delete any stale UNMATCHED review row for this refund id once its order is
    ingested and we are about to (re)process it, so a resolved refund never leaves
    a dangling UNMATCHED row alongside the new PENDING / posted one. Fail-soft."""
    if db is None or not refund_id:
        return
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        if coll is None:
            return
        if hasattr(coll, "delete_many"):
            coll.delete_many({"shopify_refund_id": refund_id, "status": "UNMATCHED"})
        elif hasattr(coll, "delete_one"):
            coll.delete_one({"shopify_refund_id": refund_id, "status": "UNMATCHED"})
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_REFUND] supersede UNMATCHED failed", exc_info=True)


def _restock_store_for_order(order: Dict[str, Any]) -> Optional[str]:
    """The PHYSICAL store the refunded units go back to. shopify_ingest claimed
    the sold units at the fulfilment store(s) and stamped `fulfillment_stores` /
    `fulfillment_breakdown`; the online billing `store_id` is a virtual bucket
    with no serialized stock. Prefer the first fulfilment store, else the order
    store."""
    stores = order.get("fulfillment_stores")
    if isinstance(stores, list) and stores:
        return _norm(stores[0]) or None
    breakdown = order.get("fulfillment_breakdown")
    if isinstance(breakdown, list):
        for row in breakdown:
            if isinstance(row, dict) and _norm(row.get("store_id")):
                return _norm(row.get("store_id"))
    return _norm(order.get("store_id")) or None


def _line_restock_flag(refund_line: Dict[str, Any], refund_level_default: bool) -> bool:
    """Honour Shopify's per-line restock intent. `restock_type` "no_restock" is an
    explicit hold; return/cancel/legacy_restock restock; absent -> the refund's
    top-level `restock` flag (default True)."""
    rt = _norm(refund_line.get("restock_type")).lower()
    if rt == "no_restock":
        return False
    if rt in _RESTOCK_TYPES_ON:
        return True
    return refund_level_default


def _match_ims_item(
    refund_line: Dict[str, Any], order_items: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Resolve the ORIGINAL IMS order line a Shopify refund line refers to.

    Match order (most-specific first): the Shopify line-item id
    (shopify_line_item_id), then the Shopify variant id (shopify_variant_id),
    then the sku. Returns the matched IMS order item dict, or None."""
    li = refund_line.get("line_item") if isinstance(refund_line.get("line_item"), dict) else {}
    shop_line_id = _norm(refund_line.get("line_item_id")) or _norm(li.get("id"))
    variant_id = _norm(li.get("variant_id"))
    sku = _norm(li.get("sku"))

    if shop_line_id:
        for it in order_items:
            if _norm(it.get("shopify_line_item_id")) == shop_line_id:
                return it
    if variant_id:
        for it in order_items:
            if _norm(it.get("shopify_variant_id")) == variant_id:
                return it
    if sku:
        for it in order_items:
            if _norm(it.get("sku")) == sku:
                return it
    return None


def _build_return_lines(
    payload: Dict[str, Any], order: Dict[str, Any]
) -> List[Any]:
    """Map Shopify refund_line_items -> IMS return lines (pydantic ReturnLine),
    each matched to its original IMS order line so the SHARED return machinery
    resolves the billed gross + GST rate + restock decision. Lines that can't be
    matched to an order line are skipped (logged). Never raises."""
    from ..routers.returns import ReturnLine

    order_items = [i for i in (order.get("items") or []) if isinstance(i, dict)]
    refund_level_restock = bool(payload.get("restock", True))
    lines: List[Any] = []
    for rl in payload.get("refund_line_items") or []:
        if not isinstance(rl, dict):
            continue
        qty = _f(rl.get("quantity"))
        if qty <= 0:
            continue
        ims_item = _match_ims_item(rl, order_items)
        if ims_item is None:
            logger.info(
                "[SHOPIFY_REFUND] refund line did not match any IMS order line "
                "(order=%s) -- skipped", order.get("order_id")
            )
            continue
        # order_item_id -> the IMS line's item_id so _priced_return_lines recovers
        # the billed gross + rate BY ITEM (never by product_id). product_id -> the
        # IMS product id so the restock claims the right serialized units (the
        # Shopify product_id is not the IMS one).
        ims_product_id = _norm(ims_item.get("ims_product_id")) or _norm(
            ims_item.get("product_id")
        )
        lines.append(
            ReturnLine(
                order_item_id=_norm(ims_item.get("item_id")) or None,
                product_id=ims_product_id or None,
                product_name=_norm(ims_item.get("product_name")),
                sku=_norm(ims_item.get("sku")),
                return_qty=qty,
                # Placeholder: _priced_return_lines OVERRIDES this with the GST-
                # inclusive gross recovered from the ORIGINAL IMS order line (the
                # tax reversal), so the till-supplied price is never trusted here.
                unit_price=0.0,
                condition="GOOD",
                restock=_line_restock_flag(rl, refund_level_restock),
                reason="Shopify refund",
            )
        )
    return lines


def _return_lines_from_proposed(proposed: List[Dict[str, Any]]) -> List[Any]:
    """Rebuild ReturnLine objects from a review row's stored `proposed_restock`
    (a list of ReturnLine model_dumps), so the accountant CONFIRM path posts the
    exact restock the webhook proposed. Never raises -- a bad row is skipped."""
    from ..routers.returns import ReturnLine

    out: List[Any] = []
    fields = set(getattr(ReturnLine, "model_fields", {}).keys())
    for d in proposed or []:
        if not isinstance(d, dict):
            continue
        try:
            out.append(ReturnLine(**{k: v for k, v in d.items() if k in fields}))
        except Exception:  # noqa: BLE001
            logger.debug("[SHOPIFY_REFUND] could not rebuild ReturnLine", exc_info=True)
    return out


def _shopify_refunded_amount(payload: Dict[str, Any]) -> Optional[float]:
    """The amount Shopify ACTUALLY refunded, read from the payload:
      1. sum of `transactions` with kind=='refund' and status=='success';
      2. fallback: sum of `refund_line_items` (subtotal + total_tax).
    Returns None when neither is derivable (the caller then cannot reconcile and
    does not force a discrepancy on missing data)."""
    if not isinstance(payload, dict):
        return None
    txns = payload.get("transactions")
    if isinstance(txns, list) and txns:
        total = 0.0
        found = False
        for t in txns:
            if not isinstance(t, dict):
                continue
            if _norm(t.get("kind")).lower() != "refund":
                continue
            status = _norm(t.get("status")).lower()
            if status and status != "success":
                continue
            total += _f(t.get("amount"))
            found = True
        if found:
            return round(total, 2)
    rlis = payload.get("refund_line_items")
    if isinstance(rlis, list) and rlis:
        total = 0.0
        found = False
        for rli in rlis:
            if not isinstance(rli, dict):
                continue
            total += _f(rli.get("subtotal")) + _f(rli.get("total_tax"))
            found = True
        if found:
            return round(total, 2)
    return None


def _is_gateway_refund(payload: Dict[str, Any]) -> bool:
    """True when the payload shows the money already went back via a payment
    GATEWAY (card / UPI / wallet), so IMS must NOT also mint redeemable store
    credit (double benefit). A `manual` / `store_credit` / `gift_card` gateway is
    an internal settlement, not external money -> False."""
    if not isinstance(payload, dict):
        return False
    txns = payload.get("transactions")
    if not isinstance(txns, list):
        return False
    for t in txns:
        if not isinstance(t, dict):
            continue
        if _norm(t.get("kind")).lower() != "refund":
            continue
        status = _norm(t.get("status")).lower()
        if status and status != "success":
            continue
        gateway = _norm(t.get("gateway")).lower()
        if gateway and gateway not in _NON_EXTERNAL_GATEWAYS:
            return True
    return False


def _system_user(store_id: Optional[str]) -> Dict[str, Any]:
    """A synthetic actor for the reused return helpers (they read user_id +
    active_store_id off a current_user dict)."""
    return {
        "user_id": "SYSTEM_SHOPIFY_REFUND",
        "username": "shopify-refund",
        "full_name": "Shopify Refund (system)",
        "active_store_id": store_id,
    }


def handle_shopify_refund(
    db,
    payload: Dict[str, Any],
    *,
    webhook_id: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Turn a verified Shopify `refunds/create` webhook into a GST credit note +
    stock restock (AUTO), or an accountant review-queue item (DEFAULT).

    Idempotent on the Shopify refund id. NEVER raises (the NEXUS drain loop
    relies on this). Returns a structured result dict:
      {"status": "queued"|"credited"|"credit_failed"|"duplicate"|"simulated"|
                 "skipped"|"order_not_found", "refund_id": <str>, ...}
    """
    try:
        payload = payload if isinstance(payload, dict) else {}
        refund_id = _norm(payload.get("id"))
        shopify_order_id = _norm(payload.get("order_id"))
        if not refund_id:
            return {"status": "skipped", "reason": "no_refund_id"}

        # No DB -> SIMULATE (compute nothing, persist nothing). Keeps the contract
        # identical whether or not Mongo is reachable.
        if db is None:
            return {"status": "simulated", "refund_id": refund_id}

        # Idempotency: a re-delivered refund webhook must not double-credit /
        # double-restock (UNMATCHED rows stay reprocessable -- see the guard).
        if _refund_already_processed(db, refund_id):
            return {
                "status": "duplicate",
                "refund_id": refund_id,
                "shopify_order_id": shopify_order_id,
            }

        order = _find_ims_order(db, shopify_order_id)
        if not order:
            # Fail-soft: never crash on an unmatched refund. Record it in the
            # review queue as UNMATCHED so a live business never silently loses a
            # refund (an accountant investigates), then return. The row stays
            # reprocessable once the order is ingested.
            _queue_review(
                db,
                refund_id=refund_id,
                shopify_order_id=shopify_order_id,
                order=None,
                credit_note=None,
                restock_lines=[],
                restock_store=None,
                status="UNMATCHED",
                note="No IMS order found for this Shopify order id.",
            )
            logger.warning(
                "[SHOPIFY_REFUND] no IMS order for shopify_order_id=%s refund=%s "
                "-- queued UNMATCHED",
                shopify_order_id,
                refund_id,
            )
            return {
                "status": "order_not_found",
                "refund_id": refund_id,
                "shopify_order_id": shopify_order_id,
            }

        # HISTORICAL import guard (mirrors online_order_mapper): a pre-IMS order
        # imported for customer-360 history was settled OUTSIDE IMS books; never
        # book a credit note / restock against it.
        if order.get("historical") or order.get("source") == "bvi_import":
            logger.info(
                "[SHOPIFY_REFUND] skip refund for HISTORICAL import order=%s",
                order.get("order_id"),
            )
            return {
                "status": "skipped",
                "reason": "historical_import_order",
                "refund_id": refund_id,
            }

        return_lines = _build_return_lines(payload, order)
        if not return_lines:
            return {
                "status": "skipped",
                "reason": "no_mappable_refund_lines",
                "refund_id": refund_id,
                "shopify_order_id": shopify_order_id,
            }

        # --- GST credit note math: REUSE the in-store return machinery ----------
        # _priced_return_lines recovers the GST-INCLUSIVE gross the customer paid
        # for each refunded unit from the ORIGINAL IMS order line; returned_value
        # sums it; gst_breakup_lines backs the tax OUT of EACH line at ITS OWN
        # rate (exact for mixed GST rates -- the dominant-rate shortcut mis-taxed a
        # mixed refund by hundreds of rupees). No new GST math is introduced here.
        from ..routers.returns import _priced_return_lines
        from . import returns_engine as engine

        priced = _priced_return_lines(return_lines, order)
        gross_refund = engine.returned_value(priced)
        gst_view = engine.gst_breakup_lines(priced)
        restock_store = _restock_store_for_order(order)

        # AMOUNT RECONCILIATION: what Shopify actually refunded may differ from the
        # billed gross (a partial / goodwill refund). Never auto-post a mismatch.
        shopify_refunded = _shopify_refunded_amount(payload)
        discrepancy = (
            shopify_refunded is not None
            and gross_refund > 0
            and abs(shopify_refunded - gross_refund) > _AMOUNT_EPS
        )

        credit_note = {
            "gross_refund": gross_refund,
            "net_refund": gross_refund,  # no online restocking fee
            "gst_breakup": gst_view,
            "shopify_refunded_amount": shopify_refunded,
            "lines": priced,
        }

        # We are about to (re)process this refund now that its order exists ->
        # supersede any stale UNMATCHED review row for it.
        _supersede_unmatched(db, refund_id)

        if discrepancy:
            # Never auto-post a mismatched amount; route to the accountant to
            # reconcile (applies in BOTH queue and auto postures).
            return _queue_review(
                db,
                refund_id=refund_id,
                shopify_order_id=shopify_order_id,
                order=order,
                credit_note=credit_note,
                restock_lines=return_lines,
                restock_store=restock_store,
                status="DISCREPANCY",
                note=(
                    f"Shopify refunded Rs {shopify_refunded} but the computed "
                    f"credit note is Rs {gross_refund} -- accountant must reconcile."
                ),
            )

        if not _refund_auto_enabled(db):
            # DEFAULT: accountant review queue. NO ledger, NO stock movement.
            return _queue_review(
                db,
                refund_id=refund_id,
                shopify_order_id=shopify_order_id,
                order=order,
                credit_note=credit_note,
                restock_lines=return_lines,
                restock_store=restock_store,
                status="PENDING",
                note="Awaiting accountant confirmation (SHOPIFY_REFUND_AUTO off).",
            )

        # AUTO: post the credit note + restock automatically (opt-in only).
        settled_externally = _is_gateway_refund(payload)
        result = _post_credit_and_restock(
            db,
            refund_id=refund_id,
            order=order,
            return_lines=return_lines,
            credit_note=credit_note,
            restock_store=restock_store,
            settled_externally=settled_externally,
        )

        if result.get("status") == "credit_failed":
            # A credit note SHOULD have issued but didn't (guest / no customer /
            # ledger failure). Do NOT leave it silently COMPLETED -- give the
            # accountant a surface (the returns doc already keeps the refund id).
            queue_status = "NO_CUSTOMER" if not result.get("customer_id") else "CREDIT_FAILED"
            _queue_review(
                db,
                refund_id=refund_id,
                shopify_order_id=shopify_order_id,
                order=order,
                credit_note=credit_note,
                restock_lines=return_lines,
                restock_store=restock_store,
                status=queue_status,
                note=(
                    "AUTO restock done but the store credit could not be issued "
                    "(no customer on the order or a ledger error) -- an accountant "
                    "must issue the credit note manually."
                ),
            )
            result["queue_status"] = queue_status
        result.setdefault("shopify_order_id", _norm(order.get("shopify_order_id")))
        return result
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning("[SHOPIFY_REFUND] handle_shopify_refund failed soft: %s", exc)
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}


def _queue_review(
    db,
    *,
    refund_id: str,
    shopify_order_id: str,
    order: Optional[Dict[str, Any]],
    credit_note: Optional[Dict[str, Any]],
    restock_lines: List[Any],
    restock_store: Optional[str],
    status: str,
    note: str,
) -> Dict[str, Any]:
    """Persist the proposed credit note + restock to `shopify_refund_review` for
    an accountant to confirm (routers/online_store_refund_reviews.py). NO
    financial entry, NO stock movement. Unique on the refund id (a duplicate
    insert is a no-op). Fail-soft."""
    _ensure_unique_refund_index(db, _REVIEW_COLLECTION)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "review_id": str(uuid.uuid4()),
        "shopify_refund_id": refund_id,
        "shopify_order_id": shopify_order_id,
        "order_id": (order or {}).get("order_id"),
        "order_number": (order or {}).get("order_number"),
        "invoice_number": (order or {}).get("invoice_number"),
        "customer_id": (order or {}).get("customer_id"),
        "customer_name": (order or {}).get("customer_name"),
        "store_id": (order or {}).get("store_id"),
        "restock_store_id": restock_store,
        "credit_note": credit_note,
        "gross_refund": (credit_note or {}).get("gross_refund"),
        "shopify_refunded_amount": (credit_note or {}).get("shopify_refunded_amount"),
        "proposed_restock": [
            r.model_dump() if hasattr(r, "model_dump") else r for r in restock_lines
        ],
        "status": status,
        "note": note,
        "resolved": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        if coll is not None:
            coll.insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
        if _is_dup_key(exc):
            logger.info(
                "[SHOPIFY_REFUND] review row for refund=%s already exists -- no-op",
                refund_id,
            )
        else:
            logger.warning("[SHOPIFY_REFUND] review-queue write failed: %s", exc)
    logger.info(
        "[SHOPIFY_REFUND] refund=%s order=%s queued for accountant review "
        "(status=%s, gross=%s)",
        refund_id,
        (order or {}).get("order_id"),
        status,
        (credit_note or {}).get("gross_refund"),
    )
    return {
        "status": "queued",
        "queue_status": status,
        "refund_id": refund_id,
        "shopify_order_id": shopify_order_id,
        "order_id": (order or {}).get("order_id"),
        "gross_refund": (credit_note or {}).get("gross_refund"),
        "gst_breakup": (credit_note or {}).get("gst_breakup"),
        "shopify_refunded_amount": (credit_note or {}).get("shopify_refunded_amount"),
        "restock_store_id": restock_store,
    }


def _post_credit_and_restock(
    db,
    *,
    refund_id: str,
    order: Dict[str, Any],
    return_lines: List[Any],
    credit_note: Dict[str, Any],
    restock_store: Optional[str],
    settled_externally: bool = False,
) -> Dict[str, Any]:
    """Post the GST credit note to `credit_note_ledger` (via the SAME returns.py
    `_issue_store_credit` an in-store CREDIT_NOTE uses, so the output-tax reversal
    flows into the GSTR-1 CDNR report) + restock the refunded units (via the SAME
    returns.py `_restock_good_items`).

    CLAIM-FIRST idempotency: the `returns` doc (stamped with the refund id) is
    INSERTED as PENDING -- backed by a unique partial index -- BEFORE any credit /
    restock. A redelivery mid-flight then hits the unique index (DuplicateKeyError
    -> "duplicate") instead of double-posting. The doc is updated to COMPLETED (or
    CREDIT_FAILED) once the credit + restock finish.

    GST: the credit note is booked UNDER THE ORIGINAL ORDER'S STORE (the online
    billing store / GSTIN, not the physical restock store) and stamped with the
    real taxable/tax split. CARD refunds (settled_externally=True) write the CDNR
    ledger row WITHOUT bumping the customer's redeemable store credit.

    Shared by the AUTO webhook path and the accountant CONFIRM route. Fully
    fail-soft. Returns {"status": "credited"|"credit_failed"|"duplicate", ...}."""
    order_id = _norm(order.get("order_id"))
    customer_id = _norm(order.get("customer_id")) or None
    billing_store = _norm(order.get("store_id")) or None
    gross_refund = _f(credit_note.get("gross_refund"))
    gst_view = credit_note.get("gst_breakup") or {}

    try:
        from ..routers.returns import generate_return_id

        return_id = generate_return_id()
    except Exception:  # noqa: BLE001
        return_id = f"RET-{refund_id}"

    # (0) CLAIM-FIRST: insert the PENDING returns doc stamped with the refund id
    #     BEFORE any side effect, so a concurrent / replayed delivery is blocked.
    _ensure_unique_refund_index(db, _RETURNS_COLLECTION)
    now = datetime.now(timezone.utc).isoformat()
    claim_doc = {
        "return_id": return_id,
        "shopify_refund_id": refund_id,
        "order_id": order_id,
        "order_number": order.get("order_number"),
        "customer_id": customer_id,
        "customer_name": order.get("customer_name"),
        "store_id": billing_store,
        "restock_store_id": restock_store,
        "return_type": "CREDIT_NOTE",
        "source": "shopify",
        "channel": "ONLINE",
        "items": credit_note.get("lines", []),
        "returned_value": gross_refund,
        "gross_refund": gross_refund,
        "restocking_fee": 0.0,
        "net_refund": gross_refund,
        "gst_breakup": gst_view,
        "shopify_refunded_amount": credit_note.get("shopify_refunded_amount"),
        "settled_externally": bool(settled_externally),
        "status": "PENDING",
        "reason_summary": "Shopify refund",
        "created_by": "SYSTEM_SHOPIFY_REFUND",
        "created_at": now,
    }
    returns_coll = None
    claimed = False
    try:
        returns_coll = db.get_collection(_RETURNS_COLLECTION)
    except Exception:  # noqa: BLE001
        returns_coll = None
    if returns_coll is not None:
        try:
            returns_coll.insert_one(dict(claim_doc))
            claimed = True
        except Exception as exc:  # noqa: BLE001
            if _is_dup_key(exc):
                logger.info(
                    "[SHOPIFY_REFUND] refund=%s already claimed -- duplicate", refund_id
                )
                return {
                    "status": "duplicate",
                    "refund_id": refund_id,
                    "order_id": order_id,
                }
            logger.warning("[SHOPIFY_REFUND] claim insert failed: %s", exc)

    # (a) GST credit note -> credit_note_ledger (the CDNR source). Booked under the
    #     BILLING store with the real GST split; card refunds skip the balance bump.
    credit_entry = None
    try:
        from ..routers.returns import _issue_store_credit

        if customer_id and gross_refund > 0:
            credit_entry = _issue_store_credit(
                customer_id,
                gross_refund,
                reason=f"Shopify refund {refund_id} for order {order_id}",
                ref=return_id,
                current_user=_system_user(billing_store),
                gross=gross_refund,
                restocking_fee=0.0,
                taxable=gst_view.get("taxable"),
                tax=gst_view.get("tax"),
                gst_rate=gst_view.get("gst_rate"),
                bump_balance=not settled_externally,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] credit note post failed: %s", exc)

    # (b) Restock the refunded serialized units back to the fulfilling store.
    restock_result: Dict[str, Any] = {
        "restocked": [],
        "restock_stock_ids": [],
        "applied": False,
        "skipped": [],
    }
    try:
        from ..routers.returns import _restock_good_items

        restock_result = _restock_good_items(
            return_lines,
            restock_store,
            return_id or refund_id,
            order_id=order_id,
            user_id="SYSTEM_SHOPIFY_REFUND",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] restock failed (recorded, not applied): %s", exc)

    # Finalize: a credit note that SHOULD have issued (gross>0) but didn't must NOT
    # be marked COMPLETED (finding #6) -> CREDIT_FAILED, so the accountant gets a
    # surface while the refund id stays consumed (idempotency preserved).
    credit_ok = bool(credit_entry)
    result_status = "credited"
    final_status = "COMPLETED"
    if gross_refund > 0 and not credit_ok:
        result_status = "credit_failed"
        final_status = "CREDIT_FAILED"

    restock_applied = bool(restock_result.get("applied"))
    update_fields = {
        "status": final_status,
        "credit_amount": gross_refund if credit_ok else None,
        "credit_entry": credit_entry,
        "credit_note_issued": credit_ok,
        "settled_externally": bool(settled_externally),
        "restocked": restock_result.get("restocked", []),
        "restock_applied": restock_applied,
        "restock_stock_ids": restock_result.get("restock_stock_ids", []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if returns_coll is not None and claimed:
        try:
            returns_coll.update_one(
                {"shopify_refund_id": refund_id}, {"$set": update_fields}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_REFUND] returns doc finalize failed: %s", exc)
    elif returns_coll is not None:
        # The claim insert did not land (a non-dup DB error) -> best-effort persist
        # so the credit/restock is still auditable (no idempotency protection).
        try:
            returns_coll.insert_one({**claim_doc, **update_fields})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_REFUND] returns doc persist failed: %s", exc)

    logger.info(
        "[SHOPIFY_REFUND] AUTO-posted refund=%s order=%s credit=%s tax=%s "
        "credit_issued=%s settled_externally=%s restock_applied=%s",
        refund_id,
        order_id,
        gross_refund,
        gst_view.get("tax"),
        credit_ok,
        settled_externally,
        restock_applied,
    )
    return {
        "status": result_status,
        "refund_id": refund_id,
        "shopify_order_id": _norm(order.get("shopify_order_id")),
        "order_id": order_id,
        "return_id": return_id,
        "customer_id": customer_id,
        "gross_refund": gross_refund,
        "gst_breakup": gst_view,
        "credit_note_issued": credit_ok,
        "settled_externally": bool(settled_externally),
        "restock_applied": restock_applied,
        "restock_store_id": restock_store,
    }


def post_from_review(db, review: Dict[str, Any]) -> Dict[str, Any]:
    """Post the credit note + restock from a STORED `shopify_refund_review` row
    (the accountant CONFIRM action). Rebuilds the order context + ReturnLine list
    from the row and calls the SAME `_post_credit_and_restock` the AUTO path uses,
    so a confirmed refund books identically. NEVER raises; returns the post
    result. The caller (the consumer router) stamps the review row status."""
    if not isinstance(review, dict):
        return {"status": "skipped", "reason": "bad_review"}
    refund_id = _norm(review.get("shopify_refund_id"))
    credit_note = review.get("credit_note") or {}
    if not refund_id or not credit_note:
        return {"status": "skipped", "reason": "no_credit_note", "refund_id": refund_id}

    order = {
        "order_id": review.get("order_id"),
        "order_number": review.get("order_number"),
        "customer_id": review.get("customer_id"),
        "customer_name": review.get("customer_name"),
        "store_id": review.get("store_id"),
        "shopify_order_id": review.get("shopify_order_id"),
    }
    return_lines = _return_lines_from_proposed(review.get("proposed_restock") or [])
    settled_externally = bool(
        review.get("settled_externally") or credit_note.get("settled_externally")
    )
    return _post_credit_and_restock(
        db,
        refund_id=refund_id,
        order=order,
        return_lines=return_lines,
        credit_note=credit_note,
        restock_store=review.get("restock_store_id"),
        settled_externally=settled_externally,
    )
