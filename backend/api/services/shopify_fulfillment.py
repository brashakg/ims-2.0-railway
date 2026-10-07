"""
IMS 2.0 - Shopify FULFILLMENT reconcile  (BVI-retirement phase 0)
==================================================================
Handles Shopify `fulfillments/create` and `fulfillments/update` webhooks: stamp
the matching IMS online order's tracking (AWB / carrier / URL / shipment status)
and hand the fulfilment's ONE lifecycle fact to the transition table
(online_order_status). BVI reconciled fulfilment today; when BVI is retired IMS
must, or an online order that has actually shipped would stay CONFIRMED forever.

CONTRACT:
  * Match the IMS order by shopify_order_id. NOT found -> log + no-op (fail-soft;
    never crash the NEXUS drain loop).
  * $set the tracking fields + fulfillment_status, riding the status claim
    (apply_fact `marks`). Because we only $set (never increment), a
    re-delivered webhook is naturally idempotent.
  * The lifecycle status is the table's (online_order_status.apply_fact): a
    fulfilment states SHIP, or DELIVER when shipment_status is "delivered"
    (owner ruling 2026-09-28: DELIVERED only from the courier), or nothing (a
    cancelled / failed fulfilment). The table never regresses a status, keeps
    a finished one, and withholds SHIPPED / DELIVERED from an order on an active
    Rx / stock hold (owner decision 2026-06-30), re-raising the Rx task --
    tracking / fulfillment_status / shipment_status still land.
  * HISTORICAL import orders (bvi_import) are skipped (settled outside IMS books).

PUBLIC API:
    reconcile_fulfillment(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Shopify fulfillment.status -> canonical IMS fulfillment_status.
# (Shopify: pending | open | success | cancelled | error | failure.)
_FULFILLMENT_STATUS_MAP = {
    "success": "FULFILLED",
    "open": "PARTIAL",
    "pending": "PARTIAL",
    "cancelled": "CANCELLED",
    "error": "ERROR",
    "failure": "ERROR",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


# Fulfilment clocks (a payload's updated_at, naive UTC) -- the order-level one
# (shopify_updated_at) is the mapper's, and an order body's updated_at is a
# different clock from a fulfilment's. FULFILLMENT_CLOCKS holds EACH
# fulfilment's own last applied clock, keyed by _clock_key: a split shipment's
# parcels move on their own clocks, so a fulfilment is only ever compared with
# its own earlier state. FULFILLMENT_WATERMARK is the clock of the fulfilment
# the order's tracking fields show -- the newest one IMS applied.
FULFILLMENT_CLOCKS = "shopify_fulfillment_clocks"
FULFILLMENT_WATERMARK = "shopify_fulfillment_updated_at"
# Each parcel's AWB ([{"id": _clock_key, "awb"}]; "" for a cancelled or
# failed one). The tracking fields show one parcel; the courier legs
# (the Shiprocket poll and webhook) ask about all of them (tracked_awbs), so a
# split shipment is delivered by whichever parcel the courier delivers.
PARCEL_AWBS = "shopify_parcel_awbs"


def tracked_awbs(order: Dict[str, Any]) -> list:
    """The AWBs the courier legs track for an order: every live parcel's, or,
    on an order no fulfilment has reached since the parcel list, its awb."""
    parcels = order.get(PARCEL_AWBS)
    if isinstance(parcels, list):
        return [p["awb"] for p in parcels if isinstance(p, dict) and p.get("awb")]
    return [order["awb"]] if order.get("awb") else []


def awb_filter(awb: str) -> Dict[str, Any]:
    """The orders query for the order an AWB belongs to, by tracked_awbs'
    own rule."""
    return {"$or": [{f"{PARCEL_AWBS}.awb": awb}, {"awb": awb, PARCEL_AWBS: {"$exists": False}}]}


def _write_parcel(orders, oid: Dict[str, Any], key: str, awb: Optional[str],
                  clock: Dict[str, Any], watermark: Any) -> None:
    """ONE parcel's entry in PARCEL_AWBS ({"id": key, "awb"}: its live AWB,
    "" once it is cancelled / failed, so the order still gets the list and
    tracked_awbs never falls back to the order's awb; None: a live parcel with
    no tracking number yet, whose stored AWB stays -- an empty one never
    clears what an older event wrote, _tracking_fields' rule) and its clock,
    in ONE write that matches only while this fulfilment's stored clock is
    not newer: a stale reconcile racing a newer one of the same parcel lands
    before it or not at all. The entry is replaced in place, else appended; another
    parcel's entry is never written, so two parcels reconciled at once both
    stay."""
    guard = {} if watermark is None else {f"{FULFILLMENT_CLOCKS}.{key}": {"$not": {"$gt": watermark}}}
    entry = {} if awb is None else {f"{PARCEL_AWBS}.$.awb": awb}
    # Two passes: an append that lost to the same parcel's own append finds
    # its entry (entries are never removed) and replaces it on the second.
    for _ in range(2):
        if orders.update_one({**oid, **guard, f"{PARCEL_AWBS}.id": key},
                             {"$set": {**entry, **clock}}).matched_count:
            return
        if orders.update_one({**oid, **guard, f"{PARCEL_AWBS}.id": {"$ne": key}},
                             {"$push": {PARCEL_AWBS: {"id": key, "awb": awb or ""}},
                              "$set": clock}).matched_count:
            return


def _clock_key(f: Dict[str, Any]) -> str:
    """A fulfilment's key in FULFILLMENT_CLOCKS: its bare id (the push stamps
    the GraphQL gid), prefixed so the dotted write is never an array index."""
    return "f" + _norm(f.get("id")).rsplit("/", 1)[-1]


def fulfilment_clock(existing: Dict[str, Any], f: Dict[str, Any]) -> Any:
    """The clock IMS last applied for THIS fulfilment (None: never applied)."""
    return (existing.get(FULFILLMENT_CLOCKS) or {}).get(_clock_key(f))


def newest_fulfilment(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The newest fulfilment on a Shopify order body (None when it carries
    none): what a whole body is compared with (fulfilment_body_stale)."""
    from .shopify_ingest import _to_naive_utc

    rows = [f for f in (order.get("fulfillments") or []) if isinstance(f, dict) and f.get("id")]
    if not rows:
        return None
    return max(
        rows,
        key=lambda f: _to_naive_utc(f.get("updated_at") or f.get("created_at")) or datetime.min,
    )


