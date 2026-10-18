"""Shop-scope rules (owner 2026-10-18), probed over HTTP on the real app.

  1  An ACCOUNTANT assigned to a shop sees ONLY that shop on the Tally sales
     JV, the B2B invoice book (list + by-id actions), the GST cross-check and
     bank statements; company-wide (and the company's cross-check sign-off)
     needs an admin; an accountant with no shop is refused (R4).
  2  GET /orders from a non-admin login with NO shop lists no shop's orders.
  3  An ADMIN with no shop named reads EVERY shop on reports, HR, tasks,
     workshop and inventory (store_in_view), not his top-bar shop; a screen
     of one shop's own (a day's close) keeps the top-bar shop; non-admins
     unchanged.
  4  Collection insights: area and catalogue managers read their own shops.
  5  Pune's store_id is a UUID (code BV-PUN-01): PO / receipt numbers, the
     audit-log store filter and the payout / points ids print the code.
  6  The barcode trace makes no query for a login with no shop.

Two shops (DHN, BOK) plus Pune (a UUID store_id), on mongomock.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.dependencies import NO_SHOP_DETAIL  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402

DHN, BOK = "BV-DHN-01", "BV-BOK-01"
PUNE, PUNE_CODE = "4dc49c44-08a1-46e1-85fb-8b7eca55f560", "BV-PUN-01"
GSTIN = "20ABCDE1234F1Z5"
TODAY = ist_today().isoformat()


def _user(role, shops=(), active="first"):
    shops = list(shops)
    act = (shops[0] if shops else None) if active == "first" else active
    return {"user_id": f"u-{role.lower()}-{'-'.join(shops) or 'none'}",
            "username": role.lower(), "roles": [role],
            "store_ids": shops, "active_store_id": act}


def _hdr(user):
    from api.routers.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user)}"}


class _Conn:
    """The app's database connection, backed by mongomock."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        return self.db[name]


@pytest.fixture
def world(monkeypatch):
    import database.connection as conn_mod
    from api import dependencies as deps

    db = mongomock.MongoClient().db
    now = datetime.now()
    for sid, code, state in ((DHN, DHN, "Jharkhand"), (BOK, BOK, "Jharkhand"),
                             (PUNE, PUNE_CODE, "Maharashtra")):
        db.stores.insert_one({"store_id": sid, "store_code": code, "store_name": code,
                              "state": state, "entity_id": "E1"})
        db.orders.insert_one({
            "order_id": f"O-{code}", "order_number": f"INV-{code}", "store_id": sid,
            "status": "DELIVERED", "created_at": now, "grand_total": 1050.0,
            "tax_amount": 50.0, "balance_due": 10.0, "customer_gstin": GSTIN,
            "customer_name": f"Firm {code}",
            "items": [{"product_id": "P1", "quantity": 1, "unit_price": 1050.0,
                       "total": 1050.0, "gst_rate": 5}],
        })
        db.bank_statements.insert_one({"statement_id": f"BS-{code}", "store_id": sid,
                                       "uploaded_at": now.isoformat(), "rows": []})
        db.shifts.insert_one({"shift_id": f"SH-{code}", "store_id": sid, "is_active": True})
        db.workshop_jobs.insert_one({"job_id": f"J-{code}", "store_id": sid,
                                     "status": "PENDING", "expected_date": TODAY})
        db.stock_counts.insert_one({"count_id": f"SC-{code}", "store_id": sid,
                                    "status": "COMPLETED", "created_at": now})
        db.stock_units.insert_one({"stock_id": f"U-{code}", "product_id": "P1",
                                   "store_id": sid, "barcode": f"29000000{len(code)}{code[3:6]}",
                                   "status": "AVAILABLE", "quantity": 1})
    db.ecom_collections.insert_one({"collection_id": "C1", "title": "Aviators",
                                    "handle": "aviators"})
    db.collection_products.insert_one({"collection_id": "C1", "product_id": "P1", "sku": "S1"})
    db.products.insert_one({"product_id": "P1", "sku": "S1", "name": "Aviator",
                            "category": "FRAME", "mrp": 1050.0, "is_active": True})
    conn = _Conn(db)
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(conn_mod, "get_db", lambda: conn)
    return db


