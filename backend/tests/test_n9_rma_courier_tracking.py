"""
IMS 2.0 - N9 (RMA half): courier/AWB tracking on vendor-return shipments
========================================================================
The transfers half of N9 already ships tracking (transfers.py ship-stamp +
/tracking). This covers the RMA half: a vendor return's "shipped" transition
captures courier_name / tracking_number / tracking_url (+ shipped_at) and
surfaces them on the return doc, so staff can see where an in-transit RMA
physically is. Exercises the REAL router function against a faithful in-memory
fake Mongo.
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

from api.routers import vendor_returns as vr  # noqa: E402


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    def update_one(self, query, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    def __init__(self):
        self._c: Dict[str, FakeCollection] = {}

    def get_collection(self, name):
        if name not in self._c:
            self._c[name] = FakeCollection()
        return self._c[name]


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(vr, "_get_db", lambda: fake)
    monkeypatch.setattr(vr, "validate_store_access", lambda sid, u: sid)
    return fake


def _manager(store="BV-1"):
    return {"user_id": "M1", "roles": ["STORE_MANAGER"], "store_ids": [store],
            "active_store_id": store}


def _seed_return(db, *, status="approved", return_id="VR-1"):
    db.get_collection("vendor_returns").insert_one({
        "return_id": return_id, "vendor_id": "V1", "store_id": "BV-1",
        "status": status, "total_value": 5000.0, "status_history": [],
    })
    return return_id


def _update(return_id, **kwargs):
    body = vr.VendorReturnStatusUpdate(**kwargs)
    return asyncio.run(vr.update_return_status(return_id, body, current_user=_manager()))


def test_shipped_transition_persists_courier_tracking(db):
    rid = _seed_return(db, status="approved")
    out = _update(rid, status="shipped", courier_name="Bluedart",
                  tracking_number="AWB123456", tracking_url="https://track.example/AWB123456")
    doc = out["return"]
    assert doc["status"] == "shipped"
    assert doc["courier_name"] == "Bluedart"
    assert doc["tracking_number"] == "AWB123456"
    assert doc["tracking_url"] == "https://track.example/AWB123456"
    assert doc["shipped_at"]  # stamped once on ship


def test_history_entry_carries_tracking(db):
    rid = _seed_return(db, status="approved")
    out = _update(rid, status="shipped", courier_name="DTDC", tracking_number="X1")
    hist = out["return"]["status_history"]
    assert hist[-1]["status"] == "shipped"
    assert hist[-1]["tracking_number"] == "X1"
    assert hist[-1]["courier_name"] == "DTDC"


def test_tracking_ignored_on_non_shipping_transition(db):
    """Tracking fields on a created->approved transition are NOT persisted --
    they only mean something once the parcel actually ships."""
    rid = _seed_return(db, status="created")
    out = _update(rid, status="approved", courier_name="Bluedart", tracking_number="EARLY")
    doc = out["return"]
    assert "tracking_number" not in doc and "courier_name" not in doc
    assert "shipped_at" not in doc


def test_tracking_correction_on_received_by_vendor(db):
    """A wrong AWB can be corrected while the parcel is in transit (the
    received_by_vendor transition also accepts the fields)."""
    rid = _seed_return(db, status="approved")
    _update(rid, status="shipped", tracking_number="WRONG-1")
    out = _update(rid, status="received_by_vendor", tracking_number="RIGHT-2")
    assert out["return"]["tracking_number"] == "RIGHT-2"


def test_shipped_at_not_overwritten(db):
    rid = _seed_return(db, status="approved")
    first = _update(rid, status="shipped", tracking_number="A1")["return"]["shipped_at"]
    out = _update(rid, status="received_by_vendor")
    assert out["return"]["shipped_at"] == first


def test_blank_tracking_values_not_persisted(db):
    rid = _seed_return(db, status="approved")
    out = _update(rid, status="shipped", courier_name="   ", tracking_number="")
    doc = out["return"]
    assert "courier_name" not in doc and "tracking_number" not in doc
    assert doc["status"] == "shipped"  # the transition itself still lands


def test_invalid_transition_still_400(db):
    from fastapi import HTTPException

    rid = _seed_return(db, status="created")
    with pytest.raises(HTTPException) as exc:
        _update(rid, status="received_by_vendor")
    assert exc.value.status_code == 400


def test_overlong_tracking_url_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        vr.VendorReturnStatusUpdate(status="shipped", tracking_url="x" * 501)
