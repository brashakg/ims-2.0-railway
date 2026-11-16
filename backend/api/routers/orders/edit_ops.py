"""A bill edit's stock work, written down before anything moves.

Standalone Mongo, no transaction: an edit's save and its stock moves are separate
writes, any of which can land and lose its reply, and the server can die between
them. So before a privileged edit moves a thing it stores what it will move (one
`order_edit_ops` row per press), and its save carries that row's id onto the bill
in the SAME write (`edit_ops`). The bill then says which way the row ends:

  id on the bill      the edit landed: give back what it dropped;
  id not on the bill  it never landed: give back what it took.

Both ends go by name (a unit's stock_id and the sale mark stamped on it, a lens's
own release key), so running either twice moves nothing twice, and a runner left
behind by a later edit cannot move what that later edit put back. They run in the press
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
# A regate outcome no retry changes: points already spent, or no loyalty store.
_LOYALTY_FINAL = ("balance_underflow", "loyalty_db_unavailable")
# Points the bill no longer earns that the customer had already spent: before the
# claw row (nothing written, a person's call) or after it (owed, lands by itself).
_POINTS_SPENT = ("balance_underflow", "claw_move_underflow")
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
        # Which sale of each unit this edit gives back (its sale_mark, read before
        # the save): a later edit may sell the very piece to this bill again, and
        # a runner of THIS op must never free that sale (Codex r7 P1).
        try:
            entry["marks"] = [[sid, mark] for sid, mark in stock_repo.sale_marks(order_id, units).items()]
        except Exception as exc:  # noqa: BLE001 -- nothing has moved: a refusal
            logger.error("[STOCK] units of order %s unreadable: %s", order_id, exc)
            raise HTTPException(
                status_code=503, detail="Stock could not be checked for this edit. Try again."
            ) from exc
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
        # A press stalled past its give-back can still claim a unit afterwards and
        # die; such a row is re-checked by the sweep until nothing it took is held.
        "takes_units": any(_takes_serialized_stock(ln) for ln in added),
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


def unvoided(op: dict) -> Dict[str, Any]:
    """What the edit's save filters on beside its own compare-and-set: this op was
    never given back (D61). A press can stall between taking its stock and its save
    for longer than the sweep's window; the sweep then gives that stock back and
    shuts the bill to the op (`_shut_bill`), and the late save matches nothing: a
    409, never a bill line holding no stock. One document arbitrates, so no window."""
    return {"voided_edit_ops": {"$ne": op["op_id"]}}


def _shut_bill(op: dict) -> Optional[str]:
    """Before anything an op took goes back, its bill is closed to the op's save in
    one write on the bill itself: "shut" (the save can never land now), "landed"
    (it already did: settle instead), None (unknown: nothing moves, the row stays
    OPEN for the next runner). Repeats are harmless ($addToSet)."""
    repo = get_order_repository()
    coll = getattr(repo, "collection", None)
    if coll is None or _ops() is None:
        return "shut"  # no database: no save can be in flight elsewhere
    oid, op_id = op["order_id"], op["op_id"]
    try:
        res = coll.update_one({"order_id": oid, "edit_ops": {"$ne": op_id}},
                              {"$addToSet": {"voided_edit_ops": op_id}})
        if getattr(res, "matched_count", None):
            return "shut"
        bill = coll.find_one({"order_id": oid}, {"_id": 0, "edit_ops": 1, "voided_edit_ops": 1})
    except Exception as exc:  # noqa: BLE001 -- the stock stays held; a runner retries
        logger.warning("[STOCK] edit op %s: bill %s not shut: %s", op_id, oid, exc)
        return None
    if bill is None:
        return None
    if op_id in (bill.get("edit_ops") or []):
        return "landed"
    return "shut" if op_id in (bill.get("voided_edit_ops") or []) else None


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
    """One runner per row at a time. A lost lease means someone else is on it.
    A lease can lapse under a runner that is still alive (a stalled worker), so
    the lease is not what keeps a move single: every stock move is fenced by its
    own name where it lands (a unit by its claim, a lens cell by `applied_moves`).
    The token only stops a stale runner closing or handing back a row it no
    longer holds."""
    coll = _ops()
    if coll is None:
        return True
    now, token = datetime.now(), uuid4().hex
    try:
        taken = coll.find_one_and_update(
            {"op_id": op["op_id"], "state": "OPEN",
             "$or": [{"lease_until": None}, {"lease_until": {"$lt": now}}]},
            {"$set": {"lease_until": now + timedelta(seconds=LEASE_SECONDS), "lease_token": token}},
        ) is not None
        if taken:
            op["lease_token"] = token
        return taken
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
        # Only the lease's holder ends it: a runner whose lease lapsed and was
        # taken over writes nothing here (the new holder decides the row).
        coll.update_one({"op_id": op["op_id"], "lease_token": op.get("lease_token")}, {"$set": fields})
    except Exception as exc:  # noqa: BLE001 -- a re-run moves nothing twice
        logger.warning("[STOCK] edit op %s not closed: %s", op["op_id"], exc)


def _reopen(op: dict) -> None:
    """A given-back row whose stock turned out to be still held (D61) is OPEN
    again, so the sweep retries it and the Tasks board hears of it."""
    coll = _ops()
    if coll is None:
        return
    with suppress(Exception):
        coll.update_one({"op_id": op["op_id"], "state": "GIVEN_BACK"},
                        {"$set": {"state": "OPEN", "lease_until": None}})


async def give_back(op: dict) -> bool:
    """The edit never landed: what it took goes back, claims that lost their reply
    included -- once its bill is shut to the op's save, so a save still in flight
    cannot land a line whose stock went back (D61). False leaves the row OPEN: the
    units and cells stay held, and the sweep tries again, then the Tasks board
    says so.

    Once shut, nothing the op took can ever be billed, so its releases run by
    name whoever holds the row and whatever state it is in: a press stalled
    before or inside its take can take AFTER the sweep gave the row back, and
    only a re-run then puts that stock back. The lease decides who ends the row."""
    from ...services.lens_stock_hook import release_for_cancel

    shut = _shut_bill(op)
    if shut == "landed":
        # The save landed after its caller looked: it is settled, never undone.
        op["landed"] = True
        repo = get_order_repository()
        bill = repo.find_by_id(op["order_id"])
        return bill is not None and await settle(op, bill, repo)
    if shut is None:
        return False
    leased = _take_lease(op)
    order_id, store_id = op["order_id"], op.get("store_id") or ""
    done, unfenced = True, False
    if any(_takes_serialized_stock(ln) for ln in op["added"]):
        done = _release_op_claims(order_id, op["op_id"])
    for line in op["added"]:
        if not takes_lens_cell(line):
            continue
        try:
            rec = await release_for_cancel(
                order_item=line, order_id=order_id, line_index=line["item_id"],
                store_id=store_id, user=op["user"], void=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LENS_HOOK] give back failed (order %s): %s", order_id, exc)
            rec = {"status": "failed"}
        done = done and (rec or {}).get("status") != "failed"
        unfenced = unfenced or (rec or {}).get("status") == "voided_no_cell"
    if done and any(takes_lens_cell(ln) for ln in op["added"]):
        done = _mark_lens_unfenced(op, unfenced)
    if not done:
        logger.error("[STOCK] edit %s of order %s not given back; the sweep retries it", op["op_id"], order_id)
    if leased:
        _end(op, "GIVEN_BACK" if done else None)
    elif not done:
        _reopen(op)
    return done


def _mark_lens_unfenced(op: dict, unfenced: bool) -> bool:
    """D77: a lens whose cell did not exist at give-back carries no void, so a
    press stalled before its reserve could still reserve there once the cell is
    created. Such a row is marked and re-run by the sweep (_stranded_claims)
    until the cell carries the void or the reservation went back. False: the
    mark is unwritten, so the row is not given back yet (the sweep retries)."""
    coll = _ops()
    if coll is None:
        return True
    try:
        coll.update_one({"op_id": op["op_id"]}, {"$set": {"lens_unfenced": unfenced}})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STOCK] edit op %s: lens fence not recorded: %s", op["op_id"], exc)
        return False


def flag_order(repo, order_id: str, item_id) -> None:
    try:
        if not repo.update(order_id, {RELEASE_FAILED: True, RELEASE_FAILED_ITEM: item_id}):
            logger.error("[STOCK] could not flag order %s: %s not settled", order_id, item_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[STOCK] could not flag order %s (%s): %s", order_id, item_id, exc)


def _names(op: dict) -> set:
    """What a flag raised for this op can name: never an older flag's line."""
    lines = [*op["added"], *(e["line"] for e in op["give_back"])]
    return {ln.get(k) for ln in lines for k in ("item_id", "stock_id") if ln.get(k)}


