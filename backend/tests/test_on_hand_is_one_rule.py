"""
IMS 2.0 -- "IS THIS UNIT ON HAND?" IS ONE RULE, NOT FOURTEEN
============================================================
Round-2 finding MF1. The stock count's coverage clause hand-appended the
case-sensitive literal "RESERVED" onto an allowlist that carried BOTH cases of
every other on-hand word, so a unit stored as lowercase `reserved` -- which
`item_events.canonical_state` upper-cases and calls RESERVED -- was NOT in the
count's expected set, while `get_non_moving_stock` (which listed "reserved"
too) called the same unit on hand at the same instant. Skipping that product
therefore cost the counter nothing and the day-end locked at
`coverage 100%, full_count true, within_tolerance true` over a half-walked
shelf. Same unit, same second, two answers.

This file is the DIFFERENTIAL PROBE that is supposed to make that impossible:
every reader that decides "is this unit here?" is fed the SAME unit in EVERY
storage shape, and they must all answer the same. The expected answers below
are written out BY HAND, not computed from the code under test -- a fixture
that derived them from `is_on_hand` could not fail.

Two questions, one permitted difference (RESERVED):
  * SELLABLE  -- may POS / valuation / Shopify offer this unit? A reserved unit
    is committed to somebody's order, so no.
  * PHYSICAL  -- will the counter walking the shelf find it? A reserved unit is
    standing right there, so yes.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_on_hand_is_one_rule.py -q

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict, List, Tuple

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "ST-ONEHAND"


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock only as a dev-box fallback
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_onhand_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return None
        client = mongomock.MongoClient()
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
        client.close()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        # Some services reach for db["stock_units"] rather than
        # get_collection(); a proxy answering only one of the two would hand
        # those readers an empty collection and silence the probe.
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


# ============================================================================
# The corpus: every shape a stock_units row is stored in, and -- BY HAND --
# whether that unit is sellable and whether it is physically on the shelf.
# ============================================================================
# (label, stored shape, sellable, physically here)
_SHAPES: List[Tuple[str, Dict[str, Any], bool, bool]] = [
    ("AVAILABLE", {"status": "AVAILABLE"}, True, True),
    ("available (legacy lowercase)", {"status": "available"}, True, True),
    ("Available (legacy title case)", {"status": "Available"}, True, True),
    ("padded ' available ' import", {"status": " available "}, True, True),
    ("IN_STOCK (legacy token)", {"status": "IN_STOCK"}, True, True),
    ("in_stock (legacy lowercase)", {"status": "in_stock"}, True, True),
    ("no status field at all", {}, True, True),
    ("status stored as null", {"status": None}, True, True),
    ("RESERVED", {"status": "RESERVED"}, False, True),
    ("reserved (MF1: lowercase)", {"status": "reserved"}, False, True),
    ("Reserved (title case)", {"status": "Reserved"}, False, True),
    ("SOLD", {"status": "SOLD"}, False, False),
    ("sold (lowercase)", {"status": "sold"}, False, False),
    ("QUARANTINED", {"status": "QUARANTINED"}, False, False),
    ("quarantined (lowercase)", {"status": "quarantined"}, False, False),
    ("TRANSFERRED", {"status": "TRANSFERRED"}, False, False),
    ("empty string status", {"status": ""}, False, False),
    ("unknown junk status", {"status": "FOO"}, False, False),
]

_IDS = [s[0] for s in _SHAPES]


def _seed(mongo_db, shape: Dict[str, Any]) -> Tuple[str, str]:
    """One product with one unit stored exactly as `shape` says -- including,
    deliberately, with no `status` key at all. Returns (product_id, barcode)."""
    pid = f"PRD-{uuid.uuid4().hex[:10]}"
    barcode = f"BC-{uuid.uuid4().hex[:10]}"
    mongo_db["products"].insert_one(
        {
            "_id": pid,
            "product_id": pid,
            "sku": f"SKU-{pid[-8:]}",
            "brand": "Ray-Ban",
            "model": "RB3025",
            # CONTACT_LENS so the CL/FEFO reader, which only looks at contact
            # lenses, is probed by the same corpus as everything else.
            "category": "CONTACT_LENS",
            "mrp": 5000.0,
            "cost_price": 2000.0,
            "is_active": True,
        }
    )
    doc: Dict[str, Any] = {
        "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
        "product_id": pid,
        "store_id": STORE,
        "barcode": barcode,
        "quantity": 1,
        "location_code": "DEFAULT",
    }
    doc.update(shape)
    mongo_db["stock_units"].insert_one(doc)
    return pid, barcode


@pytest.fixture
def http(mongo_db, monkeypatch):
    """A TestClient over the REAL inventory router bound to this engine. Only
    the DB handle, the repositories and the store-access check are redirected;
    every on-hand decision under test runs for real."""
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        inv_mod,
        "get_stock_repository",
        lambda: StockRepository(mongo_db["stock_units"]),
    )
    monkeypatch.setattr(
        inv_mod,
        "get_product_repository",
        lambda: ProductRepository(mongo_db["products"]),
    )
    monkeypatch.setattr(
        inv_mod, "validate_store_access", lambda sid, u: sid or u.get("active_store_id")
    )

    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")

    async def _user():
        return {
            "user_id": "u-onhand",
            "username": "manager",
            "roles": ["ADMIN"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


# ============================================================================
# The readers. Each answers HOW MANY units of `pid` it can see at STORE.
# ============================================================================


def _ledger_row_for(mongo_db, pid) -> Dict[str, Any]:
    from api.routers.inventory import _build_store_ledger
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    rows = _build_store_ledger(
        StockRepository(mongo_db["stock_units"]),
        ProductRepository(mongo_db["products"]),
        STORE,
    )
    for row in rows:
        if row.get("product_id") == pid:
            return row
    return {}


def _physical_readers(mongo_db, http, pid, barcode) -> Dict[str, int]:
    from api.routers.inventory import _on_hand_now, _scoped_product_ids

    db = _DBProxy(mongo_db)
    out: Dict[str, int] = {}

    # 1. the count's COVERAGE set -- what the session must walk
    scope = _scoped_product_ids(db, STORE) or []
    out["count coverage (_scoped_product_ids)"] = 1 if pid in scope else 0

    # 2. the count's VARIANCE -- what the system says is on that shelf
    live, _prints = _on_hand_now(db, STORE, [pid])
    out["count variance (_on_hand_now)"] = int(live.get(pid, 0) or 0)

    # 3. the scan screen the counter types into (with no session it computes
    #    the system count itself)
    resp = http.post(
        "/inventory/stock-count-scan",
        json={"barcode": barcode, "physical_count": 0},
        params={"store_id": STORE},
    )
    assert resp.status_code == 200, resp.text
    out["POST /stock-count-scan system_count"] = int(resp.json()["system_count"])

    # 4. GET /inventory/non-moving -- the reader that disagreed with the count
    resp = http.get("/inventory/non-moving", params={"store_id": STORE, "days": 90})
    assert resp.status_code == 200, resp.text
    rows = {p["product_id"]: p for p in resp.json()["products"]}
    out["GET /non-moving current_stock"] = int(
        rows.get(pid, {}).get("current_stock", 0)
    )

    # 5. GET /inventory/aging
    resp = http.get("/inventory/aging", params={"store_id": STORE})
    assert resp.status_code == 200, resp.text
    rows = {p.get("product_id") or p.get("id"): p for p in resp.json()["products"]}
    out["GET /aging quantity"] = int(rows.get(pid, {}).get("quantity", 0))

    # 6. GET /inventory/overstock-analysis (no sales -> any on-hand unit is
    #    over-stocked, so the row appears with its on-hand quantity)
    resp = http.get("/inventory/overstock-analysis", params={"store_id": STORE})
    assert resp.status_code == 200, resp.text
    rows = {p["product_id"]: p for p in resp.json()["items"]}
    out["GET /overstock-analysis current_stock"] = int(
        rows.get(pid, {}).get("current_stock", 0)
    )

    # 7. the contact-lens / FEFO drawer listing (one row per unit)
    from api.routers.inventory import _load_cl_stock_rows

    cl_rows = _load_cl_stock_rows(db, STORE)
    out["_load_cl_stock_rows (CL drawer)"] = sum(
        1 for r in cl_rows if r.get("product_id") == pid
    )

    # 8. the Stock Ledger buckets in Python: on hand + reserved is the whole
    #    physical shelf
    row = _ledger_row_for(mongo_db, pid)
    out["stock ledger (quantity + reserved)"] = int(row.get("quantity", 0)) + int(
        row.get("reserved_quantity", 0)
    )
    return out


def _sellable_readers(mongo_db, pid, sku) -> Dict[str, int]:
    from api.routers.inventory import _on_hand_by_product
    from api.services import collection_insights, inventory_balancing
    from api.services import online_stock_writeback, online_sync_health, shopify_ingest

    db = _DBProxy(mongo_db)
    out: Dict[str, int] = {}
    out["inventory._on_hand_by_product"] = int(
        _on_hand_by_product(db, [pid], STORE).get(pid, 0) or 0
    )
    out["collection_insights._stock_rollup"] = int(
        collection_insights._stock_rollup(db, [pid], STORE).get(pid, 0) or 0
    )
    out["online_sync_health._on_hand_by_product"] = int(
        online_sync_health._on_hand_by_product(db, [pid], STORE).get(pid, 0) or 0
    )
    out["inventory_balancing._on_hand_by_product_store"] = int(
        inventory_balancing._on_hand_by_product_store(db, [pid]).get((pid, STORE), 0)
        or 0
    )
    out["online_stock_writeback._on_hand_for_skus"] = int(
        online_stock_writeback._on_hand_for_skus(db, [sku], STORE).get(sku, 0) or 0
    )
    # a fulfilment candidate is a store that HAS a sellable unit
    stores = shopify_ingest._available_stores_for_product(db, pid)
    out["shopify_ingest._available_stores_for_product"] = 1 if STORE in stores else 0
    # the ledger's on-hand column is the sellable half of the same bucketing
    out["stock ledger (quantity column)"] = int(
        _ledger_row_for(mongo_db, pid).get("quantity", 0)
    )
    return out


def _disagreements(answers: Dict[str, int], expected: int) -> List[str]:
    return [
        f"      {name}: {got}  (expected {expected})"
        for name, got in sorted(answers.items())
        if got != expected
    ]


# ============================================================================
# The probe
# ============================================================================


@pytest.mark.parametrize("label,shape,sellable,physical", _SHAPES, ids=_IDS)
def test_every_reader_agrees_whether_this_unit_is_physically_on_the_shelf(
    mongo_db, http, label, shape, sellable, physical
):
    """One unit, every PHYSICAL reader, one answer.

    MF1 lived exactly here: `reserved` was on hand to /non-moving and gone to
    the count, and that gap is what let a skipped product cost nothing.
    """
    pid, barcode = _seed(mongo_db, shape)
    want = 1 if physical else 0
    answers = _physical_readers(mongo_db, http, pid, barcode)
    bad = _disagreements(answers, want)
    assert not bad, (
        f"a unit stored as {label} is "
        f"{'ON the shelf' if physical else 'NOT on the shelf'}, but these "
        f"readers of 'is it physically here?' disagree:\n" + "\n".join(bad)
    )


@pytest.mark.parametrize("label,shape,sellable,physical", _SHAPES, ids=_IDS)
def test_every_reader_agrees_whether_this_unit_may_be_sold(
    mongo_db, http, label, shape, sellable, physical
):
    """The same unit, every SELLABLE reader (on-hand rollup, valuation,
    Shopify write-back, balancing, collection insights), one answer."""
    pid, _barcode = _seed(mongo_db, shape)
    sku = mongo_db["products"].find_one({"_id": pid})["sku"]
    want = 1 if sellable else 0
    answers = _sellable_readers(mongo_db, pid, sku)
    bad = _disagreements(answers, want)
    assert not bad, (
        f"a unit stored as {label} is "
        f"{'SELLABLE' if sellable else 'NOT sellable'}, but these readers of "
        f"'may this be sold?' disagree:\n" + "\n".join(bad)
    )


def test_reserved_is_the_only_difference_between_the_two_questions():
    """The two questions may differ on RESERVED and nowhere else -- if they
    ever differ elsewhere, one of them has grown a second rule."""
    differ = {
        label for label, _shape, sellable, physical in _SHAPES if sellable != physical
    }
    assert differ == {
        "RESERVED",
        "reserved (MF1: lowercase)",
        "Reserved (title case)",
    }, f"sellable and physical disagree outside RESERVED: {sorted(differ)}"


def test_a_lowercase_reserved_unit_is_not_invisible_to_the_count(mongo_db):
    """MF1, pinned on its own: the exact unit that bought a clean day-end.

    `canonical_state('reserved')` is RESERVED, so by this codebase's own rule
    the unit is standing on the shelf and its product MUST be in the set the
    count is judged complete against."""
    from api.routers.inventory import _scoped_product_ids
    from api.services.item_events import canonical_state, StockState

    assert canonical_state("reserved") is StockState.RESERVED

    pid, _bc = _seed(mongo_db, {"status": "reserved"})
    scope = _scoped_product_ids(_DBProxy(mongo_db), STORE) or []
    assert pid in scope, (
        "a lowercase `reserved` unit is on the shelf but its product never "
        "entered the count's expected set -- skipping it costs the counter "
        "nothing and the day-end locks as a clean count"
    )
