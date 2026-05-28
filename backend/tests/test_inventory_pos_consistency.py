"""
IMS 2.0 - Inventory <-> POS cross-surface consistency
======================================================
Regression suite for the QA-reported "inventory split-brain" bug
(2026-05-27 sweep, owner-confirmed):

  1. POS at BV-BOK-01 -> product search "Fastrack P357" appears -> sale
     completes (ORD-BOK01-2026-8E5731).
  2. /inventory at BV-BOK-01 -> same search -> "No products found
     matching your filters" -> ledger blind to the SKU the floor just sold.

Root cause: /inventory/stock returned RAW stock_units rows (per-unit
serialised docs with no sku/name/brand fields). The frontend Stock Ledger
filters by item.name/sku/brand which were undefined on those rows, so the
search always returned empty even when units existed. POR was unaffected
because /products reads the catalog directly.

Fix (this PR): /inventory/stock now returns a PRODUCT-LEVEL view: one row
per active catalog product, enriched with on-hand counts aggregated from
stock_units. The set of products listed at a store via /inventory/stock
agrees with the set the POS can find at the same store via /products.

This module exercises a REAL mongo:7.0 (CI provides one; local dev can
fall back to localhost). Skipped fail-soft when Mongo is unreachable so
it never breaks the unit-test sweep on a laptop without Mongo.

Bugs caught here:
  * Stock Ledger returning raw stock_units docs instead of product-level
    rows (QA repro: P357 invisible).
  * Cross-store leakage (BV-BOK-01 products leaking into BV-DHN-01).
  * Catalog-only SKUs (zero on-hand) hidden from the ledger - the floor
    needs to see the full catalog the store CAN sell, not just what's
    physically on the shelf right now.
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Mongo fixture (shared shape with test_stock_integration.py)
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    """Real mongo:7.0 connection. Skip the test module fail-soft if absent."""
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip(f"Mongo unavailable at {uri}; skipping integration tests")
        return None

    db_name = f"ims_test_inv_pos_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


def _product_repo(mongo_db):
    """Wire a real ProductRepository against the test mongo db's products."""
    from database.repositories.product_repository import ProductRepository

    return ProductRepository(mongo_db["products"])


def _stock_repo(mongo_db):
    """Wire a real StockRepository against the test mongo db's stock_units."""
    from database.repositories.product_repository import StockRepository

    return StockRepository(mongo_db["stock_units"])


def _seed_catalog(mongo_db, products: List[Dict[str, Any]]) -> List[str]:
    """Bulk-create products. Returns the list of product_ids."""
    repo = _product_repo(mongo_db)
    pids: List[str] = []
    for p in products:
        created = repo.create(p)
        assert created is not None
        pids.append(created["product_id"])
    return pids


def _seed_unit(
    mongo_db,
    product_id: str,
    store_id: str,
    status: str = "AVAILABLE",
    barcode: str = "",
) -> str:
    """Create a single serialized stock_unit. Returns the stock_id."""
    repo = _stock_repo(mongo_db)
    sid = f"STK-{uuid.uuid4().hex[:8]}"
    bc = barcode or f"BC-{uuid.uuid4().hex[:8]}"
    repo.create(
        {
            "stock_id": sid,
            "product_id": product_id,
            "store_id": store_id,
            "barcode": bc,
            "quantity": 1,
            "status": status,
            "location_code": "DEFAULT",
        }
    )
    return sid


class _DBProxy:
    """Minimal get_db() shape that exposes mongo collections by name.

    Used so we can call the inventory router helpers (which expect a db
    object with get_collection) directly against the test mongo db,
    without booting the whole FastAPI app or messing with global
    dependencies.
    """

    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        # Some callers do `db.<collection_name>` (e.g. db.stock_units).
        # Defer to the underlying mongo db so both shapes work.
        return self._db[name]


# ============================================================================
# Direct invocation of the ledger builder + per-store filtering
# ============================================================================


