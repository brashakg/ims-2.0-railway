"""
A create whose answer was lost is looked for on Shopify before anything is
created again (shopify_push.creates, round 4).

productCreate, collectionCreate and menuCreate are SENT ONCE (transport): a
read timeout or a 5xx after the send may have created the object. The round-3
branch then re-sent the create blind on the next press -- two products on the
storefront (Product/900 and Product/901) from one lost answer. Every test here
runs the REAL transport (_graphql over _post_once) against a PRODUCTION-SHAPED
fake: a create mints the object with Shopify's createdAt; the products search
honours its created_at bounds; nothing is ever sent to a real Shopify.

REVERT-PROOF (each run red against the named revert before it counted):
  C1 test_a_lost_product_create_is_found...   no settle_lost_create (blind
                                              re-create)                -> 2 products
  C2 test_a_create_that_never_landed...       no _SETTLE_AFTER wait     -> created
                                                                           while it may still show
  C3 test_two_candidates_refuse...            take the first candidate  -> linked to
                                                                           the human's product
  C4 test_a_lost_collection_or_menu_create... no settle_lost_create     -> 2 objects
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402
import copy  # noqa: E402
import re  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from api.services import shopify_live_sync, shopify_push  # noqa: E402
from api.services.shopify_push import creates  # noqa: E402
from tests.test_design_queue_repress import OWN, _DB, _Shopify, _product, gates  # noqa: E402,F401

JOURNAL = creates.CREATES_COLLECTION


def _run(coro):
    return asyncio.run(coro)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class _Shop(_Shopify):
    """The repress fake (media, publish, variants, ...) plus the product
    CREATE and the products search, production-shaped: a create mints
    Product/900, 901, ... with createdAt = now; the search answers the
    products whose createdAt lies inside the query's created_at bounds.
    ``lose``: the next create applies, then its answer is lost (ReadTimeout).
    ``refuse``: the next create answers that HTTP status, nothing applied."""

    def __init__(self):
        super().__init__([])
        self.products = []
        self.lose = None
        self.refuse = None

    async def __call__(self, db, query, variables):
        if "imsProductsMade" in query:
            self.calls.append({"op": "imsProductsMade", "variables": copy.deepcopy(variables)})
            lo, hi = re.findall(r"'([^']+)'", variables["q"])
            return {"data": {"products": {"nodes": [
                {"id": p["id"], "title": p["title"], "createdAt": _iso(p["created"])}
                for p in self.products
                if lo <= _iso(p["created"]) <= hi
            ]}}}
        if "mutation imsProductCreate(" in query:
            self.calls.append({"op": "imsProductCreate", "variables": copy.deepcopy(variables)})
            gid = "gid://shopify/Product/%d" % (900 + len(self.products))
            self.products.append({"id": gid, "title": variables["input"]["title"], "created": datetime.now(timezone.utc)})
            return {"data": {"productCreate": {"product": {
                "id": gid, "handle": "h", "tags": [],
                "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/901", "title": "Default Title",
                                        "selectedOptions": [], "inventoryItem": {"id": "gid://shopify/InventoryItem/902"}}]},
            }, "userErrors": []}}}
        return await super().__call__(db, query, variables)


def _wire(monkeypatch):
    shop = _Shop()

    async def _post_once(url, headers, payload):
        if "mutation imsProductCreate(" in payload["query"]:
            if shop.refuse:
                status, shop.refuse = shop.refuse, None
                shop.calls.append({"op": "imsProductCreate", "variables": {}})
                return httpx.Response(status, text="bad gateway")
            if shop.lose:
                exc, shop.lose = shop.lose, None
                await shop(None, payload["query"], payload["variables"])
                raise exc
        return httpx.Response(200, json=await shop(None, payload["query"], payload["variables"]))

    monkeypatch.setattr(shopify_push, "_post_once", _post_once)
    return shop


def _new_product(db):
    doc = _product([OWN])
    doc["ecom"] = {"status": "DRAFT", "locally_modified": True}
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    return db["catalog_products"].find_one({"id": "P1"})


def _age(db, shop, minutes, key="product:P1"):
    """The clock moving on: the create's intent and what it made age together."""
    intent = db[JOURNAL].find_one({"_id": key})
    shift = intent["sent_at"] - (datetime.now(timezone.utc) - timedelta(minutes=minutes))
    db[JOURNAL].update_one({"_id": key}, {"$set": {"sent_at": intent["sent_at"] - shift}})
    for p in shop.products:
        p["created"] -= shift


