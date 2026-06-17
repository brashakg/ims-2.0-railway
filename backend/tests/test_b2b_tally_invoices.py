"""
IMS 2.0 - B2B invoices -> Tally console + worklist tests
=========================================================
Covers backend/api/routers/finance.py B2B-invoice endpoints (owner decision
2026-06-17: e-invoice + e-way bill are issued in Tally, so the accountant pulls
B2B invoices as Tally XML and keeps a reminder worklist).

What is asserted:
  * list filters to B2B customers only (B2C / walk-in excluded)
  * needs_eway derivation (inter-state OR value >= Rs 50,000)
  * PENDING-age overdue reminder + tally_status default
  * worklist PENDING-only filter
  * mark-done transition (-> DONE, stamps done_at/done_by)
  * mark-exported stamps exported_to_tally + advances PENDING -> IN_TALLY
  * export builds well-formed Tally XML for the selection
  * RBAC gate: SALES_STAFF is 403 on every endpoint

FakeDB/FakeCollection borrowed from test_tally_export.py.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo double (subset used by the B2B handlers)
# ---------------------------------------------------------------------------
def _doc_matches(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$lt" and not (actual is not None and actual < op_val):
                    return False
                if op == "$in" and actual not in op_val:
                    return False
                if op == "$nin" and actual in op_val:
                    return False
                if op == "$ne" and actual == op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("order_id")})()

    def find(self, filter_=None, projection=None):
        return [dict(d) for d in self.docs if _doc_matches(d, filter_)]

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _doc_matches(d, filter_):
                return dict(d)
        return None

    def update_one(self, filter_, update, upsert=False):
        matched = 0
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter_):
                matched = 1
                d.update((update or {}).get("$set", {}) or {})
                modified = 1
                break
        return type("R", (), {"matched_count": matched, "modified_count": modified})()

    def update_many(self, filter_, update, upsert=False):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter_):
                d.update((update or {}).get("$set", {}) or {})
                modified += 1
        return type("R", (), {"matched_count": modified, "modified_count": modified})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


# ---------------------------------------------------------------------------
# Seeded data
# ---------------------------------------------------------------------------
def _seed_db():
    db = FakeDB()
    db.get_collection("stores").insert_one(
        {"store_id": "S-JH", "state": "Jharkhand", "store_code": "BOK01", "store_name": "Bokaro"}
    )

    customers = db.get_collection("customers")
    # B2B intra-state (Jharkhand), small value -> needs_eway False
    customers.insert_one(
        {"customer_id": "C-B2B-INTRA", "customer_type": "B2B", "gstin": "20ABCDE1234F1Z5", "state": "Jharkhand", "name": "Acme Optics"}
    )
    # B2B inter-state (Maharashtra) -> needs_eway True (inter-state)
    customers.insert_one(
        {"customer_id": "C-B2B-INTER", "customer_type": "B2B", "gstin": "27ABCDE1234F1Z5", "state": "Maharashtra", "name": "Mumbai Vision"}
    )
    # B2B intra-state but high value (>= 50k) -> needs_eway True (value)
    customers.insert_one(
        {"customer_id": "C-B2B-BIG", "customer_type": "B2B", "gstin": "20ZZZZE1234F1Z5", "state": "Jharkhand", "name": "Bulk Buyer"}
    )
    # B2C retail -> excluded entirely
    customers.insert_one(
        {"customer_id": "C-B2C", "customer_type": "B2C", "gstin": "", "state": "Jharkhand", "name": "Walk-in Ravi"}
    )

    orders = db.get_collection("orders")
    now = datetime.now()
    old = now - timedelta(days=10)  # well past the 3-day reminder

    def _items(rate, taxable, tax):
        return [{"gst_rate": rate, "taxable_value": taxable, "tax_amount": tax}]

    # B2B intra, old -> PENDING + overdue, needs_eway False
    orders.insert_one(
        {
            "order_id": "ORD-INTRA", "order_number": "BV/BOK01/26-27/0001",
            "store_id": "S-JH", "customer_id": "C-B2B-INTRA", "customer_name": "Acme Optics",
            "status": "DELIVERED", "created_at": old,
            "grand_total": 1180.0, "items": _items(18.0, 1000.0, 180.0),
        }
    )
    # B2B inter, recent -> PENDING not overdue, needs_eway True (inter-state)
    orders.insert_one(
        {
            "order_id": "ORD-INTER", "order_number": "BV/BOK01/26-27/0002",
            "invoice_number": "INV/BOK01/26-27/0002",
            "store_id": "S-JH", "customer_id": "C-B2B-INTER", "customer_name": "Mumbai Vision",
            "status": "COMPLETED", "created_at": now,
            "grand_total": 2360.0, "items": _items(18.0, 2000.0, 360.0),
        }
    )
    # B2B intra big value -> needs_eway True (value >= 50k)
    orders.insert_one(
        {
            "order_id": "ORD-BIG", "order_number": "BV/BOK01/26-27/0003",
            "store_id": "S-JH", "customer_id": "C-B2B-BIG", "customer_name": "Bulk Buyer",
            "status": "DELIVERED", "created_at": now,
            "grand_total": 59000.0, "items": _items(18.0, 50000.0, 9000.0),
        }
    )
    # B2C retail -> must NOT appear
    orders.insert_one(
        {
            "order_id": "ORD-B2C", "order_number": "BV/BOK01/26-27/0004",
            "store_id": "S-JH", "customer_id": "C-B2C", "customer_name": "Walk-in Ravi",
            "status": "DELIVERED", "created_at": now,
            "grand_total": 1180.0, "items": _items(18.0, 1000.0, 180.0),
        }
    )
    # DRAFT B2B -> excluded (not a real invoice)
    orders.insert_one(
        {
            "order_id": "ORD-DRAFT", "order_number": "BV/BOK01/26-27/0005",
            "store_id": "S-JH", "customer_id": "C-B2B-INTRA", "customer_name": "Acme Optics",
            "status": "DRAFT", "created_at": now,
            "grand_total": 1180.0, "items": _items(18.0, 1000.0, 180.0),
        }
    )
    return db


# ---------------------------------------------------------------------------
# Fixtures: patched finance._get_db + TestClient + JWTs
# ---------------------------------------------------------------------------
@pytest.fixture
def fdb(monkeypatch):
    db = _seed_db()
    from api.routers import finance as finance_module

    monkeypatch.setattr(finance_module, "_get_db", lambda: db)
    return db


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _token(roles, store_ids=None):
    from api.routers.auth import create_access_token

    return create_access_token(
        {
            "user_id": f"u-{roles[0].lower()}",
            "username": roles[0].lower(),
            "roles": roles,
            "active_role": roles[0],
            "store_ids": store_ids or [],
            "active_store_id": (store_ids or [None])[0],
        }
    )


@pytest.fixture
def acct_headers():
    return {"Authorization": f"Bearer {_token(['ACCOUNTANT'])}"}


@pytest.fixture
def staff_headers():
    return {"Authorization": f"Bearer {_token(['SALES_STAFF'], ['S-JH'])}"}


# ---------------------------------------------------------------------------
# List: B2B-only + GST split + needs_eway + overdue
# ---------------------------------------------------------------------------
def test_list_returns_only_b2b_invoices(fdb, client, acct_headers):
    r = client.get("/api/v1/finance/b2b-invoices", headers=acct_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["order_id"] for row in body["invoices"]}
    # The three B2B real orders only -- B2C + DRAFT excluded.
    assert ids == {"ORD-INTRA", "ORD-INTER", "ORD-BIG"}
    assert body["summary"]["count"] == 3
    assert body["eway_threshold"] == 50000.0


def test_needs_eway_derivation(fdb, client, acct_headers):
    rows = {r["order_id"]: r for r in client.get(
        "/api/v1/finance/b2b-invoices", headers=acct_headers).json()["invoices"]}
    # intra-state + small value -> no e-way
    assert rows["ORD-INTRA"]["needs_eway"] is False
    assert rows["ORD-INTRA"]["interstate"] is False
    # inter-state -> e-way
    assert rows["ORD-INTER"]["needs_eway"] is True
    assert rows["ORD-INTER"]["interstate"] is True
    assert rows["ORD-INTER"]["igst"] > 0
    # intra but value >= 50k -> e-way (value threshold)
    assert rows["ORD-BIG"]["needs_eway"] is True
    assert rows["ORD-BIG"]["interstate"] is False


def test_gst_split_intra_vs_inter(fdb, client, acct_headers):
    rows = {r["order_id"]: r for r in client.get(
        "/api/v1/finance/b2b-invoices", headers=acct_headers).json()["invoices"]}
    intra = rows["ORD-INTRA"]
    assert intra["cgst"] == 90.0 and intra["sgst"] == 90.0 and intra["igst"] == 0.0
    assert intra["taxable"] == 1000.0
    inter = rows["ORD-INTER"]
    assert inter["cgst"] == 0.0 and inter["sgst"] == 0.0 and inter["igst"] == 360.0
    # invoice_number preferred over order_number when stamped
    assert inter["invoice_number"] == "INV/BOK01/26-27/0002"
    # un-stamped order falls back to order_number
    assert intra["invoice_number"] == "BV/BOK01/26-27/0001"


def test_pending_default_and_overdue_reminder(fdb, client, acct_headers):
    rows = {r["order_id"]: r for r in client.get(
        "/api/v1/finance/b2b-invoices", headers=acct_headers).json()["invoices"]}
    # default status PENDING
    assert all(r["tally_status"] == "PENDING" for r in rows.values())
    # 10-day-old PENDING -> overdue; same-day ones not overdue
    assert rows["ORD-INTRA"]["overdue"] is True
    assert rows["ORD-INTRA"]["age_days"] >= 3
    assert rows["ORD-INTER"]["overdue"] is False


# ---------------------------------------------------------------------------
# Worklist: PENDING-only filter
# ---------------------------------------------------------------------------
def test_worklist_pending_only_filter(fdb, client, acct_headers):
    # Move one to DONE, then filter PENDING -- it should drop out.
    client.post("/api/v1/finance/b2b-invoices/ORD-BIG/mark-done", headers=acct_headers)
    r = client.get("/api/v1/finance/b2b-invoices?tally_status=PENDING", headers=acct_headers)
    ids = {row["order_id"] for row in r.json()["invoices"]}
    assert ids == {"ORD-INTRA", "ORD-INTER"}
    assert "ORD-BIG" not in ids


# ---------------------------------------------------------------------------
# Transitions: mark-done, mark-exported (-> IN_TALLY), attention-note
# ---------------------------------------------------------------------------
def test_mark_done_transition(fdb, client, acct_headers):
    r = client.post("/api/v1/finance/b2b-invoices/ORD-INTRA/mark-done", headers=acct_headers)
    assert r.status_code == 200, r.text
    assert r.json()["tally_status"] == "DONE"
    doc = fdb.get_collection("orders").find_one({"order_id": "ORD-INTRA"})
    assert doc["tally_status"] == "DONE"
    assert doc["done_at"] is not None
    assert doc["done_by"]


def test_mark_done_404_unknown(fdb, client, acct_headers):
    r = client.post("/api/v1/finance/b2b-invoices/NOPE/mark-done", headers=acct_headers)
    assert r.status_code == 404


def test_mark_exported_stamps_and_advances(fdb, client, acct_headers):
    r = client.post(
        "/api/v1/finance/b2b-invoices/mark-exported",
        json={"order_ids": ["ORD-INTRA", "ORD-INTER"]},
        headers=acct_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 2
    rows = {x["order_id"]: x for x in client.get(
        "/api/v1/finance/b2b-invoices", headers=acct_headers).json()["invoices"]}
    assert rows["ORD-INTRA"]["exported_to_tally"] is True
    assert rows["ORD-INTRA"]["tally_status"] == "IN_TALLY"
    assert rows["ORD-INTER"]["exported_to_tally"] is True


def test_attention_note_set_and_clear(fdb, client, acct_headers):
    r = client.post(
        "/api/v1/finance/b2b-invoices/ORD-BIG/attention-note",
        json={"note": "Check e-way bill before filing"},
        headers=acct_headers,
    )
    assert r.status_code == 200
    rows = {x["order_id"]: x for x in client.get(
        "/api/v1/finance/b2b-invoices", headers=acct_headers).json()["invoices"]}
    assert rows["ORD-BIG"]["attention_note"] == "Check e-way bill before filing"
    # Clear it
    client.post(
        "/api/v1/finance/b2b-invoices/ORD-BIG/attention-note",
        json={"note": ""},
        headers=acct_headers,
    )
    rows = {x["order_id"]: x for x in client.get(
        "/api/v1/finance/b2b-invoices", headers=acct_headers).json()["invoices"]}
    assert rows["ORD-BIG"]["attention_note"] == ""


# ---------------------------------------------------------------------------
# Export: per-invoice + bulk XML well-formed
# ---------------------------------------------------------------------------
def test_per_invoice_tally_xml_well_formed(fdb, client, acct_headers):
    r = client.get("/api/v1/finance/b2b-invoices/ORD-INTER/tally-xml", headers=acct_headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    xml = r.text
    assert "<ENVELOPE>" in xml
    assert xml.count('<VOUCHER VCHTYPE="Sales"') == 1
    # inter-state -> IGST ledger
    assert "<LEDGERNAME>IGST Output</LEDGERNAME>" in xml
    # voucher number uses the stamped invoice number
    assert "INV/BOK01/26-27/0002" in xml


def test_per_invoice_xml_404_for_b2c(fdb, client, acct_headers):
    # A B2C order is not a B2B invoice -> 404 (not exportable here).
    r = client.get("/api/v1/finance/b2b-invoices/ORD-B2C/tally-xml", headers=acct_headers)
    assert r.status_code == 404


def test_bulk_export_xml_and_in_tally_advance(fdb, client, acct_headers):
    r = client.post(
        "/api/v1/finance/b2b-invoices/export",
        json={"order_ids": ["ORD-INTRA", "ORD-INTER", "ORD-BIG"], "mark_in_tally": True},
        headers=acct_headers,
    )
    assert r.status_code == 200, r.text
    xml = r.text
    assert xml.count('<VOUCHER VCHTYPE="Sales"') == 3
    assert "<LEDGERNAME>IGST Output</LEDGERNAME>" in xml  # the inter-state one
    assert "<LEDGERNAME>CGST Output</LEDGERNAME>" in xml  # the intra-state ones
    # PENDING rows advanced to IN_TALLY
    for oid in ("ORD-INTRA", "ORD-INTER", "ORD-BIG"):
        assert fdb.get_collection("orders").find_one({"order_id": oid})["tally_status"] == "IN_TALLY"


def test_bulk_export_404_when_selection_not_b2b(fdb, client, acct_headers):
    r = client.post(
        "/api/v1/finance/b2b-invoices/export",
        json={"order_ids": ["ORD-B2C"]},
        headers=acct_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# RBAC: floor staff blocked on every endpoint
# ---------------------------------------------------------------------------
def test_rbac_blocks_sales_staff(fdb, client, staff_headers):
    checks = [
        ("GET", "/api/v1/finance/b2b-invoices", None),
        ("GET", "/api/v1/finance/b2b-invoices/ORD-INTRA/tally-xml", None),
        ("POST", "/api/v1/finance/b2b-invoices/export", {"order_ids": ["ORD-INTRA"]}),
        ("POST", "/api/v1/finance/b2b-invoices/mark-exported", {"order_ids": ["ORD-INTRA"]}),
        ("POST", "/api/v1/finance/b2b-invoices/ORD-INTRA/mark-done", None),
        ("POST", "/api/v1/finance/b2b-invoices/ORD-INTRA/attention-note", {"note": "x"}),
    ]
    for method, path, json_body in checks:
        if method == "GET":
            resp = client.get(path, headers=staff_headers)
        else:
            resp = client.post(path, json=json_body, headers=staff_headers)
        assert resp.status_code == 403, f"{method} {path} expected 403, got {resp.status_code}"
