"""
Per-store website stock says only what IMS knows (rebuild of parked #1172).
=========================================================================
Four rules, each driven through the real writer with a MOCKED Shopify
transcript (no network, no Mongo):

  1. A size added to a listing that is already LIVE is created tracked and
     "stop selling at 0" IN THE SAME productVariantsBulkCreate call, and
     Shopify's answer must confirm both. Not confirmed on a live listing ->
     the listing is taken off the website (Draft) and the press says so.
  2. "live and SOLD OUT" (the writer's `sold_out` stamp) only when every
     listed SKU was accepted as 0 at EVERY mapped shop and nothing is
     unknown: a refused write, an unreadable shelf, an unmapped holder or a
     stray size each clears it.
  3. A read that died is UNKNOWN and named (STOCK_ONHAND_UNKNOWN), never a
     green no-op or "0 of 0": the sweep's catalogue read, its size-row read,
     the stray-size reads, the sale's online-status read.
  4. A shop whose write failed keeps its last-sent number, so removing its
     location later still zeroes what Shopify shows there.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_per_store_stock_honest.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402
from test_shopify_online_stock import (  # noqa: E402,F401 -- _reset is the autouse fixture
    INV_GID,
    INV_TWO,
    LOC_A,
    LOC_B,
    LOC_C,
    VARIANT_GID,
    _ThrottledTracking,
    _Spy,
    _baseline,
    _break_shop,
    _catalog_row,
    _db,
    _listed,
    _live,
    _ok_body,
    _product_body,
    _reset,
    _responses,
    _run,
    _set_error,
)

MINTED = "gid://shopify/ProductVariant/77"
INV_L = "gid://shopify/InventoryItem/777"


class _DeadFind(StrictCollection):
    """A collection whose find / find_one raise for the filters `hit` picks;
    every other read answers."""

    def __init__(self, base, hit, message="cursor died"):
        super().__init__(base.name, base.docs)
        self._hit = hit
        self._message = message

    def find(self, filter=None, *a, **k):
        if self._hit(filter or {}):
            raise RuntimeError(self._message)
        return super().find(filter, *a, **k)

    def find_one(self, filter=None, *a, **k):
        if self._hit(filter or {}):
            raise RuntimeError(self._message)
        return super().find_one(filter, *a, **k)


def _kill(db, name, hit, message="cursor died"):
    db._collections[name] = _DeadFind(db.get_collection(name), hit, message)


def _cat(db, pid="cat-1"):
    return db.get_collection("catalog_products").find_one({"id": pid})


# ---------------------------------------------------------------------------
# 1. a new size on a live listing
# ---------------------------------------------------------------------------


def _live_listing_gets_size_l():
    """cat-1 is LIVE (PUBLISHED, tracked) with sizes M and S on Shopify; IMS
    has just added size L (no gid yet), one unit of it at BV-A."""
    db = _db(a=2, b=1, c=0)
    db.seed("products", [{"product_id": "spine-S", "sku": "SP-1-S"}, {"product_id": "spine-L", "sku": "SP-1-L"}])
    db.get_collection("stock_units").insert_one(
        {"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-A", "status": "AVAILABLE"}
    )
    sent = {"quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}, "SP-1-S": {"BV-A": 0, "BV-B": 0, "BV-C": 0}},
            "tracked": True, "policy": "DENY"}
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status="PUBLISHED",
                                              locally_modified=True, online_stock=sent)])
    variants = [
        {"sku": "SP-1", "parent_product_id": "cat-1", "price": 1500, "option_size": "M",
         "shopify_variant_id": VARIANT_GID, "shopify_inventory_item_id": INV_GID},
        {"sku": "SP-1-S", "parent_product_id": "cat-1", "price": 1500, "option_size": "S",
         "shopify_variant_id": "gid://shopify/ProductVariant/6", "shopify_inventory_item_id": INV_TWO},
        {"sku": "SP-1-L", "parent_product_id": "cat-1", "price": 1500, "option_size": "L"},
    ]
    db.seed("catalog_variants", [dict(v) for v in variants])
    upd = _product_body("productUpdate")
    upd["data"]["productUpdate"]["product"]["variants"]["nodes"] = [
        {"id": VARIANT_GID, "selectedOptions": [{"name": "Size", "value": "M"}], "inventoryItem": {"id": INV_GID}},
        {"id": "gid://shopify/ProductVariant/6", "selectedOptions": [{"name": "Size", "value": "S"}],
         "inventoryItem": {"id": INV_TWO}},
    ]
    return db, variants, upd


def _created(*, tracked=True, policy="DENY"):
    """Shopify's productVariantsBulkCreate answer for size L. None leaves the
    field out of the answer (nothing confirms it)."""
    inv = {"id": INV_L}
    if tracked is not None:
        inv["tracked"] = tracked
    node = {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "L"}], "inventoryItem": inv}
    if policy is not None:
        node["inventoryPolicy"] = policy
    return _ok_body("productVariantsBulkCreate", productVariants=[node])


def _drafts(spy):
    """The take-down writes: productUpdate {id, status: DRAFT} and nothing else."""
    return [
        i for i, c in enumerate(spy.calls)
        if "productUpdate(" in c["query"] and set((c["variables"] or {}).get("input") or {}) == {"id", "status"}
        and c["variables"]["input"]["status"] == "DRAFT"
    ]


def _index(spy, marker):
    return [i for i, c in enumerate(spy.calls) if marker in c["query"]]


def test_1_a_new_size_on_a_live_listing_is_created_tracked_and_stop_at_zero_in_one_call(monkeypatch):
    """The create call itself carries tracked=true + DENY, and Shopify's answer
    confirming both is what makes the size safe: a refused re-send of tracking
    afterwards changes nothing on Shopify, so the press is the live-listing
    warning, never "WITHOUT LIMIT", and the listing stays on the website.
    Send the create row without `tracked` / `inventoryPolicy` -> the first half
    fails; treat a confirmed size as unconfirmed -> WITHOUT LIMIT -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _ThrottledTracking(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created()}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    (create,) = spy.calls_for("productVariantsBulkCreate")
    (row,) = create["variables"]["variants"]
    assert row["inventoryItem"] == {"sku": "SP-1-L", "tracked": True}, row
    assert row["inventoryPolicy"] == "DENY", row
    sel = shopify_push._VARIANTS_BULK_CREATE
    assert "inventoryPolicy" in sel and "tracked" in sel, "the answer must carry what it confirms"
    assert _drafts(spy) == [], "confirmed by Shopify -- the listing stays on the website"
    assert res.ok is True and res.code == shopify_push.STOCK_TRACKING_FAILED, res
    assert "re-confirmed" in res.error and "WITHOUT LIMIT" not in res.error, res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"
    assert _baseline(db)["tracked"] is True