def _queued(db):
    return [d["id"] for d in shopify_live_sync.select_dirty_products(db)[0]] == ["P1"]


def _creates(shop):
    return len(shop.calls_of("imsProductCreate"))


def test_a_lost_product_create_is_found_and_updated_never_created_twice(gates, monkeypatch):
    """C1, the finding's case. The first press's productCreate commits on
    Shopify and its answer is lost (ReadTimeout): ok=False 'not retried', the
    row stays queued, the intent stays on record. The next press READS
    Shopify first: the one product made in the send window with this title
    is linked and UPDATED -- one product on Shopify, never two.
    REVERT-PROOF: no settle_lost_create -> a second productCreate, Product/901."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    shop.lose = httpx.ReadTimeout("read timed out")

    first = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert first.ok is False and "not retried" in (first.error or ""), first.error
    assert _creates(shop) == 1 and len(shop.products) == 1 and _queued(db)
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is not None

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is True and again.shopify_id == "gid://shopify/Product/900", again.error
    assert _creates(shop) == 1 and len(shop.products) == 1, "never created twice"
    ops = shop.ops()
    assert ops.index("imsProductsMade") < ops.index("imsProductUpdate")
    assert db["catalog_products"].find_one({"id": "P1"})["ecom"]["shopify_product_id"] == "gid://shopify/Product/900"
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is None and not _queued(db)


def test_a_create_that_never_landed_is_sent_again_only_once_shopify_could_show_it(gates, monkeypatch):
    """C2. The first productCreate answers 502 and nothing is applied. The
    next press finds nothing -- but Shopify's search may show a new product
    minutes late, so right after the send it REFUSES (nothing sent, the time
    to press again named). Once the window has closed and that time has
    passed, nothing found means never made: the create is sent -- once.
    REVERT-PROOF: no _SETTLE_AFTER wait -> created on the early press."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    shop.refuse = 502

    first = _run(shopify_push.push_product(db, _new_product_doc(db), []))
    assert first.ok is False and shop.products == []

    early = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert early.ok is False and early.reason == "create_unsettled", early.error
    assert "press again after" in early.error and _creates(shop) == 1 and _queued(db)
    assert [o for o in shop.ops() if o.startswith("ims") and o != "imsProductsMade"] == ["imsProductCreate"]

    _age(db, shop, 20)
    late = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert late.ok is True and late.shopify_id == "gid://shopify/Product/900", late.error
    assert _creates(shop) == 2 and len(shop.products) == 1


