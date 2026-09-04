"""
Catalog screen truth: the photo predicate, the Online column, the counts row
=============================================================================

The owner's complaint (2026-09-04): the catalog screen was designed on 08-30
and never built. The design's defining number is PHOTOS -- "of 69 products
only 6 carry a usable photo URL" -- and a prod probe while building it found
WHY: the billing spine had 69/76 products with an absolute photo URL, the
catalog twin (the doc the Shopify push actually reads) had 6, because
product_master._build_pim_doc never projected `images`. So the screen must:

  * judge "usable photo" with the push's OWN predicate
    (shopify_push.product_photo_urls) -- one place, server-side, the frontend
    never re-derives it -- and judge a SPINE row by its TWIN, exactly as the
    publish gate would;
  * carry the photograph from the spine onto the twin at create AND on edit
    (and queue the edit: the photo is storefront content);
  * tally the counts row with the same per-row rule the columns use, and read
    "waiting to push" off ecom.locally_modified -- the flag the push sweep
    walks -- which the Shopify write-back must never set (ping-pong).

Every test here fails if the behaviour is reverted: the predicate cases are
asserted per value, the mirror tests read the twin back from the store, the
count tests seed rows whose classification differs on each rule, and the
write-back test drives the real shopify_push write-back.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_catalog_photo_state.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402
import itertools  # noqa: E402

import pytest  # noqa: E402

from strict_fakes import StrictCollection, StrictDB  # noqa: E402
from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.online_catalog import (  # noqa: E402
    catalog_counts,
    online_summary,
    product_online_state,
    stamp_online_state,
)

HTTP = "https://cdn.example.com/rb2140.jpg"
APP_UPLOAD = "/api/v1/products/image/abc123"
ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}


@pytest.fixture(autouse=True)
def _no_public_base(monkeypatch):
    """The predicate's ONE opt-in is dark unless a test turns it on."""
    monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)


# ===========================================================================
# 1. The predicate -- shopify_push.product_photo_urls
# ===========================================================================


@pytest.mark.parametrize(
    "doc",
    [
        {},
        {"image_url": None},
        {"image_url": ""},
        {"image_url": "   "},
        {"images": None},
        {"images": []},
        {"images": [None, "", "   "]},
        {"images": "not-a-list"},
        {"image_url": "/uploads/rb2140.jpg"},
        {"images": ["uploads/rb2140.jpg"]},
        {"images": [{"url": "/uploads/x.jpg"}, {"src": ""}, {"nope": HTTP}]},
        {"image": "data:image/png;base64,AAAA"},
        {"image_url": "ftp://cdn.example.com/x.jpg"},
        # The in-app upload path is NOT a photograph while the public base is unset.
        {"images": [APP_UPLOAD]},
    ],
)
def test_predicate_rejects_missing_null_blank_and_relative(doc):
    assert shopify_push.product_photo_urls(doc) == []
    assert product_online_state(doc)["has_photo"] is False


@pytest.mark.parametrize(
    "doc, expected",
    [
        ({"image_url": HTTP}, [HTTP]),
        ({"image_url": f"  {HTTP}  "}, [HTTP]),
        ({"images": ["http://cdn.example.com/a.jpg"]}, ["http://cdn.example.com/a.jpg"]),
        ({"images": [{"url": HTTP}]}, [HTTP]),
        ({"images": [{"src": HTTP}]}, [HTTP]),
        ({"image": HTTP}, [HTTP]),
        # blanks and relatives are skipped, usable ones kept in order, deduped
        (
            {"image_url": "", "images": ["/uploads/x.jpg", HTTP, HTTP, "https://b/2.png"]},
            [HTTP, "https://b/2.png"],
        ),
    ],
)
def test_predicate_accepts_only_absolute_http_urls(doc, expected):
    assert shopify_push.product_photo_urls(doc) == expected
    assert product_online_state(doc)["has_photo"] is True


