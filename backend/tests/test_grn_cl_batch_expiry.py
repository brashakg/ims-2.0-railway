"""Purchase P2 -- contact-lens batch + expiry captured at goods-receipt.

A CL GRN line carries batch_code (or lot_number) + expiry_date; accept_grn
stamps them onto each minted serialized unit so the stock is dated for FEFO
consumption + near-expiry reporting (the SAME fields /stock/add persists, so a
GRN-received CL unit is indistinguishable from a manually-added one). Frames /
undated lines mint exactly as before (additive, backward-compatible).

Drives the real accept_grn coroutine against the CAS-capable in-memory FakeDB
(same monkeypatch pattern as test_e3w_wiring) -- no live Mongo.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_e3_item_event import CasDB, _run  # noqa: E402

_MANAGER = {
    "user_id": "mgr-1",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S-A"],
    "active_store_id": "S-A",
}


class _FakeGRNRepo:
    def __init__(self, grn):
        self._grn = grn

    def find_by_id(self, gid):
        return self._grn if gid == self._grn.get("grn_id") else None

    def update(self, gid, patch):
        self._grn.update(patch)
        return True

    def find_many(self, *a, **k):
        return [self._grn]

    def find(self, *a, **k):
        return [self._grn]

    def claim_for_accept(self, gid, from_statuses):
        if gid != self._grn.get("grn_id") or self._grn.get("status") not in from_statuses:
            return None
        pre = dict(self._grn)
        self._grn["status"] = "ACCEPTING"
        return pre


def _env(monkeypatch, items):
    from api.routers import vendors as vd
    from database.repositories.product_repository import StockRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    grn = {
        "grn_id": "GRN-1",
        "grn_number": "GRN-001",
        "store_id": "S-A",
        "po_id": None,
        "status": "PENDING",
        "items": items,
    }
    grn_repo = _FakeGRNRepo(grn)
    monkeypatch.setattr(vd, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: None)
    monkeypatch.setattr(vd, "get_product_repository", lambda: None)
    monkeypatch.setattr(vd, "_get_db", lambda: db)
    monkeypatch.setattr(
        vd, "_cumulative_received_by_product", lambda repo, po_id: {}, raising=False
    )
    return vd, stock_repo


def test_cl_line_stamps_batch_and_expiry(monkeypatch):
    vd, stock_repo = _env(
        monkeypatch,
        [{"product_id": "CL-1", "accepted_qty": 3,
          "batch_code": "LOT42", "expiry_date": "2027-03-31"}],
    )
    out = _run(vd.accept_grn("GRN-1", _MANAGER))
    assert out["units_added"] == 3
    units = stock_repo.find_many({"product_id": "CL-1"})
    assert len(units) == 3
    assert all(u.get("batch_code") == "LOT42" for u in units)
    assert all(u.get("expiry_date") == "2027-03-31" for u in units)


def test_lot_number_alias_used_when_no_batch_code(monkeypatch):
    vd, stock_repo = _env(
        monkeypatch,
        [{"product_id": "CL-2", "accepted_qty": 1,
          "lot_number": "LOTX", "expiry_date": "2027-06-30"}],
    )
    _run(vd.accept_grn("GRN-1", _MANAGER))
    u = stock_repo.find_many({"product_id": "CL-2"})[0]
    assert u.get("batch_code") == "LOTX"
    assert u.get("expiry_date") == "2027-06-30"


def test_frame_line_without_batch_is_unchanged(monkeypatch):
    vd, stock_repo = _env(
        monkeypatch, [{"product_id": "FR-1", "accepted_qty": 2}]
    )
    out = _run(vd.accept_grn("GRN-1", _MANAGER))
    assert out["units_added"] == 2
    units = stock_repo.find_many({"product_id": "FR-1"})
    assert len(units) == 2
    # No batch/expiry stamped (the fields are simply absent / None).
    assert all(not u.get("batch_code") for u in units)
    assert all(not u.get("expiry_date") for u in units)
