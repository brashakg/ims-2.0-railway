"""
Online catalog source tests (post-BVI: IMS Mongo is the sole online truth)
==========================================================================
Pure tests for services.online_catalog: the Mongo-backed online flag / status
resolution, the Shopify inventory-target resolver the stock write-back uses,
and fail-soft behaviour. BVI + its Postgres were deleted 2026-07-20; there is
no Postgres path left to test.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.services.online_catalog import (  # noqa: E402
    inventory_items_for_skus,
    normalize_sku,
    online_mapping_available,
    online_status_for_skus,
    online_summary,
    online_variant_targets_for_skus,
    reconcile_store_barcodes,
)


# ---------------------------------------------------------------------------
# In-memory fakes: find/find_one/count_documents with the $or/$in/$exists/$nin
# predicates this module issues. Subscript access, like MockDatabase.
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    def __iter__(self):
        return iter(self._rows)


def _get_path(row, key):
    cur = row
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None, False
        if part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def _match_one(row, key, cond):
    val, exists = _get_path(row, key)
    if isinstance(cond, dict):
        if "$exists" in cond:
            if cond["$exists"] != exists:
                return False
        if "$in" in cond and val not in cond["$in"]:
            return False
        if "$nin" in cond and val in cond["$nin"]:
            return False
        return True
    return val == cond


def _matches(row, query) -> bool:
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_matches(row, clause) for clause in cond):
                return False
            continue
        if not _match_one(row, key, cond):
            return False
    return True


class _Coll:
    def __init__(self, rows=None):
        self._rows = [dict(r) for r in (rows or [])]

    def find(self, query=None, _projection=None, *_a, **_k):
        return _Cursor([r for r in self._rows if _matches(r, query or {})])

    def find_one(self, query=None, _projection=None, *_a, **_k):
        for r in self._rows:
            if _matches(r, query or {}):
                return r
        return None

    def count_documents(self, query=None):
        return sum(1 for r in self._rows if _matches(r, query or {}))


class _Db:
    def __init__(self, colls=None):
        self._colls = dict(colls or {})

    def __getitem__(self, name):
        return self._colls.get(name, _Coll([]))


def _db():
    """Catalog: one pushed DRAFT, one staged-only PUBLISHED, one unpushed DRAFT,
    plus variants carrying the write-back mapping + alternate identifiers."""
    products = _Coll(
        [
            # Pushed to Shopify (gid set) but staged DRAFT -> online (old bridge
            # semantics: pushed counts, even as a Shopify draft).
            {
                "id": "CP1",
                "sku": "SKU-PUSHED",
                "barcode": "8901111111111",
                "ecom": {"status": "DRAFT", "shopify_product_id": "gid://shopify/Product/1"},
            },
            # Staged PUBLISHED, not yet pushed -> online.
            {"id": "CP2", "sku": "SKU-PUB", "ecom": {"status": "PUBLISHED"}},
            # Staged DRAFT, never pushed -> NOT online.
            {"id": "CP3", "sku": "SKU-DRAFT", "ecom": {"status": "DRAFT"}},
            # No ecom sub-doc at all -> unknown (absent from the result).
            {"id": "CP4", "sku": "SKU-PLAIN"},
        ]
    )
    variants = _Coll(
        [
            # Variant of CP1 with the full Shopify mapping + a store barcode.
            {
                "sku": "SKU-PUSHED",
                "store_barcode": "00050567",
                "parent_product_id": "CP1",
                "shopify_variant_id": "111",
                "shopify_inventory_item_id": "999",
                "shopify_location_id": "loc-77",
            },
            # Variant-only identity (parent linkage by sku), mapped.
            {
                "sku": "VAR-2",
                "parent_sku": "SKU-PUB",
                "shopify_inventory_item_id": "888",
            },
            # Unmapped variant of the unpushed draft.
            {"sku": "SKU-DRAFT", "parent_product_id": "CP3"},
        ]
    )
    return _Db({"catalog_products": products, "catalog_variants": variants})


# ---------------------------------------------------------------------------
# normalize + fail-soft basics
# ---------------------------------------------------------------------------


def test_normalize_sku():
    assert normalize_sku("  AB-1 ") == "AB-1"
    assert normalize_sku(None) == ""
    assert normalize_sku("") == ""
    assert normalize_sku(123) == "123"


def test_everything_failsoft_without_db():
    assert online_status_for_skus(None, ["A"]) == {}
    assert online_variant_targets_for_skus(None, ["A"]) == {}
    assert inventory_items_for_skus(None, ["A"]) == {}
    assert online_mapping_available(None) is False
    assert online_summary(None) == {"configured": False, "reachable": False}


def test_failsoft_on_broken_db_object():
    broken = object()  # neither get_collection nor subscript
    assert online_status_for_skus(broken, ["A"]) == {}
    assert online_variant_targets_for_skus(broken, ["A"]) == {}
    assert online_mapping_available(broken) is False


# ---------------------------------------------------------------------------
# online_status_for_skus (Mongo truth)
# ---------------------------------------------------------------------------


def test_status_pushed_draft_counts_as_online():
    out = online_status_for_skus(_db(), ["SKU-PUSHED"])
    assert out["SKU-PUSHED"]["online"] is True
    assert out["SKU-PUSHED"]["status"] == "DRAFT"
    # The live listed qty is NOT mirrored in IMS: honest unknown, never fake 0.
    assert out["SKU-PUSHED"]["online_stock"] is None


def test_status_published_unpushed_counts_as_online():
    out = online_status_for_skus(_db(), ["SKU-PUB"])
    assert out["SKU-PUB"]["online"] is True
    assert out["SKU-PUB"]["status"] == "PUBLISHED"


def test_status_draft_unpushed_is_not_online():
    out = online_status_for_skus(_db(), ["SKU-DRAFT"])
    assert out["SKU-DRAFT"]["online"] is False


def test_status_keys_result_by_requested_identifier():
    # A store barcode and a GTIN both resolve to the same product, and the
    # result is keyed by the identifier the caller sent (old bridge contract).
    out = online_status_for_skus(_db(), ["00050567", "8901111111111"])
    assert out["00050567"]["online"] is True
    assert out["8901111111111"]["online"] is True


def test_status_variant_resolves_parent_by_sku():
    out = online_status_for_skus(_db(), ["VAR-2"])
    assert out["VAR-2"]["online"] is True
    assert out["VAR-2"]["status"] == "PUBLISHED"


def test_status_unknown_and_blank_skus_skipped():
    out = online_status_for_skus(_db(), ["NOPE", "", None, "SKU-PLAIN"])
    assert "NOPE" not in out
    assert "" not in out
    # No ecom sub-doc and no variant mapping -> unknown -> absent.
    assert "SKU-PLAIN" not in out


# ---------------------------------------------------------------------------
# write-back target resolution (the oversell-guard mapping, audit OS-015)
# ---------------------------------------------------------------------------


def test_targets_resolved_from_catalog_variants(monkeypatch):
    monkeypatch.delenv("SHOPIFY_ONLINE_LOCATION_ID", raising=False)
    out = online_variant_targets_for_skus(_db(), ["SKU-PUSHED"])
    # Variant's own shopify_location_id used when no env override.
    assert out["SKU-PUSHED"] == {
        "inventory_item_id": "999",
        "location_id": "loc-77",
    }


def test_targets_env_location_wins(monkeypatch):
    monkeypatch.setenv("SHOPIFY_ONLINE_LOCATION_ID", "loc-env")
    out = online_variant_targets_for_skus(_db(), ["SKU-PUSHED", "VAR-2"])
    assert out["SKU-PUSHED"]["location_id"] == "loc-env"
    # VAR-2 has no per-variant location; env supplies it.
    assert out["VAR-2"] == {"inventory_item_id": "888", "location_id": "loc-env"}


def test_targets_skip_variant_without_location(monkeypatch):
    # No env, no per-variant location, no integration config -> skipped (the
    # caller treats a missing target as not-online; the writeback layer alerts
    # separately when the SKU IS online).
    monkeypatch.delenv("SHOPIFY_ONLINE_LOCATION_ID", raising=False)
    out = online_variant_targets_for_skus(_db(), ["VAR-2"])
    assert "VAR-2" not in out


def test_targets_unmapped_sku_absent(monkeypatch):
    monkeypatch.setenv("SHOPIFY_ONLINE_LOCATION_ID", "loc-env")
    out = online_variant_targets_for_skus(_db(), ["SKU-DRAFT", "NOPE"])
    assert out == {}


def test_inventory_items_for_skus_maps_by_requested_key():
    out = inventory_items_for_skus(_db(), ["00050567", "VAR-2", "SKU-DRAFT"])
    assert out == {"00050567": "999", "VAR-2": "888"}


# ---------------------------------------------------------------------------
# mapping-available + summary (replaces the retired env check)
# ---------------------------------------------------------------------------


def test_mapping_available_true_when_catalog_mapped():
    assert online_mapping_available(_db()) is True


def test_mapping_available_false_on_empty_catalog():
    empty = _Db({"catalog_products": _Coll([]), "catalog_variants": _Coll([])})
    assert online_mapping_available(empty) is False


def test_online_summary_counts():
    out = online_summary(_db())
    assert out["configured"] is True
    assert out["reachable"] is True
    assert out["online_products"] == 1  # only CP1 carries a gid
    assert out["online_variants"] == 2  # two mapped variants
    assert out["published_products"] == 1
    assert out["draft_products"] == 2


# ---------------------------------------------------------------------------
# retired Postgres reconcile tool stays an honest no-op
# ---------------------------------------------------------------------------


def test_reconcile_store_barcodes_is_retired_noop():
    out = reconcile_store_barcodes({"SKU1": "00012345"}, apply=True)
    assert out["retired"] is True
    assert out["applied"] is False
    assert "deleted" in out["error"]
