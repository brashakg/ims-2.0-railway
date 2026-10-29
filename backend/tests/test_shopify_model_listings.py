"""
G2 -- ONE website listing per MODEL, its colours and sizes as variants
=====================================================================
Owner rulings 2026-10-18: same model number in other colours / sizes =
variants of ONE product on the website (G1 #1226 holds the family in IMS).
Each variant keeps its own SKU, maker GTIN, price and per-shop stock; the
lead (first catalogued) leads -- its photo first; the title is Brand + Model
+ model name; description and SEO are written for the model.

Every Shopify call is MOCKED by ``FakeShopify`` -- a small STATEFUL Shopify
(products, options, variants, inventory, media, metafields, redirects) that
answers the push's own GraphQL documents by operation name and refuses what
real Shopify refuses (an option a variant does not fill, a combination that
exists, a create that would delete the standalone variant, media not ready).
Nothing reaches the network; the DARK tests use a boundary that explodes.

Pinned (each red when its rule is reverted -- see the PR body):
  * colour-only / size-only / colour+size (a 15-variant RB3025) first
    publish -> ONE product, the options the model varies on, every variant
    priced with its own SKU + GTIN + photo, lead first
  * a live one-variant listing gains two colours: the option is ADDED (the
    lead's variant keeps its id and takes the lead's colour), two variants
    created under PRESERVE_STANDALONE_VARIANT, the rows move under the
    listing with their Shopify ids, stock per shop for each, no duplicate
  * a colour joining a live listing never takes the lead's variant
  * the lead archived -> the listing moves to the next colour (same id,
    same handle), the old lead's variant sells 0, never deleted
  * the duplicate-listing runbook: dry-run writes and sends nothing; apply
    takes the second listing down, redirects it, folds its colour in
  * title / SEO / body never name one colour; an unpriced variant, a clash
    and a blank option refuse before anything is sent
  * variants past the first 100 are read (never created twice)

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_shopify_model_listings.py -q
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import online_delist  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.product_description import names_a_colour  # noqa: E402
from api.services.shopify_push import listing as model  # noqa: E402
from api.services.shopify_push import variants as seeding  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

pytestmark = pytest.mark.usefixtures("brand_is_for_the_website")

LOC1, LOC2 = "gid://shopify/Location/71", "gid://shopify/Location/72"
SHOP1, SHOP2 = "store-ranchi", "store-dhanbad"
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "merge_duplicate_model_listings.py",
)


def _run(coro):
    return asyncio.run(coro)


def _ean(n: int) -> str:
    """A valid maker EAN-13 (prefix 805 -- never the in-store 20-29 range)."""
    body = f"805289{n:06d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


# ---------------------------------------------------------------------------
# A small STATEFUL Shopify
# ---------------------------------------------------------------------------


def _op(query: str) -> str:
    m = re.search(r"(?:mutation|query)\s+(\w+)", query or "")
    return m.group(1) if m else ""


class FakeShopify:
    """Answers the push's GraphQL documents by operation name, keeping the
    state real Shopify would keep -- and refusing what it would refuse."""

    def __init__(self, locations=(LOC1, LOC2)):
        self.products = {}
        self.calls = []
        self.locations = list(locations)
        self.quantities = {}
        self.metafields = {}
        self.redirects = []
        self.deleted_variants = []
        self.media_ready = True
        self.variant_refusal = None
        self._n = 1000

    # -- bookkeeping --------------------------------------------------------
    def _id(self, kind):
        self._n += 1
        return f"gid://shopify/{kind}/{self._n}"

    def ops(self, name=None):
        return [v for (n, v) in self.calls if name is None or n == name]

    def writes(self):
        return [n for (n, _v) in self.calls if n not in ("imsLocationList", "imsProductVariantsPage")]

    def new_product(self, *, handle, options=None, variants=None, status="ACTIVE"):
        """A listing that is already live (the 25 one-variant listings)."""
        gid = self._id("Product")
        opts = options or [{"name": "Title", "values": ["Default Title"]}]
        p = {"id": gid, "title": handle, "handle": handle, "status": status, "tags": [], "options": copy.deepcopy(opts),
             "variants": [], "media": [], "published": True, "input": {}}
        for v in variants or [{"opts": {"Title": "Default Title"}}]:
            p["variants"].append({"id": self._id("ProductVariant"), "inv": self._id("InventoryItem"), "price": v.get("price"),
                                  "compareAtPrice": None, "barcode": None, "sku": v.get("sku"), "tracked": True,
                                  "policy": "DENY", "media": None, "opts": dict(v["opts"])})
        self.products[gid] = p
        return p

    def variant(self, gid):
        for p in self.products.values():
            for v in p["variants"]:
                if v["id"] == gid:
                    return p, v
        return None, None

    # -- shapes -------------------------------------------------------------
    def _vnode(self, p, v):
        names = [o["name"] for o in p["options"]]
        return {
            "id": v["id"],
            "title": " / ".join(v["opts"].get(n, "") for n in names),
            "selectedOptions": [{"name": n, "value": v["opts"].get(n)} for n in names],
            "inventoryItem": {"id": v["inv"], "tracked": v["tracked"]},
            "inventoryPolicy": v["policy"],
            "media": {"nodes": [{"id": v["media"]}] if v["media"] else []},
        }

    def _options(self, p):
        return [
            {"id": f"{p['id']}/opt{i}", "name": o["name"], "position": i + 1,
             "optionValues": [{"id": f"{p['id']}/opt{i}/{j}", "name": val} for j, val in enumerate(o["values"])]}
            for i, o in enumerate(p["options"])
        ]

    def _node(self, p):
        return {
            "id": p["id"], "handle": p["handle"], "tags": list(p["tags"]), "options": self._options(p),
            "variants": {
                "pageInfo": {"hasNextPage": len(p["variants"]) > 100, "endCursor": "100"},
                "nodes": [self._vnode(p, v) for v in p["variants"][:100]],
            },
            "media": {"nodes": [{"id": m["id"], "image": {"url": m["url"]}} for m in p["media"]]},
        }

    @staticmethod
    def _ok(field, **extra):
        return {"data": {field: {"userErrors": [], **extra}}}

    async def __call__(self, db, query, variables):  # noqa: ARG002
        name = _op(query)
        self.calls.append((name, copy.deepcopy(variables)))
        handler = getattr(self, "_h_" + name, None)
        return handler(variables or {}) if handler else {"data": {}}

    # -- products -----------------------------------------------------------
    def _h_imsProductCreate(self, v):
        inp = v["input"]
        gid = self._id("Product")
        opts = [{"name": o["name"], "values": [x["name"] for x in o["values"]]} for o in inp.get("productOptions") or []]
        opts = opts or [{"name": "Title", "values": ["Default Title"]}]
        handle = inp.get("handle") or re.sub(r"[^a-z0-9]+", "-", inp["title"].lower()).strip("-")
        p = {"id": gid, "title": inp["title"], "handle": handle, "status": inp.get("status"), "tags": list(inp.get("tags") or []),
             "options": opts, "variants": [], "media": [], "published": False, "input": copy.deepcopy(inp)}
        # productCreate makes ONE variant, the first value of every option.
        p["variants"].append({"id": self._id("ProductVariant"), "inv": self._id("InventoryItem"), "price": "0.00",
                              "compareAtPrice": None, "barcode": None, "sku": None, "tracked": False, "policy": "CONTINUE",
                              "media": None, "opts": {o["name"]: o["values"][0] for o in opts}})
        self.products[gid] = p
        return {"data": {"productCreate": {"product": self._node(p), "userErrors": []}}}

    def _h_imsProductUpdate(self, v):
        inp = v["input"]
        p = self.products[inp["id"]]
        if "productOptions" in inp:
            return {"data": {"productUpdate": {"product": None, "userErrors": [{"field": ["productOptions"], "message": "cannot be specified during update"}]}}}
        for k in ("title", "status", "handle", "descriptionHtml", "seo"):
            if k in inp:
                p[k] = inp[k]
        p["input"] = copy.deepcopy(inp)
        return {"data": {"productUpdate": {"product": self._node(p), "userErrors": []}}}

    def _h_imsProductOptionsCreate(self, v):
        p = self.products[v["productId"]]
        if [o["name"] for o in p["options"]] == ["Title"] and p["options"][0]["values"] == ["Default Title"]:
            p["options"] = []  # the placeholder is replaced
            for var in p["variants"]:
                var["opts"].pop("Title", None)
        for o in v["options"]:
            if o["name"] in [x["name"] for x in p["options"]]:
                return self._ok("productOptionsCreate", product=None) | {"data": {"productOptionsCreate": {
                    "product": None, "userErrors": [{"field": ["options"], "message": "exists", "code": "OPTION_ALREADY_EXISTS"}]}}}
            values = [x["name"] for x in o["values"]]
            p["options"].append({"name": o["name"], "values": values})
            for var in p["variants"]:  # LEAVE_AS_IS: every variant takes the first value
                var["opts"][o["name"]] = values[0]
        return {"data": {"productOptionsCreate": {"product": {"id": p["id"], "options": self._options(p)}, "userErrors": []}}}

    def _h_imsProductVariantsPage(self, v):
        p = self.products[v["id"]]
        start = int(v.get("after") or 0)
        page = p["variants"][start : start + 100]
        return {"data": {"product": {"variants": {
            "pageInfo": {"hasNextPage": start + 100 < len(p["variants"]), "endCursor": str(start + 100)},
            "nodes": [self._vnode(p, x) for x in page]}}}}

    def _h_imsListingHandle(self, v):
        p = self.products[v["id"]]
        return {"data": {"product": {"id": p["id"], "handle": p["handle"], "status": p["status"]}}}

    # -- variants -----------------------------------------------------------
    def _h_imsVariantsBulkCreate(self, v):
        p = self.products[v["productId"]]
        names = [o["name"] for o in p["options"]]
        standalone = names == ["Title"] and len(p["variants"]) == 1
        made, errors = [], []
        for i, row in enumerate(v["variants"]):
            given = {x["optionName"]: x["name"] for x in row.get("optionValues") or []}
            if set(given) - set(names):
                errors.append({"field": ["variants", str(i)], "message": "Option does not exist"})
                continue
            if set(names) - set(given):
                errors.append({"field": ["variants", str(i)], "message": "must have a value for every option"})
                continue
            if any(all(x["opts"].get(n) == given[n] for n in names) for x in p["variants"]):
                errors.append({"field": ["variants", str(i)], "message": "The variant already exists"})
                continue
            for o in p["options"]:
                if given[o["name"]] not in o["values"]:
                    o["values"].append(given[o["name"]])
            var = {"id": self._id("ProductVariant"), "inv": self._id("InventoryItem"), "price": row.get("price"),
                   "compareAtPrice": row.get("compareAtPrice"), "barcode": row.get("barcode"),
                   "sku": (row.get("inventoryItem") or {}).get("sku"),
                   "tracked": bool((row.get("inventoryItem") or {}).get("tracked")),
                   "policy": row.get("inventoryPolicy") or "CONTINUE", "media": None, "opts": given}
            p["variants"].append(var)
            made.append(self._vnode(p, var))
        if standalone and made and v.get("strategy") != "PRESERVE_STANDALONE_VARIANT":
            self.deleted_variants.append(p["variants"].pop(0)["id"])  # Shopify's DEFAULT
        return {"data": {"productVariantsBulkCreate": {"productVariants": made, "userErrors": errors}}}

    def _h_imsVariantPricesUpdate(self, v):
        out, errors = [], []
        for i, row in enumerate(v["variants"]):
            _p, var = self.variant(row["id"])
            if var is None:
                errors.append({"field": ["variants", str(i), "id"], "message": "Product variant does not exist"})
                continue
            for k in ("price", "compareAtPrice", "barcode"):
                if k in row:
                    var[k] = row[k]
            if (row.get("inventoryItem") or {}).get("sku"):
                var["sku"] = row["inventoryItem"]["sku"]
            out.append({"id": var["id"], "price": var["price"], "compareAtPrice": var["compareAtPrice"], "barcode": var["barcode"]})
        return {"data": {"productVariantsBulkUpdate": {"productVariants": out, "userErrors": errors}}}

    def _h_imsVariantInventoryUpdate(self, v):
        out = []
        for i, row in enumerate(v["variants"]):
            _p, var = self.variant(row["id"])
            if var is None:
                return {"data": {"productVariantsBulkUpdate": {"productVariants": [], "userErrors": [
                    {"field": ["variants", str(i), "id"], "message": "Product variant does not exist"}]}}}
            var["tracked"] = (row.get("inventoryItem") or {}).get("tracked", var["tracked"])
            var["policy"] = row.get("inventoryPolicy", var["policy"])
            out.append({"id": var["id"], "inventoryPolicy": var["policy"], "inventoryItem": {"id": var["inv"], "tracked": var["tracked"]}})
        return {"data": {"productVariantsBulkUpdate": {"productVariants": out, "userErrors": []}}}

    # -- stock --------------------------------------------------------------
    def _h_imsLocationList(self, v):  # noqa: ARG002
        return {"data": {"locations": {"nodes": [
            {"id": loc, "name": loc, "isActive": True, "fulfillsOnlineOrders": True, "shipsInventory": True,
             "address": {"city": "Ranchi", "province": "Jharkhand"}} for loc in self.locations]}}}

    def _h_imsInventorySetQuantities(self, v):
        for q in v["input"]["quantities"]:
            self.quantities[(q["inventoryItemId"], q["locationId"])] = q["quantity"]
        return {"data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": {"createdAt": "now", "reason": "correction"}, "userErrors": []}}}

    # -- media --------------------------------------------------------------
    def _h_imsProductCreateMedia(self, v):
        p = self.products[v["productId"]]
        made = []
        for m in v["media"]:
            node = {"id": self._id("MediaImage"), "url": m["originalSource"], "status": "READY" if self.media_ready else "PROCESSING"}
            p["media"].append(node)
            made.append({"id": node["id"], "status": node["status"], "mediaErrors": []})
        return {"data": {"productCreateMedia": {"media": made, "mediaUserErrors": []}}}

    def _h_imsProductReorderMedia(self, v):  # noqa: ARG002
        return {"data": {"productReorderMedia": {"job": None, "mediaUserErrors": []}}}

    def _h_imsVariantAppendMedia(self, v):
        p = self.products[v["productId"]]
        errors = []
        for pair in v["variantMedia"]:
            _pp, var = self.variant(pair["variantId"])
            media = next((m for m in p["media"] if m["id"] == pair["mediaIds"][0]), None)
            if self.variant_refusal:
                errors.append({"field": ["variantMedia"], "message": self.variant_refusal, "code": "INVALID"})
            elif media is None or media["status"] != "READY":
                errors.append({"field": ["variantMedia"], "message": "Non-ready media are not supported", "code": "NON_READY_MEDIA"})
            elif var["media"]:
                errors.append({"field": ["variantMedia"], "message": "already has media", "code": "PRODUCT_VARIANT_ALREADY_HAS_MEDIA"})
        if errors:
            return {"data": {"productVariantAppendMedia": {"productVariants": [], "userErrors": errors}}}
        for pair in v["variantMedia"]:
            self.variant(pair["variantId"])[1]["media"] = pair["mediaIds"][0]
        return {"data": {"productVariantAppendMedia": {"productVariants": [], "userErrors": []}}}

    def _h_imsVariantDetachMedia(self, v):
        for pair in v["variantMedia"]:
            self.variant(pair["variantId"])[1]["media"] = None
        return {"data": {"productVariantDetachMedia": {"productVariants": [], "userErrors": []}}}

    # -- the rest -----------------------------------------------------------
    def _h_metafieldsSet(self, v):
        for m in v["metafields"]:
            self.metafields.setdefault(m["ownerId"], {})[m["key"]] = m["value"]
        return {"data": {"metafieldsSet": {"metafields": [{"id": "x", "key": m["key"]} for m in v["metafields"]], "userErrors": []}}}

    def _h_metafieldsDelete(self, v):
        for m in v["metafields"]:
            self.metafields.get(m["ownerId"], {}).pop(m["key"], None)
        return {"data": {"metafieldsDelete": {"deletedMetafields": [], "userErrors": []}}}

    def _h_imsPublishablePublish(self, v):
        self.products[v["id"]]["published"] = True
        return self._ok("publishablePublish")

    def _h_imsTagsAdd(self, v):
        self.products[v["id"]]["tags"] += [t for t in v["tags"] if t not in self.products[v["id"]]["tags"]]
        return self._ok("tagsAdd", node={"id": v["id"]})

    def _h_imsTagsRemove(self, v):
        self.products[v["id"]]["tags"] = [t for t in self.products[v["id"]]["tags"] if t not in v["tags"]]
        return self._ok("tagsRemove", node={"id": v["id"]})

    def _h_imsUrlRedirectCreate(self, v):
        self.redirects.append(dict(v["urlRedirect"]))
        return {"data": {"urlRedirectCreate": {"urlRedirect": {"id": "r1", **v["urlRedirect"]}, "userErrors": []}}}


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def _world():
    db = StrictDB()
    db.seed("stores", [
        {"store_id": SHOP1, "store_code": "BV-RAN-01", "store_name": "BV Ranchi", "city": "Ranchi",
         "store_type": "RETAIL", "is_active": True, "shopify_location_id": LOC1},
        {"store_id": SHOP2, "store_code": "BV-DHN-01", "store_name": "BV Dhanbad", "city": "Dhanbad",
         "store_type": "RETAIL", "is_active": True, "shopify_location_id": LOC2},
    ])
    db.seed("storefronts", [{"storefront_id": "BV", "name": "Better Vision", "is_default": True}])
    return db


def _item(db, n, colour, code, size="58", *, mrp=12990.0, offer=None, lens=None, polarized=False, units=(1, 0),
          model="RB3025", name="Aviator Large Metal", photos=2):
    """One catalogued item (spine + twin + its own row), as the create door
    leaves it. ``n`` orders the catalogue (first catalogued = smallest)."""
    pid, twin_id = f"sp-{n}", f"tw-{n}"
    sku = f"SG-RB3025-{code.replace('/', '')}-{size}-{n}"
    attrs = {"brand_name": "Ray-Ban", "model_no": model, "model_name": name, "colour_code": code,
             "frame_color": colour, "lens_size": size, "shape": "Aviator", "gender": "Unisex",
             "frame_material": "Metal", "lens_material": "Glass", "gtin": _ean(n)}
    if lens:
        attrs["lens_colour"] = lens
    if polarized:
        attrs["polarization"] = "Yes"
    spine = {"product_id": pid, "pim_product_id": twin_id, "sku": sku, "category": "SUNGLASS", "brand": "Ray-Ban",
             "model": model, "mrp": mrp, "offer_price": offer if offer is not None else mrp, "attributes": attrs,
             "is_active": True, "created_at": T0 + timedelta(minutes=n)}
    spine["identity_key"] = pm.compute_identity_key(*pm.identity_parts(attrs, "SUNGLASS"))
    images = [f"https://cdn.example.com/{sku}-{i}.jpg" for i in range(1, photos + 1)]
    twin = {"id": twin_id, "sku": sku, "parent_sku": sku, "name": f"Ray-Ban {model} {colour} {size}",
            "title": f"Ray-Ban {model} {colour} {size}", "category": "SUNGLASS", "brand": "Ray-Ban",
            "mrp": mrp, "offer_price": spine["offer_price"], "attributes": dict(attrs), "images": images,
            "ecom": {"status": "DRAFT", "locally_modified": True, "handle": f"rb3025-{code}-{size}".lower().replace("/", "-"),
                     "seo": {"title": f"Ray-Ban {model} - {colour}", "tags": []}}}
    db["products"].insert_one(spine)
    db["catalog_products"].insert_one(twin)
    db["catalog_variants"].insert_one({"sku": sku, "parent_product_id": twin_id, "parent_sku": sku})
    for shop, count in zip((SHOP1, SHOP2), units):
        for k in range(count):
            db["stock_units"].insert_one({"stock_id": f"u-{sku}-{shop}-{k}", "product_id": pid, "store_id": shop, "status": "AVAILABLE"})
    return pid


def _family(db, items):
    """Catalogue ``items`` and link every one after the first to it (G1's door)."""
    pids = [_item(db, n, *it[:3], **(it[3] if len(it) > 3 else {})) for n, it in enumerate(items, start=1)]
    for pid in pids[1:]:
        pm.link_to_family(db, pid, pids[0], actor="u-admin")
    return pids


def _twin(db, pid):
    return db["catalog_products"].find_one({"id": db["products"].find_one({"product_id": pid})["pim_product_id"]})


def _row(db, pid):
    return db["catalog_variants"].find_one({"sku": db["products"].find_one({"product_id": pid})["sku"]})


def _push(db, pid):
    twin = _twin(db, pid)
    return _run(shopify_push.push_product(db, twin, ls.variants_for_product(db, twin), blocked=False))


def _live(monkeypatch, fake):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")


def _dark(monkeypatch):
    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("a DARK push reached the Shopify network")

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _boom)


