"""
IMS 2.0 - Shopify FULFILLMENT PUSH-BACK  (IMS -> Shopify, close the order loop)
================================================================================
The OUTBOUND counterpart to shopify_fulfillment.reconcile_fulfillment (which is
INBOUND: Shopify tells IMS an order shipped). This module does the reverse: when
IMS ships / dispatches an ONLINE order (source == "shopify"; the doc carries a
shopify_order_id), it PUSHES the fulfilment + tracking (courier / AWB / URL from
the Shiprocket booking) TO Shopify -- so the buyer gets Shopify's own shipping
notification and the order shows Fulfilled on the storefront.

Historically BVI owned the Shopify writer, so a fulfilment IMS booked was never
told to Shopify from here. With BVI retired and IMS the SOLE Shopify writer, IMS
must push the fulfilment itself or an online order that actually shipped would
sit UNFULFILLED on Shopify forever and the buyer would never get a tracking mail.

***** GATED EXACTLY LIKE shopify_push.py (the single-writer safety contract) *****
Every push is SIMULATED -- returns a dry-run PLAN and makes NO network call --
UNLESS ALL THREE hold (checked via the SAME shopify_push._live_or_reason gate we
reuse here, so this module can NEVER be looser than the catalog push):
  1. ims_shopify_writes_enabled()      -- IMS_SHOPIFY_WRITES on (default OFF).
  2. shopify_dispatch_mode() == "live" -- SHOPIFY_DISPATCH_MODE (or global
     DISPATCH_MODE) is live.
  3. Shopify creds present             -- resolve_shopify_credentials(db, "BV").
Default / gate-off / missing-creds -> mode="SIMULATED", no Shopify call.

IDEMPOTENT: once a LIVE push creates the Shopify fulfilment, its gid is written
BACK onto the IMS order (shopify_fulfillment_id + shopify_fulfillment_pushed_at).
A re-call SKIPS (the stamped id short-circuits before any network). We ALSO skip
when Shopify reports the order already has no OPEN FulfillmentOrder (already
fulfilled out-of-band) -- stamping the existing fulfilment gid so the next call
is a fast skip too.

FAIL-SOFT: every function returns a structured FulfillmentPushResult and NEVER
raises. A Shopify/GraphQL error becomes {ok: False, error: ...}. This is called
FIRE-AND-FORGET from the dispatch hook (shipping.book_shipment); a push must
never take down the booking that triggered it.

REUSE, do NOT fork the writer: the LIVE gate, the retrying GraphQL network
boundary (shopify_push._graphql), the userErrors extractor, and the GID helper
are all imported from the code-verified shopify_push / nexus_providers modules.
Tests monkeypatch shopify_push._graphql so no real Shopify call ever happens.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import logging

# Reuse the code-verified Shopify writer primitives -- NEVER a second gate/boundary.
from . import shopify_push
from agents.nexus_providers import _as_shopify_gid

logger = logging.getLogger(__name__)

MODE_SIMULATED = shopify_push.MODE_SIMULATED
MODE_LIVE = shopify_push.MODE_LIVE

# Shopify FulfillmentOrder statuses that are still fulfillable (a fulfilment can
# be created against them). Anything else (CLOSED / INCOMPLETE / CANCELLED) is
# NOT fulfillable and is skipped.
_FULFILLABLE_FO_STATUS = {"OPEN", "IN_PROGRESS", "SCHEDULED"}

# How many FulfillmentOrders / existing Fulfillments to inspect per order. An
# online optical order is a single parcel; a handful is ample headroom.
_FO_PAGE = 20
_FUL_PAGE = 10


@dataclass
class FulfillmentPushResult:
    """Structured result of one fulfilment push-back attempt. Returned by
    push_fulfillment and recorded verbatim on the chained ONLINE_STORE_PUSH audit
    row by the caller. Field names mirror shopify_push.PushResult so the shared
    _write_audit pattern reads it unchanged.

    mode        SIMULATED (dry-run, no network) | LIVE (a real Shopify write).
    entity      always "fulfillment".
    action      create | skip | noop (what we did / would do).
    target_id   the shopify_order_id (or IMS order ref) we were asked to push.
    ok          True unless an error occurred (a SIMULATED dry-run is ok=True).
    shopify_id  the Shopify Fulfillment gid (set on a LIVE create OR echoed when
                already stamped / already fulfilled).
    payload     the dry-run plan (SIMULATED) or the mutation variables (LIVE).
    error       a human string when ok=False; None otherwise.
    reason      why we are SIMULATED / skipped -- advisory.
    """

    mode: str
    action: str
    entity: str = "fulfillment"
    target_id: Optional[str] = None
    ok: bool = True
    shopify_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ===========================================================================
# GraphQL operations (pinned + minimal, mirroring shopify_push's style)
# ===========================================================================

# Resolve the order's FulfillmentOrders (what fulfillmentCreateV2 acts on) plus
# any existing Fulfillments (so an already-fulfilled order is detected + its gid
# echoed). fulfillmentOrders is a connection; fulfillments is a plain list in the
# pinned Admin API version.
_ORDER_FULFILLMENT_ORDERS = """
query imsOrderFulfillmentOrders($id: ID!, $foPage: Int!, $fulPage: Int!) {
  order(id: $id) {
    id
    fulfillments(first: $fulPage) { id status }
    fulfillmentOrders(first: $foPage) {
      edges { node { id status } }
    }
  }
}
"""

# Create ONE fulfilment covering every open FulfillmentOrder, carrying the
# tracking info so Shopify emails the buyer + shows the order Fulfilled.
# fulfillmentCreateV2 is the current (non-legacy) create path in the pinned API
# version; trackingInfo maps AWB/courier/URL from the Shiprocket booking.
_FULFILLMENT_CREATE = """
mutation imsFulfillmentCreate($fulfillment: FulfillmentV2Input!) {
  fulfillmentCreateV2(fulfillment: $fulfillment) {
    fulfillment { id status trackingInfo { number company url } }
    userErrors { field message }
  }
}
"""


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_tracking(
    order: Dict[str, Any], tracking: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build the Shopify trackingInfo dict (number / company / url). Prefer the
    explicit tracking handed in by the dispatch hook (the fresh Shiprocket
    booking), then fall back to whatever tracking is already stamped on the order
    (e.g. from a prior reconcile). Empty legs are dropped so Shopify never gets a
    blank string. Pure; never raises."""
    tracking = tracking or {}
    number = (
        _norm(tracking.get("number"))
        or _norm(tracking.get("awb"))
        or _norm(order.get("awb"))
        or _norm(order.get("tracking_number"))
    )
    company = (
        _norm(tracking.get("company"))
        or _norm(tracking.get("courier"))
        or _norm(order.get("courier"))
        or _norm(order.get("tracking_company"))
    )
    url = (
        _norm(tracking.get("url"))
        or _norm(tracking.get("tracking_url"))
        or _norm(order.get("tracking_url"))
    )
    info: Dict[str, Any] = {}
    if number:
        info["number"] = number
    if company:
        info["company"] = company
    if url:
        info["url"] = url
    return info


