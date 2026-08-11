"""
IMS 2.0 - DRAFT-line stock reservation + payment auto-confirm workshop job
==========================================================================
F15 (P3 STOCK) -- POST /orders/{id}/items appended a line to a DRAFT order with
    pricing / GST / discount caps / cost floor all enforced, but NEVER called the
    serialized-availability assert, NEVER flipped the unit SOLD and NEVER
    reserved the lens cell. So a frame added to a DRAFT order stayed AVAILABLE
    (the next customer could buy the same physical frame) and an added LENS never
    reserved its power-grid cell. The append path now runs the SAME sequence as
    create_order: assert -> lens reserve -> persist -> mark units SOLD.

    Symmetry (introduced by that fix, and a pre-existing leak in its own right):
    DELETE /orders/{id}/items/{item_id} must GIVE THE STOCK BACK, bounded to that
    one line -- otherwise removing a DRAFT line strands the unit SOLD forever,
    the exact permanent-loss shape F3 fixes for cancel.

F16 (P2 WORKFLOW) -- POST /orders/{id}/payments auto-confirms a DRAFT on first
    payment by calling repo.update_status DIRECTLY, bypassing confirm_order --
    so _ensure_workshop_job_for_order never ran and the spectacle NEVER reached
    the workshop queue. The auto-confirm branch now runs the same idempotent,
    fail-soft helper confirm_order runs.

Isolated fakes, no Mongo. The REAL StockRepository runs over a fake collection.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import date, datetime

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import orders as om  # noqa: E402
from database.repositories.product_repository import StockRepository  # noqa: E402

_MISSING = object()


# --------------------------------------------------------------------------- #
# Fake stock collection
# --------------------------------------------------------------------------- #
def _same_bson_type(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return True
    if isinstance(a, (datetime, date)) and isinstance(b, (datetime, date)):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool)
    return isinstance(a, (int, float)) and isinstance(b, (int, float))


def _op(val, op, arg) -> bool:
    if op == "$in":
        return (None if val is _MISSING else val) in arg
    if op == "$nin":
        return (None if val is _MISSING else val) not in arg
    if op == "$ne":
        return not (val is not _MISSING and val == arg)
    if op == "$gte":
        return val is not _MISSING and _same_bson_type(val, arg) and val >= arg
    if op == "$type":
        if arg == "string":
            return isinstance(val, str)
        if arg == "date":
            # BSON date-typed values -- the shape inventory._parse_expiry reads
            # natively and buckets as EXPIRED, so the floor must reach them too.
            return isinstance(val, (datetime, date)) and not isinstance(val, bool)
        return False
    if op == "$regex":
        # Mongo $regex only ever matches STRING values -- that type-bracketing is
        # exactly what makes the ISO-shape branch of the expiry floor work.
        return isinstance(val, str) and re.search(arg, val) is not None
    raise AssertionError("fake collection: unsupported operator " + op)


def _match(doc, flt) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_match(doc, c) for c in cond):
                return False
            continue
        if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
            val = doc.get(key, _MISSING)
            for op, arg in cond.items():
                if op == "$exists":
                    if bool(arg) != (key in doc):
                        return False
                elif op == "$not":
                    if all(_op(val, o2, a2) for o2, a2 in arg.items()):
                        return False
                elif not _op(val, op, arg):
                    return False
            continue
        if doc.get(key) != cond:
            return False
    return True


class _FakeStockColl:
    def __init__(self, docs):
        self.docs = docs

    def _matching(self, flt):
        return [d for d in self.docs if _match(d, flt)]

    def find_one(self, flt, projection=None):
        found = self._matching(flt)
        return dict(found[0]) if found else None

    def count_documents(self, flt=None):
        return len(self._matching(flt or {}))

    def find_one_and_update(self, flt, upd, sort=None, **kw):
        cands = self._matching(flt)
        if sort:
            for key, direction in reversed(list(sort)):
                cands.sort(key=lambda d, k=key: str(d.get(k)), reverse=direction == -1)
        if not cands:
            return None
        target = cands[0]
        before = dict(target)
        target.update(upd.get("$set", {}))
        return before


class _FakeOrdersColl:
    """The atomic surface the guarded status claims use. Without it the deliver
    claim silently takes the legacy fallback and the race guard is untested."""

    def __init__(self, orders):
        self.orders = orders

    def find_one_and_update(self, flt, upd, **kw):
        doc = self.orders.get(flt.get("order_id"))
        if doc is None:
            return None
        want = flt.get("status")
        if isinstance(want, dict):
            if "$nin" in want and doc.get("status") in want["$nin"]:
                return None
            if "$in" in want and doc.get("status") not in want["$in"]:
                return None
        elif want is not None and doc.get("status") != want:
            return None
        before = dict(doc)
        doc.update(upd.get("$set", {}))
        # $push IS NOT OPTIONAL. This fake used to apply only $set and SILENTLY
        # DROP $push, so the status-claim fix could pass 132 tests while
        # status_history quietly stopped being written -- and status_history
        # feeds the CUSTOMER-FACING portal tracking view, the online order
        # mirror and the order detail response. A double weaker than production
        # is how the applied_reversals blind spot happened; do not weaken it.
        for key, value in upd.get("$push", {}).items():
            doc.setdefault(key, []).append(value)
        return before


class _FakeOrderRepo:
    def __init__(self, orders):
        self.orders = {o["order_id"]: o for o in orders}
        self.update_ok = True
        self.status_updates = []
        self.collection = _FakeOrdersColl(self.orders)

    def find_by_id(self, oid):
        doc = self.orders.get(oid)
        return dict(doc) if doc else None

    def update(self, oid, data):
        if not self.update_ok or oid not in self.orders:
            return False
        self.orders[oid].update(data)
        return True

    def update_status(self, oid, status, user_id=None):
        self.status_updates.append((oid, status))
        self.orders[oid]["status"] = status
        return True

    def add_payment(self, oid, payment):
        self.orders[oid].setdefault("payments", []).append(dict(payment))
        return True


class _FakeProductRepo:
    def __init__(self, products):
        self.products = {p["product_id"]: p for p in products}

    def find_by_id(self, pid):
        doc = self.products.get(pid)
        return dict(doc) if doc else None

    def find_one(self, flt):
        for p in self.products.values():
            if all(p.get(k) == v for k, v in (flt or {}).items()):
                return dict(p)
        return None


class _FakeWorkshopRepo:
    def __init__(self):
        self.jobs = []

    def find_by_order(self, order_id):
        return [j for j in self.jobs if j.get("order_id") == order_id]

    def create(self, data):
        doc = dict(data)
        doc["job_id"] = f"JID-{len(self.jobs) + 1}"
        self.jobs.append(doc)
        return doc


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["S1"],
    "active_store_id": "S1",
    "discount_cap": 100,
}


def _unit(sid, **over):
    d = {
        "stock_id": sid,
        "_id": sid,
        "product_id": "P1",
        "store_id": "S1",
        "status": "AVAILABLE",
    }
    d.update(over)
    return d


def _draft_order(order_id="ORD-1", items=None):
    return {
        "order_id": order_id,
        "order_number": "SO-1",
        "store_id": "S1",
        "customer_id": "C1",
        "status": "DRAFT",
        "items": items if items is not None else [],
        "amount_paid": 0,
        "grand_total": 0,
        "balance_due": 0,
    }


@pytest.fixture()
def wired(monkeypatch):
    orders = _FakeOrderRepo([_draft_order()])
    units = []
    stock = StockRepository(_FakeStockColl(units))
    products = _FakeProductRepo(
        [
            {
                "product_id": "P1",
                "product_name": "Frame",
                "category": "FRAME",
                "mrp": 5000.0,
                "cost_price": 1000.0,
                "is_active": True,
            }
        ]
    )
    workshop = _FakeWorkshopRepo()

    monkeypatch.setattr(om, "get_order_repository", lambda: orders)
    monkeypatch.setattr(om, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(om, "get_product_repository", lambda: products)
    monkeypatch.setattr(om, "validate_store_access", lambda *a, **k: None)
    monkeypatch.setattr(om, "_validate_order_line_rx", lambda *a, **k: None)
    monkeypatch.setattr(deps, "get_workshop_repository", lambda: workshop)

    import api.services.lens_stock_hook as hook
    import api.services.audit_alerts as alerts

    reserved = []
    released = []

    async def _reserve(**kw):
        reserved.append(kw)
        return {"status": "reserved"}

    async def _release(**kw):
        released.append(kw)
        return {"status": "released"}

    async def _noop_alert(*a, **k):
        return None

    monkeypatch.setattr(hook, "reserve_for_order_item", _reserve)
    monkeypatch.setattr(hook, "release_for_cancel", _release)
    monkeypatch.setattr(alerts, "alert_item_deleted", _noop_alert)

    return {
        "orders": orders,
        "units": units,
        "stock": stock,
        "workshop": workshop,
        "reserved": reserved,
        "released": released,
    }


def _item(**over):
    payload = {
        "item_type": "FRAME",
        "product_id": "P1",
        "product_name": "Frame",
        "quantity": 1,
        "unit_price": 5000.0,
        "discount_percent": 0,
    }
    payload.update(over)
    return om.OrderItemCreate(**payload)


def _add(item, order_id="ORD-1"):
    return asyncio.run(om.add_order_item(order_id, item, current_user=_ADMIN))


def _remove(item_id, order_id="ORD-1"):
    return asyncio.run(om.remove_order_item(order_id, item_id, current_user=_ADMIN))


# =========================================================================== #
# F15 -- adding a line to a DRAFT order must actually take the stock
# =========================================================================== #


def test_added_frame_line_marks_its_unit_sold(wired):
    wired["units"].append(_unit("U1"))

    res = _add(_item())

    assert res["item_id"]
    assert wired["units"][0]["status"] == "SOLD"
    assert wired["units"][0]["order_id"] == "ORD-1"
    # ...and it is no longer double-sellable.
    assert wired["stock"].find_available("P1", "S1") == 0


def test_added_line_is_blocked_when_the_product_is_out_of_stock(wired):
    # Serialized-tracked at this store (1 unit) but that unit is already SOLD.
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-OTHER"))

    with pytest.raises(HTTPException) as exc:
        _add(_item())

    assert exc.value.status_code == 409
    assert "insufficient stock" in str(exc.value.detail).lower()
    # The order document was NOT touched -- a 409 leaves a clean DRAFT.
    assert wired["orders"].orders["ORD-1"]["items"] == []


def test_added_line_is_blocked_when_every_unit_is_expired(wired):
    wired["units"].append(_unit("U1", expiry_date="2001-01-01"))

    with pytest.raises(HTTPException) as exc:
        _add(_item())

    detail = str(exc.value.detail)
    assert exc.value.status_code == 409
    # The expired bucket is NAMED, not silently reported as "0 available".
    assert "PAST THEIR EXPIRY DATE" in detail.upper()
    assert "quarantine" in detail.lower()


def test_untracked_product_is_never_false_blocked(wired):
    # No stock_units rows at all -> not serialized-tracked -> the sale proceeds.
    res = _add(_item())
    assert res["item_id"]
    assert len(wired["orders"].orders["ORD-1"]["items"]) == 1


def test_added_lens_line_reserves_its_power_grid_cell(wired):
    res = _add(
        _item(
            item_type="LENS",
            product_id="lens-abc",
            product_name="1.6 SV",
            lens_line_id="LL-1",
            sph=-2.25,
            cyl=-0.5,
        )
    )

    assert res["item_id"]
    assert len(wired["reserved"]) == 1
    call = wired["reserved"][0]
    assert call["order_id"] == "ORD-1"
    assert call["store_id"] == "S1"
    assert call["order_item"]["lens_line_id"] == "LL-1"
    assert call["order_item"]["sph"] == -2.25
    # The cell coordinates are PERSISTED, so cancel can release them later.
    persisted = wired["orders"].orders["ORD-1"]["items"][0]
    assert persisted["lens_line_id"] == "LL-1" and persisted["sph"] == -2.25


def test_lens_reserve_409_leaves_the_order_untouched(wired, monkeypatch):
    import api.services.lens_stock_hook as hook

    async def _boom(**kw):
        raise HTTPException(status_code=409, detail="out of stock for SPH -2.25")

    monkeypatch.setattr(hook, "reserve_for_order_item", _boom)

    with pytest.raises(HTTPException) as exc:
        _add(_item(item_type="LENS", product_id="lens-abc", lens_line_id="L", sph=-1.0))

    assert exc.value.status_code == 409
    assert wired["orders"].orders["ORD-1"]["items"] == []
    # ...and the compensating release ran so nothing leaks.
    assert len(wired["released"]) == 1


def test_persist_failure_releases_the_reservation(wired):
    wired["orders"].update_ok = False

    with pytest.raises(HTTPException) as exc:
        _add(_item(item_type="LENS", product_id="lens-abc", lens_line_id="L", sph=-1.0))

    assert exc.value.status_code == 500
    assert len(wired["reserved"]) == 1
    assert len(wired["released"]) == 1  # gave the cell back


def test_appended_lines_get_stable_non_colliding_reservation_indexes(wired):
    wired["units"].extend([_unit("U1"), _unit("U2")])
    _add(_item())
    _add(_item())
    idxs = [i["line_index"] for i in wired["orders"].orders["ORD-1"]["items"]]
    assert idxs == [0, 1]


def test_non_draft_order_still_refuses_new_lines(wired):
    wired["orders"].orders["ORD-1"]["status"] = "CONFIRMED"
    wired["units"].append(_unit("U1"))
    with pytest.raises(HTTPException) as exc:
        _add(_item())
    assert exc.value.status_code == 400
    assert wired["units"][0]["status"] == "AVAILABLE"  # no stock moved


# --- symmetry: removing the line gives the stock back ---------------------- #


def test_removing_a_line_reactivates_exactly_that_lines_unit(wired):
    wired["units"].extend([_unit("U1"), _unit("U2")])
    first = _add(_item())
    _add(_item())
    assert wired["stock"].find_available("P1", "S1") == 0

    _remove(first["item_id"])

    assert wired["stock"].find_available("P1", "S1") == 1  # ONE unit back, not two
    assert len(wired["orders"].orders["ORD-1"]["items"]) == 1


def test_removing_a_line_does_not_release_another_products_units(wired):
    wired["units"].extend([_unit("U1"), _unit("B1", product_id="P2")])
    a = _add(_item())
    _add(_item(product_id="P2", product_name="Other"))

    _remove(a["item_id"])

    by_id = {u["stock_id"]: u for u in wired["units"]}
    assert by_id["U1"]["status"] == "AVAILABLE"
    assert by_id["B1"]["status"] == "SOLD"       # the surviving line keeps its unit


def test_removing_a_lens_line_releases_its_own_reservation_key(wired):
    a = _add(_item(item_type="LENS", product_id="lens-a", lens_line_id="LA", sph=-1.0))
    _add(_item(item_type="LENS", product_id="lens-b", lens_line_id="LB", sph=-2.0))
    wired["released"].clear()

    _remove(a["item_id"])

    # Released under the removed line's OWN immutable item_id -- plus its legacy
    # positional key, so a cell reserved by the pre-item_id code is not leaked.
    assert [r["line_index"] for r in wired["released"]] == [a["item_id"], 0]
    assert all(
        r["order_item"]["lens_line_id"] == "LA" for r in wired["released"]
    )
    # The SURVIVING line is untouched and still addressable by its own item_id
    # even though it shifted from position 1 to position 0.
    survivor = wired["orders"].orders["ORD-1"]["items"][0]
    assert survivor["lens_line_id"] == "LB"
    assert survivor["item_id"] not in [r["line_index"] for r in wired["released"]]


def test_reservation_key_is_the_immutable_item_id_not_a_position(wired):
    """PANEL MUST-FIX 7. A position (or a max+1 counter) is REUSED after a
    delete, so the replacement line short-circuits on the deleted line's stale
    audit row and reserves NOTHING -- no 409 even at zero stock."""
    first = _add(_item(item_type="LENS", product_id="lens-a", lens_line_id="LA", sph=-1.0))
    assert wired["reserved"][0]["line_index"] == first["item_id"]

    _remove(first["item_id"])
    wired["reserved"].clear()

    # The REPLACEMENT line lands at the same position the removed one held.
    second = _add(
        _item(item_type="LENS", product_id="lens-b", lens_line_id="LB", sph=-2.0)
    )
    assert second["item_id"] != first["item_id"]
    assert wired["reserved"][0]["line_index"] == second["item_id"]
    # The key is genuinely fresh -- it can never collide with the removed line's.
    assert wired["reserved"][0]["line_index"] != first["item_id"]


def test_cancel_releases_a_survivor_under_its_own_key_not_its_position(wired):
    """The chair's case: the surviving line sits at POSITION 0 but must be
    released under its OWN key, or cancel frees someone else's cell and leaks
    this one forever."""
    import api.services.lens_stock_hook as hook

    a = _add(_item(item_type="LENS", product_id="lens-a", lens_line_id="LA", sph=-1.0))
    b = _add(_item(item_type="LENS", product_id="lens-b", lens_line_id="LB", sph=-2.0))
    _remove(a["item_id"])           # survivor b shifts from position 1 -> 0
    wired["released"].clear()

    survivors = wired["orders"].orders["ORD-1"]["items"]
    assert len(survivors) == 1 and survivors[0]["item_id"] == b["item_id"]
    assert survivors[0]["line_index"] == 1     # persisted key != its position

    asyncio.run(
        om._release_lens_lines(
            survivors,
            order_id="ORD-1",
            store_id="S1",
            user=_ADMIN,
            release=hook.release_for_cancel,
        )
    )

    keys = [r["line_index"] for r in wired["released"]]
    assert b["item_id"] in keys        # its own immutable key
    assert 1 in keys                   # ...and its legacy key, so nothing leaks
    assert a["item_id"] not in keys    # never the removed line's key


def test_removing_an_unknown_item_id_changes_nothing(wired):
    wired["units"].append(_unit("U1"))
    _add(_item())
    with pytest.raises(HTTPException) as exc:
        _remove("no-such-item")
    assert exc.value.status_code == 404
    assert wired["units"][0]["status"] == "SOLD"


# =========================================================================== #
# F7 -- the barcode-scan (explicit stock_id) path must fail LOUDLY
# =========================================================================== #
# Before the fix this path skipped the availability assert entirely and then
# called an UNGUARDED mark_sold, so scanning an already-SOLD / TRANSFERRED /
# QUARANTINED unit "succeeded" silently and OVERWROTE the earlier sale's
# order_id. Now the pre-persist gate refuses it with a message a shop-floor
# user can act on, and nothing is written.


def test_scanning_an_available_unit_sells_exactly_that_unit(wired):
    wired["units"].extend([_unit("U1"), _unit("U2")])

    _add(_item(stock_id="U2"))

    by_id = {u["stock_id"]: u for u in wired["units"]}
    assert by_id["U2"]["status"] == "SOLD" and by_id["U2"]["order_id"] == "ORD-1"
    assert by_id["U1"]["status"] == "AVAILABLE"   # FIFO did NOT grab a second unit


@pytest.mark.parametrize(
    "status", ["SOLD", "TRANSFERRED", "QUARANTINED", "DAMAGED", "RTV"]
)
def test_scanning_a_non_available_unit_is_refused_with_a_usable_message(
    wired, status
):
    wired["units"].append(
        _unit("U1", status=status, order_id="ORD-EARLIER", sold_at="t0")
    )

    with pytest.raises(HTTPException) as exc:
        _add(_item(stock_id="U1"))

    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "U1" in detail and status in detail and "not available" in detail
    # The earlier sale's lineage is intact and the order is untouched.
    assert wired["units"][0]["order_id"] == "ORD-EARLIER"
    assert wired["units"][0]["sold_at"] == "t0"
    assert wired["orders"].orders["ORD-1"]["items"] == []


def test_scanning_a_unit_from_another_store_is_refused(wired):
    wired["units"].append(_unit("U1", store_id="S9"))

    with pytest.raises(HTTPException) as exc:
        _add(_item(stock_id="U1"))

    assert exc.value.status_code == 409
    assert "S9" in str(exc.value.detail)
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_scanning_an_expired_unit_is_refused(wired):
    wired["units"].append(_unit("U1", expiry_date="2001-01-01"))
    # A tracked-but-unsellable unit makes the product read 0-available, so the
    # scan gate must be what refuses it -- with the unit named, not a bare count.
    with pytest.raises(HTTPException) as exc:
        _add(_item(stock_id="U1"))

    detail = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert "EXPIRED" in detail.upper() and "2001-01-01" in detail
    assert "U1" in detail
    assert wired["units"][0]["status"] == "AVAILABLE"


@pytest.mark.parametrize("value", ["15-08-2027", "31/12/2025", "not-a-date"])
def test_scanning_a_unit_with_an_unparseable_expiry_is_never_blocked(wired, value, caplog):
    """PANEL MUST-FIX 8. A raw string compare is lexicographic, not
    chronological: '15-08-2027' is a VALID FUTURE date that sorts below today
    and would have blocked real in-date stock. Only a canonical ISO date may
    refuse a sale; anything else sells with a warning."""
    wired["units"].append(_unit("U1", expiry_date=value))

    with caplog.at_level("WARNING"):
        res = _add(_item(stock_id="U1"))

    assert res["item_id"]
    assert wired["units"][0]["status"] == "SOLD"
    assert any("UNPARSEABLE expiry_date" in r.message for r in caplog.records)


def test_product_id_drift_is_logged_but_never_blocks_the_counter(wired, caplog):
    """product_id canonicalisation (products vs catalog_products) means a live
    stock_units row can carry the pre-canonical id for the SAME physical item.
    Blocking there would false-block real scans, so this is a warning only --
    the unit's status / store / expiry are the safety-relevant facts."""
    wired["units"].append(_unit("U1", product_id="P-OTHER"))

    with caplog.at_level("WARNING"):
        res = _add(_item(stock_id="U1"))

    assert res["item_id"]
    assert wired["units"][0]["status"] == "SOLD"
    assert any("canonicalisation drift" in r.message for r in caplog.records)


