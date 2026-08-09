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
        return isinstance(val, str) if arg == "string" else False
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


class _FakeOrderRepo:
    def __init__(self, orders):
        self.orders = {o["order_id"]: o for o in orders}
        self.update_ok = True
        self.status_updates = []

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


def test_removing_a_lens_line_releases_its_own_reservation_index(wired):
    a = _add(_item(item_type="LENS", product_id="lens-a", lens_line_id="LA", sph=-1.0))
    _add(_item(item_type="LENS", product_id="lens-b", lens_line_id="LB", sph=-2.0))
    wired["released"].clear()

    _remove(a["item_id"])

    assert len(wired["released"]) == 1
    assert wired["released"][0]["line_index"] == 0
    assert wired["released"][0]["order_item"]["lens_line_id"] == "LA"
    # The SURVIVING line keeps its own stable index even though it shifted
    # position -- otherwise a later cancel would release the wrong cell.
    survivor = wired["orders"].orders["ORD-1"]["items"][0]
    assert survivor["lens_line_id"] == "LB" and survivor["line_index"] == 1


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

    with pytest.raises(HTTPException) as exc:
        _add(_item(stock_id="U1"))

    detail = str(exc.value.detail)
    assert exc.value.status_code == 409
    assert "EXPIRED" in detail.upper() and "2001-01-01" in detail
    assert wired["units"][0]["status"] == "AVAILABLE"


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
