"""Bulk publishing must not mislabel omitted sibling options as stock failures."""

import pytest

from api.services import shopify_push
from api.services.shopify_push import PushResult
from test_online_store_push import patched_db  # noqa: F401
from test_product_photo_order import _live_reordered


MISSING = {"sku": "INCOMPLETE", "code": "OPTION_MISSING", "reason": "no lens size", "missing_option": "lens size"}
ADDS = {"sku": "SILVER-62", "code": "OPTION_MISSING", "reason": "adds a size the listing has none of"}


@pytest.mark.parametrize("held,omitted", [([MISSING], 1), ([MISSING, ADDS], 1), ([ADDS], 0)])
@pytest.mark.usefixtures("brand_is_for_the_website")
def test_bulk_family_omission_is_a_catalogue_warning(client, auth_headers, patched_db, monkeypatch, held, omitted):
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one(_live_reordered())

    async def push(db, product, variants, blocked=None):
        return PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=True, code="OPTION_MISSING",
            listing={"held": held},
        )

    async def stock(db):
        return PushResult(mode="LIVE", entity="stock", action="noop", ok=True)

    monkeypatch.setattr(shopify_push, "push_product", push)
    monkeypatch.setattr(shopify_push, "sync_stock_levels", stock)
    response = client.post("/api/v1/online-store/push/all-pending?entities=products", headers=auth_headers)
    assert response.status_code == 200, response.text
    bucket = response.json()["summary"]["products"]
    assert bucket["pushed"] == 1 and bucket["failed"] == 0
    # Only siblings left off for a missing option are counted (one shared
    # rule, shopify_push.missing_option_holds); any other hold keeps its line.
    assert bucket.get("variants_held", 0) == omitted
    if held == [MISSING]:
        assert "stock_not_written" not in bucket and "stock_warning" not in bucket


UNPRICED = {"sku": "NOPRICE-58", "code": "VARIANT_UNPRICED", "reason": "no price"}


@pytest.mark.parametrize("listing,seeded,code", [
    ({"held": [UNPRICED]}, None, "VARIANT_UNPRICED"),
    ({"held": [MISSING, ADDS]}, None, "OPTION_MISSING"),
    ({"held": []}, {"held_data": True, "code": "LISTING_OPTIONS_UNKNOWN", "held": "options unknown"}, "LISTING_OPTIONS_UNKNOWN"),
])
@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_catalogue_hold_has_its_own_line_never_sold_out(client, auth_headers, patched_db, monkeypatch, listing, seeded, code):
    # Review of #1276: a colour held for a catalogue reason read
    # "NO stock written (sold out)".
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one(_live_reordered())

    async def push(db, product, variants, blocked=None):
        return PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=True, code=code, listing=listing, variants_seeded=seeded,
            stock={"ok": True, "set": 0},
        )

    async def stock(db):
        return PushResult(mode="LIVE", entity="stock", action="noop", ok=True)

    monkeypatch.setattr(shopify_push, "push_product", push)
    monkeypatch.setattr(shopify_push, "sync_stock_levels", stock)
    response = client.post("/api/v1/online-store/push/all-pending?entities=products", headers=auth_headers)
    assert response.status_code == 200, response.text
    bucket = response.json()["summary"]["products"]
    assert bucket.get("catalogue_held") == 1, bucket
    assert "stock_not_written" not in bucket and "stock_warning" not in bucket


@pytest.mark.parametrize("code", ["SHOPIFY_LOCATION_UNMAPPED", "PRICE_NOT_SYNCED", "PHOTOS_NOT_SYNCED"])
@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_catalogue_hold_is_counted_whatever_code_wins_the_press(client, auth_headers, patched_db, monkeypatch, code):
    # Review of #1295: counted like variants_held, outside the code chain --
    # a stock / price / photo code winning the press never hides the hold.
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one(_live_reordered())

    async def push(db, product, variants, blocked=None):
        return PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=True, code=code, listing={"held": [UNPRICED]},
            stock={"ok": True, "set": 0},
        )

    async def stock(db):
        return PushResult(mode="LIVE", entity="stock", action="noop", ok=True)

    monkeypatch.setattr(shopify_push, "push_product", push)
    monkeypatch.setattr(shopify_push, "sync_stock_levels", stock)
    response = client.post("/api/v1/online-store/push/all-pending?entities=products", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["products"].get("catalogue_held") == 1, response.json()["summary"]