def _only_product(fake):
    assert len(fake.products) == 1, f"{len(fake.products)} listings for one model"
    return next(iter(fake.products.values()))


COLOURS = [("Gold", "001"), ("Black", "002/58"), ("Silver", "003/32"), ("Gunmetal", "004/78"), ("Bronze", "9196/31")]


def _assert_every_variant_is_its_item(db, fake, product, pids):
    """One variant per item: its own SKU, its own maker GTIN, its own price
    (and MRP strikethrough), its own stock at every shop, its own photo."""
    by_sku = {v["sku"]: v for v in product["variants"]}
    assert len(product["variants"]) == len(pids) == len(by_sku), "no duplicate, nothing missing"
    for pid in pids:
        spine = db["products"].find_one({"product_id": pid})
        v = by_sku[spine["sku"]]
        assert v["barcode"] == spine["attributes"]["gtin"]
        assert float(v["price"]) == spine["offer_price"] > 0
        assert v["tracked"] is True and v["policy"] == "DENY"
        row = _row(db, pid)
        assert row["shopify_variant_id"] == v["id"] and row["shopify_inventory_item_id"] == v["inv"]
        lead_twin_id = _twin(db, pids[0])["id"]
        assert row["parent_product_id"] == lead_twin_id, "the row sits under the model's listing"
        for loc in (LOC1, LOC2):
            assert (v["inv"], loc) in fake.quantities, f"{spine['sku']} stock not written at {loc}"
        photo = _twin(db, pid)["images"][0]
        media = next(m for m in product["media"] if m["url"] == photo)
        assert v["media"] == media["id"], f"{spine['sku']} does not show its own photo"


# ---------------------------------------------------------------------------
# 1. first publish: colour only / size only / colour + size
# ---------------------------------------------------------------------------


