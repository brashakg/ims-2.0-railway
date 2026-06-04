"""
IMS 2.0 — Opening-stock importer (go-live) tests
================================================
GET-to-go-live bulk seed of shelf quantities. Covers:
  - Model bounds (qty 1..10000, rows 1..5000).
  - Row resolution: by product_id, by sku, not-found, missing-identifier.
  - PREVIEW: never writes; flags products that already hold stock.
  - COMMIT: mints N serialized rows per row; skip_if_existing guards a re-run
    so a double-submit can't double inventory.
  - Auth / role gating handled by test_inventory_gating (routes added there).
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-opening-stock")


# ============================================================================
# Model bounds
# ============================================================================
def test_row_quantity_bounds():
    from pydantic import ValidationError
    from api.routers.inventory import OpeningStockRow

    OpeningStockRow(product_id="P1", quantity=1)
    OpeningStockRow(product_id="P1", quantity=10_000)
    for bad in (0, -1, 10_001):
        with pytest.raises(ValidationError):
            OpeningStockRow(product_id="P1", quantity=bad)


def test_import_row_count_bounds():
    from pydantic import ValidationError
    from api.routers.inventory import OpeningStockImport, OpeningStockRow

    with pytest.raises(ValidationError):
        OpeningStockImport(rows=[])  # min_length=1
    ok = OpeningStockImport(rows=[OpeningStockRow(product_id="P1", quantity=1)])
    assert ok.skip_if_existing is True  # safe default


# ============================================================================
# Fakes mirroring StockRepository / ProductRepository surface used by the route
# ============================================================================
class FakeProductRepo:
    def __init__(self, by_id: Dict[str, dict], by_sku: Dict[str, dict]):
        self._by_id = by_id
        self._by_sku = by_sku

    def find_by_id(self, pid):
        return self._by_id.get(pid)

    def find_by_sku(self, sku):
        return self._by_sku.get(sku)


class FakeStockRepo:
    def __init__(self, available: Dict[str, int]):
        # product_id -> current AVAILABLE count
        self._available = dict(available)
        self.created: List[dict] = []

    def find_available(self, product_id, _store_id):
        return self._available.get(product_id, 0)

    def create(self, doc):
        self.created.append(doc)
        self._available[doc["product_id"]] = self._available.get(doc["product_id"], 0) + 1
        return doc


@pytest.fixture
def patched(monkeypatch):
    from api.routers import inventory as inv

    def install(products_by_id, products_by_sku, available):
        prod = FakeProductRepo(products_by_id, products_by_sku)
        stock = FakeStockRepo(available)
        monkeypatch.setattr(inv, "get_product_repository", lambda: prod)
        monkeypatch.setattr(inv, "get_stock_repository", lambda: stock)
        # counters collection unused in the fake create path
        monkeypatch.setattr(inv, "_get_db", lambda: None)
        return prod, stock

    return install


_PREVIEW = "/api/v1/inventory/opening-stock/preview"
_COMMIT = "/api/v1/inventory/opening-stock/commit"


# ============================================================================
# PREVIEW
# ============================================================================
def test_preview_validates_without_writing(client, auth_headers, patched):
    _prod, stock = patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {},
    )
    body = {"rows": [{"product_id": "P1", "quantity": 5}]}
    resp = client.post(_PREVIEW, json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["summary"]["units_to_add"] == 5
    assert out["rows"][0]["status"] == "WILL_ADD"
    # Nothing written in preview.
    assert stock.created == []


def test_preview_resolves_by_sku(client, auth_headers, patched):
    patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {},
    )
    body = {"rows": [{"sku": "SKU1", "quantity": 3}]}
    out = client.post(_PREVIEW, json=body, headers=auth_headers).json()
    assert out["rows"][0]["product_id"] == "P1"
    assert out["summary"]["units_to_add"] == 3


def test_preview_flags_unknown_product(client, auth_headers, patched):
    patched({}, {}, {})
    body = {"rows": [{"sku": "NOPE", "quantity": 2}]}
    out = client.post(_PREVIEW, json=body, headers=auth_headers).json()
    assert out["rows"][0]["status"] == "ERROR"
    assert out["summary"]["rows_with_errors"] == 1
    assert out["summary"]["units_to_add"] == 0


def test_preview_flags_existing_stock_as_skip(client, auth_headers, patched):
    patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"P1": 4},  # already has 4
    )
    body = {"rows": [{"product_id": "P1", "quantity": 5}], "skip_if_existing": True}
    out = client.post(_PREVIEW, json=body, headers=auth_headers).json()
    assert out["rows"][0]["status"] == "SKIP_EXISTING"
    assert out["summary"]["rows_to_skip"] == 1
    assert out["summary"]["units_to_add"] == 0


def test_preview_add_on_top_when_not_skipping(client, auth_headers, patched):
    patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"P1": 4},
    )
    body = {"rows": [{"product_id": "P1", "quantity": 5}], "skip_if_existing": False}
    out = client.post(_PREVIEW, json=body, headers=auth_headers).json()
    assert out["rows"][0]["status"] == "WILL_ADD_ON_TOP"
    assert out["summary"]["units_to_add"] == 5


# ============================================================================
# COMMIT
# ============================================================================
def test_commit_mints_rows(client, auth_headers, patched):
    _prod, stock = patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {},
    )
    body = {"rows": [{"product_id": "P1", "quantity": 3}]}
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert out["summary"]["units_added"] == 3
    assert len(stock.created) == 3
    # Each minted row is a single serialized AVAILABLE unit tagged OPENING_STOCK.
    for doc in stock.created:
        assert doc["quantity"] == 1
        assert doc["status"] == "AVAILABLE"
        assert doc["source"] == "OPENING_STOCK"
        assert doc["product_id"] == "P1"


def test_commit_skips_existing_by_default(client, auth_headers, patched):
    _prod, stock = patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"P1": 2},  # already stocked
    )
    body = {"rows": [{"product_id": "P1", "quantity": 9}], "skip_if_existing": True}
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert out["summary"]["units_added"] == 0
    assert out["summary"]["rows_skipped"] == 1
    assert stock.created == []  # double-run guard held


def test_commit_partial_failure_does_not_abort(client, auth_headers, patched):
    _prod, stock = patched(
        {"P1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {"SKU1": {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}},
        {},
    )
    body = {
        "rows": [
            {"sku": "GHOST", "quantity": 4},  # unknown -> error row
            {"product_id": "P1", "quantity": 2},  # valid -> added
        ]
    }
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert out["summary"]["rows_with_errors"] == 1
    assert out["summary"]["units_added"] == 2
    assert len(stock.created) == 2
