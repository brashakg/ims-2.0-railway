"""
Tags: one field, and hand-added Shopify tags survive a push  (sync audit gap #4)
================================================================================
Measured on prod 2026-09-06, two halves:
  (a) DEAD BOX -- the add-product "Shopify tags" box was declared and read by
      nothing, and the catalog review PUT wrote a top-level `tags` the push
      never read (build_product_input reads ONLY ecom.seo.tags), so tags typed
      on the catalog side queued a push that sent the OLD tags.
  (b) WIPE -- every LIVE productUpdate sent the whole `tags` array, which
      REPLACES Shopify's list: the hand tags on the 36 connector-uploaded
      Ray-Ban Meta products would have been wiped by the first press.

THE DESIGN under test
  * ONE spelling: ecom.seo.tags on the twin, written by product_master.
    set_twin_tags from the review PUT and the add-product box (the spine
    mirror already wrote it); product_master.twin_tags is the one reader
    (legacy top-level `tags` from the BVI import only when it is absent).
  * OWNERSHIP: ecom.shopify_tags_sent is the exact list IMS last sent. An
    UPDATE sends no `tags` on the input; the pass tagsAdd's what is new and
    tagsRemove's what IMS dropped -- only tags on the ledger. A product live
    before the ledger existed is ADOPTED: add only, nothing removed. CREATE
    keeps the full list on the input.
  * Fail-soft with a stable code (TAGS_NOT_SYNCED); the ledger is written
    only after a clean pass.

***** SAFETY-CRITICAL: every Shopify call is MOCKED (shopify_push._graphql is
monkeypatched); the dark test uses a spy that EXPLODES on any call. *****

Discriminating power was measured by reverting each rule one at a time (see
the PR body): the create-only input, the owned-only remove, the adopt-never-
removes, the no-ledger-on-error, the already-there skip, the add-before-
remove order, the dark plan and each of the three doors turn a test red.

No emoji (Windows cp1252).
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.routers import catalog as catalog_mod  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402

# The sibling harnesses, not copies: the in-memory _DB whose $set nests
# dot-notation, and the review PUT's no-DB rig (in-memory catalog + a real
# spine repo over a MockCollection).
from test_catalog_review_put import _bvi_doc, _promote, _put, _user, env  # noqa: E402,F401
from test_online_push_dirty_flag import _DB, _run  # noqa: E402

GID = "gid://shopify/Product/900"
HAND = "Meta Launch"  # a tag a human typed into the Shopify admin


# ---------------------------------------------------------------------------
# A fake Shopify that routes on the operation, keeps a transcript and holds
# the product's CURRENT tag list (what the update response reports).
# ---------------------------------------------------------------------------


class _Shopify:
    def __init__(self, tags=None):
        self.calls = []
        self.tags = list(tags or [])
        self.fail_add = False
        self.ledger_at_add = None

    @staticmethod
    def _op(query):
        for name in (
            "imsTagsAdd",
            "imsTagsRemove",
            "imsProductCreate",
            "imsProductUpdate",
            "imsPublishablePublish",
            "imsVariantPricesUpdate",
            "imsVariantsBulkCreate",
            "imsVariantInventoryUpdate",
            "imsInventorySetQuantities",
            "imsProductCreateMedia",
            "imsLocations",
            "metafieldsSet",
        ):
            if name in query:
                return name
        return "unknown"

    def calls_of(self, op):
        return [c for c in self.calls if c["op"] == op]

    def ops(self):
        return [c["op"] for c in self.calls]

    async def __call__(self, db, query, variables):
        op = self._op(query)
        self.calls.append({"op": op, "variables": copy.deepcopy(variables)})
        variant = {
            "id": "gid://shopify/ProductVariant/901",
            "title": "Default Title",
            "selectedOptions": [],
            "inventoryItem": {"id": "gid://shopify/InventoryItem/902"},
        }
        if op in ("imsProductCreate", "imsProductUpdate"):
            field = "productCreate" if op == "imsProductCreate" else "productUpdate"
            if op == "imsProductCreate":
                self.tags = list((variables.get("input") or {}).get("tags") or [])
            elif "tags" in (variables.get("input") or {}):
                # The wipe: productUpdate REPLACES the whole list.
                self.tags = list(variables["input"]["tags"])
            return {
                "data": {
                    field: {
                        "product": {
                            "id": GID,
                            "handle": "h",
                            "tags": list(self.tags),
                            "variants": {"nodes": [variant]},
                            "media": {"nodes": [{"id": "gid://shopify/MediaImage/1"}]},
                        },
                        "userErrors": [],
                    }
                }
            }
        if op == "imsTagsAdd":
            self.ledger_at_add = (
                db["catalog_products"].find_one({"id": "P1"}) or {}
            ).get("ecom", {}).get("shopify_tags_sent")
            if self.fail_add:
                return {
                    "data": {
                        "tagsAdd": {
                            "node": None,
                            "userErrors": [{"field": ["tags"], "message": "boom"}],
                        }
                    }
                }
            for t in variables["tags"]:
                if t.lower() not in [x.lower() for x in self.tags]:
                    self.tags.append(t)
            return {"data": {"tagsAdd": {"node": {"id": GID}, "userErrors": []}}}
        if op == "imsTagsRemove":
            wanted = [t.lower() for t in variables["tags"]]
            self.tags = [t for t in self.tags if t.lower() not in wanted]
            return {"data": {"tagsRemove": {"node": {"id": GID}, "userErrors": []}}}
        if op == "imsPublishablePublish":
            return {"data": {"publishablePublish": {"userErrors": []}}}
        if op == "imsVariantPricesUpdate":
            return {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [{"id": variant["id"]}],
                        "userErrors": [],
                    }
                }
            }
        if op == "imsVariantsBulkCreate":
            return {
                "data": {"productVariantsBulkCreate": {"productVariants": [], "userErrors": []}}
            }
        if op == "metafieldsSet":
            return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
        if op == "imsLocations":
            return {"data": {"locations": {"nodes": []}}}
        return {"data": {}}


@pytest.fixture
def db():
    return _DB()


@pytest.fixture
def gates(monkeypatch):
    """Open the three push gates and pin the publication id; the network is
    whatever the test installs."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {
            "shop_url": "t.myshopify.com",
            "access_token": "shpat_t",
            "source": "vault",
        },
    )
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/1")
    shopify_push._publication_id_cache.clear()


