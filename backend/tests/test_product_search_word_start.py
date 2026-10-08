"""The till and the counter lookup find multi-word names (owner 2026-10-08).

Every typed word must START A WORD of a product's brand or model (the field
start, or after a space, hyphen, slash, dot or underscore), so "Air Optix",
"Acuvue Oasys" and "Ray Ban Aviator" are found while "ray" still never finds
"Spray Cleaner" or "Gunmetal Gray". Codes (SKU, variant, barcode) keep matching
from their start, and a field-start hit still comes first. Driven through GET
/api/v1/products?search= (the till's product query; the counter lookup runs the
same search_products) over mongomock, and once over the fallback mock DB that
local no-Mongo mode runs on.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_product_search_word_start.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.dependencies as deps  # noqa: E402
import api.services.cache as cache_mod  # noqa: E402
from api.routers import products_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

_CASHIER = {"user_id": "u-c", "username": "cashier", "roles": ["SALES_STAFF"],
            "active_store_id": "BV-DHN-02", "store_ids": ["BV-DHN-02"]}


def _p(pid, brand, model, sku, **extra):
    return {"product_id": pid, "brand": brand, "model": model, "sku": sku,
            "category": "FRAME", "mrp": 1000.0, "offer_price": 1000.0,
            "is_active": True, **extra}


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        return self.db[name]


class _NoCache:
    TTL_MEDIUM = 0

    def get(self, k):
        return None

    def set(self, k, v, ttl=0):
        pass


@pytest.fixture
def db(monkeypatch):
    db = mongomock.MongoClient().db
    conn = _Conn(db)
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(cache_mod, "cache", _NoCache())
    return db


def _ids(db, q, **params):
    app = FastAPI()
    app.include_router(products_router, prefix="/api/v1/products")
    app.dependency_overrides[get_current_user] = lambda: _CASHIER
    res = TestClient(app).get("/api/v1/products", params={"search": q, **params})
    assert res.status_code == 200, res.text[:300]
    body = res.json()
    return [p["product_id"] for p in body["products"]], body["total_count"]


@pytest.fixture
def catalogue(db):
    db.products.insert_many([
        _p("CL-AIR", "Alcon", "Air Optix Plus HydraGlyde", "CL-AL-0001"),
        _p("CL-OAS", "Johnson & Johnson", "Acuvue Oasys 1-Day", "CL-JJ-0001"),
        _p("SG-AVI", "Ray-Ban", "Aviator Classic", "SG-RB-0001", variant="Matte Black"),
        _p("AC-SPR", "Zeiss", "Spray Cleaner", "AC-ZS-0001"),
        _p("FR-GRY", "Vogue", "Gunmetal Gray", "FR-VO-0001"),
    ])
    return db


@pytest.mark.parametrize(
    "q, pid",
    [("Air Optix", "CL-AIR"), ("air optix", "CL-AIR"), ("optix", "CL-AIR"),
     ("Acuvue Oasys", "CL-OAS"), ("Ray Ban Aviator", "SG-AVI")],
)
def test_a_multi_word_name_is_found(catalogue, q, pid):
    ids, total = _ids(catalogue, q)
    assert ids == [pid]
    assert total == 1


def test_variant_is_a_code_matched_from_its_start(catalogue):
    # One rule per field: the purchase-order box (#1170) also treats variant
    # as a code, so "black" is not a word-start hit on "Matte Black".
    assert _ids(catalogue, "matte") == (["SG-AVI"], 1)
    assert _ids(catalogue, "black") == ([], 0)


def test_every_typed_word_is_required(catalogue):
    assert _ids(catalogue, "Air Oasys") == ([], 0)


def test_ray_never_finds_spray_or_gray(catalogue):
    ids, total = _ids(catalogue, "ray")
    assert ids == ["SG-AVI"]
    assert total == 1


@pytest.mark.parametrize("q", ["Ray.*", "(", "a+b[", ".*", "\\", "Optix$"])
def test_a_regex_looking_input_is_matched_literally(catalogue, q):
    assert _ids(catalogue, q) == ([], 0)


def test_codes_still_match_from_their_start(catalogue):
    assert _ids(catalogue, "CL-AL")[0] == ["CL-AIR"]
    # A word inside a SKU is not a code start.
    assert _ids(catalogue, "AL-0001") == ([], 0)


@pytest.mark.parametrize("field", ["sku", "barcode"])
def test_an_exact_code_still_comes_first(db, field):
    code = "RB3025"
    db.products.insert_many([
        # Inserted FIRST, found only by word start ("RB3025" is its 2nd word).
        _p("SG-WORD", "Ray-Ban", f"Aviator {code}", "SG-RB-0007"),
        {**_p("SG-CODE", "Ray-Ban", "Classic", "SG-RB-0008"), field: code},
    ])
    ids, total = _ids(db, code)
    assert ids == ["SG-CODE", "SG-WORD"]
    assert total == 2


def test_pages_split_across_the_two_tiers_and_count_matches(db):
    db.products.insert_many([
        _p("SG-WORD", "Ray-Ban", "Aviator RB3025", "SG-RB-0007"),
        _p("SG-CODE", "Ray-Ban", "Classic", "RB3025"),
    ])
    page1, total1 = _ids(db, "RB3025", skip=0, limit=1)
    page2, total2 = _ids(db, "RB3025", skip=1, limit=1)
    assert (page1, page2) == (["SG-CODE"], ["SG-WORD"])
    assert total1 == total2 == 2


def test_every_product_search_uses_the_same_rule(catalogue):
    # The rule is the repository's, not a call's: a plain search over the
    # product fields (as any other ranked search builds on) finds what the
    # till finds.
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(catalogue.products)
    plain = repo.search("optix", list(repo.SEARCH_FIELDS), {"is_active": True})
    assert [d["product_id"] for d in plain] == ["CL-AIR"]
