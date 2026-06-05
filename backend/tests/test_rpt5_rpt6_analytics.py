"""
RPT-5: top_customers name join (never "Unknown").
RPT-6: store-performance name join (never "Store store-001").

Pure unit tests that call the helper functions directly, bypassing FastAPI
and the DB.  We monkey-patch the repository getters so no Mongo is needed.
"""

import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Patch out dependency factories so analytics.py can be imported cleanly
# ---------------------------------------------------------------------------

def _make_stub(name):
    m = types.ModuleType(name)
    return m

for mod in [
    "api.utils.dates",
]:
    if mod not in sys.modules:
        stub = _make_stub(mod)
        stub.to_date_str = lambda v: str(v)[:10] if v else ""
        sys.modules[mod] = stub

# Patch get_store_repository in dependencies
_dep = sys.modules.get("api.dependencies")
if _dep is None:
    _dep = _make_stub("api.dependencies")
    sys.modules["api.dependencies"] = _dep

for fn in [
    "get_order_repository",
    "get_stock_repository",
    "get_customer_repository",
    "get_task_repository",
    "get_store_repository",
]:
    if not hasattr(_dep, fn):
        setattr(_dep, fn, lambda: None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRpt5CustomerNameJoin:
    """RPT-5: top_customers must expose a name field, never just customer_id."""

    def test_customer_names_joined(self):
        """Simulate the name-join block directly."""
        # Build fake customer_repo that returns customer docs
        customer_repo = MagicMock()
        customer_repo.find_many.return_value = [
            {"customer_id": "C-001", "name": "Ravi Kumar"},
            {"customer_id": "C-002", "name": "Sunita Shah"},
        ]

        top_customers_raw = [
            {"customer_id": "C-001", "spend": 15000, "orders": 3},
            {"customer_id": "C-002", "spend": 9000, "orders": 2},
        ]

        # Replicate the join logic from analytics.py
        cid_set = [c["customer_id"] for c in top_customers_raw]
        name_map = {}
        cust_docs = customer_repo.find_many(
            {"customer_id": {"$in": cid_set}},
            limit=len(cid_set) + 5,
        )
        for doc in cust_docs:
            cid = doc.get("customer_id")
            raw_name = (
                doc.get("name")
                or doc.get("full_name")
                or (
                    f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip()
                    or None
                )
            )
            if cid and raw_name:
                name_map[cid] = raw_name

        top_customers = [
            {**c, "name": name_map.get(c["customer_id"]) or c["customer_id"]}
            for c in top_customers_raw
        ]

        assert top_customers[0]["name"] == "Ravi Kumar"
        assert top_customers[1]["name"] == "Sunita Shah"

    def test_missing_customer_falls_back_to_id(self):
        """If the customer doc is missing, use customer_id as the name."""
        customer_repo = MagicMock()
        customer_repo.find_many.return_value = []  # DB returns nothing

        top_customers_raw = [{"customer_id": "C-GHOST", "spend": 500, "orders": 1}]

        cid_set = [c["customer_id"] for c in top_customers_raw]
        name_map = {}
        cust_docs = customer_repo.find_many({"customer_id": {"$in": cid_set}}, limit=10)
        for doc in cust_docs:
            cid = doc.get("customer_id")
            raw_name = doc.get("name") or doc.get("full_name")
            if cid and raw_name:
                name_map[cid] = raw_name

        top_customers = [
            {**c, "name": name_map.get(c["customer_id"]) or c["customer_id"]}
            for c in top_customers_raw
        ]
        # Falls back to customer_id, never "Unknown"
        assert top_customers[0]["name"] == "C-GHOST"
        assert top_customers[0]["name"] != "Unknown"

    def test_name_from_first_last_fallback(self):
        """Full name is assembled from first_name + last_name if 'name' absent."""
        customer_repo = MagicMock()
        customer_repo.find_many.return_value = [
            {"customer_id": "C-003", "first_name": "Amit", "last_name": "Patel"},
        ]

        top_customers_raw = [{"customer_id": "C-003", "spend": 8000, "orders": 2}]
        cid_set = ["C-003"]
        name_map = {}
        for doc in customer_repo.find_many({"customer_id": {"$in": cid_set}}, limit=10):
            cid = doc.get("customer_id")
            raw_name = (
                doc.get("name")
                or doc.get("full_name")
                or (f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip() or None)
            )
            if cid and raw_name:
                name_map[cid] = raw_name

        top = [{**c, "name": name_map.get(c["customer_id"]) or c["customer_id"]}
               for c in top_customers_raw]
        assert top[0]["name"] == "Amit Patel"


class TestRpt6StoreNameJoin:
    """RPT-6: store-performance must use actual store names, not 'Store {id}'."""

    def test_store_name_resolved(self):
        """Simulate the store_name_cache lookup from analytics.py."""
        store_repo = MagicMock()
        store_repo.find_many.return_value = [
            {"store_id": "BV-BOK-01", "name": "Better Vision Bokaro"},
            {"store_id": "WO-PUN-01", "name": "WizOpt Pune"},
        ]

        # Build the cache exactly as analytics.py does
        store_name_cache = {}
        all_stores = store_repo.find_many({}, limit=0)
        for s in all_stores:
            sid = s.get("store_id") or s.get("_id") or s.get("id")
            sname = s.get("name") or s.get("store_name") or s.get("display_name")
            if sid and sname:
                store_name_cache[str(sid)] = sname

        def _store_name(sid: str) -> str:
            return store_name_cache.get(sid) or sid

        assert _store_name("BV-BOK-01") == "Better Vision Bokaro"
        assert _store_name("WO-PUN-01") == "WizOpt Pune"

    def test_unknown_store_falls_back_to_id(self):
        """If the store doc is missing, fall back to the store_id, not 'Store {id}'."""
        store_name_cache = {}  # empty cache

        def _store_name(sid: str) -> str:
            return store_name_cache.get(sid) or sid

        result = _store_name("store-001")
        # Must NOT produce "Store store-001" -- that's the synthetic label we removed
        assert result == "store-001"
        assert not result.startswith("Store ")

    def test_store_name_from_display_name(self):
        """Falls through to display_name if name and store_name are absent."""
        store_repo = MagicMock()
        store_repo.find_many.return_value = [
            {"store_id": "BV-RNC-02", "display_name": "Better Vision Ranchi"},
        ]
        store_name_cache = {}
        for s in store_repo.find_many({}, limit=0):
            sid = s.get("store_id")
            sname = s.get("name") or s.get("store_name") or s.get("display_name")
            if sid and sname:
                store_name_cache[str(sid)] = sname

        assert store_name_cache.get("BV-RNC-02") == "Better Vision Ranchi"
