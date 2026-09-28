"""Owner ruling 2026-09-28 -- a unit barcode is letters and digits only (F8/F99).

Before: the GRN door minted ``store_id[:3] + "-" + uuid8`` -> 'BV--91FA3858'. The
till treats typed text as a barcode only when it matches /^[A-Z0-9]{8,}$/
(frontend/src/components/pos/BarcodeScanner.tsx), so a hand-typed unit code fell
through to product search ('No product matches'). And TWO schemes minted unit
codes: GRN (store + uuid) vs /stock/add + opening stock (a GS1 20-prefix EAN-13).

Now ONE minter, ``services.barcode.mint_unit_barcode``: the store's two-letter
prefix + the chain-wide atomic counter ('BV0000000042'). Every door that creates
a stock_units row calls it. Existing units keep their old codes, and every unit
lookup (services.barcode.unit_barcode_match) takes the code as typed or
upper-cased, so both formats resolve in any letter case.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_e3_item_event import CasDB, _run  # noqa: E402

# The till's own test (BarcodeScanner.tsx isBarcodeFormat), copied verbatim.
TILL = re.compile(r"^[A-Z0-9]{8,}$", re.I)
# The ruled format for a unit minted at a Better Vision shop.
NEW = re.compile(r"^BV\d{10}$")

STORE = "BV-DHN-02"
_MGR = {
    "user_id": "mgr-1",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}


class _GRNRepo:
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


# --------------------------------------------------------------------------- #
# One driver per door that creates stock_units rows. Each runs the REAL route
# function against `db` and returns the barcodes it minted, in order.
# --------------------------------------------------------------------------- #
def _grn_door(monkeypatch, db, qty=3):
    from api.routers import vendors as vd
    from database.repositories.product_repository import StockRepository

    stock_repo = StockRepository(db.get_collection("stock_units"))
    grn = {
        "grn_id": "GRN-1",
        "grn_number": "GRN-001",
        "store_id": STORE,
        "po_id": None,
        "status": "PENDING",
        "items": [{"product_id": "FR-1", "accepted_qty": qty}],
    }
    grn_repo = _GRNRepo(grn)
    monkeypatch.setattr(vd, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: None)
    monkeypatch.setattr(vd, "get_product_repository", lambda: None)
    monkeypatch.setattr(vd, "_get_db", lambda: db)
    monkeypatch.setattr(
        vd, "_cumulative_received_by_product", lambda repo, po_id: {}, raising=False
    )
    out = _run(vd.accept_grn("GRN-1", _MGR))
    assert out["units_added"] == qty
    return [u["barcode"] for u in stock_repo.find_many({"product_id": "FR-1"})]


def _stock_add_door(monkeypatch, db, qty=2, user=_MGR):
    from api.routers import inventory as inv

    class _Stock:
        def create(self, doc):
            return doc

    class _Prod:
        def find_by_id(self, pid):
            return {"product_id": pid}

    monkeypatch.setattr(inv, "get_stock_repository", lambda: _Stock())
    monkeypatch.setattr(inv, "get_product_repository", lambda: _Prod())
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    out = _run(inv.add_stock(inv.StockAddRequest(product_id="P1", quantity=qty), user))
    return list(out["barcodes"])


def _opening_stock_door(monkeypatch, db, qty=2, user=_MGR):
    from api.routers import inventory as inv
    from tests.test_opening_stock_import import FakeProductRepo, FakeStockRepo

    prod = FakeProductRepo({"P1": {"product_id": "P1", "sku": "S1"}}, {})
    stock = FakeStockRepo({})
    monkeypatch.setattr(inv, "get_product_repository", lambda: prod)
    monkeypatch.setattr(inv, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    monkeypatch.setattr(inv, "get_audit_repository", lambda: None)
    body = inv.OpeningStockImport(rows=[{"product_id": "P1", "quantity": qty}])
    _run(inv.opening_stock_commit(body, user))
    return [d["barcode"] for d in stock.created]


def _serial_capture_door(monkeypatch, db, **extra):
    from api.routers import serial_tracking as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda s, u: s)
    monkeypatch.setattr(r, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(r, "_sync_online_stock", lambda *a, **k: None)
    unit = asyncio.run(
        r.capture(
            r.CaptureBody(serial="SN-1", product_id="P1", store_id=STORE, **extra), _MGR
        )
    )
    return [unit["barcode"]]


_DOORS = {
    "grn_accept": _grn_door,
    "stock_add": _stock_add_door,
    "opening_stock": _opening_stock_door,
    "serial_capture": _serial_capture_door,
}
# The fifth door, return restock, is proven the same way in
# tests/test_returns_restock.py::test_return_mint_goes_through_the_one_minter.


@pytest.mark.parametrize("door", sorted(_DOORS))
def test_every_door_mints_letters_and_digits_only(monkeypatch, door):
    """The door behind F8: a unit's code must be one the till accepts typed."""
    codes = _DOORS[door](monkeypatch, CasDB())
    assert codes and len(set(codes)) == len(codes), codes
    assert all(TILL.match(c) for c in codes), codes
    assert all(NEW.match(c) for c in codes), codes