def test_in_app_upload_becomes_a_photo_only_with_a_public_base(monkeypatch):
    """The uploader stores a RELATIVE path to this API's public image serve.
    Shopify can only fetch it from a public address, so it counts only once
    PUBLIC_API_BASE_URL names one -- and then the push is handed the
    absolute URL. A bare /uploads/ path stays unusable either way."""
    doc = {"images": [APP_UPLOAD, "/uploads/legacy.jpg"]}
    assert shopify_push.product_photo_urls(doc) == []

    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://ims-api.example.com/")
    assert shopify_push.product_photo_urls(doc) == [
        "https://ims-api.example.com" + APP_UPLOAD
    ]
    assert product_online_state(doc)["has_photo"] is True


# ===========================================================================
# 2. The Online column -- one rule per row
# ===========================================================================


def test_online_state_live_wins_even_without_a_photo():
    st = product_online_state({"ecom": {"shopify_product_id": "gid://shopify/Product/1"}})
    assert st == {"has_photo": False, "online": "LIVE", "queued": False}


def test_online_state_staged_published_is_live():
    st = product_online_state({"image_url": HTTP, "ecom": {"status": "PUBLISHED"}})
    assert st["online"] == "LIVE"


def test_online_state_blocked_when_no_photo_and_not_live():
    st = product_online_state({"ecom": {"status": "DRAFT", "locally_modified": True}})
    # Queued but photo-less: the push would refuse it, so the row says BLOCKED
    # while `queued` still reports the flag for the pending count.
    assert st == {"has_photo": False, "online": "BLOCKED", "queued": True}


def test_online_state_queued_when_photo_and_dirty():
    st = product_online_state(
        {"images": [HTTP], "ecom": {"status": "DRAFT", "locally_modified": True}}
    )
    assert st["online"] == "QUEUED"


def test_online_state_off_when_photo_and_clean():
    st = product_online_state({"images": [HTTP], "ecom": {"status": "DRAFT"}})
    assert st == {"has_photo": True, "online": "OFF", "queued": False}


def test_online_state_never_raises_on_junk():
    assert product_online_state(None)["online"] == "BLOCKED"  # type: ignore[arg-type]
    assert product_online_state({"ecom": "not-a-dict"})["online"] == "BLOCKED"


# ===========================================================================
# 3. Spine rows are judged by their TWIN (what the push reads)
# ===========================================================================


def _spine(pid, sku, pim_id=None, images=None):
    row = {"product_id": pid, "sku": sku, "images": images or []}
    if pim_id:
        row["pim_product_id"] = pim_id
    return row


def test_stamp_judges_a_spine_row_by_its_twin_not_its_own_photo():
    """THE prod finding: the spine had the photo, the twin did not, and the
    push refused the product. The screen must say what the push will do."""
    db = StrictDB()
    db.seed("catalog_products", [{"id": "P1", "sku": "SKU-1", "ecom": {"status": "DRAFT"}}])
    rows = [_spine("S1", "SKU-1", pim_id="P1", images=[HTTP])]
    stamp_online_state(db, rows)
    assert rows[0]["has_photo"] is False
    assert rows[0]["online"] == "BLOCKED"


def test_stamp_reads_the_twin_state_by_pim_id_then_sku():
    db = StrictDB()
    db.seed(
        "catalog_products",
        [
            {"id": "P1", "sku": "SKU-1", "images": [HTTP], "ecom": {"locally_modified": True}},
            # legacy twin: no pim link on the spine, matched by sku
            {"id": "LEGACY", "sku": "SKU-2", "ecom": {"shopify_product_id": "gid://x/2"}},
        ],
    )
    rows = [_spine("S1", "SKU-1", pim_id="P1"), _spine("S2", "SKU-2")]
    stamp_online_state(db, rows)
    assert (rows[0]["has_photo"], rows[0]["online"]) == (True, "QUEUED")
    assert (rows[1]["has_photo"], rows[1]["online"]) == (False, "LIVE")


def test_stamp_falls_back_to_the_spine_when_there_is_no_twin():
    db = StrictDB()
    rows = [_spine("S9", "SKU-9", pim_id="NOPE", images=[HTTP])]
    stamp_online_state(db, rows)
    assert rows[0]["has_photo"] is True
    assert rows[0]["online"] == "OFF"


def test_stamp_is_fail_soft_without_a_db():
    rows = [_spine("S1", "SKU-1", images=[HTTP])]
    stamp_online_state(None, rows)
    assert rows[0]["has_photo"] is True


