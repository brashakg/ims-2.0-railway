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
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.dependencies import NO_SHOP_DETAIL  # noqa: E402

DHN, BOK = "BV-DHN-01", "BV-BOK-01"
PUNE, PUNE_CODE = "4dc49c44-08a1-46e1-85fb-8b7eca55f560", "BV-PUN-01"
GSTIN = "20ABCDE1234F1Z5"
TODAY = datetime.now().date().isoformat()


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

_JV = f"/api/v1/finance/tally/sales-jv?from_date={TODAY}&to_date={TODAY}"


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

    for repo in (PayoutSnapshotRepository(world.payout_snapshots),
                 PointsLogRepository(world.points_log)):
        assert repo._store_code(PUNE) == "PUN"
        assert repo._store_code(DHN) == "DHN"
    assert PayoutSnapshotRepository(world.payout_snapshots)._generate_snapshot_id(
        PUNE, 2026, 10, "LOCKED") == "PAY-PUN-2026-10"
    assert PointsLogRepository(world.points_log)._generate_log_id(
        PUNE, "2026-10-18", "USER-AB12").startswith("PL-PUN-2026-10-18-AB12-")


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
