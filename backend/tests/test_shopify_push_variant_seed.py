"""
Tests for the IMS -> Shopify CREATE-side variant seeding (price + SKU) fix.

THE BUG (pre-fix): build_product_input deliberately sent no price and no SKU,
because ProductInput carries neither in the 2024-04+ product model. A
productCreate therefore landed a Shopify product whose default variant was
price 0.00 with NO SKU -- and it could never be repaired automatically, because
the write-back stored only ecom.shopify_product_id, never a ProductVariant gid.
That was tolerable while BVI owned Shopify; BVI was retired on 2026-07-20 and
IMS is now the sole writer, so a created product with no price is a live defect.

THE FIX (covered here):
  * CREATE seeds the variants Shopify mints (productVariantsBulkUpdate) with
    price / compareAtPrice / barcode / inventoryItem.sku, and creates any
    REMAINING IMS variant (productVariantsBulkCreate).
  * Every returned ProductVariant gid is written back --
    ecom.shopify_variant_id on the product + catalog_variants.shopify_variant_id
    -- so a later price push can find the money.
  * INVENTORY-ITEM CAPTURE (oversell-guard publish precondition, stacked on the
    seeding fix): the same returned variants carry inventoryItem { id }, and
    that gid is persisted too -- catalog_variants.shopify_inventory_item_id per
    variant row, ecom.shopify_inventory_item_id for a no-variant-row product --
    the exact fields the stock write-back resolver reads
    (online_catalog.online_variant_targets_for_skus / inventory_items_for_skus,
    online_sync_health._inventory_item_id_for_sku). Section 6 proves the
    resolver finds a freshly pushed product's inventory item.
  * ADVERSARIAL-PANEL MUST-FIXES (sections 5 + 7): publish-on-create is
    WITHHELD unless seeding succeeded with a PRICED row (an ACTIVE product can
    never go live at 0.00); variant matching is gid-FIRST so an option-label
    rename can never mint a duplicate live variant or re-point a stock target;
    a seed row carries an EXPLICIT compareAtPrice null when mrp <= price so a
    stale strikethrough is cleared (MRP-display compliance).
  * UPDATE of an already-mapped product is UNCHANGED by default (the ~4,400 live
    products are never silently re-priced); opt in with
    SHOPIFY_PUSH_PRICE_ON_UPDATE=1.
  * Optional Online Store publish on create, default OFF
    (SHOPIFY_PUBLISH_ON_CREATE), never for a DRAFT.
  * The three DARK gates and the SIMULATED dry-run are untouched.

***** SAFETY-CRITICAL: every Shopify call is MOCKED. ***** The network boundary
shopify_push._graphql is monkeypatched in every LIVE test; the DARK tests
install a boundary that EXPLODES if reached.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_push_variant_seed.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================


def _run(coro):
    return asyncio.run(coro)


class _EngineDB:
    """Minimal in-memory db the engine can use directly (db["name"])."""

    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


class _RouterSpy:
    """A fake shopify_push._graphql that answers PER MUTATION (the seeding flow
    issues productCreate -> productVariantsBulkUpdate -> productVariantsBulkCreate),
    recording every call for assertions."""

    def __init__(self, responses):
        self.calls = []
        self._responses = responses

    async def __call__(self, db, query, variables):
        self.calls.append({"query": query, "variables": variables})
        for marker, body in self._responses.items():
            if marker in query:
                return body
        return {"data": {}}

    def call_for(self, marker):
        for c in self.calls:
            if marker in c["query"]:
                return c
        return None

    def count_for(self, marker):
        return sum(1 for c in self.calls if marker in c["query"])


def _force_live(monkeypatch, responses):
    """Open all three gates on shopify_push's OWN namespace and replace the
    network boundary with the routing spy."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {
            "shop_url": "test.myshopify.com",
            "access_token": "shpat_test",
            "source": "vault",
        },
    )
    spy = _RouterSpy(responses)
    monkeypatch.setattr(shopify_push, "_graphql", spy)
    return spy


