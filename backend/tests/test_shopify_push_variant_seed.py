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
  * UPDATE of an already-mapped product is UNCHANGED by default (the ~4,400 live
    products are never silently re-priced); opt in with
    SHOPIFY_PUSH_PRICE_ON_UPDATE=1.
  * Optional Online Store publish on create, default OFF
    (SHOPIFY_PUBLISH_ON_CREATE), never for a DRAFT.
  * The three DARK gates and the SIMULATED dry-run are untouched.

Section 6 covers the five defects found reviewing that fix (D1-D5):
  D1 the INTERNAL GS1 20-29 barcode minted at GRN must NEVER be pushed as the
     public GTIN; only a real manufacturer GTIN/UPC is.
  D2 a row sent without a price must NOT be counted as `updated` -- a money
     no-op has to be distinguishable from a successful re-price.
  D3 the parent SKU must not be duplicated onto every variant of a
     multi-variant product (a SKU is unique per variant).
  D4 rows that matched no Shopify variant must be LOUD, not a clean success.
  D5 publish is gated on the price actually landing -- never a publicly
     buyable 0.00 product.

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


def test_seed_rows_carry_price_compare_at_and_sku():
    rows = shopify_push.build_variant_seed_rows(_product(), [])
    assert len(rows) == 1  # ONE product-level row for a no-variant product
    row = rows[0]["row"]
    assert row["price"] == "10990.00"
    assert row["compareAtPrice"] == "12990.00"  # MRP strikethrough
    # SKU lives on the inventory item in the 2024-04+ product model.
    assert row["inventoryItem"] == {"sku": "BV-RB-0001"}
    # D1: the product's `barcode` is the INTERNAL GS1 20-29 code minted at GRN.
    # It is NOT a public GTIN and must never reach Shopify (see below).
    assert "barcode" not in row
    assert rows[0]["option_values"] == []  # nothing to create; update-only
    assert rows[0]["key"] == ("", "")  # matches Shopify's "Default Title"


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
    assert pairs == [(variants[0], "gid://shopify/ProductVariant/5001")]
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
    _force_dark(monkeypatch, "writes_off")
    product = _product()
    product["ecom"] = {
        "status": "PUBLISHED",
        "shopify_product_id": "gid://shopify/Product/111",
    }
    res = _run(shopify_push.push_product(_EngineDB(), product, []))
    assert res.action == "update"
    assert res.variants_seeded is None  # nothing would be re-priced


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


