"""Owner ruling 2026-09-28 -- a unit barcode is letters and digits only (F8/F99).

Before: the GRN door minted ``store_id[:3] + "-" + uuid8`` -> 'BV--91FA3858'. The
till treats typed text as a barcode only when it matches /^[A-Z0-9]{8,}$/
(frontend/src/components/pos/BarcodeScanner.tsx), so a hand-typed unit code fell
through to product search ('No product matches'). And TWO schemes minted unit
codes: GRN (store + uuid) vs /stock/add + opening stock (a GS1 20-prefix EAN-13).

Now ONE minter, ``services.barcode.mint_unit_barcode``: the store's two-letter
prefix + the chain-wide atomic counter ('BV0000000042'). Every door that creates
a stock_units row calls it. Existing units keep their old codes and every lookup
is an exact match, so both formats still resolve.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

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


def test_grn_receipt_mints_letters_and_digits_only(monkeypatch):
    """The door behind F8: receiving a box must mint codes the till accepts typed."""
    from api.routers import vendors as vd
    from database.repositories.product_repository import StockRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    grn = {
        "grn_id": "GRN-1",
        "grn_number": "GRN-001",
        "store_id": STORE,
        "po_id": None,
        "status": "PENDING",
        "items": [{"product_id": "FR-1", "accepted_qty": 3}],
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

    assert out["units_added"] == 3
    codes = [u["barcode"] for u in stock_repo.find_many({"product_id": "FR-1"})]
    assert len(set(codes)) == 3
    assert all(TILL.match(c) for c in codes), codes
    assert all(NEW.match(c) for c in codes), codes


def test_stock_add_mints_the_same_format_as_a_receipt(monkeypatch):
    """One rule: /stock/add used to mint a 13-digit GS1 20-prefix EAN-13 instead."""
    from api.routers import inventory as inv

    db = CasDB()
    created = []

    class _Stock:
        def create(self, doc):
            created.append(doc)
            return doc

    class _Prod:
        def find_by_id(self, pid):
            return {"product_id": pid}

    monkeypatch.setattr(inv, "get_stock_repository", lambda: _Stock())
    monkeypatch.setattr(inv, "get_product_repository", lambda: _Prod())
    monkeypatch.setattr(inv, "_get_db", lambda: db)

    out = _run(inv.add_stock(inv.StockAddRequest(product_id="P1", quantity=2), _MGR))

    assert len(out["barcodes"]) == 2
    assert all(NEW.match(c) for c in out["barcodes"]), out["barcodes"]


def test_opening_stock_mints_the_same_format_as_a_receipt(monkeypatch):
    from api.routers import inventory as inv
    from tests.test_opening_stock_import import FakeProductRepo, FakeStockRepo

    db = CasDB()
    prod = FakeProductRepo({"P1": {"product_id": "P1", "sku": "S1"}}, {})
    stock = FakeStockRepo({})
    monkeypatch.setattr(inv, "get_product_repository", lambda: prod)
    monkeypatch.setattr(inv, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    monkeypatch.setattr(inv, "get_audit_repository", lambda: None)

    body = inv.OpeningStockImport(rows=[{"product_id": "P1", "quantity": 2}])
    _run(inv.opening_stock_commit(body, _MGR))

    codes = [d["barcode"] for d in stock.created]
    assert len(codes) == 2
    assert all(NEW.match(c) for c in codes), codes


def test_serial_capture_without_a_label_gets_a_minted_barcode(monkeypatch):
    from api.routers import serial_tracking as r

    db = CasDB()
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda s, u: s)
    monkeypatch.setattr(r, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(r, "_sync_online_stock", lambda *a, **k: None)

    unit = asyncio.run(
        r.capture(r.CaptureBody(serial="SN-1", product_id="P1", store_id=STORE), _MGR)
    )
    assert NEW.match(unit["barcode"] or ""), unit.get("barcode")


def test_till_lookup_still_finds_old_and_new_codes(monkeypatch):
    """Existing units keep their codes: the lookup is an exact match, so an old
    hyphenated code, an old EAN-13 and a new code all resolve."""
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
        hit = _run(inv.get_stock_by_barcode_short(code, None, _MGR))
        assert hit["barcode"] == code
