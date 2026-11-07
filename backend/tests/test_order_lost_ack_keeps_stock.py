"""
IMS 2.0 - L2-LENS-F01: a bill whose save landed keeps its lens reservation.
==========================================================================
Codex audit: the order insert landed but its acknowledgement was lost (the
call raised). The serialized-unit rollback and the approval give-back ask
"was this order saved?" first -- the lens rollback did not: it released the
cell, so a live DRAFT bill held a lens another till could sell, and the
same-key retry replayed that bill with nothing reserved.

Driven through the REAL POST /orders and POST /orders/{id}/items doors over
mongomock, with the REAL lens hook + lens_stock router (one cell, on_hand 2)
and a REAL serialized frame unit, asserted together:
  * failure BEFORE the insert -> unit back on the shelf, lens released ONCE;
  * failure AFTER the insert  -> unit stays SOLD to the bill, lens stays
    reserved; the same-key retry returns that one bill, still one reservation;
  * save outcome UNREADABLE   -> neither the unit nor the lens is freed
    (the RAW collection read fails -- the repo wrappers swallow errors);
  * the add-line door: line saved, ack lost -> its lens stays reserved.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from test_till_records_scanned_unit import (  # noqa: E402
    STORE,
    _line,
    _order_id,
    _token,
    _unit,
    till,  # noqa: F401  (pytest fixture)
)

pytestmark = pytest.mark.usefixtures("gst_numbers")

CELL = {"line_stock_id": "LS-1", "lens_line_id": "LL-1", "store_id": STORE,
        "sph": -2.0, "cyl": 0.0, "add": None, "on_hand": 2, "reserved": 0}


@pytest.fixture
def shop(till, monkeypatch):
    from api.routers import lens_stock

    db = till["db"]
    db.lens_stock_lines.insert_one(dict(CELL))
    monkeypatch.setattr(lens_stock, "_get_db", lambda: db)
    return till


def _lens_line():
    return {"product_id": "lens-cl-1", "product_name": "Monthly CL",
            "item_type": "CONTACT_LENS", "category": "CONTACT_LENS",
            "quantity": 1, "unit_price": 2000.0,
            "lens_line_id": "LL-1", "sph": -2.0, "cyl": 0.0}


def _sell(client, items, key=None):
    headers = _token(["SUPERADMIN"])
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/api/v1/orders", json={"customer_id": "cust-till", "items": items},
                       headers=headers)


def _cell(shop):
    return shop["db"].lens_stock_lines.find_one({"line_stock_id": "LS-1"})


def _lens_acts(shop, action):
    return list(shop["db"].lens_stock_audit.find({"action": action}))


def _orders(shop):
    return list(shop["db"].orders.find({}))


def _books(shop):
    """Independent reconciliation: pieces by status + the cell's reserved
    count must equal reserves minus releases in the lens audit."""
    units = {u["stock_id"]: u["status"] for u in shop["db"].stock_units.find({})}
    held = len(_lens_acts(shop, "reserve")) - len(_lens_acts(shop, "release"))
    assert _cell(shop)["reserved"] == held
    assert _cell(shop)["on_hand"] == 2
    return units, held


def _saved_then_raised(shop, monkeypatch):
    real = shop["orders"].create_unique

    def landed_then_raised(doc, **kw):
        real(doc, **kw)
        raise RuntimeError("simulated lost insert acknowledgement")

    monkeypatch.setattr(shop["orders"], "create_unique", landed_then_raised)
    return real


def test_failure_before_the_insert_frees_unit_and_lens_once(client, shop, monkeypatch):
    def never_written(*_a, **_k):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(shop["orders"], "create_unique", never_written)
    r = _sell(client, [_line(stock_id="SU-B"), _lens_line()])
    assert r.status_code == 500, r.text
    assert _orders(shop) == []
    units, held = _books(shop)
    assert units["SU-B"] == "AVAILABLE" and held == 0
    assert len(_lens_acts(shop, "reserve")) == 1 and len(_lens_acts(shop, "release")) == 1


def test_insert_landed_but_ack_lost_keeps_unit_and_lens_with_the_bill(client, shop, monkeypatch):
    real_create = _saved_then_raised(shop, monkeypatch)
    r = _sell(client, [_line(stock_id="SU-B"), _lens_line()], key="bill-key-1")
    assert r.status_code == 500, r.text
    [saved] = _orders(shop)
    units, held = _books(shop)
    assert held == 1, "the saved bill's lens was released"
    assert units["SU-B"] == "SOLD" and _unit(shop, "SU-B")["order_id"] == saved["order_id"]

    # The till's normal same-key retry: that one bill, still one reservation.
    monkeypatch.setattr(shop["orders"], "create_unique", real_create)
    again = _sell(client, [_line(stock_id="SU-B"), _lens_line()], key="bill-key-1")
    assert again.status_code in (200, 201), again.text
    assert _order_id(again) == saved["order_id"]
    assert len(_orders(shop)) == 1
    units, held = _books(shop)
    assert held == 1 and units["SU-B"] == "SOLD"


def _db_goes_dark_on_read(shop, monkeypatch, broken):
    """Break the RAW orders collection read (what a real outage breaks):
    BaseRepository.find_one / find_by_id swallow errors into None, so the
    saved-check must read the collection itself to see the failure."""
    coll = shop["orders"].collection
    real_find_one = coll.find_one

    def find_one(*a, **k):
        if broken:
            raise RuntimeError("ServerSelectionTimeoutError: cannot read orders")
        return real_find_one(*a, **k)

    monkeypatch.setattr(coll, "find_one", find_one)


def test_unreadable_save_outcome_frees_neither_unit_nor_lens(client, shop, monkeypatch):
    broken = {}

    def write_fails_and_db_goes_dark(*_a, **_k):
        broken["on"] = True
        raise RuntimeError("connection reset")

    monkeypatch.setattr(shop["orders"], "create_unique", write_fails_and_db_goes_dark)
    _db_goes_dark_on_read(shop, monkeypatch, broken)
    r = _sell(client, [_line(stock_id="SU-B"), _lens_line()])
    assert r.status_code == 500, r.text
    broken.clear()
    units, held = _books(shop)
    assert held == 1 and units["SU-B"] == "SOLD"


def test_added_lens_line_unreadable_outcome_keeps_its_reservation(client, shop, monkeypatch):
    first = _sell(client, [_line()])
    assert first.status_code in (200, 201), first.text
    order_id = _order_id(first)
    real_update = shop["orders"].update
    broken = {}

    def landed_then_db_goes_dark(oid, data, *a, **k):
        out = real_update(oid, data, *a, **k)
        if "items" in data:
            broken["on"] = True
            raise RuntimeError("simulated lost update acknowledgement")
        return out

    monkeypatch.setattr(shop["orders"], "update", landed_then_db_goes_dark)
    _db_goes_dark_on_read(shop, monkeypatch, broken)
    added = client.post(f"/api/v1/orders/{order_id}/items", json=_lens_line(),
                        headers=_token(["SUPERADMIN"]))
    assert added.status_code == 500, added.text
    broken.clear()
    assert len(shop["db"].orders.find_one({"order_id": order_id})["items"]) == 2
    _units, held = _books(shop)
    assert held == 1, "lens freed under a line whose save could not be read"


def test_added_lens_line_saved_but_ack_lost_keeps_its_reservation(client, shop, monkeypatch):
    first = _sell(client, [_line()])
    assert first.status_code in (200, 201), first.text
    order_id = _order_id(first)
    real_update = shop["orders"].update

    def landed_then_raised(oid, data, *a, **k):
        out = real_update(oid, data, *a, **k)
        if "items" in data:
            raise RuntimeError("simulated lost update acknowledgement")
        return out

    monkeypatch.setattr(shop["orders"], "update", landed_then_raised)
    added = client.post(f"/api/v1/orders/{order_id}/items", json=_lens_line(),
                        headers=_token(["SUPERADMIN"]))
    assert added.status_code == 500, added.text
    saved = shop["db"].orders.find_one({"order_id": order_id})
    assert len(saved["items"]) == 2
    _units, held = _books(shop)
    assert held == 1, "the saved line's lens was released"


def test_a_normal_bill_reserves_its_lens_once(client, shop):
    r = _sell(client, [_line(stock_id="SU-B"), _lens_line()], key="bill-key-ok")
    assert r.status_code in (200, 201), r.text
    units, held = _books(shop)
    assert held == 1 and units["SU-B"] == "SOLD"