@pytest.mark.parametrize("door", sorted(_DOORS))
def test_every_door_mints_through_the_one_minter(monkeypatch, door):
    """ONE minter: a door that builds its own code (a uuid, a second counter)
    passes a shape check but breaks chain-wide uniqueness. So swap the minter
    for a spy: every unit the door creates must carry the spy's code, and the
    door must hand the minter its real database (the counter path, not the
    no-database random fallback) and the shop it is minting for."""
    from api.services import barcode as barcode_svc

    db = CasDB()
    calls = []

    def spy(mint_db, store_id):
        calls.append((mint_db, store_id))
        return f"SPY{len(calls):07d}"

    monkeypatch.setattr(barcode_svc, "mint_unit_barcode", spy)
    codes = _DOORS[door](monkeypatch, db)
    assert codes == [f"SPY{i + 1:07d}" for i in range(len(codes))], codes
    assert calls and all(c == (db, STORE) for c in calls), calls


def test_codes_are_unique_across_doors_on_one_database(monkeypatch):
    """Chain-wide uniqueness proven THROUGH the doors, not just the minter: a
    receipt, a manual add, an opening-stock import and a serial capture on the
    same database never mint the same code."""
    db = CasDB()
    codes = [c for door in sorted(_DOORS) for c in _DOORS[door](monkeypatch, db)]
    assert len(codes) == 3 + 2 + 2 + 1
    assert len(set(codes)) == len(codes), codes


def test_serial_capture_ignores_a_label_the_caller_hands_in(monkeypatch):
    """A caller-supplied barcode ('bv-001 x': hyphen, space, lower case) used to
    be stored as the unit's code, bypassing the minter and its format."""
    (code,) = _serial_capture_door(monkeypatch, CasDB(), barcode="bv-001 x")
    assert NEW.match(code), code


@pytest.mark.parametrize("door", ["stock_add", "opening_stock"])
def test_no_stock_is_minted_without_a_shop(monkeypatch, door):
    """With no shop picked the unit would belong to no shop and its code would
    have no prefix ('0000000002'). Refuse instead."""
    from fastapi import HTTPException

    no_shop = {**_MGR, "active_store_id": None}
    with pytest.raises(HTTPException) as ei:
        _DOORS[door](monkeypatch, CasDB(), user=no_shop)
    assert ei.value.status_code == 400
    assert "shop" in ei.value.detail.lower()


def test_till_lookup_still_finds_old_and_new_codes(monkeypatch):
    """Existing units keep their codes: an old hyphenated code, an old EAN-13
    and a new code all resolve, typed in any letter case."""
    from api.routers import inventory as inv
    from database.repositories.product_repository import StockRepository

    db = CasDB()
    coll = db.get_collection("stock_units")
    codes = ("BV--91FA3858", "2000000000015", "BV0000000042")
    for code in codes:
        coll.insert_one(
            {
                "stock_id": f"S-{code}",
                "barcode": code,
                "store_id": STORE,
                "product_id": "P1",
                "status": "AVAILABLE",
            }
        )
    monkeypatch.setattr(inv, "get_stock_repository", lambda: StockRepository(coll))
    monkeypatch.setattr(inv, "get_product_repository", lambda: None)

    for code in codes:
        # As printed, all lower case (typed), and first letter only capitalised
        # (a tablet keyboard): the till finds the same unit every time.
        for typed in (code, code.lower(), code[:1] + code[1:].lower()):
            hit = _run(inv.get_stock_by_barcode_short(typed, None, _MGR))
            assert hit["barcode"] == code, typed


def test_stock_count_scan_finds_a_unit_typed_in_lower_case(monkeypatch):
    """The stock count's scan door uses the same unit lookup as the till."""
    import mongomock
    from api.routers import inventory as inv

    db = mongomock.MongoClient().db
    db.stock_units.insert_one(
        {"barcode": "BV0000000001", "product_id": "P1", "store_id": STORE,
         "status": "AVAILABLE"}
    )
    db.products.insert_one({"_id": "P1", "sku": "S1", "brand": "B", "model": "M"})
    monkeypatch.setattr(inv, "_get_db", lambda: db)

    out = _run(
        inv.scan_barcode_for_count(
            inv.BarcodeScanRequest(barcode="bv0000000001", physical_count=1), None, _MGR
        )
    )
    assert out["product_id"] == "P1"
    assert out["system_count"] == 1