def _live(monkeypatch, tags=None):
    fake = _Shopify(tags)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    return fake


def _product(seo_tags, sent=None, shopify_id=GID, pid="P1"):
    """A Ray-Ban FRAME (so ims_product_tags = seo tags + brand_rayban +
    gender_men) already on Shopify (or not, shopify_id=None), with the
    ownership ledger IMS recorded (None = never through the pass)."""
    doc = {
        "id": pid,
        "sku": "SKU-1",
        "title": "Ray-Ban RB2140",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "attributes": {"gender": "Men"},
        "mrp": 5000.0,
        "offer_price": 4000.0,
        "images": ["https://cdn.example.com/rb-front.jpg"],
        "ecom": {
            "status": "PUBLISHED",
            "locally_modified": True,
            "seo": {"tags": list(seo_tags)},
        },
    }
    if shopify_id:
        doc["ecom"]["shopify_product_id"] = shopify_id
        doc["ecom"]["shopify_variant_id"] = "gid://shopify/ProductVariant/901"
    if sent is not None:
        doc["ecom"]["shopify_tags_sent"] = list(sent)
    return doc


def _seed(db, doc):
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    return copy.deepcopy(doc)


def _ledger(db, pid="P1"):
    return (db["catalog_products"].find_one({"id": pid}) or {})["ecom"].get("shopify_tags_sent")


IMS_BASE = ["brand_rayban", "gender_men"]  # the attribute-derived filter tags


# ===========================================================================
# A. THE OWNERSHIP PASS, through push_product's live transcript
# ===========================================================================


def test_create_sends_the_full_list_on_the_input_and_records_the_ledger(db, gates, monkeypatch):
    fake = _live(monkeypatch)
    doc = _seed(db, _product(["Aviator"], shopify_id=None))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True and res.mode == "LIVE" and res.action == "create"
    (cre,) = fake.calls_of("imsProductCreate")
    assert cre["variables"]["input"]["tags"] == ["aviator"] + IMS_BASE
    assert fake.calls_of("imsTagsAdd") == [] and fake.calls_of("imsTagsRemove") == []
    assert _ledger(db) == ["aviator"] + IMS_BASE
    assert res.tags == {"added": 3, "removed": 0, "unmanaged": 0, "adopted": False}


def test_an_update_never_sends_tags_on_the_product_input(db, gates, monkeypatch):
    """THE WIPE: productUpdate's `tags` replaces Shopify's whole list. An
    existing product's input carries none, whatever IMS wants."""
    fake = _live(monkeypatch, ["aviator"] + IMS_BASE + [HAND])
    doc = _seed(db, _product(["Aviator", "Summer"], sent=["aviator"] + IMS_BASE))

    res = _run(shopify_push.push_product(db, doc, []))

    (upd,) = fake.calls_of("imsProductUpdate")
    assert "tags" not in upd["variables"]["input"], upd["variables"]["input"]
    assert res.ok is True and HAND in fake.tags


