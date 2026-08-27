"""
GET /products/gst-rates serves the canonical tables -- there is no second copy
============================================================================
The frontend used to hand-mirror two backend tables: the category -> HSN map
(gst_rates.GST_CATEGORY_TABLE) and the category -> master-row hint
(gst_rates._CATEGORY_HINT). The HSN copy had drifted: it pointed SMARTGLASSES /
SMTSG / SMTFR at 900410, the SUNGLASSES code, while this table says 852580
(owner-confirmed 2026-06-17). Since a client-supplied hsn_code WINS at the
cataloguing door (product_master.normalise_payload), that drift was one save
away from being stamped onto a product and printed on its tax invoice.

Those copies are deleted. Both maps now ride on this endpoint, so these tests
pin what it must serve.

Fixtures deliberately use SMARTGLASSES, where the two tables disagreed on the
HSN (852580 vs 900410) while AGREEING on the rate (18% both sides): a fixture
whose fields carry the same value cannot tell which one the code read.
"""

import asyncio

from api.routers import products as products_mod
from api.services import gst_rates
from api.services import product_master as pm


def _call():
    return asyncio.run(products_mod.get_gst_rates(current_user={"user_id": "t"}))


def test_endpoint_serves_the_canonical_category_to_hsn_map():
    body = _call()
    hsn = body["hsn_by_category"]
    # The drifted frontend copy said 900410 (sunglasses) for all three.
    assert hsn["SMARTGLASSES"] == "852580"
    assert hsn["SMTSG"] == "852580"
    assert hsn["SMTFR"] == "852580"
    # ... while genuine sunglasses keep 900410, so the two are distinguishable.
    assert hsn["SUNGLASS"] == "900410"
    # Every code the table holds is served verbatim.
    assert hsn == {
        cat: code for cat, (code, _r) in gst_rates.GST_CATEGORY_TABLE.items() if code
    }


def test_endpoint_serves_the_canonical_category_to_rate_map():
    """The rate half of the same table. The frontend used to hand-write its own
    category -> rate switch and consult it INSTEAD of this one, so the two could
    disagree without anything noticing -- and they did: an eye test is an exempt
    health service billed at 0% (SAC 9993) and the frontend had no row for one
    at all, so it quoted its unknown-category default on the tax invoice."""
    rates = _call()["rate_by_category"]
    assert rates == {
        cat: rate for cat, (_h, rate) in gst_rates.GST_CATEGORY_TABLE.items()
    }
    # The two the frontend contradicted, named explicitly so a table edit that
    # loses them fails here rather than silently on a printed invoice.
    assert rates["EYE_TEST"] == 0.0
    assert rates["CONSULTATION"] == 0.0
    # ...while a taxable line on the same bill is unchanged.
    assert rates["SUNGLASS"] == 18.0
    assert rates["FRAME"] == 5.0


def test_hsn_and_rate_halves_describe_the_same_categories():
    """The two maps are one table served as two. A category present in one and
    missing from the other means the frontend can resolve an HSN it cannot
    price, or price something it cannot file -- the exact shape of the drift
    this endpoint exists to end. (hsn_by_category drops rows with no code; no
    row in the table has one today, and this says so.)"""
    body = _call()
    assert set(body["rate_by_category"]) == set(gst_rates.GST_CATEGORY_TABLE)
    assert set(body["hsn_by_category"]) == set(body["rate_by_category"])


def test_endpoint_serves_the_category_hint_map():
    body = _call()
    assert body["category_hint"] == gst_rates._CATEGORY_HINT
    # CCL is the spelling the Add-Product picker uses for colour contact lenses.
    # The deleted frontend copy had no CCL entry at all, so it fell through to
    # an 18% default while this side bills 5% (the #1011 gap).
    assert body["category_hint"]["CCL"] == "COLORED_CONTACT_LENS"


def test_every_offerable_category_has_an_hsn_here():
    """A category a product-entry screen can offer must never fall through to a
    silent default. The screens' category list is all_category_specs() (served
    by GET /products/categories); every code AND every SKU prefix in it has to
    be priceable here."""
    hsn = _call()["hsn_by_category"]
    specs = pm.all_category_specs()
    offerable = {s["code"] for s in specs} | {s["sku_prefix"] for s in specs}
    assert offerable, "no category specs -- the probe itself is broken"
    missing = sorted(key for key in offerable if key not in hsn)
    assert missing == [], f"offerable categories with no HSN: {missing}"


def test_static_half_survives_the_master_lookup_blowing_up():
    """The editable-master half needs Mongo; the canonical half does not. When
    Mongo is down the frontend must still get the HSN + hint maps -- that is the
    whole reason it can stop keeping copies of them."""
    orig = gst_rates._load_lookup
    gst_rates._load_lookup = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    try:
        body = _call()
    finally:
        gst_rates._load_lookup = orig
    assert body["by_hsn"] == {} and body["by_cat"] == {}
    assert body["hsn_by_category"]["SMARTGLASSES"] == "852580"
    assert body["rate_by_category"]["EYE_TEST"] == 0.0
    assert body["category_hint"]["CCL"] == "COLORED_CONTACT_LENS"