def _get(client, user, path):
    return client.get(path, headers=_hdr(user))


ADMIN_TOPBAR_DHN = _user("ADMIN", [DHN])
ACCOUNTANT_BOK = _user("ACCOUNTANT", [BOK])
NO_SHOP_ACCOUNTANT = _user("ACCOUNTANT")


# ===========================================================================
# 1. An accountant reads his own shop's books; company-wide is an admin's
# ===========================================================================

# The day before to the day after: the orders (stamped by the box clock)
# fall inside whether the export reads a date as a UTC or an IST day -- this
# file tests the shop rule, not the day boundary.
_JV = (f"/api/v1/finance/tally/sales-jv?from_date={ist_today() - timedelta(days=1)}"
       f"&to_date={ist_today() + timedelta(days=1)}")


def test_sales_jv_is_the_accountants_shop_only(client, world):
    every = _get(client, ADMIN_TOPBAR_DHN, _JV)
    assert every.status_code == 200, every.text[:300]
    assert all(f"O-{c}" in every.text for c in (DHN, BOK, PUNE_CODE))
    own = _get(client, ACCOUNTANT_BOK, _JV)
    assert own.status_code == 200, own.text[:300]
    assert f"O-{BOK}" in own.text
    assert f"O-{DHN}" not in own.text and f"O-{PUNE_CODE}" not in own.text
    # Naming another shop is a 403; a login with no shop is refused.
    assert _get(client, ACCOUNTANT_BOK, _JV + f"&store_id={DHN}").status_code == 403
    r = _get(client, NO_SHOP_ACCOUNTANT, _JV)
    assert (r.status_code, r.json()["detail"]) == (403, NO_SHOP_DETAIL)


def test_b2b_invoice_book_is_the_accountants_shop_only(client, world):
    def ids(user, q=""):
        r = _get(client, user, "/api/v1/finance/b2b-invoices" + q)
        assert r.status_code == 200, r.text[:300]
        return {row["order_id"] for row in r.json()["invoices"]}

    assert ids(ADMIN_TOPBAR_DHN) == {f"O-{c}" for c in (DHN, BOK, PUNE_CODE)}
    assert ids(ACCOUNTANT_BOK) == {f"O-{BOK}"}
    assert ids(ACCOUNTANT_BOK, f"?entity_id=E1") == {f"O-{BOK}"}
    assert _get(client, ACCOUNTANT_BOK, f"/api/v1/finance/b2b-invoices?store_id={DHN}").status_code == 403


def test_b2b_actions_reach_only_the_accountants_shop(client, world):
    h = _hdr(ACCOUNTANT_BOK)
    # Another shop's invoice reads as one that does not exist.
    assert client.get(f"/api/v1/finance/b2b-invoices/O-{DHN}/tally-xml", headers=h).status_code == 404
    assert client.post(f"/api/v1/finance/b2b-invoices/O-{DHN}/mark-done", headers=h).status_code == 404
    assert client.post(f"/api/v1/finance/b2b-invoices/O-{DHN}/attention-note",
                       json={"note": "x"}, headers=h).status_code == 404
    r = client.post("/api/v1/finance/b2b-invoices/export",
                    json={"order_ids": [f"O-{DHN}"], "mark_in_tally": True}, headers=h)
    assert r.status_code == 404
    r = client.post("/api/v1/finance/b2b-invoices/mark-exported",
                    json={"order_ids": [f"O-{DHN}", f"O-{BOK}"]}, headers=h)
    assert r.status_code == 200 and r.json()["marked"] == 1
    assert world.orders.find_one({"order_id": f"O-{DHN}"}).get("exported_to_tally") is None
    assert world.orders.find_one({"order_id": f"O-{BOK}"})["exported_to_tally"] is True
    # His own shop's invoice: every action works.
    assert client.get(f"/api/v1/finance/b2b-invoices/O-{BOK}/tally-xml", headers=h).status_code == 200
    assert client.post(f"/api/v1/finance/b2b-invoices/O-{BOK}/mark-done", headers=h).status_code == 200
    # An admin reaches every shop's.
    assert client.post(f"/api/v1/finance/b2b-invoices/O-{DHN}/mark-done",
                       headers=_hdr(ADMIN_TOPBAR_DHN)).status_code == 200


