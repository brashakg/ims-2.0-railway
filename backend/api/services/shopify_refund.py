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
from typing import Any, Callable, Dict, List, Optional, Tuple

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


def _claim_stale_refund_for_retry(returns_coll, refund_id: str, now: str):
    """The claim-first insert hit the unique index -- a `returns` doc ALREADY
    carries this refund id. Decide ATOMICALLY whether that is a genuine duplicate
    (a credit note was already issued -> the caller must NOT re-issue) or a STALE
    claim from an earlier attempt that never managed to issue the credit note (e.g.
    a guest / no-customer online order finalized CREDIT_FAILED) and is therefore
    safe to re-process.

    Atomicity: find_one_and_update with the filter
    {shopify_refund_id, credit_note_issued != True, reprocessing_at == null,
    status != PENDING} flips reprocessing_at for the FIRST caller only; a second
    concurrent retry then no longer matches (reprocessing_at is set, or
    credit_note_issued has since become True) and gets None -> it CANNOT also
    re-issue. Single-winner => no double credit note on concurrent redeliveries.
    The `status != PENDING` leg is what closes the concurrent FIRST-EVER race: a
    brand-new claim is inserted as PENDING and only leaves PENDING once finalized,
    so a racing first-delivery loser can never re-claim the winner's still-in-flight
    PENDING doc (it would only ever match a FINALIZED, never-credited row). The
    marker is cleared on finalize.

    Returns the existing doc when THIS caller won the right to re-process a
    never-credited row (the caller falls through to the credit-issue + finalize
    path, which update_one's this same doc); returns None for a TRUE duplicate -- a
    credit note really exists, OR another worker is mid-reprocess. Never raises."""
    fou = getattr(returns_coll, "find_one_and_update", None)
    if callable(fou):
        try:
            return fou(
                {
                    "shopify_refund_id": refund_id,
                    "credit_note_issued": {"$ne": True},
                    "reprocessing_at": None,
                    # Only re-claim a FINALIZED row -- never an in-flight brand-new
                    # PENDING claim (closes the concurrent first-ever double-issue).
                    "status": {"$ne": "PENDING"},
                },
                {"$set": {"reprocessing_at": now}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_REFUND] retry-claim find_one_and_update failed: %s", exc)
            return None
    # No find_one_and_update (mock / no-DB path): best-effort NON-atomic decision.
    # Still honest -- only re-process when the row was demonstrably never credited.
    try:
        existing = returns_coll.find_one({"shopify_refund_id": refund_id})
    except Exception:  # noqa: BLE001
        existing = None
    if (
        existing
        and existing.get("credit_note_issued") is not True
        and existing.get("status") != "PENDING"
    ):
        return existing
    return None


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


def _proposed_restock_store_for_order(order: Dict[str, Any]) -> Optional[str]:
    """A DISPLAY HINT for the accountant review row: roughly where the refunded
    units are expected to go back.

    NOT the routing decision. Routing is owned by ONE place -- the F9 guard in
    returns._restock_good_items -- which resolves the physical store per UNIT
    from the fulfilment breakdown. This function must never be used to pre-
    resolve a store and hand it to _restock_good_items: `fulfillment_stores` is
    a SORTED SET (shopify_ingest.py:1736), i.e. ALPHABETICAL, not the shop that
    shipped a given line, so pre-resolving would book every unit of a two-shop
    order to the alphabetically-first shop AND would short-circuit the guard's
    per-unit narrowing entirely (the guard only narrows when it is the one that
    redirected).

    Deliberately returns None rather than falling back to the order's own
    `store_id`: on an online order that is the stockless online bucket, and
    showing "restock into BV-ONLINE-01" on the review screen is a lie. Every
    candidate is filtered the same way, since a stamp written by a pre-fix
    claim could itself name an online store."""
    from .stores_util import is_online_store

    def _physical(value) -> Optional[str]:
        sid = _norm(value)
        if not sid or is_online_store(None, sid):
            return None
        return sid

    stores = order.get("fulfillment_stores")
    if isinstance(stores, list):
        for candidate in stores:
            hit = _physical(candidate)
            if hit:
                return hit
    breakdown = order.get("fulfillment_breakdown")
    if isinstance(breakdown, list):
        for row in breakdown:
            if isinstance(row, dict):
                hit = _physical(row.get("store_id"))
                if hit:
                    return hit
    return None


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


def _ims_cancel_door_ran(order: Dict[str, Any]) -> bool:
    """True when staff cancelled the order through the IMS cancel door
    (routers/orders/cancel.py): its claim stamps `cancelled_by`, its release
    stamps `cancel_stock_released` (the retry door on a Shopify-cancelled order
    stamps only the latter). That door already put every SOLD unit of the order
    back -- and owns the retry of any it could not -- so a refund restock now
    finds no SOLD unit and MINTS a phantom second one; and the refund money may
    already have been settled at the counter."""
    return bool(order.get("cancelled_by")) or "cancel_stock_released" in order


def _cap_restock_to_returnable(
    lines: List[Any], order: Dict[str, Any], refund_id: str
) -> Tuple[List[Any], bool, bool]:
    """Restock only a unit that is really out with the buyer: first the
    counter return door's own answer (_cap_restock_to_unreturned), then the
    order's own SOLD units (_cap_restock_to_sold_units). The webhook's
    proposal, the post (AUTO or the accountant's confirm) and Goods back all
    ask it. Returns (lines, overlapped, unknown)."""
    lines, overlapped = _cap_restock_to_unreturned(lines, order, refund_id)
    lines, unknown = _cap_restock_to_sold_units(lines, order)
    return lines, overlapped, unknown


def _split_restock(line: Any, keep: float) -> List[Any]:
    """A restock line capped at `keep` units: the part within the cap restocks,
    the rest is proposed with restock=False."""
    if keep >= line.return_qty:
        return [line]
    head = [line.model_copy(update={"return_qty": keep})] if keep > 0 else []
    return head + [line.model_copy(update={"return_qty": line.return_qty - keep, "restock": False})]


def _cap_restock_to_sold_units(
    lines: List[Any], order: Dict[str, Any]
) -> Tuple[List[Any], bool]:
    """Restock no more units of an IMS product (summed over the refund's
    lines) than the order still holds SOLD in stock. An oversold or
    under-claimed line (no unit was ever taken for it) has nothing to put
    back: the restock would find no SOLD unit and MINT a phantom. A HISTORICAL
    order (our own Shopify order-history import) never claimed stock rows, so
    it keeps the restock it proposes.

    An unreadable stock answer is NO answer, never 0: the line is left as it
    is and `unknown` comes back True. The proposal keeps its restock for the
    post to ask again; the post restocks nothing and leaves the restock OPEN
    (applied=False, a task, the /returns/{id}/restock retry). Counting it 0
    finalized "applied" with the unit still SOLD and no way back."""
    if order.get("historical"):
        return lines, False
    sold: Dict[str, Optional[float]] = {}
    out: List[Any] = []
    unknown = False
    for line in lines:
        if not line.restock:
            out.append(line)
            continue
        pid = line.product_id or ""
        if pid not in sold:
            sold[pid] = _sold_units(order.get("order_id"), pid)
        left = sold[pid]
        if left is None:
            unknown = True
            out.append(line)
            continue
        keep = max(0.0, min(line.return_qty, left))
        sold[pid] = left - keep
        out.extend(_split_restock(line, keep))
    return out, unknown


def _sold_units(order_id: Any, product_id: str) -> Optional[float]:
    """How many stock units of `product_id` this order still holds SOLD; None
    when the stock cannot be read. Read through the returns router's own
    repository accessor."""
    if not order_id or not product_id:
        return 0.0
    try:
        from ..routers import returns as _r

        repo = _r.get_stock_repository()
        if repo is None:
            return None
        rows = repo.find_many({"order_id": order_id, "product_id": product_id, "status": "SOLD"})
        return float(len(rows or []))
    except Exception:  # noqa: BLE001 -- no answer: the caller keeps the restock open
        logger.warning("[SHOPIFY_REFUND] SOLD-unit read failed for order=%s", order_id,
                       exc_info=True)
        return None


def _cap_restock_to_unreturned(
    lines: List[Any], order: Dict[str, Any], refund_id: str
) -> Tuple[List[Any], bool]:
    """Restock no more units of an order line than are still out with the
    buyer, by the counter return door's own answer: the line's purchased qty
    less returns._units_already_back (every OTHER return doc of the order and
    what this refund's own doc says its restock already put back -- or the
    line's returned_qty, which every restock of a Shopify refund books). A
    unit a counter return or
    another door already took back is on the shelf again -- restocking it
    finds no SOLD unit and MINTS a phantom (always, on a historical order,
    which has no SOLD unit to find). A line THIS refund already restocked
    (its mark on the line, _restock_booked) restocks nothing again. A restock
    line over the cap splits: the part still returnable restocks, the rest
    does not. Returns (lines, overlapped): overlapped = IMS already booked a
    return for some of these units, so the counter may already have refunded
    their money. Never raises -- an unreadable answer leaves the lines as they
    are."""
    try:
        from ..routers.returns import (
            _line_purchased_qty,
            _order_line_index,
            _resolve_original_line,
            _units_already_back,
        )

        idx = _order_line_index(order)
        left: Dict[str, float] = {}
        out: List[Any] = []
        overlapped = False
        for line in lines:
            orig = _resolve_original_line(line, idx)
            if orig is None:
                out.append(line)
                continue
            key = str(orig.get("item_id") or orig.get("id") or orig.get("product_id"))
            if key not in left:
                left[key] = _line_purchased_qty(orig) - _units_already_back(
                    order.get("order_id"), orig, own_shopify_refund_id=refund_id
                )
            keep = max(0.0, min(line.return_qty, left[key]))
            left[key] -= keep
            if refund_id and refund_id in (orig.get("restocked_refunds") or {}):
                out.extend(_split_restock(line, 0.0) if line.restock else [line])
                continue
            if keep >= line.return_qty:
                out.append(line)
                continue
            overlapped = True
            out.extend(_split_restock(line, keep) if line.restock else [line])
        return out, overlapped
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_REFUND] returnable-qty cap failed", exc_info=True)
        return lines, False


