"""GET /products/website-verdict?product_id= -- the Add/Edit form's "Website
still needs" line. It must be the push gate's own answer (lock / first-publish
readiness / brand, the photo rule, the one price rule) and must NEVER name the
cost price (a catalogue manager cannot fill it; the purchase brings it)."""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import api.dependencies as deps  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
_PHOTO = "https://cdn.example.com/p.jpg"


class _Up(StrictDB):
    is_connected = True


class _Conn:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    @property
    def db(self):
        return self._db


def _spine(**over):
    doc = {
        "product_id": "P1", "sku": "FR-RAYBAN-RB2140-901", "pim_product_id": "T1",
        "category": "FRAME", "brand": "Ray-Ban", "model": "RB2140",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB2140", "colour_code": "901"},
        "mrp": 5000, "offer_price": 4500, "hsn_code": "900311", "gst_rate": 5,
    }
    doc.update(over)
    return doc


def _needs(monkeypatch, spine, twin=None):
    db = _Up()
    db.seed("brand_masters", [{"brand_id": "b1", "name": "Ray-Ban", "is_active": True,
                               "sync_to_shopify_default": True}])
    db.seed("products", [spine])
    db.seed("catalog_products", [twin] if twin else [])
    db.seed("catalog_variants", [])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    return asyncio.run(prod_router.get_website_verdict(brand="Ray-Ban", product_id="P1", current_user=_ADMIN))


def test_a_draft_with_no_photo_and_no_price_lists_each_gate_reason(monkeypatch):
    out = _needs(monkeypatch, _spine(catalog_status="DRAFT", hsn_code="", offer_price=0, mrp=0))
    assert out["online"] is True  # the brand line is unchanged
    needs = out["needs"]
    assert any("catalogue draft" in n for n in needs)
    assert "a photo" in needs
    assert any(n.startswith("a selling price") for n in needs)
    assert "hsn_code" in out["gaps"]
    assert "cost_price" not in out["gaps"] and not any("cost" in n for n in needs)


def test_a_finished_photographed_priced_product_needs_nothing(monkeypatch):
    twin = {"id": "T1", "sku": "FR-RAYBAN-RB2140-901", "brand": "Ray-Ban", "mrp": 5000,
            "offer_price": 4500, "images": [_PHOTO]}
    out = _needs(monkeypatch, _spine(catalog_status="ACTIVE"), twin)
    assert out["needs"] == [] and out["gaps"] == []


def test_only_cost_missing_is_never_listed(monkeypatch):
    """A draft whose ONLY gap is the cost price is ready for the website (the
    first-publish rule is gaps_before_cost) -- the line must not name cost."""
    twin = {"id": "T1", "sku": "FR-RAYBAN-RB2140-901", "brand": "Ray-Ban", "mrp": 5000,
            "offer_price": 4500, "images": [_PHOTO]}
    out = _needs(monkeypatch, _spine(catalog_status="DRAFT"), twin)
    assert out["needs"] == [] and out["gaps"] == []


def test_an_unknown_product_is_not_guessed(monkeypatch):
    out = _needs(monkeypatch, _spine(product_id="OTHER"))
    assert out["needs"] is None and out["gaps"] == []


def test_without_product_id_the_shape_is_unchanged(monkeypatch):
    db = _Up()
    db.seed("brand_masters", [])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    out = asyncio.run(prod_router.get_website_verdict(brand="X", current_user=_ADMIN))
    assert "needs" not in out and "gaps" not in out


# ---------------------------------------------------------------------------
# A model listing (G2): the line asks the push's OWN press rule on the
# listing view -- listing_plan + listing_view + press_unpriced_sizes, as the
# sync screen's _press_verdict does -- never the raw rule on the twin's own
# rows (NIT from the review of #1295: one rule, never two).
# ---------------------------------------------------------------------------


def _model_needs(monkeypatch, db, pid):
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    return asyncio.run(prod_router.get_website_verdict(brand="Ray-Ban", product_id=pid, current_user=_ADMIN))["needs"]


