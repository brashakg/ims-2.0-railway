"""
IMS 2.0 - Cataloguer attribution test suite
============================================
Owner requirement: multiple staff catalogue products; every product must
durably record WHO created it and WHO last edited it (user_id + display name)
so cataloguing performance can be measured and mistakes traced.

Covers:
  * create stamps created_by + created_by_name through the canonical door
    (create_via_door -> create_product -> normalise_payload), including the
    fail-soft users-collection name fallback for doors that only carry the id
  * update (product_master.update_product) stamps updated_by + updated_by_name
    and NEVER rewrites created_by / created_by_name
  * PUT /products/{id} (router path) stamps updated_by + updated_by_name
  * GET /products?created_by=... threads the filter through every repo branch
    (default / category / brand / search) as an equality Mongo filter
  * GET /products/cataloguers aggregates per-user counts, falls back to the
    raw id when no name is known, and is role-gated (403 for SALES_STAFF)
  * GET /inventory/stock created_by filter: catalog query narrowed, stranded
    rows respect the filter, ledger rows carry created_by/_name passthrough

All service/handler-level tests run with in-memory fakes (no DB needed); the
role-gate tests use the shared TestClient + JWT fixtures, and the RBAC policy
row is asserted via check_access so the CI route-coverage lock stays green.
"""

# pylint: disable=redefined-outer-name,unused-argument,protected-access

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.services import product_master as pm  # noqa: E402


# ============================================================================
# In-memory fakes
# ============================================================================


class FakeProductRepo:
    """Minimal in-memory stand-in for ProductRepository (create/update path)."""

    def __init__(self, docs: Optional[List[Dict]] = None):
        self.docs: Dict[str, Dict] = {}
        for d in docs or []:
            self.docs[d["product_id"]] = dict(d)
        self.last_find_many_filter: Optional[Dict] = None
        self.updates: List[Dict] = []

    # --- create path -------------------------------------------------------
    def find_by_sku(self, sku):
        for d in self.docs.values():
            if d.get("sku") == sku:
                return dict(d)
        return None

    def find_by_identity_key(self, identity_key):
        return None

    def find_by_barcode(self, barcode):
        return None

    def create(self, data, *, raise_on_duplicate=False):
        doc = dict(data)
        doc.setdefault("product_id", f"pid-{len(self.docs) + 1}")
        self.docs[doc["product_id"]] = doc
        return dict(doc)

    # --- update path ---------------------------------------------------------
    def find_by_id(self, pid):
        d = self.docs.get(pid)
        return dict(d) if d else None

    def update(self, pid, data):
        if pid not in self.docs:
            return False
        self.docs[pid].update(data)
        self.updates.append({"product_id": pid, "data": dict(data)})
        return True

    # --- list path ----------------------------------------------------------
    def find_many(self, flt=None, sort=None, skip=0, limit=100):
        self.last_find_many_filter = dict(flt or {})
        rows = [dict(d) for d in self.docs.values() if self._matches(d, flt)]
        return rows[skip : skip + limit]

    def count(self, flt=None):
        return len([d for d in self.docs.values() if self._matches(d, flt)])

    @staticmethod
    def _matches(doc, flt):
        for k, v in (flt or {}).items():
            if isinstance(v, dict):
                continue  # operator clauses not needed by these tests
            if doc.get(k) != v:
                return False
        return True


class FakeUsersCollection:
    """users collection stub: user_id -> {username, full_name}."""

    def __init__(self, users: Dict[str, Dict]):
        self._users = users

    def find_one(self, flt, projection=None):
        rec = self._users.get(flt.get("user_id"))
        return dict(rec) if rec else None

    def find(self, flt, projection=None):
        ids = (flt.get("user_id") or {}).get("$in") or []
        for uid in ids:
            rec = self._users.get(uid)
            if rec:
                yield {"user_id": uid, **rec}


class FakeDB:
    """db connection stub exposing get_collection('users')."""

    def __init__(self, users: Dict[str, Dict]):
        self._users = FakeUsersCollection(users)
        self.is_connected = True

    def get_collection(self, name):
        if name == "users":
            return self._users
        return None


