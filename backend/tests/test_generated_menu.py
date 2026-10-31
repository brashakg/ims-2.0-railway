"""The self-maintaining website menu (owner 2026-10-29).

services/generated_menu builds the main menu from a TEMPLATE and the IMS
collections that have products on the website, and pushes it only when it
changed. Locks:
- an empty collection is left out, and appears by itself once it has one;
- ordering: gender fixed (Men's, Women's, Unisex, Kids, Polarized), shape by
  count, price ascending, Top N brands by count (ties A-Z), brands A-Z;
- Boss and Hugo Boss stay separate;
- Top N counts only brands the website can show; a push problem, a
  Shopify-empty / missing collection and unpushed rules hide a row;
- push only on change (content hash); DARK => SIMULATED, no network, no
  repeated audit, and a DARK-simulated tree is still pushed once LIVE; the
  first LIVE push ADOPTS the existing Shopify main-menu (menuUpdate of its
  gid -- found on any page -- never menuCreate), its heading URLs and item
  ids; every LIVE overwrite backs the live tree up; a failed read pushes
  nothing; a failed push is retried next run;
- an unreadable catalogue pushes nothing; an automatic push with no
  listing, no collection link or half the links gone is refused until a
  human forces it;
- the scheduled live sync regenerates it and refreshes the membership view;
  every hand edit route is refused; the template is validated; the
  Regenerate & push now route, push-all, status and preview; the seed.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

import pytest  # noqa: E402

from api.services import generated_menu as gm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

SHOP_MENU_GID = "gid://shopify/Menu/174363639995"


def _run(coro):
    return asyncio.run(coro)


def _row(key, handle, title, gid=True):
    return {"ims_key": key, "handle": handle, "title": title, "collection_type": "SMART",
            "shopify_collection_id": f"gid://shopify/Collection/{abs(hash(handle)) % 10**6}" if gid else None}


ROWS = [
    _row("new-arrivals", "new-arrivals", "New Arrivals"),
    _row("type:sunglasses", "sunglasses", "Sunglasses"),
    _row("type:eyeglasses", "eyeglasses", "Eyeglasses"),
    _row("gender:sunglasses:unisex", "unisex-sunglasses", "Unisex Sunglasses"),
    _row("gender:sunglasses:men", "mens-sunglasses", "Men's Sunglasses"),
    _row("gender:sunglasses:kids", "kids-sunglasses", "Kids' Sunglasses"),
    _row("gender:sunglasses:women", "womens-sunglasses", "Women's Sunglasses"),
    _row("polarized:sunglasses", "polarized-sunglasses", "Polarized Sunglasses"),
    _row("shape:sunglasses:round", "round-sunglasses", "Round Sunglasses"),
    _row("shape:sunglasses:square", "square-sunglasses", "Square Sunglasses"),
    _row("shape:sunglasses:aviator", "aviator-sunglasses", "Aviator Sunglasses"),
    _row("shape:sunglasses:oval", "oval-sunglasses", "Oval Sunglasses"),
    _row("price:over-10000", "over-10000", "Over Rs 10,000"),
    _row("price:under-3000", "under-3000", "Under Rs 3,000"),
    _row("price:5000-10000", "5000-10000", "Rs 5,000-10,000"),
] + [_row(f"brand:{h}", h, t) for h, t in [
    ("ray-ban", "Ray-Ban"), ("carrera", "Carrera"), ("polaroid", "Polaroid"), ("idee", "Idee"),
    ("oakley", "Oakley"), ("marc-jacobs", "Marc Jacobs"), ("maui-jim", "Maui Jim"), ("vogue", "Vogue"),
    ("burberry", "Burberry"), ("boss", "Boss"), ("hugo-boss", "Hugo Boss"), ("emporio-armani", "Emporio Armani"),
    ("police", "Police"), ("versace", "Versace"), ("gucci", "Gucci"),
]]

COUNTS = {
    "new-arrivals": 12, "sunglasses": 700, "eyeglasses": 0,
    "mens-sunglasses": 300, "womens-sunglasses": 200, "unisex-sunglasses": 150, "kids-sunglasses": 0,
    "polarized-sunglasses": 90,
    "square-sunglasses": 235, "aviator-sunglasses": 93, "round-sunglasses": 93, "oval-sunglasses": 0,
    "under-3000": 0, "5000-10000": 140, "over-10000": 60,
    "ray-ban": 138, "carrera": 121, "polaroid": 92, "idee": 73, "oakley": 50, "marc-jacobs": 23,
    "maui-jim": 22, "vogue": 15, "burberry": 14, "boss": 13, "hugo-boss": 13, "emporio-armani": 10,
    "police": 8, "versace": 8, "gucci": 0,
}


def _counts(over=None):
    c = dict(COUNTS, **(over or {}))
    return {h: {"count": n, "reason": None} for h, n in c.items()}


def _gen(over=None, rows=None, template=None):
    return gm.generate(template or gm.default_template(), rows or ROWS, _counts(over))


def _top(items, title):
    return next(i for i in items if i["title"] == title)


def _col(panel, title):
    return [c["title"] for c in next(g for g in panel["children"] if g["title"] == title)["children"]]


# ---------------------------------------------------------------------------
# The generator (pure)
# ---------------------------------------------------------------------------


def test_the_approved_design_is_generated_in_order():
    items, _hidden = _gen()
    assert [i["title"] for i in items] == ["New Arrivals", "Sunglasses", "Shop by Brand", "About Us"]
    sun = _top(items, "Sunglasses")
    assert sun["item_type"] == "COLLECTION" and sun["handle"] == "sunglasses"
    assert [g["title"] for g in sun["children"]] == ["By Gender", "By Shape", "By Price", "Top 10 Brands"]
    about = _top(items, "About Us")
    assert [(c["title"], c["url"]) for c in about["children"]] == [
        (link["title"], link["url"]) for link in gm.default_template()["sections"][-1]["links"]]
    assert about["children"][-1]["url"] == "https://bettervision.shiprocket.co/"


def test_ordering_rules():
    sun = _top(_gen()[0], "Sunglasses")
    # gender: fixed order, empty Kids left out, Polarized, then the static Shop all
    assert _col(sun, "By Gender") == ["Men's", "Women's", "Unisex", "Polarized", "Shop all"]
    # shape: by product count, ties A-Z (Aviator 93 before Round 93), empty Oval left out
    assert _col(sun, "By Shape") == ["Square", "Aviator", "Round"]
    # price: ascending, the empty Under Rs 3,000 band left out
    assert _col(sun, "By Price") == ["Rs 5,000-10,000", "Over Rs 10,000"]
    # top 10 by count, ties A-Z (Boss 13 before Hugo Boss 13)
    assert _col(sun, "Top 10 Brands") == ["Ray-Ban", "Carrera", "Polaroid", "Idee", "Oakley", "Marc Jacobs",
                                          "Maui Jim", "Vogue", "Burberry", "Boss"]
    brands = _col(_top(_gen()[0], "Shop by Brand"), "Brands")
    assert brands == sorted(brands, key=str.lower) and "Gucci" not in brands


def test_boss_and_hugo_boss_stay_separate():
    brands = _col(_top(_gen()[0], "Shop by Brand"), "Brands")
    assert "Boss" in brands and "Hugo Boss" in brands
    nodes = {c["title"]: c for g in _top(_gen()[0], "Shop by Brand")["children"] for c in g["children"]}
    assert nodes["Boss"]["handle"] == "boss" and nodes["Hugo Boss"]["handle"] == "hugo-boss"


def test_top_n_is_the_template_value():
    t = gm.default_template()
    t["sections"][1]["groups"][3]["limit"] = 3
    sun = _top(_gen(template=t)[0], "Sunglasses")
    assert _col(sun, "Top 10 Brands") == ["Ray-Ban", "Carrera", "Polaroid"]


def test_an_empty_collection_is_hidden_and_appears_when_it_gets_products():
    items, hidden = _gen()
    assert "Eyeglasses" not in [i["title"] for i in items]
    assert any(h["handle"] == "eyeglasses" and "no products" in h["reason"] for h in hidden)
    assert any(h["handle"] == "kids-sunglasses" for h in hidden)
    items2, hidden2 = _gen({"eyeglasses": 1, "kids-sunglasses": 4, "gucci": 2})
    assert [i["title"] for i in items2] == ["New Arrivals", "Sunglasses", "Eyeglasses", "Shop by Brand", "About Us"]
    assert "Kids" in _col(_top(items2, "Sunglasses"), "By Gender")
    assert "Gucci" in _col(_top(items2, "Shop by Brand"), "Brands")
    assert not any(h["handle"] in ("eyeglasses", "kids-sunglasses", "gucci") for h in hidden2)


def test_a_collection_not_on_shopify_yet_is_hidden():
    rows = [dict(r, shopify_collection_id=None) if r["handle"] == "new-arrivals" else r for r in ROWS]
    items, hidden = _gen(rows=rows)
    assert "New Arrivals" not in [i["title"] for i in items]
    assert any(h["handle"] == "new-arrivals" and h["reason"] == "not on Shopify yet" for h in hidden)


def test_a_panel_with_no_products_anywhere_is_left_out():
    zero = {h: 0 for h in COUNTS}
    items, hidden = _gen(zero)
    assert [i["title"] for i in items] == ["About Us"]
    assert any(h["title"] == "Sunglasses" for h in hidden)


def test_the_hash_is_what_is_sent_not_the_counts():
    a, _ = _gen()
    b, _ = _gen({"ray-ban": 139})  # a count changed, the menu did not
    assert gm.menu_hash("Main", "main-menu", a) == gm.menu_hash("Main", "main-menu", b)
    c, _ = _gen({"eyeglasses": 1})
    assert gm.menu_hash("Main", "main-menu", a) != gm.menu_hash("Main", "main-menu", c)


def test_template_validation():
    assert gm.validate_template(gm.default_template()) == []
    assert gm.validate_template({"sections": []})
    bad = copy.deepcopy(gm.default_template())
    bad["sections"][1]["groups"][0]["source"] = "colour"
    bad["sections"][0]["handle"] = ""
    bad["sections"][1]["groups"][3]["limit"] = 0
    errs = gm.validate_template(bad)
    assert len(errs) == 3


def test_top_n_skips_a_brand_the_website_cannot_show():
    """The Top-N cut comes AFTER the on-Shopify check: an unpushed Ray-Ban
    (the biggest brand) leaves Top 10 at TEN brands, not nine."""
    rows = [dict(r, shopify_collection_id=None) if r["handle"] == "ray-ban" else r for r in ROWS]
    items, hidden = _gen(rows=rows)
    top = _col(_top(items, "Sunglasses"), "Top 10 Brands")
    assert len(top) == 10 and "Ray-Ban" not in top and top[-1] == "Hugo Boss"
    assert any(h["title"] == "Ray-Ban" and h["reason"] == "not on Shopify yet" and "Top 10" in h["section"]
               for h in hidden)


def test_the_tree_is_deterministic_whatever_the_row_order():
    assert _gen(rows=list(reversed(ROWS))) == _gen(rows=ROWS)


def test_a_full_tie_is_broken_by_ims_key_whatever_the_row_order():
    """Two brand rows with the same title and the same count (a renamed
    brand's twin): the final ims_key tiebreak keeps Top N and A-Z stable."""
    rows = ROWS + [_row("brand:boss-eyewear", "boss-eyewear", "Boss")]
    over = {"boss-eyewear": 13}
    for template_rows in (rows, list(reversed(rows))):
        items, _ = _gen(over, rows=template_rows)
        handles = [c["handle"] for c in next(g for g in _top(items, "Shop by Brand")["children"])["children"]]
        assert handles.index("boss") < handles.index("boss-eyewear")
    assert _gen(over, rows=list(reversed(rows))) == _gen(over, rows=rows)


def test_a_collection_with_a_push_problem_is_not_linked():
    rows = [dict(r, push_problem="Online Store publish failed: x") if r["handle"] == "carrera" else r for r in ROWS]
    items, hidden = _gen(rows=rows)
    assert "Carrera" not in _col(_top(items, "Shop by Brand"), "Brands")
    assert any(h["handle"] == "carrera" and "not on the website yet" in h["reason"] for h in hidden)


def test_before_a_live_push_shopify_counts_and_unpushed_rules_hide_a_collection():
    """shop_counts (read right before a LIVE push): a collection Shopify
    shows empty, one Shopify does not have, and one whose latest rules did
    not reach Shopify (still locally_modified after the collection sync) are
    left out; without shop_counts (the preview) the same rows show."""
    gid = {r["handle"]: r["shopify_collection_id"] for r in ROWS}
    shop = {g: 5 for g in gid.values()}
    shop[gid["oakley"]] = 0
    del shop[gid["vogue"]]
    rows = [dict(r, locally_modified=True) if r["handle"] == "idee" else r for r in ROWS]
    brands = _col(_top(gm.generate(gm.default_template(), rows, _counts(), shop)[0], "Shop by Brand"), "Brands")
    assert not {"Oakley", "Vogue", "Idee"} & set(brands) and "Carrera" in brands
    hidden = {h["handle"]: h["reason"] for h in gm.generate(gm.default_template(), rows, _counts(), shop)[1]}
    assert hidden["oakley"] == "Shopify shows no products in it" and hidden["vogue"] == "not found on Shopify"
    assert "latest rules" in hidden["idee"]
    assert {"Oakley", "Vogue", "Idee"} <= set(_col(_top(_gen(rows=rows)[0], "Shop by Brand"), "Brands"))


# ---------------------------------------------------------------------------
# Regenerate + push (fake Shopify)
# ---------------------------------------------------------------------------


class _Audit:
    def __init__(self):
        self.rows = []

    def create(self, row):
        self.rows.append(row)


LIVE_ITEMS = [
    {"id": "gid://shopify/MenuItem/1", "title": "Shop by Brand", "url": "/collections/all-brands", "type": "HTTP",
     "items": [{"id": "gid://shopify/MenuItem/2", "title": "Brands", "type": "HTTP",
                "items": [{"id": "gid://shopify/MenuItem/3", "title": "Carrera", "type": "COLLECTION"}]}]},
    {"id": "gid://shopify/MenuItem/4", "title": "About Us", "url": "/pages/our-story", "type": "HTTP", "items": []},
]


class _Shop:
    """Fake Shopify: one hand-made main-menu (on page 2 when ``pages=2``)
    and a productsCount for every IMS collection. read_fails / push_error."""

    def __init__(self, has_menu=True, read_fails=False, push_error=None, pages=1):
        self.calls = []
        self.updates = []
        self.read_fails = read_fails
        self.push_error = push_error
        self.pages = pages
        self.products = {r["shopify_collection_id"]: 5 for r in ROWS}
        self.menus = [{"id": SHOP_MENU_GID, "handle": "main-menu", "title": "Boost Mega Menu",
                       "items": copy.deepcopy(LIVE_ITEMS)}] if has_menu else []

    async def __call__(self, db, query, variables):  # noqa: ARG002
        import re

        name = re.search(r"(?:query|mutation)\s+(\w+)", query).group(1)
        self.calls.append(name)
        if name == "imsMenuByHandle":
            if self.read_fails:
                return {"errors": [{"message": "Throttled"}]}
            if self.pages == 2 and not variables.get("after"):
                return {"data": {"menus": {"nodes": [{"id": "gid://shopify/Menu/1", "handle": "footer", "items": []}],
                                           "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}
            return {"data": {"menus": {"nodes": self.menus, "pageInfo": {"hasNextPage": False}}}}
        if name == "imsCollectionsRead":
            return {"data": {"collections": {"nodes": [{"id": g, "handle": "x", "productsCount": {"count": n}}
                                                       for g, n in self.products.items()],
                                             "pageInfo": {"hasNextPage": False}}}}
        if name == "imsMenuUpdate":
            if self.push_error:
                return {"data": {"menuUpdate": {"menu": None, "userErrors": [{"field": "items", "message": self.push_error}]}}}
            self.updates.append(variables)
            return {"data": {"menuUpdate": {"menu": {"id": variables["id"], "handle": variables["handle"]}, "userErrors": []}}}
        if name == "imsMenuCreate":
            return {"data": {"menuCreate": {"menu": {"id": "gid://shopify/Menu/9", "handle": variables["handle"]},
                                            "userErrors": []}}}
        raise AssertionError(name)


@pytest.fixture
def world(monkeypatch):
    from api import dependencies as deps
    from api.services import collection_membership as cm

    db = StrictDB()
    db.seed("ecom_menus", [dict(gm.make_template_doc(), edit_rev="r1")])
    db.seed("ecom_collections", [dict(r, collection_id=r["handle"]) for r in ROWS])
    db.seed(gm.BACKUPS, [])
    state = {"counts": _counts(), "facts": [{"sku": "ANY"}]}

    def _facts(db, strict=False):
        if isinstance(state["facts"], Exception):
            if strict:
                raise state["facts"]
            return []
        return state["facts"]

    monkeypatch.setattr(cm, "online_facts", _facts)
    monkeypatch.setattr(cm, "counts_by_handle", lambda db, facts=None, collections=None: state["counts"])
    audit = _Audit()
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit)
    return db, state, audit


def _live(monkeypatch, shop):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", shop)


def _doc(db):
    return db["ecom_menus"].find_one({"handle": "main-menu"})


def _titles(items):
    return [i["title"] for i in items]


def test_dark_is_simulated_never_the_network_and_not_re_audited(world, monkeypatch):
    db, _state, audit = world

    async def _boom(db, query, variables):  # pragma: no cover
        raise AssertionError("DARK must never call Shopify")

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _boom)
    out = _run(gm.sync_generated_menus(db))
    assert out["menus"][0]["mode"] == "SIMULATED" and out["menus"][0]["ok"] is True
    doc = _doc(db)
    assert doc["items"] and doc["last_generated_at"] and not doc.get("pushed_hash")
    assert len(audit.rows) == 1
    _run(gm.sync_generated_menus(db))
    assert len(audit.rows) == 1  # the same tree is not simulated (audited) again


def test_a_dark_run_then_live_pushes_exactly_once(world, monkeypatch):
    """The simulated-hash dedupe is DARK-only: once the gates open, the tree
    a DARK run already simulated is pushed LIVE (once), not skipped."""
    db, _state, _audit = world
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    _run(gm.sync_generated_menus(db))
    assert _doc(db).get("simulated_hash")
    shop = _Shop()
    _live(monkeypatch, shop)
    _run(gm.sync_generated_menus(db))
    _run(gm.sync_generated_menus(db))
    assert shop.calls.count("imsMenuUpdate") == 1
    doc = _doc(db)
    assert doc["pushed_hash"] == doc["generated_hash"]


def test_first_live_push_adopts_the_existing_menu_after_backing_it_up(world, monkeypatch):
    db, _state, audit = world
    shop = _Shop()
    _live(monkeypatch, shop)
    out = _run(gm.sync_generated_menus(db))
    assert out["menus"][0]["ok"] is True and out["menus"][0]["mode"] == "LIVE"
    assert "imsMenuCreate" not in shop.calls  # never a second menu with the same handle
    assert shop.calls.index("imsMenuByHandle") < shop.calls.index("imsMenuUpdate")
    assert shop.updates[0]["id"] == SHOP_MENU_GID and shop.updates[0]["handle"] == "main-menu"
    assert shop.updates[0]["title"] == "Boost Mega Menu"  # the shop's own name kept
    backups = list(db[gm.BACKUPS].find({}))
    assert len(backups) == 1 and backups[0]["items"] == LIVE_ITEMS
    doc = _doc(db)
    assert doc["shopify_menu_id"] == SHOP_MENU_GID and doc["pushed_hash"] == doc["generated_hash"]
    assert audit.rows[-1]["details"]["mode"] == "LIVE"


def test_adoption_keeps_the_live_heading_urls_and_item_ids(world, monkeypatch):
    """The first push takes the live headings' URLs into the template (no
    guessed '#' / '/pages/about-us'), and every unchanged item (same title
    path) keeps its live id, so per-item mega-menu settings survive."""
    db, _state, _audit = world
    shop = _Shop()
    _live(monkeypatch, shop)
    _run(gm.sync_generated_menus(db))
    sections = {s["title"]: s for s in _doc(db)["template"]["sections"]}
    assert sections["Shop by Brand"]["url"] == "/collections/all-brands"
    assert sections["About Us"]["url"] == "/pages/our-story"
    sent = {i["title"]: i for i in shop.updates[0]["items"]}
    assert sent["Shop by Brand"]["id"] == "gid://shopify/MenuItem/1"
    assert sent["Shop by Brand"]["url"] == "/collections/all-brands"
    brands = sent["Shop by Brand"]["items"][0]
    assert brands["id"] == "gid://shopify/MenuItem/2"
    by_title = {c["title"]: c for c in brands["items"]}
    assert by_title["Carrera"]["id"] == "gid://shopify/MenuItem/3" and "id" not in by_title["Ray-Ban"]
    assert sent["About Us"]["id"] == "gid://shopify/MenuItem/4" and "id" not in sent["New Arrivals"]
    assert "shopify_item_id" not in str(_doc(db)["items"])  # ids ride the push, never the stored tree


def test_the_adopt_read_pages_until_it_finds_the_handle(world, monkeypatch):
    db, _state, _audit = world
    shop = _Shop(pages=2)
    _live(monkeypatch, shop)
    _run(gm.sync_generated_menus(db))
    assert shop.calls.count("imsMenuByHandle") == 2 and "imsMenuCreate" not in shop.calls
    assert shop.updates[0]["id"] == SHOP_MENU_GID


def test_push_only_when_the_menu_changed_and_back_up_every_overwrite(world, monkeypatch):
    db, state, _audit = world
    shop = _Shop()
    _live(monkeypatch, shop)
    _run(gm.sync_generated_menus(db))
    assert shop.calls.count("imsMenuUpdate") == 1
    state["counts"] = _counts({"ray-ban": 139})  # a count moved; the menu did not
    reads = len(shop.calls)
    out = _run(gm.sync_generated_menus(db))
    assert out["menus"][0]["changed"] is False and shop.calls.count("imsMenuUpdate") == 1
    assert len(shop.calls) == reads  # an unchanged menu costs no Shopify read at all
    state["counts"] = _counts({"eyeglasses": 2})  # Eyeglasses got products
    _run(gm.sync_generated_menus(db))
    assert shop.calls.count("imsMenuUpdate") == 2
    assert _titles(shop.updates[-1]["items"])[2] == "Eyeglasses"
    assert len(list(db[gm.BACKUPS].find({}))) == 2  # the tree it replaced, every LIVE overwrite


def test_no_backup_no_overwrite_and_retried_next_run(world, monkeypatch):
    db, _state, _audit = world
    shop = _Shop(read_fails=True)
    _live(monkeypatch, shop)
    out = _run(gm.sync_generated_menus(db))
    assert out["menus"][0]["ok"] is False and "backed up" in out["menus"][0]["error"]
    assert "imsMenuUpdate" not in shop.calls and "imsMenuCreate" not in shop.calls
    assert not _doc(db).get("pushed_hash")
    shop.read_fails = False
    out = _run(gm.sync_generated_menus(db))  # the next run retries
    assert out["menus"][0]["ok"] is True and shop.calls.count("imsMenuUpdate") == 1


def test_a_failed_push_is_recorded_and_retried(world, monkeypatch):
    db, _state, _audit = world
    shop = _Shop(push_error="Menu items limit reached")
    _live(monkeypatch, shop)
    out = _run(gm.sync_generated_menus(db))
    assert out["menus"][0]["ok"] is False
    doc = _doc(db)
    assert doc["last_push"]["ok"] is False and "limit" in str(doc["last_push"]["error"])
    assert not doc.get("pushed_hash")
    shop.push_error = None
    assert _run(gm.sync_generated_menus(db))["menus"][0]["ok"] is True


def test_a_shop_without_the_menu_gets_it_created_once(world, monkeypatch):
    db, _state, _audit = world
    shop = _Shop(has_menu=False)
    _live(monkeypatch, shop)
    _run(gm.sync_generated_menus(db))
    assert shop.calls.count("imsMenuCreate") == 1
    _run(gm.sync_generated_menus(db, force=True))
    assert shop.calls.count("imsMenuCreate") == 1 and shop.calls.count("imsMenuUpdate") == 1


# --- the About-Us-only wipe (review round 1, BLOCK x2) ----------------------


def test_an_unreadable_catalogue_pushes_nothing_and_keeps_the_last_menu(world, monkeypatch):
    """listing_facts raises (Mongo blip): the run fails, no menuUpdate is
    sent, the stored items and the last push stand -- it never reads as "no
    products" and wipes the website menu down to About Us."""
    db, state, _audit = world
    shop = _Shop()
    _live(monkeypatch, shop)
    _run(gm.sync_generated_menus(db))
    before = _doc(db)
    state["facts"] = RuntimeError("Mongo down")
    state["counts"] = _counts({h: 0 for h in COUNTS})  # what a fail-soft read would have counted
    out = _run(gm.sync_generated_menus(db))
    assert "unreadable" in out["error"] and shop.calls.count("imsMenuUpdate") == 1
    after = _doc(db)
    assert after["items"] == before["items"] and after["pushed_hash"] == before["pushed_hash"]
    # the "Regenerate & push now" path reads strictly too
    res = _run(gm.regenerate_and_push(db, dict(after), force=True))
    assert res["ok"] is False and shop.calls.count("imsMenuUpdate") == 1


@pytest.mark.parametrize("case", ["no_facts", "no_collections", "half_lost"])
def test_a_suspicious_automatic_push_is_refused_until_a_human_forces_it(world, monkeypatch, case):
    """Each guard on its own: no listing read (the counts still look fine),
    a tree with no collection link (on a menu never pushed before, so the
    drop rule cannot catch it), and more than half the links gone at once."""
    db, state, _audit = world
    shop = _Shop()
    _live(monkeypatch, shop)
    pushed = 0
    if case != "no_collections":
        _run(gm.sync_generated_menus(db))
        pushed = 1
    assert shop.calls.count("imsMenuUpdate") == pushed
    if case == "no_facts":
        state["facts"] = []
        state["counts"] = _counts({"eyeglasses": 1})  # changed, so a push is due
    elif case == "no_collections":
        state["counts"] = _counts({h: 0 for h in COUNTS})
    else:  # more than half the collection links gone at once
        keep = {"sunglasses", "mens-sunglasses", "ray-ban", "carrera"}
        state["counts"] = _counts({h: (n if h in keep else 0) for h, n in COUNTS.items()})
    out = _run(gm.sync_generated_menus(db))
    assert out["menus"][0]["ok"] is False and "refused" in out["menus"][0]["error"], out
    assert shop.calls.count("imsMenuUpdate") == pushed
    assert _doc(db)["last_push"]["ok"] is False
    _run(gm.sync_generated_menus(db))  # retried (and refused) next run, never sent
    assert shop.calls.count("imsMenuUpdate") == pushed
    res = _run(gm.regenerate_and_push(db, _doc(db), force=True))  # the human press
    assert res["ok"] is True and shop.calls.count("imsMenuUpdate") == pushed + 1


# ---------------------------------------------------------------------------
# The scheduled live sync regenerates it (and the membership view)
# ---------------------------------------------------------------------------


def _live_sync_world(monkeypatch, db):
    from tests.test_shopify_live_sync import _Cache, _Conn
    from api import dependencies as deps
    from api.services import policy_engine as pe

    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(deps, "get_audit_repository", lambda: _Audit())
    monkeypatch.setattr(pe, "cache", _Cache())
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": db[name])
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)

    async def _boom(db, query, variables):  # pragma: no cover
        raise AssertionError("DARK sync must never hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)


def test_the_live_sync_run_regenerates_the_menu(monkeypatch):
    from api.services import collection_membership as cm
    from api.services import shopify_live_sync as ls

    db = StrictDB()
    db.seed("ecom_menus", [gm.make_template_doc()])
    db.seed("ecom_collections", [dict(r, collection_id=r["handle"]) for r in ROWS])
    _live_sync_world(monkeypatch, db)
    monkeypatch.setattr(cm, "counts_by_handle", lambda db, facts=None, collections=None: _counts())
    run = _run(ls.sync_live_products(db, trigger="scheduled", actor="scheduler:shopify-live-sync"))
    assert run["menus"]["menus"][0]["mode"] == "SIMULATED"
    assert _doc(db)["items"] and _doc(db)["last_generated_at"]


def test_the_live_sync_run_refreshes_the_membership_view(monkeypatch):
    """One live listing + one registry row: after sync_live_products the
    collection_products cache holds the listing's SKU for that collection
    (and the menu read the same facts)."""
    from api.services import shopify_live_sync as ls

    db = StrictDB()
    db.seed("ecom_menus", [gm.make_template_doc()])
    db.seed("catalog_products", [{"id": "RB1", "sku": "RB-1", "brand": "Ray-Ban", "category": "SUNGLASS",
                                  "mrp": 5000, "ecom": {"shopify_product_id": "gid://shopify/Product/1"}}])
    db.seed("ecom_collections", [{"collection_id": "c-sun", "handle": "sunglasses", "ims_key": "type:sunglasses",
                                  "collection_type": "SMART", "shopify_collection_id": "gid://shopify/Collection/1",
                                  "rules": [{"field": "product_type", "relation": "EQUALS", "value": "Sunglass"}]}])
    _live_sync_world(monkeypatch, db)
    run = _run(ls.sync_live_products(db, trigger="scheduled", actor="scheduler:shopify-live-sync"))
    assert [r["sku"] for r in db["collection_products"].find({"collection_id": "c-sun"})] == ["RB-1"]
    assert "error" not in run["menus"], run["menus"]


# ---------------------------------------------------------------------------
# Routes: hand edits refused, template validated, preview served, push
# ---------------------------------------------------------------------------


@pytest.fixture
def routed(monkeypatch):
    from database.connection import MockDatabase
    from database.repositories import EcomMenuRepository
    from api.routers import online_store_menus as osm
    from api.services import collection_membership as cm

    db = MockDatabase()
    doc = gm.make_template_doc()
    db["ecom_menus"].insert_one(dict(doc))
    db["ecom_menus"].insert_one({"menu_id": "footer-1", "handle": "footer", "title": "Footer",
                                 "items": [{"id": "n1", "title": "Contact", "item_type": "HTTP", "url": "/c",
                                            "children": []}]})
    for r in ROWS:
        db["ecom_collections"].insert_one(dict(r, collection_id=r["handle"]))
    monkeypatch.setattr(osm, "_get_db", lambda: db)
    monkeypatch.setattr(osm, "_repo", lambda: EcomMenuRepository(db["ecom_menus"]))
    state = {"counts": _counts()}
    monkeypatch.setattr(cm, "counts_by_handle", lambda db, facts=None, collections=None: state["counts"])
    monkeypatch.setattr(cm, "online_facts", lambda db, strict=False: [{"sku": "ANY"}])
    return db, doc["menu_id"], state


_HAND_EDITS = [
    ("put", "", {"title": "Renamed"}),
    ("delete", "", None),
    ("post", "/items", {"item": {"title": "x"}}),
    ("put", "/items/reorder", {"items": []}),
    ("put", "/items/{item}/move", {"new_parent_id": None, "position": 0}),
    ("put", "/items/{item}", {"title": "x"}),
    ("delete", "/items/{item}", None),
]


@pytest.mark.parametrize("method,path,body", _HAND_EDITS)
def test_generated_items_are_never_hand_edited(client, auth_headers, routed, method, path, body):
    """Every mutating menus route: 409 for the generated menu, 200 for the
    static footer (still the old router's)."""
    _db, mid, _state = routed
    from api.main import app

    known = {(m, r.path) for r in app.routes for m in getattr(r, "methods", ())}
    for menu_id, item, want in ((mid, "n1", 409), ("footer-1", "n1", 200)):
        url = f"/api/v1/online-store/menus/{menu_id}" + path.replace("{item}", item)
        tmpl = "/api/v1/online-store/menus/{menu_id}" + path.replace("{item}", "{item_id}")
        assert (method.upper(), tmpl) in known, tmpl
        kw = {"json": body} if body is not None else {}
        r = client.request(method.upper(), url, headers=auth_headers, **kw)
        assert r.status_code == want, (method, url, r.status_code, r.text)


def test_preview_and_template_routes(client, auth_headers, routed):
    db, mid, _state = routed
    r = client.get("/api/v1/online-store/menus/generated", headers=auth_headers)
    assert r.status_code == 200, r.text
    m = r.json()["menus"][0]
    assert _titles(m["preview"]["items"]) == ["New Arrivals", "Sunglasses", "Shop by Brand", "About Us"]
    assert any(h["handle"] == "eyeglasses" for h in m["preview"]["hidden"])
    bad = gm.default_template()
    bad["sections"][0]["kind"] = "banner"
    url = f"/api/v1/online-store/menus/generated/{mid}/template"
    assert client.put(url, json={"template": bad}, headers=auth_headers).status_code == 400
    t = gm.default_template()
    t["sections"] = [s for s in t["sections"] if s["title"] != "New Arrivals"]
    r = client.put(url, json={"template": t}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _titles(r.json()["menu"]["items"]) == ["Sunglasses", "Shop by Brand", "About Us"]


@pytest.fixture
def pushed(monkeypatch, routed):
    """The push router over the same MockDatabase, with a chained audit."""
    from api import dependencies as deps

    db, mid, state = routed

    class _Conn:
        is_connected = True

    conn = _Conn()
    conn.db = db
    audit = _Audit()
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit)
    return db, mid, state


def test_regenerate_and_push_now_adopts_backs_up_and_rebuilds(client, auth_headers, pushed, monkeypatch):
    """POST /push/menu/{id} on the generated menu (no shopify_menu_id yet),
    LIVE: the shop's main-menu is read BEFORE the update of ITS gid, never
    a create, one backup row, and the stored items are rebuilt from the
    counts NOW (Eyeglasses just got a product)."""
    db, mid, state = pushed
    shop = _Shop()
    _live(monkeypatch, shop)
    state["counts"] = _counts({"eyeglasses": 3})
    r = client.post(f"/api/v1/online-store/push/menu/{mid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["ok"] is True and r.json()["result"]["mode"] == "LIVE"
    assert shop.calls.index("imsMenuByHandle") < shop.calls.index("imsMenuUpdate")
    assert "imsMenuCreate" not in shop.calls and shop.updates[0]["id"] == SHOP_MENU_GID
    assert len(list(db[gm.BACKUPS].find({}))) == 1
    stored = db["ecom_menus"].find_one({"menu_id": mid})
    assert "Eyeglasses" in _titles(stored["items"]) and "Eyeglasses" in _titles(shop.updates[0]["items"])


def test_push_all_rebuilds_the_generated_menu_through_its_own_path(client, auth_headers, pushed, monkeypatch):
    """push-all: the generated doc is never sent through the plain
    push_menu loop (only its own path, once), the response carries
    generated_menus, and a collections-only press runs the hook too."""
    db, mid, _state = pushed
    db["ecom_menus"].update_one({"menu_id": mid}, {"$set": {"locally_modified": True}})
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    sent = []
    real = shopify_push.push_menu

    async def _spy(db, menu):
        sent.append((menu.get("menu_id"), bool(menu.get("template"))))
        return await real(db, menu)

    monkeypatch.setattr(shopify_push, "push_menu", _spy)
    for entities in ("menus", "collections"):
        sent.clear()
        db["ecom_menus"].update_one({"menu_id": mid}, {"$unset": {"simulated_hash": ""}})
        r = client.post(f"/api/v1/online-store/push/all-pending?entities={entities}", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["generated_menus"]["menus"][0]["handle"] == "main-menu", body["generated_menus"]
        assert [m for m, _t in sent].count(mid) == 1, (entities, sent)
        assert not any(m == mid for res in body["results"] for m in [res.get("target_id")])


def test_status_and_preview_report_the_generated_menu(client, auth_headers, pushed, monkeypatch):
    db, mid, _state = pushed
    url = "/api/v1/online-store/menus/generated"
    assert client.get(url, headers=auth_headers).json()["menus"][0]["preview"]["changed_since_push"] is True
    shop = _Shop()
    _live(monkeypatch, shop)
    assert client.post(f"/api/v1/online-store/push/menu/{mid}", headers=auth_headers).json()["result"]["ok"]
    assert client.get(url, headers=auth_headers).json()["menus"][0]["preview"]["changed_since_push"] is False
    st = client.get("/api/v1/online-store/push/status", headers=auth_headers)
    assert st.status_code == 200, st.text
    g = st.json()["generated_menus"][0]
    assert g["handle"] == "main-menu" and g["item_count"] == 4 and g["hidden_count"] >= 1
    assert g["last_push"]["ok"] is True and g["last_push"]["mode"] == "LIVE"


def test_routes_are_catalogued():
    from api.services import rbac_policy as rbac

    assert rbac.check_access("GET", "/api/v1/online-store/menus/generated", ["CATALOG_MANAGER"]) is True
    url = "/api/v1/online-store/menus/generated/x/template"
    assert rbac.check_access("PUT", url, ["ADMIN"]) is True
    assert rbac.check_access("PUT", url, ["CATALOG_MANAGER"]) is False


# ---------------------------------------------------------------------------
# The seed script
# ---------------------------------------------------------------------------


def _seed_module():
    import importlib.util

    path = os.path.join(os.path.dirname(HERE), "scripts", "seed_generated_main_menu.py")
    spec = importlib.util.spec_from_file_location("seed_generated_main_menu", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_seed_is_a_dry_run_by_default_and_refuses_a_second_doc(monkeypatch):
    from api.services import collection_membership as cm

    monkeypatch.setattr(cm, "online_facts", lambda db, strict=False: [{"sku": "ANY"}])
    monkeypatch.setattr(cm, "counts_by_handle", lambda db, facts=None, collections=None: _counts())
    seed = _seed_module()
    db = StrictDB()
    db.seed("ecom_menus", [])
    db.seed("ecom_collections", [dict(r, collection_id=r["handle"]) for r in ROWS])
    assert seed.run(db, apply=False) == {"created": False, "reason": "dry-run"}
    assert list(db["ecom_menus"].find({})) == []
    made = seed.run(db, apply=True)
    assert made["created"] is True
    doc = _doc(db)
    assert doc["generated"] is True and doc["items"] and not doc.get("shopify_menu_id")
    assert seed.run(db, apply=True) == {"created": False, "reason": "exists"}
    assert len(list(db["ecom_menus"].find({}))) == 1