def _build_return_lines(
    payload: Dict[str, Any], order: Dict[str, Any]
) -> List[Any]:
    """Map Shopify refund_line_items -> IMS return lines (pydantic ReturnLine),
    each matched to its original IMS order line so the SHARED return machinery
    resolves the billed gross + GST rate + restock decision. Lines that can't be
    matched to an order line are skipped (logged). Never raises. An order the
    IMS cancel door already released restocks nothing (_ims_cancel_door_ran) --
    on the AUTO post AND on the accountant's confirm of the proposed restock."""
    from ..routers.returns import ReturnLine

    order_items = [i for i in (order.get("items") or []) if isinstance(i, dict)]
    refund_level_restock = bool(payload.get("restock", True))
    door_released = _ims_cancel_door_ran(order)
    # A Shopify "cancel" line is a quantity Shopify never fulfilled. On an
    # order a person delivered at the counter (the counter door stamps its
    # user; the courier legs stamp "system:"), the customer has it anyway
    # (owner ruling 2026-09-28): the line restocks nothing, and Goods back on
    # the review row restocks it if it comes back. After a courier delivery
    # of other units, it never left the shelf and restocks as Shopify says.
    by = str(order.get("status_updated_by") or "")
    handed_over = (str(order.get("status") or "").strip().upper() == "DELIVERED"
                   and bool(by) and not by.startswith("system:"))
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
                restock=(
                    _line_restock_flag(rl, refund_level_restock)
                    and not door_released
                    and not (handed_over and _norm(rl.get("restock_type")).lower() == "cancel")
                ),
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

        # HISTORICAL import guard: skip ONLY a pre-IMS customer-360 import
        # (scripts/migrate_bvi_pim.py orders leg; source=bvi_import, status
        # HISTORICAL) -- that order was settled OUTSIDE IMS books and carries NO
        # IMS revenue/output-GST, so a credit note would reverse tax that was never
        # output.
        #
        # Our OWN Shopify order-history import (import_source=shopify_order_history)
        # is DIFFERENT: it books real IMS revenue + output GST (status DELIVERED /
        # REFUNDED). A real refund webhook against one of those MUST still produce a
        # GST credit note. Every refund PRESENT in the order payload at import time
        # was already credited (a `returns` doc stamped with its refund id makes
        # _refund_already_processed above catch it as a duplicate); only a NEW, later
        # refund -- absent at import -- reaches here, and it should be booked (via
        # the accountant review queue by default), NOT permanently skipped.
        if order.get("source") == "bvi_import" or (
            order.get("historical")
            and order.get("import_source") != "shopify_order_history"
        ):
            logger.info(
                "[SHOPIFY_REFUND] skip refund for pre-IMS customer-360 import order=%s",
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
        # An unreadable SOLD answer keeps the proposal's restock: the post asks again.
        return_lines, counter_returned, _ = _cap_restock_to_returnable(
            return_lines, order, refund_id
        )

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
        # DISPLAY HINT ONLY -- the real routing is done by the F9 guard inside
        # _restock_good_items (see _proposed_restock_store_for_order).
        restock_store = _proposed_restock_store_for_order(order)

        # AMOUNT RECONCILIATION: what Shopify actually refunded may differ from the
        # billed gross (a partial / goodwill refund). Never auto-post a mismatch.
        shopify_refunded = _shopify_refunded_amount(payload)
        discrepancy = (
            shopify_refunded is not None
            and gross_refund > 0
            and abs(shopify_refunded - gross_refund) > _AMOUNT_EPS
        )

        # Whether the money already went back via a payment GATEWAY (card/UPI/
        # wallet). Computed HERE -- BEFORE the queue/discrepancy branches -- and
        # PERSISTED in the credit_note dict so the accountant-confirm path
        # (post_from_review reads credit_note.settled_externally) never mints
        # redeemable store credit ON TOP of a gateway refund. Previously only the
        # AUTO branch computed it, so a queued gateway refund confirmed by the
        # accountant granted the customer a double benefit.
        settled_externally = _is_gateway_refund(payload)

        credit_note = {
            "gross_refund": gross_refund,
            "net_refund": gross_refund,  # no online restocking fee
            "gst_breakup": gst_view,
            "shopify_refunded_amount": shopify_refunded,
            "settled_externally": settled_externally,
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

        door_cancelled = _ims_cancel_door_ran(order)
        if door_cancelled:
            note = (
                "Cancelled in IMS before this Shopify refund: its units are "
                "already back on the shelf (no restock) -- confirm the credit "
                "note only if the counter did not settle this money."
            )
        elif counter_returned:
            note = (
                "IMS already booked a return for some of these units (no second "
                "restock for them) -- confirm the credit note only if the counter "
                "did not already refund this money."
            )
        else:
            note = "Awaiting accountant confirmation (SHOPIFY_REFUND_AUTO off)."
        # Goods out with the courier or the customer: a person decides, even
        # under AUTO -- the units are not on any shelf to put back yet.
        goods_out = bool(
            str(order.get("status") or "").strip().upper() in ("SHIPPED", "DELIVERED")
            or order.get("awb")
            or order.get("shopify_fulfillment_id")
        )
        if goods_out and not (door_cancelled or counter_returned):
            note = (
                "Goods are with the courier or the customer: when they physically "
                "come back, press Goods back here (never a counter return -- "
                "Shopify already refunded this money)."
            )
        if door_cancelled or counter_returned or goods_out or not _refund_auto_enabled(db):
            # DEFAULT: accountant review queue. NO ledger, NO stock movement.
            # An order staff cancelled, or took a return of, in IMS is queued
            # even under AUTO: the counter may already have settled this money.
            return _queue_review(
                db,
                refund_id=refund_id,
                shopify_order_id=shopify_order_id,
                order=order,
                credit_note=credit_note,
                restock_lines=return_lines,
                restock_store=restock_store,
                status="PENDING",
                note=note,
            )

        # AUTO: post the credit note + restock automatically (opt-in only).
        # settled_externally was computed above (shared with the queue path).
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
    restock_unverified: bool = False,
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
        # PROPOSAL only. The authoritative `restock_store_id` is written by the
        # finalize $set below, from the guard's actual answer -- never from this
        # pre-guard hint (which used to be able to name the stockless online
        # store and was then never corrected).
        "proposed_restock_store_id": restock_store,
        "restock_store_id": None,
        "restock_store_ids": [],
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
    # The already-finalized doc when this call is a RE-drive of a never-credited
    # refund (e.g. a guest confirm that finished CREDIT_FAILED). Its restock may
    # already have happened -- re-running it would mint a SECOND set of units.
    prior_doc: Optional[Dict[str, Any]] = None
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
                # A returns doc already carries this refund id. Do NOT blindly call
                # it a duplicate: it may be a STALE claim from an earlier attempt
                # that never issued the credit note (e.g. a no-customer online
                # order that finalized CREDIT_FAILED). Atomically claim the retry --
                # only when the existing row was never credited AND no other worker
                # is mid-reprocess -- so a genuinely-posted refund is still a true
                # duplicate but an unposted one gets re-driven (not silently
                # swallowed while the GST reversal is lost).
                existing = _claim_stale_refund_for_retry(returns_coll, refund_id, now)
                prior_doc = existing
                if existing is None:
                    logger.info(
                        "[SHOPIFY_REFUND] refund=%s already credited -- duplicate", refund_id
                    )
                    return {
                        "status": "duplicate",
                        "refund_id": refund_id,
                        "order_id": order_id,
                    }
                logger.info(
                    "[SHOPIFY_REFUND] refund=%s re-attempting previously-unposted refund",
                    refund_id,
                )
                claimed = True
            else:
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
                # GSTR-1 CDNR head consistency: reverse under the SAME head the
                # parent online order filed under (its persisted interstate
                # flag); absent -> the CDNR state-compare fallback unchanged.
                interstate=(
                    order.get("interstate")
                    if isinstance(order.get("interstate"), bool)
                    else None
                ),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] credit note post failed: %s", exc)

    # (b) Restock the refunded serialized units back to the fulfilling store --
    # unless the IMS cancel door already put them back. Asked HERE, at post
    # time, because the door can run AFTER the refund was queued (staff cancel
    # while the accountant's review row still proposes the restock): the SOLD
    # unit is gone by the confirm, and the restock would mint a phantom.
    # Likewise a counter return taken after the refund was queued: its unit is
    # on the shelf again (the returnable-qty answer, asked again now).
    if _ims_cancel_door_ran(order):
        return_lines = [line.model_copy(update={"restock": False}) for line in return_lines]
    return_lines, _, unknown = _cap_restock_to_returnable(return_lines, order, refund_id)
    # An unreadable SOLD answer: restock nothing now and keep the restock OPEN
    # (applied=False, a task, the /returns/{id}/restock retry) -- the same
    # posture as an order that cannot be read.
    restock_unverified = restock_unverified or unknown
    restock_result: Dict[str, Any] = {
        "restocked": [],
        "restock_stock_ids": [],
        "applied": False,
        "skipped": [],
        "restock_store_id": None,
        "restock_store_ids": [],
        "restock_store_redirected_from": None,
        "restock_store_reason": None,
    }
    # IDEMPOTENT RESTOCK. This call can be a RE-drive of a refund that already
    # finalized without a credit note (a guest confirm -> CREDIT_FAILED, which is
    # confirmable again). The credit note must be retried; the RESTOCK must NOT
    # -- re-running it mints a SECOND set of units, so two physical frames end up
    # as four AVAILABLE rows across live shelves.
    prior_restocked = bool((prior_doc or {}).get("restock_applied"))
    if prior_restocked:
        logger.info(
            "[SHOPIFY_REFUND] refund=%s already restocked on an earlier attempt "
            "-- re-driving the credit note ONLY (no second restock)",
            refund_id,
        )
        restock_result = {
            "restocked": (prior_doc or {}).get("restocked", []),
            "restock_stock_ids": (prior_doc or {}).get("restock_stock_ids", []),
            "applied": True,
            "skipped": [],
            "restock_store_id": (prior_doc or {}).get("restock_store_id"),
            "restock_store_ids": (prior_doc or {}).get("restock_store_ids", []),
            "restock_store_redirected_from": (prior_doc or {}).get(
                "restock_store_redirected_from"
            ),
            "restock_store_reason": (prior_doc or {}).get("restock_store_reason"),
        }
    elif restock_unverified and any(line.restock for line in return_lines):
        # We could not read the real order (or its SOLD units), so we do NOT
        # know which shop shipped which unit, or whether it is still out. There is no safe fallback -- a single-store guess strands
        # the other shop's real unit SOLD forever and mints a phantom on a live
        # shelf, while reporting success. Restock NOTHING, fail loud, and let the
        # blocked units surface as a task + the /returns/{id}/restock retry.
        # The credit note (the money + GST leg) still posts below.
        from ..routers.returns import (
            _RESTOCK_ROUTE_UNRESOLVED,
            _raise_restock_blocked_task,
            _restock_intent_rows,
        )

        logger.error(
            "[SHOPIFY_REFUND] restock BLOCKED for refund=%s order=%s: the order "
            "or its SOLD units could not be read, so where each unit goes is unknown. "
            "Nothing restocked (a single-store guess would strand one shop's "
            "unit and mint a phantom on another). Credit note still posted.",
            refund_id,
            order_id,
        )
        units = [
            {
                "product_id": getattr(line, "product_id", None),
                "sku": getattr(line, "sku", ""),
                "product_name": getattr(line, "product_name", ""),
            }
            for line in return_lines
            if line.restock
        ]
        restock_result = {
            "restocked": _restock_intent_rows(units),
            "restock_stock_ids": [],
            "applied": False,
            "skipped": [],
            "restock_store_id": None,
            "restock_store_ids": [],
            "restock_store_redirected_from": billing_store,
            "restock_store_reason": _RESTOCK_ROUTE_UNRESOLVED,
        }
        _raise_restock_blocked_task(
            return_id or refund_id, order_id, billing_store, units, None
        )
    else:
        try:
            from ..routers.returns import _restock_good_items

            # Hand the guard the ORDER's own store (the ONLINE billing bucket)
            # and the VERIFIED order dict, and let the SINGLE F9 router decide
            # where each unit goes. Pre-resolving a physical store here (what
            # this door used to do) made is_online_store False, so the guard
            # short-circuited "already physical" and its per-unit narrowing
            # NEVER ran on the dominant automated door -- booking every unit of
            # a two-shop order to the alphabetically-first shop.
            # Booked on the order lines first (_restock_booked): Goods back or
            # the retry door may run for this refund too, before or after.
            # None: one of them just put the units back -- nothing to restock.
            restock_result = _restock_booked(order, return_lines, refund_id, lambda ls: _restock_good_items(
                ls,
                billing_store,
                return_id or refund_id,
                order_id=order_id,
                user_id="SYSTEM_SHOPIFY_REFUND",
                # No human counter on this door (it runs as SYSTEM), and the
                # stored review-row proposal is NOT a routing signal: it is
                # derived from the same stamps as tier-1, so it is either
                # redundant or -- when the stamps are missing -- baseless. The
                # counter door still supplies the operator's real store here.
                processing_store_id=None,
                order=order,
            )) or {**restock_result, "applied": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SHOPIFY_REFUND] restock failed (recorded, not applied): %s", exc
            )

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
        # The guard's ACTUAL routing answer overwrites the pre-guard proposal,
        # so the returns doc can never claim units went to a store they did not
        # (this door previously wrote the proposal once and never corrected it).
        "restock_store_id": restock_result.get("restock_store_id"),
        "restock_store_ids": restock_result.get("restock_store_ids", []),
        "restock_store_redirected_from": restock_result.get(
            "restock_store_redirected_from"
        ),
        "restock_store_reason": restock_result.get("restock_store_reason"),
        # Release the retry claim so a legitimate later retry of a still-uncredited
        # row can re-claim it; a genuinely credited row is already guarded by
        # credit_note_issued=True, so clearing this here is safe either way.
        "reprocessing_at": None,
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
        # Where the units ACTUALLY landed (None when the restock was blocked or
        # a split sent them to several shops -- see restock_store_ids).
        "restock_store_id": restock_result.get("restock_store_id"),
        "restock_store_ids": restock_result.get("restock_store_ids", []),
        "restock_store_reason": restock_result.get("restock_store_reason"),
        # Empty with restock_applied=True: nothing was put back (the goods are
        # still out) -- the screen says so and points at Goods back.
        "restock_stock_ids": restock_result.get("restock_stock_ids", []),
    }


