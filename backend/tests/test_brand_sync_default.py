"""
IMS 2.0 - Brand Master `sync_to_shopify_default` + brand-driven reads
=====================================================================
Covers the 2026-07-04 Brand Master upgrades:

  1. admin_catalog: BrandCreate persists sync_to_shopify_default (default
     False), BrandUpdate can flip it (incl. explicitly back to False).
  2. catalog_dictionary.load_brand_sync_default: True only for an ACTIVE
     brand doc with the flag True; fail-soft False otherwise.
  3. GET /products/brand-options exposes sync_to_shopify_default per brand.
  4. The FORM create door stamps `sync_to_shopify` on the spine: explicit
     payload value wins; omitted -> brand default; unknown brand -> False.
     (INTENT only -- nothing pushes to Shopify from IMS; BVI owns Shopify.)
  5. GET /products/brands (catalog.py) reads brand_masters, falling back to
     the legacy hardcoded BRANDS dict when the master is empty/unreadable.
  6. admin_catalog._attach_product_counts: one aggregation, case-insensitive
     attributes.brand_name match, fail-soft omission.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_brand_sync_default.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import admin_catalog as ac  # noqa: E402
from api.routers import catalog as cat  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from api.services import catalog_dictionary as cd  # noqa: E402
from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes (mirrors test_subbrands.py)
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, docs, agg_rows=None):
        self._docs = [dict(d) for d in docs]
        self._agg_rows = agg_rows
        self.updates = []

    def find(self, query=None, projection=None):
        query = query or {}
        out = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False
                elif isinstance(v, dict) and "$ne" in v:
                    if d.get(k) == v["$ne"]:
                        ok = False
                elif d.get(k) != v:
                    ok = False
            if ok:
                out.append(dict(d))
        return out

    def find_one(self, query=None, projection=None):
        res = self.find(query)
        return res[0] if res else None

    def insert_one(self, doc):
        self._docs.append(dict(doc))

    def update_one(self, query, update):
        self.updates.append((query, update))
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                break

    def aggregate(self, pipeline):
        if self._agg_rows is None:
            raise RuntimeError("aggregate unsupported")
        return list(self._agg_rows)


class _FakeDb:
    is_connected = True

    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name) or _FakeColl([])


_BRANDS = [
    {"brand_id": "b1", "name": "Ray-Ban", "categories": ["SG", "FR"],
     "tier": "PREMIUM", "is_active": True, "sync_to_shopify_default": True},
    {"brand_id": "b2", "name": "Titan", "categories": ["WT"],
     "tier": "MASS", "is_active": True, "sync_to_shopify_default": False},
    {"brand_id": "b3", "name": "NoFlag", "categories": [],
     "tier": "MASS", "is_active": True},
]


def _db():
    return _FakeDb({cd.BRAND_COLLECTION: _FakeColl(_BRANDS)})


# ---------------------------------------------------------------------------
# 1. Admin CRUD persists the flag
# ---------------------------------------------------------------------------


class TestBrandCrudPersistsFlag:
    def _patch(self, monkeypatch):
        brands = _FakeColl([])
        monkeypatch.setattr(
            ac, "_coll",
            lambda name: brands if name == "brand_masters" else None,
        )
        return brands

    def test_create_defaults_false_and_persists_true(self, monkeypatch):
        coll = self._patch(monkeypatch)
        base = {"name": "Vogue", "code": "VOGUE", "tier": "MASS"}
        created = asyncio.run(ac.create_brand(ac.BrandCreate(**base)))
        assert created["sync_to_shopify_default"] is False

        created2 = asyncio.run(ac.create_brand(ac.BrandCreate(
            name="Prada", code="PRADA", tier="LUXURY",
            sync_to_shopify_default=True,
        )))
        assert created2["sync_to_shopify_default"] is True
        stored = coll.find_one({"code": "PRADA"})
        assert stored["sync_to_shopify_default"] is True

    def test_update_flips_flag_both_ways(self, monkeypatch):
        coll = self._patch(monkeypatch)
        created = asyncio.run(ac.create_brand(ac.BrandCreate(
            name="Vogue", code="VOGUE", tier="MASS",
        )))
        bid = created["brand_id"]
        up = asyncio.run(ac.update_brand(
            bid, ac.BrandUpdate(sync_to_shopify_default=True)
        ))
        assert up["sync_to_shopify_default"] is True
        # Explicit False must persist too (exclude_unset keeps it).
        up2 = asyncio.run(ac.update_brand(
            bid, ac.BrandUpdate(sync_to_shopify_default=False)
        ))
        assert up2["sync_to_shopify_default"] is False
        assert coll.find_one({"brand_id": bid})["sync_to_shopify_default"] is False


# ---------------------------------------------------------------------------
# 2. Loader semantics
# ---------------------------------------------------------------------------


class TestLoadBrandSyncDefault:
    def test_true_only_when_flag_true(self):
        assert cd.load_brand_sync_default(_db(), "Ray-Ban") is True
        assert cd.load_brand_sync_default(_db(), "ray-ban") is True  # ci match
        assert cd.load_brand_sync_default(_db(), "Titan") is False
        assert cd.load_brand_sync_default(_db(), "NoFlag") is False

    def test_fail_soft_false(self):
        assert cd.load_brand_sync_default(None, "Ray-Ban") is False
        assert cd.load_brand_sync_default(_db(), "Unknown") is False
        assert cd.load_brand_sync_default(_db(), "") is False

        class _Boom:
            def get_collection(self, name):
                raise RuntimeError("down")

        assert cd.load_brand_sync_default(_Boom(), "Ray-Ban") is False


# ---------------------------------------------------------------------------
# 3. /products/brand-options exposes the flag
# ---------------------------------------------------------------------------


class TestBrandOptionsExposesFlag:
    def test_flag_included_per_brand(self, monkeypatch):
        monkeypatch.setattr(deps, "get_db", lambda: _db())
        out = asyncio.run(prod_router.get_brand_options(
            category=None, current_user={"user_id": "u1"}
        ))
        by_name = {b["name"]: b for b in out["brands"]}
        assert by_name["Ray-Ban"]["sync_to_shopify_default"] is True
        assert by_name["Titan"]["sync_to_shopify_default"] is False
        assert by_name["NoFlag"]["sync_to_shopify_default"] is False


# ---------------------------------------------------------------------------
# 4. Create door stamps sync_to_shopify on the spine
# ---------------------------------------------------------------------------


def _form_product(**over):
    base = {
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
    }
    base.update(over)
    return prod_router.ProductCreate(**base)


class TestCreateDoorSyncStamp:
    @pytest.fixture(autouse=True)
    def _mirror_off(self, monkeypatch):
        monkeypatch.setenv("PM_MIRROR_ENABLED", "")
        yield

    def _create(self, monkeypatch, product):
        repo = ProductRepository(MockCollection("products"))
        monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
        monkeypatch.setattr(deps, "get_db", lambda: _db())
        monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
        return prod_router._create_via_canonical_door(
            product, {"user_id": "tester"}, source="FORM"
        )

    def test_brand_default_true_resolved_when_omitted(self, monkeypatch):
        created = self._create(monkeypatch, _form_product())
        assert created["sync_to_shopify"] is True  # Ray-Ban default True

    def test_brand_default_false_resolved_when_omitted(self, monkeypatch):
        created = self._create(
            monkeypatch,
            _form_product(brand="Titan", category="WRIST_WATCH",
                          attributes={"dial_size": "42mm"}, model="Raga-1"),
        )
        assert created["sync_to_shopify"] is False

    def test_explicit_value_wins_over_brand_default(self, monkeypatch):
        created = self._create(
            monkeypatch, _form_product(sync_to_shopify=False)
        )
        assert created["sync_to_shopify"] is False  # despite brand True

    def test_unknown_brand_fails_soft_false(self, monkeypatch):
        # An EMPTY Brand Master fails open at the dictionary gate (any brand
        # is creatable) -- the sync default then resolves fail-soft to False.
        repo = ProductRepository(MockCollection("products"))
        monkeypatch.setattr(prod_router, "get_product_repository", lambda: repo)
        monkeypatch.setattr(
            deps, "get_db",
            lambda: _FakeDb({cd.BRAND_COLLECTION: _FakeColl([])}),
        )
        monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
        created = prod_router._create_via_canonical_door(
            _form_product(brand="Mystery"), {"user_id": "tester"}, source="FORM"
        )
        assert created["sync_to_shopify"] is False

    def test_resolver_fail_soft_without_db(self, monkeypatch):
        monkeypatch.setattr(deps, "get_db", lambda: None)
        assert prod_router._resolve_sync_to_shopify(_form_product(), None) is False
        assert prod_router._resolve_sync_to_shopify(
            _form_product(sync_to_shopify=True), None
        ) is True


# ---------------------------------------------------------------------------
# 5. GET /products/brands reads brand_masters with hardcoded fallback
# ---------------------------------------------------------------------------


class TestCatalogBrandsFromMaster:
    def _call(self, monkeypatch, db, category=None):
        monkeypatch.setattr(deps, "get_db", lambda: db)
        return asyncio.run(cat.get_brands(
            category=category, current_user={"user_id": "u1"}
        ))

    def test_master_names_returned(self, monkeypatch):
        out = self._call(monkeypatch, _db())
        assert set(out["brands"]) == {"Ray-Ban", "Titan", "NoFlag"}

    def test_category_prefix_filter(self, monkeypatch):
        out = self._call(
            monkeypatch, _db(), category=cat.ProductCategory.WRIST_WATCH
        )
        # Titan (WT) + NoFlag (empty categories = applies to all).
        assert set(out["brands"]) == {"NoFlag", "Titan"}

    def test_empty_master_falls_back_to_hardcoded(self, monkeypatch):
        empty = _FakeDb({cd.BRAND_COLLECTION: _FakeColl([])})
        out = self._call(monkeypatch, empty, category=cat.ProductCategory.FRAME)
        assert out["brands"] == cat.BRANDS.get("frames", [])

    def test_no_db_falls_back_to_hardcoded_all(self, monkeypatch):
        out = self._call(monkeypatch, None)
        expect = set()
        for lst in cat.BRANDS.values():
            expect.update(lst)
        assert set(out["brands"]) == expect


# ---------------------------------------------------------------------------
# 6. Product-count badge aggregation
# ---------------------------------------------------------------------------


class TestBrandProductCounts:
    def test_counts_matched_case_insensitively(self, monkeypatch):
        products = _FakeColl([], agg_rows=[
            {"_id": "ray-ban", "count": 7},
            {"_id": "", "count": 3},
        ])
        monkeypatch.setattr(
            ac, "_coll", lambda name: products if name == "products" else None
        )
        brands = [{"name": "Ray-Ban"}, {"name": "Titan"}]
        out = ac._attach_product_counts(brands)
        assert out[0]["product_count"] == 7
        assert out[1]["product_count"] == 0

    def test_fail_soft_omits_counts(self, monkeypatch):
        products = _FakeColl([])  # aggregate raises
        monkeypatch.setattr(
            ac, "_coll", lambda name: products if name == "products" else None
        )
        out = ac._attach_product_counts([{"name": "Ray-Ban"}])
        assert "product_count" not in out[0]

    def test_no_db_omits_counts(self, monkeypatch):
        monkeypatch.setattr(ac, "_coll", lambda name: None)
        out = ac._attach_product_counts([{"name": "Ray-Ban"}])
        assert "product_count" not in out[0]
