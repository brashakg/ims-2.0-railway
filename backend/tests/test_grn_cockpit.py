"""Purchase P1 / S2 -- vendor-first goods-receipt cockpit endpoint.

GET /vendors/goods-receipt/cockpit assembles three worklists from existing
data: open POs (lines with received < ordered), the per-product residual
(pending-not-received), and ACTIVE cataloged items not on an open PO. Driven
directly with monkeypatched repos -- no Mongo, no HTTP.
"""
from __future__ import annotations

import os
import sys
import asyncio

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import goods_receipt_cockpit  # noqa: E402


class _FakePORepo:
    def __init__(self, pos):
        self.pos = pos

    def find_many(self, flt, limit=None, **k):
        statuses = flt.get("status", {}).get("$in", [])
        return [
            p for p in self.pos
            if p.get("vendor_id") == flt.get("vendor_id") and p.get("status") in statuses
        ]


class _FakeProdRepo:
    def __init__(self, prods):
        self.prods = prods

    def find_many(self, flt, limit=None, **k):
        return list(self.prods)


def _run(vendor_id="V1"):
    return asyncio.run(
        goods_receipt_cockpit(
            vendor_id=vendor_id, store_id=None, current_user={"roles": ["ADMIN"]}
        )
    )


def test_cockpit_open_pos_residuals_and_pending_cataloged(monkeypatch):
    pos = [
        {
            "po_id": "PO1", "po_number": "PO-1", "vendor_id": "V1", "status": "SENT",
            "items": [
                {"product_id": "P1", "product_name": "Frame A", "sku": "FA", "ordered_qty": 5, "received_qty": 2},
                {"product_id": "P2", "product_name": "Frame B", "sku": "FB", "ordered_qty": 3, "received_qty": 3},
            ],
        },
        {
            "po_id": "PO2", "po_number": "PO-2", "vendor_id": "V1", "status": "PARTIALLY_RECEIVED",
            "items": [
                {"product_id": "P1", "product_name": "Frame A", "sku": "FA", "ordered_qty": 4, "received_qty": 4},
            ],
        },
    ]
    prods = [
        {"product_id": "P1", "product_name": "Frame A", "sku": "FA", "is_active": True},
        {"product_id": "P9", "product_name": "New SG", "sku": "NS", "category": "SUNGLASS", "is_active": True},
    ]
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _FakePORepo(pos))
    monkeypatch.setattr(v, "get_product_repository", lambda: _FakeProdRepo(prods))
    # Isolate cockpit assembly from the catalog-done rule.
    monkeypatch.setattr(v._pm, "compute_catalog_status", lambda p: ("ACTIVE", []))

    res = _run()

    # open_pos: only PO1 has an open line (P1: 5 ordered, 2 received). P2's line
    # is fully received (no open line); PO2 fully received -> excluded entirely.
    assert len(res["open_pos"]) == 1
    assert res["open_pos"][0]["po_id"] == "PO1"
    lines = res["open_pos"][0]["lines"]
    assert len(lines) == 1 and lines[0]["product_id"] == "P1" and lines[0]["pending_qty"] == 3

    # pending_not_received: P1 residual = 3 (PO1 only; PO2's P1 line fully received).
    pnr = {r["product_id"]: r for r in res["pending_not_received"]}
    assert pnr["P1"]["pending_qty"] == 3
    assert "P2" not in pnr  # fully received

    # pending_cataloged: P9 (ACTIVE, not ordered) in; P1 (ordered) excluded.
    cat_ids = {c["product_id"] for c in res["pending_cataloged"]}
    assert "P9" in cat_ids and "P1" not in cat_ids


def test_cockpit_falls_back_to_header_received_for_pre_s1_pos(monkeypatch):
    # A pre-S1 PO line has no per-line received_qty; the header
    # received_qty_by_product must drive the residual instead.
    pos = [
        {
            "po_id": "PO1", "po_number": "PO-1", "vendor_id": "V1", "status": "SENT",
            "received_qty_by_product": {"P1": 1},
            "items": [{"product_id": "P1", "product_name": "Frame", "sku": "F", "quantity": 4}],
        }
    ]
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _FakePORepo(pos))
    monkeypatch.setattr(v, "get_product_repository", lambda: None)
    res = _run()
    assert res["open_pos"][0]["lines"][0]["pending_qty"] == 3  # 4 ordered - 1 (header)
