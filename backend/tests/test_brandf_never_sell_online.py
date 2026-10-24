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
  * blocking TAKES DOWN the brand's live listings through the one take-down
    door (owner 2026-10-22); unblocking republishes nothing;
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


@pytest.mark.parametrize("changes", [
    {"sync_to_shopify_default": True},
    {"tier": "PREMIUM"},
    {"never_sell_online": True, "sync_to_shopify_default": True},
])
def test_blocked_brand_saves_normalize_default_even_when_block_is_omitted(brands, changes):
    db, _ = brands
    _set_block(db, True)
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(**changes), current_user=_ADMIN))
    assert out["never_sell_online"] is True
    assert out["sync_to_shopify_default"] is False
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"})["sync_to_shopify_default"] is False


def test_create_and_block_existing_brand_clear_sync_default(brands):
    db, _ = brands
    created = _run(ac.create_brand(ac.BrandCreate(
        name="BrandfNew", code="BFNEW", tier="MASS", never_sell_online=True,
        sync_to_shopify_default=True,
    ), current_user=_SUPER))
    assert created["sync_to_shopify_default"] is False
    blocked = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert blocked["sync_to_shopify_default"] is False
    unblocked = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=False), current_user=_SUPER))
    assert unblocked["sync_to_shopify_default"] is False
    enabled = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(sync_to_shopify_default=True), current_user=_ADMIN))
    assert enabled["sync_to_shopify_default"] is True


def _block_rows(audit):
    return [r for r in audit.rows if r["action"].startswith("BRAND_NEVER_SELL_ONLINE")]


def test_superadmin_block_takes_down_live_listings_simulated_while_dark(brands, monkeypatch):
    """Owner 2026-10-22: blocking takes the live listings down through the
    existing take-down door. DARK gates -> SIMULATED, zero network."""
    db, audit = brands

    async def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("a dark take-down must not call Shopify")

    monkeypatch.setattr(sp, "_graphql", _boom)
    monkeypatch.setattr(sp.product, "_graphql", _boom)
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert out["never_sell_online"] is True
    assert out["live_listings_take_down"] == [
        {"sku": "BRANDF-BrandfLux", "ok": True, "mode": "SIMULATED", "error": None}
    ]
    # A SIMULATED take-down stamps NOTHING on the twin (the Take down door's
    # rule): it still reads as live, so it is taken down once the gates go live.
    ecom = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"]
    for key in ("online_state", "delist_mode", "delist_reason", "taken_down_at"):
        assert key not in ecom
    on, done = _block_rows(audit)
    assert on["action"] == "BRAND_NEVER_SELL_ONLINE_ON" and on["user_id"] == "brandf_super"
    assert on["details"]["live_listings_to_take_down"] == ["BRANDF-BrandfLux"]
    assert done["action"] == "BRAND_NEVER_SELL_ONLINE_TAKE_DOWN"
    assert done["details"]["live_listings_take_down"] == out["live_listings_take_down"]
    # The take-down door's own chained push audit row rides too.
    assert any(r["action"] == "ONLINE_STORE_PUSH" and r["entity_id"] == "brandf_BrandfLux" for r in audit.rows)
    off = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=False), current_user=_SUPER))
    assert _block_rows(audit)[-1]["action"] == "BRAND_NEVER_SELL_ONLINE_OFF"
    assert off["live_listings_take_down"] == []


class _Shopify:
    """LIVE gates + a recording productUpdate double. No network."""

    def __init__(self, monkeypatch, user_errors=None):
        self.calls = []
        monkeypatch.setattr(sp.product, "_live_or_reason", lambda _db: (True, None))

        async def _graphql(_db, _query, variables):
            self.calls.append(variables)
            return {"data": {"productUpdate": {"userErrors": user_errors or []}}}

        monkeypatch.setattr(sp.product, "_graphql", _graphql)


def test_a_live_block_drafts_the_listing_on_shopify_and_keeps_its_id(brands, monkeypatch):
    db, _ = brands
    db["catalog_products"].insert_one({**_live("BrandfOk"), "ecom": {
        "shopify_product_id": "gid://shopify/Product/8"}})
    shop = _Shopify(monkeypatch)
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert out["live_listings_take_down"] == [
        {"sku": "BRANDF-BrandfLux", "ok": True, "mode": "LIVE", "error": None}
    ]
    # Only the blocked brand's listing, and only to Draft (never a delete).
    assert shop.calls == [{"input": {"id": "gid://shopify/Product/9", "status": "DRAFT"}}]
    ecom = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"]
    assert ecom["status"] == "DRAFT" and ecom["taken_down_at"]
    assert ecom["shopify_product_id"] == "gid://shopify/Product/9"
    assert ecom["online_state"] == "DELISTED" and ecom["delist_mode"] == "LIVE"

    # Unblocking republishes NOTHING: no Shopify call, the listing stays taken
    # down and is not queued -- it returns through the normal publish path.
    _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=False), current_user=_SUPER))
    assert len(shop.calls) == 1
    ecom = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"]
    assert ecom["taken_down_at"] and not ecom.get("locally_modified")