def _frame_payload(**overrides) -> Dict[str, Any]:
    base = {
        "category": "FRAME",
        "brand": "Acme",
        "model": "M100",
        "color": "BLK",
        "mrp": 1000.0,
        "offer_price": 900.0,
        "sku": "FR-ATTRIB-1",
    }
    base.update(overrides)
    return base


# ============================================================================
# 1. Create stamps created_by + created_by_name
# ============================================================================


class TestCreateStampsAttribution:
    def test_normalise_payload_stamps_both(self):
        doc = pm.normalise_payload(
            category="FRAME",
            attributes={"brand_name": "Acme", "model_no": "M1", "colour_code": "BLK"},
            mrp=1000.0,
            offer_price=900.0,
            sku="FR-NP-1",
            created_by="u-77",
            created_by_name="ramesh",
        )
        assert doc["created_by"] == "u-77"
        assert doc["created_by_name"] == "ramesh"

    def test_normalise_payload_omits_name_when_unknown(self):
        # Backward-compatible: docs without a known name simply lack the key
        # (exactly like every pre-feature legacy doc).
        doc = pm.normalise_payload(
            category="FRAME",
            attributes={"brand_name": "Acme", "model_no": "M2", "colour_code": "BLK"},
            mrp=1000.0,
            offer_price=900.0,
            sku="FR-NP-2",
            created_by="u-77",
        )
        assert doc["created_by"] == "u-77"
        assert "created_by_name" not in doc

    def test_create_via_door_stamps_actor_name(self):
        repo = FakeProductRepo()
        created = pm.create_via_door(
            _frame_payload(),
            source="FORM",
            actor="u-11",
            actor_name="priya",
            product_repo=repo,
        )
        assert created["created_by"] == "u-11"
        assert created["created_by_name"] == "priya"
        # persisted, not just echoed
        stored = repo.docs[created["product_id"]]
        assert stored["created_by"] == "u-11"
        assert stored["created_by_name"] == "priya"

    def test_create_via_door_resolves_name_from_users_when_not_passed(self):
        # Doors that only carry the id (catalog promote / import) still get the
        # display name via the fail-soft users lookup.
        repo = FakeProductRepo()
        db = FakeDB({"u-22": {"username": "suresh", "full_name": "Suresh K"}})
        created = pm.create_via_door(
            _frame_payload(sku="FR-ATTRIB-2"),
            source="IMPORT",
            actor="u-22",
            product_repo=repo,
            db=db,
        )
        assert created["created_by"] == "u-22"
        assert created["created_by_name"] == "suresh"

    def test_create_without_db_or_name_is_fail_soft(self):
        repo = FakeProductRepo()
        created = pm.create_via_door(
            _frame_payload(sku="FR-ATTRIB-3"),
            source="BULK",
            actor="u-33",
            product_repo=repo,
        )
        assert created["created_by"] == "u-33"
        assert "created_by_name" not in created

    def test_resolve_actor_name_fail_soft(self):
        assert pm.resolve_actor_name(None, db=FakeDB({})) is None
        assert pm.resolve_actor_name("u-1", db=None) is None
        assert pm.resolve_actor_name("unknown-user", db=FakeDB({})) is None
        assert (
            pm.resolve_actor_name("u-9", db=FakeDB({"u-9": {"username": "asha"}}))
            == "asha"
        )
        # full_name fallback when username is blank
        assert (
            pm.resolve_actor_name(
                "u-8", db=FakeDB({"u-8": {"username": "", "full_name": "Rina D"}})
            )
            == "Rina D"
        )

    def test_build_product_data_stamps_name(self):
        from api.routers.products import ProductCreate, _build_product_data

        product = ProductCreate(
            sku="FR-BPD-1",
            category="FRAME",
            brand="Acme",
            model="M1",
            color="BLK",
            mrp=1000.0,
            offer_price=900.0,
        )
        data = _build_product_data(product, created_by="u-1", created_by_name="ramesh")
        assert data["created_by"] == "u-1"
        assert data["created_by_name"] == "ramesh"
        # legacy call shape (no name) unchanged
        data2 = _build_product_data(product, created_by="u-1")
        assert "created_by_name" not in data2