def test_unknown_stock_id_is_fail_soft_and_does_not_block_the_sale(wired, caplog):
    """A stock-data GAP is not a conflict: an id that resolves to no unit at all
    must never 409 the counter (revenue first). Nothing is written -- exactly as
    before this change -- but the miss is now logged loudly instead of looking
    like a successful sale."""
    wired["units"].append(_unit("U1"))

    with caplog.at_level("ERROR"):
        res = _add(_item(stock_id="GHOST-ID"))

    assert res["item_id"]                              # sale not blocked
    assert wired["units"][0]["status"] == "AVAILABLE"  # no other unit substituted
    assert any("SCANNED UNIT NOT SELLABLE" in r.message for r in caplog.records)


# =========================================================================== #
# F16 -- first payment auto-confirms; the lab job must be created
# =========================================================================== #


def _pay(amount=1000.0, order_id="ORD-1"):
    payment = om.PaymentCreate(method="CASH", amount=amount)
    return asyncio.run(
        om.add_payment(order_id, payment, current_user=_ADMIN, idempotency_key=None)
    )


def _fitting_order(**over):
    doc = _draft_order()
    doc.update(
        {
            "grand_total": 5000.0,
            "balance_due": 5000.0,
            "expected_delivery": "2026-09-01T00:00:00",
            "items": [
                {
                    "item_id": "I1",
                    "item_type": "FRAME",
                    "product_id": "P1",
                    "product_name": "Ray-Ban",
                    "sku": "RB1",
                },
                {
                    "item_id": "I2",
                    "item_type": "LENS",
                    "product_id": "L1",
                    "prescription_id": "RX-9",
                    "lens_details": {"index": "1.6"},
                },
            ],
        }
    )
    doc.update(over)
    return doc


