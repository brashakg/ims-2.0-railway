"""
IMS 2.0 — Opening-stock importer (go-live) tests
================================================
GET-to-go-live bulk seed of shelf quantities. Covers:
  - Model bounds (qty 1..10000, rows 1..5000, unit_cost >= 0).
  - Row resolution: by product_id, by sku, not-found, missing-identifier.
  - PREVIEW: never writes; flags products that already hold stock; echoes the
    per-row unit_cost and the total_value valuation.
  - COMMIT: mints N serialized rows per row; skip_if_existing guards a re-run
    so a double-submit can't double inventory; stamps GRN-style cost fields
    (unit_cost / cost_price / cost_source) when unit_cost is given; writes ONE
    opening_stock_batches summary doc (movements-ledger source) and ONE
    compliance audit row -- both fail-soft.
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


def test_row_unit_cost_bounds():
    from pydantic import ValidationError
    from api.routers.inventory import OpeningStockRow

    assert OpeningStockRow(product_id="P1", quantity=1).unit_cost is None
    OpeningStockRow(product_id="P1", quantity=1, unit_cost=0)
    OpeningStockRow(product_id="P1", quantity=1, unit_cost=1450.5)
    with pytest.raises(ValidationError):
        OpeningStockRow(product_id="P1", quantity=1, unit_cost=-0.01)


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


class FakeBatchColl:
    """Capturing stand-in for the `opening_stock_batches` collection."""

    def __init__(self):
        self.docs: List[dict] = []

    def insert_one(self, doc):
        self.docs.append(doc)
        return doc


class FakeCommitDB:
    """get_collection('opening_stock_batches') -> capturing coll. Anything else
    (counters) -> a bare object with no find_one_and_update, so the barcode
    allocator fail-softs to the legacy scheme exactly like a missing counter."""

    def __init__(self):
        self.batches = FakeBatchColl()

    def get_collection(self, name):
        if name == "opening_stock_batches":
            return self.batches
        return object()


class FakeAuditRepo:
    def __init__(self):
        self.rows: List[dict] = []

    def create(self, doc):
        self.rows.append(doc)
        return doc


class BoomAuditRepo:
    def create(self, doc):
        raise RuntimeError("audit store down")


@pytest.fixture
def patched(monkeypatch):
    from api.routers import inventory as inv

    def install(products_by_id, products_by_sku, available, db=None, audit=None):
        prod = FakeProductRepo(products_by_id, products_by_sku)
        stock = FakeStockRepo(available)
        monkeypatch.setattr(inv, "get_product_repository", lambda: prod)
        monkeypatch.setattr(inv, "get_stock_repository", lambda: stock)
        # db=None (default): counters + batch write both fail-soft skipped.
        monkeypatch.setattr(inv, "_get_db", lambda: db)
        # audit=None (default): the fail-soft audit helper no-ops.
        monkeypatch.setattr(inv, "get_audit_repository", lambda: audit)
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


# ============================================================================
# COST CAPTURE (valuation)
# ============================================================================
_P1 = {"product_id": "P1", "sku": "SKU1", "model": "Frame A"}
_P2 = {"product_id": "P2", "sku": "SKU2", "model": "Sun B"}
_TWO_PRODUCTS = (
    {"P1": _P1, "P2": _P2},
    {"SKU1": _P1, "SKU2": _P2},
)


def test_preview_echoes_cost_and_total_value(client, auth_headers, patched):
    patched(*_TWO_PRODUCTS, {"P2": 4})  # P2 already stocked -> skipped
    body = {
        "rows": [
            {"product_id": "P1", "quantity": 5, "unit_cost": 100.5},
            {"product_id": "P2", "quantity": 3, "unit_cost": 50},
        ],
        "skip_if_existing": True,
    }
    out = client.post(_PREVIEW, json=body, headers=auth_headers).json()
    assert out["rows"][0]["unit_cost"] == 100.5
    assert out["rows"][1]["unit_cost"] == 50.0
    # Only the row that WILL add units contributes to the valuation.
    assert out["summary"]["total_value"] == pytest.approx(502.5)


def test_preview_without_cost_has_zero_total_value(client, auth_headers, patched):
    patched(*_TWO_PRODUCTS, {})
    body = {"rows": [{"product_id": "P1", "quantity": 5}]}
    out = client.post(_PREVIEW, json=body, headers=auth_headers).json()
    assert out["rows"][0]["unit_cost"] is None
    assert out["summary"]["total_value"] == 0.0


def test_commit_stamps_cost_fields(client, auth_headers, patched):
    _prod, stock = patched(*_TWO_PRODUCTS, {})
    body = {"rows": [{"product_id": "P1", "quantity": 3, "unit_cost": 1450.559}]}
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert out["summary"]["units_added"] == 3
    # GRN-parity cost fields on EVERY minted unit (rounded to 2 dp).
    for doc in stock.created:
        assert doc["unit_cost"] == 1450.56
        assert doc["cost_price"] == 1450.56
        assert doc["cost_source"] == "OPENING_STOCK"
    assert out["rows"][0]["unit_cost"] == 1450.56
    assert out["summary"]["total_value"] == pytest.approx(4351.68)


def test_commit_without_cost_mints_without_cost_fields(client, auth_headers, patched):
    _prod, stock = patched(*_TWO_PRODUCTS, {})
    body = {"rows": [{"product_id": "P1", "quantity": 2}]}
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert out["summary"]["units_added"] == 2
    for doc in stock.created:
        assert "unit_cost" not in doc
        assert "cost_price" not in doc
        assert "cost_source" not in doc
    assert out["summary"]["total_value"] == 0.0


def test_commit_zero_cost_treated_as_absent(client, auth_headers, patched):
    _prod, stock = patched(*_TWO_PRODUCTS, {})
    body = {"rows": [{"product_id": "P1", "quantity": 1, "unit_cost": 0}]}
    client.post(_COMMIT, json=body, headers=auth_headers)
    assert "unit_cost" not in stock.created[0]


# ============================================================================
# BATCH SUMMARY DOC (movements-ledger source)
# ============================================================================
def test_commit_writes_batch_summary_doc(client, auth_headers, patched):
    db = FakeCommitDB()
    patched(*_TWO_PRODUCTS, {}, db=db)
    body = {
        "rows": [
            {"product_id": "P1", "quantity": 2, "unit_cost": 100},
            {"product_id": "P2", "quantity": 3},  # no cost -> still a line
        ]
    }
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert len(db.batches.docs) == 1
    doc = db.batches.docs[0]
    assert doc["batch_id"].startswith("OSB-")
    assert out["summary"]["batch_id"] == doc["batch_id"]
    assert doc["total_units"] == 5
    assert doc["total_value"] == pytest.approx(200.0)
    assert doc["committed_at"]  # ISO timestamp for the movements cutoff
    lines = {ln["product_id"]: ln for ln in doc["lines"]}
    assert lines["P1"]["qty"] == 2 and lines["P1"]["unit_cost"] == 100.0
    assert lines["P2"]["qty"] == 3 and lines["P2"]["unit_cost"] is None
    assert lines["P1"]["sku"] == "SKU1" and lines["P1"]["product_name"] == "Frame A"


def test_commit_all_skipped_writes_no_batch_doc(client, auth_headers, patched):
    db = FakeCommitDB()
    patched(*_TWO_PRODUCTS, {"P1": 2}, db=db)
    body = {"rows": [{"product_id": "P1", "quantity": 9}], "skip_if_existing": True}
    out = client.post(_COMMIT, json=body, headers=auth_headers).json()
    assert db.batches.docs == []
    assert out["summary"]["batch_id"] is None


def test_commit_batch_write_failure_is_fail_soft(client, auth_headers, patched):
    class BoomBatchDB:
        """Only the batch-summary collection is down; counters just fail-soft
        to the legacy barcode scheme like the default fake."""

        def get_collection(self, name):
            if name == "opening_stock_batches":
                raise RuntimeError("db down")
            return object()

    _prod, stock = patched(*_TWO_PRODUCTS, {}, db=BoomBatchDB())
    body = {"rows": [{"product_id": "P1", "quantity": 2}]}
    resp = client.post(_COMMIT, json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    # The minted stock always wins; the summary just carries no batch ref.
    assert out["summary"]["units_added"] == 2
    assert out["summary"]["batch_id"] is None
    assert len(stock.created) == 2


# ============================================================================
# COMPLIANCE AUDIT ROW
# ============================================================================
def test_commit_writes_audit_row(client, auth_headers, patched):
    db = FakeCommitDB()
    audit = FakeAuditRepo()
    patched(*_TWO_PRODUCTS, {"P2": 1}, db=db, audit=audit)
    body = {
        "rows": [
            {"product_id": "P1", "quantity": 2, "unit_cost": 10},
            {"product_id": "P2", "quantity": 5},  # skipped (existing)
            {"sku": "GHOST", "quantity": 1},  # error
        ]
    }
    client.post(_COMMIT, json=body, headers=auth_headers)
    assert len(audit.rows) == 1  # ONE summary row per commit, not per unit
    row = audit.rows[0]
    assert row["action"] == "OPENING_STOCK_IMPORT"
    assert row["entity_type"] == "OPENING_STOCK_BATCH"
    assert row["entity_id"] == db.batches.docs[0]["batch_id"]
    details = row["details"]
    assert details["total_rows"] == 3
    assert details["products_count"] == 1
    assert details["units_minted"] == 2
    assert details["rows_skipped"] == 1
    assert details["rows_with_errors"] == 1
    assert details["total_value"] == pytest.approx(20.0)


def test_commit_audit_failure_is_fail_soft(client, auth_headers, patched):
    _prod, stock = patched(*_TWO_PRODUCTS, {}, audit=BoomAuditRepo())
    body = {"rows": [{"product_id": "P1", "quantity": 1}]}
    resp = client.post(_COMMIT, json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["units_added"] == 1
    assert len(stock.created) == 1