def _orders_coll(db):
    """The orders collection, via get_collection() with a subscript fallback so
    both real pymongo and the in-memory mock work. None -> caller no-ops."""
    if db is None:
        return None
    try:
        getter = getattr(db, "get_collection", None)
        if callable(getter):
            return getter("orders")
        return db["orders"]
    except Exception:  # noqa: BLE001
        return None


def _order_match_key(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The idempotent write-back key: prefer the unique shopify_order_id, else the
    IMS order_id. None when neither exists (nothing to write back to)."""
    soid = _norm(order.get("shopify_order_id"))
    if soid:
        return {"shopify_order_id": soid}
    oid = _norm(order.get("order_id"))
    if oid:
        return {"order_id": oid}
    return None


def _writeback_fulfillment(db, order: Dict[str, Any], fulfillment_id: str) -> None:
    """Stamp shopify_fulfillment_id + shopify_fulfillment_pushed_at on the IMS
    order so a re-call short-circuits (idempotency). Fail-soft: the Shopify write
    already succeeded, so a write-back error is logged, never raised."""
    coll = _orders_coll(db)
    key = _order_match_key(order)
    if coll is None or key is None or not fulfillment_id:
        return
    try:
        coll.update_one(
            key,
            {
                "$set": {
                    "shopify_fulfillment_id": fulfillment_id,
                    "shopify_fulfillment_pushed_at": _now_iso(),
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SHOPIFY_FULFILL_PUSH] write-back failed order=%s: %s",
            key,
            exc,
        )


def _parse_fulfillment_orders(
    body: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str], List[str]]:
    """From the fulfillmentOrders query body return
    (order_node, open_fo_gids, existing_fulfillment_gids).

    order_node is None when Shopify has no such order (a hard error upstream).
    open_fo_gids are the FulfillmentOrders still fulfillable. existing gids are
    any Fulfillments already on the order (used to echo/stamp when there is
    nothing left to fulfil). Pure; tolerant of a malformed body."""
    data = (body or {}).get("data") or {}
    order = data.get("order")
    if not isinstance(order, dict):
        return None, [], []
    open_fos: List[str] = []
    for edge in ((order.get("fulfillmentOrders") or {}).get("edges") or []):
        node = (edge or {}).get("node") or {}
        gid = node.get("id")
        status = _norm(node.get("status")).upper()
        if gid and status in _FULFILLABLE_FO_STATUS:
            open_fos.append(gid)
    existing_fuls: List[str] = [
        f.get("id")
        for f in (order.get("fulfillments") or [])
        if isinstance(f, dict) and f.get("id")
    ]
    return order, open_fos, existing_fuls


# ===========================================================================
# The push -- DARK by default; LIVE only behind the shared gate.
# ===========================================================================


async def push_fulfillment(
    db,
    order: Dict[str, Any],
    *,
    tracking: Optional[Dict[str, Any]] = None,
    notify_customer: bool = True,
) -> FulfillmentPushResult:
    """Push an ONLINE order's fulfilment + tracking to Shopify. Never raises.

    ``order``    the IMS order doc (must be source == "shopify" with a
                 shopify_order_id to do anything -- otherwise a clean skip).
    ``tracking`` optional {number/awb, company/courier, url} from the fresh
                 Shiprocket booking; falls back to tracking stamped on the order.
    ``notify_customer`` passed to Shopify so the buyer gets the shipping email.

    Flow:
      1. Not an online/Shopify order            -> skip (ok=True).
      2. shopify_fulfillment_id already stamped -> skip (idempotent, no network).
      3. DARK gate closed                       -> SIMULATED plan (no network).
      4. LIVE: resolve the order's OPEN FulfillmentOrder(s); if none remain the
         order is already fulfilled -> skip (echo/stamp the existing gid); else
         fulfillmentCreateV2 with trackingInfo, write the new gid back.
    """
    order = order or {}
    shopify_order_id = _norm(order.get("shopify_order_id"))
    ims_ref = _norm(order.get("order_id")) or _norm(order.get("order_number")) or shopify_order_id
    target = shopify_order_id or ims_ref or None

    # 1. Only ONLINE (Shopify) orders are ever pushed. An in-store order booked
    #    through the same Shiprocket path is a clean, silent skip.
    if not shopify_order_id or _norm(order.get("source")).lower() != "shopify":
        return FulfillmentPushResult(
            mode=MODE_SIMULATED,
            action="skip",
            target_id=target,
            ok=True,
            reason="not_an_online_order (source!=shopify or no shopify_order_id)",
        )

    # 2. Idempotency short-circuit: a stamped fulfilment id means we already
    #    pushed -- never create a duplicate fulfilment, and never hit the network.
    existing_stamp = _norm(order.get("shopify_fulfillment_id"))
    if existing_stamp:
        return FulfillmentPushResult(
            mode=MODE_SIMULATED,
            action="skip",
            target_id=shopify_order_id,
            ok=True,
            shopify_id=existing_stamp,
            reason="already_pushed (shopify_fulfillment_id stamped)",
        )

    tracking_info = _resolve_tracking(order, tracking)
    order_gid = _as_shopify_gid(shopify_order_id, "Order")
    plan: Dict[str, Any] = {
        "order_gid": order_gid,
        "trackingInfo": tracking_info,
        "notifyCustomer": notify_customer,
    }

    # 3. DARK by default -> SIMULATED dry-run plan, NO network call. The gate is
    #    the SAME one the catalog push uses (reused, never re-implemented).
    live, reason = shopify_push._live_or_reason(db)
    if not live:
        return FulfillmentPushResult(
            mode=MODE_SIMULATED,
            action="create",
            target_id=shopify_order_id,
            ok=True,
            payload=plan,
            reason=reason,
        )

    # 4a. LIVE: resolve the order's FulfillmentOrder(s).
    try:
        body = await shopify_push._graphql(
            db,
            _ORDER_FULFILLMENT_ORDERS,
            {"id": order_gid, "foPage": _FO_PAGE, "fulPage": _FUL_PAGE},
        )
    except Exception as exc:  # noqa: BLE001 -- fail-soft, never propagate
        return FulfillmentPushResult(
            mode=MODE_LIVE,
            action="create",
            target_id=shopify_order_id,
            ok=False,
            payload=plan,
            error=f"fulfillmentOrders query failed: {exc}",
        )
    top_err = shopify_push._user_errors(body, "order")
    if top_err:
        return FulfillmentPushResult(
            mode=MODE_LIVE,
            action="create",
            target_id=shopify_order_id,
            ok=False,
            payload=plan,
            error=top_err,
        )

    order_node, open_fos, existing_fuls = _parse_fulfillment_orders(body)
    if order_node is None:
        return FulfillmentPushResult(
            mode=MODE_LIVE,
            action="create",
            target_id=shopify_order_id,
            ok=False,
            payload=plan,
            error="order not found on Shopify (no order node in response)",
        )

    # 4b. No OPEN FulfillmentOrder left -> nothing to fulfil. If a Fulfillment
    #     already exists the order was fulfilled out-of-band: SKIP + stamp the
    #     existing gid so future calls fast-skip. If neither, it is a clean noop
    #     (e.g. a cancelled / unfulfillable order).
    if not open_fos:
        if existing_fuls:
            existing_fid = existing_fuls[0]
            _writeback_fulfillment(db, order, existing_fid)
            return FulfillmentPushResult(
                mode=MODE_LIVE,
                action="skip",
                target_id=shopify_order_id,
                ok=True,
                shopify_id=existing_fid,
                reason="already_fulfilled_on_shopify",
            )
        return FulfillmentPushResult(
            mode=MODE_LIVE,
            action="noop",
            target_id=shopify_order_id,
            ok=True,
            reason="no_open_fulfillment_orders",
        )

    # 4c. Create ONE fulfilment across every open FulfillmentOrder, with tracking.
    fulfillment_input: Dict[str, Any] = {
        "lineItemsByFulfillmentOrder": [
            {"fulfillmentOrderId": fo_gid} for fo_gid in open_fos
        ],
        "notifyCustomer": notify_customer,
    }
    if tracking_info:
        fulfillment_input["trackingInfo"] = tracking_info
    mutation_vars = {"fulfillment": fulfillment_input}

    try:
        body = await shopify_push._graphql(db, _FULFILLMENT_CREATE, mutation_vars)
    except Exception as exc:  # noqa: BLE001 -- fail-soft, never propagate
        return FulfillmentPushResult(
            mode=MODE_LIVE,
            action="create",
            target_id=shopify_order_id,
            ok=False,
            payload=mutation_vars,
            error=f"fulfillmentCreateV2 failed: {exc}",
        )
    err = shopify_push._user_errors(body, "fulfillmentCreateV2")
    if err:
        return FulfillmentPushResult(
            mode=MODE_LIVE,
            action="create",
            target_id=shopify_order_id,
            ok=False,
            payload=mutation_vars,
            error=err,
        )

    ful = (
        ((body.get("data") or {}).get("fulfillmentCreateV2") or {}).get("fulfillment")
        or {}
    )
    new_fid = ful.get("id")
    if new_fid:
        _writeback_fulfillment(db, order, new_fid)
    return FulfillmentPushResult(
        mode=MODE_LIVE,
        action="create",
        target_id=shopify_order_id,
        ok=True,
        shopify_id=new_fid,
        payload=mutation_vars,
    )