def test_gst_cross_check_is_the_accountants_shop_and_signoff_an_admins(client, world):
    every = _get(client, ADMIN_TOPBAR_DHN, "/api/v1/finance/gst/cross-check")
    assert every.status_code == 200, every.text[:300]
    assert (every.json()["store_count"], every.json()["store_id"]) == (3, None)
    own = _get(client, ACCOUNTANT_BOK, "/api/v1/finance/gst/cross-check")
    assert own.status_code == 200, own.text[:300]
    assert (own.json()["store_count"], own.json()["store_id"]) == (1, BOK)
    body = {"month": datetime.now().month, "year": datetime.now().year}
    r = client.post("/api/v1/finance/gst/cross-check-signoff", json=body,
                    headers=_hdr(ACCOUNTANT_BOK))
    assert r.status_code == 403 and "admin" in r.json()["detail"]


def test_bank_statements_are_the_accountants_shop_only(client, world):
    def ids(user, q=""):
        r = _get(client, user, "/api/v1/finance/bank-statement" + q)
        assert r.status_code == 200, r.text[:300]
        return {s["statement_id"] for s in r.json()["statements"]}

    assert ids(ADMIN_TOPBAR_DHN) == {f"BS-{c}" for c in (DHN, BOK, PUNE_CODE)}
    assert ids(ADMIN_TOPBAR_DHN, f"?store_id={PUNE}") == {f"BS-{PUNE_CODE}"}
    assert ids(ACCOUNTANT_BOK) == {f"BS-{BOK}"}
    assert _get(client, ACCOUNTANT_BOK, f"/api/v1/finance/bank-statement?store_id={DHN}").status_code == 403
    assert _get(client, ACCOUNTANT_BOK, f"/api/v1/finance/bank-statement/BS-{DHN}").status_code == 404
    assert _get(client, ACCOUNTANT_BOK, f"/api/v1/finance/bank-statement/BS-{BOK}").status_code == 200


def test_tender_receipt_jv_holds_the_same_gate(client, world):
    # Dark by default (policy off -> 403 'disabled'); the shop gate answers
    # first, in the owner's words, for a login with no shop.
    r = _get(client, NO_SHOP_ACCOUNTANT, "/api/v1/finance/tally/tender-receipt-jv")
    assert (r.status_code, r.json()["detail"]) == (403, NO_SHOP_DETAIL)


# ===========================================================================
# 2. GET /orders: a login with no shop lists no shop's orders
# ===========================================================================


@pytest.mark.parametrize("role", ("SALES_STAFF", "CASHIER", "STORE_MANAGER", "ACCOUNTANT"))
def test_orders_list_is_empty_for_a_login_with_no_shop(client, world, role):
    r = _get(client, _user(role), "/api/v1/orders")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["orders"] == []
    assert _get(client, _user(role), f"/api/v1/orders?store_id={DHN}").status_code == 403


def test_orders_list_unchanged_for_shop_logins_and_admins(client, world):
    def ids(user):
        r = _get(client, user, "/api/v1/orders")
        assert r.status_code == 200, r.text[:300]
        return {o["id"] for o in r.json()["orders"]}

    assert ids(_user("SALES_STAFF", [BOK])) == {f"O-{BOK}"}
    assert ids(_user("ADMIN")) == {f"O-{c}" for c in (DHN, BOK, PUNE_CODE)}


