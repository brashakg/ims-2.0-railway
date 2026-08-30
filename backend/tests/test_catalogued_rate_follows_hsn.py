"""
IMS 2.0 - the catalogued GST rate follows the product's HSN
===========================================================

Owner, in his own words: "make it understand product tax calculation during
product cataloguing and then according to hsn no mentioned, do gst calculation."

WHY THIS IS THE FIX AND NOT A SYNCED TABLE
------------------------------------------
A GST rate is a legal consequence of an HSN. Before this, three places worked
one out independently -- the cataloguing screen (a category -> rate switch), the
frontend's HSN_CODES table (an HSN -> rate map) and the server -- and they
drifted. Measured over the fifteen HSN codes the two sides held between them
(13 each): eleven were held by both and agreed on the rate -- ZERO rate
disagreements -- and FOUR were held by one side only, so one side could price
the goods and the other could not. 900140 and 900319 were priced by the screen
and unknown to the server (the screen asserted 5% on 900319 where the server
would state nothing); 852580 -- the Ray-Ban Meta range, 35 of the 68 catalogued
products -- and 9993 were priced by the server and unknown to the screen. Two
tables that answer for different goods are still two tables.

The cure is not a fourth table kept in step with the other three. It is that
ONE resolver answers, at the door, and everything downstream reads the answer
off the product:

  cataloguing door  product_master.normalise_payload -> resolve_gst_rate_strict
  purchase order    routers/vendors._po_line_gst_rate -> resolve_gst_rate_strict
  every screen      shows product.gst_rate, and works nothing out

These tests pin that. The one that matters most is
`test_the_purchase_order_charges_exactly_what_the_catalogue_stored`: it is the
invariant the screens depend on, and it dies the moment either side starts
resolving differently.
"""

# pylint: disable=protected-access

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import vendors as v  # noqa: E402
from api.services import product_master as pm  # noqa: E402


