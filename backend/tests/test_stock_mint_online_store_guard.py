"""
IMS 2.0 -- F9: physical stock must never be minted onto an ONLINE store
=======================================================================
BV-ONLINE-01 / WO-ONLINE-01 are POOLED and STOCKLESS by design: the storefront
sells the physical shops' combined stock and the online store owns no serialized
units of its own. Two inventory write paths happily created real `stock_units`
against ANY store_id, including those two:

  * POST /inventory/stock/add          (add_stock)
  * POST /inventory/opening-stock/commit (opening_stock_commit)

Units minted there are invisible to every shop, unsellable at any POS, and
double every pooled on-hand rollup. Both now answer 400.

The guard reuses the ONE canonical backend detector,
``api.services.stores_util.is_online_store`` -- the same helper behind the POS,
PO delivery-store, GRN-accept and till guards -- including its fail-open
convention: the two known online ids are caught with no DB at all, an unknown id
resolves via the store doc's ``store_type``, and a flaky lookup never
false-blocks a physical shop.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_stock_mint_online_store_guard.py -q
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List

os.environ.setdefault("JWT_SECRET_KEY", "test-key-online-stock-guard")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import inventory as inv  # noqa: E402
from api.routers.inventory import (  # noqa: E402
    OpeningStockImport,
    OpeningStockRow,
    StockAddRequest,
)


def _run(coro):
    """Drive a no-await coroutine to completion without an event loop."""
    try:
        coro.send(None)
    except StopIteration as stop:
        # dict(...) -- every handler under test answers a JSON object, and it
        # keeps static analysis from inferring StopIteration.value as None.
        return dict(stop.value or {})
    raise AssertionError("handler awaited -- harness needs a real event loop")


def _user(store_id):
    return {
        "user_id": "u1",
        "username": "mgr",
        "roles": ["STORE_MANAGER"],
        "active_store_id": store_id,
        "store_ids": [store_id],
    }


# ---------------------------------------------------------------------------
# Fakes (mirror test_opening_stock_import.py)
# ---------------------------------------------------------------------------
class _ProductRepo:
    def __init__(self, by_id: Dict[str, dict]):
        self._by_id = by_id

    def find_by_id(self, pid):
        return self._by_id.get(pid)

    def find_by_sku(self, sku):
        return None


class _StockRepo:
    def __init__(self):
        self.created: List[dict] = []

    def find_available(self, _product_id, _store_id):
        return 0

    def create(self, doc):
        self.created.append(dict(doc))
        return doc


class _StoresDb:
    """Minimal db handle: get_collection('stores').find_one -> the seeded doc.

    Lets the test prove the guard consults the STORE DOC's store_type, not just
    the known-id allow-list."""

    def __init__(self, docs: Dict[str, dict]):
        self._docs = docs

    def get_collection(self, name):
        if name == "stores":
            return self
        return object()  # counters etc -> barcode allocator fail-softs

    def find_one(self, flt, _projection=None):
        return self._docs.get(flt.get("store_id"))


@pytest.fixture()
def wired(monkeypatch):
    def install(store_docs=None):
        prod = _ProductRepo({"P1": {"product_id": "P1", "sku": "SKU1", "model": "F"}})
        stock = _StockRepo()
        monkeypatch.setattr(inv, "get_product_repository", lambda: prod)
        monkeypatch.setattr(inv, "get_stock_repository", lambda: stock)
        monkeypatch.setattr(inv, "get_audit_repository", lambda: None)
        db = _StoresDb(store_docs) if store_docs is not None else None
        monkeypatch.setattr(inv, "_get_db", lambda: db)
        return stock

    return install


_ADD = StockAddRequest(product_id="P1", quantity=4)


def _import():
    return OpeningStockImport(rows=[OpeningStockRow(product_id="P1", quantity=6)])


# ---------------------------------------------------------------------------
# /inventory/stock/add
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("online_id", ["BV-ONLINE-01", "WO-ONLINE-01"])
def test_add_stock_rejected_for_known_online_store(wired, online_id):
    stock = wired()
    with pytest.raises(HTTPException) as err:
        _run(inv.add_stock(_ADD, _user(online_id)))
    assert err.value.status_code == 400
    assert "online store" in err.value.detail
    assert stock.created == [], "no unit may be minted on a pooled store"


def test_add_stock_rejected_for_store_typed_online_store(wired):
    """An online store that is NOT in the known-id list is still caught, via the
    shared detector's store_type lookup."""
    stock = wired(
        {"ZZ-ONLINE-99": {"store_id": "ZZ-ONLINE-99", "store_type": "ONLINE"}}
    )
    with pytest.raises(HTTPException) as err:
        _run(inv.add_stock(_ADD, _user("ZZ-ONLINE-99")))
    assert err.value.status_code == 400
    assert stock.created == []