class TestStockLedgerProductLevel:
    """The Stock Ledger row shape MUST be product-level (sku, name, brand,
    mrp, on-hand) so the frontend's text search and category filter can
    actually match. The OLD shape (raw stock_units) had none of those
    fields, which is what made the QA-reported "No products found" appear."""

    def test_ledger_returns_product_fields_not_raw_stock_units(self, mongo_db):
        """A product with stock at a store appears as ONE row with
        product-master fields (sku, name, brand, mrp), not per-unit
        stock_units docs."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "FAST-P357BK1",
                    "category": "SUNGLASS",
                    "brand": "Fastrack",
                    "model": "P357BK1",
                    "mrp": 2500.0,
                    "offer_price": 2200.0,
                    "is_active": True,
                }
            ],
        )
        pid = pids[0]

        # Three units at the same store - they should ROLL UP to one row.
        _seed_unit(mongo_db, pid, "BV-BOK-01")
        _seed_unit(mongo_db, pid, "BV-BOK-01")
        _seed_unit(mongo_db, pid, "BV-BOK-01")

        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )

        # Exactly one row for this product (units rolled up).
        matching = [r for r in rows if r["product_id"] == pid]
        assert len(matching) == 1, (
            f"Expected ONE rolled-up row per product, got {len(matching)} - "
            "ledger is back to per-unit shape."
        )

        row = matching[0]
        # Product-master fields present + correctly typed (the old shape
        # had none of these).
        assert row["sku"] == "FAST-P357BK1"
        assert row["brand"] == "Fastrack"
        assert "P357BK1" in row["name"], (
            f"name should include the model; got {row['name']!r}"
        )
        assert row["category"] == "SUNGLASS"
        assert row["mrp"] == 2500.0
        # FE consumers landed on a mix of `offerPrice` / `offer_price` -
        # both populated for backwards-compat.
        assert row["offerPrice"] == 2200.0
        assert row["offer_price"] == 2200.0
        # on-hand rolled up from 3 units. FE alias `stock` populated too.
        assert row["stock"] == 3
        assert row["quantity"] == 3
        assert row["reserved"] == 0

    def test_ledger_includes_catalog_only_products_with_zero_on_hand(
        self, mongo_db
    ):
        """A product that's in the catalog but has NO stock_units at this
        store still appears in the ledger - with on_hand=0. This is what
        keeps /inventory/stock in sync with what POS can sell (a product
        the POS lists at a store MUST appear in the ledger for that store).
        """
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "RB-AVIATOR-58",
                    "category": "SUNGLASS",
                    "brand": "Ray-Ban",
                    "model": "Aviator 58",
                    "mrp": 8500.0,
                    "offer_price": 8500.0,
                    "is_active": True,
                }
            ],
        )
        pid = pids[0]
        # Deliberately NO stock_units seeded.

        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        matching = [r for r in rows if r["product_id"] == pid]
        assert len(matching) == 1, (
            "Catalog product was hidden because it had no stock_units - "
            "ledger must show the full sellable catalog, not just rows "
            "with on-hand."
        )
        row = matching[0]
        assert row["stock"] == 0
        assert row["reserved"] == 0
        assert row["sku"] == "RB-AVIATOR-58"

    def test_ledger_reserved_counts_split_from_available(self, mongo_db):
        """RESERVED stock_units roll into the `reserved` column, NOT into
        on-hand `stock`. AVAILABLE counts toward `stock`."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "TEST-RES-1",
                    "category": "FRAME",
                    "brand": "TestBrand",
                    "model": "Reserved Test",
                    "mrp": 1000.0,
                    "offer_price": 1000.0,
                    "is_active": True,
                }
            ],
        )
        pid = pids[0]
        _seed_unit(mongo_db, pid, "BV-BOK-01", status="AVAILABLE")
        _seed_unit(mongo_db, pid, "BV-BOK-01", status="AVAILABLE")
        _seed_unit(mongo_db, pid, "BV-BOK-01", status="RESERVED")

        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        row = next(r for r in rows if r["product_id"] == pid)
        assert row["stock"] == 2, "Available units should NOT include RESERVED"
        assert row["reserved"] == 1

    def test_ledger_sold_units_do_not_count_as_on_hand(self, mongo_db):
        """SOLD stock_units must NOT inflate on-hand. After a sale, the
        unit still EXISTS in stock_units (carrying order_id for restock
        reactivation - see PR #267) but it must be invisible to on-hand
        counts. This was the wider invariant the inventory canonicalisation
        was supposed to deliver."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "TEST-SOLD-1",
                    "category": "FRAME",
                    "brand": "TestBrand",
                    "model": "Sold Test",
                    "mrp": 1000.0,
                    "offer_price": 1000.0,
                    "is_active": True,
                }
            ],
        )
        pid = pids[0]
        _seed_unit(mongo_db, pid, "BV-BOK-01", status="AVAILABLE")
        _seed_unit(mongo_db, pid, "BV-BOK-01", status="SOLD")
        _seed_unit(mongo_db, pid, "BV-BOK-01", status="SOLD")

        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        row = next(r for r in rows if r["product_id"] == pid)
        assert row["stock"] == 1, (
            f"Only AVAILABLE counts on-hand; SOLD/DAMAGED/RETURNED do not. "
            f"Got stock={row['stock']} - SOLD is leaking back into on-hand."
        )


# ============================================================================
# Cross-store scoping
# ============================================================================


class TestStockLedgerStoreScope:
    def test_ledger_is_scoped_to_one_store(self, mongo_db):
        """A unit at BV-BOK-01 must NOT count toward BV-DHN-01's on-hand."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "CROSS-STORE-1",
                    "category": "FRAME",
                    "brand": "TestBrand",
                    "model": "Cross-Store",
                    "mrp": 1000.0,
                    "offer_price": 1000.0,
                    "is_active": True,
                }
            ],
        )
        pid = pids[0]
        _seed_unit(mongo_db, pid, "BV-BOK-01")  # only at BOK
        _seed_unit(mongo_db, pid, "BV-BOK-01")  # only at BOK

        bok_rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        dhn_rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-DHN-01"
        )

        bok = next(r for r in bok_rows if r["product_id"] == pid)
        dhn = next(r for r in dhn_rows if r["product_id"] == pid)
        assert bok["stock"] == 2
        # DHN has the product in catalog but NO units. on-hand must be 0,
        # not the BOK count.
        assert dhn["stock"] == 0, (
            "Cross-store leak: BV-BOK-01 stock counted toward BV-DHN-01. "
            f"Got stock={dhn['stock']} - the store_id filter on stock_units "
            "is not being applied."
        )


