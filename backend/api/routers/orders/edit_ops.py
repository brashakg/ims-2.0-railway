"""A bill edit's stock work, written down before anything moves.

Standalone Mongo, no transaction: an edit's save and its stock moves are separate
writes, any of which can land and lose its reply, and the server can die between
them. So before a privileged edit moves a thing it stores what it will move (one
`order_edit_ops` row per press), and its save carries that row's id onto the bill
in the SAME write (`edit_ops`). The bill then says which way the row ends:

  id on the bill      the edit landed: give back what it dropped;
  id not on the bill  it never landed: give back what it took.

Both ends go by name (a unit's stock_id and the claim stamped on it, a lens's own
release key), so running either twice moves nothing twice. They run in the press
itself; on an identical retry or the next edit of that bill (landed rows only: a
row not on the bill may be a press still in flight); and in the scheduler's sweep,
which also takes the rows nobody came back for. What the sweep cannot finish stays
OPEN and is retried; the bill keeps its flag and the Tasks board says so.
"""

from contextlib import suppress
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import HTTPException

from api.utils.event_time import utc_now_iso

from ...dependencies import get_order_repository, get_stock_repository
from ...services.lens_stock_hook import _is_lens_item, takes_lens_cell
from ._shared import logger
from .release import CANCELLABLE_STATUSES, _is_unit_tracked, _release_lens_lines
from .stock import CLAIM_SWEEP_MINUTES, _release_op_claims, _takes_serialized_stock

OPS_COLLECTION = "order_edit_ops"
LEASE_SECONDS = 120  # one runner at a time; a dead runner's lease lapses
RELEASE_FAILED = "line_remove_stock_release_failed"
RELEASE_FAILED_ITEM = "line_remove_stock_failed_item_id"
_ACTOR_FIELDS = ("user_id", "username", "full_name", "roles", "store_ids", "active_store_id")


def _ops():
    """The ops rows, beside the orders. None for a store with no database (no
    durability to offer: the press runs the same steps, unrecorded)."""
    coll = getattr(get_order_repository(), "collection", None)
    database = getattr(coll, "database", None)
    return None if database is None else database.get_collection(OPS_COLLECTION)


def _name_units(order: dict, entries: List[dict], new_items: List[dict]) -> None:
    """Which units each dropped line holds, by name, read before the save: a line
    that took first-available units names none, and a re-run by name is what makes
    its release safe to repeat. A surviving line's unit is never one of them."""
    stock_repo = get_stock_repository()
    if stock_repo is None or not hasattr(stock_repo, "sold_unit_ids"):
        return
    order_id, store_id = order.get("order_id"), order.get("store_id")
    named = {str(ln["stock_id"]) for ln in new_items if ln.get("stock_id")}
    for entry in entries:
        line = entry["line"]
        if not _takes_serialized_stock(line):
            continue
        pid, qty = line.get("product_id") or "", max(int(line.get("quantity") or 1), 1)
        units = [str(line["stock_id"])] if line.get("stock_id") else []
        named.update(units)
        if len(units) < qty:
            try:
                units += stock_repo.sold_unit_ids(order_id, pid, named, qty - len(units))
            except Exception as exc:  # noqa: BLE001 -- nothing has moved: a refusal
                logger.error("[STOCK] units of order %s unreadable: %s", order_id, exc)
                raise HTTPException(
                    status_code=503, detail="Stock could not be checked for this edit. Try again."
                ) from exc
        named.update(units)
        entry["units"] = units
        # A legacy line billed before POS 4 may hold none: that is no shortfall.
        entry["short"] = len(units) < qty and _is_unit_tracked(stock_repo, pid, store_id)