# ============================================================================
# 2. Update stamps updated_by + updated_by_name and preserves creation stamp
# ============================================================================


class TestUpdateStampsAttribution:
    def _seed_repo(self):
        return FakeProductRepo(
            [
                {
                    "product_id": "pid-1",
                    "sku": "FR-UP-1",
                    "category": "FRAME",
                    "mrp": 1000.0,
                    "offer_price": 900.0,
                    "is_active": True,
                    "created_by": "u-creator",
                    "created_by_name": "creator",
                }
            ]
        )

    def test_update_product_stamps_editor(self):
        repo = self._seed_repo()
        updated = pm.update_product(
            product_id="pid-1",
            patch={"mrp": 1200.0},
            actor="u-editor",
            actor_name="editor",
            product_repo=repo,
        )
        assert updated["updated_by"] == "u-editor"
        assert updated["updated_by_name"] == "editor"
        # creation attribution untouched
        assert updated["created_by"] == "u-creator"
        assert updated["created_by_name"] == "creator"

    def test_update_product_never_rewrites_created_by(self):
        repo = self._seed_repo()
        updated = pm.update_product(
            product_id="pid-1",
            patch={
                "mrp": 1100.0,
                "created_by": "u-hijack",
                "created_by_name": "hijack",
            },
            actor="u-editor",
            actor_name="editor",
            product_repo=repo,
        )
        assert updated["created_by"] == "u-creator"
        assert updated["created_by_name"] == "creator"

    def test_update_product_resolves_name_via_users_fallback(self):
        repo = self._seed_repo()
        db = FakeDB({"u-editor": {"username": "meena"}})
        updated = pm.update_product(
            product_id="pid-1",
            patch={"mrp": 1050.0},
            actor="u-editor",
            product_repo=repo,
            db=db,
        )
        assert updated["updated_by"] == "u-editor"
        assert updated["updated_by_name"] == "meena"

    def test_router_put_stamps_updated_by_name(self, monkeypatch):
        # PUT /products/{id} is the validated spine-update door the FE uses.
        from api.routers import products as products_mod

        repo = self._seed_repo()
        monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
        # Restamp + collection refresh are DB-touching fail-softs; neutralise.
        monkeypatch.setattr(
            products_mod._pm, "apply_restamp_atomic", lambda *a, **k: {}
        )
        monkeypatch.setattr(
            products_mod, "_refresh_collections_after_product", lambda *a, **k: None
        )

        body = products_mod.ProductUpdate(mrp=1300.0)
        out = asyncio.run(
            products_mod.update_product(
                "pid-1",
                body,
                current_user={
                    "user_id": "u-editor",
                    "username": "editor",
                    "roles": ["ADMIN"],
                },
            )
        )
        assert out["product_id"] == "pid-1"
        stored = repo.docs["pid-1"]
        assert stored["updated_by"] == "u-editor"
        assert stored["updated_by_name"] == "editor"
        assert stored["created_by"] == "u-creator"
        assert stored["created_by_name"] == "creator"


# ============================================================================
# 3. GET /products?created_by= filter threading
# ============================================================================