# ===========================================================================
# 3. An admin with no shop named reads every shop (store_in_view)
# ===========================================================================

# (path, list key, id key, id prefix)
_ALL_SHOP_LISTS = [
    ("/api/v1/hr/shifts", "shifts", "shift_id", "SH-"),
    ("/api/v1/workshop/pending", "jobs", None, None),
    ("/api/v1/inventory/stock-count", "counts", "count_id", "SC-"),
]


def _shops_in(client, user, path, key, id_key, prefix, query=""):
    r = _get(client, user, path + query)
    assert r.status_code == 200, (path, r.text[:300])
    rows = r.json()[key]
    if id_key is None:
        return {row.get("storeId") or row.get("store_id") for row in rows}
    return {row[id_key][len(prefix):] for row in rows}


@pytest.mark.parametrize("path,key,id_key,prefix", _ALL_SHOP_LISTS)
def test_admin_with_no_shop_named_reads_every_shop(client, world, path, key, id_key, prefix):
    every = {DHN, BOK, PUNE_CODE} if id_key else {DHN, BOK, PUNE}
    assert _shops_in(client, ADMIN_TOPBAR_DHN, path, key, id_key, prefix) == every
    # The shop filter still narrows it.
    named = _shops_in(client, ADMIN_TOPBAR_DHN, path, key, id_key, prefix, f"?store_id={BOK}")
    assert named == {BOK}


@pytest.mark.parametrize("path,key,id_key,prefix", _ALL_SHOP_LISTS)
def test_non_admins_keep_their_own_shop(client, world, path, key, id_key, prefix):
    assert _shops_in(client, _user("STORE_MANAGER", [BOK]), path, key, id_key, prefix) == {BOK}


def test_admin_reports_read_every_shop(client, world):
    r = _get(client, ADMIN_TOPBAR_DHN, "/api/v1/reports/finance/outstanding")
    assert r.status_code == 200, r.text[:300]
    assert {d["order_id"] for d in r.json()["data"]} == {f"O-{c}" for c in (DHN, BOK, PUNE_CODE)}
    r = _get(client, _user("STORE_MANAGER", [BOK]), "/api/v1/reports/finance/outstanding")
    assert {d["order_id"] for d in r.json()["data"]} == {f"O-{BOK}"}


def test_a_screen_of_one_shops_own_keeps_the_admins_topbar_shop(client, world):
    r = _get(client, ADMIN_TOPBAR_DHN, f"/api/v1/reports/day-end-close?date={TODAY}")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["store_id"] == DHN


# ===========================================================================
# 4. Collection insights: area and catalogue managers read their own shops
# ===========================================================================


@pytest.mark.parametrize("role", ("AREA_MANAGER", "CATALOG_MANAGER"))
def test_collection_insights_hold_managers_to_their_shops(client, world, role):
    manager = _user(role, [BOK, DHN])
    r = _get(client, manager, "/api/v1/collections/insights/summary")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["store_id"] == BOK
    assert _get(client, manager, f"/api/v1/collections/insights/summary?store_id={PUNE}").status_code == 403
    assert _get(client, manager, f"/api/v1/collections/C1/insights?store_id={PUNE}").status_code == 403
    r = _get(client, manager, "/api/v1/collections/C1/insights/stores")
    assert r.status_code == 200, r.text[:300]
    assert {row["store_id"] for row in r.json()["stores"]} == {BOK, DHN}


def test_collection_insights_admin_reads_every_shop(client, world):
    r = _get(client, ADMIN_TOPBAR_DHN, "/api/v1/collections/C1/insights/stores")
    assert r.status_code == 200, r.text[:300]
    assert {row["store_id"] for row in r.json()["stores"]} == {DHN, BOK, PUNE}
    assert _get(client, ADMIN_TOPBAR_DHN, "/api/v1/collections/insights/summary").json()["store_id"] is None


