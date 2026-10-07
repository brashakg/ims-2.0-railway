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
    shopify split  = Shopify split the order over several mapped shops: every
                     shop claims and ships its own part (``split``; Q2 allows
                     Shopify's split). Only a leg shop SHORT for its own part
                     is acted on (Q4): its leg moves to a mapped shop holding
                     it that passes the seller check (``_follow_split``),
                     else its claim fails loud at that
                     shop -- a leg that holds its units is never touched, and
                     a line IMS does not stock never moves anything. The shop
                     shipping the largest fulfillment order bills it; a leg
                     shop under another GSTIN or without its own state's
                     GSTIN holds the order (``seller_problem``, the one seller
                     check), released by Re-map (``reroute_held_order``).
    shipping shop  = otherwise assigned if it covers (the fallback also only
                     if it passes the seller check: the order moves INTO it),
                     else the first MAPPED shop
                     that covers the whole order AND passes the seller check
                     (IMS never moves an order into a hold it creates; the
                     shop holding the order's own units first on a Re-map,
                     then the most stock), else the
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
                     into a short shop) and passes the seller check, it
                     carries an item IMS stocks, and relocation is on -- one
                     by one: a sibling carrying no IMS item never keeps it
                     back. One that cannot be moved is FO_AT_OTHER_SHOP: loud
                     + the order is held (a move lifts only its own hold). While a move
                     is pending the order is held too; the move lifts that
                     hold, a failed move keeps it (MOVE_FAILED, released by
                     Re-map, which re-reads the routing and sends the move
                     again -- never changing the order's seller, invoice or
                     tax, ``seller_change``); a move for a
                     cancelled or human-released order is SKIPPED, never sent.
                     A target whose claim came up short (a race after the
                     count) still gets its move: the claim and the invoice
                     are there, so the fulfillment order follows them.

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


def usable_fallback(shops: List[Dict[str, Any]]) -> Optional[str]:
    """THE answer to "which shop is the fallback": ONLINE_FULFILLMENT_STORE_ID
    when it names an ACTIVE physical shop (``shops``: stores_util
    .physical_stores), else none -- a stockless ONLINE store, a closed shop or
    a typo can never ship, and must never become the seller whose GSTIN
    issues the invoice. route_order ships from it; the sync-health tile
    reports it (never a shop route_order would not use)."""
    raw = fallback_store_id()
    return raw if raw in {s.get("store_id") for s in shops or []} else None


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


def _ident(p: Dict[str, Any]) -> tuple:
    """What a route problem is ABOUT, for "the order already had it" (a human
    may have closed its task): its code and subject -- a MOVE_FAILED is its
    fulfillment orders and target shop, never Shopify's wording of the error."""
    return (p.get("code"), p.get("about") or p.get("message"))


def _tasked(route: Optional[Dict[str, Any]]) -> set:
    """The ``_ident`` of every problem the order carried before its last
    Re-map (kept on the route as ``tasked``): tasked once already, so never
    again -- by Re-map or by any other sender of the same move."""
    return {tuple(x) for x in (route or {}).get("tasked") or []}