def test_colour_only_model_first_publish_is_one_listing_chosen_by_colour(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001", "58"), ("Black", "002/58", "58"), ("Silver", "003/32", "58")])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok and res.action == "create", (res.error, res.code)
    product = _only_product(fake)
    assert product["options"] == [{"name": "Color", "values": ["Gold (001)", "Black (002/58)", "Silver (003/32)"]}]
    assert product["variants"][0]["sku"] == db["products"].find_one({"product_id": pids[0]})["sku"], "the lead leads"
    _assert_every_variant_is_its_item(db, fake, product, pids)
    assert product["title"] == "Ray-Ban RB3025 Aviator Large Metal"
    assert product["handle"] == "ray-ban-rb3025-aviator-large-metal"
    assert product["media"][0]["url"] == _twin(db, pids[0])["images"][0], "the lead's photo 1 is the main image"
    assert len(product["media"]) == 6, "every colour's photos ride the listing"
    assert _twin(db, pids[0])["ecom"]["shopify_product_id"] == product["id"]
    assert not any(_twin(db, p)["ecom"].get("shopify_product_id") for p in pids[1:]), "no colour owns a listing"
    # per-variant attributes are variant metafields; the product keeps what all share
    lead_variant = product["variants"][0]["id"]
    assert fake.metafields[lead_variant]["frame_color"] == "Gold"
    assert fake.metafields[lead_variant]["gtin"] == db["products"].find_one({"product_id": pids[0]})["attributes"]["gtin"]
    assert "frame_color" not in fake.metafields.get(product["id"], {})
    assert fake.metafields[product["id"]]["model_no"] == "RB3025"
    # tags: the union -- a colour filter finds every colour
    assert {"framecolor_gold", "framecolor_black", "framecolor_silver", "modelno_rb3025"} <= set(product["tags"])
    assert res.listing["options"] == [{"name": "Color", "values": ["Gold (001)", "Black (002/58)", "Silver (003/32)"]}]


def test_size_only_model_first_publish_is_chosen_by_size(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001", "55"), ("Gold", "001", "58"), ("Gold", "001", "62")])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    product = _only_product(fake)
    assert product["options"] == [{"name": "Size", "values": ["55", "58", "62"]}]
    _assert_every_variant_is_its_item(db, fake, product, pids)


def test_a_15_variant_rb3025_publishes_as_one_listing_with_colour_and_size(monkeypatch):
    db = _world()
    items = [(c, code, size) for c, code in COLOURS for size in ("55", "58", "62")]
    pids = _family(db, items)
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    product = _only_product(fake)
    assert [o["name"] for o in product["options"]] == ["Color", "Size"]
    assert product["options"][0]["values"] == [f"{c} ({code})" for c, code in COLOURS]
    assert product["options"][1]["values"] == ["55", "58", "62"]
    combos = {(v["opts"]["Color"], v["opts"]["Size"]) for v in product["variants"]}
    assert len(combos) == 15 == len(product["variants"])
    _assert_every_variant_is_its_item(db, fake, product, pids)
    assert fake.ops("imsProductCreate").__len__() == 1


# ---------------------------------------------------------------------------
# 2. a LIVE one-variant listing gains two colours
# ---------------------------------------------------------------------------


def _live_lead(db, fake, *, units=(2, 1)):
    """The lead is on the website as ONE 'Default Title' variant (the 25 live
    listings), its own row carrying that variant; then two more colours of
    its model are catalogued and linked (G1: their rows stay their own)."""
    lead = _item(db, 1, "Gold", "001", units=units)
    p = fake.new_product(handle="ray-ban-rb3025-gold-live")
    var = p["variants"][0]
    var.update({"sku": db["products"].find_one({"product_id": lead})["sku"], "price": "12990.00"})
    twin = _twin(db, lead)
    db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {
        "ecom.shopify_product_id": p["id"], "ecom.shopify_variant_id": var["id"], "ecom.shopify_inventory_item_id": var["inv"],
        "ecom.status": "PUBLISHED", "ecom.locally_modified": False, "ecom.handle": "ray-ban-rb3025-gold-live",
        "ecom.confirmed_sizes": {var["id"]: "DENY"}}})
    db["catalog_variants"].update_one({"sku": twin["sku"]}, {"$set": {"shopify_variant_id": var["id"], "shopify_inventory_item_id": var["inv"]}})
    black = _item(db, 2, "Black", "002/58", units=(1, 1))
    silver = _item(db, 3, "Silver", "003/32", units=(0, 3))
    for pid in (black, silver):
        out = pm.link_to_family(db, pid, lead, actor="u-admin")
        assert out["on_listing"] is False, "G1: under a live lead the row waits for the website step"
    return [lead, black, silver], p, var


def test_a_live_one_variant_listing_gains_two_colours_and_keeps_its_variant(monkeypatch):
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is True, "linking a colour queued the model's listing"
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok and res.action == "update", (res.error, res.code)
    assert fake.ops("imsProductCreate") == [], "the live listing is updated, never re-created"
    (opt,) = fake.ops("imsProductOptionsCreate")
    assert opt["options"] == [{"name": "Color", "values": [{"name": "Gold (001)"}]}], "the lead's colour first"
    product = _only_product(fake)
    assert product["variants"][0]["id"] == var["id"], "the lead's variant keeps its id"
    assert product["variants"][0]["opts"] == {"Color": "Gold (001)"}
    assert fake.deleted_variants == []
    (create,) = fake.ops("imsVariantsBulkCreate")
    assert create["strategy"] == "PRESERVE_STANDALONE_VARIANT" and len(create["variants"]) == 2
    assert product["options"] == [{"name": "Color", "values": ["Gold (001)", "Black (002/58)", "Silver (003/32)"]}]
    _assert_every_variant_is_its_item(db, fake, product, pids)
    assert fake.quantities[(var["inv"], LOC1)] == 2 and fake.quantities[(var["inv"], LOC2)] == 1
    silver = next(v for v in product["variants"] if v["opts"]["Color"] == "Silver (003/32)")
    assert (fake.quantities[(silver["inv"], LOC1)], fake.quantities[(silver["inv"], LOC2)]) == (0, 3)
    sent = fake.ops("imsProductUpdate")[0]["input"]
    assert "handle" not in sent and product["handle"] == "ray-ban-rb3025-gold-live", "no URL ever breaks"
    assert sent["title"] == "Ray-Ban RB3025 Aviator Large Metal"
    assert len({v["inv"] for v in product["variants"]}) == 3, "no shared inventory item"

    # a second press adds nothing: no option, no variant, no duplicate
    before = len(fake.calls)
    res2 = _push(db, pids[0])
    assert res2.ok, (res2.error, res2.code)
    names = [n for n, _v in fake.calls[before:]]
    assert "imsProductOptionsCreate" not in names and "imsVariantsBulkCreate" not in names
    assert len(_only_product(fake)["variants"]) == 3


def test_a_new_colour_never_takes_the_live_leads_variant(monkeypatch):
    """The lone-pair rule (one row left, one variant left -> pair them) used
    to hand a colour joining a live one-variant listing the LEAD's variant:
    its SKU, price and stock target. A variant another row owns is never
    anyone else's."""
    db = _world()
    fake = FakeShopify()
    lead = _item(db, 1, "Gold", "001")
    p = fake.new_product(handle="rb3025-gold")
    var = p["variants"][0]
    twin = _twin(db, lead)
    db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {
        "ecom.shopify_product_id": p["id"], "ecom.shopify_variant_id": var["id"],
        "ecom.shopify_inventory_item_id": var["inv"], "ecom.status": "PUBLISHED"}})
    black = _item(db, 2, "Black", "002/58")
    pm.link_to_family(db, black, lead, actor="u-admin")
    _live(monkeypatch, fake)
    res = _push(db, lead)
    assert res.ok, (res.error, res.code)
    product = _only_product(fake)
    assert len(product["variants"]) == 2
    lead_v = next(v for v in product["variants"] if v["id"] == var["id"])
    assert lead_v["opts"] == {"Color": "Gold (001)"} and lead_v["sku"] in (None, twin["sku"])
    assert _row(db, black)["shopify_variant_id"] != var["id"]
    assert _row(db, lead)["shopify_variant_id"] == var["id"], "the lead's row got its own ids"


def test_assign_never_pairs_a_claimed_or_foreign_variant():
    product = {"id": "P", "sku": "L", "offer_price": 100.0}
    nodes = [{"id": "gid://shopify/ProductVariant/1", "selectedOptions": [{"name": "Title", "value": "Default Title"}]}]
    plain = seeding.build_variant_seed_rows(product, [{"sku": "C", "option_color": "Black (002)", "mrp": 100.0}])
    joining = seeding.build_variant_seed_rows(
        product, [{"sku": "C", "option_color": "Black (002)", "mrp": 100.0, model.PENDING: True}]
    )
    # control: one row, one foreign-looking node -> the old 1:1 trust holds
    _u, create, _cv, pairs, _s = seeding._assign_seed_rows(plain, nodes, set())
    assert len(pairs) == 1 and create == []
    # the node is the lead's (another row owns it): claimed -> created, never paired
    _u, create, _cv, pairs, _s = seeding._assign_seed_rows(plain, nodes, {"gid://shopify/ProductVariant/1"})
    assert pairs == [] and len(create) == 1
    # a colour JOINING the listing is never handed a variant that is not its own
    _u, create, _cv, pairs, _s = seeding._assign_seed_rows(joining, nodes, set())
    assert pairs == [] and len(create) == 1
    # _claimed_gids: every other row's gid, and the listing's default unless a seed row is the product's own
    rows = [{"sku": "L", "shopify_variant_id": "gid://shopify/ProductVariant/1"}, {"sku": "C", "option_color": "x", "mrp": 1.0}]
    seed = seeding.build_repair_seed_rows(product, rows)
    assert seeding._claimed_gids(product, rows, seed) == {"gid://shopify/ProductVariant/1"}