class FakeListRepo(FakeProductRepo):
    """Extends the fake with the list/search methods list_products calls,
    recording the created_by each branch received."""

    def __init__(self, docs=None):
        super().__init__(docs)
        self.calls: List[Dict] = []

    def search_products(self, query, category=None, *, is_active=True,
                        created_by=None, skip=0, limit=100):
        self.calls.append({"branch": "search", "created_by": created_by})
        return [d for d in self.docs.values()
                if not created_by or d.get("created_by") == created_by]

    def count_search_products(self, query, category=None, *, is_active=True,
                              created_by=None):
        return len([d for d in self.docs.values()
                    if not created_by or d.get("created_by") == created_by])

    def find_by_brand(self, brand, category=None, *, is_active=True,
                      created_by=None, skip=0, limit=100):
        self.calls.append({"branch": "brand", "created_by": created_by})
        return [d for d in self.docs.values()
                if d.get("brand") == brand
                and (not created_by or d.get("created_by") == created_by)]

    def count_by_brand(self, brand, category=None, *, is_active=True,
                       created_by=None):
        return len(self.find_by_brand(brand, category, is_active=is_active,
                                      created_by=created_by))

    def find_by_category(self, category, active_only=True, *, is_active=True,
                         created_by=None, skip=0, limit=100):
        self.calls.append({"branch": "category", "created_by": created_by})
        return [d for d in self.docs.values()
                if d.get("category") == category
                and (not created_by or d.get("created_by") == created_by)]

    def count_by_category(self, category, *, is_active=True, created_by=None):
        return len(self.find_by_category(category, is_active=is_active,
                                         created_by=created_by))


def _list_docs():
    return [
        {"product_id": "p1", "sku": "FR-1", "category": "FRAME", "brand": "Acme",
         "is_active": True, "created_by": "u-a", "created_by_name": "asha"},
        {"product_id": "p2", "sku": "FR-2", "category": "FRAME", "brand": "Acme",
         "is_active": True, "created_by": "u-b", "created_by_name": "bala"},
        {"product_id": "p3", "sku": "SG-1", "category": "SUNGLASS", "brand": "Zen",
         "is_active": True},  # legacy: no attribution
    ]


def _run_list(monkeypatch, repo, **kwargs):
    import uuid

    from api.routers import products as products_mod

    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
    params: Dict[str, Any] = {
        "category": None,
        "brand": None,
        "search": None,
        "tag": None,
        "created_by": None,
        # unique store per call keeps the process-global cache from serving a
        # stale entry across assertions (store id is part of the cache key)
        "store_id": f"S-attrib-{uuid.uuid4().hex[:10]}",
        "skip": 0,
        "limit": 50,
        "is_active": "all",
        "current_user": {"user_id": "u-x", "username": "x", "roles": ["ADMIN"]},
    }
    params.update(kwargs)
    return asyncio.run(products_mod.list_products(**params))


class TestListProductsCreatedByFilter:
    def test_default_branch_filters_by_created_by(self, monkeypatch):
        repo = FakeListRepo(_list_docs())
        out = _run_list(monkeypatch, repo, created_by="u-a")
        assert {p["product_id"] for p in out["products"]} == {"p1"}
        assert out["total_count"] == 1
        assert repo.last_find_many_filter.get("created_by") == "u-a"

    def test_default_branch_without_filter_unchanged(self, monkeypatch):
        repo = FakeListRepo(_list_docs())
        out = _run_list(monkeypatch, repo)
        assert {p["product_id"] for p in out["products"]} == {"p1", "p2", "p3"}
        assert "created_by" not in (repo.last_find_many_filter or {})

    def test_category_branch_threads_created_by(self, monkeypatch):
        repo = FakeListRepo(_list_docs())
        out = _run_list(monkeypatch, repo, category="FRAME", created_by="u-b")
        assert {p["product_id"] for p in out["products"]} == {"p2"}
        assert repo.calls[-1] == {"branch": "category", "created_by": "u-b"}

    def test_brand_branch_threads_created_by(self, monkeypatch):
        repo = FakeListRepo(_list_docs())
        out = _run_list(monkeypatch, repo, brand="Acme", created_by="u-a")
        assert {p["product_id"] for p in out["products"]} == {"p1"}
        assert repo.calls[-1] == {"branch": "brand", "created_by": "u-a"}

    def test_search_branch_threads_created_by(self, monkeypatch):
        repo = FakeListRepo(_list_docs())
        out = _run_list(monkeypatch, repo, search="FR", created_by="u-b")
        assert {p["product_id"] for p in out["products"]} == {"p2"}
        assert repo.calls[-1] == {"branch": "search", "created_by": "u-b"}

    def test_list_rows_carry_attribution_fields(self, monkeypatch):
        # list_products returns full docs, so created_by / created_by_name ride
        # along for the FE without a projection change.
        repo = FakeListRepo(_list_docs())
        out = _run_list(monkeypatch, repo)
        byid = {p["product_id"]: p for p in out["products"]}
        assert byid["p1"]["created_by_name"] == "asha"
        assert "created_by_name" not in byid["p3"]  # legacy row: absent, not fabricated