# ===========================================================================
# 5. Pune (a UUID store_id) prints its code
# ===========================================================================


def test_po_and_receipt_numbers_print_punes_code(world, monkeypatch):
    from api.routers.vendors import numbering

    monkeypatch.setattr(numbering, "_get_db", lambda: world)
    po = numbering.generate_po_number(PUNE)
    grn = numbering.generate_grn_number(PUNE)
    assert po.startswith(f"PO/{PUNE_CODE}/") and PUNE not in po
    assert grn.startswith(f"RCPT/{PUNE_CODE}/") and PUNE not in grn
    # A code passed in still wins; a shop with no store document keeps its id.
    assert numbering.generate_po_number(PUNE, "X-1").startswith("PO/X-1/")
    assert numbering.generate_po_number("BV-NEW-09").startswith("PO/BV-NEW-09/")


def test_payout_and_points_ids_print_punes_code(world):
    from database.repositories.payout_snapshot_repository import PayoutSnapshotRepository
    from database.repositories.points_log_repository import PointsLogRepository

    # The FULL code: two online shops never share one (both cut to ONL).
    world.stores.insert_many([{"store_id": "BV-ONLINE-01", "store_code": "BV-ONLINE-01"},
                              {"store_id": "WO-ONLINE-01", "store_code": "WO-ONLINE-01"}])
    for repo in (PayoutSnapshotRepository(world.payout_snapshots),
                 PointsLogRepository(world.points_log)):
        assert repo._store_code(PUNE) == PUNE_CODE
        assert repo._store_code(DHN) == DHN
        assert repo._store_code("BV-ONLINE-01") != repo._store_code("WO-ONLINE-01")
    assert PayoutSnapshotRepository(world.payout_snapshots)._generate_snapshot_id(
        PUNE, 2026, 10, "LOCKED") == "PAY-BV-PUN-01-2026-10"
    assert PointsLogRepository(world.points_log)._generate_log_id(
        PUNE, "2026-10-18", "USER-AB12").startswith("PL-BV-PUN-01-2026-10-18-AB12-")


def test_audit_log_filter_finds_pune_by_its_id_or_its_code(client, world):
    for sid in (PUNE, PUNE_CODE):
        world.audit_logs.insert_one({"log_id": f"L-{sid}", "store_id": sid,
                                     "action": "VIEW", "timestamp": datetime.now()})
    for named in (PUNE, PUNE_CODE):
        r = _get(client, _user("SUPERADMIN"), f"/api/v1/settings/audit-logs?store_id={named}")
        assert r.status_code == 200, r.text[:300]
        assert {log["log_id"] for log in r.json()["logs"]} == {f"L-{PUNE}", f"L-{PUNE_CODE}"}


# ===========================================================================
# 6. The barcode trace makes no query for a login with no shop
# ===========================================================================


def test_trace_skips_every_query_for_a_login_with_no_shop(client, world, monkeypatch):
    from api.routers.inventory import barcode_trace

    touched = []

    class _Spy:
        def get_collection(self, name):
            touched.append(name)
            return world[name]

    monkeypatch.setattr(barcode_trace, "_get_db", lambda: _Spy())
    unit = world.stock_units.find_one({"store_id": BOK})["barcode"]
    r = _get(client, _user("STORE_MANAGER"), f"/api/v1/inventory/barcode/{unit}/trace")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["stock_unit"] is None and r.json()["sales"] == []
    assert touched == []
    # A shop login still traces its own unit.
    r = _get(client, _user("STORE_MANAGER", [BOK]), f"/api/v1/inventory/barcode/{unit}/trace")
    assert r.json()["stock_unit"]["store_id"] == BOK and touched