def test_an_unreadable_model_is_refused_before_anything_is_sent(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    twin = _twin(db, pids[0])
    rows = ls.variants_for_product(db, twin)
    real = db["products"].find

    def _dead(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(db["products"], "find", _dead)
    res = _run(shopify_push.push_product(db, twin, rows, blocked=False))
    monkeypatch.setattr(db["products"], "find", real)
    assert res.ok is False and res.code == "FAMILY_UNREADABLE" and fake.calls == []


# ---------------------------------------------------------------------------
# 3. the lead archived -> the listing moves to the next colour
# ---------------------------------------------------------------------------


def test_archived_lead_hands_the_listing_over_without_a_new_product(monkeypatch):
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    lead_twin = _twin(db, pids[0])
    # archive the lead (what the archive door writes), then the hand-over
    db["products"].update_one({"product_id": pids[0]}, {"$set": {"deleted_at": "2026-10-19T00:00:00+00:00", "is_active": False}})
    assert pm.promote_new_lead(db, pids[0], actor="u-admin") == pids[1]
    new_twin, old_twin = _twin(db, pids[1]), _twin(db, pids[0])
    assert new_twin["ecom"]["shopify_product_id"] == p["id"] and new_twin["ecom"]["handle"] == lead_twin["ecom"]["handle"]
    assert new_twin["ecom"]["media_map"] == lead_twin["ecom"]["media_map"]
    assert "shopify_product_id" not in old_twin["ecom"] and old_twin["ecom"]["variant_of"]["twin_id"] == new_twin["id"]
    for pid in pids:
        assert _row(db, pid)["parent_product_id"] == new_twin["id"]
    assert _row(db, pids[0])["shopify_variant_id"] == var["id"], "the old lead's variant stays on the listing"
    # the archived lead's own variant comes off sale (DENY + 0), never deleted
    out = _run(online_delist.delist_if_live(db, old_twin, reason="deleted", actor={"user_id": "u-admin"}))
    assert out and out["ok"], out
    assert fake.quantities[(var["inv"], LOC1)] == 0 and fake.quantities[(var["inv"], LOC2)] == 0
    # the new lead pushes the SAME listing: no product, no variant created
    before = len(fake.calls)
    res = _push(db, pids[1])
    assert res.ok and res.action == "update", (res.error, res.code)
    names = [n for n, _v in fake.calls[before:]]
    assert "imsProductCreate" not in names and "imsVariantsBulkCreate" not in names
    product = _only_product(fake)
    assert len(product["variants"]) == 3 and fake.deleted_variants == []
    assert product["handle"] == lead_twin["ecom"]["handle"]


# ---------------------------------------------------------------------------
# 4. the duplicate-listing runbook
# ---------------------------------------------------------------------------


def _script():
    spec = importlib.util.spec_from_file_location("merge_duplicate_model_listings", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _two_live_listings(db, fake):
    """Two colours of one model, each on the website as its own listing
    (Carrera CARDUC 038/S on prod): not linked -- G1 refuses that."""
    keep, other = _item(db, 1, "Gold", "001"), _item(db, 2, "Black", "002/58")
    out = []
    for pid, handle in ((keep, "carrera-gold"), (other, "carrera-black")):
        p = fake.new_product(handle=handle)
        v = p["variants"][0]
        v["sku"] = db["products"].find_one({"product_id": pid})["sku"]
        twin = _twin(db, pid)
        db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {
            "ecom.shopify_product_id": p["id"], "ecom.shopify_variant_id": v["id"], "ecom.shopify_inventory_item_id": v["inv"],
            "ecom.status": "PUBLISHED", "ecom.handle": handle, "ecom.locally_modified": False}})
        db["catalog_variants"].update_one({"sku": twin["sku"]}, {"$set": {"shopify_variant_id": v["id"], "shopify_inventory_item_id": v["inv"]}})
        out.append((pid, p))
    with pytest.raises(pm.ProductMasterError):
        pm.link_to_family(db, other, keep, actor="u-admin")
    return out


def test_duplicate_listing_runbook_dry_run_sends_and_writes_nothing(monkeypatch):
    db = _world()
    fake = FakeShopify()
    (keep, kp), (other, op) = _two_live_listings(db, fake)
    _dark(monkeypatch)
    snapshot = {c: copy.deepcopy(list(db[c].find({}))) for c in ("products", "catalog_products", "catalog_variants")}
    mod = _script()
    report = mod.plan(db)
    assert len(report["merges"]) == 1
    merge = report["merges"][0]
    assert merge["keep"]["product_id"] == keep and merge["keep"]["shopify_product_id"] == kp["id"]
    assert [f["product_id"] for f in merge["fold"]] == [other]
    mod.print_report(report, "DRY-RUN")
    assert {c: list(db[c].find({})) for c in snapshot} == snapshot, "a dry-run writes nothing"
    with pytest.raises(SystemExit):
        _run(mod.apply(db, report))  # dark: refused before anything


def test_duplicate_listing_runbook_apply_folds_the_second_listing_in(monkeypatch):
    db = _world()
    fake = FakeShopify()
    (keep, kp), (other, op) = _two_live_listings(db, fake)
    _live(monkeypatch, fake)
    mod = _script()
    out = _run(mod.apply(db, mod.plan(db)))
    assert out["stopped"] == [] and out["merged"][0]["sku"] == db["products"].find_one({"product_id": other})["sku"]
    assert op["status"] == "DRAFT" and op["id"] in fake.products, "taken down, never deleted"
    assert fake.redirects == [{"path": "/products/carrera-black", "target": "/products/carrera-gold"}]
    other_twin = _twin(db, other)
    assert "shopify_product_id" not in other_twin["ecom"] and other_twin["ecom"]["former_listing"]["shopify_product_id"] == op["id"]
    assert db["products"].find_one({"product_id": other})["variant_of"] == keep
    # the kept listing's next push adds the colour
    res = _push(db, keep)
    assert res.ok, (res.error, res.code)
    assert len(kp["variants"]) == 2 and {v["opts"]["Color"] for v in kp["variants"]} == {"Gold (001)", "Black (002/58)"}
    assert _row(db, other)["shopify_variant_id"] in {v["id"] for v in kp["variants"]}
    assert _row(db, other)["shopify_variant_id"] != op["variants"][0]["id"], "never the drafted listing's variant"


# ---------------------------------------------------------------------------
# 5. the model's words: no single colour
# ---------------------------------------------------------------------------


def test_title_seo_and_description_are_the_models_never_one_colour(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001", "58", {"lens": "Green"}), ("Black", "002/58", "58", {"lens": "Grey"}),
                        ("Havana", "003/32", "58", {"lens": "Brown"})])
    twin = _twin(db, pids[0])
    lst = model.model_listing(db, twin, ls.variants_for_product(db, twin))
    colours = ["Gold", "Black", "Havana", "Green", "Grey", "Brown", "001", "002/58", "003/32"]
    for text in (lst["title"], lst["seo_title"], lst["seo_description"]):
        assert not names_a_colour(text, colours), text
        assert "58" not in text, "no size either"
    assert lst["title"] == "Ray-Ban RB3025 Aviator Large Metal"
    assert lst["seo_description"].startswith("Buy Ray-Ban RB3025 Aviator Large Metal") and "3 colours" in lst["seo_description"]
    html = lst["description_html"]
    head = html.split("<h4>Technical Specifications</h4>")[0]
    assert not names_a_colour(head, colours), "the model line and paragraph name no colour"
    assert "Model Number: Ray-Ban RB3025 Aviator Large Metal</h5>" in html, "no category appended (audit 2026-10-22)"
    assert "Available Colours" in html and "Gold (001), Black (002/58), Havana (003/32)" in html
    assert "<h4>Colours and Sizes</h4>" in html and "Lens Colour" in html.split("<h4>Colours and Sizes</h4>")[1]
    assert "Frame Material</td>\n<td>Metal" in html, "what every colour shares stays in the shared body"
    assert "805289" not in html, "a barcode is per colour, never in the body"
    _dark(monkeypatch)
    res = _push(db, pids[0])
    assert res.mode == "SIMULATED" and res.payload["title"] == lst["title"]
    assert res.payload["seo"]["title"] == lst["seo_title"]
    assert [o["name"] for o in res.payload["productOptions"]] == ["Color"]
    assert [v["sku"] for v in res.listing["variants"]] == [db["products"].find_one({"product_id": p})["sku"] for p in pids]


def test_ai_model_paragraph_is_thrown_away_when_it_names_a_colour(monkeypatch):
    from agents import claude_client
    from api.services import product_description as pdesc

    monkeypatch.setattr(claude_client, "is_claude_available", lambda: True)

    async def _named(system, user, **kw):  # noqa: ARG001
        assert "Gold" not in user and "Black" not in user, "no colour name is sent"
        return "Reflect your style with the Ray-Ban RB3025 in classic Gold."

    monkeypatch.setattr(claude_client, "call_claude", _named)
    attrs = {"brand_name": "Ray-Ban", "model_no": "RB3025", "model_name": "Aviator", "frame_color": "Gold"}
    assert _run(pdesc.generate_model_paragraph("Sunglass", attrs, ["Gold (001)", "Black (002)"], [])) is None

    async def _clean(system, user, **kw):  # noqa: ARG001
        return "Reflect your style with the Ray-Ban RB3025 Aviator, in 2 colours."

    monkeypatch.setattr(claude_client, "call_claude", _clean)
    assert _run(pdesc.generate_model_paragraph("Sunglass", attrs, ["Gold (001)", "Black (002)"], [])).endswith("2 colours.")


# ---------------------------------------------------------------------------
# 6. refused before anything is sent
# ---------------------------------------------------------------------------


