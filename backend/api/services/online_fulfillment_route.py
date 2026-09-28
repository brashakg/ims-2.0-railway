"""
IMS 2.0 - Which shop ships an online order (multi-location PR 5)
================================================================
Owner rulings 2026-09-06/07: one Shopify location per IMS physical shop; Shopify's
routing picks the shop (Q4); IMS moves the fulfillment order only when that shop
is short (Q4), preferring ONE location per order (Q2); the tax invoice is issued
from the SHIPPING SHOP'S OWN GSTIN (Q1, accountant confirmed 2026-09-07).

ONE decision, ``route_order``: the shop it returns is the shop that CLAIMS the
units, BILLS the order (the order's ``store_id`` -- so the seller GSTIN, legal
entity, invoice series and the CGST+SGST vs IGST split all follow it through the
existing order.store_id -> store doc -> entity chain; no second GSTIN picker)
and FULFILS it on Shopify (``fulfillment_order_ids``: only the fulfillment
orders sitting at that shop's location).

    assigned shop  = the shop mapped (stores.shopify_location_id, one reader
                     stores_util.physical_stores, one mapping shopify_push
                     inventory._mapped) to the location Shopify assigned the
                     order's largest open fulfillment order to.
    covers(shop)   = the shop holds CLAIMABLE units (the claim's own rule,
                     StockRepository.sellable_filter) for every claimable line.
    shopify split  = Shopify split the order over several mapped shops and each
                     covers its OWN fulfillment orders' lines: every shop claims
                     and ships its own part (``split``), nothing moves (Q2 allows
                     Shopify's split, Q4 moves only a short shop); the largest
                     fulfillment order's shop bills it, and a leg shop under
                     another GSTIN or without its own state's GSTIN holds the
                     order (``seller_problem``, the one seller check).
    shipping shop  = otherwise assigned if it covers, else the first MAPPED shop
                     that covers the whole order (most stock first), else the
                     assigned shop (the claim under-claims and
                     shopify_ingest._record_stock_miss holds the order + tasks
                     the shop: fail loud, IMS never splits an order itself).
                     No fulfillment orders read (dark / unread): the fallback
                     below or no shop -- never a guess from stock counts. No
                     shop: nothing is claimed and the order is held
                     (SELLER_UNKNOWN).
    moves          = a fulfillment order that is not the shipping shop's
                     (fo_is_shops) is moved to the shipping shop's location ONLY
                     when that shop holds every unit of the order (Q4: never
                     into a short shop) and relocation is on. One that cannot
                     be moved is FO_AT_OTHER_SHOP: loud + the order is held.
                     While a move is pending the order is held too; the move
                     lifts that hold, a failed move keeps it (MOVE_FAILED); a
                     move for a cancelled, human-released or short-claimed
                     order is SKIPPED, never sent.

ONLINE_FULFILLMENT_STORE_ID is the documented FALLBACK, used only while there is
no usable assignment: the assigned location maps to no IMS shop (Pune stays
unmapped until its opening stock lands) or the fulfillment orders could not be
read. It is kept, not retired, because Pune is deliberately unmapped today.
``fallback_store_id`` is its ONE reader (ingest, returns restock, sync health).

Every live problem is loud: a stamped ``fulfillment_route.problems`` entry on the
order AND one deduped P1 task. The Shopify read and the move go through the SAME
gate as every other writer (shopify_push._live_or_reason); DARK -> no network,
the documented fallback applies and the route says so.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fulfillment-order statuses that still need shipping (the push's own set plus
# ON_HOLD, which Shopify releases back to OPEN).
_ACTIVE_FO = {"OPEN", "IN_PROGRESS", "SCHEDULED", "ON_HOLD"}

_ROUTING_QUERY = """
query imsOrderRouting($id: ID!) {
  order(id: $id) {
    id
    fulfillmentOrders(first: 20) {
      nodes {
        id
        status
        assignedLocation { name location { id } }
        lineItems(first: 50) { nodes { remainingQuantity lineItem { id } } }
      }
    }
  }
}
"""

_MOVE_MUTATION = """
mutation imsFulfillmentOrderMove($id: ID!, $newLocationId: ID!) {
  fulfillmentOrderMove(id: $id, newLocationId: $newLocationId) {
    movedFulfillmentOrder { id status }
    userErrors { field message }
  }
}
"""


def fallback_store_id() -> Optional[str]:
    """ONLINE_FULFILLMENT_STORE_ID -- the ONE reader. The shop that ships an
    online order only while Shopify's assignment is unusable (unmapped
    location, unread fulfillment orders); see the module docstring."""
    return (os.getenv("ONLINE_FULFILLMENT_STORE_ID") or "").strip() or None


def shop_locations(db) -> Optional[Dict[str, str]]:
    """``{store_id: Shopify location gid}`` of every mapped physical shop (the
    ONE mapping, shopify_push.inventory._mapped over
    stores_util.physical_stores). None when it cannot be read -- never an
    empty map, which would read as "no shop is mapped"."""
    try:
        from .shopify_push.inventory import _mapped
        from .stores_util import physical_stores

        return _mapped(physical_stores(db))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] shop locations unreadable: %s", exc)
        return None


def fo_is_shops(location_id: Optional[str], shop_loc: Optional[str], mapped_locs) -> bool:
    """THE rule for "this fulfillment order is the shipping shop's to ship"
    (route_order at booking, the fulfilment push at dispatch): it sits at the
    shop's own Shopify location. A shop with no location (the
    ONLINE_FULFILLMENT_STORE_ID fallback) owns the ones at locations no shop
    maps; one at another MAPPED shop's location is never its."""
    return location_id == shop_loc if shop_loc else location_id not in mapped_locs