def _unprice(db, pid):
    from test_shopify_model_listings import _twin

    db["products"].update_one({"product_id": pid}, {"$set": {"mrp": 0.0, "offer_price": 0.0}})
    db["catalog_products"].update_one({"id": _twin(db, pid)["id"]}, {"$set": {"mrp": 0.0, "offer_price": 0.0}})


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_live_model_whose_held_colour_is_unpriced_is_not_told_it_needs_a_price(monkeypatch):
    """The live lead has no price: the press HOLDS it (it keeps its last
    price on the website) and seeds only the two new, priced colours -- so
    the press goes through and the line must not say "a selling price"."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import FakeShopify, _live_lead, _twin, _world

    db = _world()
    pids, _p, _var = _live_lead(db, FakeShopify())
    _unprice(db, pids[0])
    assert diag._press_verdict(db, _twin(db, pids[0]))[0] is None, "the press itself refuses nothing"
    needs = _model_needs(monkeypatch, db, pids[0])
    assert needs == [], needs


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_first_publish_with_an_unpriced_colour_needs_a_selling_price(monkeypatch):
    """The press refuses a first publish that would seed an unpriced colour
    (UNPRICED_SIZE) -- the lead's line names that colour."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _twin, _world

    db = _world()
    lead, black = _family(db, [("Gold", "001"), ("Black", "002", "58", {"mrp": 0.0})])
    black_sku = db["products"].find_one({"product_id": black})["sku"]
    assert diag._press_verdict(db, _twin(db, lead))[0] == "UNPRICED_SIZE"
    assert _model_needs(monkeypatch, db, lead) == ["a selling price (" + black_sku + ")"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_colour_of_a_model_goes_with_its_models_page(monkeypatch):
    """push_product refuses a colour's own press (variant_of): the line says
    what the sync screen says, never a photo of a page it has none of -- and
    nothing more while its model's press carries it."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _world

    db = _world()
    _lead, black = _family(db, [("Gold", "001"), ("Black", "002", "58", {"photos": 0})])
    assert _model_needs(monkeypatch, db, black) == [diag.PUSH_THE_MODEL]


def _sweep_reason(db, pid):
    """The sync screen's own per-SKU reason for this product."""
    from api.services import online_content_diagnostics as diag

    sku = db["products"].find_one({"product_id": pid})["sku"]
    (row,) = [r for r in diag.colour_size_parity(db)["missing_items"] if r["sku"] == sku]
    return row["reason"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_colour_of_a_refused_model_names_its_models_refusal(monkeypatch):
    """Its model's first publish is refused (it would seed this colour with
    no price): the colour's line says so, in the sync screen's own words."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _world

    db = _world()
    _lead, black = _family(db, [("Gold", "001"), ("Black", "002", "58", {"mrp": 0.0})])
    reason = _sweep_reason(db, black)
    assert reason.startswith("no selling price on "), reason
    assert _model_needs(monkeypatch, db, black) == [diag.PUSH_THE_MODEL, "its model's page: " + reason]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_new_unpriced_colour_of_a_live_model_names_its_hold(monkeypatch):
    """A live model's press goes through but HOLDS a new colour with no
    price: that colour's line names the hold, as the sync screen does."""
    from api.services import online_content_diagnostics as diag
    from api.services import product_master as pm
    from test_shopify_model_listings import FakeShopify, _item, _live_single, _world

    db = _world()
    lead, _p, _var = _live_single(db, FakeShopify())
    black = _item(db, 2, "Black", "002/58", mrp=0.0)
    pm.link_to_family(db, black, lead, actor="u-admin")
    reason = _sweep_reason(db, black)
    assert reason.startswith("colour/size held - UNPRICED_SIZE"), reason
    assert _model_needs(monkeypatch, db, black) == [diag.PUSH_THE_MODEL, "its model's page: " + reason]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_colour_of_a_taken_down_model_says_taken_down(monkeypatch):
    """A colour whose model was taken down will not go up with it."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _twin, _world

    db = _world()
    lead, black = _family(db, [("Gold", "001"), ("Black", "002")])
    db["catalog_products"].update_one({"id": _twin(db, lead)["id"]}, {"$set": {"ecom.taken_down_at": "2026-10-01"}})
    assert _model_needs(monkeypatch, db, black) == [
        diag.PUSH_THE_MODEL, "its model's page: taken down - not queued for automatic publish"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_lead_with_no_photo_of_its_own_ships_its_models_photos(monkeypatch):
    """The photo rule reads the listing view (the model's photos), as the
    press does: a lead whose colour carries the photos is not 'a photo' short."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _twin, _world

    db = _world()
    lead, _black = _family(db, [("Gold", "001", "58", {"photos": 0}), ("Black", "002")])
    assert diag._press_verdict(db, _twin(db, lead))[0] is None, "the press ships the model's photos"
    assert _model_needs(monkeypatch, db, lead) == []


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_an_unreadable_model_is_not_guessed(monkeypatch):
    """The listing read is strict (a dead read is never "no sizes"): the line
    says it could not be checked, never a guess."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _world

    def _dead(*_a, **_k):
        raise RuntimeError("catalog_variants unreachable")

    db = _world()
    lead, _black = _family(db, [("Gold", "001"), ("Black", "002")])
    monkeypatch.setattr(diag, "variant_rows_for_product", _dead)
    assert _model_needs(monkeypatch, db, lead) is None


# ---------------------------------------------------------------------------
# A lead's own line asks the press's OWN gates (press_gates, the function
# _press_verdict asks) -- block status and the family plan's refusals too,
# never a partial copy that checks only photo and price (review of #1305).
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("brand_is_for_the_website")
@pytest.mark.parametrize("blocked, code", [(True, "online_sync_blocked"), (None, "block_status_unverifiable")])
def test_a_blocked_or_unreadable_block_lead_says_what_the_press_says(monkeypatch, blocked, code):
    from api.services import online_block
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _item, _twin, _world

    db = _world()
    lead = _item(db, 1, "Gold", "001")
    monkeypatch.setattr(online_block, "is_blocked_from_online_strict", lambda product, db: blocked)
    got_code, reason, _plan = diag._press_verdict(db, _twin(db, lead))
    assert got_code == code
    assert _model_needs(monkeypatch, db, lead) == [reason]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_model_whose_family_push_is_refused_says_so(monkeypatch):
    """Two colours that look the same: the press refuses the family
    (OPTION_CLASH) -- the lead's line must not read 'nothing more is needed'."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _family, _twin, _world

    db = _world()
    lead, _twin_colour = _family(db, [("Gold", "001", "58"), ("Gold", "001", "58")])
    code, reason, _plan = diag._press_verdict(db, _twin(db, lead))
    assert code == "OPTION_CLASH", (code, reason)
    assert _model_needs(monkeypatch, db, lead) == [reason]


def test_a_colour_whose_own_refusal_stands_does_not_repeat_it_through_its_model(monkeypatch):
    """Colour and lead share a brand kept off the website: the colour's own
    refusal is said once, never again as 'its model's page: ...'."""
    from api.services import catalog_dictionary
    from api.services import online_content_diagnostics as diag
    from api.services.shopify_push import product_push_refusal
    from test_shopify_model_listings import _family, _twin, _world

    monkeypatch.setattr(catalog_dictionary, "brand_website_refusal",
                        lambda db, brand: "Ray-Ban is kept off the website")
    db = _world()
    _lead, black = _family(db, [("Gold", "001"), ("Black", "002")])
    why = product_push_refusal(db, _twin(db, black))
    assert why, "the brand keeps the colour off the website"
    needs = _model_needs(monkeypatch, db, black)
    assert needs == [why, diag.PUSH_THE_MODEL], needs
    assert not any(n.startswith("its model's page:") for n in needs)


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_parent_with_no_sku_never_borrows_a_stray_sku_less_twin_as_lead():
    """The parent spine has no SKU and a stray twin has sku '': model_lead
    must find no lead (never the stray), and the sync screen and the form
    both say so."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _item, _twin, _world

    db = _world()
    lead = _item(db, 1, "Gold", "001")
    black = _item(db, 2, "Black", "002")
    db["products"].update_one({"product_id": black}, {"$set": {"variant_of": lead}})
    db["products"].update_one({"product_id": lead}, {"$set": {"sku": ""}})
    db["catalog_products"].insert_one({"id": "tw-stray", "sku": "", "name": "stray", "ecom": {}})
    spine = db["products"].find_one({"product_id": black})
    product = _twin(db, black)
    twins, spines = db["catalog_products"], db["products"]
    assert diag.model_lead(product, spine, lambda i: twins.find_one({"id": i}),
                           lambda s: twins.find_one({"sku": s}),
                           lambda i: spines.find_one({"product_id": i})) is None
    assert diag.model_hold(db, product, spine) == "family lead could not be found"
    sweep = diag.colour_size_parity(db)
    assert sweep["checked"], sweep
    (row,) = [r for r in sweep["missing_items"] if r["sku"] == spine["sku"]]
    assert row["reason"] == "family lead could not be found", row


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_draft_colour_still_names_its_models_own_blocker(monkeypatch):
    """The colour's own refusal (a catalogue draft) is a different fact from
    its model's press verdict (no photo): both are listed at once, never
    revealed one after the other (review of #1305)."""
    from api.services import online_content_diagnostics as diag
    from api.services.shopify_push import product_push_refusal
    from test_shopify_model_listings import _family, _twin, _world

    db = _world()
    lead, black = _family(db, [("Gold", "001", "58", {"photos": 0}), ("Black", "002", "58", {"photos": 0})])
    db["products"].update_one({"product_id": black}, {"$set": {"catalog_status": "DRAFT"}})
    code, reason, _plan = diag._press_verdict(db, _twin(db, lead))
    assert code == "no_photo", (code, reason)
    why = product_push_refusal(db, _twin(db, black))
    assert why and "draft" in why, why
    assert _model_needs(monkeypatch, db, black) == [why, diag.PUSH_THE_MODEL, "its model's page: " + reason]