def _clear_flag(repo, op: dict) -> None:
    try:
        repo.update_if(op["order_id"], {}, {}, unset=[RELEASE_FAILED, RELEASE_FAILED_ITEM])
        _close_task(op["order_id"], op["user"])
    except Exception as exc:  # noqa: BLE001 -- a false alarm, never a lost unit
        logger.warning("[STOCK] flag not cleared (order %s): %s", op["order_id"], exc)


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
    """By name and by sale: only the sale this edit took the unit off (its mark)
    goes back, so a stale runner of this op cannot free the piece a later edit
    sold to the bill again. 'gone' is what the shelf says."""
    stock_repo = get_stock_repository()
    keep = {str(ln.get("stock_id")) for ln in after.get("items") or [] if ln.get("stock_id")}
    for entry in op["give_back"]:
        if "marks" not in entry and entry["units"]:
            # A row that names no sale of its units frees nothing by stock id
            # alone (D77): the unit stays SOLD and the bill is flagged.
            failed.append(entry["line"].get("item_id"))
            continue
        marks = dict(entry.get("marks") or [])
        units = [sid for sid in marks if sid not in keep]
        if not units:
            continue
        try:
            for sid in units:
                stock_repo.release_sold_units_for_order(
                    op["order_id"], stock_id=sid, reason="ORDER_LINE_REMOVED", sale_mark=marks[sid]
                )
            left = stock_repo.count({"order_id": op["order_id"], "status": "SOLD", "$or": [
                {"stock_id": sid, "sale_mark": marks[sid]} for sid in units]})
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
    elif op.get("raised_flag") or after.get(RELEASE_FAILED_ITEM) in _names(op):
        _clear_flag(repo, op)
    moved = op["added"] + [e["line"] for e in op["give_back"]]
    if moved:
        from ...services.online_stock_writeback import writeback_after_sale

        writeback_after_sale(None, moved, store_id)
    # Owner hard rule: an edit that raises discounts must not leave the create-time
    # loyalty earn standing. Each claw lands once per step (regate_earn_after_edit);
    # one that could not finish (its row landed without its balance move, or the
    # ledger could not be read) keeps this row OPEN, so the next runner completes
    # it. Points already spent (underflow) go on the Tasks board (_points_task).
    regated: Dict[str, Any] = {"ok": False, "reason": "error"}
    with suppress(Exception):
        from ..loyalty import regate_earn_after_edit

        regated = regate_earn_after_edit(after, user_id=op["user"].get("user_id")) or {}
    owed = not regated.get("ok") and regated.get("reason") not in _LOYALTY_FINAL
    _points_task(after, regated)
    _end(op, None if failed or owed else "SETTLED")
    return not failed


