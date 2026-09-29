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
  C5 test_a_found_product_with_variants...    the create-shaped input   -> refused
                                              on the update                (productOptions), 2 products
  C6 test_a_failed_update_of_a_found_...      clear the record on      -> a blind create,
     (product, collection, menu)              'found'                     2 objects
  C7 test_a_dropped_connection_after_...      SentOnce only for a       -> record cleared,
     (RemoteProtocolError, a garbled 200,     timeout / 5xx                2 products
     a 200 INTERNAL_SERVER_ERROR)
  C8 test_the_product_finder_reads_every_page one page only             -> 'none', 2 products
  C9 test_a_same_title_product_another_...    no held-by-IMS filter     -> refused for good
  C10 test_a_menu_or_collection_already_...   no 'before' list          -> the shop's own
                                                                           main-menu overwritten
  C11 test_a_candidate_another_open_record... no rival check            -> P1 linked to P2's
                                                                           product, updated it
  C12 test_a_create_refused_unapplied_...     keep the record on a      -> the next press
                                              non-SentOnce exception       refused for 15 min
  C13 test_a_rival_record_whose_send_...      any same-title record a   -> refused for good
                                              rival (no window check)      by a stale record
  C14 test_the_send_window_counts_the_...     _REACH = PROVIDER_TIMEOUT -> 'none', Product/901
                                              + 90 s
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
    ``lose``: the next create applies, then its answer is lost (the
    exception given). ``garble``: the next create applies and answers a 200
    whose body is not JSON. ``refuse``: the next create answers that HTTP
    status, nothing applied. ``fail_updates``: that many productUpdates
    answer 503, nothing applied. productUpdate REFUSES productOptions, as
    production does (product_input.py). The search answers ``page`` nodes
    at a time with Shopify's pageInfo / after cursor."""

    def __init__(self):
        super().__init__([])
        self.products = []
        self.lose = None
        self.garble = False
        self.refuse = None
        self.fail_updates = 0
        self.page = 250

    async def __call__(self, db, query, variables):
        if "imsProductsMade" in query:
            self.calls.append({"op": "imsProductsMade", "variables": copy.deepcopy(variables)})
            lo, hi = re.findall(r"'([^']+)'", variables["q"])
            hits = sorted((p for p in self.products if lo <= _iso(p["created"]) <= hi), key=lambda p: p["created"])
            start = int(variables.get("after") or 0)
            nodes = [{"id": p["id"], "title": p["title"], "createdAt": _iso(p["created"])}
                     for p in hits[start:start + self.page]]
            more = start + self.page < len(hits)
            return {"data": {"products": {"nodes": nodes, "pageInfo": {
                "hasNextPage": more, "endCursor": str(start + self.page) if more else None}}}}
        if "mutation imsProductUpdate(" in query and "productOptions" in variables["input"]:
            self.calls.append({"op": "imsProductUpdate", "variables": copy.deepcopy(variables)})
            return {"data": {"productUpdate": {"product": None, "userErrors": [
                {"field": ["productOptions"], "message": "product_options cannot be specified during update"}]}}}
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
            if shop.garble:
                body, shop.garble = shop.garble, False
                await shop(None, payload["query"], payload["variables"])
                if body is True:
                    return httpx.Response(200, text="<html>upstream reset</html>")
                return httpx.Response(200, json=body)
        if "mutation imsProductUpdate(" in payload["query"] and shop.fail_updates:
            shop.fail_updates -= 1
            shop.calls.append({"op": "imsProductUpdate", "variables": {}})
            return httpx.Response(503, text="service unavailable")
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
    object -- a handle already on the shop made unique with Shopify's '-<n>'
    suffix; the collections and menus lists answer every object, a page at a
    time (two per page here, so the pager runs). ``lose``: the next create
    applies, its answer is lost. ``fail_updates``: that many updates answer
    503, nothing applied. ``updated``: the object each update wrote."""

    def __init__(self):
        self.objects = {"collection": [], "menu": []}
        self.calls = []
        self.lose = None
        self.fail_updates = 0
        self.updated = []

    def add(self, kind, title, handle):
        obj = {"id": "gid://shopify/%s/%d" % (kind.title(), 700 + len(self.objects[kind])),
               "title": title, "handle": handle}
        taken = {o["handle"] for o in self.objects[kind]}
        n = 0
        while obj["handle"] in taken:
            n += 1
            obj["handle"] = "%s-%d" % (handle, n)
        self.objects[kind].append(obj)
        return obj

    async def post_once(self, url, headers, payload):
        q, v = payload["query"], payload["variables"]
        op = re.search(r"(query|mutation)\s+(\w+)", q).group(2)
        self.calls.append(op)
        if op in ("imsCollectionCreate", "imsMenuCreate"):
            kind = "collection" if op == "imsCollectionCreate" else "menu"
            src = v["input"] if kind == "collection" else v
            obj = self.add(kind, src["title"], src["handle"])
            if self.lose:
                exc, self.lose = self.lose, None
                raise exc
            field = "collectionCreate" if kind == "collection" else "menuCreate"
            return httpx.Response(200, json={"data": {field: {kind: {"id": obj["id"], "handle": obj["handle"]}, "userErrors": []}}})
        if op in ("imsCollections", "imsMenus"):
            kind, field = ("collection", "collections") if op == "imsCollections" else ("menu", "menus")
            start = int(v.get("after") or 0)
            more = start + 2 < len(self.objects[kind])
            nodes = [{"id": o["id"], "title": o["title"], "handle": o["handle"]}
                     for o in self.objects[kind][start:start + 2]]
            return httpx.Response(200, json={"data": {field: {"nodes": nodes, "pageInfo": {
                "hasNextPage": more, "endCursor": str(start + 2) if more else None}}}})
        if op in ("imsCollectionUpdate", "imsMenuUpdate"):
            if self.fail_updates:
                self.fail_updates -= 1
                return httpx.Response(503, text="service unavailable")
            kind = "collection" if op == "imsCollectionUpdate" else "menu"
            gid = v["input"]["id"] if kind == "collection" else v["id"]
            self.updated.append(gid)
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


# ===========================================================================
# Round 5: a found create is linked only once its gid is saved; every lost
# answer is SentOnce; the finders read every page, leave out what IMS holds
# and what was on the shop before the send.
# ===========================================================================

_VARIANTS = [
    {"variant_id": "V1", "sku": "P1-BLK", "option_color": "Black", "offer_price": 4000.0, "mrp": 5000.0},
    {"variant_id": "V2", "sku": "P1-RED", "option_color": "Red", "offer_price": 4000.0, "mrp": 5000.0},
]


def _no_backoff(monkeypatch):
    monkeypatch.setattr(shopify_push.transport, "_retry_delay", lambda attempt, retry_after: 0)


def test_a_found_product_with_variants_is_updated_with_an_update_shaped_input(gates, monkeypatch):
    """C5, the panel's HIGH. A product with two colour variants: its
    productCreate (carrying productOptions and tags) commits and the answer
    is lost. The next press finds Product/900 and UPDATES it -- with an
    update-shaped input (no productOptions, which productUpdate refuses in
    production, no tags, which would wipe hand-added ones) -- and saves the
    gid. A third press never creates.
    REVERT-PROOF: reuse the create-shaped input -> 'product_options cannot
    be specified during update', no gid saved, the record gone -> Product/901."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    shop.lose = httpx.ReadTimeout("read timed out")

    first = _run(shopify_push.push_product(db, _new_product_doc(db), _VARIANTS))
    assert first.ok is False and shop.calls_of("imsProductCreate")[0]["variables"]["input"]["productOptions"]

    again = _run(shopify_push.push_product(db, _new_product_doc(db), _VARIANTS))

    (update,) = shop.calls_of("imsProductUpdate")
    sent = update["variables"]["input"]
    assert sent["id"] == "gid://shopify/Product/900"
    assert "productOptions" not in sent and "tags" not in sent, sent
    assert "product_options" not in (again.error or ""), again.error
    assert db["catalog_products"].find_one({"id": "P1"})["ecom"]["shopify_product_id"] == "gid://shopify/Product/900"
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is None

    _run(shopify_push.push_product(db, _new_product_doc(db), _VARIANTS))
    assert _creates(shop) == 1 and len(shop.products) == 1, "never created twice"