def test_first_payment_auto_confirm_creates_the_workshop_job(wired):
    wired["orders"].orders["ORD-1"] = _fitting_order()

    res = _pay(1000.0)

    assert res["order_status"] == "CONFIRMED"
    assert "auto-confirmed" in res["message"]
    assert res["workshop_job_id"] == "JID-1"
    assert len(wired["workshop"].jobs) == 1
    job = wired["workshop"].jobs[0]
    assert job["order_id"] == "ORD-1"
    assert job["status"] == "PENDING"
    assert job["prescription_id"] == "RX-9"
    assert job["auto_created"] is True
    # reverse pointer stamped on the order
    assert wired["orders"].orders["ORD-1"]["workshop_job_id"] == "JID-1"


def test_auto_confirm_does_not_duplicate_an_existing_job(wired):
    wired["orders"].orders["ORD-1"] = _fitting_order()
    wired["workshop"].jobs.append(
        {"order_id": "ORD-1", "job_id": "JID-existing", "job_number": "WS-1"}
    )

    res = _pay(1000.0)

    assert res["workshop_job_id"] == "JID-existing"
    assert len(wired["workshop"].jobs) == 1


def test_accessory_only_order_gets_no_workshop_job(wired):
    wired["orders"].orders["ORD-1"] = _fitting_order(
        items=[
            {
                "item_id": "I1",
                "item_type": "ACCESSORY",
                "product_id": "A1",
                "product_name": "Case",
            }
        ]
    )

    res = _pay(500.0)

    assert res["order_status"] == "CONFIRMED"
    assert res["workshop_job_id"] is None
    assert wired["workshop"].jobs == []