def test_an_unpriced_variant_blocks_the_press_before_any_write(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58", "58", {"mrp": 0.0, "offer": 0.0})])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok is False and res.code == "UNPRICED_SIZE", (res.error, res.code)
    assert db["products"].find_one({"product_id": pids[1]})["sku"] in res.error
    assert fake.writes() == [] and fake.products == {}


def test_two_variants_that_look_the_same_are_refused_unless_the_lens_tells_them_apart(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001", "58"), ("Gold", "001", "58")])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok is False and res.code == "OPTION_CLASH" and fake.writes() == []

    db2 = _world()
    pids2 = _family(db2, [("Gold", "001", "58", {"lens": "Green", "polarized": True}), ("Gold", "001", "58", {"lens": "Grey"})])
    res2 = _push(db2, pids2[0])
    assert res2.ok, (res2.error, res2.code)
    values = _only_product(fake)["options"][0]["values"]
    assert values == ["Gold (001) / Green Polarized", "Gold (001) / Grey"]


@pytest.mark.parametrize("third_complete", [False, True])
def test_first_publish_skips_missing_sibling_and_its_fix_queues_the_page(monkeypatch, third_complete):
    db = _world()
    pids = _family(db, [("Gold", "001", "55"), ("Gold", "001", "58")])
    db["products"].update_one({"product_id": pids[1]}, {"$unset": {"attributes.lens_size": ""}})
    if third_complete:
        pids.append(_item(db, 3, "Gold", "001", "62"))
        pm.link_to_family(db, pids[2], pids[0], actor="u-admin")
    complete = [pid for pid in pids if pid != pids[1]]
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok is True, res.error
    missing_sku = db["products"].find_one({"product_id": pids[1]})["sku"]
    assert res.code == "OPTION_MISSING" and missing_sku in res.error
    assert res.listing["held"] == [{"sku": missing_sku, "code": "OPTION_MISSING", "reason": "no lens size",
                                    "missing_option": "lens size"}]
    assert missing_sku not in {v["sku"] for v in res.listing["variants"]}, "the plan never shows it as sent"
    page = _only_product(fake)
    _assert_every_variant_is_its_item(db, fake, page, complete)
    assert not _row(db, pids[1]).get("shopify_variant_id")
    # Review of #1276: never re-queued for the sibling (no loop every run) --
    # the edit that fills its size is what queues the page again.
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is False
    original_ids = {pid: _row(db, pid)["shopify_variant_id"] for pid in complete}

    # An unchanged press never duplicates the variants already on the website.
    again = _push(db, pids[0])
    assert again.ok and again.code == "OPTION_MISSING"
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is False
    _assert_every_variant_is_its_item(db, fake, _only_product(fake), complete)

    # The fix goes through the product edit door, which queues the model's listing.
    spine = db["products"].find_one({"product_id": pids[1]})
    attrs = {**spine["attributes"], "lens_size": "58"}
    db["products"].update_one({"product_id": pids[1]}, {"$set": {"attributes": attrs}})
    pm.mirror_update_to_catalog_twin(product_id=pids[1], current=spine, patch={"attributes": attrs}, db=db)
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is True, "filling the size queued the page"
    fixed = _push(db, pids[0])
    assert fixed.ok and not fixed.listing["held"], fixed.error
    _assert_every_variant_is_its_item(db, fake, _only_product(fake), pids)
    assert {pid: _row(db, pid)["shopify_variant_id"] for pid in original_ids} == original_ids
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is False


def test_first_publish_drops_the_omitted_siblings_row_so_its_missing_price_cannot_refuse(monkeypatch):
    """Review of #1276: the omitted sibling's ROW leaves the plan too -- kept,
    an unpriced omitted sibling would turn the whole press into UNPRICED_SIZE."""
    db = _world()
    pids = _family(db, [("Gold", "001", "55"), ("Gold", "001", "58")])
    db["products"].update_one({"product_id": pids[1]}, {"$unset": {"attributes.lens_size": ""},
                                                       "$set": {"mrp": 0.0, "offer_price": 0.0}})
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    missing_sku = _sku(db, pids[1])
    assert res.ok and res.code == "OPTION_MISSING", (res.error, res.code)
    assert [h["sku"] for h in res.listing["held"]] == [missing_sku]
    assert [v["sku"] for v in res.listing["variants"]] == [_sku(db, pids[0])]
    _assert_every_variant_is_its_item(db, fake, _only_product(fake), [pids[0]])


def test_a_live_lead_with_no_size_and_a_new_sized_colour_is_not_requeued_every_run(monkeypatch):
    """Review of #1276: the seeding step's own OPTION_MISSING hold ('no new
    colour or size was added: there is no size on <lead>') is a data hold,
    never a press re-queued every run (review of #1233)."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is False
    db["products"].update_one({"product_id": pids[0]}, {"$unset": {"attributes.lens_size": ""}})
    bronze = _item(db, 4, "Bronze", "9196/31", "62")
    pm.link_to_family(db, bronze, pids[0], actor="u-admin")
    res = _push(db, pids[0])
    assert res.ok and res.code == "OPTION_MISSING", (res.error, res.code)
    assert res.listing["held"] == [] and res.variants_seeded["held_data"] is True
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is False, "re-queued every run"


def test_first_publish_still_refuses_a_lead_missing_the_required_size(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001", "55"), ("Gold", "001", "58")])
    db["products"].update_one({"product_id": pids[0]}, {"$unset": {"attributes.lens_size": ""}})
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok is False and res.code == "OPTION_MISSING"
    assert fake.writes() == []
    assert not _row(db, pids[0]).get("shopify_variant_id")


# ---------------------------------------------------------------------------
# 7. more than 100 variants, never truncated
# ---------------------------------------------------------------------------


def test_variants_past_the_first_100_are_read_and_never_created_twice(monkeypatch):
    """A live listing with 102 variants: the colour whose variant is number
    102 (on page 2) is found there and adopted -- never minted a second time."""
    db = _world()
    fake = FakeShopify()
    lead = _item(db, 1, "Gold", "001")
    black = _item(db, 2, "Black", "002/58")
    black_sku = db["products"].find_one({"product_id": black})["sku"]
    others = [{"opts": {"Color": f"Other {i}"}} for i in range(100)]
    p = fake.new_product(handle="rb3025", options=[{"name": "Color", "values": ["Gold (001)"] + [f"Other {i}" for i in range(100)] + ["Black (002/58)"]}],
                         variants=[{"opts": {"Color": "Gold (001)"}}] + others + [{"opts": {"Color": "Black (002/58)"}, "sku": black_sku}])
    twin = _twin(db, lead)
    lead_v = p["variants"][0]
    db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {
        "ecom.shopify_product_id": p["id"], "ecom.shopify_variant_id": lead_v["id"],
        "ecom.shopify_inventory_item_id": lead_v["inv"], "ecom.status": "PUBLISHED"}})
    pm.link_to_family(db, black, lead, actor="u-admin")
    _live(monkeypatch, fake)
    res = _push(db, lead)
    assert fake.ops("imsProductVariantsPage"), "page 2 was read"
    assert fake.ops("imsVariantsBulkCreate") == [], "the colour on page 2 is never created again"
    assert len(p["variants"]) == 102
    assert _row(db, black)["shopify_variant_id"] == p["variants"][101]["id"]
    assert res.variants_seeded["updated"] == 1


def test_an_unread_page_holds_every_create(monkeypatch):
    fake = FakeShopify()
    p = fake.new_product(handle="x", options=[{"name": "Color", "values": [f"C{i}" for i in range(150)]}],
                         variants=[{"opts": {"Color": f"C{i}"}} for i in range(150)])

    async def _fails_on_page_2(db, query, variables):
        if "imsProductVariantsPage" in query:
            raise RuntimeError("timeout")
        return await fake(db, query, variables)

    first = fake._node(p)["variants"]
    monkeypatch.setattr(seeding, "_graphql", _fails_on_page_2)
    nodes, complete = _run(seeding.all_variant_nodes(None, p["id"], first))
    assert (len(nodes), complete) == (100, False)
    monkeypatch.setattr(seeding, "_graphql", fake)
    nodes, complete = _run(seeding.all_variant_nodes(None, p["id"], first))
    assert (len(nodes), complete) == (150, True)
    summary = _run(seeding._seed_variants_after_write(
        None, {"id": "P", "sku": "L", "offer_price": 10.0},
        [{"sku": "L", "shopify_variant_id": p["variants"][0]["id"], "option_color": "C0"},
         {"sku": "N", "option_color": "New", "mrp": 10.0, model.PENDING: True}],
        p["id"], nodes[:100], repair_only=True, listing_options=None, nodes_complete=False,
    ))
    assert summary["created"] == 0 and fake.ops("imsVariantsBulkCreate") == []
    assert "could not all be read" in summary["held"]


# ---------------------------------------------------------------------------
# 8. queueing, prices, photos still processing
# ---------------------------------------------------------------------------


def test_a_colour_added_or_edited_queues_its_models_listing():
    db = _world()
    lead = _item(db, 1, "Gold", "001")
    db["catalog_products"].update_one({"id": "tw-1"}, {"$set": {"ecom.locally_modified": False, "ecom.edit_rev": "r0"}})
    black = _item(db, 2, "Black", "002/58")
    pm.link_to_family(db, black, lead, actor="u-admin")
    ecom = _twin(db, lead)["ecom"]
    assert ecom["locally_modified"] is True and ecom["edit_rev"] != "r0"
    db["catalog_products"].update_one({"id": "tw-1"}, {"$set": {"ecom.locally_modified": False, "ecom.edit_rev": "r1"}})
    spine = db["products"].find_one({"product_id": black})
    pm.mirror_update_to_catalog_twin(product_id=black, current=spine, patch={"mrp": 14990.0}, db=db)
    ecom = _twin(db, lead)["ecom"]
    assert ecom["locally_modified"] is True and ecom["edit_rev"] != "r1"
    assert _twin(db, black)["ecom"].get("locally_modified") is False, "the colour itself owns no listing"


def test_a_lead_price_edit_in_the_same_press_as_a_new_colour_reaches_the_website(monkeypatch):
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    db["catalog_products"].update_one({"id": _twin(db, pids[0])["id"]}, {"$set": {"offer_price": 11990.0}})
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    assert var["price"] == "11990.00" and var["compareAtPrice"] == "12990.00"


def test_a_colour_photo_still_processing_is_attached_on_the_next_run(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    fake = FakeShopify()
    fake.media_ready = False
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok and res.code == "PHOTOS_NOT_SYNCED", (res.error, res.code)
    assert "Photos still attaching" in res.error
    assert "still being processed" in res.photos["variant_error"]
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is True, "re-queued for the next run"
    product = _only_product(fake)
    assert all(v["media"] is None for v in product["variants"])
    for m in product["media"]:
        m["status"] = "READY"
    res2 = _push(db, pids[0])
    assert res2.ok and res2.code is None, (res2.error, res2.code)
    _assert_every_variant_is_its_item(db, fake, product, pids)


def test_model_paragraph_runbook_writes_the_ai_paragraph_the_listing_then_carries(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    spec = importlib.util.spec_from_file_location(
        "write_model_paragraphs", os.path.join(os.path.dirname(_SCRIPT), "write_model_paragraphs.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    models = mod.plan(db)
    assert [(m["lead_sku"], len(m["colours"]), m["has_paragraph"]) for m in models] == [
        (db["products"].find_one({"product_id": pids[0]})["sku"], 2, False)
    ]

    async def _para(category, attrs, colours, sizes, max_length=350):  # noqa: ARG001
        assert colours and "frame_color" in attrs  # the generator itself drops colour names
        return "Reflect your style with the Ray-Ban RB3025 Aviator Large Metal, in 2 colours."

    monkeypatch.setattr(mod, "generate_model_paragraph", _para)
    db["catalog_products"].update_one({"id": "tw-1"}, {"$set": {"ecom.locally_modified": False}})
    out = _run(mod.apply(db, models))
    assert out["written"] == [models[0]["lead_sku"]]
    ecom = _twin(db, pids[0])["ecom"]
    assert ecom["model_paragraph"].endswith("in 2 colours.") and ecom["locally_modified"] is True
    twin = _twin(db, pids[0])
    lst = model.model_listing(db, twin, ls.variants_for_product(db, twin))
    assert "<p>Reflect your style with the Ray-Ban RB3025 Aviator Large Metal, in 2 colours.</p>" in lst["description_html"]


def test_a_colour_whose_twin_lost_its_half_of_the_link_never_gets_a_listing(monkeypatch):
    """The spine says 'colour of model X' but the twin's ecom.variant_of is
    missing (a link write that died half-way): it is still never pushed as a
    listing of its own -- that would be a second listing for the model."""
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    twin = _twin(db, pids[1])
    db["catalog_products"].update_one({"id": twin["id"]}, {"$unset": {"ecom.variant_of": ""}})
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[1])
    assert res.ok is False and res.reason == "variant_of" and fake.calls == []


# ---------------------------------------------------------------------------
# 9. review of #1233 (BLOCK, owner decisions 19 Oct 2026)
# ---------------------------------------------------------------------------


def _price_edit(db, pid, offer):
    """A lead price edit waiting in the same press (the decisions' control)."""
    twin = _twin(db, pid)
    db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {"offer_price": offer}})
    db["products"].update_one({"product_id": pid}, {"$set": {"offer_price": offer}})


def test_unfinished_colours_wait_and_only_finished_ones_join_a_live_listing(monkeypatch):
    """Decision 1: a linked colour not yet on the listing joins only when a
    person has finished it -- active, not archived, not a PO draft, not a
    catalogue DRAFT, not in Needs review. Colours already on it keep syncing."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)  # Black + Silver finished, linked
    po = _item(db, 4, "Bronze", "9196/31")
    db["products"].update_one({"product_id": po}, {"$set": {"provisional": True, "catalog_status": "DRAFT"}})
    off = _item(db, 5, "Blue", "005/71")
    db["products"].update_one({"product_id": off}, {"$set": {"is_active": False}})
    draft = _item(db, 6, "Green", "006/71")
    db["products"].update_one({"product_id": draft}, {"$set": {"catalog_status": "DRAFT"}})
    review = _item(db, 7, "Pink", "007/71")
    db["catalog_products"].update_one({"id": "tw-7"}, {"$set": {"needs_review": True}})
    for pid in (po, off, draft, review):
        pm.link_to_family(db, pid, pids[0], actor="u-admin")
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    skus = lambda ids: {db["products"].find_one({"product_id": i})["sku"] for i in ids}  # noqa: E731
    assert {v["sku"] for v in p["variants"]} == skus(pids), "only the finished colours went on"
    waiting = {w["sku"]: w["reason"] for w in res.listing["waiting"]}
    assert set(waiting) == skus([po, off, draft, review])
    assert "purchase-order draft" in waiting[db["products"].find_one({"product_id": po})["sku"]]
    assert "Needs review" in waiting[db["products"].find_one({"product_id": review})["sku"]]
    # a colour ALREADY on the listing keeps syncing when it is switched off
    db["products"].update_one({"product_id": pids[1]}, {"$set": {"is_active": False}})
    black = next(v for v in p["variants"] if v["sku"] == db["products"].find_one({"product_id": pids[1]})["sku"])
    fake.quantities.clear()
    assert _push(db, pids[0]).ok
    assert fake.quantities[(black["inv"], LOC1)] == 0 and fake.quantities[(black["inv"], LOC2)] == 0
    assert any(v["sku"] == black["sku"] for v in p["variants"]), "never deleted"


def test_one_price_rule_for_every_variant_the_lead_too(monkeypatch):
    """Decision 2 (no engine rule): its own offer when <= its MRP, the MRP
    struck through -- the lead exactly as every colour."""
    db = _world()
    pids = _family(db, [("Gold", "001", "58", {"offer": 11990.0}), ("Black", "002/58", "58", {"offer": 10990.0})])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    by_sku = {v["sku"]: v for v in _only_product(fake)["variants"]}
    gold, black = (by_sku[db["products"].find_one({"product_id": p})["sku"]] for p in pids)
    assert (gold["price"], gold["compareAtPrice"]) == ("11990.00", "12990.00")
    assert (black["price"], black["compareAtPrice"]) == ("10990.00", "12990.00")


def test_the_engine_price_wins_for_every_variant_and_a_new_colour_gets_it_too(monkeypatch):
    """Decision 2 with an online discount rule, and (decision 7) the NEW
    colour joining a live listing is priced by the engine before it exists."""
    from api.services.online_discount_engine import recompute_online_price

    db = _world()
    db["ecom_discount_rules"].insert_one({"id": "r10", "category": "SUNGLASS", "brand": "Ray-Ban",
                                          "discount_percentage": 10, "active": True})
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _price_edit(db, pids[0], 11990.0)
    recompute_online_price(_twin(db, pids[0]), db=db)  # the engine's own pass over what is on the listing
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    for v in p["variants"]:
        assert (v["price"], v["compareAtPrice"]) == ("11691.00", "12990.00"), v["sku"]
    black = _row(db, pids[1])
    assert black["discounted_price"] == 11691.0, "the new colour's engine price is stored with its row"


def test_a_live_listing_holds_back_only_an_unpriced_colour(monkeypatch):
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    db["products"].update_one({"product_id": pids[2]}, {"$set": {"mrp": 0.0, "offer_price": 0.0}})
    _price_edit(db, pids[0], 11990.0)
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    silver_sku = db["products"].find_one({"product_id": pids[2]})["sku"]
    assert res.ok and res.code == "UNPRICED_SIZE", (res.error, res.code)
    assert silver_sku in res.error and "everything else was sent" in res.error
    assert {v["sku"] for v in p["variants"]} == {db["products"].find_one({"product_id": i})["sku"] for i in pids[:2]}
    assert var["price"] == "11990.00", "the lead's price edit went out in the same press"
    assert res.listing["held"] == [{"sku": silver_sku, "code": "UNPRICED_SIZE", "reason": "no MRP"}]


@pytest.mark.parametrize("photos_pending", [False, True])
def test_a_live_listing_holds_back_only_a_colour_with_no_size(monkeypatch, photos_pending):
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    if photos_pending:
        fake.media_ready = False
        for media in p["media"]:
            media["status"] = "PROCESSING"
    db["products"].update_one({"product_id": pids[1]}, {"$set": {"attributes.lens_size": "62"}})
    db["products"].update_one({"product_id": pids[2]}, {"$unset": {"attributes.lens_size": ""}})
    _price_edit(db, pids[0], 11990.0)
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    silver_sku = db["products"].find_one({"product_id": pids[2]})["sku"]
    assert res.ok and res.code == "OPTION_MISSING", (res.error, res.code)
    assert silver_sku in res.error
    if photos_pending:
        assert res.photos["sync_state"] == "pending"
    assert res.listing["held"] == [{"sku": silver_sku, "code": "OPTION_MISSING", "reason": "no lens size",
                                    "missing_option": "lens size"}]
    # The held colour never re-queues the lead (#1276); still-attaching
    # photos do, on their own retry rule (#1277).
    assert _twin(db, pids[0])["ecom"]["locally_modified"] is photos_pending, "a held colour never loops the lead"
    assert [o["name"] for o in p["options"]] == ["Color", "Size"]
    assert var["opts"] == {"Color": "Gold (001)", "Size": "58"}, "the lead's variant took the lead's size"
    assert len(p["variants"]) == 2 and var["price"] == "11990.00"


def test_a_live_listing_holds_back_only_a_colour_that_looks_the_same(monkeypatch):
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    twin_gold = _item(db, 4, "Gold", "001")
    pm.link_to_family(db, twin_gold, pids[0], actor="u-admin")
    _price_edit(db, pids[0], 11990.0)
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    dup_sku = db["products"].find_one({"product_id": twin_gold})["sku"]
    assert res.ok and res.code == "OPTION_CLASH", (res.error, res.code)
    assert [h["sku"] for h in res.listing["held"]] == [dup_sku]
    assert len(p["variants"]) == 3 and var["price"] == "11990.00"


def test_a_colour_catalogued_again_takes_over_its_archived_twins_variant(monkeypatch):
    """Decision 3: an archived colour still on the listing never makes a
    permanent clash with the item catalogued again in its place -- the new
    one takes over the variant (never deleted, no second variant)."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    old_black = _row(db, pids[1])
    db["products"].update_one({"product_id": pids[1]}, {"$set": {"deleted_at": "2026-10-19T00:00:00+00:00", "is_active": False}})
    again = _item(db, 8, "Black", "002/58", units=(4, 0))
    pm.link_to_family(db, again, pids[0], actor="u-admin")
    _price_edit(db, pids[0], 11990.0)
    res = _push(db, pids[0])
    assert res.ok and res.code is None, (res.error, res.code)
    assert len(p["variants"]) == 3 and fake.ops("imsVariantsBulkCreate")[1:] == [], "no new variant"
    new_row = _row(db, again)
    assert new_row["shopify_variant_id"] == old_black["shopify_variant_id"]
    assert "shopify_variant_id" not in _row(db, pids[1]), "the archived row let go of it"
    taken = next(v for v in p["variants"] if v["id"] == old_black["shopify_variant_id"])
    assert taken["sku"] == db["products"].find_one({"product_id": again})["sku"]
    assert fake.quantities[(taken["inv"], LOC1)] == 4
    assert var["price"] == "11990.00"


def test_a_new_colour_never_becomes_the_leads_default_variant(monkeypatch):
    """Decision 4: adding Black never stores Black's variant as the lead's
    default; Gold's variant deleted in Shopify admin is re-created as GOLD,
    never mapped to Black's."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    assert _twin(db, pids[0])["ecom"]["shopify_variant_id"] == var["id"]
    black_gid = _row(db, pids[1])["shopify_variant_id"]
    p["variants"] = [v for v in p["variants"] if v["id"] != var["id"]]  # deleted in Shopify admin
    _push(db, pids[0])  # finds it gone and forgets it
    assert "shopify_variant_id" not in _row(db, pids[0])
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    gold = next(v for v in p["variants"] if v["opts"].get("Color") == "Gold (001)")
    assert gold["id"] not in (var["id"], black_gid)
    assert _row(db, pids[0])["shopify_variant_id"] == gold["id"] != black_gid
    assert _twin(db, pids[0])["ecom"]["shopify_variant_id"] == gold["id"]
    assert _row(db, pids[1])["shopify_variant_id"] == black_gid


def test_the_lead_never_adopts_a_stored_default_another_row_owns(monkeypatch):
    """Decision 4 (listing): damage from before the fix -- the lead's stored
    default IS Black's variant. The lead's row never takes it; it finds its
    own Gold variant instead."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    black_gid = _row(db, pids[1])["shopify_variant_id"]
    db["catalog_products"].update_one({"id": _twin(db, pids[0])["id"]}, {"$set": {"ecom.shopify_variant_id": black_gid}})
    db["catalog_variants"].update_one({"sku": _twin(db, pids[0])["sku"]}, {"$unset": {"shopify_variant_id": "", "shopify_inventory_item_id": ""}})
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    assert _row(db, pids[0])["shopify_variant_id"] == var["id"], "its own Gold variant, by its colour"
    assert _row(db, pids[1])["shopify_variant_id"] == black_gid
    assert len(p["variants"]) == 3


def test_an_archived_colour_is_never_created(monkeypatch):
    """Decision 7: the archived-colour guard -- a colour archived before it
    reached the listing is never created (first publish: its row already sits
    under the lead, G1; live: it waits)."""
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58"), ("Silver", "003/32")])
    db["products"].update_one({"product_id": pids[2]}, {"$set": {"deleted_at": "2026-10-19T00:00:00+00:00"}})
    fake = FakeShopify()
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    assert len(_only_product(fake)["variants"]) == 2
    assert [w["sku"] for w in res.listing["waiting"]] == [db["products"].find_one({"product_id": pids[2]})["sku"]]


def test_one_colours_details_leave_the_product_level_of_a_live_listing(monkeypatch):
    """Decision 7: the per-colour metafields an older push left on the
    PRODUCT (its frame colour, its barcode) are removed there -- they live on
    each variant now."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    fake.metafields[p["id"]] = {"frame_color": "Gold", "gtin": "8052890000013", "model_no": "RB3025"}
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    assert "frame_color" not in fake.metafields[p["id"]] and "gtin" not in fake.metafields[p["id"]]
    assert fake.metafields[p["id"]]["model_no"] == "RB3025"
    assert fake.metafields[var["id"]]["frame_color"] == "Gold"


def _photo_clock(monkeypatch):
    from api.services.shopify_push import product as product_mod

    clock = [datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(product_mod, "_now", lambda: clock[0])
    return clock


def test_pressing_again_while_shopify_is_still_processing_stays_amber(monkeypatch):
    # Review of #1277: the streak counts sync runs, not presses. Pressing
    # Send twice in a minute while Shopify is still processing the photos
    # must not turn the product red and claim two runs happened.
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    fake = FakeShopify()
    fake.media_ready = False
    _live(monkeypatch, fake)
    clock = _photo_clock(monkeypatch)
    for _press in range(3):
        clock[0] += timedelta(minutes=1)
        res = _push(db, pids[0])
        assert res.code == "PHOTOS_NOT_SYNCED", res.error
        assert res.photos["sync_state"] == "pending" and res.photos["consecutive_incomplete"] == 1
    clock[0] += timedelta(hours=8)
    res = _push(db, pids[0])
    assert res.code == "PHOTOS_SYNC_FAILED" and res.photos["consecutive_incomplete"] == 2


def test_a_colour_photo_shopify_refuses_is_red_at_once_with_shopifys_reason(monkeypatch):
    # Review of #1277: a variant refusal that is not NON_READY_MEDIA never
    # fixes itself -- red on the first press, with Shopify's reason on it.
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    fake = FakeShopify()
    fake.variant_refusal = "Media ZZZ-REASON is invalid"
    _live(monkeypatch, fake)
    res = _push(db, pids[0])
    assert res.ok and res.code == "PHOTOS_SYNC_FAILED", (res.error, res.code)
    assert res.photos["sync_state"] == "failed"
    assert "ZZZ-REASON" in res.error and "still attaching" not in res.error


def test_photo_retries_stop_after_six_runs(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58")])
    fake = FakeShopify()
    fake.media_ready = False
    _live(monkeypatch, fake)
    async def no_wait(*args, **kwargs):
        raise AssertionError("photo processing must not add per-product sleeps")

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    clock = _photo_clock(monkeypatch)
    for run in range(1, 7):
        clock[0] += timedelta(hours=8)  # one scheduled sync slot apart
        res = _push(db, pids[0])
        assert res.code == ("PHOTOS_NOT_SYNCED" if run == 1 else "PHOTOS_SYNC_FAILED"), (run, res.error)
        assert res.photos["sync_state"] == ("pending" if run == 1 else "failed")
        assert res.photos["consecutive_incomplete"] == run
        assert _twin(db, pids[0])["ecom"]["locally_modified"] is (run < 6), run
    assert "tried 6 times" in res.error
    assert "next sync retries" not in res.error
    for m in _only_product(fake)["media"]:
        m["status"] = "READY"
    res = _push(db, pids[0])
    assert res.code is None and _twin(db, pids[0])["ecom"]["photo_sync_tries"] == 0
    assert res.photos["sync_state"] == "synced"


def test_duplicate_listing_runbook_never_folds_a_different_model(monkeypatch):
    db = _world()
    fake = FakeShopify()
    (keep, _kp), (other, _op) = _two_live_listings(db, fake)
    db["products"].update_one({"product_id": other}, {"$set": {"family_opt_out": True}})
    _dark(monkeypatch)
    mod = _script()
    report = mod.plan(db)
    assert report["merges"] == []
    assert any("This is a different model" in left["reason"] for left in report["left"])
    mod.print_report(report, "DRY-RUN")
    assert db["products"].find_one({"product_id": other})["family_opt_out"] is True


def test_duplicate_listing_revert_warns_and_takes_the_added_colour_down(monkeypatch):
    db = _world()
    fake = FakeShopify()
    (keep, kp), (other, op) = _two_live_listings(db, fake)
    _live(monkeypatch, fake)
    mod = _script()
    out = _run(mod.apply(db, mod.plan(db)))
    assert _push(db, keep).ok
    added = next(v for v in kp["variants"] if v["opts"].get("Color") == "Black (002/58)")
    refused = _run(mod.revert(db))
    assert refused["refused"] and [a["sku"] for a in refused["added"]] == [out["merged"][0]["sku"]]
    assert db["products"].find_one({"product_id": other})["variant_of"] == keep, "nothing restored"
    done = _run(mod.revert(db, take_down_added=True))
    assert done.get("taken_down") == [out["merged"][0]["sku"]] and done["restored"] > 0, done.get("why")
    assert added["policy"] == "DENY" and added["id"] in {v["id"] for v in kp["variants"]}, "off sale, never deleted"
    assert (fake.quantities[(added["inv"], LOC1)], fake.quantities[(added["inv"], LOC2)]) == (0, 0)
    assert not db["products"].find_one({"product_id": other}).get("variant_of")
    assert _twin(db, other)["ecom"]["shopify_product_id"] == op["id"]


def test_a_colour_on_the_listing_that_lost_its_price_never_freezes_the_rest(monkeypatch):
    """Decision 3: a colour ALREADY on the live listing whose IMS price was
    removed keeps its last price there and is named; a new colour and the
    lead's price edit still go out in the same press."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    black = next(v for v in p["variants"] if v["sku"] == db["products"].find_one({"product_id": pids[1]})["sku"])
    db["products"].update_one({"product_id": pids[1]}, {"$set": {"mrp": 0.0, "offer_price": 0.0}})
    db["catalog_variants"].update_one({"sku": black["sku"]}, {"$set": {"mrp": 0.0}})
    bronze = _item(db, 4, "Bronze", "9196/31")
    pm.link_to_family(db, bronze, pids[0], actor="u-admin")
    _price_edit(db, pids[0], 11990.0)
    res = _push(db, pids[0])
    assert res.ok and res.code == "UNPRICED_SIZE", (res.error, res.code)
    assert black["sku"] in res.error and "keeps its last price" in res.error
    assert len(p["variants"]) == 4 and var["price"] == "11990.00"
    assert black["price"] == "12990.00", "its last price stands -- never 0.00"


# ---------------------------------------------------------------------------
# 10. re-review of #1233 (round 2)
# ---------------------------------------------------------------------------


def _sku(db, pid):
    return db["products"].find_one({"product_id": pid})["sku"]


def _live_single(db, fake, *, size="58"):
    """A lead live as ONE 'Default Title' variant, nothing linked yet."""
    lead = _item(db, 1, "Gold", "001", size)
    p = fake.new_product(handle="rb3025-gold-live")
    var = p["variants"][0]
    var.update({"sku": _sku(db, lead), "price": "12990.00"})
    twin = _twin(db, lead)
    db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {
        "ecom.shopify_product_id": p["id"], "ecom.shopify_variant_id": var["id"],
        "ecom.shopify_inventory_item_id": var["inv"], "ecom.status": "PUBLISHED",
        "ecom.locally_modified": False, "ecom.handle": "rb3025-gold-live",
        "ecom.confirmed_sizes": {var["id"]: "DENY"}}})
    db["catalog_variants"].update_one({"sku": twin["sku"]}, {"$set": {
        "shopify_variant_id": var["id"], "shopify_inventory_item_id": var["inv"]}})
    return lead, p, var


