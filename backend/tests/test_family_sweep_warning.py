"""Bulk publishing must not mislabel omitted sibling options as stock failures."""

import pytest

from api.services import shopify_push
from api.services.shopify_push import PushResult
from test_online_store_push import patched_db  # noqa: F401
from test_product_photo_order import _live_reordered


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_bulk_family_omission_is_a_catalogue_warning(client, auth_headers, patched_db, monkeypatch):
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one(_live_reordered())

    async def push(db, product, variants, blocked=None):
        return PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=True, code="OPTION_MISSING",
            listing={"held": [{"sku": "INCOMPLETE", "code": "OPTION_MISSING", "reason": "no lens size"}]},
        )

    async def stock(db):
        return PushResult(mode="LIVE", entity="stock", action="noop", ok=True)

    monkeypatch.setattr(shopify_push, "push_product", push)
    monkeypatch.setattr(shopify_push, "sync_stock_levels", stock)
    response = client.post("/api/v1/online-store/push/all-pending?entities=products", headers=auth_headers)
    assert response.status_code == 200, response.text
    bucket = response.json()["summary"]["products"]
    assert bucket["pushed"] == 1 and bucket["failed"] == 0
    assert bucket["variants_held"] == 1
    assert "stock_not_written" not in bucket and "stock_warning" not in bucket