# ============================================================================
# 4. GET /products/cataloguers
# ============================================================================


class FakeStatsRepo(FakeProductRepo):
    def __init__(self, rows):
        super().__init__()
        self._rows = rows

    def cataloguer_stats(self):
        return [dict(r) for r in self._rows]


class TestCataloguersEndpoint:
    def test_aggregates_counts_and_falls_back_to_id(self, monkeypatch):
        from api.routers import products as products_mod
        from api import dependencies as deps_mod

        rows = [
            {"_id": "u-a", "name": "asha", "created_count": 7,
             "last_created_at": None},
            {"_id": "u-legacy", "name": None, "created_count": 3,
             "last_created_at": None},
        ]
        monkeypatch.setattr(
            products_mod, "get_product_repository", lambda: FakeStatsRepo(rows)
        )
        # u-legacy has no stamped name and is NOT in users -> raw id fallback.
        monkeypatch.setattr(deps_mod, "get_db", lambda: FakeDB({}))
        out = asyncio.run(
            products_mod.list_cataloguers(
                current_user={"user_id": "m", "roles": ["ADMIN"]}
            )
        )
        cats = {c["user_id"]: c for c in out["cataloguers"]}
        assert cats["u-a"]["name"] == "asha"
        assert cats["u-a"]["created_count"] == 7
        assert cats["u-legacy"]["name"] == "u-legacy"

    def test_legacy_names_resolved_via_users_batch(self, monkeypatch):
        from api.routers import products as products_mod
        from api import dependencies as deps_mod

        rows = [
            {"_id": "u-old", "name": None, "created_count": 2,
             "last_created_at": None},
        ]
        monkeypatch.setattr(
            products_mod, "get_product_repository", lambda: FakeStatsRepo(rows)
        )
        monkeypatch.setattr(
            deps_mod, "get_db", lambda: FakeDB({"u-old": {"username": "kiran"}})
        )
        out = asyncio.run(
            products_mod.list_cataloguers(
                current_user={"user_id": "m", "roles": ["ADMIN"]}
            )
        )
        assert out["cataloguers"][0]["name"] == "kiran"

    def test_no_repo_is_fail_soft(self, monkeypatch):
        from api.routers import products as products_mod

        monkeypatch.setattr(products_mod, "get_product_repository", lambda: None)
        out = asyncio.run(
            products_mod.list_cataloguers(
                current_user={"user_id": "m", "roles": ["ADMIN"]}
            )
        )
        assert out == {"cataloguers": []}

    def test_rbac_policy_row(self):
        # The CI route-coverage lock (test_no_uncatalogued_routes) requires a
        # POLICY row; assert its semantics here too.
        from api.services.rbac_policy import check_access

        path = "/api/v1/products/cataloguers"
        assert check_access("GET", path, ["SUPERADMIN"]) is True
        assert check_access("GET", path, ["ADMIN"]) is True
        assert check_access("GET", path, ["AREA_MANAGER"]) is True
        assert check_access("GET", path, ["STORE_MANAGER"]) is True
        assert check_access("GET", path, ["CATALOG_MANAGER"]) is True
        assert check_access("GET", path, ["SALES_STAFF"]) is False
        assert check_access("GET", path, ["OPTOMETRIST"]) is False

    def test_sales_staff_gets_403(self, client, staff_headers):
        resp = client.get("/api/v1/products/cataloguers", headers=staff_headers)
        assert resp.status_code == 403

    def test_superadmin_gets_200_shape(self, client, auth_headers):
        resp = client.get("/api/v1/products/cataloguers", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "cataloguers" in body
        assert isinstance(body["cataloguers"], list)


# ============================================================================
# 5. Inventory stock ledger: created_by filter + attribution passthrough
# ============================================================================


class FakeStockRepo:
    """stock_units stand-in: only .collection.aggregate is used by the ledger."""

    class _Coll:
        def __init__(self, rows):
            self._rows = rows

        def aggregate(self, pipeline):
            return list(self._rows)

    def __init__(self, rows=None):
        self.collection = self._Coll(rows or [])


class TestInventoryLedgerCreatedByFilter:
    def _products(self):
        return [
            {"product_id": "p1", "sku": "FR-1", "category": "FRAME",
             "brand": "Acme", "model": "M1", "mrp": 1000, "offer_price": 900,
             "is_active": True, "created_by": "u-a", "created_by_name": "asha"},
            {"product_id": "p2", "sku": "FR-2", "category": "FRAME",
             "brand": "Acme", "model": "M2", "mrp": 1000, "offer_price": 900,
             "is_active": True, "created_by": "u-b", "created_by_name": "bala"},
        ]

    def test_filter_narrows_catalog_query_and_rows(self, monkeypatch):
        from api.routers import inventory as inv_mod

        # No GRN join noise.
        monkeypatch.setattr(inv_mod, "_last_grn_by_product", lambda sid: {})
        product_repo = FakeProductRepo(self._products())
        items = inv_mod._build_store_ledger(
            FakeStockRepo(), product_repo, "S1", created_by="u-a"
        )
        # Index-friendly: pushed into the Mongo filter, not post-filtered.
        assert product_repo.last_find_many_filter.get("created_by") == "u-a"
        assert [i["product_id"] for i in items] == ["p1"]
        assert items[0]["created_by_name"] == "asha"

    def test_no_filter_keeps_all_rows_with_attribution(self, monkeypatch):
        from api.routers import inventory as inv_mod

        monkeypatch.setattr(inv_mod, "_last_grn_by_product", lambda sid: {})
        product_repo = FakeProductRepo(self._products())
        items = inv_mod._build_store_ledger(FakeStockRepo(), product_repo, "S1")
        assert "created_by" not in (product_repo.last_find_many_filter or {})
        names = {i["product_id"]: i.get("created_by_name") for i in items}
        assert names == {"p1": "asha", "p2": "bala"}

    def test_stranded_units_respect_filter(self, monkeypatch):
        from api.routers import inventory as inv_mod

        monkeypatch.setattr(inv_mod, "_last_grn_by_product", lambda sid: {})
        # p-stranded has stock units but is NOT in the active catalog; it was
        # created by u-b, so a u-a filter must exclude it.
        stranded = {
            "product_id": "p-stranded", "sku": "SG-9", "category": "SUNGLASS",
            "brand": "Zen", "model": "Z9", "mrp": 500, "offer_price": 450,
            "is_active": False, "created_by": "u-b", "created_by_name": "bala",
        }
        product_repo = FakeProductRepo(self._products() + [stranded])
        # Aggregation row shape mirrors the $group in _build_store_ledger.
        stock_rows = [
            {"_id": {"product_id": "p-stranded", "status": "AVAILABLE"},
             "qty": 2, "barcode": "B1", "location_code": "L1"},
        ]
        items_a = inv_mod._build_store_ledger(
            FakeStockRepo(stock_rows), product_repo, "S1", created_by="u-a"
        )
        assert "p-stranded" not in {i["product_id"] for i in items_a}
        items_b = inv_mod._build_store_ledger(
            FakeStockRepo(stock_rows), product_repo, "S1", created_by="u-b"
        )
        assert "p-stranded" in {i["product_id"] for i in items_b}

    def test_get_stock_endpoint_accepts_created_by(self, client, auth_headers):
        # End-to-end param acceptance (works with or without a live DB: no DB
        # -> {"items": [], "total": 0}; CI mongo -> filtered ledger).
        resp = client.get(
            "/api/v1/inventory/stock",
            params={"store_id": "BV-TEST-01", "created_by": "u-nobody"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["items"])
        # Every returned row (if any) must match the requested cataloguer.
        assert all(i.get("created_by") == "u-nobody" for i in body["items"])