# ===========================================================================
# 4. The photograph reaches the twin -- create door AND edit mirror
# ===========================================================================


def _frame_payload(sku="FR-PHOTO-001"):
    return {
        "category": "FRAME",
        "sku": sku,
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "cost_price": 2000.0,
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB-2140",
            "colour_code": "BLK",
            "colour_name": "Black",
            "lens_size": 50,
            "shape": "SQUARE",
            "gender": "UNISEX",
        },
    }


def test_build_pim_doc_projects_the_spine_photos():
    doc = pm._build_pim_doc({"pim_product_id": "PIM-1", "sku": "X", "images": [HTTP, "", None, " "]})
    assert doc["images"] == [HTTP]
    assert product_online_state(doc)["has_photo"] is True
    # still pure / tolerant of a spine with no images at all
    assert pm._build_pim_doc({"pim_product_id": "PIM-2"})["images"] == []


def test_create_door_lands_the_photo_on_the_twin_the_push_reads():
    """Driven the way POST /products drives the door: the form's images ride
    in `extra_fields` (products._form_extra_fields), land on the spine, and
    must come out on the twin."""
    db = StrictDB()
    created = pm.create_via_door(
        _frame_payload(),
        source="FORM",
        actor="u1",
        extra_fields={"images": [HTTP, "", "  "]},
        product_repo=ProductRepository(MockCollection("products")),
        audit_repo=AuditRepository(MockCollection("audit_logs")),
        db=db,
    )
    assert created["images"] == [HTTP, "", "  "] or created["images"] == [HTTP]
    twin = db.get_collection("catalog_products").find_one({"id": created["pim_product_id"]})
    assert twin is not None
    assert twin["images"] == [HTTP]
    st = product_online_state(twin)
    assert st["has_photo"] is True
    assert st["online"] == "QUEUED"  # born dirty AND has a photo


def test_spine_photo_edit_mirrors_onto_the_twin_and_queues_it():
    db = StrictDB()
    db.seed("catalog_products", [{"id": "P1", "sku": "SKU-1", "ecom": {"status": "DRAFT", "locally_modified": False}}])
    pm.mirror_update_to_catalog_twin(
        product_id="SPINE-1",
        current={"pim_product_id": "P1"},
        patch={"images": [HTTP, ""]},
        db=db,
    )
    twin = db.get_collection("catalog_products").find_one({"id": "P1"})
    assert twin["images"] == [HTTP]
    assert twin["ecom"]["locally_modified"] is True
    assert product_online_state(twin)["online"] == "QUEUED"


def test_spine_edit_without_images_leaves_the_twin_photo_alone():
    db = StrictDB()
    db.seed("catalog_products", [{"id": "P1", "sku": "SKU-1", "images": [HTTP], "ecom": {"status": "DRAFT"}}])
    pm.mirror_update_to_catalog_twin(
        product_id="SPINE-1", current={"pim_product_id": "P1"}, patch={"hsn_code": "900311"}, db=db
    )
    twin = db.get_collection("catalog_products").find_one({"id": "P1"})
    assert twin["images"] == [HTTP]
    assert twin["ecom"].get("locally_modified") is not True


# ===========================================================================
# 5. The counts row -- same rule as the rows; pending = the sweep's flag
# ===========================================================================


def _seed_counts_db():
    db = StrictDB()
    db.seed(
        "catalog_products",
        [
            # smart glasses, live, with photo
            {"id": "A", "category": "SMARTGLASSES", "images": [HTTP],
             "ecom": {"shopify_product_id": "gid://x/1", "status": "PUBLISHED"}},
            # own, NO photo, queued -> BLOCKED, counts as pending
            {"id": "B", "category": "SUNGLASS", "ecom": {"status": "DRAFT", "locally_modified": True}},
            # own, photo, queued -> QUEUED, pending
            {"id": "C", "category": "FR", "images": [HTTP], "ecom": {"status": "DRAFT", "locally_modified": True}},
            # own, photo, clean -> OFF
            {"id": "D", "category": "SUNGLASS", "image_url": HTTP, "ecom": {"status": "DRAFT"}},
            # inactive: OUT of the catalog population, but its dirty flag is
            # still what the sweep walks -> pending only
            {"id": "E", "category": "SUNGLASS", "is_active": False,
             "ecom": {"status": "DRAFT", "locally_modified": True}},
            # an import awaiting review: OUT of the population, counted as needs_review
            {"id": "F", "category": "SUNGLASS", "needs_review": True, "ecom": {"status": "DRAFT"}},
        ],
    )
    return db


