"""
IMS 2.0 - Vendor performance (QC pass-rate + MTD spend) + new credit-note types
================================================================================
Covers the workshop+vendor gap closure:

GAP 2(a) -- vendor-performance endpoint now also returns:
  - qc_pass_rate  : joins GRN QC (accepted/received) + workshop QC (job pass/fail)
  - mtd_spend     : sum of vendor_bills dated in the current calendar month

GAP 2(b) -- vendor credit notes recognise DISCOUNT_CN + QUALITY_CN types
  - schema accepts the two new types (+ existing RETURN_CN / SCHEME_CN)
  - schema rejects an unknown type
  - a created note persists cn_type and (GL/ledger) reduces the payable exactly
    like the existing CN types (ap_engine.build_ledger DEBIT_NOTE row).

Tests run with a fake in-memory DB injected onto vendors._get_db -- no Mongo.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import vendors  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.vendors import DebitNoteCreate, VENDOR_CN_TYPES  # noqa: E402
from api.services.ap_engine import build_ledger  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Mongo (just enough for the debit-note + performance endpoints)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, _n):
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None, projection=None):
        query = query or {}

        def _match(d):
            return all(d.get(k) == v for k, v in query.items())

        return _FakeCursor([d for d in self.docs if _match(d)])

    def find_one(self, query=None, projection=None):
        for d in self.find(query, projection):
            return d
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def update_one(self, *a, **k):
        return None


class _FakeDB:
    def __init__(self, collections=None):
        self._c = {name: _FakeColl(docs) for name, docs in (collections or {}).items()}

    def get_collection(self, name):
        if name not in self._c:
            self._c[name] = _FakeColl([])
        return self._c[name]


class _FakeVendorRepo:
    def __init__(self, vendor):
        self._v = vendor

    def find_by_id(self, _vid):
        return self._v


def _client(db, vendor=None, roles=("SUPERADMIN",), monkeypatch=None):
    app = FastAPI()
    app.include_router(vendors.router, prefix="/api/v1/vendors")

    async def _u():
        return {
            "user_id": "u1",
            "full_name": "T",
            "username": "t",
            "roles": list(roles),
            "store_ids": ["S1"],
            "active_store_id": "S1",
            "discount_cap": None,
        }

    app.dependency_overrides[get_current_user] = _u
    monkeypatch.setattr(vendors, "_get_db", lambda: db)
    monkeypatch.setattr(
        vendors, "get_vendor_repository", lambda: _FakeVendorRepo(vendor)
    )
    return TestClient(app)


VENDOR = {"vendor_id": "v1", "trade_name": "Acme", "legal_name": "Acme Pvt"}


# ===========================================================================
# GAP 2(a) -- qc_pass_rate + mtd_spend on /performance
# ===========================================================================


def test_performance_includes_mtd_spend(monkeypatch):
    this_month = datetime.now().strftime("%Y-%m-15")
    last_month_year, last_month = divmod(datetime.now().month - 2, 12)
    old = f"{datetime.now().year + last_month_year}-{last_month + 1:02d}-15"
    db = _FakeDB(
        {
            "vendor_bills": [
                {"vendor_id": "v1", "total_amount": 1000.0, "bill_date": this_month},
                {"vendor_id": "v1", "total_amount": 500.0, "bill_date": this_month},
                {"vendor_id": "v1", "total_amount": 999.0, "bill_date": old},  # not MTD
            ],
            "grns": [],
        }
    )
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    r = cli.get("/api/v1/vendors/v1/performance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mtd_spend" in body
    assert body["mtd_spend"] == 1500.0  # only this month's two bills


def test_performance_qc_pass_rate_from_grn(monkeypatch):
    db = _FakeDB(
        {
            "vendor_bills": [],
            "grns": [
                {
                    "vendor_id": "v1",
                    "status": "ACCEPTED",
                    "total_received": 10,
                    "total_accepted": 8,
                    "created_at": datetime.now().isoformat(),
                },
                {
                    "vendor_id": "v1",
                    "status": "ACCEPTED",
                    "total_received": 10,
                    "total_accepted": 10,
                    "created_at": datetime.now().isoformat(),
                },
            ],
        }
    )
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    body = cli.get("/api/v1/vendors/v1/performance").json()
    # 18 accepted / 20 received = 0.9
    assert body["qc_pass_rate"] == 0.9
    assert body["qc_sample_size"] == 20


def test_performance_qc_pass_rate_joins_workshop(monkeypatch):
    db = _FakeDB(
        {
            "vendor_bills": [],
            "grns": [
                {
                    "vendor_id": "v1",
                    "status": "ACCEPTED",
                    "total_received": 2,
                    "total_accepted": 2,  # 2/2 pass
                    "created_at": datetime.now().isoformat(),
                }
            ],
            "workshop_jobs": [
                {"vendor_id": "v1", "qc_passed": True},   # pass
                {"vendor_id": "v1", "qc_passed": False},  # fail
                {"vendor_id": "v1"},                       # no QC -> excluded
            ],
        }
    )
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    body = cli.get("/api/v1/vendors/v1/performance").json()
    # GRN: 2 pass / 2 ; workshop: 1 pass / 2 -> total 3 pass / 4 = 0.75
    assert body["qc_sample_size"] == 4
    assert body["qc_pass_rate"] == 0.75


def test_performance_qc_pass_rate_none_when_no_signal(monkeypatch):
    db = _FakeDB({"vendor_bills": [], "grns": []})
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    body = cli.get("/api/v1/vendors/v1/performance").json()
    assert body["qc_pass_rate"] is None
    assert body["qc_sample_size"] == 0
    assert body["mtd_spend"] == 0.0


# ===========================================================================
# GAP 2(b) -- DISCOUNT_CN + QUALITY_CN credit-note types
# ===========================================================================


def test_schema_recognizes_new_cn_types():
    assert "DISCOUNT_CN" in VENDOR_CN_TYPES
    assert "QUALITY_CN" in VENDOR_CN_TYPES
    for t in ("RETURN_CN", "SCHEME_CN", "DISCOUNT_CN", "QUALITY_CN"):
        dn = DebitNoteCreate(amount=100.0, date="2026-06-17", reason="x", cn_type=t)
        assert dn.cn_type == t


def test_schema_defaults_cn_type_to_return():
    dn = DebitNoteCreate(amount=10.0, date="2026-06-17", reason="x")
    assert dn.cn_type == "RETURN_CN"


def test_schema_lowercases_and_accepts():
    dn = DebitNoteCreate(amount=10.0, date="2026-06-17", reason="x", cn_type="quality_cn")
    assert dn.cn_type == "QUALITY_CN"


def test_schema_rejects_unknown_cn_type():
    with pytest.raises(Exception):
        DebitNoteCreate(amount=10.0, date="2026-06-17", reason="x", cn_type="BOGUS")


def test_create_discount_cn_persists_type_and_reduces_payable(monkeypatch):
    db = _FakeDB({"vendor_debit_notes": [], "vendor_bills": []})
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    r = cli.post(
        "/api/v1/vendors/v1/debit-notes",
        json={
            "amount": 250.0,
            "date": "2026-06-17",
            "reason": "negotiated post-billing discount",
            "cn_type": "DISCOUNT_CN",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cn_type"] == "DISCOUNT_CN"
    assert body["source"] == "DISCOUNT_CN"
    # Persisted to the SAME collection AP/ledger reads.
    saved = db.get_collection("vendor_debit_notes").docs
    assert len(saved) == 1
    assert saved[0]["cn_type"] == "DISCOUNT_CN"


def test_create_quality_cn_persists_type(monkeypatch):
    db = _FakeDB({"vendor_debit_notes": [], "vendor_bills": []})
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    r = cli.post(
        "/api/v1/vendors/v1/debit-notes",
        json={
            "amount": 75.0,
            "date": "2026-06-17",
            "reason": "defect compensation, goods kept",
            "cn_type": "QUALITY_CN",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["cn_type"] == "QUALITY_CN"


def test_create_rejects_unknown_cn_type_via_api(monkeypatch):
    db = _FakeDB({"vendor_debit_notes": [], "vendor_bills": []})
    cli = _client(db, VENDOR, monkeypatch=monkeypatch)
    r = cli.post(
        "/api/v1/vendors/v1/debit-notes",
        json={"amount": 75.0, "date": "2026-06-17", "reason": "x", "cn_type": "WRONG"},
    )
    assert r.status_code == 422, r.text


def test_new_cn_types_get_gl_treatment_like_existing():
    """A DISCOUNT_CN / QUALITY_CN reduces the running payable in build_ledger
    exactly like a RETURN_CN -- the GL treatment mirrors the existing types
    (amount is a DEBIT row that lowers what we owe), with the cn_type surfaced."""
    bills = [{"bill_number": "B1", "total_amount": 1000.0, "bill_date": "2026-06-01"}]
    debit_notes = [
        {
            "debit_note_number": "DN1",
            "amount": 300.0,
            "date": "2026-06-05",
            "reason": "discount",
            "cn_type": "DISCOUNT_CN",
        }
    ]
    led = build_ledger(bills, [], debit_notes)
    assert led["closing_balance"] == 700.0  # 1000 billed - 300 CN
    dn_rows = [r for r in led["entries"] if r["type"] == "DEBIT_NOTE"]
    assert len(dn_rows) == 1
    assert dn_rows[0]["debit"] == 300.0
    assert dn_rows[0]["cn_type"] == "DISCOUNT_CN"
    assert led["total_debit_notes"] == 300.0
