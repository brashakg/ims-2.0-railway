"""
IMS 2.0 - Brand Master "Never sell online" (owner 2026-10-19, SUPERADMIN)
========================================================================
"Brands that have not allowed us to sell online cannot be even manually
selected." One flag on the brand (never_sell_online), checked FIRST inside
THE website gate (catalog_dictionary.brand_website_refusal, asked by
shopify_push.product_push_refusal), so every door refuses with one answer:
the product push, the price push, the image push, push-all / the scheduled
sync, the collection registry's "ready" count and the Add/Edit form's line.

  * only SUPERADMIN can set or clear it (server 403 + audit row);
  * it wins over "Sync to Shopify by default";
  * blocking takes NOTHING down: the live listings are reported instead;
  * clearing it lets the brand publish again.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_brandf_never_sell_online.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import admin_catalog as ac  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from api.services import shopify_push as sp  # noqa: E402
from api.services.catalog_dictionary import brand_website_refusal  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

_ADMIN = {"user_id": "brandf_admin", "roles": ["ADMIN"]}
_SUPER = {"user_id": "brandf_super", "roles": ["SUPERADMIN"]}
_BLOCK = "has not allowed online selling"


def _run(coro):
    return asyncio.run(coro)


class _Db(StrictDB):
    is_connected = True


class _Conn:
    """Shaped like SeededDatabaseConnection, what get_db() really returns:
    NOT subscriptable, so a caller that forgets to unwrap .db fails here."""

    def __init__(self, db):
        self.db = db
        self.is_connected = True

    def get_collection(self, name):
        return self.db.get_collection(name)


def _db(blocked=True):
    db = _Db()
    db.seed("brand_masters", [
        # Ticked for the website AND blocked: the block must win.
        {"brand_id": "brandf_b1", "name": "BrandfLux", "code": "BFLUX", "is_active": True,
         "tier": "LUXURY", "sync_to_shopify_default": True, "never_sell_online": blocked},
        {"brand_id": "brandf_b2", "name": "BrandfOk", "code": "BFOK", "is_active": True,
         "tier": "MASS", "sync_to_shopify_default": True},
    ])
    return db


def _set_block(db, on):
    db["brand_masters"].update_one({"brand_id": "brandf_b1"}, {"$set": {"never_sell_online": on}})


def _new(brand, **extra):
    return {"id": f"brandf_{brand}", "product_id": f"brandf_{brand}", "sku": f"BRANDF-{brand}",
            "category": "FRAME", "title": f"{brand} frame", "brand": brand,
            "attributes": {"brand_name": brand}, "images": ["https://cdn.example.com/p.jpg"],
            "mrp": 1000, **extra}


def _live(brand):
    return {**_new(brand), "offer_price": 900.0, "online_price": 900.0,
            "ecom": {"shopify_product_id": "gid://shopify/Product/9",
                     "default_variant_gid": "gid://shopify/ProductVariant/9"}}


# ---------------------------------------------------------------------------
# Who may change it (server-side)
# ---------------------------------------------------------------------------


class _Audit:
    def __init__(self):
        self.rows = []

    def create(self, row):
        self.rows.append(row)


@pytest.fixture
def brands(monkeypatch):
    db = _db(blocked=False)
    db.seed("catalog_products", [_live("BrandfLux")])
    audit = _Audit()
    monkeypatch.setattr(ac, "_coll", lambda name: db[name])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit)
    return db, audit


def test_an_admin_cannot_set_or_clear_it(brands):
    db, audit = brands
    with pytest.raises(HTTPException) as e:
        _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_ADMIN))
    assert e.value.status_code == 403
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"}).get("never_sell_online") is not True
    _set_block(db, True)
    with pytest.raises(HTTPException):
        _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=False), current_user=_ADMIN))
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"})["never_sell_online"] is True
    with pytest.raises(HTTPException):
        _run(ac.create_brand(ac.BrandCreate(name="BrandfNew", code="BFNEW", tier="MASS",
                                            never_sell_online=True), current_user=_ADMIN))
    assert audit.rows == []


def test_an_admin_can_still_edit_the_rest_of_a_blocked_brand(brands):
    db, _ = brands
    _set_block(db, True)
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(tier="PREMIUM", never_sell_online=True),
                               current_user=_ADMIN))
    assert out["tier"] == "PREMIUM" and out["never_sell_online"] is True


def test_superadmin_sets_it_with_an_audit_row_and_nothing_is_taken_down(brands, monkeypatch):
    db, audit = brands

    async def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("blocking must not call Shopify")

    monkeypatch.setattr(sp, "_graphql", _boom)
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert out["never_sell_online"] is True
    # The live listing is REPORTED, not taken down.
    assert out["live_listings_not_taken_down"] == ["BRANDF-BrandfLux"]
    live = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})
    assert live["ecom"]["shopify_product_id"] and not live["ecom"].get("taken_down_at")
    [row] = audit.rows
    assert row["action"] == "BRAND_NEVER_SELL_ONLINE_ON" and row["user_id"] == "brandf_super"
    assert row["details"]["live_listings_not_taken_down"] == ["BRANDF-BrandfLux"]
    _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=False), current_user=_SUPER))
    assert audit.rows[-1]["action"] == "BRAND_NEVER_SELL_ONLINE_OFF"


# ---------------------------------------------------------------------------
# Every door refuses a blocked brand (one gate)
# ---------------------------------------------------------------------------


def test_the_gate_refuses_even_when_the_brand_is_ticked_for_the_website():
    db = _db()
    assert _BLOCK in brand_website_refusal(db, "BrandfLux")
    assert _BLOCK in brand_website_refusal(db, "brandflux")  # same casefold match
    assert brand_website_refusal(db, "BrandfOk") is None
    # A blocked inactive twin row still blocks (fail-closed).
    db["brand_masters"].insert_one({"brand_id": "brandf_b3", "name": "BrandfOk",
                                    "is_active": False, "never_sell_online": True})
    assert _BLOCK in brand_website_refusal(db, "BrandfOk")


def test_product_push_refuses():
    res = _run(sp.push_product(_db(), _new("BrandfLux"), blocked=False))
    assert res.mode == sp.MODE_BLOCKED and res.ok is False and _BLOCK in (res.error or "")
    assert _run(sp.push_product(_db(), _new("BrandfOk"), blocked=False)).action == "create"


def test_price_push_refuses():
    res = _run(sp.push_variant_prices(_db(), _live("BrandfLux")))
    assert res.mode == sp.MODE_BLOCKED and res.ok is False


def test_image_push_refuses():
    db = _db()
    db.seed("catalog_products", [_live("BrandfLux")])
    res = _run(sp.push_image(db, {"image_id": "brandf_img", "product_id": "brandf_BrandfLux",
                                  "status": "APPROVED"}))
    assert res.mode == sp.MODE_BLOCKED


def test_push_all_and_the_scheduled_sync_skip_it(monkeypatch):
    """push_product_docs is the shared core of push-all and the 01:00/09:00 sync."""
    from api.services import shopify_live_sync as live_sync

    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    docs = [{**_new("BrandfLux"), "ecom": {"locally_modified": True}},
            {**_new("BrandfOk"), "ecom": {"locally_modified": True}}]
    out = _run(live_sync.push_product_docs(_db(), docs, current_user=_ADMIN, max_results=10))
    assert [r["target_id"] for r in out["results"]] == ["brandf_BrandfOk"]
    assert out["blocked_skipped"] == 1
    assert _BLOCK in out["refused"][0][1]


def test_the_collection_registry_never_counts_it_as_ready():
    from api.services.shopify_push.collection_registry import listing_facts

    db = _db()
    db.seed("catalog_products", [_new("BrandfLux"), _new("BrandfOk")])
    assert [f["brand"] for f in listing_facts(db, include_ready=True)] == ["BrandfOk"]


def test_the_add_edit_form_line_says_why(monkeypatch):
    from api.services import policy_engine as pe

    db = _db()
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(pe, "get_policy", lambda key, default=None: default)
    out = _run(prod_router.get_website_verdict(brand="BrandfLux", current_user=_ADMIN))
    assert out["online"] is False and _BLOCK in out["reason"]


def test_the_online_state_says_not_for_website():
    from api.services.online_catalog import doc_online_state

    assert doc_online_state(_db(), _new("BrandfLux"))["online"] == "NOT_FOR_WEBSITE"


def test_unblocking_lets_the_brand_publish_again():
    db = _db()
    assert _run(sp.push_product(db, _new("BrandfLux"), blocked=False)).mode == sp.MODE_BLOCKED
    _set_block(db, False)
    assert brand_website_refusal(db, "BrandfLux") is None
    assert _run(sp.push_product(db, _new("BrandfLux"), blocked=False)).action == "create"


# ---------------------------------------------------------------------------
# No way round the block: delete, rename, near-spelling twin (review fixes)
# ---------------------------------------------------------------------------


def test_an_admin_cannot_delete_a_blocked_brand(brands):
    db, _ = brands
    _set_block(db, True)
    with pytest.raises(HTTPException) as e:
        _run(ac.delete_brand("brandf_b1", current_user=_ADMIN))
    assert e.value.status_code == 403 and "Never sell online" in e.value.detail
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"}) is not None
    # An unblocked brand still deletes for an admin; SUPERADMIN may delete a blocked one.
    assert _run(ac.delete_brand("brandf_b2", current_user=_ADMIN))["deleted"] is True
    assert _run(ac.delete_brand("brandf_b1", current_user=_SUPER))["deleted"] is True


def test_an_admin_cannot_rename_a_blocked_brand(brands):
    db, _ = brands
    _set_block(db, True)
    with pytest.raises(HTTPException) as e:
        _run(ac.update_brand("brandf_b1", ac.BrandUpdate(name="BrandfLux Old"), current_user=_ADMIN))
    assert e.value.status_code == 403
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"})["name"] == "BrandfLux"
    # Sending the same name back (a normal full-form save) is not a rename.
    _run(ac.update_brand("brandf_b1", ac.BrandUpdate(name="BrandfLux", tier="PREMIUM"), current_user=_ADMIN))
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(name="BrandfLux Old"), current_user=_SUPER))
    assert out["name"] == "BrandfLux Old"


@pytest.mark.parametrize("spelling", [
    "BrandfLux", "brandflux", " Brandf Lux ", "BRANDF-LUX", "Brandf.Lux", "Brandf_Lux",
    "Brandf–Lux", "Brandf​Lux", "Brandf'Lux", "BrandfLux.", "Brandf/Lux",
])
def test_an_admin_cannot_add_or_rename_to_a_blocked_brand_spelling(brands, spelling):
    db, _ = brands
    _set_block(db, True)
    with pytest.raises(HTTPException) as e:
        _run(ac.create_brand(ac.BrandCreate(name=spelling, code="BFTWIN", tier="MASS"), current_user=_ADMIN))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        _run(ac.update_brand("brandf_b2", ac.BrandUpdate(name=spelling), current_user=_ADMIN))
    assert db["brand_masters"].find_one({"code": "BFTWIN"}) is None
    # SUPERADMIN may (a deliberate act, audited by the block itself).
    assert _run(ac.create_brand(ac.BrandCreate(name=spelling, code="BFTWIN", tier="MASS"),
                                current_user=_SUPER))["name"] == spelling


@pytest.mark.parametrize("spelling", [
    "Brandf-Lux", "BRANDF LUX", "Brandf.Lux", "Brandf_Lux", "Brandf–Lux", "Brandf​Lux",
])
def test_a_near_spelling_product_brand_is_still_refused(spelling):
    assert _BLOCK in brand_website_refusal(_db(), spelling)


@pytest.mark.parametrize("spelling", [
    "Ｒａｙ-Ｂａｎ",  # full-width Ray-Ban
    "𝐑ay-Ban",  # maths bold R
])
def test_a_unicode_look_alike_of_a_blocked_brand_is_refused(spelling):
    """Review of #1245: NFKC before the punctuation strip, so a full-width or
    maths-letter spelling collapses to the blocked brand's key."""
    from api.services.catalog_dictionary import brand_name_key

    assert brand_name_key(spelling) == brand_name_key("Ray-Ban") == "rayban"
    db = _db()
    db["brand_masters"].insert_one({"brand_id": "brandf_rb", "name": "Ray-Ban", "is_active": True,
                                    "sync_to_shopify_default": True, "never_sell_online": True})
    assert _BLOCK in brand_website_refusal(db, spelling)