def _force_dark(monkeypatch, reason="writes_off"):
    """Close ONE gate so the engine is SIMULATED, and make the network boundary
    explode if anything reaches it."""
    creds = {"shop_url": "x", "access_token": "y", "source": "vault"}
    monkeypatch.setattr(
        shopify_push, "ims_shopify_writes_enabled", lambda: reason != "writes_off"
    )
    monkeypatch.setattr(
        shopify_push,
        "shopify_dispatch_mode",
        lambda: "off" if reason == "dispatch_off" else "live",
    )
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": None if reason == "no_creds" else creds,
    )

    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("DARK push must not hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)


def _create_response(variant_nodes, gid="gid://shopify/Product/900"):
    return {
        "data": {
            "productCreate": {
                "product": {
                    "id": gid,
                    "handle": "rb",
                    "variants": {"nodes": variant_nodes},
                },
                "userErrors": [],
            }
        }
    }


_DEFAULT_VARIANT_NODE = {
    "id": "gid://shopify/ProductVariant/5001",
    "title": "Default Title",
    "selectedOptions": [{"name": "Title", "value": "Default Title"}],
}


# A realistic no-variant eyewear product: MRP + offer on the spine, a SKU and an
# internal barcode. Exactly the shape the 2,032 draft pushes carried.
def _product(**over):
    doc = {
        "id": "P1",
        "title": "Ray-Ban Aviator",
        "sku": "BV-RB-0001",
        "barcode": "2000000000017",
        "mrp": 12990,
        "offer_price": 10990,
        "ecom": {"status": "PUBLISHED"},
    }
    doc.update(over)
    return doc


# ===========================================================================
# 1. Pure builders -- what we WANT on the Shopify variant
# ===========================================================================


def test_seed_rows_carry_price_compare_at_sku_and_barcode():
    rows = shopify_push.build_variant_seed_rows(_product(), [])
    assert len(rows) == 1  # ONE product-level row for a no-variant product
    row = rows[0]["row"]
    assert row["price"] == "10990.00"
    assert row["compareAtPrice"] == "12990.00"  # MRP strikethrough
    # SKU lives on the inventory item in the 2024-04+ product model.
    assert row["inventoryItem"] == {"sku": "BV-RB-0001"}
    # The product's `barcode` here is our INTERNAL store barcode (GS1 20-29,
    # restricted distribution). It must NOT be published as a public GTIN --
    # Shopify republishes that field into the Google/Meta Shopping feeds. This
    # assertion used to expect "2000000000017"; the 2,032 draft pushes of
    # 2026-07-20 carried exactly that shape, which is the leak now closed.
    assert "barcode" not in row
    assert rows[0]["option_values"] == []  # nothing to create; update-only
    assert rows[0]["key"] == ("", "")  # matches Shopify's "Default Title"


def test_seed_rows_publish_a_real_manufacturer_gtin():
    """The counterpart to the guard above: a genuine GTIN still gets pushed."""
    rows = shopify_push.build_variant_seed_rows(
        _product(gtin="8056597720373"), []
    )
    assert rows[0]["row"]["barcode"] == "8056597720373"


def test_seed_rows_never_send_a_zero_price_but_still_send_the_sku():
    """A product with no usable price must NOT be given price 0.00 -- but its
    SKU is still worth pushing (that is the join key for online orders)."""
    rows = shopify_push.build_variant_seed_rows(
        {"id": "P9", "sku": "BV-NOPRICE", "ecom": {}}, []
    )
    assert len(rows) == 1
    assert "price" not in rows[0]["row"]
    assert rows[0]["row"]["inventoryItem"] == {"sku": "BV-NOPRICE"}
    # Nothing at all to say -> no row (and therefore no mutation).
    assert shopify_push.build_variant_seed_rows({"id": "P0", "ecom": {}}, []) == []


def test_seed_rows_per_variant_use_variant_pricing_and_options():
    product = _product()
    variants = [
        {
            "sku": "BV-RB-0001-BLK",
            "option_color": "Black",
            "discounted_price": 9990,
            "compare_at_price": 12990,
            "gtin": "8056597857239",
        },
        {"sku": "BV-RB-0001-GLD", "option_color": "Gold"},
    ]
    rows = shopify_push.build_variant_seed_rows(product, variants)
    assert [r["row"]["inventoryItem"]["sku"] for r in rows] == [
        "BV-RB-0001-BLK",
        "BV-RB-0001-GLD",
    ]
    assert rows[0]["row"]["price"] == "9990.00"
    assert rows[0]["row"]["barcode"] == "8056597857239"
    # Second variant has no own price -> falls back to the parent offer price.
    assert rows[1]["row"]["price"] == "10990.00"
    assert rows[0]["option_values"] == [{"optionName": "Color", "name": "Black"}]


def test_assign_seed_rows_matches_by_option_key_and_flags_the_rest_for_create():
    product = _product()
    variants = [
        {"sku": "S-BLK", "option_color": "Black"},
        {"sku": "S-GLD", "option_color": "Gold"},
    ]
    rows = shopify_push.build_variant_seed_rows(product, variants)
    # productCreate materialises ONLY the first option-value combination.
    nodes = [
        {
            "id": "gid://shopify/ProductVariant/5001",
            "selectedOptions": [{"name": "Color", "value": "Black"}],
        }
    ]
    upd, crt, crt_vars, pairs, skipped = shopify_push._assign_seed_rows(rows, nodes)
    assert [r["id"] for r in upd] == ["gid://shopify/ProductVariant/5001"]
    assert len(crt) == 1 and crt[0]["optionValues"] == [
        {"optionName": "Color", "name": "Gold"}
    ]
    assert crt_vars[0]["sku"] == "S-GLD"
    # Pairs are (variant, gid, inventory_item_gid) -- the node above carries no
    # inventoryItem, so the third member is None (set-only, never cleared).
    assert pairs == [(variants[0], "gid://shopify/ProductVariant/5001", None)]
    assert skipped == 0


# ===========================================================================
# 2. The three DARK gates still hold -- SIMULATED plan, zero network
# ===========================================================================


@pytest.mark.parametrize("reason", ["writes_off", "dispatch_off", "no_creds"])
def test_dark_create_plans_price_and_sku_without_touching_the_network(
    monkeypatch, reason
):
    _force_dark(monkeypatch, reason)
    res = _run(shopify_push.push_product(_EngineDB(), _product(), []))
    assert res.mode == "SIMULATED" and res.ok is True and res.action == "create"
    assert res.reason  # still explains WHY we are dark
    # The dry-run now SHOWS the money that would be sent.
    plan = res.variants_seeded
    assert plan is not None
    assert plan["variants"][0]["price"] == "10990.00"
    assert plan["variants"][0]["inventoryItem"] == {"sku": "BV-RB-0001"}
    # ...and the ProductInput itself is unchanged (still no price/sku on it).
    assert "price" not in res.payload and "sku" not in res.payload


def test_dark_update_does_not_plan_a_reprice_by_default(monkeypatch):
    """An ALREADY-SEEDED product (carries a stored default-variant gid, i.e. a
    previous create-seed succeeded) is left alone on an ordinary update -- the
    flag protects an already-priced product from a silent re-price, it does
    NOT gate the repair-only path (see the sibling _never_seeded test below,
    which covers the product that has NO stored gid anywhere)."""
    _force_dark(monkeypatch, "writes_off")
    product = _product()
    product["ecom"] = {
        "status": "PUBLISHED",
        "shopify_product_id": "gid://shopify/Product/111",
        "shopify_variant_id": "gid://shopify/ProductVariant/5001",
        "shopify_inventory_item_id": "gid://shopify/InventoryItem/7001",
    }
    res = _run(shopify_push.push_product(_EngineDB(), product, []))
    assert res.action == "update"
    assert res.variants_seeded is None  # nothing would be re-priced


def test_dark_update_never_seeded_plans_a_repair_seed_regardless_of_the_flag(
    monkeypatch,
):
    """#944 follow-up: a mapped product (has shopify_product_id) that was NEVER
    seeded -- no stored shopify_variant_id / shopify_inventory_item_id anywhere
    -- must plan a repair seed on an ordinary update even with
    SHOPIFY_PUSH_PRICE_ON_UPDATE OFF. Before this fix such a product was
    permanently stuck at Shopify's auto-created 0.00/no-SKU with no self-heal
    path (a failed create-seed, or a pre-#943 push, could never be repaired
    short of manually arming the global re-price flag)."""
    _force_dark(monkeypatch, "writes_off")
    product = _product()
    product["ecom"] = {
        "status": "PUBLISHED",
        "shopify_product_id": "gid://shopify/Product/111",
        # NO shopify_variant_id / shopify_inventory_item_id -> never seeded.
    }
    res = _run(shopify_push.push_product(_EngineDB(), product, []))
    assert res.action == "update"
    plan = res.variants_seeded
    assert plan is not None
    assert plan["variants"][0]["price"] == "10990.00"
    assert plan["variants"][0]["inventoryItem"] == {"sku": "BV-RB-0001"}


# ===========================================================================
# 3. LIVE create -- price + SKU actually sent, variant gid written back
# ===========================================================================


def test_live_create_sends_price_and_sku_then_writes_back_the_variant_gid(monkeypatch):
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [
                            {"id": "gid://shopify/ProductVariant/5001"}
                        ],
                        "userErrors": [],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})

    res = _run(shopify_push.push_product(db, product, []))
    assert res.mode == "LIVE" and res.ok is True and res.action == "create"

    # The variant DID get priced + SKU'd (this is the whole defect).
    call = spy.call_for("productVariantsBulkUpdate")
    assert call is not None, "create must seed the variant price/sku"
    assert call["variables"]["productId"] == "gid://shopify/Product/900"
    row = call["variables"]["variants"][0]
    assert row["id"] == "gid://shopify/ProductVariant/5001"
    assert row["price"] == "10990.00"
    assert row["compareAtPrice"] == "12990.00"
    assert row["inventoryItem"] == {"sku": "BV-RB-0001"}
    assert res.variants_seeded["updated"] == 1
    assert res.variants_seeded["errors"] == []

    # WRITE-BACK: both the product gid AND the variant gid are persisted, so a
    # later price change has a handle to update.
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_product_id"] == "gid://shopify/Product/900"
    assert saved["ecom"]["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert saved["ecom"]["locally_modified"] is False


def test_live_create_creates_the_remaining_variants_and_writes_back_each_gid(
    monkeypatch,
):
    """productCreate only ever materialises ONE variant, so a 2-colour product
    needs the second created -- and BOTH catalog_variants rows must end up with
    their Shopify gid."""
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response(
                [
                    {
                        "id": "gid://shopify/ProductVariant/5001",
                        "selectedOptions": [{"name": "Color", "value": "Black"}],
                    }
                ]
            ),
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [
                            {"id": "gid://shopify/ProductVariant/5001"}
                        ],
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkCreate": {
                "data": {
                    "productVariantsBulkCreate": {
                        "productVariants": [
                            {
                                "id": "gid://shopify/ProductVariant/5002",
                                "selectedOptions": [{"name": "Color", "value": "Gold"}],
                            }
                        ],
                        "userErrors": [],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    db["catalog_variants"].insert_one(
        {
            "variant_id": "V1",
            "sku": "S-BLK",
            "parent_product_id": "P1",
            "option_color": "Black",
        }
    )
    db["catalog_variants"].insert_one(
        {
            "variant_id": "V2",
            "sku": "S-GLD",
            "parent_product_id": "P1",
            "option_color": "Gold",
        }
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [
        db["catalog_variants"].find_one({"sku": "S-BLK"}),
        db["catalog_variants"].find_one({"sku": "S-GLD"}),
    ]

    res = _run(shopify_push.push_product(db, product, variants))
    assert res.ok is True
    assert res.variants_seeded["updated"] == 1
    assert res.variants_seeded["created"] == 1
    assert res.variants_seeded["errors"] == []

    created_call = spy.call_for("productVariantsBulkCreate")
    assert created_call["variables"]["variants"][0]["optionValues"] == [
        {"optionName": "Color", "name": "Gold"}
    ]
    assert created_call["variables"]["variants"][0]["inventoryItem"] == {"sku": "S-GLD"}

    assert (
        db["catalog_variants"].find_one({"sku": "S-BLK"})["shopify_variant_id"]
        == "gid://shopify/ProductVariant/5001"
    )
    assert (
        db["catalog_variants"].find_one({"sku": "S-GLD"})["shopify_variant_id"]
        == "gid://shopify/ProductVariant/5002"
    )


def test_live_create_harvests_partially_successful_bulk_create(monkeypatch):
    """#944 follow-up: productVariantsBulkCreate can return BOTH userErrors AND
    a non-empty productVariants list -- some rows in the chunk were rejected,
    others were actually created on Shopify. The old code `continue`d past the
    body on ANY error, discarding the ones that DID succeed: they existed on
    Shopify but their ProductVariant/InventoryItem gids were never written
    back, so IMS could neither re-price nor oversell-guard them again. Gold
    (created) must be harvested and written back even though Silver (in the
    SAME bulk-create call) failed."""
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response(
                [
                    {
                        "id": "gid://shopify/ProductVariant/5001",
                        "selectedOptions": [{"name": "Color", "value": "Black"}],
                    }
                ]
            ),
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [
                            {"id": "gid://shopify/ProductVariant/5001"}
                        ],
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkCreate": {
                "data": {
                    "productVariantsBulkCreate": {
                        # Gold WAS created; Silver is simply absent (rejected) --
                        # a real partial-success Shopify response shape.
                        "productVariants": [
                            {
                                "id": "gid://shopify/ProductVariant/5002",
                                "selectedOptions": [
                                    {"name": "Color", "value": "Gold"}
                                ],
                                "inventoryItem": {
                                    "id": "gid://shopify/InventoryItem/7002"
                                },
                            }
                        ],
                        "userErrors": [
                            {
                                "field": ["variants", "1", "sku"],
                                "message": "SKU has already been taken",
                            }
                        ],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    db["catalog_variants"].insert_one(
        {"variant_id": "V1", "sku": "S-BLK", "parent_product_id": "P1",
         "option_color": "Black"}
    )
    db["catalog_variants"].insert_one(
        {"variant_id": "V2", "sku": "S-GLD", "parent_product_id": "P1",
         "option_color": "Gold"}
    )
    db["catalog_variants"].insert_one(
        {"variant_id": "V3", "sku": "S-SLV", "parent_product_id": "P1",
         "option_color": "Silver"}
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [
        db["catalog_variants"].find_one({"sku": "S-BLK"}),
        db["catalog_variants"].find_one({"sku": "S-GLD"}),
        db["catalog_variants"].find_one({"sku": "S-SLV"}),
    ]

    res = _run(shopify_push.push_product(db, product, variants))
    assert res.ok is True  # the product push stays fail-soft either way

    # The partial error IS reported...
    assert any("SKU has already been taken" in e for e in res.variants_seeded["errors"])
    # ...but the ROW SHOPIFY DID CREATE is still harvested, not discarded.
    assert res.variants_seeded["created"] == 1
    assert "gid://shopify/ProductVariant/5002" in res.variants_seeded["variant_gids"]
    assert (
        "gid://shopify/InventoryItem/7002"
        in res.variants_seeded["inventory_item_gids"]
    )

    gold = db["catalog_variants"].find_one({"sku": "S-GLD"})
    assert gold["shopify_variant_id"] == "gid://shopify/ProductVariant/5002"
    assert gold["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/7002"
    # Silver genuinely failed -- no gid to write back, mapping stays absent.
    silver = db["catalog_variants"].find_one({"sku": "S-SLV"})
    assert "shopify_variant_id" not in silver
    assert spy.count_for("productVariantsBulkCreate") == 1


def test_live_create_seed_failure_never_fails_the_product_push(monkeypatch):
    """The seeding step is a fail-SOFT side channel: a userErrors from the bulk
    update is reported, but the product create itself stays ok (a re-push
    repairs the variant)."""
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [],
                        "userErrors": [{"field": ["price"], "message": "boom"}],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})

    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True and res.mode == "LIVE"
    assert res.variants_seeded["updated"] == 0
    assert any("boom" in e for e in res.variants_seeded["errors"])


def test_live_create_with_no_price_and_no_sku_makes_no_extra_call(monkeypatch):
    """Nothing to seed -> no second mutation (and certainly no price 0.00)."""
    spy = _force_live(
        monkeypatch, {"productCreate": _create_response([_DEFAULT_VARIANT_NODE])}
    )
    db = _EngineDB()
    db["catalog_products"].insert_one({"id": "P2", "title": "X", "ecom": {}})
    product = db["catalog_products"].find_one({"id": "P2"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True
    assert res.variants_seeded is None
    assert spy.count_for("productVariantsBulkUpdate") == 0
    assert len(spy.calls) == 1


# ===========================================================================
# 4. LIVE update -- prices are NOT clobbered by default
# ===========================================================================


def test_live_update_of_a_seeded_product_does_not_reseed(monkeypatch):
    """An ALREADY-SEEDED product (carries a stored default-variant gid from a
    prior successful seed) is never RE-SEEDED by an ordinary catalogue edit:
    no `variants_seeded` summary is produced and productVariantsBulkCreate is
    never called (the ~4,400 already-live products are never silently
    force-recreated).

    Its price MAY still ride the SEPARATE, pre-existing OS-016
    push_variant_prices side channel (#950): that mechanism re-prices every
    gid-mapped product on every live update regardless of ANY flag here --
    it is untouched by this change, is exactly why the stored default gid
    exists in the first place (`_variants_for_price_push`'s pseudo-variant
    synthesis), and is covered on its own in test_online_store_push.py /
    test_online_discount_engine.py. This test's scope is narrowly "seeding
    does not run twice", not "nothing about this product's price ever
    changes on Shopify"."""
    spy = _force_live(
        monkeypatch,
        {
            "productUpdate": {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": "gid://shopify/Product/111",
                            "handle": "rb",
                            "variants": {"nodes": [_DEFAULT_VARIANT_NODE]},
                        },
                        "userErrors": [],
                    }
                }
            },
            # Answers the OS-016 push_variant_prices side channel, which DOES
            # still fire for this already-mapped product -- see the docstring.
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [],
                        "userErrors": [],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    product = _product()
    product["ecom"] = {
        "status": "PUBLISHED",
        "shopify_product_id": "gid://shopify/Product/111",
        "shopify_variant_id": "gid://shopify/ProductVariant/5001",
        "shopify_inventory_item_id": "gid://shopify/InventoryItem/7001",
    }
    res = _run(shopify_push.push_product(db, product, []))
    assert res.action == "update" and res.ok is True
    assert res.variants_seeded is None  # no re-seed: already seeded
    assert spy.count_for("productVariantsBulkCreate") == 0  # never creates


def test_live_update_never_seeded_repairs_regardless_of_the_flag(monkeypatch):
    """#944 follow-up: repair-only seeding is INDEPENDENT of
    SHOPIFY_PUSH_PRICE_ON_UPDATE. A mapped product with NO stored variant /
    inventory-item gid anywhere (a previous create-seed failed, or it predates
    the #943/#944 stack) must self-heal on its next ordinary update -- price,
    SKU, and BOTH gids get written back -- with the flag left OFF."""
    monkeypatch.delenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", raising=False)
    spy = _force_live(
        monkeypatch,
        {
            "productUpdate": {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": "gid://shopify/Product/111",
                            "handle": "rb",
                            "variants": {"nodes": [_DEFAULT_NODE_WITH_INV]},
                        },
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        _product(
            ecom={
                "status": "PUBLISHED",
                "shopify_product_id": "gid://shopify/Product/111",
                # NO shopify_variant_id / shopify_inventory_item_id.
            }
        )
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.action == "update" and res.ok is True
    assert res.variants_seeded is not None
    assert res.variants_seeded["updated"] == 1
    assert spy.count_for("productVariantsBulkUpdate") == 1
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert (
        saved["ecom"]["shopify_inventory_item_id"]
        == "gid://shopify/InventoryItem/7001"
    )


def test_live_update_seeds_prices_only_when_the_owner_opts_in(monkeypatch):
    monkeypatch.setenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", "1")
    spy = _force_live(
        monkeypatch,
        {
            "productUpdate": {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": "gid://shopify/Product/111",
                            "variants": {"nodes": [_DEFAULT_VARIANT_NODE]},
                        },
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [
                            {"id": "gid://shopify/ProductVariant/5001"}
                        ],
                        "userErrors": [],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        _product(
            ecom={
                "status": "PUBLISHED",
                "shopify_product_id": "gid://shopify/Product/111",
            }
        )
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.action == "update"
    assert res.variants_seeded["updated"] == 1
    assert spy.count_for("productVariantsBulkUpdate") == 1
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"


# ===========================================================================
# 5. Sales-channel publish -- default OFF
# ===========================================================================


def test_publish_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SHOPIFY_PUBLISH_ON_CREATE", raising=False)
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    # PRICED fixture (panel must-fix 1): publish tests must not lean on a
    # priceless product, or they enshrine the 0.00-publish hole.
    db["catalog_products"].insert_one(_product(id="P3"))
    product = db["catalog_products"].find_one({"id": "P3"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.publication is None
    assert spy.count_for("publishablePublish") == 0
    assert shopify_push.publish_on_create_enabled() is False


def test_publish_on_create_when_flag_on_and_product_is_priced_and_active(monkeypatch):
    """Publish fires ONLY on the happy path: ACTIVE + seeding succeeded with a
    PRICED row. (The old fixture was priceless and asserted published:True --
    it proved the 0.00 hole instead of guarding it; panel must-fix 1.)"""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product(id="P4"))
    product = db["catalog_products"].find_one({"id": "P4"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.variants_seeded["errors"] == []
    assert res.variants_seeded["priced_rows"] == 1
    assert res.publication == {
        "published": True,
        "publication_id": "gid://shopify/Publication/77",
    }
    call = spy.call_for("publishablePublish")
    assert call["variables"]["id"] == "gid://shopify/Product/900"
    assert call["variables"]["input"] == [
        {"publicationId": "gid://shopify/Publication/77"}
    ]


def test_a_draft_is_never_published_even_with_the_flag_on(monkeypatch):
    """The owner's publish gate: the 2,032 staged DRAFTs must stay invisible --
    even a fully PRICED draft (priced fixture per panel must-fix 1)."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product(id="P5", ecom={"status": "DRAFT"}))
    product = db["catalog_products"].find_one({"id": "P5"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.payload["status"] == "DRAFT"
    assert res.publication is None
    assert spy.count_for("publishablePublish") == 0


def test_publish_withheld_when_seeding_failed(monkeypatch):
    """Panel must-fix 1(a): seeding is fail-soft, so a bulk-update userError at
    go-live would previously still publish -- an ACTIVE product LIVE at 0.00.
    The precondition now withholds publish and reports why."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [],
                        "userErrors": [{"field": ["price"], "message": "boom"}],
                    }
                }
            },
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product(id="P6"))
    product = db["catalog_products"].find_one({"id": "P6"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True  # the product push itself stays fail-soft
    assert res.publication == {
        "published": False,
        "error": "publish withheld: variant unpriced or seeding failed",
    }
    assert spy.count_for("publishablePublish") == 0


def test_publish_withheld_for_an_unpriced_published_product(monkeypatch):
    """Panel must-fix 1(b): a PUBLISHED product with no resolvable price must
    never go visible -- Shopify's auto-created variant sits at 0.00. Covers
    BOTH unpriced shapes: SKU-only seeding (priced_rows == 0) and
    nothing-to-seed at all (seed_summary is None)."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    withheld = {
        "published": False,
        "error": "publish withheld: variant unpriced or seeding failed",
    }
    # Shape 1: SKU-only (seeding ran, but zero priced rows).
    db["catalog_products"].insert_one(
        {"id": "P7", "title": "X", "sku": "BV-NOPRICE", "ecom": {"status": "PUBLISHED"}}
    )
    res = _run(
        shopify_push.push_product(
            db, db["catalog_products"].find_one({"id": "P7"}), []
        )
    )
    assert res.variants_seeded["priced_rows"] == 0
    assert res.publication == withheld
    # Shape 2: no price AND no SKU (nothing to seed at all).
    db["catalog_products"].insert_one(
        {"id": "P8", "title": "Y", "ecom": {"status": "PUBLISHED"}}
    )
    res2 = _run(
        shopify_push.push_product(
            db, db["catalog_products"].find_one({"id": "P8"}), []
        )
    )
    assert res2.variants_seeded is None
    assert res2.publication == withheld
    assert spy.count_for("publishablePublish") == 0


def test_push_mode_status_reports_the_new_flags(monkeypatch):
    monkeypatch.delenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", raising=False)
    monkeypatch.delenv("SHOPIFY_PUBLISH_ON_CREATE", raising=False)
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "off")
    status = shopify_push.push_mode_status(None)
    assert status["mode"] == "SIMULATED"
    assert status["price_on_update"] is False
    assert status["publish_on_create"] is False


# ===========================================================================
# 6. Inventory-item capture -- the oversell-guard publish precondition
# ===========================================================================
# The stock write-back that keeps the website from overselling resolves a SKU
# to its Shopify InventoryItem via catalog_variants.shopify_inventory_item_id
# (with an ecom.shopify_inventory_item_id fallback on the product). These tests
# prove a LIVE push now persists that mapping for products IMS itself creates,
# that SIMULATED writes nothing, that a re-push is idempotent, and -- the point
# of it all -- that the REAL resolvers find a freshly pushed product.


_DEFAULT_NODE_WITH_INV = {
    "id": "gid://shopify/ProductVariant/5001",
    "title": "Default Title",
    "selectedOptions": [{"name": "Title", "value": "Default Title"}],
    "inventoryItem": {"id": "gid://shopify/InventoryItem/7001"},
}

_BLACK_NODE_WITH_INV = {
    "id": "gid://shopify/ProductVariant/5001",
    "selectedOptions": [{"name": "Color", "value": "Black"}],
    "inventoryItem": {"id": "gid://shopify/InventoryItem/7001"},
}

_GOLD_NODE_WITH_INV = {
    "id": "gid://shopify/ProductVariant/5002",
    "selectedOptions": [{"name": "Color", "value": "Gold"}],
    "inventoryItem": {"id": "gid://shopify/InventoryItem/7002"},
}

_BULK_UPDATE_OK = {
    "data": {
        "productVariantsBulkUpdate": {
            "productVariants": [{"id": "gid://shopify/ProductVariant/5001"}],
            "userErrors": [],
        }
    }
}


class _ProjColl(MockCollection):
    """MockCollection that ALSO accepts pymongo's (filter, projection) call
    shape -- online_sync_health._inventory_item_id_for_sku passes a projection,
    which the plain MockCollection.find_one signature rejects."""

    def find_one(self, filter=None, projection=None, *a, **k):  # noqa: A002
        return super().find_one(filter or {})


class _ProjDB(_EngineDB):
    def __getitem__(self, name):
        return self._colls.setdefault(name, _ProjColl(name))


def test_node_inventory_item_gid_is_failsoft_and_normalising():
    f = shopify_push._node_inventory_item_gid
    assert f(None) is None
    assert f({}) is None
    assert f({"inventoryItem": None}) is None
    assert f({"inventoryItem": {}}) is None
    assert f({"inventoryItem": {"id": ""}}) is None
    # A bare numeric id is promoted to a full gid; a full gid passes through.
    assert f({"inventoryItem": {"id": "7001"}}) == "gid://shopify/InventoryItem/7001"
    assert (
        f({"inventoryItem": {"id": "gid://shopify/InventoryItem/7001"}})
        == "gid://shopify/InventoryItem/7001"
    )


def test_mutations_select_the_inventory_item_id():
    """The selection is the capture vehicle: every mutation the seeding flow
    reads variants back from must ask for inventoryItem { id }."""
    for q in (
        shopify_push._PRODUCT_CREATE,
        shopify_push._PRODUCT_UPDATE,
        shopify_push._VARIANTS_BULK_CREATE,
    ):
        assert "inventoryItem { id }" in q


def test_live_create_stamps_the_inventory_item_on_a_no_variant_product(monkeypatch):
    """A no-variant product's single default variant IS the product: its
    InventoryItem gid must land on ecom.shopify_inventory_item_id (the
    resolver's documented product-level fallback)."""
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_NODE_WITH_INV]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})

    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True and res.mode == "LIVE" and res.action == "create"
    # The create mutation actually sent asked for the inventory item.
    assert "inventoryItem { id }" in spy.call_for("productCreate")["query"]
    assert res.variants_seeded["inventory_item_gids"] == [
        "gid://shopify/InventoryItem/7001"
    ]
    assert (
        res.variants_seeded["product_level_inventory_item_gid"]
        == "gid://shopify/InventoryItem/7001"
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert (
        saved["ecom"]["shopify_inventory_item_id"]
        == "gid://shopify/InventoryItem/7001"
    )


def test_live_create_stamps_the_inventory_item_on_each_variant_row(monkeypatch):
    """Multi-variant: the productCreate-materialised variant AND the
    bulk-created one BOTH get shopify_inventory_item_id on their
    catalog_variants row -- and the PARENT ecom does NOT get any variant's
    inventory item (a parent-sku lookup must never hit the wrong variant's
    stock target)."""
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_BLACK_NODE_WITH_INV]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
            "productVariantsBulkCreate": {
                "data": {
                    "productVariantsBulkCreate": {
                        "productVariants": [_GOLD_NODE_WITH_INV],
                        "userErrors": [],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    db["catalog_variants"].insert_one(
        {"variant_id": "V1", "sku": "S-BLK", "parent_product_id": "P1",
         "option_color": "Black"}
    )
    db["catalog_variants"].insert_one(
        {"variant_id": "V2", "sku": "S-GLD", "parent_product_id": "P1",
         "option_color": "Gold"}
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [
        db["catalog_variants"].find_one({"sku": "S-BLK"}),
        db["catalog_variants"].find_one({"sku": "S-GLD"}),
    ]

    res = _run(shopify_push.push_product(db, product, variants))
    assert res.ok is True
    assert res.variants_seeded["product_level_inventory_item_gid"] is None

    blk = db["catalog_variants"].find_one({"sku": "S-BLK"})
    gld = db["catalog_variants"].find_one({"sku": "S-GLD"})
    assert blk["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert blk["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/7001"
    assert gld["shopify_variant_id"] == "gid://shopify/ProductVariant/5002"
    assert gld["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/7002"

    saved = db["catalog_products"].find_one({"id": "P1"})
    # ecom carries the default VARIANT gid (price handle) but NOT an inventory
    # item -- that belongs to the variant rows only for a variant-ful product.
    assert "shopify_inventory_item_id" not in saved["ecom"]


def test_live_create_without_inventory_item_in_response_leaves_mapping_alone(
    monkeypatch,
):
    """An old/partial response (no inventoryItem selected) must NOT clear an
    existing mapping: the write is set-only."""
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response(
                [
                    {
                        "id": "gid://shopify/ProductVariant/5001",
                        "selectedOptions": [{"name": "Color", "value": "Black"}],
                    }
                ]
            ),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    db["catalog_variants"].insert_one(
        {"variant_id": "V1", "sku": "S-BLK", "parent_product_id": "P1",
         "option_color": "Black",
         "shopify_inventory_item_id": "gid://shopify/InventoryItem/OLD"}
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [db["catalog_variants"].find_one({"sku": "S-BLK"})]

    res = _run(shopify_push.push_product(db, product, variants))
    assert res.ok is True
    row = db["catalog_variants"].find_one({"sku": "S-BLK"})
    # gid stamped, existing inventory-item mapping untouched (NOT cleared).
    assert row["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert row["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/OLD"


@pytest.mark.parametrize("reason", ["writes_off", "dispatch_off", "no_creds"])
def test_simulated_push_writes_no_inventory_mapping(monkeypatch, reason):
    """SIMULATED writes NOTHING: no variant gid, no inventory item, no ecom
    stamp -- and never reaches the network (the _boom boundary proves that)."""
    _force_dark(monkeypatch, reason)
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    db["catalog_variants"].insert_one(
        {"variant_id": "V1", "sku": "S-BLK", "parent_product_id": "P1",
         "option_color": "Black"}
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [db["catalog_variants"].find_one({"sku": "S-BLK"})]

    res = _run(shopify_push.push_product(db, product, variants))
    assert res.mode == "SIMULATED" and res.ok is True

    row = db["catalog_variants"].find_one({"sku": "S-BLK"})
    assert "shopify_variant_id" not in row
    assert "shopify_inventory_item_id" not in row
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert "shopify_product_id" not in (saved.get("ecom") or {})
    assert "shopify_inventory_item_id" not in (saved.get("ecom") or {})


def test_repush_is_idempotent_for_the_inventory_item_mapping(monkeypatch):
    """CREATE stamps the mapping; a later UPDATE re-push (owner opt-in flag on)
    returning the SAME variants re-stamps the SAME values -- no duplicate rows,
    no changed target."""
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_BLACK_NODE_WITH_INV]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    db["catalog_variants"].insert_one(
        {"variant_id": "V1", "sku": "S-BLK", "parent_product_id": "P1",
         "option_color": "Black"}
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [db["catalog_variants"].find_one({"sku": "S-BLK"})]
    res1 = _run(shopify_push.push_product(db, product, variants))
    assert res1.ok is True and res1.action == "create"

    # Second push: the product now carries the Shopify gid -> UPDATE. Opt in to
    # the seeding-on-update path so the capture runs again.
    monkeypatch.setenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", "1")
    _force_live(
        monkeypatch,
        {
            "productUpdate": {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": "gid://shopify/Product/900",
                            "handle": "rb",
                            "variants": {"nodes": [_BLACK_NODE_WITH_INV]},
                        },
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    product2 = db["catalog_products"].find_one({"id": "P1"})
    assert (product2["ecom"]["shopify_product_id"]) == "gid://shopify/Product/900"
    variants2 = [db["catalog_variants"].find_one({"sku": "S-BLK"})]
    res2 = _run(shopify_push.push_product(db, product2, variants2))
    assert res2.ok is True and res2.action == "update"

    rows = list(db["catalog_variants"].find({"sku": "S-BLK"}))
    assert len(rows) == 1  # updated in place, never duplicated
    assert rows[0]["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert (
        rows[0]["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/7001"
    )


def test_resolver_finds_a_freshly_pushed_products_inventory_item(monkeypatch):
    """END-TO-END against the REAL resolvers (the point of the whole change):
    after a LIVE push, online_catalog's variant-target resolver and
    online_sync_health's per-SKU lookup -- the two paths the oversell-guard
    stock write-back uses -- both find the inventory item, for BOTH shapes:
    a variant-row product (catalog_variants mapping) and a no-variant product
    (ecom fallback)."""
    from api.services import online_catalog
    from api.services import online_sync_health

    db = _ProjDB()
    # Product A: no catalog_variants rows (the common eyewear case).
    db["catalog_products"].insert_one(_product())  # sku BV-RB-0001, id P1
    # Product B: one variant row.
    db["catalog_products"].insert_one(
        {
            "id": "P2",
            "title": "Wayfarer",
            "sku": "BV-RB-0002",
            "mrp": 9990,
            "offer_price": 7990,
            "ecom": {"status": "PUBLISHED"},
        }
    )
    db["catalog_variants"].insert_one(
        {"variant_id": "V1", "sku": "S-BLK", "parent_product_id": "P2",
         "option_color": "Black"}
    )

    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_NODE_WITH_INV]),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    res_a = _run(
        shopify_push.push_product(
            db, db["catalog_products"].find_one({"id": "P1"}), []
        )
    )
    assert res_a.ok is True

    # Product B's variant carries a DISTINCT inventory item (7002) so any
    # cross-product contamination of the mapping would be caught below.
    black_b = {
        "id": "gid://shopify/ProductVariant/5002",
        "selectedOptions": [{"name": "Color", "value": "Black"}],
        "inventoryItem": {"id": "gid://shopify/InventoryItem/7002"},
    }
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response(
                [black_b], gid="gid://shopify/Product/901"
            ),
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    res_b = _run(
        shopify_push.push_product(
            db,
            db["catalog_products"].find_one({"id": "P2"}),
            [db["catalog_variants"].find_one({"sku": "S-BLK"})],
        )
    )
    assert res_b.ok is True

    # --- online_catalog: the mapping the stock write-back resolves through ---
    items = online_catalog.inventory_items_for_skus(db, ["S-BLK", "BV-RB-0001"])
    assert items["S-BLK"] == "gid://shopify/InventoryItem/7002"
    assert items["BV-RB-0001"] == "gid://shopify/InventoryItem/7001"

    monkeypatch.setenv(
        "SHOPIFY_ONLINE_LOCATION_ID", "gid://shopify/Location/11"
    )
    targets = online_catalog.online_variant_targets_for_skus(
        db, ["S-BLK", "BV-RB-0001"]
    )
    assert targets["S-BLK"] == {
        "inventory_item_id": "gid://shopify/InventoryItem/7002",
        "location_id": "gid://shopify/Location/11",
    }
    assert targets["BV-RB-0001"]["inventory_item_id"] == (
        "gid://shopify/InventoryItem/7001"
    )

    # --- online_sync_health: the oversell re-push sweep's per-SKU lookup ---
    assert (
        online_sync_health._inventory_item_id_for_sku(db, "S-BLK")
        == "gid://shopify/InventoryItem/7002"
    )
    assert (
        online_sync_health._inventory_item_id_for_sku(db, "BV-RB-0001")
        == "gid://shopify/InventoryItem/7001"
    )


# ===========================================================================
# 7. Adversarial-panel must-fixes 2 + 3 (gid-first matching, compareAt null)
# ===========================================================================


def test_update_gid_first_matching_survives_an_option_rename(monkeypatch):
    """Panel must-fix 2: a mapped multi-variant product whose IMS option value
    was renamed (option-key no longer matches Shopify's selectedOptions) must
    pair on the STORED shopify_variant_id -- both gids get bulk UPDATE rows and
    productVariantsBulkCreate is NEVER called. Before the fix the drifted row
    fell into the create branch: a duplicate live variant was minted and the
    IMS row's stock target re-pointed at it, leaving the old variant sellable
    at a stale price with its stock never synced again."""
    monkeypatch.setenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", "1")
    spy = _force_live(
        monkeypatch,
        {
            "productUpdate": {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": "gid://shopify/Product/900",
                            "handle": "rb",
                            "variants": {
                                "nodes": [
                                    {
                                        "id": "gid://shopify/ProductVariant/5001",
                                        "selectedOptions": [
                                            {"name": "Color", "value": "Black"}
                                        ],
                                        "inventoryItem": {
                                            "id": "gid://shopify/InventoryItem/7001"
                                        },
                                    },
                                    {
                                        "id": "gid://shopify/ProductVariant/5002",
                                        "selectedOptions": [
                                            {"name": "Color", "value": "Gold"}
                                        ],
                                        "inventoryItem": {
                                            "id": "gid://shopify/InventoryItem/7002"
                                        },
                                    },
                                ]
                            },
                        },
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
            "productVariantsBulkCreate": {
                "data": {
                    "productVariantsBulkCreate": {
                        "productVariants": [
                            {"id": "gid://shopify/ProductVariant/9999"}
                        ],
                        "userErrors": [],
                    }
                }
            },
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        _product(
            ecom={
                "status": "PUBLISHED",
                "shopify_product_id": "gid://shopify/Product/900",
            }
        )
    )
    # V1's option_color was RENAMED in IMS ("Black" -> "Matte Black"): the
    # option key no longer matches Shopify's node. Its stored gid must win.
    db["catalog_variants"].insert_one(
        {
            "variant_id": "V1",
            "sku": "S-BLK",
            "parent_product_id": "P1",
            "option_color": "Matte Black",
            "shopify_variant_id": "gid://shopify/ProductVariant/5001",
        }
    )
    # V2 stores a BARE NUMERIC gid (the BVI-era storage shape) -- the gid-first
    # pass must normalise it before matching.
    db["catalog_variants"].insert_one(
        {
            "variant_id": "V2",
            "sku": "S-GLD",
            "parent_product_id": "P1",
            "option_color": "Gold",
            "shopify_variant_id": "5002",
        }
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [
        db["catalog_variants"].find_one({"sku": "S-BLK"}),
        db["catalog_variants"].find_one({"sku": "S-GLD"}),
    ]

    res = _run(shopify_push.push_product(db, product, variants))
    assert res.ok is True and res.action == "update"

    # Both STORED gids received bulk UPDATE rows; nothing was bulk-created.
    call = spy.call_for("productVariantsBulkUpdate")
    assert {r["id"] for r in call["variables"]["variants"]} == {
        "gid://shopify/ProductVariant/5001",
        "gid://shopify/ProductVariant/5002",
    }
    assert spy.count_for("productVariantsBulkCreate") == 0
    assert res.variants_seeded["created"] == 0
    assert res.variants_seeded["errors"] == []

    # The drifted row kept its OWN stock target (7001) -- not a duplicate's.
    blk = db["catalog_variants"].find_one({"sku": "S-BLK"})
    assert blk["shopify_variant_id"] == "gid://shopify/ProductVariant/5001"
    assert blk["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/7001"
    gld = db["catalog_variants"].find_one({"sku": "S-GLD"})
    assert gld["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/7002"


def test_assign_seed_rows_never_creates_a_row_whose_stored_gid_is_present():
    """Pure-level pin of must-fix 2: option drift + stored gid present in the
    nodes => UPDATE row on that gid, create list EMPTY."""
    product = {"id": "P1", "mrp": 100, "offer_price": 90, "ecom": {}}
    variants = [
        {
            "sku": "S-1",
            "option_color": "Matte Black",  # drifted label
            "shopify_variant_id": "gid://shopify/ProductVariant/71",
        }
    ]
    rows = shopify_push.build_variant_seed_rows(product, variants)
    nodes = [
        {
            "id": "gid://shopify/ProductVariant/71",
            "selectedOptions": [{"name": "Color", "value": "Black"}],
            "inventoryItem": {"id": "gid://shopify/InventoryItem/81"},
        }
    ]
    upd, crt, crt_vars, pairs, skipped = shopify_push._assign_seed_rows(rows, nodes)
    assert [r["id"] for r in upd] == ["gid://shopify/ProductVariant/71"]
    assert crt == [] and crt_vars == []
    assert pairs == [
        (
            variants[0],
            "gid://shopify/ProductVariant/71",
            "gid://shopify/InventoryItem/81",
        )
    ]
    assert skipped == 0


def test_update_seed_row_clears_stale_compare_at_with_explicit_null(monkeypatch):
    """Panel must-fix 3: when the MRP no longer exceeds the selling price, the
    flag-ON update seed row must carry an EXPLICIT compareAtPrice None
    (GraphQL null) so Shopify CLEARS the stale strikethrough -- the same
    contract build_variant_price_inputs already honours. Omitting the field
    would leave a fake "was <old MRP>" above the real price."""
    # Pure builder first: mrp == price -> explicit None present in the row.
    rows = shopify_push.build_variant_seed_rows(
        {"id": "PX", "sku": "BV-X", "mrp": 10990, "offer_price": 10990, "ecom": {}},
        [],
    )
    assert rows[0]["row"]["price"] == "10990.00"
    assert "compareAtPrice" in rows[0]["row"]
    assert rows[0]["row"]["compareAtPrice"] is None

    # Full flag-ON update path: the null must ride on the wire row.
    monkeypatch.setenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", "1")
    spy = _force_live(
        monkeypatch,
        {
            "productUpdate": {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": "gid://shopify/Product/900",
                            "variants": {"nodes": [_DEFAULT_NODE_WITH_INV]},
                        },
                        "userErrors": [],
                    }
                }
            },
            "productVariantsBulkUpdate": _BULK_UPDATE_OK,
        },
    )
    db = _EngineDB()
    # Owner lowered the MRP to the selling price: strikethrough must clear.
    db["catalog_products"].insert_one(
        _product(
            mrp=10990,
            ecom={
                "status": "PUBLISHED",
                "shopify_product_id": "gid://shopify/Product/900",
            },
        )
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True and res.action == "update"
    row = spy.call_for("productVariantsBulkUpdate")["variables"]["variants"][0]
    assert row["price"] == "10990.00"
    assert "compareAtPrice" in row
    assert row["compareAtPrice"] is None