def open_op(order: dict, added: List[dict], removed: List[dict], new_items: List[dict], user: dict) -> dict:
    """Write down what this press owes, before it moves anything. A row that
    cannot be written is a 503 with nothing moved."""
    lines = order.get("items") or []

    def position(dropped: dict) -> int:
        whole = dropped.get("part_of") or dropped
        return next((at for at, ln in enumerate(lines) if ln is whole), 0)

    entries = [
        {
            "line": {k: v for k, v in r.items() if k not in ("part_of", "hold_key")},
            "position": position(r),
            "part": bool(r.get("part_of")),
            "hold": r.get("hold_key"),
            "units": [],
            "short": False,
        }
        for r in removed
        if _takes_serialized_stock(r) or _is_lens_item(r)
    ]
    _name_units(order, entries, new_items)
    # Only a bill the counter may still cancel gives stock back (the cancel door's
    # own rule): on any other, a parcel or the customer holds it.
    open_bill = str(order.get("status") or "").upper() in CANCELLABLE_STATUSES
    op = {
        "op_id": uuid4().hex,
        "order_id": order.get("order_id"),
        "store_id": order.get("store_id"),
        "state": "OPEN",
        "created_at": datetime.now(),
        "lease_until": None,
        "user": {k: user.get(k) for k in _ACTOR_FIELDS},
        "added": [dict(ln) for ln in added],
        "give_back": entries if open_bill else [],
        # A lens billed on a bill that left reaches no workshop step: it is out now.
        "commit": [] if open_bill else [dict(ln) for ln in added if takes_lens_cell(ln)],
        "left_with_parcel": [] if open_bill else entries,
        "raised_flag": bool(open_bill and entries and not order.get(RELEASE_FAILED)),
    }
    coll = _ops()
    if coll is not None:
        try:
            coll.insert_one(dict(op))
        except Exception as exc:  # noqa: BLE001
            logger.error("[STOCK] edit of order %s not recorded: %s", order.get("order_id"), exc)
            raise HTTPException(status_code=503, detail="This edit could not be started. Try again.") from exc
    return op


def save_fields(op: dict, order: dict) -> Dict[str, Any]:
    """What the edit's save writes with itself, so that landing proves all of it:
    the op's id, the flag that reads as owing until the settle says otherwise,
    and the lineage of goods taken off a bill that has left the shop (the unit
    stays SOLD to it; `removed_lines` is what a return finds them by)."""
    fields: Dict[str, Any] = {"edit_ops": [*(order.get("edit_ops") or []), op["op_id"]]}
    if op["raised_flag"]:
        fields[RELEASE_FAILED] = True
        fields[RELEASE_FAILED_ITEM] = op["give_back"][0]["line"].get("item_id")
    gone = [_removed_line(entry, op, order) for entry in op["left_with_parcel"]]
    if gone:
        fields["removed_lines"] = [*(order.get("removed_lines") or []), *gone]
    return fields


def _removed_line(entry: dict, op: dict, order: dict) -> dict:
    line = entry["line"]
    # A lowered line lives on under its own id: its pieces get one of their own.
    item_id = f"{line.get('item_id')}-{op['op_id'][:8]}" if entry["part"] else line.get("item_id")
    return {
        **{k: line.get(k) for k in ("product_id", "product_name", "sku", "item_type", "category",
                                    "quantity", "stock_id", "unit_price", "gst_rate")},
        "item_id": item_id,
        "stock_ids": entry["units"],
        "removed_from_bill": True,
        "removed_at": utc_now_iso(),
        "removed_by": op["user"].get("user_id"),
        "removed_from_invoice": order.get("invoice_number"),
        "status_when_removed": order.get("status"),
    }


def _take_lease(op: dict) -> bool:
    """One runner per row: the lens hook's dedupe is a read, so two at once could
    release a cell twice. A lost lease means someone else is on it."""
    coll = _ops()
    if coll is None:
        return True
    now = datetime.now()
    try:
        return coll.find_one_and_update(
            {"op_id": op["op_id"], "state": "OPEN",
             "$or": [{"lease_until": None}, {"lease_until": {"$lt": now}}]},
            {"$set": {"lease_until": now + timedelta(seconds=LEASE_SECONDS)}},
        ) is not None
    except Exception as exc:  # noqa: BLE001 -- the row stays OPEN for the next runner
        logger.warning("[STOCK] edit op %s not taken up: %s", op["op_id"], exc)
        return False


def _end(op: dict, state: Optional[str]) -> None:
    """Close the row, or (no state) hand it back for the next runner."""
    coll = _ops()
    if coll is None:
        return
    fields = {"lease_until": None, **({"state": state, "closed_at": utc_now_iso()} if state else {})}
    try:
        coll.update_one({"op_id": op["op_id"]}, {"$set": fields})
    except Exception as exc:  # noqa: BLE001 -- a re-run moves nothing twice
        logger.warning("[STOCK] edit op %s not closed: %s", op["op_id"], exc)