def test_an_update_adds_the_new_and_removes_the_dropped_ims_tags_only(db, gates, monkeypatch):
    """IMS swapped `aviator` for `summer`. Shopify carries the old IMS list
    plus a hand tag. Exactly one add, exactly one remove, add first; the
    hand tag is counted unmanaged and never named; the ledger moves on."""
    fake = _live(monkeypatch, ["aviator"] + IMS_BASE + [HAND])
    doc = _seed(db, _product(["Summer"], sent=["aviator"] + IMS_BASE))

    res = _run(shopify_push.push_product(db, doc, []))

    tag_ops = [o for o in fake.ops() if o.startswith("imsTags")]
    assert tag_ops == ["imsTagsAdd", "imsTagsRemove"], tag_ops
    (add,) = fake.calls_of("imsTagsAdd")
    (rem,) = fake.calls_of("imsTagsRemove")
    assert add["variables"] == {"id": GID, "tags": ["summer"]}
    assert rem["variables"] == {"id": GID, "tags": ["aviator"]}
    assert fake.tags == IMS_BASE + [HAND, "summer"]
    assert res.tags == {"added": 1, "removed": 1, "unmanaged": 1, "adopted": False}
    assert _ledger(db) == ["summer"] + IMS_BASE
    assert res.ok is True and res.publication["published"] is True


def test_dropping_every_ims_tag_leaves_the_hand_tags_standing(db, gates, monkeypatch):
    """The measured hazard in its purest form: IMS clears its own tags; the
    remove names ONLY what IMS sent, so `Meta Launch` stays."""
    fake = _live(monkeypatch, ["aviator", HAND, "Limited"])
    doc = _seed(db, _product([], sent=["aviator"]))
    doc["brand"] = None
    doc["attributes"] = {}
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"brand": None, "attributes": {}}})

    res = _run(shopify_push.push_product(db, doc, []))

    (rem,) = fake.calls_of("imsTagsRemove")
    assert rem["variables"]["tags"] == ["aviator"]
    assert fake.tags == [HAND, "Limited"]
    assert res.tags["unmanaged"] == 2 and res.tags["removed"] == 1
    assert _ledger(db) == []


def test_a_product_live_before_the_ledger_is_adopted_add_only(db, gates, monkeypatch):
    """The 36 connector-uploaded Ray-Ban Meta products and the six IMS pushed
    before this: on Shopify, no ledger. Every tag already there is
    unmanaged -- IMS adds what it wants and is missing, removes NOTHING,
    and records the ledger so the next press can diff."""
    fake = _live(monkeypatch, ["brand_rayban", "smart-glasses", HAND])
    doc = _seed(db, _product(["Aviator"]))  # gid, no shopify_tags_sent

    res = _run(shopify_push.push_product(db, doc, []))

    (add,) = fake.calls_of("imsTagsAdd")
    assert add["variables"]["tags"] == ["aviator", "gender_men"]
    assert fake.calls_of("imsTagsRemove") == []
    assert fake.tags == ["brand_rayban", "smart-glasses", HAND, "aviator", "gender_men"]
    assert res.tags == {"added": 2, "removed": 0, "unmanaged": 2, "adopted": True}
    assert _ledger(db) == ["aviator"] + IMS_BASE


def test_a_repress_with_nothing_changed_makes_no_tag_call(db, gates, monkeypatch):
    fake = _live(monkeypatch, ["aviator"] + IMS_BASE + [HAND])
    doc = _seed(db, _product(["Aviator"], sent=["aviator"] + IMS_BASE))

    res = _run(shopify_push.push_product(db, doc, []))

    assert not [o for o in fake.ops() if o.startswith("imsTags")], fake.ops()
    assert res.tags == {"added": 0, "removed": 0, "unmanaged": 1, "adopted": False}
    assert _ledger(db) == ["aviator"] + IMS_BASE


def test_a_tag_a_human_already_added_is_not_re_added_but_becomes_managed(db, gates, monkeypatch):
    """IMS now wants `summer`, which someone already put on in the admin: no
    call (tagsAdd would be a no-op), but the ledger records it -- IMS
    computed it, so IMS will take it off if it later drops it."""
    fake = _live(monkeypatch, ["aviator"] + IMS_BASE + ["Summer"])
    doc = _seed(db, _product(["Aviator", "Summer"], sent=["aviator"] + IMS_BASE))

    res = _run(shopify_push.push_product(db, doc, []))

    assert fake.calls_of("imsTagsAdd") == []
    assert res.tags["added"] == 0 and res.tags["unmanaged"] == 0
    assert _ledger(db) == ["aviator", "summer"] + IMS_BASE