@pytest.mark.parametrize(
    "answer",
    [_created(tracked=None, policy=None), _created(tracked=False), _created(policy="CONTINUE")],
    ids=["answer-silent", "untracked", "keeps-selling-at-0"],
)
def test_1_an_unconfirmed_new_size_on_a_live_listing_takes_the_listing_off_the_website(monkeypatch, answer):
    """Shopify created size L but its answer does not confirm tracked + stop
    selling at 0: L may sell without limit on a listing that is live. The
    listing goes to Draft right after the create (before any stock write or
    publish), IMS records Draft, the publish is skipped and the press says so,
    naming the size. Drop the take-down -> no Draft call, ok True -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": answer}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    drafts = _drafts(spy)
    assert len(drafts) == 1, [c["query"][:40] for c in spy.calls]
    assert _index(spy, "productVariantsBulkCreate")[0] < drafts[0] < _index(spy, "inventorySetQuantities")[0]
    assert spy.calls_for("publishablePublish") == [], "a listing taken down is not published again"
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "DRAFT" and ecom.get("taken_down_at"), ecom
    assert res.ok is False and res.reason == "publish_withheld", res
    assert res.code == shopify_push.STOCK_TRACKING_FAILED
    assert "SP-1-L" in res.error and "taken OFF the website (Draft)" in res.error, res.error


class _DraftRefused(_Spy):
    """Shopify refuses the take-down's {id, status: DRAFT} productUpdate."""

    async def __call__(self, db, query, variables):  # noqa: ARG002
        inp = (variables or {}).get("input") or {}
        if "productUpdate(" in query and set(inp) == {"id", "status"}:
            self.calls.append({"query": query, "variables": variables})
            return {"data": {"productUpdate": {"product": None, "userErrors": [
                {"field": ["status"], "message": "Throttled"}]}}}
        return await super().__call__(db, query, variables)


def test_1_a_refused_take_down_says_the_listing_is_still_live(monkeypatch):
    """The take-down itself refused: the listing is STILL on the website with
    a size that may sell without limit. Never "withheld / not made visible";
    the press fails and tells the owner to set it to Draft in Shopify admin.
    IMS keeps PUBLISHED (that is what the website shows)."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _DraftRefused(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=None)}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert len(_drafts(spy)) == 1
    assert res.ok is False and res.reason != "publish_withheld", res
    assert "STILL LIVE" in res.error and "Shopify admin" in res.error and "SP-1-L" in res.error, res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"
    assert spy.calls_for("publishablePublish") == []