# ===========================================================================
# Review round 1 (coordinator, 2026-10-19)
# ===========================================================================

# -- 1. Every order list, not only GET /orders: a login with no shop sees none

_ORDER_LISTS = [
    ("/api/v1/orders/pending/delivery", "orders"),
    ("/api/v1/orders/unpaid/list", "orders"),
    ("/api/v1/orders/overdue/list", "orders"),
    ("/api/v1/orders/search?q=INV", "orders"),
    ("/api/v1/orders/status/counts", "statusCounts"),
]


@pytest.fixture
def open_orders(world):
    """One open, part-paid, overdue order per Jharkhand shop: every list holds it."""
    for code in (DHN, BOK):
        world.orders.insert_one({
            "order_id": f"OPEN-{code}", "order_number": f"INV-OPEN-{code}",
            "store_id": code, "status": "READY", "payment_status": "PARTIAL",
            "balance_due": 100.0, "expected_delivery": "2026-01-01",
            "created_at": datetime.now(), "grand_total": 500.0,
        })
    return world


@pytest.mark.parametrize("path,key", _ORDER_LISTS)
@pytest.mark.parametrize("role", ("SALES_STAFF", "CASHIER"))
def test_every_order_list_is_empty_for_a_login_with_no_shop(client, open_orders, path, key, role):
    r = _get(client, _user(role), path)
    assert r.status_code == 200, (path, r.text[:300])
    assert not r.json()[key], (path, r.json()[key])
    # The same list still answers a shop login with its own shop.
    r = _get(client, _user(role, [BOK]), path)
    assert r.status_code == 200 and r.json()[key], (path, r.text[:300])
    assert DHN not in r.text


def test_crm_customer_lists_hold_a_login_with_no_shop(client, world, monkeypatch):
    from api.routers import crm

    customers = [{"customer_id": f"C-{c}", "store_ids": [c], "primary_store_id": c}
                 for c in (DHN, BOK)]
    seen = []

    class _Db:
        def query_customers_by_store(self, store_id):
            seen.append(store_id)
            return [c for c in customers if store_id in c["store_ids"]]

        def query_all_customers(self):
            seen.append("ALL")
            return list(customers)

    monkeypatch.setattr(crm, "db", _Db())
    for path in ("/api/v1/crm/customers/segment/rfm", "/api/v1/crm/customers/churn-risk/list"):
        seen.clear()
        r = _get(client, _user("STORE_MANAGER"), path)
        assert r.status_code == 200, (path, r.text[:300])
        assert "ALL" not in seen and DHN not in seen and BOK not in seen, (path, seen)


def test_promotions_report_holds_a_login_with_no_shop(client, world, monkeypatch):
    from api.routers.reports import promotions as promo_report

    world.promo_applications.insert_one({
        "promo_id": "PR-1", "store_id": DHN, "order_id": "O-x",
        "total_discount_given": 50.0, "applied_at": f"{TODAY}T10:00:00",
    })
    monkeypatch.setattr(promo_report, "get_db", lambda: _Conn(world))

    def given(user):
        r = _get(client, user, "/api/v1/reports/promotions")
        assert r.status_code == 200, r.text[:300]
        return r.json()["summary"]["total_discount_given"]

    assert given(_user("STORE_MANAGER")) == 0
    assert given(_user("STORE_MANAGER", [DHN])) == 50.0
    assert given(_user("STORE_MANAGER", [BOK])) == 0


# -- 2 + 3. The cross-check: the accountant's shops' bills only, and a GSTIN
#    he shares with other shops is withheld (admin view)

GST_20, GST_27 = "20AAAAA0000A1Z5", "27AAAAA0000A1Z5"


