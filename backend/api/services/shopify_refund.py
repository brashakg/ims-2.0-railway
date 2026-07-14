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
      returns_engine.gst_breakup -- the identical credit-note reversal an
      in-store CREDIT_NOTE runs. When a customer is resolved, the credit note is
      recorded to `credit_note_ledger` via returns.py `_issue_store_credit` (the
      SAME ledger the GSTR-1 CDNR report reads), so the reversal flows into GST
      reporting exactly like a counter credit note.
  (b) a STOCK RESTOCK of the refunded serialized units back to the FULFILLING
      store, reusing returns.py `_restock_good_items` (re-activate the original
      SOLD unit, else mint a fresh AVAILABLE one) -- the same restock path a
      counter return runs. Shopify's per-line restock_type is honoured.

IDEMPOTENT on the Shopify refund id: a re-delivered `refunds/create` webhook
must never double-credit or double-restock. We guard on the refund id before any
write (a prior `returns` doc OR a prior review-queue row for this refund id ->
"duplicate", no-op).

REFUND POLICY (safe for a LIVE business):
  * DEFAULT = ACCOUNTANT REVIEW QUEUE. We do NOT auto-post financial entries. The
    computed credit note + proposed restock are written to `shopify_refund_review`
    as a PENDING item for an accountant to confirm. Nothing hits the ledger or
    stock.
  * AUTO (opt-in, DARK by default) = auto-credit-note + auto-restock. Gated behind
    `SHOPIFY_REFUND_AUTO` (env) OR the shopify integration config flag
    `refund_auto`. Only when explicitly turned ON does IMS post the credit note +
    restock automatically.
  BOTH code paths are built; the default is the queue. See `_refund_auto_enabled`.

FAIL-SOFT, end to end: a bad/partial payload, an unresolved order, or a DB error
yields a logged, structured SKIP/QUEUE result and NEVER raises (the NEXUS drain
loop must keep ticking).

PUBLIC API:
    handle_shopify_refund(db, payload, *, webhook_id=None, topic=None) -> dict
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REVIEW_COLLECTION = "shopify_refund_review"

# Shopify per-line restock intent -> should this returned unit go back on the
# shelf. "no_restock" is an explicit hold; everything else (return / cancel /
# legacy_restock) restocks. Absent -> defer to the refund-level `restock` flag.
_RESTOCK_TYPES_ON = {"return", "cancel", "legacy_restock"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
    doc OR a prior review-queue row already carries this refund id. Fail-soft ->
    False (the caller then proceeds; a stray double is preferable to dropping a
    real refund, and the single NEXUS drain makes a race unlikely)."""
    if db is None or not refund_id:
        return False
    try:
        returns_coll = db.get_collection("returns")
        if returns_coll is not None and returns_coll.find_one(
            {"shopify_refund_id": refund_id}
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        review_coll = db.get_collection(_REVIEW_COLLECTION)
        if review_coll is not None and review_coll.find_one(
            {"shopify_refund_id": refund_id}
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


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
      {"status": "queued"|"credited"|"duplicate"|"simulated"|"skipped"|
                 "order_not_found",
       "refund_id": <str>, "shopify_order_id": <str>, ...}
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
        # double-restock.
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
            # refund (an accountant investigates), then return.
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
        # sums it; gst_breakup backs the tax OUT of that gross (the reversal). No
        # new GST math is introduced here.
        from ..routers.returns import _priced_return_lines
        from . import returns_engine as engine

        priced = _priced_return_lines(return_lines, order)
        gross_refund = engine.returned_value(priced)
        gst_view = engine.gst_breakup(
            gross_refund, engine.dominant_gst_rate(priced)
        )
        restock_store = _restock_store_for_order(order)

        credit_note = {
            "gross_refund": gross_refund,
            "net_refund": gross_refund,  # no online restocking fee
            "gst_breakup": gst_view,
            "lines": priced,
        }

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
        return _auto_post(
            db,
            refund_id=refund_id,
            payload=payload,
            order=order,
            return_lines=return_lines,
            credit_note=credit_note,
            restock_store=restock_store,
        )
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
    an accountant to confirm. NO financial entry, NO stock movement. Idempotent
    on the refund id. Fail-soft."""
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "review_id": str(uuid.uuid4()),
        "shopify_refund_id": refund_id,
        "shopify_order_id": shopify_order_id,
        "order_id": (order or {}).get("order_id"),
        "order_number": (order or {}).get("order_number"),
        "invoice_number": (order or {}).get("invoice_number"),
        "customer_id": (order or {}).get("customer_id"),
        "store_id": (order or {}).get("store_id"),
        "restock_store_id": restock_store,
        "credit_note": credit_note,
        "proposed_restock": [
            r.model_dump() if hasattr(r, "model_dump") else r for r in restock_lines
        ],
        "status": status,
        "resolved": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        if coll is not None:
            coll.insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
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
        "restock_store_id": restock_store,
    }