def test_a_failed_update_of_a_found_product_keeps_the_record(gates, monkeypatch):
    """C6, the panel's HIGH, the product door. The lost create is found, and
    the productUpdate of it fails on every try (503 x4): no gid saved. The
    record STAYS, so the press after that finds Product/900 again and links
    it -- never a blind productCreate.
    REVERT-PROOF: clear the record on 'found' -> press 3 creates Product/901."""
    _no_backoff(monkeypatch)
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    shop.fail_updates = 4

    failed = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert failed.ok is False, failed
    assert db["catalog_products"].find_one({"id": "P1"})["ecom"].get("shopify_product_id") is None
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is not None, "kept until the gid is saved"

    third = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert third.ok is True and third.shopify_id == "gid://shopify/Product/900", third.error
    assert _creates(shop) == 1 and len(shop.products) == 1, "never created twice"
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is None


@pytest.mark.parametrize("kind", ["collection", "menu"])
def test_a_failed_update_of_a_found_collection_or_menu_keeps_the_record(gates, monkeypatch, kind):
    """C6, the other two creates: the found object's update fails on every
    try; the next press finds it again and links it -- one object.
    REVERT-PROOF: clear the record on 'found' -> a second create."""
    _no_backoff(monkeypatch)
    shop = _NavShop()
    monkeypatch.setattr(shopify_push, "_post_once", shop.post_once)
    db = _DB()
    push, stored = _nav_doc(db, kind)
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(push())
    shop.fail_updates = 4

    failed = _run(push())
    assert failed.ok is False and stored() is None
    assert db[JOURNAL].find_one({"_id": "%s:%s" % (kind, _NAV_KEY[kind])}) is not None

    third = _run(push())

    assert third.ok is True and third.shopify_id == shop.objects[kind][0]["id"] == stored(), third.error
    assert len(shop.objects[kind]) == 1, "never created twice"