def _points_task(order: dict, regated: dict) -> None:
    """Points a bill no longer earns, already spent by the customer, are on the
    Tasks board while they stand (not only in a log), and IMS closes the task
    once the edit's claw has landed. Closing it by hand waives points, so it is a
    head-office item (D81): an ADMIN holds it (a SUPERADMIN if there is none) and
    only ADMIN/SUPERADMIN may close it (tasks._ensure_may_close). Fail-soft:
    never stops a settle."""
    from ...dependencies import get_task_repository
    from ...services.task_triggers import _person_holding, create_system_task
    from ..loyalty import POINTS_UNCLAWED_REF
    from ..returns import _close_task as close

    order_id, reason = order.get("order_id"), regated.get("reason")
    ref = f"{POINTS_UNCLAWED_REF}{order_id}"
    with suppress(Exception):
        if regated.get("ok"):
            close(ref, {"user_id": "system"}, "IMS took back the points this bill no longer earns.")
            return
        if reason not in _POINTS_SPENT:
            return
        number, points = order.get("order_number") or order_id, regated.get("delta")
        owed = reason == "claw_move_underflow"
        create_system_task(
            get_task_repository(),
            title=f"Order {number}: {points} loyalty points not yet taken back",
            description=(
                f"An edit of order {number} lowered the points it earns by {points}, but customer "
                f"{order.get('customer_id')} had already spent them. "
                + ("IMS has recorded taking them back and takes them as soon as the customer's "
                   "balance covers them, then closes this task." if owed else
                   "IMS has not taken them back. Head office decides what to do; only an admin "
                   "can close this task, and IMS closes it if a later edit takes them back.")
            ),
            priority="P2",
            category="Accounts",
            store_id=order.get("store_id"),
            dedupe_ref=ref,
            assigned_to=(_person_holding("ADMIN", None, any_store=True)
                         or _person_holding("SUPERADMIN", None, any_store=True)),
            extra={"payload": {"order_id": order_id, "customer_id": order.get("customer_id"),
                               "points": points, "owed": owed}},
        )


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


