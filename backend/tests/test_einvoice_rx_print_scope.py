"""
Codex audit L3-18 + L2-CLIN-F01: two read doors that skipped the per-object
store rule (and, for the Rx print, the clinical role gate) their canonical
siblings enforce.

L3-18      POST /finance/einvoice/{order_id} returned another shop's cached
           IRN / QR / ack to a store-scoped ACCOUNTANT whose GET /orders/{id}
           for the same order is 403.
L2-CLIN-F01 GET /clinical/prescriptions/{id}/print served the full Rx card to
           INVESTOR (403 on GET /prescriptions/{id}) and to a foreign shop.
           Sibling redo doors (/redo, /redos) had the same per-object hole.

Bare app + dependency override + fake repos: no Mongo, no GSP/IRP call (the
order already carries an IRN, so generate_irn short-circuits to SKIPPED).
"""

from __future__ import annotations

import importlib
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

einvoice_router = importlib.import_module("api.routers.finance.einvoice")

STORE_A, STORE_B = "store-A", "store-B"


def _user(roles, store_ids, active=None):
    return {
        "user_id": "u1",
        "username": "tester",
        "full_name": "Test User",
        "roles": roles,
        "activeRole": roles[0],
        "store_ids": store_ids,
        "active_store_id": active if active is not None else (store_ids or [None])[0],
    }


# ============================================================================
# L3-18 -- e-invoice door
# ============================================================================

_ORDER_B = {
    "id": "ord-B1",
    "store_id": STORE_B,
    "irn": "IRN-CACHED-SECRET",
    "ack_no": "ACK-999",
    "ack_date": "2026-11-01",
    "einvoice_signed_qr": "QR-SECRET",
}


class _FakeColl:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, *_a, **_k):
        return dict(self._doc) if self._doc else None


class _FakeDb:
    def get_collection(self, name):
        return _FakeColl(_ORDER_B if name == "orders" else None)


def _einv_client(monkeypatch, user):
    monkeypatch.setattr(einvoice_router, "_get_db", lambda: _FakeDb())
    app = FastAPI()
    app.include_router(einvoice_router.router, prefix="/finance")

    async def _fake():
        return user

    app.dependency_overrides[get_current_user] = _fake
    return TestClient(app)


class TestEinvoiceStoreScope:
    def test_foreign_store_accountant_gets_no_cached_irn(self, monkeypatch):
        c = _einv_client(monkeypatch, _user(["ACCOUNTANT"], [STORE_A]))
        r = c.post("/finance/einvoice/ord-B1")
        assert r.status_code == 403
        assert "IRN-CACHED-SECRET" not in r.text
        assert "QR-SECRET" not in r.text

    def test_same_store_accountant_still_reads_cached_irn(self, monkeypatch):
        c = _einv_client(monkeypatch, _user(["ACCOUNTANT"], [STORE_B]))
        r = c.post("/finance/einvoice/ord-B1")
        assert r.status_code == 200
        assert r.json()["irn"] == "IRN-CACHED-SECRET"

    def test_admin_reads_any_store(self, monkeypatch):
        c = _einv_client(monkeypatch, _user(["ADMIN"], [STORE_A]))
        r = c.post("/finance/einvoice/ord-B1")
        assert r.status_code == 200
        assert r.json()["signed_qr"] == "QR-SECRET"


# ============================================================================
# L2-CLIN-F01 -- Rx print + sibling redo doors
# ============================================================================

_RX_B = {
    "prescription_id": "rx-B1",
    "id": "rx-B1",
    "store_id": STORE_B,
    "patient_name": "AUDIT PATIENT",
    "right_eye": {"sphere": "-7.25", "cylinder": "-1.50", "axis": 90},
    "left_eye": {"sphere": "-6.75", "cylinder": "-1.25", "axis": 85},
    "redos": [{"redo_id": "r1", "reason": "SECRET-REDO-REASON"}],
}


class _FakeRxRepo:
    def __init__(self):
        self.updated = []

    def find_by_id(self, rx_id):
        return dict(_RX_B) if rx_id == "rx-B1" else None

    def update(self, rx_id, fields):
        self.updated.append((rx_id, fields))
        return True


def _rx_client(monkeypatch, user, repo=None):
    repo = repo or _FakeRxRepo()
    monkeypatch.setattr(clinical, "get_prescription_repository", lambda: repo)
    monkeypatch.setattr(clinical, "get_store_repository", lambda: None)
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake():
        return user

    app.dependency_overrides[get_current_user] = _fake
    return TestClient(app)


PRINT = "/clinical/prescriptions/rx-B1/print"
REDOS = "/clinical/prescriptions/rx-B1/redos"
REDO = "/clinical/prescriptions/rx-B1/redo"


class TestRxPrintScope:
    def test_investor_denied_print(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["INVESTOR"], [STORE_B]))
        r = c.get(PRINT)
        assert r.status_code == 403
        assert "AUDIT PATIENT" not in r.text

    def test_foreign_shop_optometrist_gets_404_no_content(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_A]))
        r = c.get(PRINT)
        assert r.status_code == 404
        assert "AUDIT PATIENT" not in r.text

    def test_area_manager_outside_reach_404(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["AREA_MANAGER"], [STORE_A]))
        assert c.get(PRINT).status_code == 404

    def test_same_shop_optometrist_prints(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_B]))
        r = c.get(PRINT)
        assert r.status_code == 200
        assert "AUDIT PATIENT" in r.text

    def test_area_manager_own_stores_prints(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["AREA_MANAGER"], [STORE_A, STORE_B]))
        assert c.get(PRINT).status_code == 200

    def test_admin_prints_any_shop(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["ADMIN"], [STORE_A]))
        assert c.get(PRINT).status_code == 200


class TestRedoSiblingScope:
    def test_investor_denied_redo_history(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["INVESTOR"], [STORE_B]))
        r = c.get(REDOS)
        assert r.status_code == 403
        assert "SECRET-REDO-REASON" not in r.text

    def test_foreign_shop_redo_history_404(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_A]))
        r = c.get(REDOS)
        assert r.status_code == 404
        assert "SECRET-REDO-REASON" not in r.text

    def test_same_shop_redo_history(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_B]))
        r = c.get(REDOS)
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_foreign_shop_cannot_record_redo(self, monkeypatch):
        repo = _FakeRxRepo()
        c = _rx_client(monkeypatch, _user(["STORE_MANAGER"], [STORE_A]), repo)
        r = c.post(REDO, json={"reason": "lens remake"})
        assert r.status_code == 404
        assert repo.updated == []

    def test_same_shop_records_redo(self, monkeypatch):
        repo = _FakeRxRepo()
        c = _rx_client(monkeypatch, _user(["STORE_MANAGER"], [STORE_B]), repo)
        r = c.post(REDO, json={"reason": "lens remake"})
        assert r.status_code == 200
        assert len(repo.updated) == 1