def _relocation_enabled() -> bool:
    """ONLINE_FULFILLMENT_FALLBACK=off pins every order to its assigned shop
    (no relocation, a short shop fails loud). Default ON."""
    return (os.getenv("ONLINE_FULFILLMENT_FALLBACK") or "on").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def _stock_by_store(db, product_id: str) -> Dict[str, int]:
    """``{store_id: CLAIMABLE units}`` of one product at every PHYSICAL shop,
    most stock first (tie: store_id). ONLINE stores are excluded: they are
    pooled and stockless, a unit parked there is a phantom nobody can ship.
    Fail-soft -> {} (logged).

    "Claimable" is the CLAIM's own rule (StockRepository.sellable_filter:
    status exactly AVAILABLE and not past its expiry -- what
    claim_one_available will actually hand this order), not the looser
    on-hand match (item_events.on_hand_match also counts a status-less,
    lowercase or expired unit). Counting with the looser rule sent orders to
    a shop whose units the claim then refused: held, while another shop
    could have shipped it."""
    from database.repositories.product_repository import StockRepository

    from .stores_util import is_online_store

    try:
        coll = (
            db.get_collection("stock_units")
            if hasattr(db, "get_collection")
            else db["stock_units"]
        )
        claimable = {
            k: v
            for k, v in StockRepository(coll).sellable_filter(product_id, None).items()
            if k != "store_id"  # every shop, grouped below
        }
        rows = coll.aggregate(
            [
                {"$match": claimable},
                {"$group": {"_id": "$store_id", "n": {"$sum": 1}}},
                {"$sort": {"n": -1, "_id": 1}},
            ]
        )
        return {
            str(r["_id"]): int(r.get("n") or 0)
            for r in rows
            if r.get("_id") and not is_online_store(db, str(r["_id"]))
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] stock lookup failed for %s: %s", product_id, exc)
        return {}