def test_add_stock_unaffected_for_a_physical_store(wired):
    stock = wired({"BV-TEST-01": {"store_id": "BV-TEST-01", "store_type": "RETAIL"}})
    out = _run(inv.add_stock(_ADD, _user("BV-TEST-01")))
    assert out["quantity"] == 4
    assert len(stock.created) == 4
    assert {u["store_id"] for u in stock.created} == {"BV-TEST-01"}


def test_add_stock_fails_open_when_the_store_lookup_flakes(wired):
    """A physical shop's intake must NEVER be blocked by a DB hiccup -- the
    detector's documented fail-open convention, asserted here so a future
    'tighten it up' change cannot silently close a revenue path."""
    stock = wired()  # no db handle at all -> lookup unavailable
    out = _run(inv.add_stock(_ADD, _user("BV-TEST-01")))
    assert out["quantity"] == 4
    assert len(stock.created) == 4


# ---------------------------------------------------------------------------
# /inventory/opening-stock/commit
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("online_id", ["BV-ONLINE-01", "WO-ONLINE-01"])
def test_opening_stock_commit_rejected_for_known_online_store(wired, online_id):
    stock = wired()
    with pytest.raises(HTTPException) as err:
        _run(inv.opening_stock_commit(_import(), _user(online_id)))
    assert err.value.status_code == 400
    assert "online store" in err.value.detail
    assert stock.created == []


def test_opening_stock_commit_rejected_for_store_typed_online_store(wired):
    stock = wired(
        {"ZZ-ONLINE-99": {"store_id": "ZZ-ONLINE-99", "store_type": "online"}}
    )
    with pytest.raises(HTTPException) as err:
        _run(inv.opening_stock_commit(_import(), _user("ZZ-ONLINE-99")))
    assert err.value.status_code == 400
    assert stock.created == []


def test_opening_stock_commit_unaffected_for_a_physical_store(wired):
    stock = wired({"BV-TEST-01": {"store_id": "BV-TEST-01", "store_type": "RETAIL"}})
    out = _run(inv.opening_stock_commit(_import(), _user("BV-TEST-01")))
    assert out["summary"]["units_added"] == 6
    assert len(stock.created) == 6
    assert {u["store_id"] for u in stock.created} == {"BV-TEST-01"}


def test_guard_beats_the_missing_repo_503(wired, monkeypatch):
    """An online store answers the same clear 400 whether or not the inventory
    repos are up -- the operator gets a real reason, not 'store not available'."""
    wired()
    monkeypatch.setattr(inv, "get_stock_repository", lambda: None)
    monkeypatch.setattr(inv, "get_product_repository", lambda: None)
    with pytest.raises(HTTPException) as err:
        _run(inv.opening_stock_commit(_import(), _user("BV-ONLINE-01")))
    assert err.value.status_code == 400


def test_inventory_imports_the_one_shared_detector():
    """There must be exactly ONE online-store detector in the backend -- no
    second, drifting copy inside inventory.py."""
    from api.services import stores_util

    assert inv.is_online_store is stores_util.is_online_store


def test_guard_delegates_to_the_shared_detector(monkeypatch):
    """The guard must ASK the shared helper (so any future change to the
    detector -- new online store id, new store_type -- applies here for free)."""
    calls = []

    def _spy(_db, store_id):
        calls.append(store_id)
        return False

    monkeypatch.setattr(inv, "is_online_store", _spy)
    inv._reject_stock_mint_on_online_store("BV-TEST-01", "add stock")
    assert calls == ["BV-TEST-01"]