@pytest.mark.parametrize("finish", ["cost", "switch_on", "status", "catalog_door", "reactivate"])
def test_finishing_a_waiting_colour_queues_its_models_listing(monkeypatch, finish):
    """Round 2, item 1: ANY edit of a colour of a model queues the model's
    listing -- cost / HSN / GST, the catalogue status, the switch -- so the
    colour Add Product left as a draft reaches the website once finished."""
    db = _world()
    fake = FakeShopify()
    lead, p, var = _live_single(db, fake)
    black = _item(db, 2, "Black", "002/58")
    if finish in ("switch_on", "reactivate"):
        db["products"].update_one({"product_id": black}, {"$set": {"is_active": False}})
    else:
        db["products"].update_one({"product_id": black}, {"$set": {"catalog_status": "DRAFT"}})
    pm.link_to_family(db, black, lead, actor="u-admin")
    _live(monkeypatch, fake)
    res = _push(db, lead)
    assert res.ok and [w["sku"] for w in res.listing["waiting"]] == [_sku(db, black)]
    assert _twin(db, lead)["ecom"]["locally_modified"] is False
    spine = db["products"].find_one({"product_id": black})
    if finish == "reactivate":
        db["products"].update_one({"product_id": black}, {"$set": {"is_active": True}})
        _run(online_delist.on_active_flip(db, spine, was_active=False, now_active=True, actor={"user_id": "u"}))
    elif finish == "catalog_door":
        from api.routers import catalog as cat

        monkeypatch.setattr(cat, "_get_db", lambda: db)
        monkeypatch.setattr(cat, "_catalog_coll", lambda: db["catalog_products"])
        db["products"].update_one({"product_id": black}, {"$set": {"catalog_status": "ACTIVE"}})
        cat._save_catalog_product({**_twin(db, black), "cost_price": 5000.0})
    else:
        patch = {"cost": {"cost_price": 5000.0}, "switch_on": {"is_active": True},
                 "status": {"catalog_status": "ACTIVE"}}[finish]
        db["products"].update_one({"product_id": black}, {"$set": {**patch, "catalog_status": "ACTIVE", "is_active": True}})
        pm.mirror_update_to_catalog_twin(product_id=black, current=spine, patch=patch, db=db)
    assert _twin(db, lead)["ecom"]["locally_modified"] is True, f"{finish} never queued the model's listing"
    res = _push(db, lead)
    assert res.ok and res.listing["waiting"] == []
    assert _sku(db, black) in {v["sku"] for v in p["variants"]}, "the finished colour went on"