def test_a_refused_take_down_is_reported_and_the_save_stands(brands, monkeypatch):
    db, audit = brands
    _Shopify(monkeypatch, user_errors=[{"field": ["id"], "message": "nope"}])
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert out["never_sell_online"] is True
    [res] = out["live_listings_take_down"]
    assert res["ok"] is False and res["mode"] == "LIVE" and "nope" in res["error"]
    ecom = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"]
    assert ecom["online_state"] == "DELIST_FAILED" and not ecom.get("taken_down_at")
    assert _block_rows(audit)[-1]["details"]["live_listings_take_down"] == [res]


def test_the_decision_row_is_written_before_the_take_down_loop(brands, monkeypatch):
    """Review of #1252: a request that dies mid-loop must still have left the
    ON row (with the listings it set out to take down)."""
    _, audit = brands
    seen = []
    monkeypatch.setattr(sp.product, "_live_or_reason", lambda _db: (True, None))

    async def _graphql(_db, _q, _v):
        seen.append([r["action"] for r in audit.rows])
        return {"data": {"productUpdate": {"userErrors": []}}}

    monkeypatch.setattr(sp.product, "_graphql", _graphql)
    _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert seen == [["BRAND_NEVER_SELL_ONLINE_ON"]]


def _two_live(db):
    db["catalog_products"].insert_one({**_live("BrandfLux"), "id": "brandf_lux2", "product_id": "brandf_lux2",
                                       "sku": "BRANDF-lux2",
                                       "ecom": {"shopify_product_id": "gid://shopify/Product/10"}})


def test_a_take_down_cut_off_midway_is_finished_by_a_superadmin_re_save(brands, monkeypatch):
    """Review of #1252: the request dies after the first take-down (timeout /
    cancel). A SUPERADMIN re-save (true -> true) takes down the rest, and a
    further re-save is a no-op: no Shopify call, no audit row."""
    db, audit = brands
    _two_live(db)
    calls = []
    monkeypatch.setattr(sp.product, "_live_or_reason", lambda _db: (True, None))

    async def _graphql(_db, _q, variables):
        calls.append(variables["input"]["id"])
        if len(calls) == 2:
            raise asyncio.CancelledError()  # the request was cut off here
        return {"data": {"productUpdate": {"userErrors": []}}}

    monkeypatch.setattr(sp.product, "_graphql", _graphql)
    with pytest.raises(asyncio.CancelledError):
        _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"})["never_sell_online"] is True
    _first, second = calls

    # An ADMIN's save of the blocked brand retries nothing.
    _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_ADMIN))
    assert len(calls) == 2

    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert calls[2:] == [second], "only the leftover is retried"
    assert [r["ok"] for r in out["live_listings_take_down"]] == [True]
    assert _block_rows(audit)[-2]["action"] == "BRAND_NEVER_SELL_ONLINE_RETRY"
    rows_before = len(audit.rows)
    again = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert again["live_listings_take_down"] == [] and len(calls) == 3 and len(audit.rows) == rows_before


def _sync(db, limit=50):
    from api.services import shopify_live_sync as live_sync

    return _run(live_sync.sync_live_products(db, trigger="manual", actor="u", limit=limit))


def test_a_failed_take_down_is_finished_by_the_next_sync(brands, monkeypatch):
    """Review of #1252: a refused take-down leaves the listing live; the next
    sync names it and takes it down, though it is NOT dirty."""
    db, _ = brands
    _Shopify(monkeypatch, user_errors=[{"field": ["id"], "message": "nope"}])
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert out["live_listings_take_down"][0]["ok"] is False
    assert not db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"].get("locally_modified")

    still = _sync(db)  # Shopify still says no: named, retried next run
    assert still["never_sell_live"] == 1 and still["never_sell_taken_down"] == 0
    [named] = [f for f in still["failures"] if f["sku"] == "BRANDF-BrandfLux"]
    assert named["code"] == "NEVER_SELL_STILL_LIVE" and "take-down failed" in named["error"]

    shop = _Shopify(monkeypatch)  # Shopify takes it now
    run = _sync(db)
    assert run["never_sell_live"] == 1 and run["never_sell_taken_down"] == 1
    assert shop.calls == [{"input": {"id": "gid://shopify/Product/9", "status": "DRAFT"}}]
    ecom = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"]
    assert ecom["status"] == "DRAFT" and ecom["taken_down_at"] and ecom["online_state"] == "DELISTED"
    assert _sync(db)["never_sell_live"] == 0 and len(shop.calls) == 1