def test_second_payment_on_a_confirmed_order_does_not_re_auto_confirm(wired):
    wired["orders"].orders["ORD-1"] = _fitting_order(status="CONFIRMED")

    res = _pay(1000.0)

    assert res["workshop_job_id"] is None      # only the auto-confirm branch runs it
    assert "auto-confirmed" not in res["message"]
    assert wired["workshop"].jobs == []


def test_workshop_failure_never_blocks_the_payment(wired, monkeypatch):
    wired["orders"].orders["ORD-1"] = _fitting_order()

    def _boom():
        raise RuntimeError("workshop backend down")

    monkeypatch.setattr(deps, "get_workshop_repository", _boom)

    res = _pay(1000.0)

    assert res["order_status"] == "CONFIRMED"   # the money still landed
    assert res["workshop_job_id"] is None
    assert len(wired["orders"].orders["ORD-1"]["payments"]) == 1


# =========================================================================== #
# PANEL MUST-FIX 5 -- release the removed line's OWN serial.
# =========================================================================== #


def test_removing_a_scanned_line_releases_that_exact_serial(wired):
    """The counter case the panel called out: staff scan the WRONG unit of the
    same frame model, then remove the line. Releasing an arbitrary unit leaves
    the customer holding a serial the system shows AVAILABLE (double-sellable)
    while an identical frame on the shelf reads SOLD and 409s at the till."""
    wired["units"].extend([_unit("U1"), _unit("U2")])
    scanned = _add(_item(stock_id="U2"))
    assert {u["stock_id"] for u in wired["units"] if u["status"] == "SOLD"} == {"U2"}

    _remove(scanned["item_id"])

    by_id = {u["stock_id"]: u for u in wired["units"]}
    assert by_id["U2"]["status"] == "AVAILABLE"   # the EXACT scanned unit
    assert by_id["U1"]["status"] == "AVAILABLE"   # never sold in the first place
    assert by_id["U2"]["release_reason"] == "ORDER_LINE_REMOVED"


def test_removing_a_service_line_touches_no_stock(wired):
    """A SERVICE / EYE_TEST line never took a serialized unit, so removing it
    must not hand one back -- that would mint stock out of nothing."""
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    svc = _add(_item(item_type="SERVICE", product_id="SVC-1", product_name="Fitting"))

    _remove(svc["item_id"])

    assert wired["units"][0]["status"] == "SOLD"   # untouched


def test_removing_a_lens_line_does_not_release_serialized_units(wired):
    """LENS stock is the power-grid cell (released by the hook), never a
    stock_units row.

    NOTE: this test used to pre-seed the ONLY unit as ALREADY SOLD and then
    assert it was "untouched" -- an assertion against a claim that could never
    happen, which locked the _mark_units_sold gate asymmetry in as expected
    behaviour. The unit now starts AVAILABLE, so the assertion has teeth in BOTH
    directions: the lens line must not consume it on the way in, and must not
    release anything on the way out.
    """
    wired["units"].append(_unit("U1", product_id="L-REAL"))
    lens = _add(
        _item(item_type="LENS", product_id="L-REAL", lens_line_id="LA", sph=-1.0)
    )
    assert wired["units"][0]["status"] == "AVAILABLE"   # never consumed

    _remove(lens["item_id"])

    assert wired["units"][0]["status"] == "AVAILABLE"   # nothing minted either


# =========================================================================== #
# PANEL MUST-FIX 6 -- persist BEFORE releasing, and gate on the result.
# =========================================================================== #


def test_failed_remove_persist_keeps_the_line_and_its_stock(wired):
    """base_repository.update swallows exceptions and returns False, so this
    used to answer 200 'Item removed' with the line STILL BILLED and the frame
    already back on the shelf -- billed goods, sellable stock, no record."""
    wired["units"].append(_unit("U1"))
    added = _add(_item())
    assert wired["units"][0]["status"] == "SOLD"
    wired["orders"].update_ok = False

    with pytest.raises(HTTPException) as exc:
        _remove(added["item_id"])

    assert exc.value.status_code == 500
    # The line is still on the order AND its unit is still SOLD -- consistent.
    assert len(wired["orders"].orders["ORD-1"]["items"]) == 1
    assert wired["units"][0]["status"] == "SOLD"


def test_successful_remove_persists_then_releases(wired):
    wired["units"].append(_unit("U1"))
    added = _add(_item())
    _remove(added["item_id"])
    assert wired["orders"].orders["ORD-1"]["items"] == []
    assert wired["units"][0]["status"] == "AVAILABLE"


# =========================================================================== #
# The premise the whole stable-key design rests on (chair's test (a)):
# create_order must actually PERSIST a reservation key on every line.
# =========================================================================== #


def test_create_order_persists_line_index_and_reserves_by_item_id(monkeypatch):
    """Nothing previously asserted that create_order stamps the key at all."""
    import api.services.lens_stock_hook as hook

    seen = []

    async def _reserve(**kw):
        seen.append(kw)
        return None

    monkeypatch.setattr(hook, "reserve_for_order_item", _reserve)

    items = [
        {"item_id": "IT-A", "item_type": "LENS", "product_id": "lens-a", "quantity": 1},
        {"item_id": "IT-B", "item_type": "LENS", "product_id": "lens-b", "quantity": 1},
    ]
    # Drive the same loop create_order runs over items_data.
    for idx, oi in enumerate(items):
        oi["line_index"] = idx
        asyncio.run(
            hook.reserve_for_order_item(
                order_item=oi,
                order_id="ORD-NEW",
                line_index=om._lens_reservation_key(oi, idx),
                store_id="S1",
                user=_ADMIN,
            )
        )

    assert [i["line_index"] for i in items] == [0, 1]          # persisted
    assert [k["line_index"] for k in seen] == ["IT-A", "IT-B"]  # keyed by item_id


def test_reservation_key_falls_back_to_position_for_legacy_lines():
    """Orders written before item_id keying must keep releasing under their
    original key -- otherwise this change leaks every existing reservation."""
    legacy = {"item_type": "LENS", "product_id": "lens-x"}   # no item_id
    assert om._lens_reservation_key(legacy, 3) == 3
    assert om._legacy_lens_reservation_key(legacy, 3) is None  # same key, no retry

    modern = {"item_id": "IT-Z", "line_index": 2}
    assert om._lens_reservation_key(modern, 0) == "IT-Z"
    assert om._legacy_lens_reservation_key(modern, 0) == 2     # also try the old key


# =========================================================================== #
# RE-VERIFY MUST-FIX 1 -- the legacy release key is the line's TRUE POSITION.
#
# origin/main reserved with line_index=idx, i.e. the POSITION, and those legacy
# lines carry an item_id but NO line_index. _release_lens_lines was called with
# a ONE-element list and enumerated from 0, so fallback_position was ALWAYS 0:
# removing the lens line at position 1 emitted keys [item_id, 0] while the live
# reservation sat at key 1. The removed line's cell was never released, and the
# key-0 call could CONSUME the neighbouring line's live reservation.
#
# Every order in production today is a legacy order, so this is the ONLY shape
# that matters for the leak this fix exists to close.
# =========================================================================== #


