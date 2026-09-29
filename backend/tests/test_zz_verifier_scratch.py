"""VERIFIER SCRATCH -- deleted after the run. Not part of the branch."""
import sys
import mongomock
import pytest
from fastapi import HTTPException


def _stock_mod():
    import api.routers.orders.stock  # noqa: F401
    return sys.modules["api.routers.orders.stock"]


def test_rule_change_moves_tile_and_guard_together(client, staff_headers, monkeypatch):
    """Change THE rule (sellable_units) -> the tile number moves AND the guard
    blocks at the moved number."""
    from api.routers import inventory as inv
    from api.routers import orders as om

    class R:
        def count(self, q):
            return 10

        def find_available(self, p, s):
            return 10

        def count_expired(self, p, s):
            return 0

    repo = R()
    monkeypatch.setattr(inv, "get_stock_repository", lambda: repo)
    monkeypatch.setattr(om, "get_stock_repository", lambda: repo)
    sm = _stock_mod()
    monkeypatch.setattr(sm, "sellable_units", lambda r, p, s: 3)
    r = client.get(
        "/api/v1/inventory/sellable",
        params={"product_ids": "FR-X", "item_types": "FRAME"},
        headers=staff_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["sellable"] == {"FR-X": 3}
    line = lambda q: [{"product_id": "FR-X", "item_type": "FRAME", "quantity": q}]
    sm._assert_serialized_stock_available(line(3), "BV-TEST-01")
    with pytest.raises(HTTPException):
        sm._assert_serialized_stock_available(line(4), "BV-TEST-01")


def test_real_repo_excludes_reserved_transfer_quarantine_sold_expired_other_shop(
    client, staff_headers, monkeypatch
):
    from api.routers import inventory as inv
    from api.routers import orders as om
    from database.repositories.product_repository import StockRepository

    coll = mongomock.MongoClient().db.stock_units
    S = "BV-TEST-01"
    rows = [
        ("u1", S, "AVAILABLE", None),
        ("u2", S, "AVAILABLE", "2099-01-01"),
        ("u3", S, "RESERVED", None),
        ("u4", S, "TRANSFERRED", None),
        ("u5", S, "QUARANTINED", None),
        ("u6", S, "SOLD", None),
        ("u7", S, "AVAILABLE", "2000-01-01"),  # expired
        ("u8", "BV-OTHER-02", "AVAILABLE", None),
        ("u9", "BV-OTHER-02", "AVAILABLE", None),
    ]
    for sid, st, status, exp in rows:
        d = {"stock_id": sid, "product_id": "FR-1", "store_id": st, "status": status}
        if exp:
            d["expiry_date"] = exp
        coll.insert_one(d)
    # product only in other shop
    coll.insert_one({"stock_id": "x1", "product_id": "FR-2", "store_id": "BV-OTHER-02", "status": "AVAILABLE"})
    repo = StockRepository(coll)
    monkeypatch.setattr(inv, "get_stock_repository", lambda: repo)
    monkeypatch.setattr(om, "get_stock_repository", lambda: repo)
    r = client.get(
        "/api/v1/inventory/sellable",
        params={"product_ids": "FR-1,FR-2", "item_types": "FRAME,FRAME"},
        headers=staff_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["sellable"] == {"FR-1": 2, "FR-2": None}
    sm = _stock_mod()
    line = lambda q: [{"product_id": "FR-1", "item_type": "FRAME", "quantity": q}]
    sm._assert_serialized_stock_available(line(2), S)
    with pytest.raises(HTTPException):
        sm._assert_serialized_stock_available(line(3), S)