def fulfilment_body_stale(existing: Dict[str, Any], body: Dict[str, Any]) -> bool:
    """ONE fulfilment-clock check for the mapper and the hourly pull sweep: an
    order body whose newest fulfilment (the body itself when it carries none)
    is STRICTLY older than the fulfilment IMS last applied (the reconcile's
    FULFILLMENT_WATERMARK) states no fulfillment_status fact -- writing it
    would rewind SHIPPED / FULFILLED. Only the reconcile moves the watermark;
    this only reads it. Fail-open (the mapper's stale rule)."""
    from .online_order_mapper import _shopify_payload_stale

    return _shopify_payload_stale(existing, newest_fulfilment(body) or body, field=FULFILLMENT_WATERMARK)


def _tracking_fields(payload: Dict[str, Any]) -> Dict[str, str]:
    """The tracking fields a fulfilment payload $sets on the IMS order -- only
    the NON-EMPTY ones: an empty one never clears what an older fulfilment
    wrote. ONE extraction for the reconcile and for the hourly pull sweep's
    "did the fulfilment move" (nexus_providers._fulfilment_moved)."""
    tracking_number = _norm(payload.get("tracking_number")) or _norm(
        (payload.get("tracking_numbers") or [None])[0]
    )
    fields = {
        "awb": tracking_number,
        "tracking_number": tracking_number,
        "tracking_company": _norm(payload.get("tracking_company")),
        "tracking_url": _norm(payload.get("tracking_url"))
        or _norm((payload.get("tracking_urls") or [None])[0]),
        "shipment_status": _norm(payload.get("shipment_status")).lower(),
    }
    return {k: v for k, v in fields.items() if v}