def test_a_tag_error_is_reported_with_a_code_and_the_ledger_stays_put(db, gates, monkeypatch):
    """Fail-soft: the push still publishes, the operator gets a stable code,
    the remove is NOT attempted (add-before-remove stops on failure) and the
    ledger is untouched so the next press diffs against the truth."""
    fake = _live(monkeypatch, ["aviator"] + IMS_BASE)
    fake.fail_add = True
    doc = _seed(db, _product(["Summer"], sent=["aviator"] + IMS_BASE))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True and res.publication["published"] is True
    assert res.tags["code"] == shopify_push.TAGS_CODE == "TAGS_NOT_SYNCED"
    assert "tagsAdd" in res.tags["error"] and "boom" in res.tags["error"]
    assert res.tags["added"] == 0 and res.tags["removed"] == 0
    assert fake.calls_of("imsTagsRemove") == []
    assert fake.ledger_at_add == ["aviator"] + IMS_BASE
    assert _ledger(db) == ["aviator"] + IMS_BASE


def test_the_dark_plan_carries_the_diff_with_zero_network(db, monkeypatch):
    async def _explode(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("dark press touched the network")

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _explode)

    doc = _seed(db, _product(["Summer"], sent=["aviator"] + IMS_BASE))
    res = _run(shopify_push.push_product(db, doc, []))
    assert res.mode == "SIMULATED" and res.ok is True
    assert "tags" not in res.payload
    assert res.tags == {
        "add": ["summer"], "remove": ["aviator"],
        "unmanaged": None, "adopt": False, "create": False,
    }
    assert _ledger(db) == ["aviator"] + IMS_BASE  # a dry-run records nothing

    new = _seed(db, _product(["Aviator"], shopify_id=None, pid="P2"))
    res2 = _run(shopify_push.push_product(db, new, []))
    assert res2.payload["tags"] == ["aviator"] + IMS_BASE
    assert res2.tags == {
        "add": ["aviator"] + IMS_BASE, "remove": [],
        "unmanaged": 0, "adopt": False, "create": True,
    }


# ===========================================================================
# B. ONE FIELD -- every door lands tags where the push reads them
# ===========================================================================


def test_the_review_put_lands_tags_where_the_push_reads_them(env):
    """Half (a): the review editor's tags used to go top-level and never
    reach Shopify. Now they are the twin's ecom.seo.tags -- and the very next
    payload carries them."""
    doc = _bvi_doc(doc_id="clx0tagsput1", sku="TAGSPUT1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"tags": [" Aviator ", "New Arrival", "", "aviator"]})

    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["ecom"]["seo"]["tags"] == ["aviator", "new arrival"]
    assert pm.twin_tags(updated) == ["aviator", "new arrival"]
    assert updated["tags"] == ["eyewear"]  # the import's legacy copy, not rewritten
    sent = shopify_push.ims_product_tags(updated)
    assert sent[:2] == ["aviator", "new arrival"]
    assert updated["ecom"]["locally_modified"] is True  # queued for the next press


def test_the_add_product_shopify_tags_box_lands_on_the_twin_and_the_spine(env):
    """Half (a), the dead box: ShopifySyncInput.shopify_tags was declared and
    dropped. Through the real create door it is now the spine's governed
    `tags` and the twin's ecom.seo.tags."""
    inp = catalog_mod.ProductCreateInput(
        category="FR",
        attributes={"brand_name": "Ray-Ban", "model_no": "RB-TAG-001", "colour_code": "BLK"},
        pricing={"mrp": 4000, "offer_price": 3600, "discount_category": "MASS"},
        shopify={"sync_to_shopify": False, "shopify_tags": [" Bestseller ", "New", "new"]},
    )

    res = _run(catalog_mod.create_catalog_product(inp, _user()))

    twin = res["product"]
    assert twin["ecom"]["seo"]["tags"] == ["bestseller", "new"]
    assert twin["ecom"]["locally_modified"] is True
    spine = env["repo"].find_by_id(twin["id"])
    assert spine is not None and spine["tags"] == ["bestseller", "new"]
    assert shopify_push.ims_product_tags(twin)[:2] == ["bestseller", "new"]


def test_promote_reads_ecom_seo_tags_before_the_imports_top_level_copy(env):
    """The one reader of the twin's tags on the spine side: a door-written
    ecom.seo.tags wins; a doc that never went through a door still promotes
    the BVI import's top-level list."""
    edited = _bvi_doc(doc_id="clx0tagspro1", sku="TAGSPRO1")
    edited["ecom"] = {"seo": {"tags": ["aviator"]}}
    catalog_mod.CATALOG_PRODUCTS[edited["id"]] = edited
    legacy = _bvi_doc(doc_id="clx0tagspro2", sku="TAGSPRO2")
    catalog_mod.CATALOG_PRODUCTS[legacy["id"]] = legacy

    _promote(edited["id"])
    _promote(legacy["id"])

    assert env["repo"].find_by_id(edited["id"])["tags"] == ["aviator"]
    assert env["repo"].find_by_id(legacy["id"])["tags"] == ["eyewear"]
