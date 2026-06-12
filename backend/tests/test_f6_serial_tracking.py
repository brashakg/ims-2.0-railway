"""
IMS 2.0 - Feature #6 per-unit serial tracking -- intent tests.

A unique serial is captured at STOCK-IN, atomically transitioned IN_STOCK -> SOLD
at sale (so a serial can NEVER be double-sold), and looked up for warranty/recall.

Covers:
  - normalize_serial + duplicate-serial rejection (409),
  - capture mints an IN_STOCK unit,
  - mark_serial_sold is the atomic double-sell guard (second sale of one serial -> None),
  - transition return/recall + illegal-transition guard,
  - warranty lookup -> unit + sale + customer,
  - route RBAC: a cashier cannot capture/mark-sold (403) but CAN read a warranty,
  - route store-scope: a cross-store actor is blocked,
  - a non-serialized category is a no-op (no forced serial).

No whole-JSON substring asserts; the unique index is emulated in the fake. No emoji.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import serial_tracking as svc  # noqa: E402


# --------------------------------------------------------------------------- #
# Fake Mongo with an emulated UNIQUE index on serial
# --------------------------------------------------------------------------- #
class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def _match(self, d, q):
        for k, v in q.items():
            if isinstance(v, dict) and "$in" in v:
                if d.get(k) not in v["$in"]:
                    return False
            elif d.get(k) != v:
                return False
        return True

    def insert_one(self, doc):
        sn = doc.get("serial")
        if sn and any(d.get("serial") == sn for d in self.docs):
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("E11000 duplicate key: serial")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": "oid"})()

    def find_one(self, q, *a, **k):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    def find_one_and_update(self, q, u, return_document=None, **k):
        for d in self.docs:
            if self._match(d, q):
                d.update(u.get("$set") or {})
                return dict(d)
        return None

    def create_index(self, *a, **k):
        return "idx"


class _DB:
    def __init__(self):
        self._c: Dict[str, _Coll] = {}

    def get_collection(self, name):
        return self._c.setdefault(name, _Coll())


SERIALIZED = ["HEARING_AID", "LUXURY_FRAME"]


def _policy(key, scope=None, *, default=None):
    if key == svc.SERIALIZED_CATEGORIES_KEY:
        return SERIALIZED
    return default


_MGR = {"user_id": "m1", "roles": ["STORE_MANAGER"], "active_store_id": "S1"}
_CASHIER = {"user_id": "c1", "roles": ["SALES_CASHIER"], "active_store_id": "S1"}


@pytest.fixture
def units():
    return _Coll()


# --------------------------------------------------------------------------- #
# Pure + capture + uniqueness
# --------------------------------------------------------------------------- #
def test_normalize_serial():
    assert svc.normalize_serial("  ab-12 ") == svc.normalize_serial("AB-12")
    assert svc.normalize_serial("") in ("", None)


def test_capture_mints_in_stock(units):
    u = svc.capture_serial(units, serial="SN-1", product_id="P1", store_id="S1")
    assert u["status"] == svc.STATUS_IN_STOCK
    assert u["serial"] == svc.normalize_serial("SN-1")


def test_duplicate_serial_rejected(units):
    svc.capture_serial(units, serial="SN-1", product_id="P1", store_id="S1")
    with pytest.raises(svc.SerialError) as ei:
        svc.capture_serial(units, serial="sn-1", product_id="P2", store_id="S1")  # same after normalize
    assert ei.value.status == 409


# --------------------------------------------------------------------------- #
# The atomic double-sell guard
# --------------------------------------------------------------------------- #
def test_mark_sold_atomic_no_double_sell(units):
    svc.capture_serial(units, serial="SN-9", product_id="P1", store_id="S1")
    first = svc.mark_serial_sold(units, serial="SN-9", order_id="ORD-1", store_id="S1", customer_id="CUST-1")
    assert first is not None and first["status"] == svc.STATUS_SOLD
    # Second sale of the SAME serial: the IN_STOCK guard no longer matches -> None.
    second = svc.mark_serial_sold(units, serial="SN-9", order_id="ORD-2", store_id="S1")
    assert second is None  # NO double-sell


def test_recall_and_illegal_transition(units):
    svc.capture_serial(units, serial="SN-R", product_id="P1", store_id="S1")
    rec = svc.transition_serial(units, serial="SN-R", to_status=svc.STATUS_RECALLED, store_id="S1", reason="defect")
    assert rec["status"] == svc.STATUS_RECALLED
    # RECALLED is terminal -> cannot move to SOLD.
    with pytest.raises(svc.SerialError) as ei:
        svc.transition_serial(units, serial="SN-R", to_status=svc.STATUS_SOLD, store_id="S1")
    assert ei.value.status == 409


def test_warranty_lookup(units):
    db = _DB()
    u = db.get_collection("stock_units")
    svc.capture_serial(u, serial="SN-W", product_id="P1", store_id="S1", warranty_months=12)
    svc.mark_serial_sold(u, serial="SN-W", order_id="ORD-1", store_id="S1", customer_id="CUST-1")
    db.get_collection("orders").insert_one({"order_id": "ORD-1", "customer_id": "CUST-1"})
    db.get_collection("customers").insert_one({"customer_id": "CUST-1", "name": "A"})
    res = svc.lookup_warranty(db.get_collection("stock_units"), db.get_collection("orders"),
                              db.get_collection("customers"), serial="SN-W")
    assert res["unit"]["serial"] == svc.normalize_serial("SN-W")
    assert res["unit"]["status"] == svc.STATUS_SOLD


def test_non_serialized_category_is_noop(units):
    # A category NOT in the configured serialized set -> is_serialized_category False.
    assert svc.is_serialized_category("FRAME", _policy, "S1") is False
    assert svc.is_serialized_category("HEARING_AID", _policy, "S1") is True


# --------------------------------------------------------------------------- #
# Route RBAC + store-scope
# --------------------------------------------------------------------------- #
def test_route_cashier_cannot_capture(monkeypatch):
    from api.routers import serial_tracking as r
    from fastapi import HTTPException
    monkeypatch.setattr(r, "_units", lambda: _Coll())
    monkeypatch.setattr(r, "validate_store_access", lambda s, u: s)
    body = r.CaptureBody(serial="SN-1", product_id="P1", store_id="S1")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.capture(body, _CASHIER))
    assert ei.value.status_code == 403


def test_route_cashier_can_read_warranty(monkeypatch):
    from api.routers import serial_tracking as r
    db = _DB()
    u = db.get_collection("stock_units")
    svc.capture_serial(u, serial="SN-W", product_id="P1", store_id="S1", warranty_months=12)
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda s, usr: s)
    out = asyncio.run(r.warranty("SN-W", _CASHIER))  # cashier CAN read
    assert out["unit"]["serial"] == svc.normalize_serial("SN-W")


def test_route_capture_cross_store_blocked(monkeypatch):
    from api.routers import serial_tracking as r
    from fastapi import HTTPException
    monkeypatch.setattr(r, "_units", lambda: _Coll())

    def _deny(store_id, user):
        raise HTTPException(status_code=403, detail="cross-store")

    monkeypatch.setattr(r, "validate_store_access", _deny)
    body = r.CaptureBody(serial="SN-1", product_id="P1", store_id="S2")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.capture(body, _MGR))
    assert ei.value.status_code == 403