def _find_ims_order(db, shopify_order_id: str) -> Optional[Dict[str, Any]]:
    if db is None or not shopify_order_id:
        return None
    try:
        coll = db.get_collection("orders")
        if coll is None:
            return None
        return coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_FULFILL] order lookup failed", exc_info=True)
        return None


def reconcile_fulfillment(
    db, payload: Dict[str, Any], *, topic: Optional[str] = None
) -> Dict[str, Any]:
    """Reconcile a Shopify Fulfillment onto the matching IMS online order.

    Returns a structured result; NEVER raises."""
    try:
        payload = payload if isinstance(payload, dict) else {}
        shopify_order_id = _norm(payload.get("order_id"))
        fulfillment_id = _norm(payload.get("id"))
        if not shopify_order_id:
            return {"status": "skipped", "reason": "no_order_id"}
        if db is None:
            return {"status": "simulated", "fulfillment_id": fulfillment_id}

        order = _find_ims_order(db, shopify_order_id)
        if not order:
            # Fail-soft: a fulfilment for an order we never ingested. Log, no crash.
            logger.info(
                "[SHOPIFY_FULFILL] no IMS order for shopify_order_id=%s "
                "(fulfilment=%s) -- ignored",
                shopify_order_id,
                fulfillment_id,
            )
            return {
                "status": "order_not_found",
                "shopify_order_id": shopify_order_id,
                "fulfillment_id": fulfillment_id,
            }

        if order.get("historical") or order.get("source") == "bvi_import":
            return {"status": "skipped", "reason": "historical_import_order"}

        from .online_order_mapper import _shopify_payload_stale
        from .online_order_status import apply_fact, fulfilment_fact
        from .shopify_ingest import _to_naive_utc

        # OUT-OF-ORDER guard: the mapper's stale rule on THIS fulfilment's own
        # clock. A payload STRICTLY older than the state of the same
        # fulfilment IMS last applied (a retried create landing after its own
        # update, or the hourly pull's body losing the race with a
        # fulfillments/update) is skipped whole. Another parcel's clock is
        # never compared: its late "delivered" is a real fact. Fail-open
        # without a stamp on either side.
        if _shopify_payload_stale({"at": fulfilment_clock(order, payload)}, payload, field="at"):
            return {"status": "skipped", "reason": "stale_fulfillment"}

        ful_status = _FULFILLMENT_STATUS_MAP.get(
            _norm(payload.get("status")).lower(), "FULFILLED"
        )
        tracking = _tracking_fields(payload)
        shipment_status = tracking.get("shipment_status", "")
        tracking_number = tracking.get("tracking_number", "")

        # The lifecycle status: this fulfilment's ONE fact through the ONE
        # transition table (the mapper's and the courier's too). A failed
        # write leaves the fulfilment unapplied and writes nothing else, so
        # the hourly sweep sees it moved and re-feeds it.
        current_status = _norm(order.get("status")).upper()
        res = apply_fact(
            db,
            order,
            fulfilment_fact(ful_status, shipment_status, tracking_number),
            source="SHOPIFY_FULFILL",
        )
        if res["failed"]:
            return {"status": "error", "error": "status write failed",
                    "order_id": order.get("order_id"), "fulfillment_id": fulfillment_id}

        # Then this parcel's own state, never as a whole list or field set
        # from this read: another parcel's reconcile (a second worker, the
        # sweep beside a webhook) may be writing its own at the same time.
        # Its entry and clock go LAST, in one write, so a write that fails
        # before it leaves the fulfilment unapplied and the sweep re-feeds it
        # (every write here lands again unchanged).
        orders = db.get_collection("orders")
        oid = {"order_id": order.get("order_id")}
        live = ful_status not in ("CANCELLED", "ERROR")
        # The order's tracking fields show the NEWEST fulfilment IMS applied:
        # an older parcel's late event states its own fact above but never
        # takes them over from a newer one (the write matches only while the
        # stored watermark is not newer), and a cancelled / failed parcel
        # never takes them over while another parcel is still live.
        # An order reconciled before the clocks (no watermark) holds its
        # newest fulfilment -- the old reconcile stamped the last one it saw:
        # only that fulfilment itself, the order's first, or a live one over
        # a cancelled / failed stamp takes the fields over (fail closed: the
        # sweep re-feeds every older parcel, which would replace the newer).
        others = [p for p in order.get(PARCEL_AWBS) or []
                  if isinstance(p, dict) and p.get("awb") and p.get("id") != _clock_key(payload)]
        watermark = _to_naive_utc(payload.get("updated_at"))
        if live or not others:
            takeover = {"fulfillment_status": ful_status, "shopify_fulfillment_id": fulfillment_id,
                        **tracking}
            newer: Dict[str, Any] = {}
            if watermark is not None:
                takeover[FULFILLMENT_WATERMARK] = watermark
                legacy = [{FULFILLMENT_WATERMARK: None, "shopify_fulfillment_id": {
                    "$in": [None, "", fulfillment_id, f"gid://shopify/Fulfillment/{fulfillment_id}"]}}]
                if live:
                    legacy.append({FULFILLMENT_WATERMARK: None,
                                   "fulfillment_status": {"$in": ["CANCELLED", "ERROR"]}})
                newer = {"$or": [{FULFILLMENT_WATERMARK: {"$lte": watermark}}, *legacy]}
            orders.update_one({**oid, **newer}, {"$set": takeover})
        clock: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if not fulfillment_id:
            orders.update_one(oid, {"$set": clock})
        else:
            if watermark is not None:
                clock[f"{FULFILLMENT_CLOCKS}.{_clock_key(payload)}"] = watermark
            # The parcel list of an order reconciled before it starts with the
            # parcel the old reconcile stamped: the courier legs (tracked_awbs)
            # keep tracking it beside this one.
            stamped = _clock_key({"id": order.get("shopify_fulfillment_id")})
            if stamped not in ("f", _clock_key(payload)) and not isinstance(order.get(PARCEL_AWBS), list):
                gone = _norm(order.get("fulfillment_status")).upper() in ("CANCELLED", "ERROR")
                orders.update_one({**oid, PARCEL_AWBS: {"$exists": False}}, {"$set": {PARCEL_AWBS: [
                    {"id": stamped, "awb": "" if gone else _norm(order.get("awb"))}]}})
            _write_parcel(orders, oid, _clock_key(payload),
                          (tracking_number or None) if live else "", clock, watermark)
        order_status = res["to"] or current_status

        logger.info(
            "[SHOPIFY_FULFILL] order=%s fulfilment=%s -> fulfillment_status=%s "
            "status=%s awb=%s",
            order.get("order_id"),
            fulfillment_id,
            ful_status,
            order_status,
            tracking_number or "-",
        )
        return {
            "status": "reconciled",
            "shopify_order_id": shopify_order_id,
            "order_id": order.get("order_id"),
            "fulfillment_id": fulfillment_id,
            "fulfillment_status": ful_status,
            "order_status": order_status,
            "awb": tracking_number,
            # The table kept a finished status (a fulfilment on an order IMS
            # holds DELIVERED / CANCELLED / ...): the pull sweep reports it as
            # status_skipped_terminal.
            "terminal_withheld": res["terminal_withheld"],
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning("[SHOPIFY_FULFILL] reconcile_fulfillment failed soft: %s", exc)
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