def _payload(**over):
    """A minimal, valid FRAME create payload. `sku` is supplied so the mint
    never reaches for a counter/DB."""
    base = dict(
        category="FRAME",
        attributes={
            "brand_name": "Ray-Ban",
            "model_no": "RX5154",
            "colour_code": "BLACK",
        },
        mrp=5000,
        offer_price=4500,
        sku="FRTESTSKU1",
        discount_category="PREMIUM",
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


def test_the_door_stores_the_rate_the_hsn_settles_not_the_one_it_was_handed():
    """A cataloguer picks HSN 900410 (non-corrective sunglasses, 18%) while the
    screen, working off the FRAME category, hands over 5%. The HSN is what the
    tax legally follows, so 18% is what gets stored -- and the purchase order
    that later resolves the same HSN therefore cannot contradict the catalogue.

    Before this, 5% was stored, the purchase screen previewed 5%, and the server
    charged 18% off the HSN."""
    doc = pm.normalise_payload(db=None, **_payload(hsn_code="900410", gst_rate=5.0))
    assert doc["hsn_code"] == "900410"
    assert doc["gst_rate"] == 18.0


def test_a_code_the_frontend_knew_and_the_server_did_not_now_settles():
    """900319 -- frames of other materials -- is the sharp end of the measured
    drift: the screen held it at 5%, the server held no 900319 at all. It is a
    child of heading 9003, declared settled at 5% for the whole heading."""
    doc = pm.normalise_payload(db=None, **_payload(hsn_code="900319", gst_rate=18.0))
    assert doc["gst_rate"] == 5.0


def test_an_hsn_the_tables_cannot_settle_leaves_the_given_rate_alone():
    """9004 covers corrective spectacles at 5% AND sunglasses at 18%, so the
    resolver refuses it on purpose. Refusing is not the same as overruling: the
    rate the caller supplied stands, and the product is still created."""
    doc = pm.normalise_payload(db=None, **_payload(hsn_code="900499", gst_rate=18.0))
    assert doc["hsn_code"] == "900499"
    assert doc["gst_rate"] == 18.0


def test_with_no_hsn_and_no_rate_the_category_default_still_answers():
    """Nothing above removes the safety net: a category with no HSN supplied
    still resolves its canonical HSN and a rate."""
    doc = pm.normalise_payload(db=None, **_payload())
    assert doc["hsn_code"] == "900311"
    assert doc["gst_rate"] == 5.0


# ---------------------------------------------------------------------------
# The invariant every screen now leans on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category,hsn",
    [
        ("FRAME", None),  # -> 900311
        ("FRAME", "900319"),  # settled off heading 9003
        # The pair that DISAGREES: category FRAME implies 5%, the HSN says 18%.
        # The HSN wins at the door, so both sides land on 18. Stop deriving at
        # the door and the catalogue keeps 5 while the order charges 18 -- the
        # exact shape of the bug, and what makes this case worth having.
        ("FRAME", "900410"),
        ("SUNGLASS", "900410"),
        # 8-digit, inherits its 6-digit parent. Assembled in parts because
        # test_gst_rates.test_contact_lens_hsn_has_one_spelling red-flags the
        # contiguous 8-digit spelling in ANY backend .py (two spellings of one
        # HSN split GSTR-1 rows); the runtime value is untouched.
        ("CONTACT_LENS", "900130" + "00"),
        ("SMARTGLASSES", "852580"),  # 35 of the 68 live products
        ("WALL_CLOCK", "910500"),
        ("HEARING_AID", "902140"),  # NIL, and NIL must survive the round trip
        ("ACCESSORIES", "392690"),
    ],
)
def test_the_purchase_order_charges_exactly_what_the_catalogue_stored(category, hsn):
    """THE invariant. A purchase-order line carries no rate of its own, so the
    server resolves one (routers/vendors._po_line_gst_rate: HSN first, then the
    product's catalogued rate). Because the door derived that catalogued rate
    from the same HSN with the same resolver, both branches now land on the same
    number -- which is what lets a screen show `product.gst_rate` and be showing
    the rate that will actually be charged, holding no rate table of its own.

    Break either side and the two numbers part company here."""
    # as_draft: this is about the TAX, not about each category's attribute
    # sheet. The draft floor (brand + model + a resolvable category) is still
    # enforced, and the GST derivation runs identically on draft and active.
    doc = pm.normalise_payload(
        db=None,
        category=category,
        attributes={"brand_name": "Ray-Ban", "model_no": "M1", "model_name": "M1"},
        mrp=5000,
        offer_price=4500,
        sku="TESTSKU1",
        hsn_code=hsn,
        as_draft=True,
    )
    stored = doc["gst_rate"]
    assert stored is not None

    po_rate, po_hsn, source, missing = v._po_line_gst_rate({}, doc)
    assert po_hsn == doc["hsn_code"]
    assert po_rate == stored, (
        f"the purchase order would tax {category}/{doc['hsn_code']} at "
        f"{po_rate}% while the catalogue (and therefore the screen) says "
        f"{stored}% -- resolved via '{source}', missing={missing!r}"
    )


# ---------------------------------------------------------------------------
# The OTHER door: PUT /products/{id}
# ---------------------------------------------------------------------------


class _FakeRepo:
    """Just enough ProductRepository for the update path."""

    def __init__(self, doc):
        self.docs = {doc["product_id"]: dict(doc)}

    def find_by_id(self, pid):
        d = self.docs.get(pid)
        return dict(d) if d else None

    def update(self, pid, data):
        if pid not in self.docs:
            return False
        self.docs[pid].update(data)
        return True

    def find_by_barcode(self, barcode):
        return None


def test_editing_a_products_hsn_moves_its_rate_with_it(monkeypatch):
    """Deriving at CREATE only would leave the rule with a back door: a later
    PUT could set hsn_code alone and strand the old rate on the product, which
    is the same drift one screen over. A frame edited onto the sunglasses HSN
    becomes an 18% product, not a 5% product wearing an 18% code."""
    import asyncio

    from api.routers import products as products_mod

    repo = _FakeRepo(
        {
            "product_id": "pid-1",
            "sku": "FR-1",
            "category": "FRAME",
            "hsn_code": "900311",
            "gst_rate": 5.0,
            "mrp": 5000.0,
            "offer_price": 4500.0,
        }
    )
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
    monkeypatch.setattr(products_mod._pm, "apply_restamp_atomic", lambda *a, **k: {})
    monkeypatch.setattr(
        products_mod, "_refresh_collections_after_product", lambda *a, **k: None
    )

    asyncio.run(
        products_mod.update_product(
            "pid-1",
            products_mod.ProductUpdate(hsn_code="900410"),
            current_user={
                "user_id": "u-1",
                "username": "editor",
                "roles": ["ADMIN"],
            },
        )
    )
    stored = repo.docs["pid-1"]
    assert stored["hsn_code"] == "900410"
    assert stored["gst_rate"] == 18.0