def test_a_dark_sync_names_the_listing_once_and_stamps_nothing(brands, monkeypatch):
    db, _ = brands

    async def _boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("a dark sync must not call Shopify")

    monkeypatch.setattr(sp.product, "_graphql", _boom)
    _set_block(db, True)
    db["catalog_products"].update_one({"sku": "BRANDF-BrandfLux"}, {"$set": {"ecom.locally_modified": True}})
    run = _sync(db)
    named = [f for f in run["failures"] if f["sku"] == "BRANDF-BrandfLux"]
    assert [f["code"] for f in named] == ["NEVER_SELL_STILL_LIVE"], "named once, not also LIVE_NOT_PUSHED"
    assert "writes are off" in named[0]["error"]
    ecom = db["catalog_products"].find_one({"sku": "BRANDF-BrandfLux"})["ecom"]
    assert "online_state" not in ecom and "taken_down_at" not in ecom


def test_the_sync_take_down_is_bounded_by_the_run_limit(brands, monkeypatch):
    db, _ = brands
    _two_live(db)
    _set_block(db, True)
    shop = _Shopify(monkeypatch)
    run = _sync(db, limit=1)
    assert len(shop.calls) == 1 and run["never_sell_taken_down"] == 1 and run["limit_reached"] is True
    [left] = [f for f in run["failures"] if f["code"] == "NEVER_SELL_STILL_LIVE"]
    assert "take-down cap" in left["error"]


def test_a_failed_live_listing_read_is_reported_not_read_as_nothing(brands, monkeypatch):
    """Review of #1252: the save stands, but neither the response nor the
    decision row may say "nothing to take down" when the read failed."""
    from api.services import online_delist

    db, audit = brands

    def _boom(*_a, **_k):
        raise RuntimeError("mongo down")

    monkeypatch.setattr(online_delist, "never_sell_live_listings", _boom)
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert out["never_sell_online"] is True
    assert db["brand_masters"].find_one({"brand_id": "brandf_b1"})["never_sell_online"] is True
    assert "read failed" in out["live_listings_read_error"] and out["live_listings_take_down"] == []
    [on] = _block_rows(audit)
    assert on["action"] == "BRAND_NEVER_SELL_ONLINE_ON"
    assert on["details"]["live_listings_read_error"] == out["live_listings_read_error"]
    # A SUPERADMIN re-save while the read still fails says so too (no silent no-op).
    again = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert "read failed" in again["live_listings_read_error"]
    assert _block_rows(audit)[-1]["action"] == "BRAND_NEVER_SELL_ONLINE_RETRY"


def test_listing_facts_carry_the_catalog_id():
    """The take-down resolves each live listing by its catalog id (a SKU can
    repeat across twins): listing_facts must keep carrying it."""
    from api.services.shopify_push.collection_registry import listing_facts

    db = _db()
    db.seed("catalog_products", [_live("BrandfOk")])
    assert [f["id"] for f in listing_facts(db)] == ["brandf_BrandfOk"]


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
    "Ray-Ban™",  # trade mark sign: NFKC alone makes it "...TM"
    "Ray-Ban℠",  # service mark sign -> "SM"
    "Ray-Ban№",  # numero sign -> "No"
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


def test_brand_master_existence_uses_the_same_key():
    """Review of #1251: the Brand Master EXISTENCE lookup was exact-spelling
    while the block used brand_name_key -- "Montblanc" against master
    "Mont Blanc" was refused as "not in Brand Master". One rule now."""
    db = _db()
    db["brand_masters"].insert_one({"brand_id": "brandf_mb", "name": "Mont Blanc", "is_active": True,
                                    "tier": "LUXURY", "sync_to_shopify_default": True})
    assert brand_website_refusal(db, "Montblanc") is None
    assert brand_website_refusal(db, "MONT-BLANC") is None
    from api.services.catalog_dictionary import load_brand_tier

    assert load_brand_tier(db, "Montblanc") == "LUXURY"