def test_a_colour_catalogued_again_is_handed_the_archived_variant_by_its_id(monkeypatch):
    """Round 2, item 2: Shopify's variant keeps its OLD label (no lens) while
    IMS now labels both items with their lens -- the new item is handed the
    archived row's stored variant id, never a second variant beside it."""
    db = _world()
    fake = FakeShopify()
    lead, p, var = _live_single(db, fake)
    black = _item(db, 2, "Black", "002/58", lens="Grey")
    pm.link_to_family(db, black, lead, actor="u-admin")
    _live(monkeypatch, fake)
    assert _push(db, lead).ok
    old_gid = _row(db, black)["shopify_variant_id"]
    assert next(v for v in p["variants"] if v["id"] == old_gid)["opts"] == {"Color": "Black (002/58)"}
    db["products"].update_one({"product_id": black}, {"$set": {"deleted_at": "2026-10-19", "is_active": False}})
    again = _item(db, 3, "Black", "002/58", lens="Grey", units=(2, 0))
    pm.link_to_family(db, again, lead, actor="u-admin")
    res = _push(db, lead)
    assert res.ok, (res.error, res.code)
    assert len(p["variants"]) == 2, "no second Black beside the archived one"
    assert _row(db, again)["shopify_variant_id"] == old_gid
    assert not _row(db, black).get("shopify_variant_id")
    owned = {r.get("shopify_variant_id") for r in db["catalog_variants"].find({}) if r.get("shopify_variant_id")}
    assert all(v["id"] in owned for v in p["variants"]), "every variant on the listing has an IMS owner"


def test_an_archived_row_lets_go_only_of_the_id_it_handed_over(monkeypatch):
    """Round 2, item 2: when the handed variant is gone on Shopify the new
    item gets a variant of its own -- and the archived row keeps its (stale)
    record instead of releasing an id it never handed over."""
    db = _world()
    fake = FakeShopify()
    lead, p, var = _live_single(db, fake)
    black = _item(db, 2, "Black", "002/58")
    pm.link_to_family(db, black, lead, actor="u-admin")
    _live(monkeypatch, fake)
    assert _push(db, lead).ok
    old_gid = _row(db, black)["shopify_variant_id"]
    p["variants"] = [v for v in p["variants"] if v["id"] != old_gid]  # deleted in Shopify admin
    db["products"].update_one({"product_id": black}, {"$set": {"deleted_at": "2026-10-19", "is_active": False}})
    again = _item(db, 3, "Black", "002/58")
    pm.link_to_family(db, again, lead, actor="u-admin")
    _push(db, lead)
    assert _row(db, again)["shopify_variant_id"] not in (None, old_gid)
    assert _row(db, black).get("shopify_variant_id") == old_gid


def test_runbook_revert_puts_back_only_what_apply_wrote(monkeypatch):
    """Round 2, item 3: a person's later edits survive the revert -- the kept
    item's price and name, its photo record, the folded item's price."""
    db = _world()
    fake = FakeShopify()
    (keep, kp), (other, op) = _two_live_listings(db, fake)
    _live(monkeypatch, fake)
    mod = _script()
    out = _run(mod.apply(db, mod.plan(db)))
    assert out["merged"] and _push(db, keep).ok
    db["catalog_products"].update_one({"id": _twin(db, keep)["id"]}, {"$set": {"offer_price": 9990.0, "name": "EDITED"}})
    db["products"].update_one({"product_id": other}, {"$set": {"offer_price": 8990.0}})
    media_before = _twin(db, keep)["ecom"].get("media_map")
    assert media_before
    done = _run(mod.revert(db, take_down_added=True))
    assert not done.get("refused"), done
    kt = _twin(db, keep)
    assert (kt["offer_price"], kt["name"], kt["ecom"]["media_map"]) == (9990.0, "EDITED", media_before)
    assert db["products"].find_one({"product_id": other})["offer_price"] == 8990.0
    assert not db["products"].find_one({"product_id": other}).get("variant_of")
    ot = _twin(db, other)
    assert ot["ecom"]["shopify_product_id"] == op["id"] and "former_listing" not in ot["ecom"]
    assert "variant_of" not in ot["ecom"]
    row = _row(db, other)
    assert row["parent_product_id"] == ot["id"] and row["shopify_variant_id"] == op["variants"][0]["id"]