@pytest.fixture
def gst_world(world):
    """DHN and BOK file on one Jharkhand GSTIN; Pune on its own. One booked
    bill per Jharkhand shop whose credit no return places (no company)."""
    world.entities.insert_one({"entity_id": "E1", "name": "BV",
                               "gstins": [{"gstin": GST_20}, {"gstin": GST_27}]})
    month = TODAY[:7]
    for code in (DHN, BOK):
        world.vendor_bills.insert_one({
            "bill_id": f"VB-{code}", "bill_number": f"BILL-{code}", "store_id": code,
            "status": "BOOKED", "tax_amount": 180.0, "invoice_date": f"{month}-01",
            "vendor_gstin": "20BBBBB0000B1Z5",
        })
    return world


def _xc(client, user, query=""):
    r = _get(client, user, "/api/v1/finance/gst/cross-check" + query)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_cross_check_unplaced_credit_is_the_accountants_shop_bills_only(client, gst_world):
    own = _xc(client, ACCOUNTANT_BOK)["itc_unplaced"]["bill_numbers"]
    assert own == [f"BILL-{BOK}"], own
    every = _xc(client, ADMIN_TOPBAR_DHN)["itc_unplaced"]["bill_numbers"]
    assert set(every) == {f"BILL-{DHN}", f"BILL-{BOK}"}, every


def test_a_shared_gstin_withholds_the_returns_input_credit(client, gst_world):
    body = _xc(client, ACCOUNTANT_BOK)
    assert body["gstin_note"] and body["store_count"] == 1
    assert body["summary"]["gst_payable"] is None
    assert body["gstr3b"]["itc"] is None and body["gstr3b"]["netCash"] is None
    rows = {c["metric"]: c for c in body["comparisons"]}
    for metric in ("Input tax credit (ITC)", "Net GST payable (cash)"):
        assert rows[metric]["status"] == "INFO", rows[metric]
        assert all(k.startswith("Books") for k in rows[metric]["sources"]), rows[metric]
    # Holding every shop on the GSTIN: the whole filing, nothing withheld.
    both = _xc(client, _user("ACCOUNTANT", [BOK, DHN]))
    assert both["gstin_note"] is None and both["store_count"] == 2
    assert both["gstr3b"]["itc"] is not None
    # store_id picks one of his shops; another's is a 403.
    assert _xc(client, _user("ACCOUNTANT", [BOK, DHN]), f"?store_id={DHN}")["store_id"] == DHN
    assert _get(client, ACCOUNTANT_BOK, f"/api/v1/finance/gst/cross-check?store_id={DHN}").status_code == 403


# -- 10. An accountant's company must be his shop's

def test_an_accountant_naming_another_company_is_refused(client, world):
    for path in (_JV + "&entity_id=E2", "/api/v1/finance/b2b-invoices?entity_id=E2"):
        r = _get(client, ACCOUNTANT_BOK, path)
        assert r.status_code == 403, (path, r.text[:300])
        assert _get(client, ADMIN_TOPBAR_DHN, path).status_code == 200


# -- 4, 5, 9. Every screen of one shop's own (and the escalation write) keeps
#    the admin's top-bar shop when none is named -- pinned at each call site

_ONE_SHOP_MODULES = (
    "api.routers.analytics", "api.routers.dashboard_widgets", "api.routers.finance.cash_drawer",
    "api.routers.inventory.accountability", "api.routers.inventory.stock", "api.routers.lens_stock",
    "api.routers.reports.analytics", "api.routers.reports.blueprint", "api.routers.reports.customers",
    "api.routers.reports.day_end", "api.routers.reports.finance_ops", "api.routers.reports.gstr1",
    "api.routers.reports.purchase", "api.routers.tasks", "api.routers.workshop.lab_stations",
)