# ============================================================================
# QA REPRO: POS <-> Inventory consistency
# ============================================================================


class TestPOSAndInventoryAgree:
    """The QA repro in plain English: 'a product the POS can sell at a
    store should appear in /inventory at the same store'. This is the
    invariant the fix delivers."""

    def test_qa_repro_fastrack_visible_to_both_surfaces(self, mongo_db):
        """The exact QA repro: Fastrack P357BK1 at BV-BOK-01.
        Before the fix: POS finds it, Stock Ledger shows 'No products
        found matching your filters'.
        After the fix: both surfaces return the same product."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "FAST-P357BK1",
                    "category": "SUNGLASS",
                    "brand": "Fastrack",
                    "model": "P357BK1",
                    "mrp": 2500.0,
                    "offer_price": 2200.0,
                    "is_active": True,
                }
            ],
        )
        pid = pids[0]
        _seed_unit(mongo_db, pid, "BV-BOK-01")

        # POS side: search via ProductRepository.search_products (the
        # exact path /api/v1/products?search=... takes). The QA used
        # "Fastrack P357" - search across brand+model+sku+variant.
        pos_hits = _product_repo(mongo_db).search_products("Fastrack P357")
        pos_pids = {h["product_id"] for h in pos_hits}
        assert pid in pos_pids, (
            "POS search couldn't find the seeded product - check "
            "ProductRepository.search_products fields (brand/model/sku/variant)."
        )

        # Inventory side: build the per-store ledger (exact path
        # /api/v1/inventory/stock?store_id=BV-BOK-01 takes).
        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )

        # FE filter (InventoryPage.tsx:365-369) matches against
        # item.name / item.sku / item.brand. Reproduce that filter here
        # so the test fails the same way the user's browser did before
        # the fix.
        q = "Fastrack P357".lower()
        ledger_matches = [
            r
            for r in rows
            if (q in (r.get("name") or "").lower())
            or (q in (r.get("sku") or "").lower())
            or (q in (r.get("brand") or "").lower())
        ]
        assert ledger_matches, (
            "Stock Ledger search for 'Fastrack P357' returned NOTHING - "
            "this is the exact QA-reported regression. The ledger row "
            "must carry product fields (sku/name/brand) so the FE filter "
            "can match the same string POS matched."
        )
        # And the matching row IS our seeded product, not some other one
        # that happens to share part of the search string.
        assert any(r["product_id"] == pid for r in ledger_matches)

    def test_every_pos_sellable_product_appears_in_ledger(self, mongo_db):
        """Stronger invariant: every product the POS can find at a store
        must appear in the ledger at the same store. Seed three products
        with mixed stock state and assert all three appear."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "POS-INV-1",
                    "category": "FRAME",
                    "brand": "B1",
                    "model": "M1",
                    "mrp": 1000.0,
                    "offer_price": 900.0,
                    "is_active": True,
                },
                {
                    "sku": "POS-INV-2",
                    "category": "FRAME",
                    "brand": "B2",
                    "model": "M2",
                    "mrp": 2000.0,
                    "offer_price": 1800.0,
                    "is_active": True,
                },
                {
                    "sku": "POS-INV-3",
                    "category": "FRAME",
                    "brand": "B3",
                    "model": "M3",
                    "mrp": 3000.0,
                    "offer_price": 2700.0,
                    "is_active": True,
                },
            ],
        )
        # Only product 1 + 2 have stock at BOK; product 3 has only catalog.
        _seed_unit(mongo_db, pids[0], "BV-BOK-01")
        _seed_unit(mongo_db, pids[1], "BV-BOK-01")
        _seed_unit(mongo_db, pids[1], "BV-BOK-01")
        # pids[2] = NO stock at BOK.

        # POS side: every active catalog product is sellable (the user can
        # ring it up as a special-order even with no on-hand).
        pos_hits = _product_repo(mongo_db).search_products("")  # all
        pos_pids = {h["product_id"] for h in pos_hits if h.get("is_active")}
        seeded_pos = pos_pids & set(pids)
        assert seeded_pos == set(pids), (
            "Catalog search did not return all 3 seeded active products"
        )

        # Inventory side: every product POS lists at the store must appear
        # in the ledger at the same store (even pids[2] with zero on-hand).
        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        ledger_pids = {r["product_id"] for r in rows}
        missing = set(pids) - ledger_pids
        assert not missing, (
            f"Products visible to POS but invisible in the Stock Ledger: "
            f"{missing}. This is the cross-surface bug the QA repro was "
            "filed for - the two views MUST agree."
        )

        # And the on-hand counts match what was seeded.
        by_pid = {r["product_id"]: r for r in rows}
        assert by_pid[pids[0]]["stock"] == 1
        assert by_pid[pids[1]]["stock"] == 2
        assert by_pid[pids[2]]["stock"] == 0  # catalog-only, zero on-hand

    def test_inactive_catalog_products_are_hidden_from_ledger(self, mongo_db):
        """A product flipped to is_active=False (deactivated SKU) should
        NOT appear in the ledger as a "catalog union" row - the store is
        not going to sell it. The only exception: if there are STILL
        physical units sitting on the shelf, those rows surface so the
        manager can clear them (this is what the 3rd loop in
        _build_store_ledger covers)."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "INACTIVE-1",
                    "category": "FRAME",
                    "brand": "Discontinued",
                    "model": "Old Model",
                    "mrp": 500.0,
                    "offer_price": 500.0,
                    "is_active": False,  # NOT sellable
                }
            ],
        )
        pid = pids[0]
        # Case A: deactivated with NO physical units left - hide.
        rows = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        assert not any(r["product_id"] == pid for r in rows), (
            "Inactive product with zero on-hand leaked into the ledger - "
            "it shouldn't be sellable AND it has no stock to clear."
        )

        # Case B: deactivated BUT a unit is still physically there -
        # surface the row so it can be transferred/written off.
        _seed_unit(mongo_db, pid, "BV-BOK-01")
        rows2 = _build_store_ledger(
            _stock_repo(mongo_db), _product_repo(mongo_db), "BV-BOK-01"
        )
        leftover = next((r for r in rows2 if r["product_id"] == pid), None)
        assert leftover is not None, (
            "Stranded units of a deactivated product disappeared from the "
            "ledger - the floor needs them visible to write them off."
        )
        assert leftover["stock"] == 1


# ============================================================================
# Filter passthroughs
# ============================================================================


class TestLedgerFilters:
    def test_category_filter_narrows_ledger(self, mongo_db):
        """category=FRAME should return only FRAMES, not SUNGLASS too."""
        from api.routers.inventory import _build_store_ledger

        pids = _seed_catalog(
            mongo_db,
            [
                {
                    "sku": "CAT-FR-1",
                    "category": "FRAME",
                    "brand": "FB1",
                    "model": "FM1",
                    "mrp": 1000.0,
                    "offer_price": 1000.0,
                    "is_active": True,
                },
                {
                    "sku": "CAT-SG-1",
                    "category": "SUNGLASS",
                    "brand": "SB1",
                    "model": "SM1",
                    "mrp": 2000.0,
                    "offer_price": 2000.0,
                    "is_active": True,
                },
            ],
        )
        for p in pids:
            _seed_unit(mongo_db, p, "BV-BOK-01")

        rows = _build_store_ledger(
            _stock_repo(mongo_db),
            _product_repo(mongo_db),
            "BV-BOK-01",
            category="FRAME",
        )
        cats = {r["category"] for r in rows}
        # All returned rows are FRAME (or fall-back to "" for any stranded
        # legacy rows - which shouldn't be triggered here).
        assert cats <= {"FRAME", ""}, (
            f"category=FRAME returned non-FRAME rows: {cats}"
        )
        assert any(r["sku"] == "CAT-FR-1" for r in rows)
        assert not any(r["sku"] == "CAT-SG-1" for r in rows)