def test_runbook_revert_refuses_when_a_field_it_wrote_was_changed(monkeypatch):
    db = _world()
    fake = FakeShopify()
    (keep, kp), (other, op) = _two_live_listings(db, fake)
    _live(monkeypatch, fake)
    mod = _script()
    _run(mod.apply(db, mod.plan(db)))
    # a person unlinks / re-links the folded colour elsewhere after the apply
    db["products"].update_one({"product_id": other}, {"$set": {"variant_of": "sp-someone-else"}})
    snapshot = copy.deepcopy(list(db["catalog_products"].find({})))
    out = _run(mod.revert(db))
    assert out["refused"] and any("products.variant_of" in c for c in out["changed"]), out
    assert list(db["catalog_products"].find({})) == snapshot, "nothing reverted"


def test_a_new_colour_that_adds_a_size_to_a_lead_with_none_is_held_not_looped(monkeypatch):
    """Round 2, item 4: a live lead with no size, a new colour with one --
    the colour is held (named), the press is ok, nothing re-queues it every
    run, and the lead's own edit goes out."""
    db = _world()
    fake = FakeShopify()
    lead, p, var = _live_single(db, fake)
    db["products"].update_one({"product_id": lead}, {"$unset": {"attributes.lens_size": ""}})
    black = _item(db, 2, "Black", "002/58", "62")
    pm.link_to_family(db, black, lead, actor="u-admin")
    _price_edit(db, lead, 9990.0)
    _live(monkeypatch, fake)
    res = _push(db, lead)
    assert res.ok and res.code == "OPTION_MISSING", (res.error, res.code)
    assert [h["sku"] for h in res.listing["held"]] == [_sku(db, black)]
    assert _twin(db, lead)["ecom"]["locally_modified"] is False, "never re-queued every run"
    assert p["status"] == "ACTIVE" and var["price"] == "9990.00" and len(p["variants"]) == 1
    assert fake.ops("imsProductOptionsCreate") == []


def test_a_colour_with_no_mrp_is_held_on_a_live_listing_and_refused_on_a_first_publish(monkeypatch):
    """Round 2, item 6: MRP is required (the Indian MRP display rule)."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    black = next(v for v in p["variants"] if v["sku"] == _sku(db, pids[1]))
    db["products"].update_one({"product_id": pids[1]}, {"$set": {"mrp": None, "offer_price": 10990.0}})
    # its row still carries an old engine price: no MRP, so not that either
    db["catalog_variants"].update_one({"sku": black["sku"]}, {"$unset": {"mrp": ""}, "$set": {"discounted_price": 9990.0}})
    _price_edit(db, pids[0], 11990.0)
    res = _push(db, pids[0])
    assert res.ok and res.code == "UNPRICED_SIZE", (res.error, res.code)
    assert "no MRP" in res.error
    assert (black["price"], black["compareAtPrice"]) == ("12990.00", None), "its last web price stands"
    assert var["price"] == "11990.00"

    db2 = _world()
    pids2 = _family(db2, [("Gold", "001"), ("Black", "002/58", "58", {"mrp": 0.0, "offer": 10990.0})])
    fake2 = FakeShopify()
    _live(monkeypatch, fake2)
    res2 = _push(db2, pids2[0])
    assert res2.ok is False and res2.code == "UNPRICED_SIZE" and fake2.writes() == []


def test_an_offer_above_the_mrp_is_never_the_price(monkeypatch):
    """Round 2, item 8: the offer counts only when it is not above the MRP."""
    db = _world()
    pids = _family(db, [("Gold", "001", "58", {"offer": 13990.0}), ("Black", "002/58")])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    assert _push(db, pids[0]).ok
    gold = next(v for v in _only_product(fake)["variants"] if v["sku"] == _sku(db, pids[0]))
    assert (gold["price"], gold["compareAtPrice"]) == ("12990.00", None)


def test_dry_run_script_reads_only_and_names_what_would_change(monkeypatch, capsys):
    """Round 2, item 5: scripts/dry_run_model_listings.py -- for each live
    model: options / variants, held and waiting by SKU, the lead's price
    under main's rule and the G2 rule, size and own-MRP flags. No Shopify
    call, no write."""
    db = _world()
    fake = FakeShopify()
    pids, p, var = _live_lead(db, fake)
    db["catalog_variants"].update_one({"sku": _sku(db, pids[0])}, {"$set": {"mrp": 12990.0}})
    draft = _item(db, 4, "Bronze", "9196/31")
    db["products"].update_one({"product_id": draft}, {"$set": {"catalog_status": "DRAFT"}})
    pm.link_to_family(db, draft, pids[0], actor="u-admin")
    _price_edit(db, pids[0], 11990.0)
    _dark(monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "dry_run_model_listings", os.path.join(os.path.dirname(_SCRIPT), "dry_run_model_listings.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    before = {c: copy.deepcopy(list(db[c].find({}))) for c in db.list_collection_names()}
    (entry,) = mod.report(db)
    after = {c: list(db[c].find({})) for c in db.list_collection_names()}
    assert {c: after[c] for c in before} == before, "read-only"
    assert all(after[c] == [] for c in after if c not in before), "a read never writes a document"
    assert entry["lead_sku"] == _sku(db, pids[0])
    assert [w["sku"] for w in entry["plan"]["waiting"]] == [_sku(db, draft)]
    assert {v["sku"]: v["on_shopify"] for v in entry["plan"]["variants"]} == {
        _sku(db, pids[0]): True, _sku(db, pids[1]): False, _sku(db, pids[2]): False}
    assert (entry["lead_price_main"], entry["lead_price_g2"]) == (12990.0, 11990.0)
    assert entry["lead_has_size"] is True and entry["lead_row_has_own_mrp"] is True
    mod.print_report([entry])
    out = capsys.readouterr().out
    assert "<-- CHANGES" in out and "WAITING" in out and "options: Color =" in out


def test_a_listing_with_an_option_ims_does_not_manage_names_it_and_does_not_loop(monkeypatch):
    """A live listing someone gave an option IMS does not manage ('Lens'):
    no colour is added, the press says why, and it is never re-queued every
    run for a problem only a person can fix (round 2, item 4's rule for the
    seeding step's own data hold)."""
    db = _world()
    fake = FakeShopify()
    lead = _item(db, 1, "Gold", "001")
    p = fake.new_product(handle="rb3025", options=[{"name": "Lens", "values": ["Standard"]}],
                         variants=[{"opts": {"Lens": "Standard"}}])
    var = p["variants"][0]
    db["catalog_products"].update_one({"id": _twin(db, lead)["id"]}, {"$set": {
        "ecom.shopify_product_id": p["id"], "ecom.shopify_variant_id": var["id"],
        "ecom.shopify_inventory_item_id": var["inv"], "ecom.status": "PUBLISHED"}})
    black = _item(db, 2, "Black", "002/58")
    pm.link_to_family(db, black, lead, actor="u-admin")
    _live(monkeypatch, fake)
    res = _push(db, lead)
    assert res.ok and res.code == "LISTING_OPTIONS_UNKNOWN", (res.error, res.code)
    assert "Lens" in res.error and fake.ops("imsVariantsBulkCreate") == []
    assert _twin(db, lead)["ecom"]["locally_modified"] is False


# ---------------------------------------------------------------------------
# 10. publish prep (owner 2026-10-19): cost is internal
# ---------------------------------------------------------------------------

_ALL_BUT_COST = {"catalog_status": "DRAFT", "hsn_code": "90041000", "gst_rate": 18}


def test_a_draft_missing_only_its_cost_price_joins_and_one_with_other_gaps_waits(monkeypatch):
    """Decision (a): everything filled in except the cost price = finished;
    the colours of a model and its lead are held to that ONE rule."""
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58"), ("Silver", "003/32")])
    for pid in pids[:2]:
        db["products"].update_one({"product_id": pid}, {"$set": _ALL_BUT_COST})
    db["products"].update_one({"product_id": pids[2]}, {"$set": {"catalog_status": "DRAFT"}})  # no HSN/GST
    _dark(monkeypatch)
    res = _push(db, pids[0])
    assert res.ok, (res.error, res.code)
    skus = [db["products"].find_one({"product_id": p})["sku"] for p in pids]
    assert [w["sku"] for w in res.listing["waiting"]] == [skus[2]]
    assert [v["sku"] for v in res.listing["variants"]] == skus[:2]
    # The lead itself: a first publish waits for the same rule.
    db["products"].update_one({"product_id": pids[0]}, {"$unset": {"hsn_code": ""}})
    res = _push(db, pids[0])
    assert res.ok is False and "catalogue draft" in res.error


def test_an_archived_product_is_never_published_and_leaves_the_queue(monkeypatch):
    db = _world()
    pid = _item(db, 1, "Gold", "001")
    twin = _twin(db, pid)
    db["catalog_products"].update_one({"id": twin["id"]}, {"$set": {"deleted_at": "2026-10-19T00:00:00+00:00"}})
    db["products"].update_one({"product_id": pid}, {"$set": {"deleted_at": "2026-10-19T00:00:00+00:00"}})
    fake = FakeShopify()
    _live(monkeypatch, fake)
    twin = _twin(db, pid)
    assert "archived" in (shopify_push.product_push_refusal(db, twin) or "")
    res = _push(db, pid)
    assert res.ok is False and fake.calls == [] and not fake.products
    from api.services.online_catalog import doc_online_state

    assert doc_online_state(db, twin)["queued"] is False


def test_model_paragraph_counts_the_colours_on_the_listing_never_waiting_drafts(monkeypatch):
    db = _world()
    pids = _family(db, [("Gold", "001"), ("Black", "002/58"), ("Silver", "003/32")])
    db["products"].update_one({"product_id": pids[2]}, {"$set": {"catalog_status": "DRAFT"}})  # waits
    spec = importlib.util.spec_from_file_location(
        "write_model_paragraphs", os.path.join(os.path.dirname(_SCRIPT), "write_model_paragraphs.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (m,) = mod.plan(db)
    assert len(m["colours"]) == 2, "the waiting draft is not counted"

    async def _para(category, attrs, colours, sizes, max_length=350):  # noqa: ARG001
        return f"Reflect your style, in {len(colours)} colours."

    monkeypatch.setattr(mod, "generate_model_paragraph", _para)
    _run(mod.apply(db, [m]))
    twin = _twin(db, pids[0])
    assert twin["ecom"][model.PARAGRAPH_COUNTS] == [2, 1]
    lst = model.model_listing(db, twin, ls.variants_for_product(db, twin))
    assert "in 2 colours." in lst["description_html"]
    # The third colour is finished later: the listing now carries 3, so the
    # stale '2 colours' paragraph gives way to the counted one.
    db["products"].update_one({"product_id": pids[2]}, {"$set": {"catalog_status": "ACTIVE"}})
    lst = model.model_listing(db, twin, ls.variants_for_product(db, twin))
    assert "in 2 colours." not in lst["description_html"] and "3 colours" in lst["description_html"]


def test_ai_paragraph_is_cut_at_a_sentence_never_mid_sentence():
    from api.services.product_description import clip_to_sentence

    text = "Reflect your style with the RB3025. It comes in 3 colours. Discover your look today at Better Vision."
    assert clip_to_sentence(text, 70) == "Reflect your style with the RB3025. It comes in 3 colours."
    assert clip_to_sentence(text, 20) is None
    assert clip_to_sentence(text, 500) == text