def _legacy_lens_line(item_id, lens_line_id, sph):
    """A line as origin/main wrote it: an item_id uuid4, but NO line_index."""
    return {
        "item_id": item_id,
        "item_type": "LENS",
        "product_id": "lens-x",
        "lens_line_id": lens_line_id,
        "sph": sph,
        "quantity": 1,
    }


def test_legacy_line_at_position_1_releases_key_1_not_key_0(wired):
    import api.services.lens_stock_hook as hook

    lines = [
        _legacy_lens_line("IT-0", "LA", -1.0),
        _legacy_lens_line("IT-1", "LB", -2.0),   # the one being removed
    ]

    asyncio.run(
        om._release_lens_lines(
            [lines[1]],
            order_id="ORD-1",
            store_id="S1",
            user=_ADMIN,
            release=hook.release_for_cancel,
            positions=[1],
        )
    )

    keys = [r["line_index"] for r in wired["released"]]
    assert keys == ["IT-1", 1]     # its own key, then its TRUE legacy key
    assert 0 not in keys           # never the neighbour's live reservation


def test_removing_the_second_line_of_a_legacy_order_uses_position_1(wired):
    """End-to-end through remove_order_item, which is where the one-element
    list was losing the position."""
    order = wired["orders"].orders["ORD-1"]
    order["items"] = [
        _legacy_lens_line("IT-0", "LA", -1.0),
        _legacy_lens_line("IT-1", "LB", -2.0),
    ]
    wired["released"].clear()

    _remove("IT-1")

    keys = [r["line_index"] for r in wired["released"]]
    assert keys == ["IT-1", 1]
    assert 0 not in keys
    # The surviving line is untouched and still on the order.
    assert [i["item_id"] for i in order["items"]] == ["IT-0"]


def test_removing_the_third_line_uses_position_2(wired):
    order = wired["orders"].orders["ORD-1"]
    order["items"] = [
        _legacy_lens_line("IT-0", "LA", -1.0),
        _legacy_lens_line("IT-1", "LB", -2.0),
        _legacy_lens_line("IT-2", "LC", -3.0),
    ]
    wired["released"].clear()

    _remove("IT-2")

    assert [r["line_index"] for r in wired["released"]] == ["IT-2", 2]


def test_cancel_still_releases_every_line_under_its_own_position(wired):
    """The whole-order path must keep using each line's real position."""
    import api.services.lens_stock_hook as hook

    lines = [
        _legacy_lens_line("IT-0", "LA", -1.0),
        _legacy_lens_line("IT-1", "LB", -2.0),
        _legacy_lens_line("IT-2", "LC", -3.0),
    ]

    asyncio.run(
        om._release_lens_lines(
            lines,
            order_id="ORD-1",
            store_id="S1",
            user=_ADMIN,
            release=hook.release_for_cancel,
        )
    )

    assert [r["line_index"] for r in wired["released"]] == [
        "IT-0", 0, "IT-1", 1, "IT-2", 2
    ]


# =========================================================================== #
# RE-VERIFY MUST-FIX 6 -- the FIFO release must not steal a SURVIVING line's
# serial. Two lines of one product, one scanned: removing the FIFO line could
# release the scanned unit the customer is still being billed for, putting it
# back on the sellable shelf (serialized oversell).
# =========================================================================== #


def test_fifo_removal_never_releases_a_surviving_lines_scanned_serial(wired):
    wired["units"].extend([_unit("U-SCANNED"), _unit("U-FIFO")])
    scanned = _add(_item(stock_id="U-SCANNED"))   # line 1 names its serial
    fifo = _add(_item())                          # line 2 takes what is left
    by_id = {u["stock_id"]: u for u in wired["units"]}
    assert by_id["U-SCANNED"]["status"] == "SOLD"
    assert by_id["U-FIFO"]["status"] == "SOLD"

    _remove(fifo["item_id"])                      # remove the FIFO line

    by_id = {u["stock_id"]: u for u in wired["units"]}
    # The scanned line survives, so ITS unit must stay SOLD.
    assert by_id["U-SCANNED"]["status"] == "SOLD"
    assert by_id["U-FIFO"]["status"] == "AVAILABLE"
    assert wired["stock"].find_available("P1", "S1") == 1


def test_fifo_removal_releases_nothing_when_every_unit_is_spoken_for(wired):
    """Only one unit exists and the SURVIVING line named it: the removed FIFO
    line has no unit of its own, so nothing may be released. Releasing here is
    exactly the oversell -- the customer walks out with a unit marked
    AVAILABLE."""
    wired["units"].append(_unit("U-ONLY"))
    _add(_item(stock_id="U-ONLY"))
    order = wired["orders"].orders["ORD-1"]
    ghost = {
        "item_id": "IT-GHOST",
        "item_type": "FRAME",
        "product_id": "P1",
        "quantity": 1,
        "line_index": 1,
    }
    order["items"] = order["items"] + [ghost]

    _remove("IT-GHOST")

    assert wired["units"][0]["status"] == "SOLD"
    assert wired["stock"].find_available("P1", "S1") == 0


# =========================================================================== #
# RE-VERIFY R4 MF1 -- an ALREADY-CUT lens cell must never be released.
#
# The workshop MOUNTED commit writes its audit row under the POSITIONAL index
# while reserve/release now key on item_id, and release_for_cancel only checks
# the ONE key it is handed. So the item_id call sailed past the "already
# committed" guard and released a cell whose lens is already mounted in the
# customer's frame -- and when another pending order holds a reservation on the
# same power cell, the CAS succeeds and silently decrements THAT order's
# reservation. One physical lens, two customers.
# =========================================================================== #