def test_an_exact_spelling_wins_over_a_key_twin():
    from api.services.catalog_dictionary import load_brand_tier

    db = _db()
    db["brand_masters"].insert_many([
        {"brand_id": "brandf_t1", "name": "Mont-Blanc", "is_active": True, "tier": "MASS"},
        {"brand_id": "brandf_t2", "name": "Mont Blanc", "is_active": True, "tier": "LUXURY"},
    ])
    assert load_brand_tier(db, "mont blanc") == "LUXURY"
    assert load_brand_tier(db, "Mont-Blanc") == "MASS"


def test_key_twins_that_disagree_on_tier_are_a_gap_not_a_guess():
    """Review of #1252 (money): no exact spelling, twins disagree -> None."""
    from api.services.catalog_dictionary import load_brand_tier

    db = _db()
    db["brand_masters"].insert_many([
        {"brand_id": "brandf_t1", "name": "Mont-Blanc", "is_active": True, "tier": "MASS"},
        {"brand_id": "brandf_t2", "name": "Mont Blanc", "is_active": True, "tier": "LUXURY"},
    ])
    assert load_brand_tier(db, "Montblanc") is None
    db["brand_masters"].update_one({"brand_id": "brandf_t1"}, {"$set": {"tier": "LUXURY"}})
    assert load_brand_tier(db, "Montblanc") == "LUXURY"  # twins that agree are fine


def test_an_inactive_exact_row_never_falls_through_to_an_active_twin():
    from api.services.catalog_dictionary import load_brand_tier

    db = _db()
    db["brand_masters"].insert_many([
        {"brand_id": "brandf_i1", "name": "Mont Blanc", "is_active": False, "tier": "LUXURY",
         "sync_to_shopify_default": True},
        {"brand_id": "brandf_i2", "name": "Montblanc", "is_active": True, "tier": "MASS",
         "sync_to_shopify_default": True},
    ])
    assert load_brand_tier(db, "Mont Blanc") is None
    assert "inactive" in brand_website_refusal(db, "Mont Blanc")
    assert load_brand_tier(db, "Montblanc") == "MASS"
    assert brand_website_refusal(db, "Montblanc") is None


def test_key_twins_that_disagree_on_the_website_are_refused():
    db = _db()
    db["brand_masters"].insert_many([
        {"brand_id": "brandf_w1", "name": "Mont-Blanc", "is_active": True, "sync_to_shopify_default": True},
        {"brand_id": "brandf_w2", "name": "Mont Blanc", "is_active": True, "sync_to_shopify_default": False},
    ])
    assert "disagree" in brand_website_refusal(db, "Montblanc")
    assert brand_website_refusal(db, "Mont-Blanc") is None  # the exact row decides


def test_subbrand_options_follow_the_same_key():
    from api.services.catalog_dictionary import load_subbrand_options

    db = _db()
    db["brand_masters"].insert_one({"brand_id": "brandf_mb", "name": "Mont Blanc", "is_active": True})
    db.seed("subbrand_masters", [{"brand_id": "brandf_mb", "name": "Meisterstuck"}])
    assert load_subbrand_options(db, "Montblanc") == ["Meisterstuck"]
    assert load_subbrand_options(db, "Mont Blanc") == ["Meisterstuck"]


def test_a_decomposed_accent_matches_the_composed_brand():
    """Review of #1251: NFC before the first strip -- a decomposed accent's
    combining mark is not a word character, so the strip used to drop it."""
    from api.services.catalog_dictionary import brand_name_key

    decomposed, composed = "Café Lux", "Café Lux"
    assert brand_name_key(decomposed) == brand_name_key(composed) == "cafélux"
    db = _db()
    db["brand_masters"].insert_one({"brand_id": "brandf_cafe", "name": composed, "is_active": True,
                                    "sync_to_shopify_default": True, "never_sell_online": True})
    assert _BLOCK in brand_website_refusal(db, decomposed)


def test_a_different_name_is_not_caught_by_the_block():
    """Matching ignores case, spaces and punctuation only."""
    assert brand_website_refusal(_db(), "BrandfOk") is None


def test_the_live_report_matches_with_the_gate_key(brands):
    db, _ = brands
    db["catalog_products"].insert_one({**_live("Brandf.LUX"), "sku": "BRANDF-dot", "id": "brandf_dot",
                                       "product_id": "brandf_dot"})
    out = _run(ac.update_brand("brandf_b1", ac.BrandUpdate(never_sell_online=True), current_user=_SUPER))
    assert sorted(r["sku"] for r in out["live_listings_take_down"]) == ["BRANDF-BrandfLux", "BRANDF-dot"]


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
