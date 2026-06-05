"""
BVI safety-net tests: drift detector, oversell repush sweep, parity oracle,
uploads audit (Steps 3, 4, 6 of the BVI merge completion).

All tests use in-memory fakes (no real DB, no real Shopify). The Shopify
network boundary (shopify_push._graphql) is monkeypatched so no HTTP call
ever escapes. Mirrors the style of test_online_sync_health.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ECOMMERCE_DATABASE_URL", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import online_sync_health as sh  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal in-memory fakes (same _FakeCursor / _FakeColl / _FakeDb pattern
# as test_online_sync_health.py)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeColl:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def find(self, query=None, _projection=None, *_a, **_k):
        rows = [r for r in self._rows if _matches(r, query or {})]
        return _FakeCursor(rows)

    def find_one(self, query=None, _projection=None):
        for r in self._rows:
            if _matches(r, query or {}):
                return r
        return None

    def count_documents(self, query):
        return sum(1 for r in self._rows if _matches(r, query))

    def aggregate(self, _pipeline):
        return iter([])


def _get_nested(row, key):
    """Resolve a potentially dot-notation key from a nested dict."""
    parts = key.split(".")
    val = row
    for part in parts:
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def _key_present(row, key) -> bool:
    """Check whether a dot-notation key exists (even if its value is None)."""
    parts = key.split(".")
    val = row
    for i, part in enumerate(parts):
        if not isinstance(val, dict):
            return False
        if part not in val:
            return False
        if i == len(parts) - 1:
            return True
        val = val[part]
    return True


def _matches(row, query) -> bool:
    """Minimal matcher for $exists, $nin, $ne, $in, $and, literal equality.
    Supports dot-notation keys (e.g. 'ecom.shopify_product_id')."""
    for key, cond in query.items():
        if key == "$and":
            if not all(_matches(row, sub) for sub in cond):
                return False
            continue
        val = _get_nested(row, key)
        if isinstance(cond, dict):
            if "$exists" in cond:
                present = _key_present(row, key)
                if cond["$exists"] and not present:
                    return False
                if not cond["$exists"] and present:
                    return False
            if "$nin" in cond and val in cond["$nin"]:
                return False
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        else:
            if val != cond:
                return False
    return True


class _FakeDb:
    def __init__(self, colls=None):
        self._colls = dict(colls or {})

    def __getitem__(self, name):
        return self._colls.get(name, _FakeColl([]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine synchronously (test helper)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# STEP 3: detect_drift tests
# ---------------------------------------------------------------------------


def test_detect_drift_failsoft_no_creds():
    """No Shopify creds -> checked=False with a reason, NEVER raises."""
    db = _FakeDb()  # empty DB, no integrations doc
    # _has_shopify_creds returns False when no integrations doc exists.
    result = _run(sh.detect_drift(db, limit=10))
    assert result["checked"] is False
    assert result["reason"] is not None
    assert "cred" in result["reason"].lower() or "configured" in result["reason"].lower()
    assert result["drifted"] == []
    assert result["counts"]["scanned"] == 0


def test_detect_drift_failsoft_none_db():
    """None db -> checked=False, never raises."""
    result = _run(sh.detect_drift(None, limit=10))
    assert result["checked"] is False
    assert result["drifted"] == []


def test_detect_drift_no_pushed_gids():
    """DB has products but none with shopify gids -> checked=True, empty drifted."""
    db = _FakeDb({
        "catalog_products": _FakeColl([
            {"sku": "SKU001", "ecom": {"status": "DRAFT"}},  # no shopify_product_id
        ]),
        "ecom_collections": _FakeColl([]),
        "integrations": _FakeColl([
            {"type": "shopify", "enabled": True,
             "config": {"shop_url": "x.myshopify.com", "access_token": "tok"}}
        ]),
    })

    # Patch _has_shopify_creds to return True and _graphql to return empty nodes.
    async def _fake_graphql(db, query, variables):
        return {"data": {"nodes": []}}

    with patch("api.services.shopify_push._has_shopify_creds", return_value=True), \
         patch("api.services.shopify_push._graphql", new=AsyncMock(return_value={"data": {"nodes": []}})):
        result = _run(sh.detect_drift(db, limit=10))

    assert result["checked"] is True
    assert "no pushed gids" in (result["reason"] or "")
    assert result["drifted"] == []


def test_detect_drift_flags_drift_when_shopify_newer():
    """When Shopify updatedAt is after our last_pushed_at, the gid is drifted."""
    from datetime import datetime, timezone

    db = _FakeDb({
        "catalog_products": _FakeColl([
            {
                "sku": "DRIFT-SKU",
                "ecom": {
                    "shopify_product_id": "gid://shopify/Product/99",
                    "last_pushed_at": datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
                },
            }
        ]),
        "ecom_collections": _FakeColl([]),
    })

    # Shopify says it was updated AFTER our stamp -> drift.
    shopify_nodes = [
        {"id": "gid://shopify/Product/99", "updatedAt": "2026-06-02T10:00:00Z"}
    ]
    fake_body = {"data": {"nodes": shopify_nodes}}

    with patch("api.services.shopify_push._has_shopify_creds", return_value=True), \
         patch("api.services.shopify_push._graphql", new=AsyncMock(return_value=fake_body)):
        result = _run(sh.detect_drift(db, limit=10))

    assert result["checked"] is True
    assert result["counts"]["drifted"] == 1
    assert len(result["drifted"]) == 1
    assert result["drifted"][0]["gid"] == "gid://shopify/Product/99"
    assert result["drifted"][0]["sku"] == "DRIFT-SKU"


def test_detect_drift_no_drift_when_shopify_older():
    """When Shopify updatedAt is BEFORE last_pushed_at, no drift is flagged."""
    from datetime import datetime, timezone

    db = _FakeDb({
        "catalog_products": _FakeColl([
            {
                "sku": "OK-SKU",
                "ecom": {
                    "shopify_product_id": "gid://shopify/Product/55",
                    "last_pushed_at": datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc),
                },
            }
        ]),
        "ecom_collections": _FakeColl([]),
    })

    # Shopify updated BEFORE our stamp -> no drift.
    shopify_nodes = [
        {"id": "gid://shopify/Product/55", "updatedAt": "2026-06-04T10:00:00Z"}
    ]
    fake_body = {"data": {"nodes": shopify_nodes}}

    with patch("api.services.shopify_push._has_shopify_creds", return_value=True), \
         patch("api.services.shopify_push._graphql", new=AsyncMock(return_value=fake_body)):
        result = _run(sh.detect_drift(db, limit=10))

    assert result["checked"] is True
    assert result["counts"]["drifted"] == 0
    assert result["drifted"] == []


def test_detect_drift_no_timestamp_counted_separately():
    """Gids without last_pushed_at (pre-Step-3 rows) are counted in no_timestamp, not drifted."""
    db = _FakeDb({
        "catalog_products": _FakeColl([
            {
                "sku": "OLD-SKU",
                "ecom": {
                    "shopify_product_id": "gid://shopify/Product/77",
                    # no last_pushed_at
                },
            }
        ]),
        "ecom_collections": _FakeColl([]),
    })

    shopify_nodes = [
        {"id": "gid://shopify/Product/77", "updatedAt": "2026-06-05T10:00:00Z"}
    ]
    fake_body = {"data": {"nodes": shopify_nodes}}

    with patch("api.services.shopify_push._has_shopify_creds", return_value=True), \
         patch("api.services.shopify_push._graphql", new=AsyncMock(return_value=fake_body)):
        result = _run(sh.detect_drift(db, limit=10))

    assert result["checked"] is True
    assert result["counts"]["drifted"] == 0
    assert result["counts"]["no_timestamp"] == 1


# ---------------------------------------------------------------------------
# STEP 4: repush_oversell_risk tests
# ---------------------------------------------------------------------------


def test_repush_oversell_risk_dark_no_writes_enabled():
    """When IMS_SHOPIFY_WRITES is off (default), repush returns the plan without writing."""
    db = _FakeDb({"products": _FakeColl([])})

    result = _run(sh.repush_oversell_risk(db, dry_run=True))
    assert result["dry_run"] is True
    assert result["repushed"] == []
    assert result["skipped_reason"] is not None


def test_repush_oversell_risk_dry_run_returns_plan_not_write():
    """dry_run=True returns would_repush entries without calling Shopify, even if
    writes are enabled."""
    db = _FakeDb({
        "products": _FakeColl([
            {"product_id": "P1", "sku": "SKU-A", "is_active": True}
        ]),
        "stock_units": _FakeColl([]),
    })

    with patch("agents.nexus_providers.ims_shopify_writes_enabled", return_value=True), \
         patch("api.services.online_catalog.online_status_for_skus",
               return_value={"SKU-A": {"online": True, "online_stock": 5}}), \
         patch("api.services.stock_allocation.reconcile_items",
               return_value={
                   "items": [{"sku": "SKU-A", "status": "OVERSELL_RISK",
                               "in_store": 0, "online": 5}],
                   "summary": {}
               }):
        result = _run(sh.repush_oversell_risk(db, dry_run=True))

    assert result["dry_run"] is True
    # would_repush should list the oversell SKU
    assert any(r.get("sku") == "SKU-A" for r in result["would_repush"])
    # no actual Shopify calls were made
    assert result["repushed"] == []
    assert result["skipped_reason"] is not None  # "dry_run=True ..."


def test_repush_oversell_risk_none_db():
    """None db -> no crash, skipped_reason set."""
    result = _run(sh.repush_oversell_risk(None, dry_run=True))
    assert result["dry_run"] is True
    assert result["skipped_reason"] is not None


def test_repush_oversell_risk_no_oversell_skus():
    """When no SKUs are oversell-risk, would_repush is empty."""
    db = _FakeDb({"products": _FakeColl([])})

    with patch("agents.nexus_providers.ims_shopify_writes_enabled", return_value=True), \
         patch("api.services.online_catalog.online_status_for_skus", return_value={}), \
         patch("api.services.stock_allocation.reconcile_items",
               return_value={"items": [], "summary": {}}):
        result = _run(sh.repush_oversell_risk(db, dry_run=True))

    assert result["would_repush"] == []
    assert result["repushed"] == []


# ---------------------------------------------------------------------------
# STEP 6a: parity_summary tests
# ---------------------------------------------------------------------------


def test_parity_summary_none_db():
    """None db -> all zeros, ok=False, never raises."""
    result = sh.parity_summary(None)
    assert result["ok"] is False
    for row in result["entities"].values():
        assert row["total"] == 0
        assert row["pushed"] == 0
        assert row["missing"] == 0


def test_parity_summary_counts_correctly():
    """3 products total, 1 pushed -> missing=2."""
    db = _FakeDb({
        "catalog_products": _FakeColl([
            {"sku": "A"},  # no ecom.shopify_product_id -> not pushed
            {"sku": "B"},  # not pushed
            {"sku": "C", "ecom": {"shopify_product_id": "gid://shopify/Product/1"}},
        ]),
        "catalog_variants": _FakeColl([]),
        "ecom_collections": _FakeColl([
            {"handle": "x", "shopify_collection_id": "gid://shopify/Collection/2"},
        ]),
        "product_images": _FakeColl([]),
    })
    result = sh.parity_summary(db)
    assert result["ok"] is True
    assert result["entities"]["catalog_products"]["total"] == 3
    assert result["entities"]["catalog_products"]["pushed"] == 1
    assert result["entities"]["catalog_products"]["missing"] == 2
    assert result["entities"]["ecom_collections"]["pushed"] == 1
    assert result["entities"]["catalog_variants"]["total"] == 0


def test_parity_summary_all_entities_present():
    """Shape check: all four entity keys are always present."""
    result = sh.parity_summary(_FakeDb())
    assert set(result["entities"].keys()) == {
        "catalog_products", "catalog_variants",
        "ecom_collections", "product_images",
    }


# ---------------------------------------------------------------------------
# STEP 6b: uploads_image_audit tests
# ---------------------------------------------------------------------------


def test_uploads_audit_none_db():
    """None db -> checked=False, never raises."""
    result = sh.uploads_image_audit(None)
    assert result["checked"] is False
    assert result["local_url_count"] == 0
    assert result["items"] == []


def test_uploads_audit_detects_local_paths():
    """Images with /uploads/ paths are flagged."""
    db = _FakeDb({
        "product_images": _FakeColl([
            {"image_id": "IMG1", "sku": "SKU-A",
             "url": "/uploads/images/abc.jpg",
             "edited_url": None, "status": "PENDING"},
            {"image_id": "IMG2", "sku": "SKU-B",
             "url": "https://cdn.shopify.com/s/files/photo.jpg",
             "edited_url": None, "status": "APPROVED"},
        ])
    })
    result = sh.uploads_image_audit(db)
    assert result["checked"] is True
    assert result["local_url_count"] == 1
    assert result["items"][0]["image_id"] == "IMG1"


def test_uploads_audit_flags_local_edited_url_too():
    """An edited_url still at /uploads/ is also flagged even if the primary URL is remote."""
    db = _FakeDb({
        "product_images": _FakeColl([
            {"image_id": "IMG3", "sku": "SKU-C",
             "url": "https://cdn.example.com/raw.jpg",
             "edited_url": "/uploads/edited/design.jpg",
             "status": "EDITED"},
        ])
    })
    result = sh.uploads_image_audit(db)
    assert result["local_url_count"] == 1
    assert result["items"][0]["edited_local"] is True
    assert result["items"][0]["primary_local"] is False


def test_uploads_audit_clean_catalog_is_zero():
    """All https URLs -> local_url_count=0, items=[]."""
    db = _FakeDb({
        "product_images": _FakeColl([
            {"image_id": "IMG4", "sku": "SKU-D",
             "url": "https://cdn.shopify.com/s/files/1.jpg",
             "edited_url": "https://cdn.shopify.com/s/files/1e.jpg",
             "status": "APPROVED"},
        ])
    })
    result = sh.uploads_image_audit(db)
    assert result["checked"] is True
    assert result["local_url_count"] == 0
    assert result["items"] == []


def test_uploads_audit_empty_collection():
    """No images -> checked=True, local_url_count=0."""
    db = _FakeDb({"product_images": _FakeColl([])})
    result = sh.uploads_image_audit(db)
    assert result["checked"] is True
    assert result["local_url_count"] == 0


# ---------------------------------------------------------------------------
# sync_health surface includes drift block (shape test)
# ---------------------------------------------------------------------------


def test_sync_health_includes_drift_block():
    """sync_health now returns a 'drift' key (Step 3 surface)."""
    result = sh.sync_health(None)
    assert "drift" in result
    drift = result["drift"]
    assert "checked" in drift
    # When called from a sync context with no DB, the shim should gracefully
    # produce a checked=False or checked=True (no-gids) result.
    assert isinstance(drift["drifted"], list)


# ---------------------------------------------------------------------------
# HTTP endpoint tests (shape + SUPERADMIN gate)
# ---------------------------------------------------------------------------

_DRIFT_EP = "/api/v1/admin/online-store/drift"
_REPUSH_EP = "/api/v1/admin/online-store/repush-oversell"
_PARITY_EP = "/api/v1/admin/online-store/parity"


def _token(roles):
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": f"bvi-{'-'.join(roles).lower()}",
                "username": "bvi-tester",
                "roles": roles,
                "store_ids": ["BV-TEST-01"],
                "active_store_id": "BV-TEST-01",
            }
        )
    }


def test_drift_endpoint_superadmin_shape(client):
    """SUPERADMIN gets a valid drift response (no real Shopify call in test env)."""
    with patch("api.services.shopify_push._has_shopify_creds", return_value=False):
        r = client.get(_DRIFT_EP, headers=_token(["SUPERADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "checked" in body
    assert "drifted" in body
    assert "counts" in body


def test_drift_endpoint_admin_forbidden(client):
    """ADMIN passes the admin-router gate but is narrowed out."""
    r = client.get(_DRIFT_EP, headers=_token(["ADMIN"]))
    assert r.status_code == 403


def test_drift_endpoint_sales_staff_forbidden(client):
    r = client.get(_DRIFT_EP, headers=_token(["SALES_STAFF"]))
    assert r.status_code == 403


def test_repush_endpoint_superadmin_dry_run(client):
    """SUPERADMIN POST with dry_run=True returns would_repush plan, no Shopify call."""
    with patch("agents.nexus_providers.ims_shopify_writes_enabled", return_value=False):
        r = client.post(_REPUSH_EP + "?dry_run=true", headers=_token(["SUPERADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "dry_run" in body
    assert body["dry_run"] is True
    assert "would_repush" in body
    assert "repushed" in body


def test_repush_endpoint_admin_forbidden(client):
    r = client.post(_REPUSH_EP, headers=_token(["ADMIN"]))
    assert r.status_code == 403


def test_parity_endpoint_superadmin_shape(client):
    """SUPERADMIN gets a valid parity + uploads_audit response."""
    r = client.get(_PARITY_EP, headers=_token(["SUPERADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "parity" in body
    assert "uploads_audit" in body
    assert "entities" in body["parity"]
    assert "local_url_count" in body["uploads_audit"]


def test_parity_endpoint_admin_forbidden(client):
    r = client.get(_PARITY_EP, headers=_token(["ADMIN"]))
    assert r.status_code == 403
