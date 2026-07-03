"""vendor_returns update_return_status compare-and-swap (salvaged 2026-07-03).

Two racing transitions out of the SAME status must not both issue a credit note.
A frozen-read fake simulates two requests that both read the pre-transition
snapshot (received_by_vendor); the CAS baked into the update_one filter lets
exactly one win (200) and the other 409s -- so the credit note is minted once.
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendor_returns as vr  # noqa: E402


class _R:
    def __init__(self, m):
        self.matched_count = m
        self.modified_count = m


class _FrozenReadColl:
    """find_one returns the FROZEN pre-transition snapshot (both racers read it);
    update_one enforces the CAS against the REAL current status."""

    def __init__(self, doc):
        self._real = dict(doc)
        self._snapshot = dict(doc)
        self.writes = 0

    def find_one(self, filt=None, *a, **k):
        return dict(self._snapshot)

    def update_one(self, filt, update, upsert=False):
        want = filt.get("status", None)
        matches = (
            filt.get("return_id") == self._real.get("return_id")
            and (want is None or self._real.get("status") == want)
        )
        if matches:
            self._real.update(update.get("$set", {}))
            self.writes += 1
            return _R(1)
        return _R(0)


class _FakeDB:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, name):
        return self._coll


class _Body:
    """Stand-in for VendorReturnStatusUpdate (only the attrs the endpoint reads)."""

    def __init__(self, status):
        self.status = status
        self.notes = None
        self.courier_name = None
        self.tracking_number = None
        self.tracking_url = None


def _seed():
    return {
        "return_id": "VR-1",
        "store_id": "STORE-A",
        "status": "received_by_vendor",
        "total_value": 1200.0,
        "status_history": [],
    }


def _admin():
    return {"user_id": "u1", "roles": ["ADMIN"], "active_store_id": "STORE-A", "store_ids": []}


def test_concurrent_credit_issue_one_wins_one_409(monkeypatch):
    coll = _FrozenReadColl(_seed())
    monkeypatch.setattr(vr, "_get_db", lambda: _FakeDB(coll))

    async def _call():
        return await vr.update_return_status("VR-1", _Body("credit_issued"), _admin())

    # First transition wins the CAS.
    out1 = asyncio.run(_call())
    assert "credit_issued" in out1["message"]

    # Second racer still sees the frozen received_by_vendor snapshot, so it passes
    # the transition-validity check but LOSES the CAS -> 409, no second write.
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call())
    assert e.value.status_code == 409

    # Exactly ONE write happened -> exactly one credit note issued.
    assert coll.writes == 1
    assert coll._real.get("status") == "credit_issued"
    assert coll._real.get("credit_note_number")
