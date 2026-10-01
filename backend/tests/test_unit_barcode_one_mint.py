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
    # 'Ab12cd34ef': serial capture stored any caller-supplied label verbatim
    # before the one minter, so a legacy code can be mixed case.
    codes = ("BV--91FA3858", "2000000000015", "BV0000000042", "Ab12cd34ef")
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
        for typed in (code, code.lower(), code.upper(), code[:1] + code[1:].lower()):
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


# --------------------------------------------------------------------------- #
# INV-12 trace: the code as typed finds the unit; its history is found by the
# unit's own code and its sale by the order the till stamped on it.
# --------------------------------------------------------------------------- #
def _trace(monkeypatch, db, typed):
    from api.routers.inventory import barcode_trace as bt

    monkeypatch.setattr(bt, "_get_db", lambda: db)
    return _run(bt.barcode_lifecycle_trace(typed, _MGR))


def test_trace_finds_a_unit_and_its_history_typed_in_lower_case(monkeypatch):
    """The lookup found the unit in any case, but the sale and return queries
    still matched the raw typed string, so 'bv0000000042' read as never sold."""
    import mongomock

    db = mongomock.MongoClient().db
    db.stock_units.insert_one(
        {"stock_id": "SU-42", "barcode": "BV0000000042", "product_id": "P1",
         "store_id": STORE, "status": "AVAILABLE"}
    )
    db.orders.insert_one(
        {"order_number": "ORD-1", "created_at": "2026-09-20",
         "items": [{"product_id": "P1", "barcode": "BV0000000042"}]}
    )
    db.returns.insert_one(
        {"return_number": "RET-1", "created_at": "2026-09-21",
         "items": [{"barcode": "BV0000000042"}]}
    )
    for typed in ("bv0000000042", "Bv0000000042"):
        out = _trace(monkeypatch, db, typed)
        assert (out["stock_unit"] or {}).get("barcode") == "BV0000000042", typed
        assert [s["order_number"] for s in out["sales"]] == ["ORD-1"], typed
        assert [r["return_number"] for r in out["returns"]] == ["RET-1"], typed


def test_trace_shows_the_till_sale_of_a_scanned_unit(monkeypatch):
    """The till never copies the unit code onto the order line: it stamps the
    order on the UNIT (mark_sold -> stock_units.order_id). The trace must follow
    that link, or every counter sale reads as never sold."""
    import mongomock

    db = mongomock.MongoClient().db
    db.stock_units.insert_one(
        {"stock_id": "SU-7", "barcode": "BV0000000007", "product_id": "P1",
         "store_id": STORE, "status": "SOLD", "order_id": "ORD-ID-7"}
    )
    db.orders.insert_one(
        {"order_id": "ORD-ID-7", "order_number": "BV/26-27/0007",
         "created_at": "2026-09-22", "items": [{"product_id": "P1", "stock_id": "SU-7"}]}
    )
    out = _trace(monkeypatch, db, "BV0000000007")
    assert [s["order_number"] for s in out["sales"]] == ["BV/26-27/0007"]
    assert out["sales"][0]["matched_lines"] == [{"product_id": "P1", "stock_id": "SU-7"}]


def test_trace_shows_a_transfer_that_moved_the_unit(monkeypatch):
    """A transfer line records the moved units by stock_id, not by code."""
    import mongomock

    db = mongomock.MongoClient().db
    db.stock_units.insert_one(
        {"stock_id": "SU-9", "barcode": "BV0000000009", "product_id": "P1",
         "store_id": STORE, "status": "AVAILABLE"}
    )
    db.stock_transfers.insert_one(
        {"id": "TR-1", "transfer_number": "TR-001", "created_at": "2026-09-23",
         "items": [{"product_id": "P1", "shipped_stock_ids": ["SU-9"],
                    "received_stock_ids": ["SU-9"]}]}
    )
    out = _trace(monkeypatch, db, "bv0000000009")
    assert [t["transfer_number"] for t in out["transfers"]] == ["TR-001"]


def test_stock_ledger_rows_carry_their_on_hand_unit_codes():
    """Inventory > Stock and New transfer search the ledger rows, which carried
    only one sample unit's code: typing any other unit's code (old or new
    format) found nothing. A row carries the code of every unit on hand at
    THIS shop -- not a sold unit, not another shop's."""
    import mongomock
    from api.routers.inventory.stock import _build_store_ledger
    from database.repositories.product_repository import StockRepository

    db = mongomock.MongoClient().db
    for code, status, store in (
        ("BV0000000042", "AVAILABLE", STORE),
        ("BV--91FA3858", "available", STORE),
        ("BV0000000043", "SOLD", STORE),
        ("BV0000000044", "AVAILABLE", "BV-BOK-01"),
    ):
        db.stock_units.insert_one(
            {"barcode": code, "status": status, "store_id": store, "product_id": "P1"}
        )

    class _Products:
        def find_many(self, flt, limit=0):
            return [{"product_id": "P1", "sku": "S1", "name": "Frame", "is_active": True}]

        def find_by_id(self, pid):
            return None

    (row,) = _build_store_ledger(StockRepository(db.stock_units), _Products(), STORE)
    assert row["stock"] == 2
    assert sorted(row["unit_barcodes"]) == ["BV--91FA3858", "BV0000000042"]


@pytest.mark.parametrize(
    "typed",
    [
        "BV000000004",  # one digit short: the till still treats it as a barcode
        "0000000042",  # the shop prefix left off
        "BV00000000421",  # one digit too many
        ".*",  # regex characters are literal text, not a pattern
        "BV00000000.2",
        "BV0+42",
    ],
)
def test_a_unit_code_matches_whole_never_a_part_or_a_pattern(typed):
    """The lookup ignores letter case only. A partial code, or one carrying
    regex characters, must find NO unit (404 at the till), never some other
    unit that happens to contain it."""
    import mongomock
    from database.repositories.product_repository import StockRepository

    coll = mongomock.MongoClient().db.stock_units
    for n in range(40, 50):
        coll.insert_one({"stock_id": f"SU-{n}", "barcode": f"BV00000000{n}"})
    repo = StockRepository(coll)
    assert repo.find_by_barcode("bv0000000042")["stock_id"] == "SU-42"
    assert repo.find_by_barcode(typed) is None, typed
