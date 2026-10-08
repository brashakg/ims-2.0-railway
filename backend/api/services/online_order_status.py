"""
IMS 2.0 - ONE rule for an online order's lifecycle status
==========================================================
Every status change a Shopify or courier event makes on an online order goes
through apply_fact(): the order mapper (orders/*), the fulfilment reconcile
(fulfillments/*), the delete handler (orders/delete) and the Shiprocket poll
and webhook. Nothing else writes `status` for those events.

Each event reduces to ONE fact (or none), and TABLE says what that fact does
to the status IMS holds now. Owner rulings 2026-09-28:
  1. Shopify "fulfilled" means SHIPPED. DELIVERED comes only from the courier
     (Shopify shipment_status "delivered", or Shiprocket "DELIVERED").
  2. An order IMS holds DELIVERED that Shopify cancels, refunds or deletes
     STAYS DELIVERED; a person gets ONE task to decide (refund, return or a
     Shopify mistake).
  3. A Shopify edit never moves an order backwards: no cell leads to
     CONFIRMED / PROCESSING / READY, and a body with no fact writes nothing.
A finished order (CANCELLED / REFUNDED / VOID) stays finished for every fact:
finance leaves VOID in revenue, so voiding a cancelled order would count it
again, and REFUNDED -> CANCELLED would reverse the money twice.

The write is the staff door's own compare-and-swap
(routers.orders.release._claim_order_status): it stamps status_updated_at /
_by, delivered_at on DELIVERED, and a status_history entry, and loses cleanly
to a concurrent staff move (read again, decide again).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

SHIP, DELIVER, CANCEL, REFUND, DELETE = "SHIP", "DELIVER", "CANCEL", "REFUND", "DELETE"
KEEP, TASK = None, "TASK"

_LIVE = {SHIP: "SHIPPED", DELIVER: "DELIVERED", CANCEL: "CANCELLED", REFUND: "REFUNDED", DELETE: "VOID"}
# Any row or cell not listed is KEEP: CANCELLED, REFUNDED, VOID, VOIDED, DRAFT,
# HISTORICAL and legacy values never move on a Shopify / courier fact.
TABLE: Dict[str, Dict[str, Optional[str]]] = {
    "CONFIRMED": _LIVE,
    "PROCESSING": _LIVE,
    "READY": _LIVE,
    "SHIPPED": {**_LIVE, SHIP: KEEP},
    "DELIVERED": {CANCEL: TASK, REFUND: TASK, DELETE: TASK},
}
TERMINAL = frozenset({"DELIVERED", "CANCELLED", "REFUNDED", "VOID", "VOIDED"})
# Ruling 1 leaves every fulfilled online order SHIPPED until the courier
# delivers it; before the ruling it was DELIVERED. For the books it is the
# same sale, so every report that picks orders by status reads these sets,
# never a local copy: a done sale (the nightly Tally export, the commission
# ledgers) and a booked sale (the revenue widgets: all but DRAFT / CANCELLED).
SALE_DONE_STATUSES = ("COMPLETED", "DELIVERED", "PAID", "SHIPPED")
# The same done sale for the reports that also read imported history (stock
# movements, sell-through, RFM): TechCherry wrote any case, and FULFILLED.
SALE_DONE_ANY_CASE = tuple(
    v for s in (*SALE_DONE_STATUSES, "FULFILLED") for v in (s, s.lower(), s.title())
)
BOOKED_STATUSES = frozenset({"CONFIRMED", "PROCESSING", "READY", "SHIPPED", "DELIVERED"})
# The goods have left the shop, with the courier or the customer: the targets
# an active Rx / stock hold withholds (the deliver-guard's own rule), and a
# Shopify refund's goods-out rule (shopify_refund: a person decides).
GOODS_OUT = frozenset({_LIVE[SHIP], _LIVE[DELIVER]})
_VERB = {CANCEL: "cancelled", REFUND: "refunded", DELETE: "deleted"}
_ALREADY = frozenset({(DELETE, "VOIDED"), (SHIP, "DELIVERED")})


def _low(value: Any) -> str:
    return str(value or "").strip().lower()


def order_fact(body: Dict[str, Any], *, ful_stale: bool = False) -> Optional[str]:
    """The fact an orders/* body states. Cancel beats refund beats ship.
    paid / partially_* / pending / "partial" / "restocked" / a note or tag edit
    state no lifecycle fact -> None (nothing is written). `ful_stale`: the
    body's fulfilments are older than the one IMS applied, so its
    fulfillment_status is no fact."""
    if body.get("cancelled_at"):
        return CANCEL
    if _low(body.get("financial_status")) == "refunded":
        return REFUND
    if not ful_stale and _low(body.get("fulfillment_status")) == "fulfilled":
        return SHIP
    return None


def fulfilment_fact(ful_status: str, shipment_status: str, tracking_number: str) -> Optional[str]:
    """The fact a fulfilment states (ful_status is the reconcile's canonical
    one). A cancelled / failed fulfilment states nothing, tracking or not."""
    if ful_status in ("CANCELLED", "ERROR"):
        return None
    if shipment_status == "delivered":
        return DELIVER
    if ful_status == "FULFILLED" or tracking_number:
        return SHIP
    return None


def courier_fact(current_status: Any) -> Optional[str]:
    """Shiprocket's status -> DELIVER only on an exact "DELIVERED" ("RTO
    DELIVERED" is a parcel returned to origin, never a delivery)."""
    return DELIVER if str(current_status or "").strip().upper() == "DELIVERED" else None


def moved_by(fact: str) -> Tuple[str, ...]:
    """The statuses TABLE moves on `fact`: the orders a leg that states it
    (the Shiprocket poll's DELIVER) has to ask about."""
    return tuple(frm for frm, row in TABLE.items() if row.get(fact) not in (KEEP, TASK))


def decide(order: Dict[str, Any], fact: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(target status or None, why): why is "conflict" (a TASK cell),
    "withheld" (a finished status kept against a different fact), "held" (an
    active Rx / stock hold keeps a SHIPPED / DELIVERED target) or None."""
    frm = str(order.get("status") or "").strip().upper()
    if not fact:
        return None, None
    cell = TABLE.get(frm, {}).get(fact, KEEP)
    if cell == TASK:
        return None, "conflict"
    if cell is KEEP:
        # The status already holds the fact, or went past it (ruling 1: a
        # delivered order was shipped) -- no disagreement to report.
        same = _LIVE[fact] == frm or (fact, frm) in _ALREADY
        return None, ("withheld" if frm in TERMINAL and not same else None)
    if cell in GOODS_OUT:
        from ..routers.orders import order_has_active_rx_hold

        if order_has_active_rx_hold(order):
            return None, "held"
    return cell, None


def apply_fact(
    db, order: Dict[str, Any], fact: Optional[str], *, source: str,
    extra: Union[None, Dict[str, Any], Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    marks: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Decide and write. Returns {"to", "from", "why", "terminal_withheld",
    "failed"}; "from" is the status the claim moved. `extra` rides the claim
    only; a callable is asked of the order each claim is decided on (read
    again after a lost race). `marks` are the event's own markers (what a
    replay or the hourly sweep reads to know the event landed): they ride the
    same atomic claim when the table moves the status, and land alone when it
    keeps it -- never before the claim, so a write that fails leaves the event
    unmarked and it is retried. failed=True: nothing was written (an error, or
    three lost races). Never raises."""
    out: Dict[str, Any] = {"to": None, "from": None, "why": None, "terminal_withheld": False,
                           "failed": False}
    try:
        from database.repositories.order_repository import OrderRepository

        from ..routers.orders.release import _claim_order_status

        repo = OrderRepository(db.get_collection("orders"))
        for _ in range(3):
            to, why = decide(order, fact)
            out.update(why=why, terminal_withheld=why in ("withheld", "conflict"))
            if why == "held":
                _raise_rx_task(db, order, source)
            elif why == "conflict" and not _raise_conflict_task(db, order, fact, source):
                # No task, so no marks either: the event stays unmarked and
                # its redelivery or the sweep raises the one task again.
                out["failed"] = True
                return out
            oid = order.get("order_id")
            if not oid:
                return out
            if not to:
                if marks:
                    repo.collection.update_one({"order_id": oid}, {"$set": marks})
                return out
            # Bandit B610 reads any call named extra() as Django QuerySet.extra.
            fields = extra(order) if callable(extra) else extra  # nosec B610
            if _claim_order_status(
                repo, oid, to, [order.get("status")], f"system:{source}",
                extra={**(marks or {}), **(fields or {})} or None,  # nosec B610 - a Mongo field dict for our own helper, not Django QuerySet.extra()
            ):
                out["to"], out["from"] = to, order.get("status")
                return out
            # Lost a race (staff moved it): read again and decide again.
            order = repo.collection.find_one({"order_id": oid}) or {}
        logger.warning("[ONLINE_STATUS] %s %s lost three races for order=%s", source, fact,
                       order.get("order_id"))
    except Exception:  # noqa: BLE001 -- a status write never breaks a drain / sweep
        logger.warning("[ONLINE_STATUS] %s %s failed for order=%s", source, fact,
                       (order or {}).get("order_id"), exc_info=True)
    out["failed"] = True
    return out


def _raise_rx_task(db, order: Dict[str, Any], source: str) -> None:
    """(Re)raise the idempotent Rx-hold task so staff see why a Shopify /
    courier fact could not move a HELD order. Never raises."""
    try:
        from .online_rx_hold import raise_rx_hold_task

        raise_rx_hold_task(
            db,
            order_id=order.get("order_id"),
            order_ref=order.get("order_number") or order.get("order_id"),
            store_id=order.get("store_id"),
            channel=str(order.get("channel") or "ONLINE"),
            evaluation={
                "reasons": order.get("rx_hold_reasons") or [],
                "lines": [],
                "detail": order.get("rx_hold_reason") or "",
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("[%s] rx-hold task raise skipped for order=%s", source,
                     order.get("order_id"), exc_info=True)


def _raise_conflict_task(db, order: Dict[str, Any], fact: str, source: str) -> bool:
    """ONE task per order, forever (ruling 2): the order doc's
    status_conflict_at marker is claimed atomically first, so a replay, the
    hourly sweep, a remap or a closed task never raise a second one. True:
    the task is raised (now, or already by the event that holds the marker);
    False: no task yet. Never raises."""
    oid = order.get("order_id")
    if not oid:
        return True
    try:
        orders = db.get_collection("orders")
        claim = orders.update_one(
            {"order_id": oid, "status_conflict_at": None},
            {"$set": {
                "status_conflict_at": datetime.now(timezone.utc).replace(tzinfo=None),
                "status_conflict_fact": fact,
            }},
        )
        if not getattr(claim, "modified_count", 0):
            # Held by another event: raised only once its task has landed.
            # Trusted before that, this event wrote its marks (the sweep then
            # never fed it again) while the holder's insert could still fail
            # and release the marker -- no task at all. Unlanded, this event
            # stays unmarked and the next one, or the sweep, raises the task.
            # ponytail: a holder that dies between its claim and its insert
            # leaves the marker with no task, and every later event fails
            # (the sweep reports it each hour); a lease on the marker if so.
            return db.get_collection("tasks").find_one(
                {"task_type": "online_status_conflict", "order_id": oid}) is not None
    except Exception:  # noqa: BLE001
        logger.warning("[%s] status-conflict marker claim failed for order=%s", source, oid,
                       exc_info=True)
        return False
    ref = order.get("order_number") or oid
    verb = _VERB.get(fact, str(fact).lower())
    try:
        db.get_collection("tasks").insert_one({
            "task_id": f"TSK-{uuid.uuid4().hex[:10].upper()}",
            "task_type": "online_status_conflict",
            "title": (
                f"Shopify {verb} order {ref}, which IMS shows DELIVERED - decide: "
                "refund, return or Shopify mistake"
            ),
            "description": (
                f"Shopify has {verb} online order {ref}, but IMS already has it "
                "DELIVERED, so IMS kept it DELIVERED and changed nothing else "
                "(owner ruling 2026-09-28). The customer still has the goods. "
                "Decide which this is:\n"
                "1. Refund only (the customer keeps the goods): confirm its credit "
                "note in Online Store > Refund reviews; nothing is restocked.\n"
                "2. Return: when the goods physically come back, press Goods back on "
                "this order's refund in Online Store > Refund reviews, which puts them "
                "back in stock. Shopify already refunded that money, so never refund it "
                "again at the counter. Only if Shopify refunded nothing, take them "
                "through the counter return door.\n"
                "3. Shopify mistake: correct the order on Shopify, then close this task.\n"
                "Any Shopify refund for this order waits in the refund review queue "
                "and is never auto-posted."
            ),
            "status": "PENDING",
            "priority": "P1",
            "store_id": order.get("store_id"),
            "source": "ONLINE_STATUS_CONFLICT",
            "channel": str(order.get("channel") or "ONLINE"),
            "order_id": oid,
            "order_ref": ref,
            "shopify_fact": fact,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": f"system:{source}",
        })
        return True
    except Exception:  # noqa: BLE001 -- release the marker so the next event retries
        logger.warning("[%s] status-conflict task insert failed for order=%s", source, oid,
                       exc_info=True)
        # An error does not say the insert did not land (a reply lost after
        # the commit; a standalone mongod retries no write): the order's one
        # conflict task is read back first. Released after a landed insert,
        # the next event claimed the marker again and raised a second task.
        try:
            if db.get_collection("tasks").find_one(
                    {"task_type": "online_status_conflict", "order_id": oid}) is not None:
                return True
        except Exception:  # noqa: BLE001
            # ponytail: an unread task releases (a second task only on two
            # misses in a row), as the stock-in task's read-back does.
            logger.debug("[%s] status-conflict task read-back failed", source, exc_info=True)
        try:
            db.get_collection("orders").update_one(
                {"order_id": oid},
                {"$unset": {"status_conflict_at": "", "status_conflict_fact": ""}},
            )
        except Exception:  # noqa: BLE001
            logger.debug("[%s] status-conflict marker release failed", source, exc_info=True)
        return False