async def give_back(op: dict) -> bool:
    """The edit never landed: what it took goes back, claims that lost their reply
    included. False leaves the row OPEN: the units and cells stay held, and the
    sweep tries again, then the Tasks board says so."""
    from ...services.lens_stock_hook import release_for_cancel

    if not _take_lease(op):
        return False
    order_id, store_id = op["order_id"], op.get("store_id") or ""
    done = True
    if any(_takes_serialized_stock(ln) for ln in op["added"]):
        done = _release_op_claims(order_id, op["op_id"])
    for line in op["added"]:
        if not takes_lens_cell(line):
            continue
        try:
            rec = await release_for_cancel(
                order_item=line, order_id=order_id, line_index=line["item_id"],
                store_id=store_id, user=op["user"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LENS_HOOK] give back failed (order %s): %s", order_id, exc)
            rec = {"status": "failed"}
        done = done and (rec or {}).get("status") != "failed"
    if not done:
        logger.error("[STOCK] edit %s of order %s not given back; the sweep retries it", op["op_id"], order_id)
    _end(op, "GIVEN_BACK" if done else None)
    return done


def flag_order(repo, order_id: str, item_id) -> None:
    try:
        if not repo.update(order_id, {RELEASE_FAILED: True, RELEASE_FAILED_ITEM: item_id}):
            logger.error("[STOCK] could not flag order %s: %s not settled", order_id, item_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[STOCK] could not flag order %s (%s): %s", order_id, item_id, exc)


async def _give_back_cells(op: dict, failed: List[Any]) -> None:
    from ...services.lens_stock_hook import release_for_cancel

    order_id, store_id, user = op["order_id"], op.get("store_id") or "", op["user"]

    async def release(*, order_item, order_id, line_index, store_id, user, **part) -> None:
        rec = await release_for_cancel(
            order_item=order_item, order_id=order_id, line_index=line_index,
            store_id=store_id, user=user, **part,
        )
        if (rec or {}).get("status") == "failed":
            failed.append(order_item.get("item_id"))

    lowered = partial(release, part=op["op_id"])

    for entry in op["give_back"]:
        line = entry["line"]
        try:
            if entry.get("hold"):
                await lowered(order_item=line, order_id=order_id, line_index=entry["hold"],
                              store_id=store_id, user=user)
            else:
                await _release_lens_lines(
                    [line], order_id=order_id, store_id=store_id, user=user,
                    release=lowered if entry["part"] else release, positions=[entry["position"]],
                )
        except Exception as exc:  # noqa: BLE001 -- the edit stands
            logger.warning("[LENS_HOOK] release on edit failed (order %s): %s", order_id, exc)
            failed.append(line.get("item_id"))


def _give_back_units(op: dict, after: dict, failed: List[Any]) -> None:
    """By name, sparing a unit the bill bills now; 'gone' is what the shelf says."""
    stock_repo = get_stock_repository()
    keep = {str(ln.get("stock_id")) for ln in after.get("items") or [] if ln.get("stock_id")}
    for entry in op["give_back"]:
        units = [sid for sid in entry["units"] if sid not in keep]
        if not units:
            continue
        try:
            for sid in units:
                stock_repo.release_sold_units_for_order(
                    op["order_id"], stock_id=sid, reason="ORDER_LINE_REMOVED"
                )
            left = stock_repo.count(
                {"order_id": op["order_id"], "status": "SOLD", "stock_id": {"$in": units}}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[STOCK] edit restock FAILED (order %s): %s", op["order_id"], exc)
            left = len(units)
        if left:
            failed.append(entry["line"].get("item_id"))


async def _commit_departed_lenses(op: dict, failed: List[Any]) -> None:
    from ...services.lens_stock_hook import commit_for_workshop_dispatch

    for line in op["commit"]:
        rec = await commit_for_workshop_dispatch(
            order_item=line, order_id=op["order_id"], line_index=line["item_id"],
            store_id=op.get("store_id") or "", user=op["user"],
        )
        if (rec or {}).get("status") == "failed":
            failed.append(line.get("item_id"))


async def settle(op: dict, after: dict, repo) -> bool:
    """The edit landed: what it dropped goes back, the website hears of what moved,
    the earn is re-gated. Never raises: a lost reply's own error is the caller's.
    `after` is the bill as saved. False leaves the row OPEN for the next runner."""
    if not _take_lease(op):
        return False
    order_id, store_id = op["order_id"], op.get("store_id")
    failed: List[Any] = []
    await _give_back_cells(op, failed)
    _give_back_units(op, after, failed)
    await _commit_departed_lenses(op, failed)
    short = [e["line"].get("item_id") for e in op["give_back"] if e.get("short")]
    if failed or short:
        flag_order(repo, order_id, (failed or short)[0])
    elif op.get("raised_flag"):
        try:
            repo.update_if(order_id, {}, {}, unset=[RELEASE_FAILED, RELEASE_FAILED_ITEM])
            _close_task(order_id, op["user"])
        except Exception as exc:  # noqa: BLE001 -- a false alarm, never a lost unit
            logger.warning("[STOCK] flag not cleared (order %s): %s", order_id, exc)
    moved = op["added"] + [e["line"] for e in op["give_back"]]
    if moved:
        from ...services.online_stock_writeback import writeback_after_sale

        writeback_after_sale(None, moved, store_id)
    # Owner hard rule: an edit that raises discounts must not leave the create-time
    # loyalty earn standing. Delta-claw (repeats converge), fail-soft.
    with suppress(Exception):
        from ..loyalty import regate_earn_after_edit

        regate_earn_after_edit(after, user_id=op["user"].get("user_id"))
    _end(op, None if failed else "SETTLED")
    return not failed


async def finish_landed(order: dict, repo) -> dict:
    """An edit door's first step: any earlier edit of this bill that landed but
    never settled is finished now, so an identical retry repairs what its first
    try could not. Returns the bill as it now stands."""
    coll, ids = _ops(), order.get("edit_ops") or []
    if coll is None or not ids:
        return order
    try:
        rows = list(coll.find({"op_id": {"$in": ids}, "state": "OPEN"}, {"_id": 0}))
    except Exception as exc:  # noqa: BLE001 -- the sweep finishes them
        logger.warning("[STOCK] open edits of order %s unreadable: %s", order.get("order_id"), exc)
        return order
    for op in rows:
        await settle(op, order, repo)
    return (repo.find_by_id(order.get("order_id")) or order) if rows else order


def _task_ref(order_id: str) -> str:
    return f"order_stock_unsettled:{order_id}"


def _raise_task(order: dict) -> None:
    from ...dependencies import get_task_repository
    from ...services.task_triggers import create_system_task

    number = order.get("order_number") or order.get("order_id")
    create_system_task(
        get_task_repository(),
        title=f"Order {number}: stock did not settle",
        description=(
            f"A line was taken off order {number} (or an edit of it did not save) and IMS could not "
            "put its stock right. It keeps trying by itself and closes this task when it succeeds. "
            f"Line or unit: {order.get(RELEASE_FAILED_ITEM)}. Until then the unit stays SOLD or the lens "
            "stays held, so nothing can be sold twice. If this stays open, check the piece against the bill."
        ),
        priority="P1",
        category="Inventory",
        store_id=order.get("store_id"),
        dedupe_ref=_task_ref(order.get("order_id")),
        extra={"payload": {"order_id": order.get("order_id"), "item": order.get(RELEASE_FAILED_ITEM)}},
    )


def _close_task(order_id: str, user: dict) -> None:
    from ..returns import _close_task as close

    close(_task_ref(order_id), user, "IMS finished settling this order's stock.")


async def sweep_edit_ops() -> Dict[str, int]:
    """The scheduler's tick: every edit row left OPEN past an in-flight press's
    lifetime is ended by what its bill says, and any bill still flagged (here, or
    by the remove-item door) goes on the Tasks board. Fail-soft."""
    coll, repo = _ops(), get_order_repository()
    out = {"settled": 0, "given_back": 0, "unfinished": 0, "tasks": 0}
    if coll is None or repo is None:
        return out
    cutoff = datetime.now() - timedelta(minutes=CLAIM_SWEEP_MINUTES)
    try:
        rows = list(coll.find({"state": "OPEN", "created_at": {"$lt": cutoff}}, {"_id": 0}).limit(200))
        for op in rows:
            order = repo.find_by_id(op["order_id"])
            if order is None:
                out["unfinished"] += 1
                continue
            landed = op["op_id"] in (order.get("edit_ops") or [])
            done = await (settle(op, order, repo) if landed else give_back(op))
            out["settled" if done and landed else "given_back" if done else "unfinished"] += 1
            if not done and not landed:
                line = (op["added"] or [{}])[0]
                flag_order(repo, op["order_id"], line.get("stock_id") or line.get("item_id"))
        running = set(coll.distinct("order_id", {"state": "OPEN", "created_at": {"$gte": cutoff}}))
        flagged = repo.collection.find(
            {RELEASE_FAILED: True},
            {"_id": 0, "order_id": 1, "order_number": 1, "store_id": 1, RELEASE_FAILED_ITEM: 1},
        ).limit(200)
        for order in flagged:
            if order.get("order_id") not in running:
                _raise_task(order)
                out["tasks"] += 1
    except Exception as exc:  # noqa: BLE001 -- the next tick tries again
        logger.warning("[STOCK] edit sweep stopped: %s", exc)
    return out