def test_catalog_counts_tally_the_population_with_the_row_rule():
    out = catalog_counts(_seed_counts_db())
    assert out == {
        "in_catalog": 4,
        "smartglasses": 1,
        "own": 3,
        "no_photo": 1,
        "live": 1,
        "pending": 3,
        "needs_review": 1,
    }


def test_online_summary_carries_the_catalog_block():
    db = _seed_counts_db()
    out = online_summary(db)
    assert out["reachable"] is True
    assert out["catalog"] == catalog_counts(db)


def test_catalog_counts_are_zero_without_a_catalog():
    assert catalog_counts(None)["in_catalog"] == 0
    assert catalog_counts(StrictDB())["pending"] == 0


def test_pending_drops_when_the_shopify_writeback_clears_the_flag_and_never_rises():
    """The ping-pong guard, read through THIS count: a successful push's
    write-back clears the flag (pending falls) and never sets it (a second
    write-back cannot re-queue the row)."""
    db = StrictDB()
    db.seed("catalog_products", [{"id": "P1", "sku": "S1", "images": [HTTP],
                                  "ecom": {"status": "DRAFT", "locally_modified": True}}])
    assert catalog_counts(db)["pending"] == 1
    shopify_push._writeback_product(db, "P1", "gid://shopify/Product/9", status="PUBLISHED")
    assert catalog_counts(db)["pending"] == 0
    assert product_online_state(db["catalog_products"].find_one({"id": "P1"}))["online"] == "LIVE"
    shopify_push._writeback_product(db, "P1", "gid://shopify/Product/9")
    assert catalog_counts(db)["pending"] == 0


# ===========================================================================
# 6. The list endpoints -- rows carry the truth, ?photo= filters on it
# ===========================================================================


def _catalog_list(monkeypatch, docs, **kwargs):
    monkeypatch.setattr(catalog_mod, "_all_catalog_products", lambda: [dict(d) for d in docs])
    params = {
        "category": None, "brand": None, "search": None, "is_active": "all",
        "needs_review": None, "source": None, "photo": None, "limit": 50, "page": 1,
        "current_user": ADMIN,
    }
    params.update(kwargs)
    return asyncio.run(catalog_mod.list_catalog_products(**params))


_CATALOG_DOCS = [
    {"id": "A", "sku": "A", "title": "With photo", "is_active": True, "images": [HTTP],
     "ecom": {"status": "DRAFT", "locally_modified": True}, "attributes": {}},
    {"id": "B", "sku": "B", "title": "No photo", "is_active": True,
     "ecom": {"status": "DRAFT"}, "attributes": {}},
    {"id": "C", "sku": "C", "title": "Blank photo", "is_active": True, "image_url": "",
     "ecom": {"shopify_product_id": "gid://x/3"}, "attributes": {}},
]


def test_catalog_list_rows_carry_has_photo_and_online(monkeypatch):
    out = _catalog_list(monkeypatch, _CATALOG_DOCS)
    by_id = {p["id"]: p for p in out["products"]}
    assert (by_id["A"]["has_photo"], by_id["A"]["online"]) == (True, "QUEUED")
    assert (by_id["B"]["has_photo"], by_id["B"]["online"]) == (False, "BLOCKED")
    assert (by_id["C"]["has_photo"], by_id["C"]["online"]) == (False, "LIVE")


def test_catalog_list_photo_filter_missing_and_has(monkeypatch):
    missing = _catalog_list(monkeypatch, _CATALOG_DOCS, photo="missing")
    assert sorted(p["id"] for p in missing["products"]) == ["B", "C"]
    assert missing["total"] == 2
    has = _catalog_list(monkeypatch, _CATALOG_DOCS, photo="has")
    assert [p["id"] for p in has["products"]] == ["A"]
    assert has["total"] == 1