async def _stranded_claims(coll) -> None:
    """D77: a given-back op is fenced for as long as it exists, with no window. A
    lens it had not reserved was voided on its cell (release_for_cancel void=True),
    so a stalled press can never reserve it later. A unit is claimed first-
    available, so it cannot be voided ahead: a press stalled past its give-back
    may still claim one and die before its own give-back. Every tick looks, in one
    query over all such rows whatever their age, for a unit still SOLD under such
    an op's claim, and gives that op back again (by its claim: nothing else). A
    lens whose cell did not exist at give-back (`lens_unfenced`) is given back
    again every tick until its cell carries the void or the reservation went back."""
    rows = list(coll.find({"state": "GIVEN_BACK", "$or": [{"takes_units": True}, {"lens_unfenced": True}]},
                          {"_id": 0, "op_id": 1, "order_id": 1, "takes_units": 1, "lens_unfenced": 1}))
    if not rows:
        return
    units = [r for r in rows if r.get("takes_units")]
    held = set(get_stock_repository().collection.distinct("claim_op", {
        "order_id": {"$in": sorted({r["order_id"] for r in units})},
        "status": "SOLD",
        "claim_op": {"$in": [r["op_id"] for r in units]},
    })) if units else set()
    for op_id in sorted(held | {r["op_id"] for r in rows if r.get("lens_unfenced")}):
        op = coll.find_one({"op_id": op_id}, {"_id": 0})
        if op is not None:
            await give_back(op)


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
        await _stranded_claims(coll)
    except Exception as exc:  # noqa: BLE001 -- the rest of the tick still runs
        logger.warning("[STOCK] stranded-claim check stopped: %s", exc)
    try:
        # Least recently tried first (never tried sorts first), so rows that stay
        # OPEN for long (owed points, stuck stock) rotate and never crowd out newer ones.
        rows = list(coll.find({"state": "OPEN", "created_at": {"$lt": cutoff}}, {"_id": 0})
                    .sort("tried_at", 1).limit(200))
        coll.update_many({"op_id": {"$in": [op["op_id"] for op in rows]}}, {"$set": {"tried_at": datetime.now()}})
        for op in rows:
            order = repo.find_by_id(op["order_id"])
            if order is None:
                out["unfinished"] += 1
                continue
            landed = op["op_id"] in (order.get("edit_ops") or [])
            done = await (settle(op, order, repo) if landed else give_back(op))
            # A save that landed after this read was settled (or is being, by its
            # own press): never a "stock did not settle" flag for a line it holds.
            landed = landed or bool(op.get("landed"))
            out["settled" if done and landed else "given_back" if done else "unfinished"] += 1
            if not landed and not done:
                line = (op["added"] or [{}])[0]
                flag_order(repo, op["order_id"], line.get("stock_id") or line.get("item_id"))
            elif not landed and order.get(RELEASE_FAILED_ITEM) in _names(op):
                _clear_flag(repo, op)
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