_FULFILMENT_CONTEXT_KEYS = (
    "fulfillment_stores",
    "fulfillment_breakdown",
    "channel",
    "interstate",
    # The IMS cancel door's stamps (_ims_cancel_door_ran): a door that ran
    # after the review row was queued means the units are already back.
    "cancelled_by",
    "cancel_stock_released",
    # The order lines, for the returnable-qty cap (_cap_restock_to_returnable):
    # a counter return taken after the row was queued put its unit back too.
    "items",
    # The SOLD-unit cap's exemption (_cap_restock_to_sold_units).
    "historical",
)


def _merge_fulfilment_context(order: Dict[str, Any]) -> bool:
    """Copy the REAL order's fulfilment stamps onto a rebuilt order dict.

    `fulfillment_stores` / `fulfillment_breakdown` are the ONLY record of which
    physical shop shipped each unit, and the restock router needs them to send
    each returned unit back to the shop it left. A review row does not store
    them, so a dict rebuilt from the row must be topped up here.

    Mutates ``order``. Returns True only when the real order was actually READ
    -- i.e. the routing evidence is VERIFIED.

    Returning a bool is load-bearing, not cosmetic. This used to return the
    (untouched) dict on failure, and because that dict is non-None it SUPPRESSED
    the router's own re-load at returns.py `if order is None` -- so an
    unreadable order silently fell through to a single-store fallback and landed
    every unit of a multi-shop order on one shop: the other shop's real unit
    stranded SOLD forever and a phantom minted on a live shelf, reported as a
    success. The caller MUST NOT restock against an unverified order; there is
    no safe fallback here, only a guess that looks like an answer."""
    if not order.get("order_id"):
        return False
    try:
        from ..routers.returns import _load_order_for_restock

        real = _load_order_for_restock(order.get("order_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] fulfilment-context load failed: %s", exc)
        real = None
    if not isinstance(real, dict):
        return False
    for key in _FULFILMENT_CONTEXT_KEYS:
        if real.get(key) is not None and order.get(key) is None:
            order[key] = real[key]
    return True


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
    # SHOPIFY_REFUND_AUTO is OFF by default, so THIS is the door every live
    # refund walks through. The rebuilt dict above carries no fulfilment stamps,
    # and handing a NON-None order to _restock_good_items suppresses its own
    # re-load -- which killed per-unit routing on exactly the path that runs:
    # every confirm fell through to the alphabetically-first fallback store,
    # stranding the other shop's real unit SOLD forever and minting a phantom on
    # a live physical shelf. Merge the REAL order's fulfilment stamps back on.
    # A review row with no order_id cannot be verified at all. Today that shape
    # is kept out by the _CONFIRMABLE allow-list in
    # routers/online_store_refund_reviews.py (an UNMATCHED row is not
    # confirmable) -- a guard in a DIFFERENT file with nothing linking the two.
    # _merge_fulfilment_context returning False covers it here as well, so this
    # function is safe on its own terms.
    verified = _merge_fulfilment_context(order)
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
        # Display hint only (see _proposed_restock_store_for_order). Rows
        # written before the rename carry it under the old key.
        restock_store=(
            review.get("proposed_restock_store_id")
            or review.get("restock_store_id")
        ),
        settled_externally=settled_externally,
        # Could not read the real order -> we do NOT know which shop shipped
        # which unit. Refuse to restock rather than guess; the credit note still
        # posts and the blocked units become a visible, retryable task.
        restock_unverified=not verified,
    )


