"""Menu links go to Shopify in Shopify's own form (live bug 2026-11-02).

The first push of the self-maintaining main menu sent every template link as
type HTTP with a store path ("/pages/about-us"); Shopify stores such an item
as http://about-us -- 10 dead links on bettervision.in (About Us + its 7
pages, Sunglasses > By Gender > Shop all, Shop by Brand). Locks:
- the default template's 10 links are sent as PAGE / COLLECTION with the
  shop's own resourceId (Track Your Order stays an absolute HTTP link);
- no item is ever sent as HTTP with a relative url;
- a page / collection handle Shopify does not have, a store path IMS cannot
  look up, or a failed read REFUSES the push (nothing sent);
- generated collection links are sent exactly as before (no extra read);
- a stored PAGE item's bare id is promoted to a Page gid (not Collection).
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.services import generated_menu as gm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push.menus import UnresolvedLink, build_menu_items, item_form  # noqa: E402

# The shop's pages as read live on 2026-11-02 (handle -> gid).
PAGES = {
    "about-us": "gid://shopify/Page/74398728379",
    "book-eye-test-at-better-vision": "gid://shopify/Page/127707578617",
    "pune-gangadham": "gid://shopify/Page/127585779961",
    "bokaro-city-centre": "gid://shopify/Page/135660732665",
    "dhanbad-bank-more": "gid://shopify/Page/135660830969",
    "how-to-check-authentic-ray-ban": "gid://shopify/Page/98600583417",
    "contact-us": "gid://shopify/Page/74441556155",
}
SUN_GID = "gid://shopify/Collection/508057813241"
MEN_GID = "gid://shopify/Collection/600"
RESOURCES = {"PAGE": dict(PAGES), "COLLECTION": {"sunglasses": SUN_GID, "mens-sunglasses": MEN_GID}}

ROWS = [
    {"ims_key": "type:sunglasses", "handle": "sunglasses", "title": "Sunglasses", "shopify_collection_id": SUN_GID},
    {"ims_key": "gender:sunglasses:men", "handle": "mens-sunglasses", "title": "Men's Sunglasses",
     "shopify_collection_id": MEN_GID},
    {"ims_key": "brand:ray-ban", "handle": "ray-ban", "title": "Ray-Ban",
     "shopify_collection_id": "gid://shopify/Collection/700"},
]
COUNTS = {"sunglasses": {"count": 9}, "mens-sunglasses": {"count": 4}, "ray-ban": {"count": 3}}


def _tree():
    """The default template as the live menu got it: Shop by Brand's heading
    adopted from the hand-made menu (/collections/sunglasses)."""
    t = gm.default_template()
    next(s for s in t["sections"] if s["title"] == "Shop by Brand")["url"] = "/collections/sunglasses"
    return gm.generate(t, ROWS, COUNTS)[0]


def _find(items, *path):
    node = None
    for title in path:
        node = next(i for i in items if i["title"] == title)
        items = node.get("items") or []
    return node


def _walk(items):
    for i in items:
        yield i
        yield from _walk(i.get("items") or [])


def test_the_ten_live_links_are_sent_in_shopifys_own_form():
    sent = build_menu_items(_tree(), resources=RESOURCES)
    about = _find(sent, "About Us")
    assert about["type"] == "PAGE" and about["resourceId"] == PAGES["about-us"]
    for title, handle in [("Our Story", "about-us"), ("Book an Eye Test", "book-eye-test-at-better-vision"),
                          ("Pune Store", "pune-gangadham"), ("Bokaro Store", "bokaro-city-centre"),
                          ("Dhanbad Store", "dhanbad-bank-more"),
                          ("Genuine Ray-Ban Check", "how-to-check-authentic-ray-ban"),
                          ("Contact Us", "contact-us")]:
        item = _find(sent, "About Us", title)
        assert item == {"title": title, "type": "PAGE", "resourceId": PAGES[handle], "url": f"/pages/{handle}"}
    shop_all = _find(sent, "Sunglasses", "By Gender", "Shop all")
    assert shop_all["type"] == "COLLECTION" and shop_all["resourceId"] == SUN_GID
    brand = _find(sent, "Shop by Brand")
    assert brand["type"] == "COLLECTION" and brand["resourceId"] == SUN_GID
    assert _find(sent, "About Us", "Track Your Order") == {
        "title": "Track Your Order", "type": "HTTP", "url": "https://bettervision.shiprocket.co/"}


def test_no_item_is_ever_sent_as_http_with_a_store_path():
    for item in _walk(build_menu_items(_tree(), resources=RESOURCES)):
        if item["type"] == "HTTP":
            assert item["url"] == "#" or item["url"].startswith("https://"), item


def test_generated_collection_links_are_sent_as_before():
    sent = build_menu_items(_tree(), resources=RESOURCES)
    assert _find(sent, "Sunglasses") == {"title": "Sunglasses", "type": "COLLECTION", "resourceId": SUN_GID,
                                         "items": _find(sent, "Sunglasses")["items"]}
    men = _find(sent, "Sunglasses", "By Gender", "Men's")
    assert men == {"title": "Men's", "type": "COLLECTION", "resourceId": MEN_GID}
    assert _find(sent, "Sunglasses", "By Gender")["type"] == "HTTP"
    assert _find(sent, "Sunglasses", "By Gender")["url"] == "#"


@pytest.mark.parametrize("url,form", [
    ("/", {"type": "FRONTPAGE", "url": "/"}),
    ("/collections", {"type": "COLLECTIONS", "url": "/collections"}),
    ("/collections/all", {"type": "CATALOG", "url": "/collections/all"}),
    ("/search", {"type": "SEARCH", "url": "/search"}),
    ("#", {"type": "HTTP", "url": "#"}),
    ("mailto:care@bettervision.in", {"type": "HTTP", "url": "mailto:care@bettervision.in"}),
    ("/pages/Contact-Us/", {"type": "PAGE", "resourceId": PAGES["contact-us"], "url": "/pages/Contact-Us/"}),
])
def test_every_kind_of_link(url, form):
    assert item_form({"title": "x", "item_type": "HTTP", "url": url}, RESOURCES) == form


@pytest.mark.parametrize("url,why", [
    ("/pages/no-such-page", "Shopify has no page 'no-such-page'"),
    ("/collections/gone", "Shopify has no collection 'gone'"),
    ("/products/rb3025", "cannot send"),
    ("/blogs/news", "cannot send"),
])
def test_a_link_shopify_cannot_resolve_is_refused(url, why):
    with pytest.raises(UnresolvedLink, match=why):
        item_form({"title": "x", "item_type": "HTTP", "url": url}, RESOURCES)


def test_a_stored_page_item_keeps_its_own_gid_kind():
    assert item_form({"title": "x", "item_type": "PAGE", "resource_id": "74398728379"}) == {
        "type": "PAGE", "resourceId": "gid://shopify/Page/74398728379"}


# ---------------------------------------------------------------------------
# push_menu LIVE against a fake Shopify
# ---------------------------------------------------------------------------


class _Shop:
    def __init__(self, pages=None, read_fails=False):
        self.calls, self.sent = [], []
        self.pages = PAGES if pages is None else pages
        self.read_fails = read_fails

    async def __call__(self, db, query, variables):  # noqa: ARG002
        name = re.search(r"(?:query|mutation)\s+(\w+)", query).group(1)
        self.calls.append(name)
        if name == "imsPagesRead":
            if self.read_fails:
                return {"errors": [{"message": "Throttled"}]}
            nodes = [{"id": g, "handle": h} for h, g in self.pages.items()]
            if not variables.get("after"):  # two pages of results
                return {"data": {"pages": {"nodes": nodes[:3], "pageInfo": {"hasNextPage": True, "endCursor": "c"}}}}
            return {"data": {"pages": {"nodes": nodes[3:], "pageInfo": {"hasNextPage": False}}}}
        if name == "imsCollectionsRead":
            return {"data": {"collections": {"nodes": [
                {"id": SUN_GID, "handle": "sunglasses", "productsCount": {"count": 9}},
                {"id": MEN_GID, "handle": "mens-sunglasses", "productsCount": {"count": 4}}],
                "pageInfo": {"hasNextPage": False}}}}
        if name == "imsMenuUpdate":
            self.sent.append(variables)
            return {"data": {"menuUpdate": {"menu": {"id": variables["id"]}, "userErrors": []}}}
        raise AssertionError(name)


def _push(monkeypatch, shop, items):
    monkeypatch.setattr(shopify_push, "_graphql", shop)
    menu = {"menu_id": "M1", "handle": "main-menu", "title": "Main", "items": items,
            "shopify_menu_id": "gid://shopify/Menu/174363639995"}
    return asyncio.run(shopify_push.push_menu(mongomock.MongoClient()["t"], menu, live=True))


def test_a_live_push_sends_the_resolved_links(monkeypatch):
    shop = _Shop()
    res = _push(monkeypatch, shop, _tree())
    assert res.ok, res.error
    sent = shop.sent[0]["items"]
    assert _find(sent, "About Us", "Contact Us")["resourceId"] == PAGES["contact-us"]
    assert not [i for i in _walk(sent) if i["type"] == "HTTP" and i["url"].startswith("/")]


def test_an_unknown_page_refuses_the_push(monkeypatch):
    shop = _Shop(pages={h: g for h, g in PAGES.items() if h != "pune-gangadham"})
    res = _push(monkeypatch, shop, _tree())
    assert res.ok is False and "imsMenuUpdate" not in shop.calls
    assert "refused" in res.error and "Pune Store" in res.error and "pune-gangadham" in res.error


def test_a_failed_pages_read_refuses_the_push(monkeypatch):
    shop = _Shop(read_fails=True)
    res = _push(monkeypatch, shop, _tree())
    assert res.ok is False and "imsMenuUpdate" not in shop.calls and "pages read failed" in res.error


def test_a_menu_of_collection_links_reads_nothing_extra(monkeypatch):
    shop = _Shop()
    items = [{"title": "Sunglasses", "item_type": "COLLECTION", "resource_id": SUN_GID},
             {"title": "More", "item_type": "HTTP", "url": "#"}]
    res = _push(monkeypatch, shop, items)
    assert res.ok and shop.calls == ["imsMenuUpdate"]