def _open_fos(routing: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The fulfillment orders of a routing read that still need shipping --
    what route_order routes on, and what Re-map requires before it touches
    an order (none: fulfilled or closed in Shopify, or not read)."""
    return [
        f
        for f in (routing or {}).get("fulfillment_orders") or []
        if isinstance(f, dict) and f.get("id") and f.get("status") in _ACTIVE_FO
    ]


def _gstin(doc: Optional[Dict[str, Any]]) -> str:
    return str((doc or {}).get("gstin") or "").strip()


def _name(doc: Optional[Dict[str, Any]]) -> str:
    doc = doc or {}
    return doc.get("store_name") or doc.get("store_code") or doc.get("store_id") or "the shop"


def route_order(
    db,
    items: List[Dict[str, Any]],
    routing: Optional[Dict[str, Any]],
    held: Optional[Dict[str, Dict[str, int]]] = None,
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
    plan of an order Shopify split: EVERY line, at the shop that ships it
    (``_shopify_split`` + ``_follow_split``). ``held`` (Re-map only:
    ``{product_id: {store_id: n}}``) is the units the order itself holds SOLD
    -- they count as claimable for it, so a Re-map routes without first
    handing them back to the shelf, and a move prefers the shop already
    holding them (a Re-map retrying a failed move resends it there, never
    hands the packed unit to another shop) -- one holding more of them than
    the short assigned shop keeps them even when no shop covers. Nothing
    ever moves INTO a shop that fails the seller check (``fits``), at booking
    or Re-map: IMS never creates the hold itself -- a short shop stays short
    and its claim names the miss.
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
    doc_of = {s.get("store_id"): s for s in shops}

    def fits(store_id: Optional[str], gstin: Optional[str] = None) -> bool:
        """The shop passes the seller check's GSTIN rule (its own state's
        GSTIN; a split leg also shares the billing shop's GSTIN). A move
        prefers such a shop: IMS never holds an order for a shop it chose
        while a clean shop could take it."""
        doc = doc_of.get(store_id)
        return gstin_problem(doc) is None and (gstin is None or _gstin(doc) == gstin)

    fos = _open_fos(routing)
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

    # The documented fallback counts only when it names an ACTIVE physical shop.
    raw_fallback = fallback_store_id()
    fallback = usable_fallback(shops)
    assigned_loc = None
    assigned = None
    reason = "FALLBACK"
    unmapped = None  # (its place in problems, the location's name)
    if fos:
        primary = max(fos, key=lambda f: int(f.get("units") or 0))
        assigned_loc = primary.get("location_id")
        assigned = shop_of.get(assigned_loc)
        if assigned:
            reason = "ASSIGNED"
        else:
            # Worded once the shop that ships it is known (below): the
            # fallback may be short, and the order move to a mapped shop.
            unmapped = (len(problems), primary.get("location_name") or assigned_loc)
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
    for pid, at in (held or {}).items():
        for sid, n in at.items():
            if pid in stock:
                stock[pid][sid] = stock[pid].get(sid, 0) + int(n)
    pid_of = {
        str(it.get("shopify_line_item_id")): it.get("ims_product_id")
        for it in items or []
        if it.get("ims_product_id")
    }

    def covers(store_id: Optional[str]) -> bool:
        return bool(store_id) and all(
            stock[pid].get(store_id, 0) >= q for pid, q in need.items()
        )

    def own(store_id: Optional[str]) -> int:
        """The order's own units held at the shop (Re-map; 0 at booking)."""
        return sum(((held or {}).get(pid) or {}).get(store_id, 0) for pid in need)

    # Q2 + Q4: Shopify itself split the order over several mapped shops. Every
    # shop claims and ships its own part; only a shop SHORT for its own part
    # hands it on (_follow_split) -- a leg that holds its units is never
    # touched, and an order with no IMS line moves nothing.
    legs = _shopify_split(fos, shop_of, pid_of, need)
    split: Optional[List[Dict[str, Any]]] = None
    target = assigned
    # Without Shopify's fulfillment orders (dark / unread) there is no
    # routing to follow and nothing to move: IMS never guesses a seller from
    # stock counts -- the documented fallback ships it, or no shop does (loud).
    # The fallback is not Shopify's pick: the order moves INTO it, so one
    # failing the seller check yields to a clean shop, like a short one.
    into = reason == "FALLBACK" and bool(mapped.get(assigned))
    if (need and fos and not legs and _relocation_enabled()
            and (not covers(assigned) or (into and not fits(assigned)))):
        # A move needs a destination location: only a MAPPED shop passing the
        # seller check can take it; one that covers first, then the one
        # holding the order's own units, then the most stock. None covers: a
        # shop holding more of its own units than the assigned one keeps them.
        best = sorted(
            (s for s in mapped if s and s != assigned and fits(s)
             and (covers(s) or own(s) > own(assigned))),
            key=lambda s: (not covers(s), -own(s), -sum(stock[pid].get(s, 0) for pid in need)),
        )
        if best:
            target = best[0]
            reason = "MOVED"
    if not target:
        reason = "NONE"
    if unmapped:
        at, where = unmapped
        if target and target == fallback:
            ships = f"IMS used the fallback shop {fallback} (ONLINE_FULFILLMENT_STORE_ID)."
        elif target:
            ships = (
                f"The fallback shop {fallback} (ONLINE_FULFILLMENT_STORE_ID) "
                + ("fails the seller (GSTIN) check" if covers(fallback) else "does not hold every unit")
                + ", so "
                if fallback
                else "No fallback shop is set (ONLINE_FULFILLMENT_STORE_ID), so "
            ) + f"IMS ships it from {target}, a mapped shop that " + (
                "holds every unit." if covers(target) else "already holds some of its units."
            )
        else:
            ships = "No fallback shop is set (ONLINE_FULFILLMENT_STORE_ID)."
        problems.insert(at, _problem(
            "LOCATION_UNMAPPED",
            f"Shopify assigned this order to location '{where}', which no IMS shop "
            f"maps (Organization > store > Shopify location). {ships}",
        ))

    target_loc = mapped.get(target) if target else None
    moves: List[Dict[str, Any]] = []
    fo_ids: Optional[List[str]] = None
    hold_reason: Optional[str] = None
    if legs:
        home = _gstin(doc_of.get(assigned))  # the largest leg's shop bills
        ships = _follow_split(legs, pid_of, stock, mapped, lambda t: fits(t, home))
        target = ships[assigned]  # the largest fulfillment order's shop bills
        reason = "ASSIGNED" if target == assigned else "MOVED"
        split = [
            {"store_id": ships[shop], "line_item_id": lid, "qty": q}
            for shop, leg in legs.items()
            for lid, q in leg.items()
        ]
        # The fulfillment orders already at the shop that claims their lines;
        # a moved one is added once Shopify takes the move. One a failed move
        # left behind is not its shop's, so the dispatch re-asserts stock
        # once a human moves it (shopify_fulfillment_push).
        leg_of = {f["id"]: ships[shop_of[f.get("location_id")]] for f in fos}
        fo_ids = [f["id"] for f in fos if leg_of[f["id"]] == shop_of[f.get("location_id")]]
        moves = [
            {
                "fulfillment_order_id": f["id"],
                "from_location_id": f.get("location_id"),
                "to_location_id": mapped[leg_of[f["id"]]],
                "to_store_id": leg_of[f["id"]],
                "status": "PLANNED",
            }
            for f in fos
            if f["id"] not in fo_ids
        ]
        if moves:
            hold_reason = _pending_move_text(
                sorted({shop_of.get(m["to_location_id"]) or "" for m in moves})
            )
    elif fos and target:
        fo_ids = [f["id"] for f in fos if fo_is_shops(f.get("location_id"), target_loc, shop_of)]
        rest = [f for f in fos if f["id"] not in fo_ids]
        # Q4, fulfillment order by fulfillment order: one leaves its location
        # only for a shop that holds EVERY unit of the order (the whole order
        # ships from there, Q2) and passes the seller check -- never INTO a
        # short shop or one failing it, never with relocation off, and never
        # one that carries no item IMS stocks (no shop is short on it; its
        # sibling carrying the IMS item still follows the claim).
        ours = [f for f in rest if need and _carries_ims(f, pid_of)]
        movable = bool(target_loc and covers(target) and fits(target) and _relocation_enabled())
        if ours and movable:
            moves = [
                {
                    "fulfillment_order_id": f["id"],
                    "from_location_id": f.get("location_id"),
                    "to_location_id": target_loc,
                    "to_store_id": target,
                    "status": "PLANNED",
                }
                for f in ours
            ]
            hold_reason = _pending_move_text([target])
        left = [f for f in rest if not (movable and f in ours)]
        if left:
            why = []
            if any(f in ours for f in left):
                if not target_loc:
                    why.append(f"{target} has no Shopify location to move it to")
                elif not _relocation_enabled():
                    why.append("relocation is off (ONLINE_FULFILLMENT_FALLBACK=off)")
                elif not fits(target):
                    why.append(f"{target} fails the seller (GSTIN) check")
                else:
                    why.append(
                        f"{target} does not hold every unit of the order and no single "
                        "mapped shop does (IMS never splits an order across shops)"
                    )
            if not all(f in ours for f in left):
                why.append(
                    ("the others carry" if why else "they carry")
                    + " no item IMS stocks, so no shop is short on them "
                    "(IMS moves a fulfillment order only off a short shop)"
                )
            where = ", ".join(
                sorted({str(f.get("location_name") or f.get("location_id")) for f in left})
            )
            p = _problem(
                "FO_AT_OTHER_SHOP",
                f"Shopify holds {len(left)} fulfillment order(s) of this order at "
                f"'{where}', not at {target}, the shop IMS claimed and billed it at; "
                f"IMS did not move them because {'; '.join(why)}. The order is on hold: move "
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


def _pending_move_text(shops: List[str]) -> str:
    """The pending-move hold text (a successful move lifts exactly this)."""
    return (
        f"IMS is moving this order's Shopify fulfillment order to "
        f"{', '.join(shops)}'s location. If this hold does not clear within "
        "minutes, move it in Shopify admin (Orders > order > Change location), "
        "then clear the hold."
    )


def _carries_ims(fo: Dict[str, Any], pid_of: Dict[str, Any]) -> bool:
    """The fulfillment order carries an order line IMS stocks. Lines not read
    (an anonymous line) count as IMS's -- IMS cannot tell otherwise."""
    lines = fo.get("lines")
    if not isinstance(lines, list):
        return True
    return any(
        not (ln or {}).get("line_item_id") or str(ln["line_item_id"]) in pid_of
        for ln in lines
    )


def _shopify_split(fos, shop_of, pid_of, need) -> Optional[Dict[str, Dict[str, int]]]:
    """Shopify split the order over SEVERAL mapped shops -> its legs
    ``{shop: {line_item_id: qty}}`` (EVERY line, IMS stock or not, so the
    seller check and the dispatch see every shop that ships a part);
    otherwise None (the whole-order rule applies): a fulfillment order at an
    unmapped location, a line the routing read could not name, or a
    claimable unit in no open fulfillment order."""
    legs: Dict[str, Dict[str, int]] = {}
    for f in fos:
        shop = shop_of.get(f.get("location_id"))
        if not shop or not isinstance(f.get("lines"), list):
            return None
        leg = legs.setdefault(shop, {})
        for ln in f["lines"]:
            lid, q = str((ln or {}).get("line_item_id") or ""), int((ln or {}).get("qty") or 0)
            if not lid:
                return None
            if q > 0:
                leg[lid] = leg.get(lid, 0) + q
    if len(legs) < 2:
        return None
    got: Dict[str, int] = {}
    for leg in legs.values():
        for lid, q in leg.items():
            if lid in pid_of:
                got[pid_of[lid]] = got.get(pid_of[lid], 0) + q
    return legs if got == need else None


def _follow_split(legs, pid_of, stock, mapped, fits) -> Dict[str, str]:
    """Q4 on Shopify's split: ``{leg shop: the shop that claims and ships
    that leg}``. A leg shop holding its own part ships it. A leg shop SHORT
    for its own part hands the whole leg (its fulfillment orders move) to ONE
    mapped shop that holds it on top of what that shop already ships and
    ``fits`` the seller check (its own state's GSTIN, the billing shop's
    GSTIN) -- a shop already in the order first (Q2: fewest locations), then
    the most stock -- never INTO a short shop or one failing ``fits`` (IMS
    never creates a seller hold itself). None does, or relocation is off: the
    leg stays, and its claim fails loud AT that shop."""
    want: Dict[str, Dict[str, int]] = {}
    for shop, leg in legs.items():
        w = want.setdefault(shop, {})
        for lid, q in leg.items():
            if lid in pid_of:
                w[pid_of[lid]] = w.get(pid_of[lid], 0) + q
    load = {s: dict(w) for s, w in want.items()}  # the IMS units each shop ships
    ships = {s: s for s in legs}

    def holds(shop: str, extra: Dict[str, int]) -> bool:
        have = load.get(shop) or {}
        return all(
            stock[p].get(shop, 0) >= have.get(p, 0) + extra.get(p, 0)
            for p in set(have) | set(extra)
        )

    for s in sorted(legs, key=lambda s: (-sum(legs[s].values()), s)):
        if holds(s, {}) or not _relocation_enabled():
            continue
        cands = [t for t in mapped if t and t != s and holds(t, want[s]) and fits(t)]
        if not cands:
            continue
        t = min(cands, key=lambda t: (not load.get(t), -sum(stock[p].get(t, 0) for p in want[s]), t))
        for p, q in want[s].items():
            load.setdefault(t, {})[p] = load.get(t, {}).get(p, 0) + q
        load[s] = {}
        ships[s] = t
    return ships


def split_seller_problem(
    seller_doc: Optional[Dict[str, Any]], leg_docs: List[Optional[Dict[str, Any]]]
) -> Optional[Dict[str, str]]:
    """One order, one tax invoice, one GSTIN (Q1): a Shopify split across shops
    registered under DIFFERENT GSTINs cannot be invoiced from the billing
    shop's GSTIN alone. None when every shop shares the seller's GSTIN."""
    seller = _gstin(seller_doc)
    other = sorted({_gstin(d) or "(none)" for d in leg_docs} - {seller})
    if not other:
        return None
    return _problem(
        "SPLIT_SELLERS",
        f"Shopify split this order across shops with different GSTINs ({seller or '(none)'} "
        f"bills it; {', '.join(other)} ship part of it), so one tax invoice cannot "
        "cover it. The order is on hold: move its fulfillment orders to shops "
        "under one GSTIN in Shopify admin (Orders > order > Change location), "
        "then press Re-map on the Online orders screen -- IMS re-reads the "
        "routing, claims its stock again and lifts the hold (its seller, "
        "invoice and tax never change).",
    )


def _gstin_fault(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why a shop has no GSTIN of its OWN state (None: it has). The shop's
    state is org_validation.shop_state_code -- the read the GST split uses."""
    from .org_validation import gstin_state_code, shop_state_code

    gstin = _gstin(doc)
    if not gstin:
        return "has no GSTIN"
    state = shop_state_code(doc)
    if state and gstin_state_code(gstin) != state:
        return (
            f"is in state {state} but its GSTIN {gstin} is registered in "
            f"state {gstin_state_code(gstin)}"
        )
    return None


def gstin_problem(store_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The shipping shop must invoice from its OWN GSTIN for its state (Q1).
    Checks the GSTIN the invoice door reads (store.gstin): present, and
    registered in the shop's own state. None when fine."""
    fault = _gstin_fault(store_doc)
    if not fault:
        return None
    name = _name(store_doc)
    if fault == "has no GSTIN":
        return _problem(
            "SHOP_GSTIN_MISSING",
            f"{name} has no GSTIN, so the tax invoice for this online order cannot "
            "be issued from it. Set the shop's GSTIN in Organization.",
        )
    return _problem(
        "SHOP_GSTIN_MISSING",
        f"{name} {fault}. Set the shop's GSTIN for its own state in Organization.",
    )


def _leg_gstin_problem(
    leg_doc: Optional[Dict[str, Any]], seller_doc: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """gstin_problem for a shop shipping a LEG of Shopify's split, worded for
    what it is: the tax invoice is the BILLING shop's; the leg lacks a
    registration of its own state for the goods leaving it."""
    from .org_validation import gstin_state_code, shop_state_code

    fault = _gstin_fault(leg_doc)
    if not fault:
        return None
    leg, seller, sg = _name(leg_doc), _name(seller_doc), _gstin(seller_doc) or "(none)"
    # A GSTIN is one state's: a leg in another state can never be under the
    # seller's, so only the move can fix it.
    state = shop_state_code(leg_doc)
    fix = (
        f"if {leg} is registered under {sg}, set that GSTIN on {leg} in "
        "Organization and clear the hold; otherwise "
        if not state or state == gstin_state_code(sg)
        else ""
    )
    return _problem(
        "SHOP_GSTIN_MISSING",
        f"{leg} ships part of this order and {fault}. The tax invoice is "
        f"{seller}'s ({sg}), so the goods leaving {leg} must be covered by that "
        f"registration. The order is on hold: {fix}move its fulfillment order to "
        f"a shop under {sg} in Shopify admin (Orders > order > Change location) "
        "and press Re-map on the Online orders screen.",
    )


def seller_unknown_problem(bucket_id: Optional[str]) -> Dict[str, str]:
    """No shipping shop could be named (route NONE, or routing itself failed):
    nothing is claimed and the order is HELD -- its invoice number came from
    the online bucket's series, whose GSTIN is not a shipping shop's (Q1), so
    no door issues or files a tax invoice from it. IMS never changes an
    order's seller (``seller_change``): the way out is the accountant's."""
    return _problem(
        "SELLER_UNKNOWN",
        "IMS could not name the shop that ships this order, so no stock was "
        f"claimed and the order is on hold. Its invoice number was taken from "
        f"the online billing store {bucket_id}'s series, but no tax invoice can "
        "be issued from that GSTIN (owner ruling Q1), and IMS never changes an "
        "order's seller, invoice or tax: issue a credit note against it and "
        "re-book the order through the normal doors.",
    )


SELLER_CODES = ("SELLER_UNKNOWN", "SHOP_GSTIN_MISSING", "SPLIT_SELLERS")


def stored_seller_problem(
    order: Optional[Dict[str, Any]], find_store=None, cause_only: bool = False
) -> Optional[Dict[str, str]]:
    """``seller_problem`` for a STORED order, reading its shops through
    ``find_store`` (default: the store repository) -- for the doors that
    hold no store docs: the dispatch gate (orders.assert_no_active_rx_hold),
    the hold release (``cause_only``: is the CAUSE fixed?), the fulfilment
    push, GSTR-1/3B, Tally and the credit-note passes. None for an order
    never routed."""
    if not isinstance((order or {}).get("fulfillment_route"), dict):
        return None
    if find_store is None:
        try:
            from ..dependencies import get_store_repository

            find_store = getattr(get_store_repository(), "find_by_id", None)
        except Exception:  # noqa: BLE001 -- unreadable = not provably fine
            find_store = None
    finder = find_store or (lambda _sid: None)
    try:
        store_doc = finder(order.get("store_id"))
    except Exception:  # noqa: BLE001
        store_doc = None
    return seller_problem(order, store_doc, finder, cause_only)


def _held_on(order: Optional[Dict[str, Any]], codes) -> bool:
    """The order's hold is the one a route problem with one of ``codes`` put
    on it (the stock-hold reason IS that problem's text)."""
    order = order or {}
    route = order.get("fulfillment_route")
    if not order.get("fulfillment_hold") or not isinstance(route, dict):
        return False
    return order.get("stock_hold_reason") in {
        p.get("message") for p in route.get("problems") or []
        if isinstance(p, dict) and p.get("code") in codes
    }


# Stamped on the route by clear-hold when it releases a seller hold.
SELLER_RELEASED = "seller_released_at"


def seller_held(order: Optional[Dict[str, Any]]) -> bool:
    """THE verdict every door reads: the booking put the order on its seller
    hold (shopify_ingest stamps the SELLER_CODES problem on its route; a
    Re-map writes a route that never carries one) and clear-hold has not
    released it (SELLER_RELEASED). Read off the order -- never re-judged on
    today's shop records, never off the hold flags another door may clear:
    no door issues, files or ships its tax invoice until clear-hold or Re-map
    lifts it."""
    route = (order or {}).get("fulfillment_route")
    return (
        isinstance(route, dict)
        and not route.get(SELLER_RELEASED)
        and any(
            isinstance(p, dict) and p.get("code") in SELLER_CODES
            for p in route.get("problems") or []
        )
    )


def reroutable(order: Optional[Dict[str, Any]]) -> bool:
    """The holds Re-map releases (reroute_held_order): a seller hold, and a
    failed fulfillment-order move -- the move's only other door is a human
    move in Shopify admin, which Shopify may refuse for good."""
    return seller_held(order) or _held_on(order, ("MOVE_FAILED",))


def remappable(order: Optional[Dict[str, Any]]) -> bool:
    """THE orders the Re-map door re-routes (``reroute_held_order``, no
    webhook payload needed) and the list offers it for: held on a hold
    Re-map releases (``reroutable``), or a Re-map is running on it or
    stopped mid-way (its lease, taken over once stale -- the crashed one's
    write may have lifted the hold before its claim settled)."""
    return reroutable(order) or bool((order or {}).get("reroute_lease_at"))


def seller_problem(
    order: Dict[str, Any],
    store_doc: Optional[Dict[str, Any]],
    find_store,
    cause_only: bool = False,
) -> Optional[Dict[str, str]]:
    """THE seller check of a routed online order (Q1: one order, one tax
    invoice, from the shipping shop's OWN GSTIN). None when fine, and for an
    order never routed (POS, a historical import: their doors keep their own
    rules). The causes (``_seller_fault``):

      * no shop named (route NONE) -> SELLER_UNKNOWN;
      * the seller AND every shop shipping a leg of Shopify's split invoice
        from their OWN GSTIN for their own state (gstin_problem): goods
        leaving a Maharashtra shop need a Maharashtra registration;
      * every leg shop shares the seller's GSTIN (split_seller_problem).

    ONE decision, taken where the hold is: ``cause_only`` -- the booking
    (shopify_ingest), Re-map and clear-hold -- judges the shops AS THEY ARE
    NOW, and a fault holds the order (``seller_held``). Every other door (the
    invoice door JSON + PDF, the delivery challan, the e-invoice, GSTR-1/3B,
    Tally, the credit notes, the dispatch gate, the Shopify fulfilment)
    refuses ONLY while that hold stands -- with its cause, or SELLER_HELD
    once the cause is fixed (only clear-hold or Re-map checks the fix leaves
    the booked tax heads standing, ``seller_change``). An order never held,
    or released, is never judged again on today's shop records: a shop's
    GSTIN or state edited later never pulls an issued invoice out of the
    returns it was filed in.

    ``store_doc`` is the order's own shop (order.store_id), whose ``gstin`` is
    the GSTIN every one of those doors issues from; ``find_store(id)`` reads a
    leg shop. A leg that cannot be read is not provably fine: a problem."""
    route = order.get("fulfillment_route")
    if not isinstance(route, dict) or not (cause_only or seller_held(order)):
        return None
    fault = _seller_fault(order, route, store_doc, find_store)
    if fault or cause_only:
        return fault
    return _problem(
        "SELLER_HELD",
        "This order is still on its seller (GSTIN) hold. Its cause is fixed: "
        "clear the hold (or press Re-map) on the Online orders screen -- IMS "
        "checks that the shop as it is now still splits its GST as booked "
        "(the invoice keeps its number, date and tax). Until that, no tax "
        "invoice is issued or filed for it.",
    )


def _seller_fault(order, route, store_doc, find_store) -> Optional[Dict[str, str]]:
    """seller_problem's causes: no shop named, a GSTIN rule broken."""
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
        or next((p for p in (_leg_gstin_problem(d, store_doc) for d in leg_docs) if p), None)
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
    wanted (``_stale_move``) is SKIPPED, never sent. A problem the order
    already had (``_tasked``: Re-map keeps them on the route) is never tasked
    again, whoever sends the move -- a human may have closed that very task.
    Never raises."""
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
    # PLANNED -- sent now, the stock would be written back before the claim;
    # the creator sends them (and writes stock back) right after its claim.
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
        if m["moved_fulfillment_order_id"] not in fo_ids:
            fo_ids.append(m["moved_fulfillment_order_id"])
    route["moves"] = moves
    route["fulfillment_order_ids"] = fo_ids
    pending = route.get("hold_reason")
    if failed:
        # Name the shop each move was for: on a split leg it is not the
        # order's billing shop (order.store_id), where a human would move it.
        to = ", ".join(sorted({str(m.get("to_store_id") or m.get("to_location_id")) for m in failed}))
        problem = {"about": f"{sorted(m['fulfillment_order_id'] for m in failed)} -> {to}", **_problem(
            "MOVE_FAILED",
            f"IMS could not move {len(failed)} fulfillment order(s) to {to}'s "
            f"Shopify location ({failed[0].get('error')}). The order is on hold: "
            "once Shopify can take the move (if it says the location does not "
            f"stock the item, stock it at {to}'s location in Shopify admin), press "
            "Re-map on the Online orders screen -- IMS re-reads the routing and "
            f"sends the move again; or move it to {to}'s location in Shopify admin "
            "yourself (Orders > order > Change location), then clear the hold. "
            "Until then Shopify has it (and the unit it committed) at the other shop.",
        )}
        route["problems"] = list(route.get("problems") or []) + [problem]
        route["hold_reason"] = problem["message"]
        # Keep (or put) the order on hold -- unless another hold (a stock
        # miss, a seller GSTIN) already owns the reason, which stays.
        holds = [
            ({"stock_hold_reason": {"$in": [None, pending]}},
             {"$set": {"fulfillment_hold": True, "stock_hold_reason": problem["message"]}}),
        ]
    else:
        # Lift only OUR pending-move hold -- one that is a route problem's
        # own text (FO_AT_OTHER_SHOP: a sibling IMS did not move) stays --
        # deciding the Rx part on the order AS STORED NOW (an admin may
        # clear the Rx hold while the move is on the wire): still
        # Rx-pending -> stays held, else released.
        if pending in {p.get("message") for p in route.get("problems") or [] if isinstance(p, dict)}:
            pending = None
        else:
            route["hold_reason"] = None
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
    if failed and _ident(problem) not in _tasked(route):
        # Only the NEW problem: the booking-time ones were tasked at booking,
        # and a human may already have closed them.
        raise_problem_tasks(db, {**order, "fulfillment_route": {**route, "problems": [problem]}})
    _stock_write_back(db, order)
    return {"moved": len(planned) - len(failed), "failed": len(failed)}


_DEAD = ("CANCELLED", "REFUNDED")  # an order nothing ships for any more


def _stale_move(order: Dict[str, Any]) -> Optional[str]:
    """Why a PLANNED move must no longer be sent (None: send it): the order
    is cancelled, or a human already handled it (the hold text tells them to
    move it by hand and clear the hold). A claim that came up short at the
    move's target (a walk-in took the unit between the routing count and the
    claim) does NOT stop it: the claim and the invoice are already at that
    shop, so the fulfillment order must follow them -- left behind, Shopify
    keeps the old shop's unit committed while IMS shows it on sale (the
    write-back would offer it twice). The short claim is loud on its own
    (stock-miss hold + task at the short shop)."""
    status = str(order.get("status") or "").upper()
    if status in _DEAD:
        return f"the order is {status}"
    if not order.get("fulfillment_hold"):
        return "the order's hold was cleared, so a human handled it"
    return None


def _stock_write_back(db, order: Dict[str, Any]) -> None:
    try:
        from .online_stock_writeback import writeback_after_sale

        writeback_after_sale(db, order.get("items") or [], order.get("store_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ONLINE_ROUTE] stock write-back skipped for %s: %s", order.get("order_id"), exc
        )


_REROUTE_LEASE_SECONDS = 300


def seller_change(
    order: Dict[str, Any],
    store_id: Optional[str],
    store_doc: Optional[Dict[str, Any]],
    old_ships: bool = True,
) -> Optional[str]:
    """THE SIMPLIFIED ROOT RULE (owner, 2026-10-01): Re-map and clear-hold
    NEVER change an order's invoice number, invoice date, seller shop or tax
    heads -- they move only its stock claims and fulfillment orders. Why
    releasing the order at ``store_id`` (``store_doc``: that shop as it is
    now) would change its seller, or None:

      * another shop would ship -- and so bill -- it (Re-map's fresh route);
      * the shop as it is now splits the order's GST (CGST+SGST vs IGST)
        otherwise than the booking stored it (a seller hold fixed by
        changing the shop's state or GSTIN): the invoice door splits live
        while every return files the stored head.

    Either way the way out is a credit note against the invoice and a new
    booking through the normal doors -- or, when the billing shop can ship
    it (``old_ships``: it has a mapped Shopify location; the online bucket
    of a SELLER_UNKNOWN order has none), moving its fulfillment orders back
    there."""
    from ..utils.online_gst import order_place_of_supply
    from .shopify_ingest import _seller_gst_fields

    old, number = order.get("store_id"), order.get("invoice_number")
    fix = (
        f"issue a credit note against invoice {number} and re-book the order "
        "through the normal doors"
    )
    if store_id != old:
        back = (
            f"move its fulfillment orders to {old}'s location in Shopify admin "
            "(Orders > order > Change location) and press Re-map again, or "
            if old_ships
            else ""
        )
        return (
            f"Shopify's routing would now ship it from {store_id}, not from {old}, "
            f"which billed it (invoice {number}). IMS never changes an order's "
            f"seller, invoice or tax: {back}{fix}"
        )
    buyer = order.get("gst_buyer") or {"state": order_place_of_supply(order) or ""}
    now = _seller_gst_fields(order.get("items") or [], store_doc, buyer).get("interstate")
    if now == order.get("interstate"):
        return None
    head = {True: "IGST", False: "CGST+SGST"}
    return (
        f"{_name(store_doc)} as it is now splits this order's GST as "
        f"{head.get(now, 'nothing')}, but invoice {number} was booked as "
        f"{head.get(order.get('interstate'), 'no split')}. IMS never changes an "
        f"order's seller, invoice or tax: {fix}"
    )


def _open_short(items: List[Dict[str, Any]], fos: List[Dict[str, Any]]) -> bool:
    """Shopify's fresh read leaves an IMS line of the order (one whose units
    IMS claims) with less OPEN (unfulfilled) quantity than IMS booked:
    fulfilled, refunded or closed in Shopify -- the goods may have left. A
    line IMS claims nothing for (a gift card Shopify fulfils at payment) never
    blocks: Re-map moves only claims. A line the read does not name (an older
    transcript) is counted in units."""
    left: Dict[str, int] = {}
    for f in fos:
        for ln in f.get("lines") or []:
            lid = str((ln or {}).get("line_item_id") or "")
            left[lid] = left.get(lid, 0) + int((ln or {}).get("qty") or 0)
    want: Dict[str, int] = {}
    for it in items or []:
        if not it.get("ims_product_id"):
            continue
        lid = str(it.get("shopify_line_item_id") or "")
        want[lid] = want.get(lid, 0) + int(it.get("quantity") or 1)
    if "" in left or "" in want:
        return sum(left.values()) < sum(want.values())
    return any(left.get(lid, 0) < q for lid, q in want.items())


def _close_tasks(refs: List[str], note: str, keep_store: Optional[str] = None) -> None:
    """Complete the still-open system tasks with these source_refs -- but one
    at ``keep_store``. Fail-soft."""
    if not refs:
        return
    try:
        from ..dependencies import get_task_repository

        repo = get_task_repository()
        for ref in refs:
            for t in repo.find_many({"source_ref": ref}) or []:
                if keep_store and t.get("store_id") == keep_store:
                    continue
                if str(t.get("status") or "").upper() in ("OPEN", "IN_PROGRESS", "ESCALATED"):
                    repo.complete_task(t["task_id"], notes=note)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] task close skipped for %s: %s", refs, exc)


# The mark every refund and return door stamps on its order BEFORE it books
# anything (mark_refund_or_return): a counter, so each stamp changes it.
REFUND_MARK = "refund_or_return_marks"

# The order fields Re-map's and clear-hold's writes are conditioned on: what
# each read. A cancel, a fulfilment, a hold release (clear-hold: rx_pending +
# fulfillment_hold), a refund or return (REFUND_MARK) or a Re-map landing in
# between changes one of them, and the other's write matches nothing.
HOLD_CAS = (
    "status",
    "stock_hold_reason",
    "fulfillment_hold",
    "rx_pending",
    "fulfillment_status",
    "shopify_fulfillment_id",
    REFUND_MARK,
)


def mark_refund_or_return(db, order_id: Optional[str]) -> None:
    """THE mark of a refund or return on an order, stamped by every door
    that queues or books one (the Shopify refund review and AUTO paths, the
    counter return) BEFORE it writes its row: a Re-map that read the order
    before the mark never writes (HOLD_CAS), one that reads it after refuses
    (refund_or_return) -- no check-then-act window between them. Fail-soft."""
    if not order_id or db is None:
        return
    try:
        _orders(db).update_one({"order_id": order_id}, {"$inc": {REFUND_MARK: 1}})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_ROUTE] refund/return mark skipped for %s: %s", order_id, exc)


def refund_or_return(db, order: Dict[str, Any]) -> Optional[str]:
    """Why Re-map must not move the order's stock claims: a refund or return
    is booked or queued against it (its mark, a returns row or a
    shopify_refund_review row), so money or goods already went back and what
    the order still ships is a human's call (None: none is)."""
    sid = str(order.get("shopify_order_id") or "")
    if order.get(REFUND_MARK):
        return "a refund or return is booked or queued against it -- resolve it by hand"
    try:
        if db.get_collection("returns").count_documents({"order_id": order.get("order_id")}) or (
            db.get_collection("shopify_refund_review").count_documents(
                {"$or": [{"order_id": order.get("order_id")}] + ([{"shopify_order_id": sid}] if sid else [])}
            )
        ):
            return "a refund or return is booked or queued against it -- resolve it by hand"
    except Exception as exc:  # noqa: BLE001 -- unreadable = not provably safe
        return f"its refunds and returns could not be read ({exc})"
    return None


def _remap_refusal(db, order: Dict[str, Any], took_over: bool) -> Optional[str]:
    """Why Re-map must not touch the order AS IT IS NOW (None: go on). Run
    once the lease is held, and again after the routing read -- a cancel, a
    refund or a fulfilment may land while Shopify answers. A Re-map that
    crashed after its write left its lease (stale, ``took_over``): carried
    on, whatever its write did to the hold -- its claim, its tasks or its
    stock write-back may not have run, settled claim or not."""
    if not took_over and not reroutable(order):
        return "the order is not held on its seller (GSTIN) check or a failed fulfillment-order move"
    status = str(order.get("status") or "").upper()
    if status not in ("CONFIRMED", "PROCESSING") or order.get("shopify_fulfillment_id"):
        return f"the order is {status or 'not open'} or already fulfilled"
    shipped = str(order.get("fulfillment_status") or "UNFULFILLED").upper()
    if shipped != "UNFULFILLED":
        return f"Shopify shows it {shipped}: goods may have left -- resolve it by hand"
    # The creator's claim has not settled (it stamps fulfillment_breakdown
    # once it has): re-claiming now would claim the order twice.
    if "fulfillment_breakdown" not in order and not took_over:
        return "its booking is still claiming stock -- press Re-map again in a moment"
    if any(m.get("status") == "SENDING" for m in (order.get("fulfillment_route") or {}).get("moves") or []):
        return "a fulfillment-order move for it is on the wire -- press Re-map again in a moment"
    return refund_or_return(db, order)


async def reroute_held_order(db, order_id: str) -> Dict[str, Any]:
    """Re-route ONE booked online order held on its seller check
    (SELLER_UNKNOWN, SPLIT_SELLERS, SHOP_GSTIN_MISSING) or on a failed
    fulfillment-order move (MOVE_FAILED) -- ``reroutable``, the door those
    holds' text points at (Re-map on the Online orders screen). Needs no
    webhook payload: the order's stored items and a FRESH routing read.

    THE SIMPLIFIED ROOT RULE (``seller_change``): Re-map moves only the
    order's stock claims and fulfillment orders -- never its invoice number,
    invoice date, seller shop or tax heads. A fresh route that would change
    the seller (another shop, or the shop's GST split) or still fail the
    seller check is REFUSED, the order untouched.

    Takes its lease FIRST, then reads the order and runs every check on it,
    and again after the routing read (``_remap_refusal``); its write is
    conditioned on what it read (``HOLD_CAS``). Two presses, or a cancel
    landing while Shopify answers, never both act. A cancel landing after
    the write (another worker) never keeps a unit Re-map marked SOLD: every
    unit it put back or claimed is read against the order's status AFTER
    being marked, and given back if the order is dead (``_DEAD``).

    Refuses when the fresh read shows a line of the order with less open
    quantity than booked (``_open_short``); otherwise routes it again with
    THE rule (route_order, counting the units the order holds as its own),
    keeps every unit the new route still wants where it is (never swapped
    for another: it may be packed), gives back only the units the route no
    longer wants (a split leg that moved) and claims only what is missing
    with THE claim (shopify_ingest._claim_online_units).

    Tasks: only a problem the order did not have is tasked (``_tasked``,
    kept on the route for every sender of its move), a problem is closed
    only once the move has had its say, the booking's stock miss once the
    claim came up whole -- still short, its task goes to the shop short now
    -- and only a shop that claims a NEW unit is told to ship.

    Still a problem -> it stays held under the new reason. Refused, the order
    untouched (no unit given back, no task raised): not reroutable;
    dispatched, cancelled or fulfilled (in IMS, or a line Shopify shows
    fulfilled, refunded or closed); a refund or return booked or queued on
    it; routing unreadable (dark included); the booking's claim not settled
    yet or a move on the wire; another Re-map mid-flight; the fresh route
    names no shop, would change the seller or fails the seller check."""
    from datetime import datetime, timedelta, timezone

    from .shopify_ingest import (
        _claim_online_units,
        _record_stock_miss,
        claim_plan,
        raise_stock_miss_task,
    )

    coll = _orders(db)
    now = datetime.now(timezone.utc)
    mine = now.isoformat()
    stale = (now - timedelta(seconds=_REROUTE_LEASE_SECONDS)).isoformat()
    # The lease FIRST; the order as it was when the lease was taken comes back.
    order = coll.find_one_and_update(
        {"order_id": order_id, "$or": [{"reroute_lease_at": None}, {"reroute_lease_at": {"$lt": stale}}]},
        {"$set": {"reroute_lease_at": mine}},
    )

    def refused(why: str) -> Dict[str, Any]:
        return {
            "status": "refused",
            "order_id": order_id,
            "store_id": (order or {}).get("store_id"),
            "error": why,
            "message": f"Not re-routed: {why}",
        }

    if order is None:
        order = coll.find_one({"order_id": order_id}) or {}
        return refused(
            "another Re-map of this order is running"
            if order.get("reroute_lease_at")
            else _remap_refusal(db, order, False) or "the order could not be read"
        )
    took_over = bool(order.get("reroute_lease_at"))
    try:
        why = _remap_refusal(db, order, took_over)
        if why:
            return refused(why)
        routing = await read_routing(db, str(order.get("shopify_order_id") or ""))
        if "fulfillment_orders" not in routing:
            return refused(
                "Shopify's routing could not be read ("
                + str(routing.get("dark") or routing.get("error") or "unknown")
                + ")"
            )
        if _open_short(order.get("items") or [], _open_fos(routing)):
            return refused(
                "Shopify shows part of it fulfilled, refunded or closed (less of it "
                "open than was booked) -- goods may have left; resolve it by hand"
            )
        # The order as it is NOW: the checks again, on what the write is
        # conditioned on.
        order = coll.find_one({"order_id": order_id}) or {}
        why = _remap_refusal(db, order, took_over)
        if why:
            return refused(why)
        old_route = order.get("fulfillment_route") or {}
        old = order.get("store_id")
        # Every problem tasked before -- a human may have closed its task.
        tasked = _tasked(old_route) | {_ident(p) for p in old_route.get("problems") or []}

        from ..dependencies import get_store_repository
        from ..routers.orders import get_stock_repository

        items = order.get("items") or []
        ref = order.get("order_number") or order_id
        stock_repo = get_stock_repository()
        find = getattr(get_store_repository(), "find_by_id", None) or (lambda _sid: None)
        freed: List[str] = []
        kept_ids: List[str] = []  # the units Re-map never gave back

        def undone_by_cancel() -> bool:
            """The order is dead (a cancel landed while Re-map ran): every
            unit Re-map marked SOLD for it -- all but ``kept_ids`` -- is given
            back. Read AFTER the marking: a cancel flipping the status later
            runs its own release after it, so either way no unit stays SOLD
            against a cancelled order."""
            now_status = str((coll.find_one({"order_id": order_id}) or {}).get("status") or "")
            if now_status.upper() not in _DEAD:
                return False
            stock_repo.release_sold_units_for_order(
                order_id, exclude_stock_ids=kept_ids or None, reason="ORDER_CANCELLED"
            )
            return True

        def put_back() -> None:
            """The units this Re-map gave back are the order's again -- the
            very same ones, while it is live. One a till sold meanwhile is a
            stock miss, loud."""
            if not freed:
                return
            lost = [sid for sid in freed if not stock_repo.mark_sold(sid, order_id)]
            if not undone_by_cancel() and lost:
                short = sorted({shop_of_unit.get(sid) or "" for sid in lost})
                _record_stock_miss(db, order_id, short[0], "under_claim",
                                   {"lost_on_remap": lost, "short_stores": short})

        # The units the order holds now, per (shop, product) -- counted as its
        # own by the route, kept wherever the new route still wants them.
        held_ids: Dict[tuple, List[str]] = {}
        for u in stock_repo.collection.find({"order_id": order_id, "status": "SOLD"}):
            key = (str(u.get("store_id")), str(u.get("product_id")))
            held_ids.setdefault(key, []).append(str(u.get("stock_id") or u.get("_id")))
        shop_of_unit = {sid: shop for (shop, _pid), ids in held_ids.items() for sid in ids}
        own: Dict[str, Dict[str, int]] = {}
        for (shop, pid), ids in held_ids.items():
            own.setdefault(pid, {})[shop] = len(ids)
        try:
            route = route_order(db, items, routing, held=own)
            store_id = route.get("store_id")
            # Never the old shop by default (nothing would be claimed), never
            # a shop picked while the routing itself was unread.
            if not store_id or any(p["code"] == "ROUTING_UNREAD" for p in route["problems"]):
                raise ValueError(
                    "Shopify's routing names no IMS shop to ship it: "
                    + (" ".join(p["message"] for p in route["problems"]) or "no shop")
                )
            store_doc = find(store_id)
            bad = seller_problem({"store_id": store_id, "fulfillment_route": route}, store_doc, find, True)
            if bad and store_id == old:
                raise ValueError(f"Shopify's routing leaves it failing the seller check -- {bad['message']}")
            locs = shop_locations(db)  # None: unreadable -- offer the move back
            change = seller_change(order, store_id, store_doc, locs is None or old in locs)
            if change:
                raise ValueError(change)
            # Keep each unit the new route still wants at its shop; give back
            # only the rest (a unit a shop no longer ships).
            want: Dict[tuple, int] = {}
            for shop, lines in claim_plan(items, route).items():
                for ln in lines:
                    key = (str(shop), str(ln["product_id"]))
                    want[key] = want.get(key, 0) + int(ln.get("quantity") or 1)
            keep = {k: ids[: want.get(k, 0)] for k, ids in held_ids.items()}
            kept_ids.extend(sid for ids in keep.values() for sid in ids)
            if sum(map(len, keep.values())) < len(shop_of_unit):
                given = stock_repo.release_sold_units_for_order(
                    order_id,
                    exclude_stock_ids=kept_ids or None,
                    reason="ONLINE_REROUTE",
                )
                freed.extend(given.released)
                if given.incomplete:
                    raise ValueError("not every unit it no longer ships could be given back; press Re-map again")
            hold = route.get("hold_reason")
            update: Dict[str, Any] = {
                "fulfillment_route": {
                    **route,
                    "rerouted_at": now.isoformat(),
                    "tasked": sorted((list(t) for t in tasked), key=str),
                },
                "fulfillment_hold": bool(order.get("rx_pending") or hold),
            }
            unset = {"fulfillment_breakdown": "", "fulfillment_stores": ""}  # claim unsettled
            if hold:
                update["stock_hold_reason"] = hold
            else:
                unset["stock_hold_reason"] = ""
            written = coll.update_one(
                {"order_id": order_id, "reroute_lease_at": mine, **{k: order.get(k) for k in HOLD_CAS}},
                {"$set": update, "$unset": unset},
            )
            if not getattr(written, "matched_count", 0):
                raise ValueError(
                    "the order changed while Re-map ran (cancelled, released, fulfilled "
                    "or re-routed) -- press Re-map again"
                )
        except Exception as exc:  # noqa: BLE001 -- nothing written: its units are its own again
            put_back()
            return refused(str(exc))
        misses = db.get_collection("online_stock_miss")
        try:
            booked_misses = [r["_id"] for r in misses.find({"order_id": order_id, "resolved": False})]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ONLINE_ROUTE] stock-miss read skipped for %s: %s", order_id, exc)
            booked_misses = None
        kept: Dict[str, Dict[str, int]] = {}
        for (shop, pid), ids in keep.items():
            if ids:
                kept.setdefault(shop, {})[pid] = len(ids)
        breakdown, stores = _claim_online_units(db, order_id, ref, items, route, held=kept)
        if undone_by_cancel():
            _close_tasks([f"online_fallback_ship:{order_id}:{s}" for s in stores],
                         "The order was cancelled while Re-map ran.")
            _stock_write_back(db, {**order, **update})
            return refused(
                "the order was cancelled while Re-map ran -- every unit Re-map "
                "claimed for it is back on the shelf"
            )
        coll.update_one(
            {"order_id": order_id},
            {"$set": {"fulfillment_breakdown": breakdown, "fulfillment_stores": stores}},
        )
        done = "Re-mapped: the order was routed and its stock claimed again."
        if booked_misses is not None:
            try:  # the booking's stock miss is answered only by a WHOLE claim
                short = list(misses.find(
                    {"order_id": order_id, "resolved": False, "_id": {"$nin": booked_misses}}))
                misses.update_many(
                    {"_id": {"$in": booked_misses}},
                    {"$set": {"resolved": True, "resolution": "SUPERSEDED" if short else "REROUTED",
                              "resolved_at": now.isoformat()}},
                )
                miss_ref = f"online_stock_miss:{order_id}"
                if not short:
                    _close_tasks([miss_ref], done)
                else:
                    # Still short: the task is the shop's that is short NOW (a
                    # shop still short keeps its one task as it was).
                    at = short[0].get("store_id")
                    _close_tasks([miss_ref], f"Re-mapped: the order is short at {at} now, "
                                 "which has the task.", keep_store=at)
                    raise_stock_miss_task(order_id, ref, at, short[0].get("reason"), short[0].get("detail"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ONLINE_ROUTE] stock-miss close skipped for %s: %s", order_id, exc)
        # A shop that no longer ships a unit of it must not pack one: every
        # shop's task, not only those of the stores read -- a Re-map carrying
        # on a crashed one reads the stores that one already rewrote.
        try:
            from .stores_util import physical_stores

            every = {str(s.get("store_id")) for s in physical_stores(db)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ONLINE_ROUTE] shop list unreadable for %s: %s", order_id, exc)
            every = set()
        _close_tasks(
            [f"online_fallback_ship:{order_id}:{s}"
             for s in sorted((every | set(order.get("fulfillment_stores") or [])) - set(stores))],
            "Re-mapped: this shop no longer ships this order.",
        )
        # Only a problem the order did not have -- a human may have closed
        # the booking's tasks.
        raise_problem_tasks(db, {**order, **update, "fulfillment_route": {**route, "problems": [
            p for p in route["problems"] if _ident(p) not in tasked]}})
        if any(m.get("status") == "PLANNED" for m in route.get("moves") or []):
            await move_fulfillment_orders(db, order_id)  # writes stock back after the move
        else:
            _stock_write_back(db, {**order, **update})
        # Every problem the order no longer has, once the move had its say (a
        # move failing again keeps its MOVE_FAILED task as it was) -- every
        # one it was tasked for, a crashed Re-map's included.
        now_route = (coll.find_one({"order_id": order_id}) or {}).get("fulfillment_route") or route
        gone = {code for code, _about in tasked} - {
            p.get("code") for p in now_route.get("problems") or []
        }
        _close_tasks([f"online_route:{c}:{order_id}" for c in sorted(gone)], done)
    finally:
        coll.update_one({"order_id": order_id, "reroute_lease_at": mine}, {"$unset": {"reroute_lease_at": ""}})
    final = coll.find_one({"order_id": order_id}) or {}
    held = final.get("stock_hold_reason")
    return {
        "status": "rerouted",
        "order_id": order_id,
        "shopify_order_id": final.get("shopify_order_id"),
        "store_id": final.get("store_id"),
        "invoice_number": final.get("invoice_number"),
        "held": bool(final.get("fulfillment_hold")),
        "message": (
            f"Re-routed at {final.get('store_id')}; still on hold: {held}"
            if held
            else f"Re-routed at {final.get('store_id')} (invoice {final.get('invoice_number')} "
            "unchanged); "
            + ("the Rx hold stands." if final.get("fulfillment_hold") else "the hold is lifted.")
        ),
    }


async def map_routed_order(
    payload: Dict[str, Any],
    db,
    *,
    webhook_id: Optional[str] = None,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """THE door every live online-order create goes through (webhook drain,
    missed-webhook pull, Re-map of an order not booked): read Shopify's
    routing FRESH for an order IMS has not booked yet (the read always
    overwrites any stamp a stored payload carries), hand it to the (sync)
    mapper -> ingest, then send the planned moves -- for an order already
    booked too (a replayed or orders/updated delivery), so moves a crash left
    PLANNED are retried by the next delivery. Returns the mapper's result.
    Never raises."""
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