_Held = List[Tuple[Dict[str, Any], float, str]]


def _restock_booked(
    order: Dict[str, Any], lines: List[Any], refund_id: str,
    restock: Callable[[List[Any]], Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """The ONE way a Shopify refund's units go back in stock: Goods back, the
    confirm / AUTO post and the /returns/{id}/restock retry each call it with
    lines already capped by _cap_restock_to_returnable, and `restock` (their
    returns._restock_good_items call). Every restock line is first booked on
    its order line (_hold_returned_qty): its returned_qty, which the counter
    return door and every cap read, and this refund's mark, so the refund
    never restocks that line a second time, whichever door runs first -- on a
    historical order too, where no SOLD unit guards the restock and a second
    one MINTS a phantom frame. A line whose unit did not land is un-booked; a
    landed one never is. None: another door booked a line first (nothing
    restocked)."""
    held = _hold_returned_qty(order, lines, refund_id)
    if held is None:
        return None
    result: Dict[str, Any] = {"applied": False}
    try:
        result = restock(lines)
    finally:
        _release_unlanded(order, held, result, refund_id)
    return result


def _hold_returned_qty(order: Dict[str, Any], lines: List[Any], refund_id: str) -> Optional[_Held]:
    """Book each restock line on its order line through the counter return
    door's own atomic claim (returns._claim_returnable_qty, with this
    refund's mark). All or nothing: None when a line has no returnable unit
    left or this refund already restocked it (another door just did); the
    claims already taken are released."""
    from ..routers.returns import _claim_returnable_qty, _order_line_index, _resolve_original_line

    idx = _order_line_index(order)
    held: _Held = []
    for line in lines:
        orig = _resolve_original_line(line, idx) if line.restock else None
        if orig is None:
            continue
        if not _claim_returnable_qty(order.get("order_id"), orig, float(line.return_qty), refund_id):
            _release_unlanded(order, held, {}, refund_id)
            return None
        held.append((orig, float(line.return_qty), str(line.product_id or "")))
    return held


def _release_unlanded(order: Dict[str, Any], held: _Held, result: Dict[str, Any], refund_id: str) -> None:
    """Un-book what did not land, by the restock's own per-product count of
    units it reactivated or minted. A line with nothing landed drops the
    refund's mark too (another press may restock it); a partly landed one
    keeps it at the units that landed, so its landed unit is never restocked
    twice and the missing one shows as a restock not applied (ponytail: a
    qty>1 refund line gets no partial retry)."""
    from ..routers.returns import _release_returnable_qty

    landed: Dict[str, float] = {}
    for row in (result or {}).get("restocked") or []:
        if isinstance(row, dict):
            pid = str(row.get("product_id") or "")
            landed[pid] = landed.get(pid, 0.0) + _f(row.get("reactivated")) + _f(row.get("minted"))
    for orig, qty, pid in held:
        keep = min(qty, landed.get(pid, 0.0))
        landed[pid] = landed.get(pid, 0.0) - keep
        if keep < qty:
            _release_returnable_qty(order.get("order_id"), orig, qty - keep, refund_id,
                                    keep_mark=keep > 0)


def goods_back(db, review: Dict[str, Any], *, user_id: Optional[str]) -> Dict[str, Any]:
    """A person says the goods of this Shopify refund physically came back:
    put its units back in stock. This is the goods leg of a refund whose goods
    were out -- a DELIVERED order Shopify cancels or refunds stays DELIVERED
    (owner ruling 2026-09-28), and a line whose goods the customer holds
    restocks nothing at the confirm. The money
    is the confirm's, never this door's; the counter return door would refund
    it a second time.

    Every line is asked to restock, capped like every restock
    (_cap_restock_to_returnable) and restocked the one way every door does
    (_restock_booked): no unit a counter return already took back, no line
    this refund's confirm, AUTO post or retry already restocked -- so a second
    press, or a press before or after the confirm, never mints a phantom, on a
    historical order too. The units it puts back are booked returned on their
    order lines, so the counter return door cannot take them back again. ONE
    press per row, claimed on the row (goods_back_at); a restock that did not
    land releases both claims so it can be pressed again. NEVER raises. Returns
    {"status": "restocked" | "duplicate" | "not_restocked", ...}."""
    review_id = review.get("review_id")
    refund_id = _norm(review.get("shopify_refund_id"))
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        claim = coll.update_one(
            {"review_id": review_id, "goods_back_at": None},
            {"$set": {"goods_back_at": datetime.now(timezone.utc).isoformat(),
                      "goods_back_by": user_id}},
        )
    except Exception:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] goods-back claim failed for review=%s", review_id,
                       exc_info=True)
        return {"status": "not_restocked", "review_id": review_id, "reason": "claim_failed"}
    if not getattr(claim, "modified_count", 0):
        return {"status": "duplicate", "review_id": review_id}

    result: Dict[str, Any] = {"applied": False}
    reason = "order_unreadable"
    order: Dict[str, Any] = {k: review.get(k) for k in ("order_id", "store_id", "shopify_order_id")}
    try:
        if _merge_fulfilment_context(order):
            lines = [
                line.model_copy(update={"restock": True})
                for line in _return_lines_from_proposed(review.get("proposed_restock") or [])
            ]
            lines, _, unknown = _cap_restock_to_returnable(lines, order, refund_id)
            reason = "stock_unreadable" if unknown else "not_routed"
            if not unknown:
                from ..routers.returns import _restock_good_items

                booked = _restock_booked(order, lines, refund_id, lambda ls: _restock_good_items(
                    ls,
                    order.get("store_id"),
                    review.get("return_id") or refund_id,
                    order_id=order.get("order_id"),
                    user_id=user_id,
                    processing_store_id=None,
                    order=order,
                ))
                if booked is None:
                    reason = "already_returned"
                else:
                    result = booked
    except Exception:  # noqa: BLE001
        reason = "error"
        logger.warning("[SHOPIFY_REFUND] goods-back restock failed for review=%s", review_id,
                       exc_info=True)

    out = {
        "review_id": review_id,
        "restock_applied": bool(result.get("applied")),
        "restock_stock_ids": result.get("restock_stock_ids", []),
        "restock_store_id": result.get("restock_store_id"),
        "restock_store_ids": result.get("restock_store_ids", []),
    }
    # Landed: record it. Nothing landed: release the press so it can be pressed
    # again (the caps make a re-press safe even after a partial restock).
    stamp = ({"goods_restock": out} if out["restock_applied"]
             else {"goods_back_at": None, "goods_back_by": None})
    try:
        coll.update_one({"review_id": review_id}, {"$set": stamp})
    except Exception:  # noqa: BLE001
        logger.warning("[SHOPIFY_REFUND] goods-back stamp failed for review=%s", review_id,
                       exc_info=True)
    if out["restock_applied"]:
        return {"status": "restocked", **out}
    return {"status": "not_restocked", "reason": reason, **out}