def test_a_different_name_is_not_caught_by_the_block():
    """Matching ignores case, spaces and punctuation only."""
    assert brand_website_refusal(_db(), "BrandfOk") is None


def test_the_live_report_matches_with_the_gate_key(brands):
    db, _ = brands
    db["catalog_products"].insert_one({**_live("Brandf.LUX"), "sku": "BRANDF-dot", "id": "brandf_dot",
                                       "product_id": "brandf_dot"})
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert sorted(out["live_listings_not_taken_down"]) == ["BRANDF-BrandfLux", "BRANDF-dot"]


def test_an_unknown_status_is_refused(brands):
    with pytest.raises(HTTPException) as e:
        _run(ac.update_brand("brandf_b2", ac.BrandUpdate(status="PAUSED"), current_user=_ADMIN))
    assert e.value.status_code == 422
    out = _run(ac.update_brand("brandf_b2", ac.BrandUpdate(status=" inactive "), current_user=_ADMIN))
    assert out["status"] == "INACTIVE" and out["is_active"] is False


# ---------------------------------------------------------------------------
# Inactive is one fact: status and is_active move together
# ---------------------------------------------------------------------------


def test_a_brand_saved_inactive_is_off_the_website_and_out_of_the_product_brand_list(brands):
    db, _ = brands
    out = _run(ac.update_brand("brandf_b2", ac.BrandUpdate(status="INACTIVE"), current_user=_ADMIN))
    assert out["is_active"] is False
    assert "inactive" in brand_website_refusal(db, "BrandfOk")
    listed = _run(prod_router.get_brand_options(category=None, current_user=_ADMIN))
    assert "BrandfOk" not in [b["name"] for b in listed["brands"]]
    # Back to ACTIVE -> back on both.
    _run(ac.update_brand("brandf_b2", ac.BrandUpdate(status="ACTIVE"), current_user=_ADMIN))
    assert brand_website_refusal(db, "BrandfOk") is None
    listed = _run(prod_router.get_brand_options(category=None, current_user=_ADMIN))
    assert "BrandfOk" in [b["name"] for b in listed["brands"]]


def test_a_brand_created_inactive_is_inactive(brands):
    out = _run(ac.create_brand(ac.BrandCreate(name="BrandfNew", code="BFNEW", tier="MASS",
                                              status="INACTIVE"), current_user=_ADMIN))
    assert out["is_active"] is False