def _auto_post(
    db,
    *,
    refund_id: str,
    payload: Dict[str, Any],
    order: Dict[str, Any],
    return_lines: List[Any],
    credit_note: Dict[str, Any],
    restock_store: Optional[str],
) -> Dict[str, Any]:
    """AUTO path (opt-in): post the GST credit note to `credit_note_ledger` (via
    the SAME returns.py `_issue_store_credit` an in-store CREDIT_NOTE uses, so the
    output-tax reversal flows into the GSTR-1 CDNR report) + restock the refunded
    units (via the SAME returns.py `_restock_good_items`). Persist a `returns`
    credit-note doc stamped with the Shopify refund id for idempotency + audit.
    Fully fail-soft.

    NOTE (owner/accountant): issuing store credit here mirrors how IMS records an
    in-store credit note. For an online card refund the money already went back to
    the card via Shopify, so store credit on top would over-credit the buyer --
    which is exactly why the DEFAULT is the accountant review queue and this AUTO
    path is DARK/opt-in.
    """
    order_id = _norm(order.get("order_id"))
    customer_id = _norm(order.get("customer_id")) or None
    gross_refund = _f(credit_note.get("gross_refund"))
    gst_view = credit_note.get("gst_breakup") or {}

    return_id = None
    credit_entry = None
    try:
        from ..routers.returns import generate_return_id, _issue_store_credit

        return_id = generate_return_id()
        # (a) GST credit note -> credit_note_ledger (the CDNR source).
        if customer_id and gross_refund > 0:
            credit_entry = _issue_store_credit(
                customer_id,
                gross_refund,
                reason=f"Shopify refund {refund_id} for order {order_id}",
                ref=return_id,
                current_user=_system_user(restock_store),
                gross=gross_refund,
                restocking_fee=0.0,
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

    # Persist the credit-note return doc (stamped with the Shopify refund id so a
    # replay is caught by _refund_already_processed).
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "return_id": return_id,
        "shopify_refund_id": refund_id,
        "order_id": order_id,
        "order_number": order.get("order_number"),
        "customer_id": customer_id,
        "customer_name": order.get("customer_name"),
        "store_id": order.get("store_id"),
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
        "credit_amount": gross_refund if credit_entry else None,
        "credit_entry": credit_entry,
        "restocked": restock_result.get("restocked", []),
        "restock_applied": bool(restock_result.get("applied")),
        "restock_stock_ids": restock_result.get("restock_stock_ids", []),
        "status": "COMPLETED",
        "reason_summary": "Shopify refund",
        "created_by": "SYSTEM_SHOPIFY_REFUND",
        "created_at": now,
    }
    try:
        coll = db.get_collection("returns")
        if coll is not None:
            coll.insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] returns doc persist failed: %s", exc)

    logger.info(
        "[SHOPIFY_REFUND] AUTO-posted refund=%s order=%s credit=%s tax=%s "
        "restock_applied=%s",
        refund_id,
        order_id,
        gross_refund,
        gst_view.get("tax"),
        doc["restock_applied"],
    )
    return {
        "status": "credited",
        "refund_id": refund_id,
        "shopify_order_id": _norm(order.get("shopify_order_id")),
        "order_id": order_id,
        "return_id": return_id,
        "gross_refund": gross_refund,
        "gst_breakup": gst_view,
        "credit_note_issued": bool(credit_entry),
        "restock_applied": doc["restock_applied"],
        "restock_store_id": restock_store,
    }