def test_two_candidates_refuse_and_name_them(gates, monkeypatch):
    """C3. The lost create landed -- and a person made a product with the same
    title inside the same minutes. Which one is IMS's is a guess, and a
    guess is never a link: refused, both named, nothing sent.
    REVERT-PROOF: take the first candidate -> linked (and updated) at random."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    shop.products.append({"id": "gid://shopify/Product/555", "title": shop.products[0]["title"],
                          "created": shop.products[0]["created"] + timedelta(seconds=30)})

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is False and again.reason == "create_unsettled", again.error
    assert "gid://shopify/Product/900" in again.error and "gid://shopify/Product/555" in again.error
    assert shop.calls_of("imsProductUpdate") == [] and _creates(shop) == 1


def _new_product_doc(db):
    return db["catalog_products"].find_one({"id": "P1"})


class _NavShop:
    """A production-shaped shop for collections and menus: a create makes the
    object (handle unique for a menu); the collections search answers those
    updated since its bound; the menus list answers every menu."""

    def __init__(self):
        self.objects = {"collection": [], "menu": []}
        self.calls = []
        self.lose = None

    async def post_once(self, url, headers, payload):
        q, v = payload["query"], payload["variables"]
        op = re.search(r"(query|mutation)\s+(\w+)", q).group(2)
        self.calls.append(op)
        if op in ("imsCollectionCreate", "imsMenuCreate"):
            kind = "collection" if op == "imsCollectionCreate" else "menu"
            src = v["input"] if kind == "collection" else v
            obj = {"id": "gid://shopify/%s/%d" % (kind.title(), 700 + len(self.objects[kind])),
                   "title": src["title"], "handle": src["handle"], "updated": datetime.now(timezone.utc)}
            self.objects[kind].append(obj)
            if self.lose:
                exc, self.lose = self.lose, None
                raise exc
            field = "collectionCreate" if kind == "collection" else "menuCreate"
            return httpx.Response(200, json={"data": {field: {kind: {"id": obj["id"], "handle": obj["handle"]}, "userErrors": []}}})
        if op == "imsCollectionsTouched":
            (lo,) = re.findall(r"'([^']+)'", v["q"])
            nodes = [{"id": o["id"], "title": o["title"], "handle": o["handle"]}
                     for o in self.objects["collection"] if _iso(o["updated"]) >= lo]
            return httpx.Response(200, json={"data": {"collections": {"nodes": nodes}}})
        if op == "imsMenus":
            nodes = [{"id": o["id"], "title": o["title"], "handle": o["handle"]} for o in self.objects["menu"]]
            return httpx.Response(200, json={"data": {"menus": {"nodes": nodes}}})
        if op in ("imsCollectionUpdate", "imsMenuUpdate"):
            kind = "collection" if op == "imsCollectionUpdate" else "menu"
            gid = v["input"]["id"] if kind == "collection" else v["id"]
            field = "collectionUpdate" if kind == "collection" else "menuUpdate"
            return httpx.Response(200, json={"data": {field: {kind: {"id": gid, "handle": "x"}, "userErrors": []}}})
        return httpx.Response(200, json={"data": {}})


@pytest.mark.parametrize("kind", ["collection", "menu"])
def test_a_lost_collection_or_menu_create_is_found_and_updated(gates, monkeypatch, kind):
    """C4. The same rule on the two other send-once creates: a lost
    collectionCreate / menuCreate is found on Shopify by its title and the
    handle IMS sent (a menu's handle is unique on the shop) and UPDATED --
    never created a second time.
    REVERT-PROOF: no settle_lost_create -> a second create, two objects."""
    shop = _NavShop()
    monkeypatch.setattr(shopify_push, "_post_once", shop.post_once)
    db = _DB()
    shop.lose = httpx.ReadTimeout("read timed out")
    if kind == "collection":
        doc = {"collection_id": "C1", "title": "Aviators", "handle": "aviators",
               "collection_type": "SMART", "locally_modified": True}
        db["ecom_collections"].insert_one(copy.deepcopy(doc))
        push = lambda: shopify_push.push_collection(db, db["ecom_collections"].find_one({"collection_id": "C1"}))  # noqa: E731
        stored = lambda: db["ecom_collections"].find_one({"collection_id": "C1"}).get("shopify_collection_id")  # noqa: E731
    else:
        doc = {"menu_id": "M1", "title": "Main menu", "handle": "main-menu", "items": [], "locally_modified": True}
        db["ecom_menus"].insert_one(copy.deepcopy(doc))
        push = lambda: shopify_push.push_menu(db, db["ecom_menus"].find_one({"menu_id": "M1"}))  # noqa: E731
        stored = lambda: db["ecom_menus"].find_one({"menu_id": "M1"}).get("shopify_menu_id")  # noqa: E731

    first = _run(push())
    assert first.ok is False and "not retried" in (first.error or ""), first.error

    again = _run(push())

    assert again.ok is True, again.error
    assert len(shop.objects[kind]) == 1, "never created twice"
    assert again.shopify_id == shop.objects[kind][0]["id"] == stored()
    create = "imsCollectionCreate" if kind == "collection" else "imsMenuCreate"
    assert shop.calls.count(create) == 1