@pytest.mark.parametrize("fault", ["disconnected", "garbled-200", "internal-error-200"])
def test_a_dropped_connection_after_the_send_is_a_lost_answer(gates, monkeypatch, fault):
    """C7, the panel's MEDIUM. The connection drops after productCreate left
    (RemoteProtocolError: 'Server disconnected without sending a response'),
    or Shopify answers 200 with a body that is not JSON, or a 200 carrying
    its own INTERNAL_SERVER_ERROR -- either way the product may exist. It is SentOnce, the record stays, and the next press
    reads Shopify first: one product.
    REVERT-PROOF: SentOnce only for a timeout / 5xx -> the record is cleared
    as 'refused unapplied' and the next press creates Product/901 blind."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    if fault == "disconnected":
        shop.lose = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    elif fault == "garbled-200":
        shop.garble = True
    else:  # production's shape of Shopify failing on its side after taking the request
        shop.garble = {"errors": [{"message": "Internal error. Looks like something went wrong on our end.",
                                   "extensions": {"code": "INTERNAL_SERVER_ERROR", "requestId": "r-1"}}]}

    first = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert first.ok is False and "not retried" in (first.error or ""), first.error
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is not None
    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))
    assert again.ok is True and again.shopify_id == "gid://shopify/Product/900", again.error
    assert _creates(shop) == 1 and len(shop.products) == 1


def test_the_transport_names_what_never_left():
    """C7, the rule itself: a failure that proves the request never left (no
    connection) is a plain error; every other transport failure of a
    send-once mutation is SentOnce."""
    from api.services.shopify_push import transport

    req = httpx.Request("POST", "https://t.myshopify.com")
    for exc in (httpx.ConnectError("refused", request=req), httpx.ConnectTimeout("t", request=req)):
        assert isinstance(exc, transport._NEVER_SENT)
    for exc in (httpx.ReadError("r", request=req), httpx.WriteError("w", request=req),
                httpx.RemoteProtocolError("d", request=req), httpx.ReadTimeout("t", request=req)):
        assert not isinstance(exc, transport._NEVER_SENT)


def test_the_product_finder_reads_every_page(gates, monkeypatch):
    """C8, the panel's LOW. Twenty other products were made in the minute
    before the lost send (a batch, an import), and the search answers twenty
    a page: IMS's product is on page 2. The finder reads to the end and
    finds it -- never 'none' followed by a second create.
    REVERT-PROOF: read one page -> 'none', and after the settle time
    Product/921 beside Product/920."""
    shop = _wire(monkeypatch)
    shop.page = 20
    db = _DB()
    _new_product(db)
    early = datetime.now(timezone.utc) - timedelta(seconds=30)
    shop.products += [{"id": "gid://shopify/Product/%d" % (500 + i), "title": "Import %d" % i, "created": early}
                      for i in range(20)]
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    _age(db, shop, 30)

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is True, again.error
    assert [c["variables"]["input"]["id"] for c in shop.calls_of("imsProductUpdate")] == ["gid://shopify/Product/920"]
    assert _creates(shop) == 1


def test_a_same_title_product_another_ims_product_holds_is_no_candidate(gates, monkeypatch):
    """C9, the panel's LOW. P1 and P2 share a title (one model, two colour
    products) and are pressed in one batch: P1's create answer is lost, P2's
    lands seconds later and is linked. P2's product is P2's -- not a
    candidate for P1 -- so P1's next press finds its own Product/900 (never
    'a person must delete the duplicate', naming P2's live listing).
    REVERT-PROOF: no held-by-IMS filter -> refused, both named, for good."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    p2 = _product([OWN], pid="P2")
    p2["ecom"] = {"status": "DRAFT", "locally_modified": True}
    db["catalog_products"].insert_one(copy.deepcopy(p2))
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    two = _run(shopify_push.push_product(db, db["catalog_products"].find_one({"id": "P2"}), []))
    assert two.ok is True and two.shopify_id == "gid://shopify/Product/901", two.error

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is True and again.shopify_id == "gid://shopify/Product/900", again.error
    assert _creates(shop) == 2 and len(shop.products) == 2