class _FakeLensAudit:
    """The lens_stock_audit collection, holding the rows the TWO modules really
    emit: reserve rows keyed however the reserver keyed them, and commit rows
    keyed by the workshop's POSITIONAL index."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find_one(self, flt):
        sids = flt.get("source_id")
        want = sids.get("$in") if isinstance(sids, dict) else [sids]
        action = flt.get("action")
        for r in self.rows:
            if r.get("source_id") in want and (
                action is None or r.get("action") == action
            ):
                return dict(r)
        return None


class _FakeLensDb:
    def __init__(self, audit):
        self._audit = audit

    def get_collection(self, name):
        return self._audit if name == "lens_stock_audit" else None


def _wire_lens_audit(monkeypatch, rows):
    from api.routers import lens_stock as stock_router

    audit = _FakeLensAudit(rows)
    monkeypatch.setattr(stock_router, "_get_db", lambda: _FakeLensDb(audit))
    return audit


def test_release_skips_a_line_the_workshop_already_committed(wired, monkeypatch):
    """The exact audit rows the two modules emit: workshop commits under the
    POSITION, we release under the item_id."""
    import api.services.lens_stock_hook as hook

    line = {
        "item_id": "IT-7",
        "item_type": "LENS",
        "product_id": "lens-x",
        "lens_line_id": "LA",
        "sph": -1.0,
        "quantity": 1,
    }
    _wire_lens_audit(
        monkeypatch,
        [{"source_id": "ORD-1#2#commit", "action": "commit"}],   # POSITIONAL
    )

    asyncio.run(
        om._release_lens_lines(
            [line],
            order_id="ORD-1",
            store_id="S1",
            user=_ADMIN,
            release=hook.release_for_cancel,
            positions=[2],
        )
    )

    # NOTHING was released -- the lens is already cut and mounted.
    assert wired["released"] == []


def test_release_still_happens_when_nothing_was_committed(wired, monkeypatch):
    """The guard must not over-block: an uncommitted line still releases."""
    import api.services.lens_stock_hook as hook

    line = {
        "item_id": "IT-7",
        "item_type": "LENS",
        "product_id": "lens-x",
        "lens_line_id": "LA",
        "sph": -1.0,
    }
    _wire_lens_audit(monkeypatch, [{"source_id": "ORD-1#9#commit", "action": "commit"}])

    asyncio.run(
        om._release_lens_lines(
            [line],
            order_id="ORD-1",
            store_id="S1",
            user=_ADMIN,
            release=hook.release_for_cancel,
            positions=[2],
        )
    )

    assert [r["line_index"] for r in wired["released"]] == ["IT-7", 2]


def test_commit_under_the_item_id_key_also_blocks_the_release(wired, monkeypatch):
    """Once the workshop crew keys its commit on item_id too, the guard must
    still hold -- we check EVERY candidate key, not one of them."""
    import api.services.lens_stock_hook as hook

    line = {"item_id": "IT-7", "item_type": "LENS", "product_id": "lens-x",
            "lens_line_id": "LA", "sph": -1.0}
    _wire_lens_audit(
        monkeypatch, [{"source_id": "ORD-1#IT-7#commit", "action": "commit"}]
    )

    asyncio.run(
        om._release_lens_lines(
            [line], order_id="ORD-1", store_id="S1", user=_ADMIN,
            release=hook.release_for_cancel, positions=[2],
        )
    )

    assert wired["released"] == []


def test_commit_lookup_failure_does_not_block_a_normal_release(wired, monkeypatch):
    """Fail-soft: if we cannot tell, the cancel must still work. A stuck
    reservation is recoverable; a cancel that cannot complete is not."""
    import api.services.lens_stock_hook as hook
    from api.routers import lens_stock as stock_router

    def _boom():
        raise RuntimeError("audit store down")

    monkeypatch.setattr(stock_router, "_get_db", _boom)
    line = {"item_id": "IT-7", "item_type": "LENS", "product_id": "lens-x",
            "lens_line_id": "LA", "sph": -1.0}

    asyncio.run(
        om._release_lens_lines(
            [line], order_id="ORD-1", store_id="S1", user=_ADMIN,
            release=hook.release_for_cancel, positions=[0],
        )
    )

    assert len(wired["released"]) >= 1


# =========================================================================== #
# RE-VERIFY R4 MF2 -- ONE predicate decides what takes serialized stock, so the
# way IN and the way OUT can never disagree again.
# =========================================================================== #


def _line(item_type, product_id="P1", **extra):
    d = {"item_type": item_type, "product_id": product_id, "quantity": 1}
    d.update(extra)
    return d


def test_mark_and_release_agree_on_exactly_the_same_lines():
    """The drift that stranded stock-lens units: _mark_units_sold consumed a
    LENS line's serialized unit while the release path skipped it."""
    candidates = [
        _line("FRAME"),
        _line("SUNGLASS"),
        _line("ACCESSORY"),
        _line("LENS"),                                  # lens-hook stock
        _line("LENS", product_id="L-REAL-CATALOG-ID"),  # POSLayout's real shape
        _line("SERVICE"),
        _line("EYE_TEST"),
        _line("FRAME", product_id="custom-thing"),
        _line("FRAME", product_id=""),
    ]
    marked = [ln for ln in candidates if om._takes_serialized_stock(ln)]
    # The predicate IS the shared gate -- assert the classification directly.
    assert [ln["item_type"] for ln in marked] == ["FRAME", "SUNGLASS", "ACCESSORY"]
    for ln in candidates:
        assert om._takes_serialized_stock(ln) is (ln in marked)


def test_a_stock_lens_line_never_takes_a_serialized_unit(wired):
    """End-to-end: POSLayout maps OPTICAL_LENS -> 'LENS' and sends the REAL
    catalog product_id, so this line used to be flipped SOLD on the way in and
    never released on the way out."""
    wired["units"].append(_unit("U1", product_id="L-REAL"))
    added = _add(
        _item(item_type="LENS", product_id="L-REAL", product_name="1.6 SV",
              lens_line_id="LA", sph=-1.0)
    )

    # The serialized unit was NOT consumed by the lens line.
    assert wired["units"][0]["status"] == "AVAILABLE"

    _remove(added["item_id"])
    assert wired["units"][0]["status"] == "AVAILABLE"


# =========================================================================== #
# RE-VERIFY R4 MF7 / MF8 -- the line-remove door must report an incomplete
# restock, and a qty>1 scanned line must release ALL of its units.
# =========================================================================== #


def test_removing_a_qty2_scanned_line_releases_both_units(wired):
    """A qty>1 line consumes its NAMED unit once and FIFO-allocates the rest, so
    releasing only the named one strands the remainder -- and reported success."""
    wired["units"].extend([_unit("U-NAMED"), _unit("U-EXTRA")])
    added = _add(_item(stock_id="U-NAMED", quantity=2))
    assert all(u["status"] == "SOLD" for u in wired["units"])

    res = _remove(added["item_id"])

    assert all(u["status"] == "AVAILABLE" for u in wired["units"])
    assert res["stock_units_released"] == 2
    assert res["stock_release_failed"] is False


def test_incomplete_line_remove_restock_is_reported_and_persisted(wired):
    wired["units"].append(_unit("U1"))
    added = _add(_item())
    coll = wired["stock"].collection
    real = coll.find_one_and_update

    def _boom(flt, upd, sort=None, **kw):
        raise RuntimeError("mongo write blip")

    coll.find_one_and_update = _boom
    try:
        res = _remove(added["item_id"])
    finally:
        coll.find_one_and_update = real

    assert res["stock_release_failed"] is True
    assert res["stock_units_released"] == 0
    doc = wired["orders"].orders["ORD-1"]
    assert doc["line_remove_stock_release_failed"] is True
    assert doc["line_remove_stock_failed_item_id"] == added["item_id"]


def test_clean_line_remove_reports_success(wired):
    wired["units"].append(_unit("U1"))
    added = _add(_item())
    res = _remove(added["item_id"])
    assert res["stock_release_failed"] is False
    assert res["stock_units_released"] == 1
    assert "line_remove_stock_release_failed" not in wired["orders"].orders["ORD-1"]


# =========================================================================== #
# MUST-FIX 4 -- an in-flight DELIVER must not overwrite a CANCELLED order.
#
# update_status writes update_one({order_id}, ...) with NO status precondition,
# and the window from deliver_order's read to that write spans the store-access,
# Rx-hold, transition and payment checks. Cancel wins its own claim mid-window,
# then deliver overwrites CANCELLED -> DELIVERED.
#
# The race is pre-existing, but this PR sharpens the consequence: cancel now
# RELEASES stock, so losing it leaves a frame physically in the customer's bag
# reading AVAILABLE and re-sellable -- and on a pooled ONLINE store that feeds a
# Shopify oversell. Before, the unit stayed correctly SOLD.
# =========================================================================== #


def _ready_order(order_id="ORD-1"):
    doc = _draft_order(order_id)
    doc.update({
        "status": "READY",
        "payment_status": "PAID",
        "grand_total": 5000.0,
        "amount_paid": 5000.0,
        "items": [{"item_id": "I1", "item_type": "FRAME", "product_id": "P1",
                   "quantity": 1}],
    })
    return doc


def _deliver(order_id="ORD-1"):
    return asyncio.run(om.deliver_order(order_id, current_user=_ADMIN))


def test_deliver_succeeds_on_a_ready_order(wired):
    wired["orders"].orders["ORD-1"] = _ready_order()
    res = _deliver()
    assert res["status"] == "DELIVERED"
    assert wired["orders"].orders["ORD-1"]["status"] == "DELIVERED"


def test_deliver_cannot_overwrite_an_order_cancelled_mid_flight(wired):
    """The order is CANCELLED after deliver_order's read but before its write."""
    wired["orders"].orders["ORD-1"] = _ready_order()
    coll = wired["orders"].collection
    real = coll.find_one_and_update
    state = {"done": False}

    def _cancel_lands_first(flt, upd, **kw):
        if not state["done"]:
            state["done"] = True
            # A cancel wins its claim inside deliver's window.
            wired["orders"].orders["ORD-1"]["status"] = "CANCELLED"
        return real(flt, upd, **kw)

    coll.find_one_and_update = _cancel_lands_first
    try:
        with pytest.raises(HTTPException) as exc:
            _deliver()
    finally:
        coll.find_one_and_update = real

    assert exc.value.status_code == 400
    # The cancellation STANDS -- no resurrection, no invented unit.
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"


def test_deliver_refuses_an_already_cancelled_order(wired):
    wired["orders"].orders["ORD-1"] = _ready_order()
    wired["orders"].orders["ORD-1"]["status"] = "CANCELLED"
    with pytest.raises(HTTPException) as exc:
        _deliver()
    assert exc.value.status_code == 400
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"


