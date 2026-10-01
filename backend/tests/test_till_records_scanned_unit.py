"""The till records the EXACT scanned unit, and its scan replies carry no cost.

Owner ruling 2026-09-30 00:10 (POS asks, both answered YES):
  (1) the till records the exact scanned unit on each sale line (stock_id /
      barcode per line) so returns, traces and stock searches point at the
      right frame -- billing, prices and GST unchanged; a line added WITHOUT a
      scan keeps today's first-available behaviour; a scanned unit that is no
      longer AVAILABLE is refused clearly.
  (2) the till's unit-scan replies (GET /inventory/barcode/{code} and
      GET /inventory/stock/barcode/{code}) stop carrying cost for the counter
      roles; what the till shows stays exactly the same.

Finding ids (the frontend half, TSU-1..3, is pinned in
frontend/src/components/pos/__tests__/tillRecordsScannedUnit.test.ts), each
fixed and pinned by a plain test below:
  TSU-4  the barcode trace of the frame handed over showed no sale (it matched
         only items.barcode, which no order line stores); it now also matches
         the line that names the unit's stock_id.
  TSU-5  the same unit scanned onto two lines of one bill sold the FIRST
         AVAILABLE unit on the second line; it is now refused (409).
  TSU-6  once scanned lines name their unit, a typed line of the same product
         could oversell past it (the gate counted the scanned unit as free for
         the typed line, and the extra quantity on a scanned line went
         unchecked); the gate now holds the scanned unit for its own line.
  TSU-7  a return reactivated the FIRST sold unit of the product on the order,
         not the unit the returned line names: the frame still with the
         customer read AVAILABLE and the one back on the shelf stayed SOLD, so
         the till refused it. The serial check picked the same wrong unit.
  TSC-1  GET /inventory/barcode/{code} handed counter roles the unit's cost and
         the joined product's cost / landed cost; now through cost_mask.
  TSC-2  GET /inventory/stock/barcode/{code} did the same.

The server half of (1) that already worked (an explicit stock_id is honoured,
refused when not AVAILABLE, and the unit label then finds its order) is pinned
by plain tests too.

Engine: mongomock with the REAL StockRepository / OrderRepository, so the
atomic claim, the guarded mark_sold and the trace's orders query are the real
filters. No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

mongomock = pytest.importorskip("mongomock")

STORE = "BV-TEST-01"  # the conftest admin token's active store
PID = "FR-SCAN-1"
UNIT_A = {"stock_id": "SU-A", "barcode": "BVA00000001"}  # first on the shelf
UNIT_B = {"stock_id": "SU-B", "barcode": "BVB00000002"}  # the one scanned
COUNTER_ROLES = ["SALES_STAFF", "SALES_CASHIER", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"]
COST_KEYS = {"cost_price", "unit_cost", "landed_cost"}


def _token(roles):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": "till-user-" + roles[0].lower(),
                "username": "till_" + roles[0].lower(),
                "roles": roles,
                "store_ids": [STORE],
                "active_store_id": STORE,
                "discount_cap": 10.0,
            }
        )
    }


@pytest.fixture
def till(monkeypatch):
    from api import dependencies as dm
    from api.routers import inventory as inv_mod
    from api.routers import orders as om
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    db = mongomock.MongoClient()["till_scanned_unit"]
    order_repo = OrderRepository(db.get_collection("orders"))
    cust_repo = CustomerRepository(db.get_collection("customers"))
    prod_repo = ProductRepository(db.get_collection("products"))
    stock_repo = StockRepository(db.get_collection("stock_units"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    from api.routers import returns as ret_mod

    for mod in (om, inv_mod):
        monkeypatch.setattr(mod, "get_stock_repository", lambda: stock_repo)
        monkeypatch.setattr(mod, "get_product_repository", lambda: prod_repo)
    monkeypatch.setattr(ret_mod, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(om, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(om, "get_customer_repository", lambda: cust_repo)
    monkeypatch.setattr(om, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(dm, "get_audit_repository", lambda: audit_repo)
    # The barcode trace reads the raw collections (stock_units, orders).
    monkeypatch.setattr(inv_mod, "_get_db", lambda: db)

    cust_repo.create(
        {"customer_id": "cust-till", "name": "Asha", "mobile": "9100000077", "phone": "9100000077"}
    )
    prod_repo.create(
        {
            "product_id": PID,
            "name": "Carrera CA 8895 Havana 54",
            "sku": "FR-CARRERA-CA8895-807-54",
            "brand": "Carrera",
            "category": "FRAME",
            "hsn_code": "900311",
            "mrp": 10000.0,
            "offer_price": 9000.0,
            "cost_price": 4000.0,
            "landed_cost": 4100.0,
            "pricing": {"mrp": 10000.0, "cost_price": 4000.0},
            "is_active": True,
        }
    )
    for unit in (UNIT_A, UNIT_B):
        stock_repo.create(
            {
                **unit,
                "product_id": PID,
                "store_id": STORE,
                "status": "AVAILABLE",
                "quantity": 1,
                "unit_cost": 4000.0,
                "cost_price": 4000.0,
                "cost_source": "GRN_PO",
            }
        )
    return {"db": db, "stock": stock_repo}


def _line(**over):
    line = {
        "item_type": "FRAME",
        "product_id": PID,
        "product_name": "Carrera CA 8895 Havana 54",
        "sku": "FR-CARRERA-CA8895-807-54",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": 9000.0,
    }
    line.update(over)
    return line


def _sell(client, items):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-till", "items": items},
        headers=_token(["SUPERADMIN"]),
    )


def _unit(till, stock_id):
    return till["db"].get_collection("stock_units").find_one({"stock_id": stock_id}, {"_id": 0})


def _order_id(resp):
    body = resp.json()
    return body.get("order_id") or (body.get("order") or {}).get("order_id")


# ---------------------------------------------------------------------------
# (1) The server contract the till relies on -- works today (plain tests)
# ---------------------------------------------------------------------------


def test_scanned_line_sells_that_unit_and_its_label_finds_the_order(client, till):
    r = _sell(client, [_line(stock_id=UNIT_B["stock_id"])])
    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    assert order_id

    b, a = _unit(till, "SU-B"), _unit(till, "SU-A")
    assert b["status"] == "SOLD" and b["order_id"] == order_id, b
    # The first unit on the shelf was NOT touched: it is still there.
    assert a["status"] == "AVAILABLE" and not a.get("order_id"), a

    # Returns lookup by unit label (#1166 reads unit.order_id off this door).
    got = client.get(
        f"/api/v1/inventory/stock/barcode/{UNIT_B['barcode']}", headers=_token(["SUPERADMIN"])
    )
    assert got.status_code == 200, got.text
    assert got.json().get("order_id") == order_id


def test_typed_line_keeps_first_available(client, till):
    r = _sell(client, [_line()])
    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    assert _unit(till, "SU-A")["status"] == "SOLD"
    assert _unit(till, "SU-A")["order_id"] == order_id
    assert _unit(till, "SU-B")["status"] == "AVAILABLE"


def test_scanned_unit_no_longer_available_is_refused_not_swapped(client, till):
    till["db"].get_collection("stock_units").update_one(
        {"stock_id": "SU-B"}, {"$set": {"status": "SOLD", "order_id": "ORD-EARLIER"}}
    )
    r = _sell(client, [_line(stock_id=UNIT_B["stock_id"])])
    assert r.status_code == 409, r.text
    detail = r.text.lower()
    assert "su-b" in detail and "not available" in detail, r.text
    # Refused, never served from the shelf instead; the earlier sale is intact.
    assert _unit(till, "SU-A")["status"] == "AVAILABLE"
    assert _unit(till, "SU-B")["order_id"] == "ORD-EARLIER"


# ---------------------------------------------------------------------------
# (1) Findings
# ---------------------------------------------------------------------------


def test_barcode_trace_of_the_scanned_frame_shows_its_sale(client, till):
    r = _sell(client, [_line(stock_id=UNIT_B["stock_id"], barcode=UNIT_B["barcode"])])
    assert r.status_code in (200, 201), r.text
    order_number = r.json().get("order_number")
    assert order_number

    admin = _token(["SUPERADMIN"])
    sold = client.get(f"/api/v1/inventory/barcode/{UNIT_B['barcode']}/trace", headers=admin)
    assert sold.status_code == 200, sold.text
    assert [s.get("order_number") for s in sold.json()["sales"]] == [order_number]

    shelf = client.get(f"/api/v1/inventory/barcode/{UNIT_A['barcode']}/trace", headers=admin)
    assert shelf.json()["sales"] == []


def test_same_unit_scanned_twice_on_one_bill_is_refused(client, till):
    scanned = _line(stock_id=UNIT_B["stock_id"])
    r = _sell(client, [scanned, dict(scanned)])
    assert r.status_code == 409, r.text
    assert "su-b" in r.text.lower(), r.text
    # Nothing left the shelf.
    assert _unit(till, "SU-A")["status"] == "AVAILABLE"
    assert _unit(till, "SU-B")["status"] == "AVAILABLE"


def test_typed_line_before_a_scanned_line_sells_both_units(client, till):
    """FIFO would pick SU-A for the typed line and SU-A is scanned onto the
    NEXT line: both units still leave, to this order."""
    r = _sell(client, [_line(), _line(stock_id=UNIT_A["stock_id"])])
    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    assert _unit(till, "SU-A")["order_id"] == order_id
    assert _unit(till, "SU-B")["status"] == "SOLD" and _unit(till, "SU-B")["order_id"] == order_id


def test_typed_line_cannot_oversell_past_the_scanned_unit(client, till):
    """TSU-6: only SU-A is left and it is scanned; a typed line of the same
    product has nothing to take -- refused (oversell blocks), nothing leaves."""
    till["db"].get_collection("stock_units").update_one(
        {"stock_id": "SU-B"}, {"$set": {"status": "SOLD", "order_id": "ORD-EARLIER"}}
    )
    r = _sell(client, [_line(), _line(stock_id=UNIT_A["stock_id"])])
    assert r.status_code == 409, r.text
    assert "insufficient stock" in r.text.lower(), r.text
    assert _unit(till, "SU-A")["status"] == "AVAILABLE"


def test_extra_quantity_on_a_scanned_line_is_oversell_checked(client, till):
    """The scanned unit plus one more first-available: with SU-B gone there is
    no second unit, so the bill is refused rather than selling one."""
    till["db"].get_collection("stock_units").update_one(
        {"stock_id": "SU-B"}, {"$set": {"status": "SOLD", "order_id": "ORD-EARLIER"}}
    )
    r = _sell(client, [_line(stock_id=UNIT_A["stock_id"], quantity=2)])
    assert r.status_code == 409, r.text
    assert _unit(till, "SU-A")["status"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# (2) The till's unit-scan replies carry no cost for counter roles
# ---------------------------------------------------------------------------


def _cost_keys_in(obj, path=""):
    """Every (path, key) in a JSON body whose key is a cost field, at any depth."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in COST_KEYS:
                found.append(f"{path}.{k}".lstrip("."))
            found.extend(_cost_keys_in(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_cost_keys_in(v, f"{path}[{i}]"))
    return found


def _without_cost(obj):
    if isinstance(obj, dict):
        return {k: _without_cost(v) for k, v in obj.items() if k not in COST_KEYS}
    if isinstance(obj, list):
        return [_without_cost(v) for v in obj]
    return obj


_SCAN_DOORS = [
    pytest.param(
        f"/api/v1/inventory/barcode/{UNIT_B['barcode']}?store_id={STORE}", "TSC-1", id="barcode"
    ),
    pytest.param(f"/api/v1/inventory/stock/barcode/{UNIT_B['barcode']}", "TSC-2", id="stock-barcode"),
]


@pytest.mark.parametrize("url,finding", _SCAN_DOORS)
def test_admin_scan_reply_still_carries_cost(client, till, url, finding):
    """Guard for the fix: only counter roles lose cost (the owner's ask)."""
    r = client.get(url, headers=_token(["ADMIN"]))
    assert r.status_code == 200, r.text
    assert "unit_cost" in r.json() and "cost_price" in r.json(), finding


@pytest.mark.parametrize("role", COUNTER_ROLES)
@pytest.mark.parametrize("url,finding", _SCAN_DOORS)
def test_counter_scan_reply_carries_no_cost_and_is_otherwise_unchanged(
    client, till, url, finding, role
):
    admin = client.get(url, headers=_token(["ADMIN"]))
    counter = client.get(url, headers=_token([role]))
    assert counter.status_code == 200, counter.text
    body = counter.json()
    assert _cost_keys_in(body) == [], _cost_keys_in(body)
    # What the till shows stays exactly the same: the counter reply IS the
    # admin reply minus the cost fields (so the fix cannot strip mrp, offer
    # price, hsn, category, stock_id, barcode or cross_store along the way).
    assert body == _without_cost(admin.json())


# ---------------------------------------------------------------------------
# (1) A return puts back the unit that came back (TSU-7)
# ---------------------------------------------------------------------------
# The restock used to reactivate the FIRST sold unit of the product on the
# order. With scanned lines that is the wrong frame: the one still with the
# customer reads AVAILABLE, the one back on the shelf stays SOLD, and the till
# then refuses it. The line's own stock_id comes back; a line with no unit
# takes a unit no other line names.


def _order(till, order_id):
    return till["db"].get_collection("orders").find_one({"order_id": order_id}, {"_id": 0})


def _item_of(order, stock_id=None):
    return next(i for i in order["items"] if i.get("stock_id") == stock_id)


def _return_line(item, **over):
    from api.routers.returns import ReturnLine

    data = {
        "order_item_id": item["item_id"],
        "product_id": PID,
        "return_qty": 1,
        "unit_price": 9000.0,
    }
    data.update(over)
    return ReturnLine(**data)


def test_return_of_a_scanned_line_restocks_that_unit_and_the_till_sells_it(client, till):
    from api.routers import returns as ret_mod

    r = _sell(client, [_line(stock_id="SU-A"), _line(stock_id="SU-B")])
    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    order = _order(till, order_id)

    res = ret_mod._restock_good_items(
        [_return_line(_item_of(order, "SU-B"))], STORE, "RET-TSU7", order_id=order_id, order=order
    )
    assert res["restock_stock_ids"] == ["SU-B"], res
    assert _unit(till, "SU-B")["status"] == "AVAILABLE"
    a = _unit(till, "SU-A")
    assert a["status"] == "SOLD" and a["order_id"] == order_id, a  # still with the customer

    scan = client.get(
        f"/api/v1/inventory/barcode/{UNIT_B['barcode']}?store_id={STORE}",
        headers=_token(["SALES_STAFF"]),
    )
    assert scan.status_code == 200 and scan.json()["status"] == "AVAILABLE", scan.text
    again = _sell(client, [_line(stock_id="SU-B")])
    assert again.status_code in (200, 201), again.text


def test_return_of_a_typed_line_never_takes_the_scanned_lines_unit(client, till):
    """SU-A is scanned onto line 1; the typed line 2 took SU-B. Returning the
    typed line puts SU-B back, not the first SOLD unit on the order (SU-A)."""
    from api.routers import returns as ret_mod

    r = _sell(client, [_line(stock_id="SU-A"), _line()])
    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    order = _order(till, order_id)
    assert _unit(till, "SU-B")["order_id"] == order_id

    res = ret_mod._restock_good_items(
        [_return_line(_item_of(order, None))], STORE, "RET-TSU7B", order_id=order_id, order=order
    )
    assert res["restock_stock_ids"] == ["SU-B"], res
    assert _unit(till, "SU-A")["status"] == "SOLD"


def test_return_serial_check_reads_the_scanned_lines_own_unit(client, till):
    """The serial guard compares the scanned serial with THIS line's unit, so
    the right frame coming back is not 409'd as a mismatch."""
    from types import SimpleNamespace

    from api.routers import returns as ret_mod

    units = till["db"].get_collection("stock_units")
    units.update_one({"stock_id": "SU-A"}, {"$set": {"serial": "SER-A"}})
    units.update_one({"stock_id": "SU-B"}, {"$set": {"serial": "SER-B"}})
    r = _sell(client, [_line(stock_id="SU-A"), _line(stock_id="SU-B")])
    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    order = _order(till, order_id)
    b_line = _item_of(order, "SU-B")

    no_override = SimpleNamespace(
        serial_mismatch_override_token=None, serial_mismatch_override_request_id=None
    )
    resolved = [{"ret_line": _return_line(b_line, serial="SER-B"), "orig_line": b_line}]
    assert (
        ret_mod._guard_return_serial_mismatch(
            resolved, no_override, order_id, STORE, {"user_id": "u"}, order=order
        )
        is None
    )


# ---------------------------------------------------------------------------
# TSC-3: the trace's sale lines go through the one cost rule
# ---------------------------------------------------------------------------
# TSU-4 made the trace find the scanned sale, so it now returns that order's
# lines -- and each line carries cost_at_sale. The route is open to every
# signed-in user, so the lines pass through cost_mask like the scan replies.


@pytest.mark.parametrize("role", COUNTER_ROLES)
def test_trace_sale_lines_carry_no_cost_for_counter_roles(client, till, role):
    r = _sell(client, [_line(stock_id=UNIT_B["stock_id"])])
    assert r.status_code in (200, 201), r.text
    url = f"/api/v1/inventory/barcode/{UNIT_B['barcode']}/trace"

    admin = client.get(url, headers=_token(["ADMIN"])).json()["sales"]
    assert admin[0]["matched_lines"][0]["cost_at_sale"] == 4000.0  # the guard

    sales = client.get(url, headers=_token([role])).json()["sales"]
    assert [s["order_number"] for s in sales] == [s["order_number"] for s in admin]
    for line in sales[0]["matched_lines"]:
        assert "cost_at_sale" not in line and "cost_price" not in line, line