def _need(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """``{ims_product_id: qty}`` of the lines IMS claims (the same selection the
    ingest decrement uses: lines whose SKU resolved to an IMS product)."""
    out: Dict[str, int] = {}
    for it in items or []:
        pid = it.get("ims_product_id")
        if pid:
            out[pid] = out.get(pid, 0) + int(it.get("quantity") or 1)
    return out


def _problem(code: str, message: str) -> Dict[str, str]:
    return {"code": code, "message": message}


def route_order(
    db, items: List[Dict[str, Any]], routing: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """THE decision of which IMS shop ships (claims, bills, fulfils) one online
    order. ``routing`` is what ``read_routing`` stamped on the payload
    (``{"fulfillment_orders": [...]}`` / ``{"error": ...}`` / ``{"dark": ...}``;
    None when nobody read it). Returns the ``fulfillment_route`` stamped on the
    order::

        {store_id, reason, assigned_store_id, assigned_location_id,
         fulfillment_order_ids, moves, problems, hold_reason, split}

    reason: ASSIGNED | MOVED | FALLBACK | NONE. ``fulfillment_order_ids`` is
    the record of the shop's fulfillment orders at booking (None: none were
    read); the fulfilment push re-reads their locations live. ``hold_reason``
    (None or text) puts the booked order on hold: a move is pending, or a
    fulfillment order sits at another shop IMS could not move it from.
    ``split`` (None or ``[{store_id, line_item_id, qty}]``) is the per-shop
    claim plan of an order Shopify split (see ``_shopify_split``).
    Never raises."""
    from .shopify_push.inventory import _mapped
    from .stores_util import physical_stores

    routing = routing if isinstance(routing, dict) else {}
    problems: List[Dict[str, str]] = []
    try:
        shops = physical_stores(db)
    except Exception as exc:  # noqa: BLE001 -- loud below when it mattered
        shops = []
        logger.warning("[ONLINE_ROUTE] shop list unreadable: %s", exc)
        if routing.get("fulfillment_orders"):
            problems.append(_problem("ROUTING_UNREAD", f"The shop list could not be read: {exc}"))
    mapped = _mapped(shops)
    shop_of = {gid: sid for sid, gid in mapped.items()}

    fos = [
        f
        for f in routing.get("fulfillment_orders") or []
        if isinstance(f, dict) and f.get("id") and f.get("status") in _ACTIVE_FO
    ]
    if routing.get("error"):
        problems.append(_problem("ROUTING_UNREAD", str(routing["error"])))
    elif "fulfillment_orders" in routing and not fos:
        problems.append(
            _problem(
                "ROUTING_UNREAD",
                "Shopify returned no open fulfillment order for this order, so "
                "its assigned location is unknown.",
            )
        )

    # The documented fallback counts only when it names an ACTIVE physical shop
    # (the one definition, stores_util.physical_stores): a stockless ONLINE
    # store, a closed shop or a typo can never ship, and must never become the
    # seller whose GSTIN issues the invoice.
    raw_fallback = fallback_store_id()
    fallback = raw_fallback if raw_fallback in {s.get("store_id") for s in shops} else None
    assigned_loc = None
    assigned = None
    reason = "FALLBACK"
    if fos:
        primary = max(fos, key=lambda f: int(f.get("units") or 0))
        assigned_loc = primary.get("location_id")
        assigned = shop_of.get(assigned_loc)
        if assigned:
            reason = "ASSIGNED"
        else:
            problems.append(
                _problem(
                    "LOCATION_UNMAPPED",
                    f"Shopify assigned this order to location "
                    f"'{primary.get('location_name') or assigned_loc}', which no "
                    "IMS shop maps (Organization > store > Shopify location). "
                    + (
                        f"IMS used the fallback shop {fallback} "
                        "(ONLINE_FULFILLMENT_STORE_ID)."
                        if fallback
                        else "No fallback shop is set (ONLINE_FULFILLMENT_STORE_ID)."
                    ),
                )
            )
    if not assigned:
        assigned = fallback
        if raw_fallback and not fallback:
            problems.append(
                _problem(
                    "FALLBACK_INVALID",
                    f"ONLINE_FULFILLMENT_STORE_ID names '{raw_fallback}', which is not "
                    "an active physical shop (or the shop list could not be read), so "
                    "IMS did not ship or bill from it. Set it to the shop that ships "
                    "online orders.",
                )
            )

    need = _need(items)
    stock = {pid: _stock_by_store(db, pid) for pid in need}

    def covers(store_id: Optional[str]) -> bool:
        return bool(store_id) and all(
            stock[pid].get(store_id, 0) >= q for pid, q in need.items()
        )

    # Q2 + Q4: when Shopify itself split the order over several mapped shops
    # and each holds its own fulfillment orders' units, every shop ships its
    # own part -- nobody is short, so nothing moves and nothing is held.
    split = _shopify_split(fos, shop_of, items, need, stock) if need else None
    target = assigned
    # Without Shopify's fulfillment orders (dark / unread) there is no
    # routing to follow and nothing to move: IMS never guesses a seller from
    # stock counts -- the documented fallback ships it, or no shop does (loud).
    if need and fos and not split and not covers(assigned) and _relocation_enabled():
        # A move needs a destination location: only a MAPPED shop can take it.
        best = sorted(
            (s for s in mapped if s and s != assigned and covers(s)),
            key=lambda s: -sum(stock[pid].get(s, 0) for pid in need),
        )
        if best:
            target = best[0]
            reason = "MOVED"
    if not target:
        reason = "NONE"

    target_loc = mapped.get(target) if target else None
    moves: List[Dict[str, Any]] = []
    fo_ids: Optional[List[str]] = None
    hold_reason: Optional[str] = None
    if split:
        fo_ids = [f["id"] for f in fos]  # each at its own claiming shop
    elif fos and target:
        fo_ids = [f["id"] for f in fos if fo_is_shops(f.get("location_id"), target_loc, shop_of)]
        rest = [f for f in fos if f["id"] not in fo_ids]
        # Q4: a fulfillment order leaves its location only for a shop that
        # holds EVERY unit of the order (the whole order ships from there, Q2)
        # -- never INTO a short shop, and never with relocation off.
        if rest and target_loc and covers(target) and _relocation_enabled():
            moves = [
                {
                    "fulfillment_order_id": f["id"],
                    "from_location_id": f.get("location_id"),
                    "to_location_id": target_loc,
                    "status": "PLANNED",
                }
                for f in rest
            ]
            hold_reason = (
                f"IMS is moving this order's Shopify fulfillment order to {target}'s "
                "location. If this hold does not clear within minutes, move it in "
                "Shopify admin (Orders > order > Change location), then clear the hold."
            )
        elif rest:
            if not target_loc:
                why = f"{target} has no Shopify location to move it to"
            elif not _relocation_enabled():
                why = "relocation is off (ONLINE_FULFILLMENT_FALLBACK=off)"
            else:
                why = (
                    f"{target} does not hold every unit of the order and no single "
                    "mapped shop does (IMS never splits an order across shops)"
                )
            where = ", ".join(
                sorted({str(f.get("location_name") or f.get("location_id")) for f in rest})
            )
            p = _problem(
                "FO_AT_OTHER_SHOP",
                f"Shopify holds {len(rest)} fulfillment order(s) of this order at "
                f"'{where}', not at {target}, the shop IMS claimed and billed it at; "
                f"IMS did not move them because {why}. The order is on hold: move "
                "them in Shopify admin (Orders > order > Change location) or resolve "
                "the stock, then clear the hold -- otherwise the other shop can ship "
                "the same units again from Shopify.",
            )
            problems.append(p)
            hold_reason = p["message"]

    return {
        "store_id": target or None,
        "reason": reason,
        "assigned_store_id": assigned or None,
        "assigned_location_id": assigned_loc,
        "fulfillment_order_ids": fo_ids,
        "moves": moves,
        "problems": problems,
        "hold_reason": hold_reason,
        "split": split,
    }


def _shopify_split(fos, shop_of, items, need, stock) -> Optional[List[Dict[str, Any]]]:
    """Shopify split the order over SEVERAL mapped shops and every shop holds
    the claimable units of its own fulfillment orders -> the claim plan
    ``[{store_id, line_item_id, qty}]``; otherwise None (the whole-order rule
    applies). Each fulfillment order's lines come from the routing read."""
    pid_of = {
        str(it.get("shopify_line_item_id")): it.get("ims_product_id")
        for it in items or []
        if it.get("ims_product_id")
    }
    legs: Dict[str, Dict[str, int]] = {}
    for f in fos:
        shop = shop_of.get(f.get("location_id"))
        if not shop or not isinstance(f.get("lines"), list):
            return None
        leg = legs.setdefault(shop, {})
        for ln in f["lines"]:
            lid, q = str((ln or {}).get("line_item_id") or ""), int((ln or {}).get("qty") or 0)
            if lid in pid_of and q > 0:
                leg[lid] = leg.get(lid, 0) + q
    if len(legs) < 2:
        return None
    got: Dict[str, int] = {}
    for shop, leg in legs.items():
        want: Dict[str, int] = {}
        for lid, q in leg.items():
            want[pid_of[lid]] = want.get(pid_of[lid], 0) + q
        if any(stock[pid].get(shop, 0) < q for pid, q in want.items()):
            return None  # this shop is short for its own part: Q4's move rule
        for pid, q in want.items():
            got[pid] = got.get(pid, 0) + q
    if got != need:
        return None  # a claimable unit sits in no open fulfillment order
    return [
        {"store_id": shop, "line_item_id": lid, "qty": q}
        for shop, leg in legs.items()
        for lid, q in leg.items()
    ]


def split_seller_problem(
    seller_doc: Optional[Dict[str, Any]], leg_docs: List[Optional[Dict[str, Any]]]
) -> Optional[Dict[str, str]]:
    """One order, one tax invoice, one GSTIN (Q1): a Shopify split across shops
    registered under DIFFERENT GSTINs cannot be invoiced from the billing
    shop's GSTIN alone. None when every shop shares the seller's GSTIN."""
    seller = str((seller_doc or {}).get("gstin") or "").strip()
    other = sorted(
        {str((d or {}).get("gstin") or "").strip() or "(none)" for d in leg_docs} - {seller}
    )
    if not other:
        return None
    return _problem(
        "SPLIT_SELLERS",
        f"Shopify split this order across shops with different GSTINs ({seller or '(none)'} "
        f"bills it; {', '.join(other)} ship part of it), so one tax invoice cannot "
        "cover it. The order is on hold: the accountant must decide how it is "
        "invoiced, or move its fulfillment orders to one shop in Shopify admin.",
    )


def gstin_problem(store_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The shipping shop must invoice from its OWN GSTIN for its state (Q1).
    Checks the GSTIN the invoice door reads (store.gstin): present, and when
    the shop carries a state, registered in that state. None when fine."""
    from .org_validation import gstin_state_code, resolve_state_code

    doc = store_doc if isinstance(store_doc, dict) else {}
    name = doc.get("store_name") or doc.get("store_code") or doc.get("store_id") or "the shop"
    gstin = str(doc.get("gstin") or "").strip()
    if not gstin:
        return _problem(
            "SHOP_GSTIN_MISSING",
            f"{name} has no GSTIN, so the tax invoice for this online order cannot "
            "be issued from it. Set the shop's GSTIN in Organization.",
        )
    state = resolve_state_code(doc.get("state_code"), doc.get("state"))
    if state and gstin_state_code(gstin) != state:
        return _problem(
            "SHOP_GSTIN_MISSING",
            f"{name} is in state {state} but its GSTIN {gstin} is registered in "
            f"state {gstin_state_code(gstin)}. Set the shop's GSTIN for its own "
            "state in Organization.",
        )
    return None


def seller_unknown_problem(bucket_id: Optional[str]) -> Dict[str, str]:
    """No shipping shop could be named (route NONE, or routing itself failed):
    nothing is claimed and the order is HELD -- its invoice number came from
    the online bucket's series, whose GSTIN is not a shipping shop's (Q1), so
    no door issues or files a tax invoice from it. The re-issue is the
    accountant's."""
    return _problem(
        "SELLER_UNKNOWN",
        "IMS could not name the shop that ships this order, so no stock was "
        f"claimed and the order is on hold. Its invoice number was taken from "
        f"the online billing store {bucket_id}'s series, but no tax invoice can "
        "be issued from that GSTIN (owner ruling Q1). Find the shop that ships "
        "it; the accountant must then cancel this invoice number and re-issue "
        "it from that shop's GSTIN.",
    )


def seller_problem(
    order: Dict[str, Any], store_doc: Optional[Dict[str, Any]], find_store
) -> Optional[Dict[str, str]]:
    """THE seller check of a routed online order (Q1: one order, one tax
    invoice, from the shipping shop's OWN GSTIN). ONE rule for the booking
    hold (shopify_ingest), the invoice door (JSON + PDF), the delivery
    challan, the e-invoice and GSTR-1 -- so no door issues or files what the
    booking held. None when fine, and for an order never routed (POS, a
    historical import: their doors keep their own rules).

      * no shop named (route NONE) -> SELLER_UNKNOWN;
      * the seller AND every shop shipping a leg of Shopify's split invoice
        from their OWN GSTIN for their own state (gstin_problem): goods
        leaving a Maharashtra shop need a Maharashtra registration;
      * every leg shop shares the seller's GSTIN (split_seller_problem).

    ``store_doc`` is the order's own shop (order.store_id), whose ``gstin`` is
    the GSTIN every one of those doors issues from; ``find_store(id)`` reads a
    leg shop. A leg that cannot be read is not provably fine: a problem."""
    route = order.get("fulfillment_route")
    if not isinstance(route, dict):
        return None
    if not route.get("store_id"):
        return seller_unknown_problem(order.get("store_id"))
    leg_docs = []
    for sid in sorted(
        {r.get("store_id") for r in route.get("split") or [] if isinstance(r, dict)}
        - {order.get("store_id"), None}
    ):
        try:
            doc = find_store(sid)
        except Exception:  # noqa: BLE001 -- unreadable = not provably fine
            doc = None
        leg_docs.append(doc or {"store_id": sid})
    return (
        gstin_problem(store_doc)
        or next((p for p in map(gstin_problem, leg_docs) if p), None)
        or split_seller_problem(store_doc, leg_docs)
    )


def raise_problem_tasks(db, order: Dict[str, Any]) -> None:
    """One deduped P1 task per (order, problem code). Fail-soft."""
    route = order.get("fulfillment_route") or {}
    problems = route.get("problems") or []
    if not problems:
        return
    order_id = order.get("order_id")
    ref = order.get("order_number") or order_id
    try:
        from ..dependencies import get_task_repository
        from .task_triggers import create_system_task

        repo = get_task_repository()
        for p in problems:
            create_system_task(
                repo,
                title=f"Online order {ref}: {p['code'].replace('_', ' ').lower()}",
                description=p["message"],
                priority="P1",
                category="ONLINE_ORDER",
                store_id=route.get("store_id"),
                dedupe_ref=f"online_route:{p['code']}:{order_id}",
                extra={"link": "/orders", "payload": {"order_id": order_id}},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] problem task skipped for %s: %s", order_id, exc)


# ---------------------------------------------------------------------------
# Shopify side: the read and the move (GATED like every other writer)
# ---------------------------------------------------------------------------


async def read_routing(db, shopify_order_id: str) -> Dict[str, Any]:
    """Read the order's fulfillment orders + assigned locations. DARK ->
    ``{"dark": reason}`` with ZERO network; a failed read -> ``{"error": ...}``.
    Never raises."""
    from agents.nexus_providers import _as_shopify_gid

    from . import shopify_push

    live, reason = shopify_push._live_or_reason(db)
    if not live:
        return {"dark": reason}
    try:
        body = await shopify_push._graphql(
            db, _ROUTING_QUERY, {"id": _as_shopify_gid(shopify_order_id, "Order")}
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Reading the order's fulfillment orders failed: {exc}"}
    err = shopify_push._user_errors(body, "order")
    order = ((body or {}).get("data") or {}).get("order")
    if err or not isinstance(order, dict):
        return {"error": f"Reading the order's fulfillment orders failed: {err or 'order not found'}"}
    fos: List[Dict[str, Any]] = []
    for n in (order.get("fulfillmentOrders") or {}).get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        assigned = n.get("assignedLocation") or {}
        loc = (assigned.get("location") or {}).get("id")
        fos.append(
            {
                "id": n["id"],
                "status": str(n.get("status") or "").upper(),
                "location_id": _as_shopify_gid(loc, "Location") if loc else None,
                "location_name": assigned.get("name"),
                "units": sum(
                    int((li or {}).get("remainingQuantity") or 0)
                    for li in (n.get("lineItems") or {}).get("nodes") or []
                ),
                # Which order lines this fulfillment order carries (a Shopify
                # split puts different lines at different shops).
                "lines": [
                    {
                        "line_item_id": str(((li or {}).get("lineItem") or {}).get("id") or "")
                        .rsplit("/", 1)[-1],
                        "qty": int((li or {}).get("remainingQuantity") or 0),
                    }
                    for li in (n.get("lineItems") or {}).get("nodes") or []
                ],
            }
        )
    return {"fulfillment_orders": fos}


def _orders(db):
    return db.get_collection("orders") if hasattr(db, "get_collection") else db["orders"]


async def move_fulfillment_orders(db, order_id: str) -> Dict[str, Any]:
    """Send every PLANNED fulfillmentOrderMove on the order's route, record the
    moved fulfillment order as the shipping shop's, and fail LOUD (MOVE_FAILED
    problem + task, the order stays held) on anything not moved; all moved
    lifts the pending-move hold route_order put on the order. Then the Shopify
    stock write-back, AFTER the moves: inventorySetQuantities writes absolute
    per-location numbers, and a move that landed after it would shift the
    committed unit and leave the old shop one phantom unit high. Idempotent
    (only PLANNED moves are sent; they are claimed first, so two deliveries of
    the same order never send one move twice). A planned move that is no longer
    wanted (``_stale_move``) is SKIPPED, never sent. Never raises."""
    from . import shopify_push

    try:
        order = _orders(db).find_one({"order_id": order_id}) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] order read failed for %s: %s", order_id, exc)
        return {"moved": 0, "failed": 0}
    route = dict(order.get("fulfillment_route") or {})
    moves = [dict(m) for m in route.get("moves") or []]
    planned = [m for m in moves if m.get("status") == "PLANNED"]
    if not planned:
        return {"moved": 0, "failed": 0}
    # The booking's claim has not settled yet (shopify_ingest stamps
    # fulfillment_breakdown, possibly [], once it has): a duplicate delivery
    # racing the creator between its insert and its claim. Leave the moves
    # PLANNED -- judged now they would be SKIPPED as short-claimed and the
    # stock written back before the claim; the creator sends them (and writes
    # stock back) right after its claim.
    # ponytail: a creator that dies before settling leaves them PLANNED, the
    # order held under the pending-move text; add a lease if that happens.
    if "fulfillment_breakdown" not in order:
        return {"moved": 0, "failed": 0}
    stale = _stale_move(order)
    try:
        claimed = _orders(db).update_one(
            {"order_id": order_id, "fulfillment_route.moves": route.get("moves")},
            {"$set": {"fulfillment_route.moves": [
                ({**m, "status": "SKIPPED", "error": stale} if stale else {**m, "status": "SENDING"})
                if m in planned
                else m
                for m in moves
            ]}},
        )
        # ponytail: a crash between this claim and the write below leaves the
        # moves SENDING (never retried) -- the order stays held under the
        # pending-move reason, loud on the orders screen; add a lease time if
        # that ever happens.
        if not getattr(claimed, "modified_count", 0):
            return {"moved": 0, "failed": 0}  # another delivery is sending them
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] move claim failed for %s: %s", order_id, exc)
        return {"moved": 0, "failed": 0}
    if stale:
        logger.warning("[ONLINE_ROUTE] move skipped for %s: %s", order_id, stale)
        _stock_write_back(db, order)  # the booking deferred it to the move
        return {"moved": 0, "failed": 0, "skipped": len(planned)}
    live, reason = shopify_push._live_or_reason(db)
    fo_ids = list(route.get("fulfillment_order_ids") or [])
    failed = []
    for m in planned:
        err = None if live else f"not sent: {reason}"
        body: Dict[str, Any] = {}
        if live:
            try:
                body = await shopify_push._graphql(
                    db,
                    _MOVE_MUTATION,
                    {"id": m["fulfillment_order_id"], "newLocationId": m["to_location_id"]},
                )
                err = shopify_push._user_errors(body, "fulfillmentOrderMove")
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
        if err:
            m.update(status="FAILED", error=err)
            failed.append(m)
            continue
        moved = ((body.get("data") or {}).get("fulfillmentOrderMove") or {}).get(
            "movedFulfillmentOrder"
        ) or {}
        m.update(status="MOVED", moved_fulfillment_order_id=moved.get("id") or m["fulfillment_order_id"])
        fo_ids.append(m["moved_fulfillment_order_id"])
    route["moves"] = moves
    route["fulfillment_order_ids"] = fo_ids
    pending = route.get("hold_reason")
    if failed:
        problem = _problem(
            "MOVE_FAILED",
            f"IMS could not move {len(failed)} fulfillment order(s) to the "
            f"shipping shop's Shopify location ({failed[0].get('error')}). The "
            "order is on hold: move it in Shopify admin (Orders > order > Change "
            "location), then clear the hold -- until then Shopify has it (and "
            "the unit it committed) at the other shop.",
        )
        route["problems"] = list(route.get("problems") or []) + [problem]
        route["hold_reason"] = problem["message"]
        # Keep (or put) the order on hold -- unless another hold (a stock
        # miss, a seller GSTIN) already owns the reason, which stays.
        holds = [
            ({"stock_hold_reason": {"$in": [None, pending]}},
             {"$set": {"fulfillment_hold": True, "stock_hold_reason": problem["message"]}}),
        ]
    else:
        route["hold_reason"] = None
        # Lift only OUR pending-move hold, deciding the Rx part on the order
        # AS STORED NOW (an admin may clear the Rx hold while the move is on
        # the wire): still Rx-pending -> stays held, else released.
        holds = [
            ({"stock_hold_reason": pending, "rx_pending": {"$ne": True}},
             {"$set": {"fulfillment_hold": False}, "$unset": {"stock_hold_reason": ""}}),
            ({"stock_hold_reason": pending}, {"$unset": {"stock_hold_reason": ""}}),
        ] if pending else []
    try:
        _orders(db).update_one({"order_id": order_id}, {"$set": {"fulfillment_route": route}})
        for flt, upd in holds:
            _orders(db).update_one({"order_id": order_id, **flt}, upd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] route write-back failed for %s: %s", order_id, exc)
    if failed:
        # Only the NEW problem: the booking-time ones were tasked at booking,
        # and a human may already have closed them.
        raise_problem_tasks(db, {**order, "fulfillment_route": {**route, "problems": [problem]}})
    _stock_write_back(db, order)
    return {"moved": len(planned) - len(failed), "failed": len(failed)}


def _stale_move(order: Dict[str, Any]) -> Optional[str]:
    """Why a PLANNED move must no longer be sent (None: send it). A move is
    only ever retried for a live order that is still held and whose shipping
    shop really claimed every unit: never for a cancelled order, never for
    one a human already handled (the hold text tells them to move it by hand
    and clear the hold), and never INTO a shop whose claim came up short (a
    concurrent sale took the unit between the routing count and the claim --
    the stock-miss hold is already loud)."""
    status = str(order.get("status") or "").upper()
    if status in ("CANCELLED", "REFUNDED"):
        return f"the order is {status}"
    if not order.get("fulfillment_hold"):
        return "the order's hold was cleared, so a human handled it"
    got: Dict[str, int] = {}
    for r in order.get("fulfillment_breakdown") or []:
        if isinstance(r, dict) and r.get("store_id") == order.get("store_id"):
            got[r.get("product_id")] = got.get(r.get("product_id"), 0) + int(r.get("qty") or 0)
    if any(got.get(pid, 0) < q for pid, q in _need(order.get("items") or []).items()):
        return (
            f"{order.get('store_id')} could not claim every unit (stock-miss hold), "
            "and IMS never moves an order into a short shop"
        )
    return None


def _stock_write_back(db, order: Dict[str, Any]) -> None:
    try:
        from .online_stock_writeback import writeback_after_sale

        writeback_after_sale(db, order.get("items") or [], order.get("store_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ONLINE_ROUTE] stock write-back skipped for %s: %s", order.get("order_id"), exc
        )


async def map_routed_order(
    payload: Dict[str, Any],
    db,
    *,
    webhook_id: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """THE door every live online-order create goes through (webhook drain,
    missed-webhook pull, Re-map): read Shopify's routing FRESH for an order
    IMS has not booked yet (the read always overwrites any stamp a stored
    payload carries), hand it to the (sync) mapper -> ingest, then send the
    planned moves -- for an order already booked too (a replayed or
    orders/updated delivery), so moves a crash left PLANNED are retried by
    the next delivery. Returns the mapper's result. Never raises."""
    from . import online_order_mapper
    from .shopify_ingest import order_payload_refusal

    payload = payload if isinstance(payload, dict) else {}
    sid = str(payload.get("id") or "").strip()
    try:
        if (
            db is not None
            and sid
            and payload.get("line_items")
            and not order_payload_refusal(payload, booking=True)
            and _orders(db).find_one({"shopify_order_id": sid}) is None
        ):
            payload["_ims_routing"] = await read_routing(db, sid)
    except Exception as exc:  # noqa: BLE001
        payload["_ims_routing"] = {"error": f"Routing read failed: {exc}"}
    result = online_order_mapper.map_shopify_order(
        payload, db, webhook_id=webhook_id, topic=topic
    )
    if (result or {}).get("status") in ("created", "duplicate", "replayed") and result.get(
        "order_id"
    ):
        try:
            await move_fulfillment_orders(db, result["order_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ONLINE_ROUTE] moves skipped for %s: %s", sid, exc)
    return result
