"""
Online catalog bridge tests (consolidation Step 4)
==================================================
Pure tests for services.online_catalog row-mapping + fail-soft behaviour.
The live cross-DB query is exercised against the e-commerce Postgres in
production; here we test the deterministic logic.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
# Ensure the bridge is treated as unconfigured for the fail-soft test.
os.environ.pop("ECOMMERCE_DATABASE_URL", None)

from api.services.online_catalog import (  # noqa: E402
    ecommerce_db_configured,
    map_rows,
    normalize_sku,
    online_status_for_skus,
)


def test_normalize_sku():
    assert normalize_sku("  AB-1 ") == "AB-1"
    assert normalize_sku(None) == ""
    assert normalize_sku("") == ""
    assert normalize_sku(123) == "123"


def test_map_rows_online_when_pushed_to_shopify():
    rows = [("SKU1", 7, "DRAFT", True)]  # pushed -> online even if DRAFT
    out = map_rows(rows)
    assert out["SKU1"] == {"online": True, "online_stock": 7, "status": "DRAFT"}


def test_map_rows_online_when_published():
    rows = [("SKU2", 0, "PUBLISHED", False)]
    out = map_rows(rows)
    assert out["SKU2"]["online"] is True
    assert out["SKU2"]["online_stock"] == 0


def test_map_rows_not_online_when_draft_and_unpushed():
    rows = [("SKU3", 5, "DRAFT", False)]
    out = map_rows(rows)
    assert out["SKU3"]["online"] is False
    assert out["SKU3"]["online_stock"] == 5


def test_map_rows_skips_blank_sku_and_coerces_stock():
    rows = [("", 9, "PUBLISHED", True), ("SKU4", None, "PUBLISHED", True)]
    out = map_rows(rows)
    assert "" not in out
    assert out["SKU4"]["online_stock"] == 0  # None -> 0


def test_map_rows_empty():
    assert map_rows([]) == {}
    assert map_rows(None) == {}


def test_status_for_skus_failsoft_when_unconfigured():
    # No ECOMMERCE_DATABASE_URL -> empty dict, never raises.
    assert ecommerce_db_configured() is False
    assert online_status_for_skus(["A", "B"]) == {}
    assert online_status_for_skus([]) == {}