def test_live_update_of_a_mapped_product_does_not_touch_prices(monkeypatch):
    """The ~4,400 already-live products must not be silently re-priced by an
    ordinary catalogue edit: an UPDATE issues the productUpdate and NOTHING
    else."""
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
    }
    res = _run(shopify_push.push_product(db, product, []))
    assert res.action == "update" and res.ok is True
    assert res.variants_seeded is None
    assert spy.count_for("productVariantsBulkUpdate") == 0
    assert len(spy.calls) == 1  # productUpdate only


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
        monkeypatch, {"productCreate": _create_response([_DEFAULT_VARIANT_NODE])}
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P3", "title": "X", "ecom": {"status": "PUBLISHED"}}
    )
    product = db["catalog_products"].find_one({"id": "P3"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.publication is None
    assert spy.count_for("publishablePublish") == 0
    assert shopify_push.publish_on_create_enabled() is False


def test_publish_on_create_when_flag_on_and_the_price_actually_landed(monkeypatch):
    """The happy path: an ACTIVE product whose variant seeding cleanly priced the
    variant IS published. (The product must carry a price -- see
    test_publish_is_skipped_when_seeding_did_not_price_anything.)"""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
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
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product(id="P4"))
    product = db["catalog_products"].find_one({"id": "P4"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.variants_seeded["ok"] is True
    assert res.variants_seeded["priced"] == 1
    assert res.warnings is None
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
    """The owner's publish gate: the 2,032 staged DRAFTs must stay invisible.

    The product here is FULLY priced and its seeding is clean, so the ONLY thing
    holding the publish back is DRAFT status -- proving that gate on its own."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
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
    db["catalog_products"].insert_one(_product(id="P5", ecom={"status": "DRAFT"}))
    product = db["catalog_products"].find_one({"id": "P5"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.payload["status"] == "DRAFT"
    assert res.variants_seeded["ok"] is True  # seeding WAS clean
    assert res.publication is None
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
# 6. Review defects D1-D5 -- regressions
# ===========================================================================

# ---------------------------------------------------------------------------
# D1: the internal GS1 20-29 barcode is NOT a public GTIN
# ---------------------------------------------------------------------------


def _bulk_update_ok(variant_gid="gid://shopify/ProductVariant/5001"):
    return {
        "data": {
            "productVariantsBulkUpdate": {
                "productVariants": [{"id": variant_gid}],
                "userErrors": [],
            }
        }
    }


@pytest.mark.parametrize(
    "code",
    [
        "2000000000017",  # EAN-13 minted by barcode.next_unit_ean13 (prefix 20)
        "2900000000019",  # top of the GS1 restricted range (29)
        "200000000017",  # the 12-digit UPC-A equivalent (leading 2)
    ],
)
def test_d1_internal_restricted_codes_are_recognised(code):
    assert shopify_push._is_internal_restricted_code(code) is True


@pytest.mark.parametrize(
    "code",
    [
        "8056597857239",  # a real Luxottica EAN-13
        "190000000005",  # a 12-digit UPC-A that is NOT restricted
        "RB3025-58",  # not numeric at all -- a vendor part code
    ],
)
def test_d1_real_manufacturer_codes_are_not_flagged_as_internal(code):
    assert shopify_push._is_internal_restricted_code(code) is False


def test_d1_internal_product_barcode_is_never_pushed_as_the_gtin():
    """THE DEFECT: the seed row used to fall back to product["barcode"], the
    INTERNAL GS1 20-29 code minted at GRN. That is not a valid public GTIN and
    would land in the Google/Meta shopping feed."""
    rows = shopify_push.build_variant_seed_rows(_product(), [])
    assert "barcode" not in rows[0]["row"]
    # ...and it is not laundered through the variant either.
    rows = shopify_push.build_variant_seed_rows(
        _product(), [{"sku": "V1", "barcode": "2000000000017"}]
    )
    assert "barcode" not in rows[0]["row"]


def test_d1_a_real_manufacturer_gtin_is_pushed():
    """A genuine GTIN is exactly what Shopify's `barcode` is for -- from the
    product spine, from the "GTIN (mfr)" attribute, or from the "UPC (mfr)" one."""
    from_spine = shopify_push.build_variant_seed_rows(
        _product(gtin="8056597857239"), []
    )
    assert from_spine[0]["row"]["barcode"] == "8056597857239"

    from_attr = shopify_push.build_variant_seed_rows(
        _product(attributes={"gtin": "8056597857239", "brand_name": "Ray-Ban"}), []
    )
    assert from_attr[0]["row"]["barcode"] == "8056597857239"

    from_upc = shopify_push.build_variant_seed_rows(
        _product(attributes={"upc": "190000000005"}), []
    )
    assert from_upc[0]["row"]["barcode"] == "190000000005"

    # The variant's own GTIN still wins over everything on the parent.
    from_variant = shopify_push.build_variant_seed_rows(
        _product(gtin="8056597857239"), [{"sku": "V1", "gtin": "8056597111111"}]
    )
    assert from_variant[0]["row"]["barcode"] == "8056597111111"


def test_d1_an_internal_code_typed_into_the_gtin_field_is_still_refused():
    """Belt and braces: the guard is on the VALUE, not just the field name, so a
    mis-keyed internal code cannot sneak through the mfr-GTIN door either."""
    rows = shopify_push.build_variant_seed_rows(
        _product(gtin="2000000000017", attributes={"gtin": "2000000000017"}), []
    )
    assert "barcode" not in rows[0]["row"]


def test_d1_live_create_sends_no_barcode_when_only_the_internal_one_exists(monkeypatch):
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True
    row = spy.call_for("productVariantsBulkUpdate")["variables"]["variants"][0]
    assert "barcode" not in row, "an internal 20-29 code must never reach Shopify"
    assert row["price"] == "10990.00"  # the price still lands


def test_d1_live_create_sends_the_real_gtin_when_there_is_one(monkeypatch):
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        _product(attributes={"gtin": "8056597857239"})  # "GTIN (mfr)" on the form
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    _run(shopify_push.push_product(db, product, []))
    row = spy.call_for("productVariantsBulkUpdate")["variables"]["variants"][0]
    assert row["barcode"] == "8056597857239"


# ---------------------------------------------------------------------------
# D2: a price-less row must not be reported as a successful update
# ---------------------------------------------------------------------------


def test_d2_a_price_less_row_is_not_counted_as_updated(monkeypatch):
    """THE DEFECT: a SKU-only row (no resolvable price) was sent and then
    counted in `updated` with errors == [] -- a money no-op that read exactly
    like a successful re-price."""
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P6", "title": "No price", "sku": "BV-NOPRICE", "ecom": {}}
    )
    product = db["catalog_products"].find_one({"id": "P6"})
    res = _run(shopify_push.push_product(db, product, []))

    # The SKU still goes out (it is the join key for online orders) and it
    # carries NO price -- 0.00 is never written.
    row = spy.call_for("productVariantsBulkUpdate")["variables"]["variants"][0]
    assert row["inventoryItem"] == {"sku": "BV-NOPRICE"}
    assert "price" not in row

    seeded = res.variants_seeded
    assert seeded["updated"] == 0, "`updated` counts PRICED rows only"
    assert seeded["priced"] == 0
    assert seeded["skipped_no_price"] == 1
    assert seeded["ok"] is False
    assert any("0.00" in w for w in seeded["warnings"])
    # ...and it is visible at the TOP level, not buried in a nested counter.
    assert res.warnings and any("0.00" in w for w in res.warnings)
    assert res.ok is True  # fail-soft contract unchanged: the product WAS created


def test_d2_a_priced_row_still_counts_as_updated(monkeypatch):
    """The control: a real price makes updated/priced 1 and the result clean."""
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.variants_seeded["updated"] == 1
    assert res.variants_seeded["priced"] == 1
    assert res.variants_seeded["skipped_no_price"] == 0
    assert res.variants_seeded["ok"] is True
    assert res.warnings is None


def test_d2_the_dry_run_also_shows_an_unpriced_row(monkeypatch):
    """The owner must be able to see the money hole BEFORE the cutover."""
    _force_dark(monkeypatch, "writes_off")
    res = _run(
        shopify_push.push_product(
            _EngineDB(), {"id": "P7", "sku": "BV-NOPRICE", "ecom": {}}, []
        )
    )
    assert res.mode == "SIMULATED"
    plan = res.variants_seeded
    assert plan["priced"] == 0 and plan["skipped_no_price"] == 1
    assert plan["ok"] is False
    assert res.warnings


# ---------------------------------------------------------------------------
# D3: the parent SKU is not copied onto every variant
# ---------------------------------------------------------------------------


def test_d3_parent_sku_is_not_duplicated_across_multiple_variants():
    """THE DEFECT: every variant fell back to the parent SKU, so a 2-colour
    product shipped the SAME SKU on both rows. A SKU is unique per variant."""
    rows = shopify_push.build_variant_seed_rows(
        _product(),
        [{"option_color": "Black"}, {"option_color": "Gold"}],  # neither has a SKU
    )
    assert len(rows) == 2
    for r in rows:
        assert "inventoryItem" not in r["row"], "parent SKU must not be duplicated"
        assert r["row"]["price"] == "10990.00"  # the price still falls back


def test_d3_a_variant_with_its_own_sku_still_gets_it():
    rows = shopify_push.build_variant_seed_rows(
        _product(),
        [{"sku": "S-BLK", "option_color": "Black"}, {"option_color": "Gold"}],
    )
    assert rows[0]["row"]["inventoryItem"] == {"sku": "S-BLK"}
    assert "inventoryItem" not in rows[1]["row"]


def test_d3_a_single_row_product_still_inherits_the_parent_sku():
    """The no-variant eyewear case (2,032 of the drafts): the single row IS the
    product, so the parent SKU is correct there -- and a lone variant row that
    has no SKU of its own inherits it too."""
    assert shopify_push.build_variant_seed_rows(_product(), [])[0]["row"][
        "inventoryItem"
    ] == {"sku": "BV-RB-0001"}
    lone = shopify_push.build_variant_seed_rows(_product(), [{"option_color": "Black"}])
    assert lone[0]["row"]["inventoryItem"] == {"sku": "BV-RB-0001"}


def test_d3_live_create_never_sends_the_same_sku_twice(monkeypatch):
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
            "productVariantsBulkUpdate": _bulk_update_ok(),
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
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [{"option_color": "Black"}, {"option_color": "Gold"}]
    _run(shopify_push.push_product(db, product, variants))

    sent = []
    for marker in ("productVariantsBulkUpdate", "productVariantsBulkCreate"):
        call = spy.call_for(marker)
        if call:
            sent.extend(call["variables"]["variants"])
    skus = [r["inventoryItem"]["sku"] for r in sent if r.get("inventoryItem")]
    assert skus == [], "no SKU is better than the same SKU on two variants"
    assert len(skus) == len(set(skus))


# ---------------------------------------------------------------------------
# D4: unmatched rows are LOUD
# ---------------------------------------------------------------------------


def test_d4_unmatched_variant_rows_do_not_read_as_a_clean_success(monkeypatch):
    """THE DEFECT: a multi-variant product with NO option axes matched only
    row[0]; the rest landed in skipped_unmatched while errors stayed empty and
    ok stayed true -- a partial pricing that looked like a clean push."""
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})
    # Two IMS variants, no Color/Size axes -> nothing to create them ON, and
    # productCreate only materialised one variant.
    variants = [{"sku": "S-A"}, {"sku": "S-B"}]
    res = _run(shopify_push.push_product(db, product, variants))

    seeded = res.variants_seeded
    assert seeded["updated"] == 1  # only row[0] was priced
    assert seeded["skipped_unmatched"] == 1  # row[1] was silently dropped before
    assert seeded["ok"] is False
    assert any("matched no Shopify variant" in w for w in seeded["warnings"])
    assert res.warnings and any("matched no Shopify variant" in w for w in res.warnings)
    # Fail-soft is intact: the product itself still succeeded.
    assert res.ok is True
    assert spy.count_for("productVariantsBulkCreate") == 0


def test_d4_a_fully_matched_multi_variant_push_stays_clean(monkeypatch):
    """The control: when every row matches, there is nothing to warn about."""
    _force_live(
        monkeypatch,
        {
            "productCreate": _create_response(
                [
                    {
                        "id": "gid://shopify/ProductVariant/5001",
                        "selectedOptions": [{"name": "Color", "value": "Black"}],
                    },
                    {
                        "id": "gid://shopify/ProductVariant/5002",
                        "selectedOptions": [{"name": "Color", "value": "Gold"}],
                    },
                ]
            ),
            "productVariantsBulkUpdate": _bulk_update_ok(),
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product())
    product = db["catalog_products"].find_one({"id": "P1"})
    variants = [
        {"sku": "S-BLK", "option_color": "Black"},
        {"sku": "S-GLD", "option_color": "Gold"},
    ]
    res = _run(shopify_push.push_product(db, product, variants))
    assert res.variants_seeded["updated"] == 2
    assert res.variants_seeded["skipped_unmatched"] == 0
    assert res.variants_seeded["ok"] is True
    assert res.warnings is None


# ---------------------------------------------------------------------------
# D5: publish only when the price actually landed
# ---------------------------------------------------------------------------


def test_d5_publish_is_skipped_when_seeding_did_not_price_anything(monkeypatch):
    """THE DEFECT: with SHOPIFY_PUBLISH_ON_CREATE=1 the product was published to
    the Online Store even when nothing had been priced -- a PUBLICLY BUYABLE
    0.00 product."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {
            "id": "P8",
            "title": "No price",
            "sku": "BV-NOPRICE",
            "ecom": {"status": "PUBLISHED"},
        }
    )
    product = db["catalog_products"].find_one({"id": "P8"})
    res = _run(shopify_push.push_product(db, product, []))

    assert spy.count_for("publishablePublish") == 0, "0.00 must never go live"
    assert res.publication["published"] is False
    assert res.publication["skipped"] is True
    assert "not clean" in res.publication["reason"]
    assert any("publish SKIPPED" in w for w in res.warnings)


def test_d5_publish_is_skipped_when_nothing_could_be_seeded_at_all(monkeypatch):
    """No price AND no SKU -> the seeding step never runs -> the Shopify variant
    is still the 0.00 Shopify minted. Publishing it would be the same defect."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P9", "title": "X", "ecom": {"status": "PUBLISHED"}}
    )
    product = db["catalog_products"].find_one({"id": "P9"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.variants_seeded is None
    assert spy.count_for("publishablePublish") == 0
    assert res.publication["skipped"] is True
    assert "did not run" in res.publication["reason"]


def test_d5_publish_is_skipped_when_the_seed_mutation_returned_user_errors(monkeypatch):
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
    db["catalog_products"].insert_one(_product(id="PA"))
    product = db["catalog_products"].find_one({"id": "PA"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.variants_seeded["errors"]
    assert spy.count_for("publishablePublish") == 0
    assert res.publication["skipped"] is True


def test_d5_publish_is_skipped_when_only_some_variants_were_priced(monkeypatch):
    """A partial seed must not publish either: the unmatched variant would be a
    buyable 0.00 row on a live product."""
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    spy = _force_live(
        monkeypatch,
        {
            "productCreate": _create_response([_DEFAULT_VARIANT_NODE]),
            "productVariantsBulkUpdate": _bulk_update_ok(),
            "publishablePublish": {"data": {"publishablePublish": {"userErrors": []}}},
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(_product(id="PB"))
    product = db["catalog_products"].find_one({"id": "PB"})
    res = _run(shopify_push.push_product(db, product, [{"sku": "S-A"}, {"sku": "S-B"}]))
    assert res.variants_seeded["skipped_unmatched"] == 1
    assert spy.count_for("publishablePublish") == 0
    assert res.publication["skipped"] is True


# ---------------------------------------------------------------------------
# The safety properties the fix must NOT have broken
# ---------------------------------------------------------------------------


def test_default_off_update_is_still_byte_identical_to_pre_seeding(monkeypatch):
    """(a) With NO new env flag set, an UPDATE of an already-mapped product does
    exactly one thing: the productUpdate. No seeding, no variant-gid capture, no
    publish -- and nothing to warn about."""
    monkeypatch.delenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", raising=False)
    monkeypatch.delenv("SHOPIFY_PUBLISH_ON_CREATE", raising=False)
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
            }
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
    assert len(spy.calls) == 1 and spy.call_for("productUpdate") is not None
    assert res.variants_seeded is None
    assert res.publication is None
    assert res.warnings is None
    assert res.ok is True
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert "shopify_variant_id" not in saved["ecom"]


def test_dark_dry_run_still_makes_zero_network_calls_with_every_flag_on(monkeypatch):
    """(d) The SIMULATED gate wins over BOTH new flags: _force_dark installs a
    boundary that raises if any Shopify call is attempted."""
    monkeypatch.setenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", "1")
    monkeypatch.setenv("SHOPIFY_PUBLISH_ON_CREATE", "1")
    _force_dark(monkeypatch, "no_creds")
    db = _EngineDB()
    res = _run(shopify_push.push_product(db, _product(), []))
    assert res.mode == "SIMULATED" and res.ok is True
    assert res.publication is None  # nothing is published from a dry-run