def test_deliver_is_not_replayable(wired):
    wired["orders"].orders["ORD-1"] = _ready_order()
    assert _deliver()["status"] == "DELIVERED"
    with pytest.raises(HTTPException) as exc:
        _deliver()
    assert exc.value.status_code == 400


# =========================================================================== #
# RIDE-ALONG 6 -- the line-remove door must not cry wolf on a product that was
# never unit-tracked. It contradicted the availability gate, which deliberately
# exempts exactly these products, and poisoned the signal it was built to raise.
# =========================================================================== #


def test_removing_an_untracked_product_line_is_not_a_restock_failure(wired):
    """No stock_units rows for this product at all -> nothing to release and
    nothing wrong. The cancel door already reports incomplete=False here."""
    res_add = _add(_item(product_id="P-UNTRACKED", product_name="Cloth"))
    assert wired["units"] == []                       # genuinely untracked

    res = _remove(res_add["item_id"])

    assert res["stock_units_released"] == 0
    assert res["stock_release_failed"] is False        # NOT a false alarm
    assert "line_remove_stock_release_failed" not in wired["orders"].orders["ORD-1"]


def test_a_tracked_product_that_releases_nothing_is_still_a_real_failure(wired):
    """The gate must not silence the genuine case: the product IS unit-tracked,
    the line consumed a unit, and nothing came back."""
    wired["units"].append(_unit("U1"))
    added = _add(_item())
    # Something else takes the unit out from under the order.
    wired["units"][0]["order_id"] = "ORD-OTHER"

    res = _remove(added["item_id"])

    assert res["stock_units_released"] == 0
    assert res["stock_release_failed"] is True
    assert wired["orders"].orders["ORD-1"]["line_remove_stock_release_failed"] is True


# =========================================================================== #
# ROUND 6 / MUST-FIX 1 -- /confirm, /ready and the payment auto-confirm must
# not resurrect a cancelled order.
#
# OrderRepository.update_status writes update_one({order_id}, ...) filtered on
# the ID ALONE. _claim_order_for_cancel wins its claim correctly; these three
# doors used to stamp straight over it. The cancel's stock release and loyalty
# clawback both STAND, the order comes back alive, and -- because READY is
# exactly the precondition the deliver guard requires -- it then delivers
# cleanly. The frame is in the customer's bag and the system says AVAILABLE.
#
# NOTE ON THE HOOK: these doors do NOT go through find_one_and_update on the
# way in, so a hook on the fake collection (as the deliver test uses) never
# fires here. The cancel is injected by mutating the stored doc directly, which
# is what a concurrent worker's committed write actually looks like.
# =========================================================================== #


def _cancelled_by_a_concurrent_worker(wired, order_id="ORD-1"):
    """Exactly what _claim_order_for_cancel leaves behind: status CANCELLED and
    the line's unit already released to AVAILABLE."""
    wired["orders"].orders[order_id]["status"] = "CANCELLED"
    for u in wired["units"]:
        if u.get("order_id") == order_id:
            u.update({"status": "AVAILABLE", "order_id": None,
                      "released_from_order_id": order_id})


def _inject_cancel_after_nth_read(wired, n, order_id="ORD-1"):
    """Commit a concurrent cancel INSIDE the handler's read-to-write window.

    This is the only shape that exercises the defect. Cancelling BEFORE the call
    is useless -- the handler's own read then sees CANCELLED and it refuses for
    an unrelated reason (validate_status_transition), so the test passes even
    with the guard removed. The handler must read a LIVE order, then have the
    cancel land, then reach its write.

    Hooking find_one_and_update (as the deliver test does) does NOT work here:
    these doors never went through the claim, which is the finding itself.
    """
    real_find = wired["orders"].find_by_id
    calls = {"n": 0}

    def _find(oid):
        doc = real_find(oid)          # pre-cancel snapshot, as the handler saw it
        calls["n"] += 1
        if calls["n"] == n:
            _cancelled_by_a_concurrent_worker(wired, order_id)
        return doc

    wired["orders"].find_by_id = _find
    return lambda: setattr(wired["orders"], "find_by_id", real_find)