_NAV_KEY = {"collection": "C1", "menu": "M1"}


def _nav_doc(db, kind):
    if kind == "collection":
        db["ecom_collections"].insert_one({"collection_id": "C1", "title": "Main menu", "handle": "main-menu",
                                           "collection_type": "SMART", "locally_modified": True})
        return (
            lambda: shopify_push.push_collection(db, db["ecom_collections"].find_one({"collection_id": "C1"})),
            lambda: db["ecom_collections"].find_one({"collection_id": "C1"}).get("shopify_collection_id"),
        )
    db["ecom_menus"].insert_one({"menu_id": "M1", "title": "Main menu", "handle": "main-menu",
                                 "items": [], "locally_modified": True})
    return (
        lambda: shopify_push.push_menu(db, db["ecom_menus"].find_one({"menu_id": "M1"})),
        lambda: db["ecom_menus"].find_one({"menu_id": "M1"}).get("shopify_menu_id"),
    )


@pytest.mark.parametrize("kind", ["collection", "menu"])
@pytest.mark.parametrize("landed", [True, False])
def test_a_menu_or_collection_already_on_the_shop_is_never_taken_for_ims_own(gates, monkeypatch, kind, landed):
    """C10, the panel's LOW. Every shop carries its own 'main-menu' (and a
    collection can share IMS's title and handle). IMS's create of 'Main
    menu' / main-menu loses its answer -- having made main-menu-1 (landed),
    or nothing. The object that was on the shop BEFORE the send is never
    IMS's: never linked, never overwritten. Landed -> main-menu-1 is found
    and linked; not landed -> none (refused until the settle time, then
    created once).
    REVERT-PROOF: no 'before' list -> the shop's own main-menu is found,
    overwritten with IMS's items and linked."""
    shop = _NavShop()
    monkeypatch.setattr(shopify_push, "_post_once", shop.post_once)
    db = _DB()
    shops_own = shop.add(kind, "Main menu", "main-menu")
    push, stored = _nav_doc(db, kind)
    shop.lose = httpx.ReadTimeout("read timed out")
    real = shop.add
    if not landed:
        shop.add = lambda *a: {"id": "never-made", "handle": "x"}  # the create applied nothing
    first = _run(push())
    shop.add = real
    assert first.ok is False

    again = _run(push())

    assert shops_own["id"] not in shop.updated and stored() != shops_own["id"], "the shop's own object"
    if landed:
        assert again.ok is True and again.shopify_id == shop.objects[kind][1]["id"] == stored(), again.error
        assert shop.objects[kind][1]["handle"] == "main-menu-1"
    else:
        assert again.ok is False and again.reason == "create_unsettled", again.error
        db[JOURNAL].update_one({"_id": "%s:%s" % (kind, _NAV_KEY[kind])},
                               {"$set": {"sent_at": datetime.now(timezone.utc) - timedelta(minutes=30)}})
        late = _run(push())
        assert late.ok is True and late.shopify_id == shop.objects[kind][1]["id"] == stored(), late.error
        assert shops_own["id"] not in shop.updated