_ONE_SHOP_ROUTES = [
    ("GET", "/api/v1/analytics/dashboard-summary"),
    ("GET", "/api/v1/analytics/revenue-trends"),
    ("GET", "/api/v1/analytics/inventory-intelligence"),
    ("GET", "/api/v1/analytics/customer-insights"),
    ("GET", "/api/v1/analytics/enterprise-kpis"),
    ("GET", "/api/v1/clinical/patient-queue"),
    ("GET", "/api/v1/analytics/store-target-today"),
    ("GET", "/api/v1/finance/cash-register/sessions"),
    ("GET", "/api/v1/inventory/transfer-recommendations"),
    ("GET", "/api/v1/inventory/stock?product_id=P1"),
    ("GET", "/api/v1/inventory/units?product_id=P1"),
    ("GET", "/api/v1/lens-stock/LL1"),
    ("GET", "/api/v1/reports/walkouts/footfall-audit"),
    ("GET", "/api/v1/reports/blueprint"),
    ("GET", "/api/v1/reports/targets"),
    ("GET", f"/api/v1/reports/day-end-close?date={TODAY}"),
    ("GET", f"/api/v1/reports/finance/gst?from_date={TODAY}&to_date={TODAY}"),
    ("GET", f"/api/v1/reports/gstr1?month={TODAY[:7]}"),
    ("GET", f"/api/v1/reports/gstr1/gstn-json?month={TODAY[:7]}"),
    ("GET", "/api/v1/reports/purchase/recommendations"),
    ("GET", "/api/v1/tasks/checklists?type=opening"),
    ("POST", "/api/v1/tasks/scan/payment-variance"),
    ("GET", "/api/v1/tasks/sop-checklist?template_id=SOP1"),
    ("POST", "/api/v1/tasks/auto-escalate-overdue"),
    ("GET", "/api/v1/workshop/stations"),
    ("GET", "/api/v1/workshop/stations/EDGING/queue"),
]


@pytest.mark.parametrize("method,path", _ONE_SHOP_ROUTES)
def test_a_screen_of_one_shops_own_reads_the_admins_topbar_shop(client, world, monkeypatch, method, path):
    import importlib

    from api import dependencies as deps

    world.sop_templates.insert_one({"template_id": "SOP1", "title": "Open", "steps": []})
    got = []

    def _spy(store_id, current_user, **kw):
        shop = deps.store_in_view(store_id, current_user, **kw)
        got.append(shop)
        return shop

    for name in _ONE_SHOP_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "store_in_view", _spy)
    # The SOP template the checklist route reads before the shop.
    monkeypatch.setattr(importlib.import_module("api.routers.tasks"), "get_db", lambda: _Conn(world))
    try:
        client.request(method, path, headers=_hdr(_user("SUPERADMIN", [DHN])))
    except Exception:  # noqa: BLE001 -- the shop is decided before any later failure
        pass
    # His top-bar shop -- never every shop, and never the store-001 fallback.
    assert got and got[0] == DHN, (path, got)


# -- 12. The admin's all-shops dashboard counts every order, not the last 500

def test_all_shops_dashboard_counts_every_order(client, world):
    world.orders.insert_many([
        {"order_id": f"BULK-{i}", "store_id": BOK, "status": "DELIVERED",
         "grand_total": 1.0, "created_at": datetime.now()} for i in range(520)
    ])
    r = _get(client, _user("ADMIN"), "/api/v1/reports/dashboard")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["totalSales"] == pytest.approx(520 + 3 * 1050.0), r.json()


# -- 13. A stores read that fails refuses the number (never the UUID series)

def test_numbering_refuses_when_the_shop_cannot_be_read(monkeypatch):
    from fastapi import HTTPException as _HTTP

    from api.routers.vendors import numbering

    class _Stores:
        def find_one(self, *a, **k):
            raise RuntimeError("stores unreadable")

    class _Broken:
        def get_collection(self, name):
            return _Stores()

    monkeypatch.setattr(numbering, "_get_db", lambda: _Broken())
    for mint in (numbering.generate_po_number, numbering.generate_grn_number):
        with pytest.raises(_HTTP) as err:
            mint(PUNE)
        assert err.value.status_code == 503
