"""
A failed price push keeps the product queued and says so  (sync audit #7)
=========================================================================
Before this: the LIVE product push wrote the product, published it, and then
ran the variant price/barcode push on a fail-soft side channel. When THAT
step failed the product write-back had already cleared ecom.locally_modified,
the result said ok, the sync page said "pushed", and the twice-daily live
sync (which selects by that flag) never saw the product again -- the website
kept selling at the OLD price with nothing left in the queue to retry it.

THE ONE RULE under test (shopify_push/product.py): the single re-queue after
a press that reached Shopify -- `not published_ok OR price_not_synced` -> the
row goes back in the queue. ok stays True (the product IS live), and the
result carries code=PRICE_NOT_SYNCED + a plain-language `error`, which the
audit row, the sweep summary and the live-sync run summary all carry.

***** SAFETY-CRITICAL: every Shopify call is MOCKED (shopify_push._graphql is
monkeypatched); the dark test uses a spy that EXPLODES on any call. *****

Discriminating power (measured by reverting each rule; table in the PR):
the re-queue, the code on the result, the audit severity, the run-summary
count + failures entry, and the sweep tally each have a test that goes red
when that one rule is removed.

No emoji (Windows cp1252).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402

# The sibling rigs, not copies of them: the routing fake Shopify with a
# transcript, the open gates, the in-memory _DB, the live-sync `world` and
# the route-level patched DB.
from test_online_push_dirty_flag import _run  # noqa: E402
from test_online_store_push import patched_db  # noqa: E402,F401
from test_shopify_live_sync import _Audit, world  # noqa: E402,F401
from test_shopify_media_title_sync import (  # noqa: E402,F401
    GID,
    U1,
    _Shopify,
    _m,
    _nodes,
    _product,
    _seed,
    db,
    gates,
)

CODE = shopify_push.PRICE_NOT_SYNCED


class _ShopifyPriceFails(_Shopify):
    """The media-title rig, with the price step switchable to a userError."""

    def __init__(self, media_nodes=None, fail_prices=True):
        super().__init__(media_nodes)
        self.fail_prices = fail_prices

    async def __call__(self, db, query, variables):
        body = await super().__call__(db, query, variables)
        if self.fail_prices and self._op(query) == "imsVariantPricesUpdate":
            return {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [],
                        "userErrors": [{"field": ["price"], "message": "boom"}],
                    }
                }
            }
        return body


def _live_product():
    """Already on Shopify with its photograph there (IMS owns media 1, and it
    is on the product), edited since -> a LIVE update that publishes and then
    pushes the price."""
    return _product([U1], media_map=[(U1, _m(1))])


def _flag(db, pid="P1"):
    return (db["catalog_products"].find_one({"id": pid}) or {})["ecom"].get(
        "locally_modified"
    )


def _install(monkeypatch, fail_prices):
    fake = _ShopifyPriceFails(_nodes(1), fail_prices=fail_prices)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    return fake


# ---------------------------------------------------------------------------
# The engine: the row stays queued, the product stays live, the result says so
# ---------------------------------------------------------------------------


def test_failed_price_push_keeps_the_product_queued_and_carries_the_code(
    db, gates, monkeypatch
):
    fake = _install(monkeypatch, fail_prices=True)
    doc = _seed(db, _live_product())

    res = _run(shopify_push.push_product(db, doc, []))

    # The product IS live: written, published, ok -- never a false "failed".
    assert res.mode == "LIVE" and res.ok is True
    assert fake.ops().count("imsProductUpdate") == 1
    assert fake.ops().count("imsPublishablePublish") == 1
    assert fake.ops().count("imsVariantPricesUpdate") == 1
    assert res.variant_prices["ok"] is False
    # ...but it says so, for the operator and for the human.
    assert res.code == CODE
    assert res.error == shopify_push._PRICE_NOT_SYNCED_MSG
    assert res.reason is None
    # ...and it STAYS QUEUED so the next press / scheduled sync retries it.
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["locally_modified"] is True
    assert saved["ecom"]["shopify_product_id"] == GID
    assert saved["ecom"]["status"] == "PUBLISHED"


def test_successful_price_push_clears_the_flag_and_carries_no_code(
    db, gates, monkeypatch
):
    """The control: the same press with the price landing drains the queue."""
    fake = _install(monkeypatch, fail_prices=False)
    doc = _seed(db, _live_product())

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True and res.code is None and res.error is None
    assert res.variant_prices["ok"] is True
    assert fake.ops().count("imsVariantPricesUpdate") == 1
    assert _flag(db) is False


def test_dark_press_is_unchanged_and_makes_no_call(db, monkeypatch):
    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("a DARK press must never hit the network")

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _boom)
    doc = _seed(db, _live_product())

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.mode == "SIMULATED" and res.ok is True and res.code is None
    assert _flag(db) is True  # a dry-run never touches the queue


# ---------------------------------------------------------------------------
# The audit row the press writes
# ---------------------------------------------------------------------------


def test_audit_row_carries_the_code_and_is_a_warning(db, gates, monkeypatch):
    _install(monkeypatch, fail_prices=True)
    doc = _seed(db, _live_product())
    audit = _Audit()
    from api import dependencies as deps

    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit)

    res = _run(shopify_push.push_product(db, doc, []))
    ls.write_push_audit(res.to_dict(), {"user_id": "u1"})

    (row,) = audit.rows
    assert row["entity_id"] == "P1"
    assert row["details"]["ok"] is True
    assert row["details"]["code"] == CODE
    assert row["details"]["error"] == shopify_push._PRICE_NOT_SYNCED_MSG
    assert row["details"]["variant_prices"]["ok"] is False
    # Live at the OLD price is something to act on: it surfaces in the
    # warnings view, not buried under INFO with the clean pushes.
    assert row["severity"] == "WARNING"


# ---------------------------------------------------------------------------
# The scheduled live sync: counts it, names it, and picks it up again
# ---------------------------------------------------------------------------


def _live_world(world, monkeypatch, fail_prices):
    db, audit = world
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setenv(
        "SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/1"
    )
    shopify_push._publication_id_cache.clear()
    fake = _install(monkeypatch, fail_prices=fail_prices)
    db.seed("catalog_products", [_live_product()])
    return db, audit, fake


def test_live_sync_counts_it_names_it_and_retries_it_next_run(world, monkeypatch):
    db, audit, fake = _live_world(world, monkeypatch, fail_prices=True)

    run = _run(ls.sync_live_products(db, trigger="manual", actor="u"))
    assert run["selected"] == 1 and run["pushed_ok"] == 1 and run["failed"] == 0
    assert run["price_not_synced"] == 1
    assert run["failures"] == [
        {
            "product_id": "P1",
            "sku": "SKU-1",
            "name": "Ray-Ban RB2140",
            "code": CODE,
            "reason": None,
            "error": shopify_push._PRICE_NOT_SYNCED_MSG,
        }
    ]
    assert db["online_sync_runs"].docs[-1]["price_not_synced"] == 1
    assert [r["details"]["code"] for r in audit.rows if r["entity_id"] == "P1"] == [CODE]

    # Still queued -> the next run selects it AGAIN and re-tries the price.
    run2 = _run(ls.sync_live_products(db, trigger="manual", actor="u"))
    assert run2["selected"] == 1 and run2["price_not_synced"] == 1
    assert fake.ops().count("imsVariantPricesUpdate") == 2

    # The price lands -> the queue drains -> the run after selects nothing.
    fake.fail_prices = False
    run3 = _run(ls.sync_live_products(db, trigger="manual", actor="u"))
    assert run3["price_not_synced"] == 0 and run3["failures"] == []
    assert _flag(db) is False
    assert _run(ls.sync_live_products(db, trigger="manual", actor="u"))["selected"] == 0


# ---------------------------------------------------------------------------
# The manual sweep: its own line in the summary, still counted as pushed
# ---------------------------------------------------------------------------


def test_sweep_summary_has_its_own_price_not_synced_line(
    client, auth_headers, patched_db, monkeypatch
):
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one(_live_product())

    async def _live_at_old_price(db, product, variants, blocked=None):
        return shopify_push.PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=True, shopify_id=GID, code=CODE, error=shopify_push._PRICE_NOT_SYNCED_MSG,
        )

    monkeypatch.setattr(shopify_push, "push_product", _live_at_old_price)

    async def _no_stock(db):
        return shopify_push.PushResult(mode="LIVE", entity="stock", action="noop", ok=True)

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _no_stock)

    r = client.post(
        "/api/v1/online-store/push/all-pending?entities=products", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    products = body["summary"]["products"]
    assert products["pushed"] == 1 and products["failed"] == 0
    assert products["price_not_synced"] == 1
    assert body["pushed_count"] == 1
    assert body["results"][0]["code"] == CODE