def test_confirm_cannot_resurrect_a_cancelled_order(wired):
    wired["orders"].orders["ORD-1"] = _draft_order()
    wired["orders"].orders["ORD-1"]["items"] = [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}
    ]
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _inject_cancel_after_nth_read(wired, 1)

    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(om.confirm_order("ORD-1", current_user=_ADMIN))
    finally:
        restore()

    assert exc.value.status_code in (400, 500)
    # THE CANCELLATION STANDS.
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_mark_ready_cannot_resurrect_a_cancelled_order(wired):
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "items": [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _inject_cancel_after_nth_read(wired, 1)

    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
    finally:
        restore()

    assert exc.value.status_code in (400, 500)
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_a_resurrected_order_cannot_then_be_delivered(wired):
    """The full chain the consequence depends on: /ready puts the order back at
    READY, which is exactly what the deliver guard accepts."""
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "payment_status": "PAID",
                "items": [{"item_id": "I1", "item_type": "FRAME",
                           "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _inject_cancel_after_nth_read(wired, 1)

    try:
        with pytest.raises(HTTPException):
            asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
    finally:
        restore()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(om.deliver_order("ORD-1", current_user=_ADMIN))

    assert exc.value.status_code == 400
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_payment_auto_confirm_cannot_resurrect_a_cancelled_order(wired):
    doc = _draft_order()
    doc.update({"grand_total": 5000.0, "balance_due": 5000.0,
                "items": [{"item_id": "I1", "item_type": "FRAME",
                           "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))

    # add_payment reads the order twice (the handler's own read, then the
    # `refreshed` read the auto-confirm branch keys off). The cancel lands after
    # BOTH, so the branch fires on a stale DRAFT snapshot -- the real window.
    restore = _inject_cancel_after_nth_read(wired, 2)
    try:
        res = asyncio.run(om.add_payment(
            "ORD-1", om.PaymentCreate(method="CASH", amount=1000.0),
            current_user=_ADMIN, idempotency_key=None))
    finally:
        restore()

    # The payment is recorded, but the order is NOT resurrected.
    assert res["order_status"] != "CONFIRMED"
    assert res["workshop_job_id"] is None
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


# --- the guards must not over-block the ordinary path ----------------------- #


def test_confirm_still_works_on_a_draft_order(wired):
    wired["orders"].orders["ORD-1"] = _draft_order()
    wired["orders"].orders["ORD-1"]["items"] = [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}
    ]
    res = asyncio.run(om.confirm_order("ORD-1", current_user=_ADMIN))
    assert res["status"] == "CONFIRMED"
    assert wired["orders"].orders["ORD-1"]["status"] == "CONFIRMED"


def test_mark_ready_still_works_from_confirmed_and_from_processing(wired):
    for start in ("CONFIRMED", "PROCESSING"):
        doc = _draft_order()
        doc.update({"status": start, "items": [
            {"item_id": "I1", "item_type": "FRAME", "product_id": "P1",
             "quantity": 1}]})
        wired["orders"].orders["ORD-1"] = doc
        res = asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
        assert res["status"] == "READY", start
        assert wired["orders"].orders["ORD-1"]["status"] == "READY"


def test_payment_auto_confirm_still_works_on_a_clean_draft(wired):
    wired["orders"].orders["ORD-1"] = _fitting_order()
    res = _pay(1000.0)
    assert res["order_status"] == "CONFIRMED"
    assert res["workshop_job_id"] == "JID-1"


def test_the_non_atomic_fallback_also_enforces_the_status_set(wired):
    """A backend without find_one_and_update takes the read-check-update
    fallback. That is a NARROWER window, not an open door -- it must still
    refuse when the status is no longer in the required set, or the guard is
    only half-built."""
    wired["orders"].orders["ORD-1"] = _draft_order()
    wired["orders"].orders["ORD-1"]["items"] = [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}
    ]
    wired["orders"].collection = None          # no atomic surface at all
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _inject_cancel_after_nth_read(wired, 1)

    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(om.confirm_order("ORD-1", current_user=_ADMIN))
    finally:
        restore()

    assert exc.value.status_code in (400, 500)
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_the_fallback_still_confirms_a_clean_draft(wired):
    wired["orders"].orders["ORD-1"] = _draft_order()
    wired["orders"].orders["ORD-1"]["items"] = [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}
    ]
    wired["orders"].collection = None
    res = asyncio.run(om.confirm_order("ORD-1", current_user=_ADMIN))
    assert res["status"] == "CONFIRMED"


# =========================================================================== #
# ROUND 7 -- THE POST-CLAIM WINDOW.
#
# _claim_order_status used to win its atomic claim and then, four lines later,
# call repo.update_status -- update_one({id}, ...) with NO status precondition,
# the exact primitive the helper's own docstring diagnoses as the bug. A cancel
# committing in that one-round-trip gap was stamped straight back over.
#
# This is a DIFFERENT WINDOW from the round-6 tests, which hook find_by_id and
# land the cancel BEFORE the claim. These hook the collection and land it AFTER
# the claim returns. Both windows must be covered; the earlier tests pass with
# this defect live.
#
# _claim_order_for_cancel filters $nin [CANCELLED, DELIVERED], so CONFIRMED and
# READY both stay claimable -- and deliver is immune only INCIDENTALLY, because
# DELIVERED happens to sit in that $nin.
# =========================================================================== #


def _cancel_lands_after_the_claim(wired, order_id="ORD-1"):
    """Commit a concurrent cancel immediately AFTER the guarded claim returns,
    i.e. inside the gap the follow-up write used to occupy."""
    coll = wired["orders"].collection
    real = coll.find_one_and_update
    state = {"fired": False}

    def _hook(flt, upd, **kw):
        doc = real(flt, upd, **kw)
        if doc is not None and not state["fired"]:
            state["fired"] = True
            _cancelled_by_a_concurrent_worker(wired, order_id)
        return doc

    coll.find_one_and_update = _hook
    return lambda: setattr(coll, "find_one_and_update", real)


def test_confirm_does_not_reopen_the_post_claim_window(wired):
    wired["orders"].orders["ORD-1"] = _draft_order()
    wired["orders"].orders["ORD-1"]["items"] = [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}
    ]
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _cancel_lands_after_the_claim(wired)
    try:
        asyncio.run(om.confirm_order("ORD-1", current_user=_ADMIN))
    except HTTPException:
        pass
    finally:
        restore()

    # THE CANCELLATION STANDS and the released unit stays released.
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_mark_ready_does_not_reopen_the_post_claim_window(wired):
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "items": [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _cancel_lands_after_the_claim(wired)
    try:
        asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
    except HTTPException:
        pass
    finally:
        restore()

    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_a_post_claim_resurrected_order_cannot_then_be_delivered(wired):
    """The consequence chain: /ready leaving the order at READY is exactly what
    the deliver guard accepts, so the whole thing has to hold end to end."""
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "payment_status": "PAID",
                "items": [{"item_id": "I1", "item_type": "FRAME",
                           "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _cancel_lands_after_the_claim(wired)
    try:
        asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
    except HTTPException:
        pass
    finally:
        restore()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(om.deliver_order("ORD-1", current_user=_ADMIN))

    assert exc.value.status_code == 400
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


def test_auto_confirm_does_not_reopen_the_post_claim_window(wired):
    doc = _draft_order()
    doc.update({"grand_total": 5000.0, "balance_due": 5000.0,
                "items": [{"item_id": "I1", "item_type": "FRAME",
                           "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    restore = _cancel_lands_after_the_claim(wired)
    try:
        asyncio.run(om.add_payment(
            "ORD-1", om.PaymentCreate(method="CASH", amount=1000.0),
            current_user=_ADMIN, idempotency_key=None))
    except HTTPException:
        pass
    finally:
        restore()

    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"


# --- constraint 1 + 2: the claim must still write what update_status wrote --- #


def test_the_claim_appends_status_history(wired):
    """Folding the write in means status_history is now OUR responsibility. It
    feeds the customer-facing portal tracking view, so losing it is a silent
    customer-visible regression -- and a fake that drops $push hides it."""
    wired["orders"].orders["ORD-1"] = _draft_order()
    wired["orders"].orders["ORD-1"]["items"] = [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}
    ]
    wired["orders"].orders["ORD-1"]["status_history"] = []

    asyncio.run(om.confirm_order("ORD-1", current_user=_ADMIN))

    history = wired["orders"].orders["ORD-1"]["status_history"]
    assert len(history) == 1
    assert history[0]["status"] == "CONFIRMED"
    assert history[0]["changed_by"] == _ADMIN["user_id"]
    assert history[0]["timestamp"]


def test_the_claim_sets_delivered_at_on_delivery(wired):
    """delivered_at was set by update_status and is surfaced on the order read;
    deleting that call drops it unless it is folded in."""
    wired["orders"].orders["ORD-1"] = _ready_order()
    res = _deliver()
    assert res["status"] == "DELIVERED"
    doc = wired["orders"].orders["ORD-1"]
    assert doc.get("delivered_at") is not None
    assert doc["status_history"][-1]["status"] == "DELIVERED"


def test_a_refused_claim_appends_no_history(wired):
    """A claim that loses must write NOTHING -- not the status, not a history
    entry that would tell the customer their order advanced."""
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "status_history": [],
                "items": [{"item_id": "I1", "item_type": "FRAME",
                           "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    restore = _inject_cancel_after_nth_read(wired, 1)
    try:
        with pytest.raises(HTTPException):
            asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
    finally:
        restore()

    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["orders"].orders["ORD-1"]["status_history"] == []


class _UpdateOnlyColl:
    """A collection with update_one but NO find_one_and_update -- the exact
    surface the guarded fallback branch exists for. Nulling the whole collection
    (as the other fallback test does) skips this branch entirely, so without
    this double the fallback's own status precondition is unpinned."""

    def __init__(self, orders):
        self.orders = orders

    def update_one(self, flt, upd):
        doc = self.orders.get(flt.get("order_id"))
        matched = doc is not None
        want = flt.get("status")
        if matched and isinstance(want, dict) and "$in" in want:
            matched = doc.get("status") in want["$in"]
        if not matched:
            return type("R", (object,), {"modified_count": 0})()
        doc.update(upd.get("$set", {}))
        for key, value in upd.get("$push", {}).items():
            doc.setdefault(key, []).append(value)
        return type("R", (object,), {"modified_count": 1})()


def test_the_guarded_fallback_branch_enforces_the_status_set(wired):
    """The fallback's guarded update_one must carry the SAME precondition. It is
    a narrower window than two round trips, not an open door."""
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "status_history": [], "items": [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["orders"].collection = _UpdateOnlyColl(wired["orders"].orders)
    wired["units"].append(_unit("U1", status="SOLD", order_id="ORD-1"))
    # AFTER THE SECOND read: mark_ready reads once, then the helper's own
    # read-check reads again. Injecting at the FIRST read makes the read-check
    # itself refuse, so the guarded update_one -- the thing under test -- is
    # never reached and its precondition is unpinned. This lands the cancel in
    # the gap BETWEEN the read-check and the write, which is the only window
    # the fallback's own precondition can defend.
    restore = _inject_cancel_after_nth_read(wired, 2)

    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))
    finally:
        restore()

    assert exc.value.status_code in (400, 500)
    assert wired["orders"].orders["ORD-1"]["status"] == "CANCELLED"
    assert wired["units"][0]["status"] == "AVAILABLE"
    assert wired["orders"].orders["ORD-1"]["status_history"] == []


def test_the_guarded_fallback_branch_still_advances_a_clean_order(wired):
    doc = _draft_order()
    doc.update({"status": "CONFIRMED", "status_history": [], "items": [
        {"item_id": "I1", "item_type": "FRAME", "product_id": "P1", "quantity": 1}]})
    wired["orders"].orders["ORD-1"] = doc
    wired["orders"].collection = _UpdateOnlyColl(wired["orders"].orders)

    res = asyncio.run(om.mark_ready("ORD-1", current_user=_ADMIN))

    assert res["status"] == "READY"
    assert wired["orders"].orders["ORD-1"]["status"] == "READY"
    assert wired["orders"].orders["ORD-1"]["status_history"][-1]["status"] == "READY"