def test_a_candidate_another_open_record_could_have_made_is_refused(gates, monkeypatch):
    """C11, the panel's MEDIUM (identity). P1 and P2 share a title (two
    colour codes both named 'Black'). P1's productCreate answers 502 --
    nothing applied, but a 5xx may have been, so its record stays. Seconds
    later P2's create lands and ITS answer is lost: Product/900 is P2's, and
    P2's gid is not saved yet. P1's next press sees one candidate that
    either record could have made: refused, P2 named -- never P1 linked to
    P2's listing (and never P2's title / variants overwritten by P1).
    REVERT-PROOF: no rival check -> P1 'found' Product/900 and updated it."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    p2 = _product([OWN], pid="P2")
    p2["ecom"] = {"status": "DRAFT", "locally_modified": True}
    db["catalog_products"].insert_one(copy.deepcopy(p2))
    shop.refuse = 502
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    shop.lose = httpx.ReadTimeout("read timed out")
    two = _run(shopify_push.push_product(db, db["catalog_products"].find_one({"id": "P2"}), []))
    assert two.ok is False and [p["id"] for p in shop.products] == ["gid://shopify/Product/900"]

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is False and again.reason == "create_unsettled", again.error
    assert "product:P2" in again.error and "gid://shopify/Product/900" in again.error
    assert shop.calls_of("imsProductUpdate") == [], "P2's listing never touched by P1's press"
    assert db["catalog_products"].find_one({"id": "P1"})["ecom"].get("shopify_product_id") is None


@pytest.mark.parametrize("fault", ["422", "connect-refused"])
def test_a_create_refused_unapplied_clears_the_record_so_the_next_press_creates_at_once(gates, monkeypatch, fault):
    """C12 (the panel's weak-test M10). A productCreate refused before it was
    applied -- a 4xx, or no connection at all -- made nothing: the record is
    cleared and the next press creates straight away (no 15-minute wait for
    a create that provably never happened).
    REVERT-PROOF: keep the record on a non-SentOnce exception -> the next
    press is refused 'create_unsettled'."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    if fault == "422":
        shop.refuse = 422
    else:
        wired, left = shopify_push._post_once, [1]

        async def _refused(url, headers, payload):
            if "mutation imsProductCreate(" in payload["query"] and left[0]:
                left[0] -= 1
                raise httpx.ConnectError("connection refused")
            return await wired(url, headers, payload)

        monkeypatch.setattr(shopify_push, "_post_once", _refused)

    first = _run(shopify_push.push_product(db, _new_product_doc(db), []))
    assert first.ok is False and shop.products == [], first
    assert db[JOURNAL].find_one({"_id": "product:P1"}) is None

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is True and again.shopify_id == "gid://shopify/Product/900", again.error
    assert len(shop.products) == 1


def test_a_rival_record_whose_send_window_misses_the_candidate_never_blocks(gates, monkeypatch):
    """C13. P2 -- same title -- holds a create record two days old that was
    never settled (its product deleted from IMS since, say). P1's create
    lands and its answer is lost: Product/900 was made inside P1's send
    window and nowhere near P2's, so P2 could not have made it -- P1 finds
    and links it. (A rival counts only when ITS send could have made the
    candidate too, else one stale record refuses every same-title create for
    good.)
    REVERT-PROOF: any open same-title record a rival -> P1 refused, naming
    product:P2."""
    shop = _wire(monkeypatch)
    db = _DB()
    _new_product(db)
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    mine = db[JOURNAL].find_one({"_id": "product:P1"})
    db[JOURNAL].insert_one({**mine, "_id": "product:P2", "sent_at": mine["sent_at"] - timedelta(days=2)})
    assert shop.products[0]["title"] == mine["title"]

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is True and again.shopify_id == "gid://shopify/Product/900", again.error
    assert _creates(shop) == 1


def test_the_send_window_counts_the_transports_own_tries_before_the_send(gates, monkeypatch):
    """C14, the panel's LOW (probe test_probe_window_ignores_transport_pre_
    send_retries). The record's sent_at is stamped BEFORE _graphql, and the
    transport tries a connect timeout three times (30 s each, then its
    backoff) before the create leaves; Shopify makes the product 25 s into
    that last try and the answer is lost: createdAt = sent_at + 122 s. The
    window reaches over every try the transport can make, so the next press
    FINDS it -- never 'none' and a second productCreate.
    REVERT-PROOF: _REACH = PROVIDER_TIMEOUT + 90 s -> Product/900 outside
    the window, and after the settle time Product/901 beside it."""
    shop = _wire(monkeypatch)
    wired, left = shopify_push._post_once, [3]

    async def _connect_timeouts_first(url, headers, payload):
        if "mutation imsProductCreate(" in payload["query"] and left[0]:
            left[0] -= 1
            raise httpx.ConnectTimeout("connect timed out")
        return await wired(url, headers, payload)

    monkeypatch.setattr(shopify_push, "_post_once", _connect_timeouts_first)
    monkeypatch.setattr(shopify_push.transport, "_retry_delay", lambda attempt, retry_after: 0)
    db = _DB()
    _new_product(db)
    shop.lose = httpx.ReadTimeout("read timed out")
    _run(shopify_push.push_product(db, _new_product_doc(db), []))
    assert left == [0] and _creates(shop) == 1 and len(shop.products) == 1
    shop.products[0]["created"] = db[JOURNAL].find_one({"_id": "product:P1"})["sent_at"] + timedelta(seconds=122)
    _age(db, shop, 30)

    again = _run(shopify_push.push_product(db, _new_product_doc(db), []))

    assert again.ok is True and again.shopify_id == "gid://shopify/Product/900", again.error
    assert _creates(shop) == 1 and len(shop.products) == 1, "never created twice"