# The listing cache key is process-global, so each call needs its own store.
# A COUNTER, not a uuid slice: the BUG-104 guard hunts `[:10]` (a UTC day
# face) and cannot tell a uuid slice from a date one.
_STORE_SEQ = itertools.count()


def _unique_store(prefix: str) -> str:
    return "%s-%d" % (prefix, next(_STORE_SEQ))


def _spine_repo(rows):
    coll = StrictCollection("products")
    for r in rows:
        coll.insert_one(dict(r))
    return ProductRepository(coll)


def _products_list(monkeypatch, repo, db, **kwargs):
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_db", lambda: db)
    params = {
        "category": None, "brand": None, "search": None, "tag": None, "created_by": None,
        "store_id": _unique_store("S-photo"),
        "skip": 0, "limit": 50, "is_active": "all", "photo": None, "current_user": ADMIN,
    }
    params.update(kwargs)
    return asyncio.run(products_mod.list_products(**params))


def _spine_fixture():
    rows = [
        {"product_id": "S1", "sku": "SKU-1", "pim_product_id": "P1", "is_active": True, "images": [HTTP]},
        {"product_id": "S2", "sku": "SKU-2", "pim_product_id": "P2", "is_active": True, "images": [HTTP]},
        {"product_id": "S3", "sku": "SKU-3", "pim_product_id": "P3", "is_active": True},
    ]
    db = StrictDB()
    db.seed(
        "catalog_products",
        [
            {"id": "P1", "sku": "SKU-1", "images": [HTTP], "ecom": {"status": "DRAFT"}},
            # the spine has the photo, the twin does not: the push would refuse it
            {"id": "P2", "sku": "SKU-2", "ecom": {"status": "DRAFT", "locally_modified": True}},
            {"id": "P3", "sku": "SKU-3", "ecom": {"shopify_product_id": "gid://x/3"}},
        ],
    )
    return rows, db


def test_products_list_rows_are_judged_by_their_twin(monkeypatch):
    rows, db = _spine_fixture()
    out = _products_list(monkeypatch, _spine_repo(rows), db)
    by_id = {p["product_id"]: p for p in out["products"]}
    assert (by_id["S1"]["has_photo"], by_id["S1"]["online"]) == (True, "OFF")
    assert (by_id["S2"]["has_photo"], by_id["S2"]["online"]) == (False, "BLOCKED")
    assert (by_id["S3"]["has_photo"], by_id["S3"]["online"]) == (False, "LIVE")


def test_products_list_photo_filter_pages_the_filtered_set(monkeypatch):
    rows, db = _spine_fixture()
    repo = _spine_repo(rows)
    missing = _products_list(monkeypatch, repo, db, photo="missing")
    assert sorted(p["product_id"] for p in missing["products"]) == ["S2", "S3"]
    assert missing["total_count"] == 2
    page2 = _products_list(monkeypatch, repo, db, photo="missing", skip=1, limit=1)
    assert len(page2["products"]) == 1 and page2["total_count"] == 2
    has = _products_list(monkeypatch, repo, db, photo="has")
    assert [p["product_id"] for p in has["products"]] == ["S1"]
    assert has["total_count"] == 1


def test_products_list_restamps_on_a_cache_hit(monkeypatch):
    """The listing is cached for 5 minutes; the photo / online truth is not.
    A twin that gains a photo (or a push that goes live) shows on the very
    next load, cache hit or miss."""
    rows, db = _spine_fixture()
    repo = _spine_repo(rows)
    store = _unique_store("S-cache")
    first = _products_list(monkeypatch, repo, db, store_id=store)
    assert {p["product_id"]: p["has_photo"] for p in first["products"]}["S2"] is False
    db["catalog_products"].update_one({"id": "P2"}, {"$set": {"images": [HTTP]}})
    second = _products_list(monkeypatch, repo, db, store_id=store)
    s2 = {p["product_id"]: p for p in second["products"]}["S2"]
    assert (s2["has_photo"], s2["online"]) == (True, "QUEUED")
