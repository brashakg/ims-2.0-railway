"""The till and the counter lookup find multi-word names (owner 2026-10-08).

Every typed word must START A WORD of a product's brand, model, minted name,
model_name or subbrand (the field start, or after a space, hyphen, slash, dot
or underscore), so "Air Optix", "Acuvue Oasys" and "Ray Ban Aviator" are
found while "ray" still never finds "Spray Cleaner" or "Gunmetal Gray". Codes
(SKU, variant, barcode) keep matching from their start, and a field-start hit
still comes first. Driven through GET /api/v1/products?search= (the till's
product query; the counter lookup runs the same search_products) over
mongomock, and once over the fallback mock DB that local no-Mongo mode runs on.

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


def _get(q, **params):
    app = FastAPI()
    app.include_router(products_router, prefix="/api/v1/products")
    app.dependency_overrides[get_current_user] = lambda: _CASHIER
    return TestClient(app).get("/api/v1/products", params={"search": q, **params})


def _ids(db, q, **params):
    res = _get(q, **params)
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


@pytest.mark.parametrize(
    "q, pids",
    [("ban", ["RB-HYP"]), ("71", ["OK-SLA"]), ("1109/71", ["OK-SLA"]),
     ("ray", ["RB-HYP", "EY-DOT", "NE-UND"]), ("ray 71", [])],
)
def test_a_hyphen_slash_dot_or_underscore_starts_a_word(db, q, pids):
    db.products.insert_many([
        _p("RB-HYP", "Ray-Ban", "Clubmaster", "SG-RB-0002"),
        _p("OK-SLA", "Oakley", "Holbrook 1109/71", "SG-OK-0001"),
        _p("EY-DOT", "Eye.Ray", "Pilot", "SG-EY-0001"),
        _p("NE-UND", "Neo_Ray", "Round", "SG-NE-0001"),
    ])
    assert _ids(db, q) == (pids, len(pids))


def test_a_legacy_colour_only_in_variant_is_matched_from_its_start(catalogue):
    # SG-AVI is a legacy row: no minted name, its colour only in `variant`.
    # variant is a code (one rule per field: the purchase-order box, #1170,
    # treats it as one too), so "matte" starts it and "black" does not.
    assert _ids(catalogue, "matte") == (["SG-AVI"], 1)
    assert _ids(catalogue, "black") == ([], 0)


def test_a_search_is_at_most_100_characters(catalogue):
    # A pasted wall of text cannot build a huge regex; 100 still searches.
    assert _ids(catalogue, "Air Optix".ljust(100)) == (["CL-AIR"], 1)
    res = _get("Air Optix".ljust(101))
    assert res.status_code == 422, res.text[:300]


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


def test_a_page_never_holds_more_than_the_limit(db):
    db.products.insert_many([
        _p("SG-W1", "Ray-Ban", "Aviator RB3025", "SG-RB-0007"),
        _p("SG-W2", "Ray-Ban", "Clubmaster RB3025", "SG-RB-0009"),
        _p("SG-CODE", "Ray-Ban", "Classic", "RB3025"),
    ])
    assert _ids(db, "RB3025", skip=0, limit=2) == (["SG-CODE", "SG-W1"], 3)
    assert _ids(db, "RB3025", skip=1, limit=2) == (["SG-W1", "SG-W2"], 3)
    assert _ids(db, "RB3025", skip=2, limit=2) == (["SG-W2"], 3)


def test_every_product_search_uses_the_same_rule(catalogue):
    # The rule is the repository's, not a call's: a plain search over the
    # product fields (as any other ranked search builds on) finds what the
    # till finds.
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(catalogue.products)
    plain = repo.search("optix", list(repo.SEARCH_FIELDS), {"is_active": True})
    assert [d["product_id"] for d in plain] == ["CL-AIR"]


@pytest.mark.parametrize("q", ["optix", "Air Optix", "ban", "oasys"])
def test_the_fallback_mock_db_lists_what_it_counts(q):
    # Local no-Mongo mode runs on MockCollection; a word-only hit sits in the
    # $nor half of the ranked query, so the mock has to understand $nor.
    from database.connection import MockCollection
    from database.repositories.product_repository import ProductRepository

    coll = MockCollection("products")
    coll.insert_many([
        _p("CL-AIR", "Alcon", "Air Optix Plus HydraGlyde", "CL-AL-0001"),
        _p("CL-OAS", "Johnson & Johnson", "Acuvue Oasys 1-Day", "CL-JJ-0001"),
        _p("SG-AVI", "Ray-Ban", "Aviator Classic", "SG-RB-0001"),
    ])
    repo = ProductRepository(coll)
    expected = {"optix": ["CL-AIR"], "Air Optix": ["CL-AIR"], "ban": ["SG-AVI"],
                "oasys": ["CL-OAS"]}[q]
    assert [d["product_id"] for d in repo.search_products(q)] == expected
    assert repo.count_search_products(q) == len(expected)


def _door(db, pid, category, **attrs):
    # A product exactly as the one create door builds it: a SUNGLASS's spine
    # model is its model_no, so "Aviator" lives only in the minted name or
    # attributes.model_name, and a lens's "Acuvue" only in attributes.subbrand.
    from api.services import product_master as pm

    doc = pm.normalise_payload(category=category, attributes=attrs, mrp=5000.0,
                               offer_price=5000.0, as_draft=True, db=None)
    db.products.insert_one({**doc, "product_id": pid})


@pytest.mark.parametrize(
    "q, want",
    # "aviator": SG-MODEL's model_name STARTS with it, so it ranks first;
    # SG-SHAPE has it only as a word of its title. "ban" starts neither
    # field, so for "Ray Ban Aviator" both are word hits, in stored order.
    [("Ray Ban Aviator", ["SG-SHAPE", "SG-MODEL"]),
     ("aviator", ["SG-MODEL", "SG-SHAPE"]),
     ("Ray Ban Aviator Classic", ["SG-MODEL"]),
     ("Acuvue Oasys", ["CL-SUB"]), ("acuvue", ["CL-SUB"])],
)
def test_a_door_made_product_is_found_by_the_words_the_till_shows(db, q, want):
    from database.repositories.product_repository import ProductRepository

    # No model_name: the door mints "Ray-Ban RB3025 Aviator Sunglasses" from
    # the shape, and the shape itself is not searched -- so "Aviator" is
    # found here ONLY through the title, by a word that is not its start.
    _door(db, "SG-SHAPE", "SUNGLASS", brand_name="Ray-Ban", model_no="RB3025",
          colour_code="001", shape="Aviator")
    shaped = db.products.find_one({"product_id": "SG-SHAPE"})
    assert shaped["name"] == "Ray-Ban RB3025 Aviator Sunglasses"
    assert "model_name" not in shaped["attributes"]
    assert "attributes.shape" not in ProductRepository.SEARCH_FIELDS
    # model_name is not in this one's title ("Ray-Ban RB3026 Sunglasses").
    _door(db, "SG-MODEL", "SUNGLASS", brand_name="Ray-Ban", model_no="RB3026",
          colour_code="001", model_name="Aviator Classic")
    _door(db, "CL-SUB", "CONTACT_LENS", brand_name="Johnson & Johnson",
          subbrand="Acuvue", model_name="Oasys 1-Day")
    assert _ids(db, q) == (want, len(want))


# Main's till rule, before 2026-10-08: each word STARTS brand, model, sku,
# variant or barcode.
_MAIN_FIELDS = ["brand", "model", "sku", "variant", "barcode"]


def _main_ids(db, q):
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(db.products)
    query = repo._search_query(q, _MAIN_FIELDS, {"is_active": True}, word_fields=())
    return [d["product_id"] for d in repo.find_many(query)]


def test_a_title_word_finds_a_product_after_what_main_found(db):
    # Stored FIRST, so only the ranking can put main's hit ahead of it.
    _door(db, "SG-MBK", "SUNGLASS", brand_name="Ray-Ban", model_no="RB3025",
          colour_code="002", shape="Aviator", frame_color="Matte Black")
    assert db.products.find_one({"product_id": "SG-MBK"})["name"] == (
        "Ray-Ban RB3025 Aviator Sunglasses - Matte Black")
    db.products.insert_one(_p("SG-AVI", "Ray-Ban", "Aviator Classic",
                              "SG-RB-0001", variant="Matte Black"))
    # "black" is a word of the door-made title (deliberate: a colour word in
    # the title finds it); main found nothing for it.
    assert _main_ids(db, "black") == []
    assert _ids(db, "black") == (["SG-MBK"], 1)
    # "matte" already found the legacy row on main: main's list comes first,
    # unchanged, and the new title-word hit after it.
    assert _main_ids(db, "matte") == ["SG-AVI"]
    assert _ids(db, "matte") == (["SG-AVI", "SG-MBK"], 2)


def test_other_searches_keep_matching_from_the_field_start(db):
    # The any-word-start rule is the PRODUCT repository's alone: a customer,
    # vendor, order or user search keeps the field-start rule.
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.vendor_repository import VendorRepository

    db.customers.insert_one({"customer_id": "C1", "name": "Anita Ray",
                             "mobile": "98 76543210"})
    db.vendors.insert_one({"vendor_id": "V1", "legal_name": "Lux Ray Pvt Ltd"})
    customers = CustomerRepository(db.customers)
    assert customers.search_customers("ray") == []
    assert customers.search_customers("7654") == []
    assert VendorRepository(db.vendors).search_vendors("ray") == []
