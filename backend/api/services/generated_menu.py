"""
IMS 2.0 - The self-maintaining website menu  (owner 2026-10-29)
==============================================================
"Generate menus directly from collections that have products, rather than
static pushes." The website's main menu is an ``ecom_menus`` doc flagged
``generated``: it holds a TEMPLATE (ordered sections) that the owner / an
admin edits, and IMS fills it from the IMS collection registry every time
the collections can have changed:

  * at the end of every scheduled / manual live sync run
    (services/shopify_live_sync.sync_live_products), right after the
    collection sync;
  * after a push-all that touched collections or menus;
  * on the Menus page's "Regenerate & push now".

A collection appears in the menu only while it has products ON THE WEBSITE
(services/collection_membership -- the one membership rule) and exists on
Shopify; an empty one is left out and comes back by itself when it gets a
product. Generated items are never hand-edited (the menus router refuses).

TEMPLATE  ``{"sections": [section, ...]}`` -- a section is one of

  {"kind": "collection", "title", "handle"}
      one top-level link to a collection (New Arrivals, Eyeglasses).
  {"kind": "links", "title", "url"?, "links": [{"title", "url"}, ...]}
      static links under a heading (About Us), shown exactly as written.
  {"kind": "panel", "title", "handle"?, "url"?, "groups": [group, ...]}
      a mega-menu panel (Sunglasses, Shop by Brand); ``handle`` makes the
      heading itself a collection link.

  group  {"title", "source", "category"?, "limit"?, "links"?: [{title,url}]}
      source  gender      Men's, Women's, Unisex, Kids' (fixed order), Polarized
              shape       by product count, most first (ties A-Z)
              price       the price bands, lowest first
              top_brands  the ``limit`` (default 10) brands with most products
                          (ties A-Z)
              brands_az   every brand with products, A-Z
      ``category`` (default "sunglasses") scopes gender / shape; ``links``
      are static links appended after the generated items (Shop all).

PUSH  only when the generated tree differs from what was last pushed (a
stable content hash on the doc). The FIRST LIVE overwrite of the hand-made
website menu is only ever a person's "Regenerate & push now": until then an
automatic run records "waiting for the first push by a person" and sends
nothing. LIVE: the shop's menu with the same handle is read first -- the
person's first push ADOPTS it (menuUpdate of its gid, its name and its
heading URLs; never a second menu with the same handle; a menu recreated on
Shopify is re-adopted only by a person) -- and every LIVE overwrite saves
the tree it replaces in ``ecom_menu_backups`` (scripts/
restore_main_menu_backup.py puts it back) and keeps the live item ids of
unchanged items. Before a LIVE push the tree is rebuilt with Shopify's own
productsCount (a collection Shopify shows empty or lacks is left out, and
stays out of the preview until a later read sees it filled; a partial read
pushes nothing). A push is refused when IMS read no listing or the tree has
no collection link (even for a person), and an AUTOMATIC one when it would
lose more than half of the last pushed collection links. The listings are
read STRICTLY: an unreadable catalogue skips the run and the last pushed
menu stands. DARK / SIMULATED vs LIVE is decided once per push
(``_live_or_reason``); every attempt writes the same chained audit row as
the other pushes. A failed or refused push is recorded on the doc
(``last_push``) and retried on the next run.

The confirm a person sees is the IMS-only tree: when Shopify's counts would
take away more than half of those links, the push is refused EVEN for the
press ("Shopify shows X of N collections empty - not pushed"). A collection
the same run just pushed new rules for is still being counted by Shopify
for a while, so an AUTOMATIC run whose tree its zero count would change
waits for the next run instead of churning the live menu.

KNOWN LIMIT: an unchanged menu (generated hash == pushed hash, nothing
hidden by the last read) costs no Shopify read, so a linked collection that
Shopify alone empties stays linked until the menu next changes or a person
presses "Regenerate & push now" (both re-read the counts).

No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

MENUS = "ecom_menus"
BACKUPS = "ecom_menu_backups"
GENERATED = "generated"
MAIN_MENU = "main-menu"
TOP_BRANDS_DEFAULT = 10
SOURCES = ("gender", "shape", "price", "top_brands", "brands_az")
SECTION_KINDS = ("collection", "links", "panel")
_GENDER_ORDER = ("men", "women", "unisex", "kids")
_GENDER_LABEL = {"men": "Men's", "women": "Women's", "unisex": "Unisex", "kids": "Kids"}
SCHEDULER_USER = {"user_id": "scheduler:shopify-live-sync"}
SHOP_EMPTY = "Shopify shows no products in it"
SHOP_MISSING = "not found on Shopify"
NOT_PUBLISHED = "not published on the website"
RETIRED_REASON = "retired: deleted from the website"
BACKUPS_KEPT = 20  # plus every adoption backup (the owner's hand-made menu)
WAITING_FIRST = "waiting for the first push by a person (Regenerate & push now)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The approved design (owner 2026-10-19 Gem Opticians-style mega menu; the
# About Us links exactly as the live menu has them on 2026-10-29).
# ---------------------------------------------------------------------------


def default_template() -> Dict[str, Any]:
    return {
        "sections": [
            {"kind": "collection", "title": "New Arrivals", "handle": "new-arrivals"},
            {
                "kind": "panel", "title": "Sunglasses", "handle": "sunglasses",
                "groups": [
                    {"title": "By Gender", "source": "gender", "category": "sunglasses",
                     "links": [{"title": "Shop all", "url": "/collections/sunglasses"}]},
                    {"title": "By Shape", "source": "shape", "category": "sunglasses"},
                    {"title": "By Price", "source": "price"},
                    {"title": "Top 10 Brands", "source": "top_brands", "limit": TOP_BRANDS_DEFAULT},
                ],
            },
            {"kind": "collection", "title": "Eyeglasses", "handle": "eyeglasses"},
            {"kind": "panel", "title": "Shop by Brand", "url": "#",
             "groups": [{"title": "Brands", "source": "brands_az"}]},
            {
                "kind": "links", "title": "About Us", "url": "/pages/about-us",
                "links": [
                    {"title": "Our Story", "url": "/pages/about-us"},
                    {"title": "Book an Eye Test", "url": "/pages/book-eye-test-at-better-vision"},
                    {"title": "Pune Store", "url": "/pages/pune-gangadham"},
                    {"title": "Bokaro Store", "url": "/pages/bokaro-city-centre"},
                    {"title": "Dhanbad Store", "url": "/pages/dhanbad-bank-more"},
                    {"title": "Genuine Ray-Ban Check", "url": "/pages/how-to-check-authentic-ray-ban"},
                    {"title": "Contact Us", "url": "/pages/contact-us"},
                    {"title": "Track Your Order", "url": "https://bettervision.shiprocket.co/"},
                ],
            },
        ]
    }


def _unparseable(url: Any) -> bool:
    try:
        urlsplit(str(url or "").strip())
        return False
    except ValueError:
        return True


def validate_template(template: Any) -> List[str]:
    """Every problem with a template (empty when it can be generated). Pure."""
    errs: List[str] = []
    sections = (template or {}).get("sections") if isinstance(template, dict) else None
    if not isinstance(sections, list) or not sections:
        return ["the template needs at least one section"]
    for i, s in enumerate(sections, 1):
        where = f"section {i}"
        if not isinstance(s, dict) or s.get("kind") not in SECTION_KINDS:
            errs.append(f"{where}: kind must be one of {', '.join(SECTION_KINDS)}")
            continue
        if not str(s.get("title") or "").strip():
            errs.append(f"{where}: needs a title")
        if s["kind"] == "collection" and not str(s.get("handle") or "").strip():
            errs.append(f"{where}: needs the collection handle")
        if s["kind"] in ("links", "panel") and _unparseable(s.get("url")):
            errs.append(f"{where}: every link needs a valid url")
        def _lst(x: Any) -> List[Any]:
            return x if isinstance(x, list) else []
        urls = list(_lst(s.get("links")))
        urls += [lk for g in _lst(s.get("groups")) if isinstance(g, dict) for lk in _lst(g.get("links"))]
        for link in urls:
            if isinstance(link, dict) and _unparseable(link.get("url")):
                errs.append(f"{where}: every link needs a valid url")
                break
        if s["kind"] == "links":
            links = s.get("links")
            if not isinstance(links, list) or not links:
                errs.append(f"{where}: needs at least one link")
            for link in links or []:
                if not str((link or {}).get("title") or "").strip() or not str((link or {}).get("url") or "").strip():
                    errs.append(f"{where}: every link needs a title and a url")
        if s["kind"] == "panel":
            groups = s.get("groups")
            if not isinstance(groups, list) or not groups:
                errs.append(f"{where}: needs at least one group")
            for g in groups or []:
                if not isinstance(g, dict) or g.get("source") not in SOURCES:
                    errs.append(f"{where}: a group's source must be one of {', '.join(SOURCES)}")
                    continue
                if not str(g.get("title") or "").strip():
                    errs.append(f"{where}: every group needs a title")
                if g.get("limit") is not None:
                    try:
                        if not 1 <= int(g["limit"]) <= 100:
                            raise ValueError
                    except (TypeError, ValueError):
                        errs.append(f"{where}: a group's limit must be 1-100")
                for link in g.get("links") or []:
                    if not str((link or {}).get("title") or "").strip() or not str((link or {}).get("url") or "").strip():
                        errs.append(f"{where}: every link needs a title and a url")
    return errs


# ---------------------------------------------------------------------------
# The generator. Pure: template + registry rows + live counts -> item tree.
# ---------------------------------------------------------------------------


def _coll_node(row: Dict[str, Any], title: str, count: int) -> Dict[str, Any]:
    return {
        "title": title, "item_type": "COLLECTION", "resource_id": row["shopify_collection_id"],
        "handle": row.get("handle"), "count": count, "generated": True,
    }


def _link_node(title: str, url: str) -> Dict[str, Any]:
    return {"title": title, "item_type": "HTTP", "url": url, "static": True}


def _count(row: Optional[Dict[str, Any]], counts: Dict[str, Dict[str, Any]]) -> int:
    return int((counts.get((row or {}).get("handle")) or {}).get("count") or 0)


def _collection_handle(url: str) -> Optional[str]:
    """The lowercased <h> of any URL whose path is /collections/<h>[/...]
    (any scheme/host, query or fragment), else None."""
    try:
        path = urlsplit(url.strip()).path
    except ValueError:  # e.g. 'http://[oops' -- not a collection link
        return None
    if not path.startswith("/collections/"):
        return None
    return path[len("/collections/"):].split("/", 1)[0].lower() or None


def _usable(
    row: Optional[Dict[str, Any]], counts: Dict[str, Dict[str, Any]],
    shop_counts: Optional[Dict[str, Any]] = None,
    shop_hidden: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """(shown, why hidden) for one collection row. ``shop_counts`` ({gid:
    Shopify productsCount}, read right before a LIVE push) also hides what
    Shopify itself shows empty or does not have -- its count for the live
    gid is what the website shows, even while newer rules wait to be pushed.
    Without a read, ``shop_hidden`` ({handle: reason}, what the last LIVE
    read hid) keeps those rows out, so the preview and the stored tree never
    link a collection the last push left out."""
    from agents.nexus_providers import _as_shopify_gid

    if row is None:
        return False, "no such collection in IMS"
    if _count(row, counts) <= 0:
        why = (counts.get(row.get("handle")) or {}).get("reason")
        return False, f"empty ({why})" if why else "no products on the website"
    if not row.get("shopify_collection_id"):
        return False, "not on Shopify yet"
    if row.get("push_problem"):
        return False, f"not on the website yet ({row['push_problem']})"
    if row.get("published") is False:
        return False, NOT_PUBLISHED
    if shop_counts is not None:
        gid = _as_shopify_gid(row["shopify_collection_id"], "Collection")
        if gid not in shop_counts:
            return False, SHOP_MISSING
        if not shop_counts[gid]:
            return False, SHOP_EMPTY
    elif shop_hidden and row.get("handle") in shop_hidden:
        return False, shop_hidden[row["handle"]]
    return True, ""


def _group_rows(group: Dict[str, Any], by_key: Dict[str, Dict[str, Any]], counts) -> List[Tuple[Dict[str, Any], str]]:
    """(collection row, menu label) for a group's source, in the group's
    order (deterministic: ties by name, then ims_key), BEFORE the hidden
    ones are dropped and before Top-N is cut."""
    from .shopify_push.collection_registry import PRICE_BANDS, _handle

    def n(row):
        return _count(row, counts)

    src = group["source"]
    cat = _handle(str(group.get("category") or "sunglasses"))
    if src == "gender":
        out = [(by_key.get(f"gender:{cat}:{g}"), _GENDER_LABEL[g]) for g in _GENDER_ORDER]
        out.append((by_key.get(f"polarized:{cat}"), "Polarized"))
        return [(r, label) for r, label in out if r is not None]
    if src == "shape":
        rows = [r for k, r in by_key.items() if k.startswith(f"shape:{cat}:")]
        rows.sort(key=lambda r: (-n(r), str(r["ims_key"]).rsplit(":", 1)[1], str(r["ims_key"])))
        return [(r, str(r["ims_key"]).rsplit(":", 1)[1].replace("-", " ").title()) for r in rows]
    if src == "price":
        return [(by_key[f"price:{h}"], by_key[f"price:{h}"].get("title") or t)
                for h, t, _lo, _hi in PRICE_BANDS if f"price:{h}" in by_key]
    rows = [r for k, r in by_key.items() if k.startswith("brand:")]
    if src == "top_brands":
        rows.sort(key=lambda r: (-n(r), str(r.get("title") or "").lower(), str(r["ims_key"])))
    else:  # brands_az
        rows.sort(key=lambda r: (str(r.get("title") or "").lower(), str(r["ims_key"])))
    return [(r, str(r.get("title") or "")) for r in rows]


def generate(
    template: Dict[str, Any],
    collections: List[Dict[str, Any]],
    counts: Dict[str, Dict[str, Any]],
    shop_counts: Optional[Dict[str, Any]] = None,
    shop_hidden: Optional[Dict[str, str]] = None,
    retired: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(items, hidden): the menu tree in the ecom_menus item shape (what
    shopify_push.menus.build_menu_items sends) and every collection left out
    with why (once per collection). ``collections`` are the live
    ecom_collections rows (collection_membership.live_collections: retired
    ones are already out; registry rows carry ``ims_key``), ``counts`` is
    collection_membership.counts_by_handle, ``shop_counts`` Shopify's own
    productsCount per gid (LIVE push only), ``shop_hidden`` what the last
    LIVE read hid, ``retired`` the handles of retired (deleted from the
    website) collections, which a template URL may still name. Pure and
    deterministic."""
    by_key = {str(c["ims_key"]): c for c in collections if c.get("ims_key")}
    by_handle = {str(c["handle"]): c for c in collections if c.get("handle")}
    by_lower = {h.lower(): c for h, c in by_handle.items()}
    gone = {str(h).lower() for h in retired or ()}
    items: List[Dict[str, Any]] = []
    hidden: List[Dict[str, Any]] = []

    def usable(row):
        return _usable(row, counts, shop_counts, shop_hidden)

    def hide(section: str, title: str, row: Optional[Dict[str, Any]], why: str, handle: Optional[str] = None):
        h = (row or {}).get("handle") or handle
        if h and any(x["handle"] == h for x in hidden):
            return  # a brand left out of Top N and of A-Z is listed once
        hidden.append({"section": section, "title": title, "handle": h, "reason": why})

    def gate_url(where: str, title: str, url: str) -> Optional[str]:
        """Every template URL to /collections/<h> of a collection IMS holds
        goes through the same gate as a generated link: None (and listed in
        hidden, with why) when the gate refuses it."""
        h = _collection_handle(url)
        if h in by_lower:
            ok, why = usable(by_lower[h])
            if not ok:
                hide(where, title, by_lower[h], why)
                return None
        elif h in gone:
            hide(where, title, None, RETIRED_REASON, h)
            return None
        return url

    def statics(where: str, links) -> List[Dict[str, Any]]:
        """Static links (Shop all, a links section's children): one to a
        collection the gate refused is dropped."""
        out = []
        for link in links or []:
            t = str(link["title"])
            url = gate_url(where, t, str(link["url"]))
            if url is not None:
                out.append(_link_node(t, url))
        return out

    for s in (template or {}).get("sections") or []:
        kind, title = s.get("kind"), str(s.get("title") or "").strip()
        if kind == "collection":
            row = by_handle.get(str(s.get("handle") or ""))
            ok, why = usable(row)
            if ok:
                items.append(_coll_node(row, title, _count(row, counts)))
            else:
                hide(title, title, row, why, s.get("handle"))
        elif kind == "links":
            node = _link_node(title, gate_url(title, title, str(s.get("url") or "#")) or "#")
            node["children"] = statics(title, s.get("links"))
            items.append(node)
        elif kind == "panel":
            columns = []
            for g in s.get("groups") or []:
                kids = []
                where = f"{title} > {g.get('title')}"
                top_n = int(g.get("limit") or TOP_BRANDS_DEFAULT) if g.get("source") == "top_brands" else None
                for row, label in _group_rows(g, by_key, counts):
                    ok, why = usable(row)
                    if top_n is not None:
                        # Top N of the brands the menu can SHOW: the cut comes
                        # after the "on the website" check, so N stays N. An
                        # empty brand is reported under the brands A-Z group.
                        if ok and len(kids) < top_n:
                            kids.append(_coll_node(row, label, _count(row, counts)))
                        elif not ok and _count(row, counts) > 0:
                            hide(where, label, row, why)
                        continue
                    if ok:
                        kids.append(_coll_node(row, label, _count(row, counts)))
                    else:
                        hide(where, label, row, why)
                if not kids:
                    continue  # a column with no collection with products is left out
                kids.extend(statics(where, g.get("links")))
                col = _link_node(str(g.get("title") or ""), "#")
                col.pop("static")
                col["children"] = kids
                columns.append(col)
            if not columns:
                hide(title, title, None, "no group has a collection with products", s.get("handle"))
                continue
            head_row = by_handle.get(str(s.get("handle") or "")) if s.get("handle") else None
            ok = usable(head_row)[0] if head_row is not None else False
            if ok:
                node = _coll_node(head_row, title, _count(head_row, counts))
            else:
                node = _link_node(title, gate_url(title, title, str(s.get("url") or "#")) or "#")
                node.pop("static")
            node["children"] = columns
            items.append(node)
    return items, hidden


def collection_nodes(items: List[Dict[str, Any]]) -> int:
    """How many collection links a menu tree holds (every depth). Pure."""
    return sum(int(n.get("item_type") == "COLLECTION") + collection_nodes(n.get("children") or []) for n in items or [])


def menu_hash(title: str, handle: str, items: List[Dict[str, Any]]) -> str:
    """Stable hash of what the push SENDS (build_menu_items' output), so a
    changed product count alone never re-pushes an unchanged menu."""
    from .shopify_push.menus import build_menu_items

    blob = json.dumps({"title": title, "handle": handle, "items": build_menu_items(items)},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The doc.
# ---------------------------------------------------------------------------


def generated_menus(db) -> List[Dict[str, Any]]:
    return [d for d in db[MENUS].find({GENERATED: True})]


def make_template_doc(template: Optional[Dict[str, Any]] = None, handle: str = MAIN_MENU,
                      title: str = "Main menu") -> Dict[str, Any]:
    """A new generated-menu doc (the seed script's insert)."""
    now = _now()
    return {
        "menu_id": str(uuid.uuid4()), "handle": handle, "title": title, GENERATED: True,
        "template": template or default_template(), "items": [], "hidden": [],
        "active": True, "is_default": handle == MAIN_MENU, "locally_modified": False,
        "created_at": now, "updated_at": now,
    }


def shop_hidden_of(doc: Dict[str, Any]) -> Dict[str, str]:
    """{handle: reason} the last LIVE read of Shopify hid (empty / missing
    there). Pure."""
    return {str(h["handle"]): str(h["reason"]) for h in doc.get("shop_hidden") or [] if h.get("handle")}


def regenerate(db, doc: Dict[str, Any], facts: List[Dict[str, Any]],
               shop_counts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fill ``doc`` from the registry + live membership over ``facts`` (a
    STRICT read by the caller -- an unreadable catalogue never becomes "no
    products") and save the result (items, hidden, generated_hash,
    last_generated_at). Returns the update."""
    return _save(db, doc, _build(db, doc, facts, shop_counts))


def retired_handles(db) -> set:
    """Handles of the collections IMS retired (deleted from the website)."""
    from . import collection_membership

    return {str(c["handle"]).lower() for c in db["ecom_collections"].find({collection_membership.RETIRED: True})
            if c.get("handle")}


def _save(db, doc: Dict[str, Any], upd: Dict[str, Any]) -> Dict[str, Any]:
    db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": upd})
    doc.update(upd)
    return upd


def _build(db, doc: Dict[str, Any], facts: List[Dict[str, Any]],
           shop_counts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What regenerate would save (nothing written)."""
    from . import collection_membership

    collections = collection_membership.live_collections(db)
    counts = collection_membership.counts_by_handle(db, facts, collections)
    stored = None if shop_counts is not None else shop_hidden_of(doc)
    items, hidden = generate(doc.get("template") or {}, collections, counts, shop_counts, stored,
                             retired=retired_handles(db))
    title = doc.get("title") or doc.get("handle") or MAIN_MENU
    upd: Dict[str, Any] = {
        "items": items, "hidden": hidden, "generated_hash": menu_hash(title, doc.get("handle") or MAIN_MENU, items),
        "last_generated_at": _now(),
    }
    if shop_counts is not None:
        upd["shop_hidden"] = [{"handle": h["handle"], "reason": h["reason"]} for h in hidden
                              if h.get("handle") and h["reason"] in (SHOP_EMPTY, SHOP_MISSING)]
    return upd


# ---------------------------------------------------------------------------
# Shopify side: read the live menu, adopt it, back it up, push.
# ---------------------------------------------------------------------------

_MENU_BY_HANDLE = """
query imsMenuByHandle($after: String) {
  menus(first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id handle title items { id title type url resourceId tags
      items { id title type url resourceId tags items { id title type url resourceId tags } } } }
  }
}
"""


async def _shop_menu(db, handle: str) -> Optional[Dict[str, Any]]:
    """The Shopify menu with ``handle`` (id, title, items with their ids) or
    None when the shop has none -- every page is read. Raises when a read
    fails: nothing is created or overwritten on a half-read shop."""
    from .shopify_push.transport import _graphql

    after = None
    while True:
        body = await _graphql(db, _MENU_BY_HANDLE, {"after": after})
        page = ((body or {}).get("data") or {}).get("menus")
        if page is None or (body or {}).get("errors"):
            raise RuntimeError(f"menus read failed: {(body or {}).get('errors')}")
        hit = next((m for m in page.get("nodes") or [] if m.get("handle") == handle), None)
        if hit is not None:
            return hit
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return None
        after = info.get("endCursor")


async def static_push_refusal(db, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The refused result for a LIVE push of a NON-generated menu that has no
    Shopify id (push_menu would menuCreate it) when the shop already has a
    menu with its handle -- a second "footer" -- or the menus cannot be read
    to check. None = push it (DARK, or it carries its id, or the handle is
    free)."""
    from .shopify_push import MODE_LIVE, PushResult, _live_or_reason

    handle = doc.get("handle") or ""
    if doc.get("generated") or doc.get("shopify_menu_id") or not _live_or_reason(db)[0]:
        return None
    try:
        shop = await _shop_menu(db, handle)
        why = None if shop is None else (f"refused: Shopify already has a menu with the handle {handle}; "
                                         "creating it again would make a duplicate")
    except Exception as e:  # noqa: BLE001 -- unchecked => not created
        why = f"refused: the Shopify menus could not be read to check the handle {handle}: {e}"
    if why is None:
        return None
    return PushResult(mode=MODE_LIVE, entity="menu", action="create", target_id=doc.get("menu_id"),
                      ok=False, error=why).to_dict()


def _key(title: Any) -> str:
    return str(title or "").strip().lower()


def _adopt(db, doc: Dict[str, Any], shop: Dict[str, Any]) -> None:
    """First LIVE push: take over the shop's menu with this handle -- its gid
    (menuUpdate, never a second menuCreate), its name, and the URLs its
    top-level headings link to today (a links / panel section whose title
    matches a live heading keeps that heading's URL instead of a guess)."""
    upd: Dict[str, Any] = {"shopify_menu_id": shop["id"]}
    if shop.get("title"):
        upd["title"] = shop["title"]  # keep the name the shop shows
    live_urls = {_key(i.get("title")): i.get("url") for i in shop.get("items") or [] if i.get("url")}
    template = copy.deepcopy(doc.get("template") or {})
    changed = False
    for sec in template.get("sections") or []:
        url = live_urls.get(_key(sec.get("title")))
        if sec.get("kind") in ("links", "panel") and url and sec.get("url") != url:
            sec["url"] = url
            changed = True
    if changed:
        upd["template"] = template
    db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": upd})
    doc.update(upd)


def _carry_ids(items: List[Dict[str, Any]], shop_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A copy of ``items`` where every node whose title path (Sunglasses >
    By Shape > Square) exists in the live menu carries that live item's id,
    so menuUpdate keeps the ids of unchanged items -- and their tags (per-item
    mega-menu settings an app keys on them survive). Each live id is used
    once."""
    pool: Dict[Tuple[str, ...], Dict[str, Any]] = {}

    def walk(nodes, path):
        for n in nodes or []:
            p = path + (_key(n.get("title")),)
            if n.get("id"):
                pool.setdefault(p, n)
            walk(n.get("items"), p)

    def attach(nodes, path):
        out = []
        for n in nodes or []:
            p = path + (_key(n.get("title")),)
            m = dict(n)
            if pool.get(p):
                live = pool.pop(p)
                m["shopify_item_id"] = live["id"]
                if live.get("tags"):
                    m["shopify_item_tags"] = list(live["tags"])
            if n.get("children"):
                m["children"] = attach(n["children"], p)
            out.append(m)
        return out

    walk(shop_items, ())
    return attach(items, ())


DROP_LIMIT = 0.5  # an auto push may not lose more than half the collection links


def _refusal(doc: Dict[str, Any], facts: List[Dict[str, Any]], force: bool = False) -> Optional[str]:
    """Why a live push of ``doc``'s tree is refused (None = send it). No
    listing read and no collection link at all are refused EVEN for the
    human press (that menu is About Us only -- a bad read, never a real
    catalogue). A menu that suddenly lost more than half its collection
    links is refused automatically; only "Regenerate & push now" (force)
    sends it, after the page's confirm."""
    nodes = collection_nodes(doc.get("items") or [])
    prev = int(doc.get("pushed_collection_nodes") or 0)
    if not facts:
        return "refused: IMS read no listing on the website (the menu would lose every collection)"
    if nodes == 0:
        return "refused: the generated menu has no collection with products"
    if not force and prev and nodes < prev * DROP_LIMIT:
        return f"refused: the menu would drop from {prev} to {nodes} collection links (more than half)"
    return None


async def _shop_counts(db) -> Dict[str, Any]:
    """{gid: productsCount} of every shop collection. Raises when the read
    is partial: an IMS collection Shopify returned without a count would
    otherwise read as "not on the website" and silently thin the menu."""
    from agents.nexus_providers import _as_shopify_gid

    from . import collection_membership
    from .shopify_push.collection_registry import shop_collections

    counts = {str(c.get("id")): c.get("products") for c in await shop_collections(db)}
    unread = [
        r.get("handle") for r in collection_membership.live_collections(db)
        if r.get("shopify_collection_id")
        and _as_shopify_gid(r["shopify_collection_id"], "Collection") in counts
        and counts[_as_shopify_gid(r["shopify_collection_id"], "Collection")] is None
    ]
    if unread:
        raise RuntimeError(f"Shopify returned no product count for {', '.join(sorted(map(str, unread)))}")
    return counts


def _record(db, doc: Dict[str, Any], result: Dict[str, Any], actor: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Save one push attempt on the doc (last_push; the pushed / simulated
    hash on success) and write its chained audit row."""
    from .shopify_live_sync import write_push_audit
    from .shopify_push import MODE_LIVE

    at = _now()
    upd: Dict[str, Any] = {"last_push": {"at": at, "mode": result.get("mode"), "ok": bool(result.get("ok")),
                                         "action": result.get("action"), "error": result.get("error"),
                                         "reason": result.get("reason"),
                                         "collection_links": result.get("collection_links")}}
    if result.get("ok") and result.get("mode") == MODE_LIVE:
        upd.update({"pushed_hash": doc.get("generated_hash"), "last_pushed_at": at, "locally_modified": False,
                    "pushed_collection_nodes": collection_nodes(doc.get("items") or [])})
        if result.get("shopify_id"):
            upd["shopify_menu_id"] = result["shopify_id"]
    elif result.get("ok"):
        upd["simulated_hash"] = doc.get("generated_hash")
    db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": upd})
    doc.update(upd)
    write_push_audit(result, actor or SCHEDULER_USER)
    return {"handle": doc.get("handle"), "changed": True, **upd["last_push"]}


async def push_generated(db, doc: Dict[str, Any], actor: Optional[Dict[str, Any]] = None,
                         force: bool = False, facts: Optional[List[Dict[str, Any]]] = None,
                         fresh: Optional[Iterable[str]] = None,
                         expected: Optional[int] = None) -> Dict[str, Any]:
    """Push ``doc``'s generated tree when it differs from what was last
    pushed (or ``force``: the human "Regenerate & push now"). Records
    ``last_push`` on the doc and an audit row. Never raises.

    The LIVE/DARK decision is made ONCE, here. DARK: a SIMULATED result
    built here (no network). LIVE: the FIRST overwrite of the hand-made
    website menu is only ever a person's press -- an automatic run waits.
    Then: read the shop's menu (adopt it on the press) and every
    collection's Shopify productsCount, rebuild hiding what Shopify shows
    empty, refuse a suspicious push (_refusal; more than half of the
    confirmed links gone on Shopify's count, even for a person), wait a run
    when only a collection pushed this run (``fresh`` handles) would change
    the tree, back the live tree up, then menuUpdate carrying the live item
    ids and tags. Any failure pushes nothing and is retried next run.
    ``expected``: the collection-link count the person's confirm showed --
    a press whose tree (after Shopify's counts) has any other number is
    refused, saying the real number, so the page asks again
    (``collection_links`` on the result: the links sent, or that would be)."""
    from .shopify_push import MODE_LIVE, MODE_SIMULATED, PushResult, _live_or_reason, push_menu
    from .shopify_push.menus import build_menu_items

    handle = doc.get("handle") or MAIN_MENU
    live, reason = _live_or_reason(db)
    if (not force and doc.get("generated_hash") and doc.get("generated_hash") == doc.get("pushed_hash")
            and not doc.get("shop_hidden")):
        # Unchanged, and the last LIVE read hid nothing: no Shopify read at
        # all. (While Shopify still shows a collection empty, every run
        # re-reads it so the link comes back once Shopify catches up.)
        return {"handle": handle, "changed": False}
    gid = doc.get("shopify_menu_id")
    if not live:
        if not force and doc.get("generated_hash") == doc.get("simulated_hash"):
            # DARK: this exact tree was already simulated -- no new audit row.
            return {"handle": handle, "changed": False, "mode": MODE_SIMULATED}
        payload = {"title": doc.get("title") or handle, "handle": handle,
                   "items": build_menu_items(doc.get("items") or [], with_ids=bool(gid))}
        return _record(db, doc, dict(PushResult(
            mode=MODE_SIMULATED, entity="menu", action="update" if gid else "create",
            target_id=doc.get("menu_id"), ok=True, shopify_id=gid, payload=payload, reason=reason,
        ).to_dict(), collection_links=collection_nodes(doc.get("items") or [])), actor)
    if not force and not (doc.get("pushed_hash") and gid):
        return _not_sent(db, doc, WAITING_FIRST)

    # What the page previewed and the person confirmed: the IMS-only tree.
    confirmed = collection_nodes(doc.get("items") or [])
    error, shop, send, links = None, None, doc, None
    try:
        if facts is None:
            from . import collection_membership

            facts = collection_membership.online_facts(db, strict=True)
        shop = await _shop_menu(db, handle)
        shop_counts = await _shop_counts(db)
    except Exception as e:  # noqa: BLE001 -- no read => no overwrite
        error = f"the listings or the existing Shopify menu could not be read or backed up: {e}"
    if error is None and shop is not None and shop.get("id") != gid:
        if gid and not force:
            error = ("refused: the website main-menu was recreated on Shopify; "
                     "press Regenerate & push now to adopt it")
        else:
            _adopt(db, doc, shop)  # a person's press: first push, or the menu was recreated
    if error is None:
        upd = _build(db, doc, facts, shop_counts)
        nodes = collection_nodes(upd["items"])
        late = sorted({h["handle"] for h in upd["hidden"]
                       if h.get("handle") in set(fresh or ()) and h["reason"] in (SHOP_EMPTY, SHOP_MISSING)})
        if confirmed and confirmed - nodes > confirmed * DROP_LIMIT:
            # Not saved: the stored tree (and the next confirm) stay the IMS one.
            error = (f"refused: Shopify shows {confirmed - nodes} of {confirmed} collections empty - "
                     "not pushed; try again in a few minutes")
        elif expected is not None and nodes != expected:
            # Not saved either: the person confirmed another number.
            links = nodes
            error = (f"refused: you confirmed {expected} collection links but the menu would have {nodes} now "
                     f"(Shopify shows {max(confirmed - nodes, 0)} of {confirmed} collections empty) - "
                     f"press again to confirm {nodes}")
        elif late and not force and upd["generated_hash"] != doc.get("pushed_hash"):
            return _not_sent(db, doc, f"waiting one run: Shopify is still counting the collections "
                                      f"just updated ({', '.join(late)}); pushed on the next run")
        else:
            _save(db, doc, upd)
            if not force and doc.get("generated_hash") == doc.get("pushed_hash"):
                return {"handle": handle, "changed": False}
            error = _refusal(doc, facts, force)
    if error is None and shop is not None:
        try:  # every LIVE overwrite saves the tree it replaces
            db[BACKUPS].insert_one({
                "backup_id": str(uuid.uuid4()), "menu_id": doc["menu_id"], "handle": shop.get("handle"), "shopify_menu_id": shop.get("id"),
                "title": shop.get("title"), "items": shop.get("items") or [], "taken_at": _now(),
                # The tree a person's adoption replaced: the hand-made menu
                # (scripts/restore_main_menu_backup.py --adopted).
                # Also when an earlier, refused press already adopted it
                # (no backup was taken then): the first backup of a menu
                # is always the hand-made tree.
                "adopted": shop.get("id") != gid
                or db[BACKUPS].find_one({"menu_id": doc["menu_id"], "adopted": True}) is None,
            })
            _prune_backups(db, shop.get("handle"))
            db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": {"backup_taken_at": _now()}})
            doc["backup_taken_at"] = _now()
            send = dict(doc, items=_carry_ids(doc.get("items") or [], shop.get("items") or []))
        except Exception as e:  # noqa: BLE001
            error = f"the existing Shopify menu could not be read or backed up: {e}"
    if error:
        result = PushResult(mode=MODE_LIVE, entity="menu", action="update", target_id=doc.get("menu_id"),
                            ok=False, error=error).to_dict()
    else:
        result = (await push_menu(db, send, live=True)).to_dict()
        links = collection_nodes(doc.get("items") or [])
    result["collection_links"] = links
    return _record(db, doc, result, actor)


def _not_sent(db, doc: Dict[str, Any], why: str) -> Dict[str, Any]:
    """Record a LIVE run that sent nothing on purpose (waiting for a
    person's first press, or one run for Shopify's counts): last_push says
    why; no audit row (nothing was attempted)."""
    from .shopify_push import MODE_LIVE

    last = {"at": _now(), "mode": MODE_LIVE, "ok": False, "action": None, "error": None,
            "reason": why, "waiting": True}
    db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": {"last_push": last}})
    doc["last_push"] = last
    return {"handle": doc.get("handle") or MAIN_MENU, "changed": False, **last}


def _prune_backups(db, handle: Optional[str]) -> None:
    """Keep every adoption backup and the BACKUPS_KEPT newest others."""
    rows = sorted(db[BACKUPS].find({"handle": handle}), key=lambda r: r.get("taken_at") or _now(), reverse=True)
    extra = [r for r in rows if not r.get("adopted")][BACKUPS_KEPT:]
    for r in extra:
        db[BACKUPS].delete_one({"backup_id": r["backup_id"]} if r.get("backup_id") else {"_id": r.get("_id")})


async def regenerate_and_push(db, doc: Dict[str, Any], actor: Optional[Dict[str, Any]] = None,
                              force: bool = False, facts: Optional[List[Dict[str, Any]]] = None,
                              fresh: Optional[Iterable[str]] = None,
                              expected: Optional[int] = None) -> Dict[str, Any]:
    """Rebuild ``doc`` from a STRICT read of the listings and push it when it
    changed. An unreadable catalogue changes nothing (the last generated
    and pushed menu stand) and is reported. Never raises."""
    from . import collection_membership

    doc.pop("_id", None)
    try:
        if facts is None:
            facts = collection_membership.online_facts(db, strict=True)
        regenerate(db, doc, facts)
        return await push_generated(db, doc, actor, force=force, facts=facts, fresh=fresh, expected=expected)
    except Exception as e:  # noqa: BLE001
        logger.warning("[MENUS] %s skipped: %s", doc.get("handle"), e)
        from .shopify_push import MODE_LIVE, MODE_SIMULATED, _live_or_reason

        try:  # the toast says LIVE / dry-run truthfully, even for a failure
            mode = MODE_LIVE if _live_or_reason(db)[0] else MODE_SIMULATED
        except Exception:  # noqa: BLE001
            mode = None
        return {"handle": doc.get("handle"), "ok": False, "changed": False, "mode": mode,
                "error": f"skipped, the last menu stands: {e}"}


async def sync_generated_menus(db, actor: Optional[Dict[str, Any]] = None, force: bool = False,
                               facts: Optional[List[Dict[str, Any]]] = None,
                               fresh: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Regenerate every generated menu and push the changed ones -- the live
    sync's last step. The listings are read ONCE, STRICTLY (or handed in by
    the live sync, shared with the membership view): an unreadable catalogue
    skips the run and keeps the last pushed menu. ``fresh``: the handles of
    the collections the same run just pushed (push_generated waits a run
    rather than trust Shopify's count for them yet). Never raises."""
    try:
        docs = generated_menus(db)
    except Exception as e:  # noqa: BLE001
        return {"menus": [], "error": f"menus unreadable: {e}"}
    if not docs:
        return {"menus": [], "note": "no generated menu (scripts/seed_generated_main_menu.py)"}
    from . import collection_membership

    if facts is None:
        try:
            facts = collection_membership.online_facts(db, strict=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[MENUS] listings unreadable, menus skipped: %s", e)
            return {"menus": [], "error": f"listings unreadable, the last pushed menu stands: {e}"}
    return {"menus": [await regenerate_and_push(db, doc, actor, force=force, facts=facts, fresh=fresh)
                      for doc in docs]}


def status(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The Menus page's / Sync page's view of one generated menu."""
    def iso(v):
        return v.isoformat() if isinstance(v, datetime) else v

    last = dict(doc.get("last_push") or {})
    if last.get("at") is not None:
        last["at"] = iso(last["at"])
    return {
        "menu_id": doc.get("menu_id"), "handle": doc.get("handle"), "title": doc.get("title"),
        "template": doc.get("template") or {}, "items": doc.get("items") or [], "hidden": doc.get("hidden") or [],
        "shopify_menu_id": doc.get("shopify_menu_id"),
        "last_generated_at": iso(doc.get("last_generated_at")), "last_pushed_at": iso(doc.get("last_pushed_at")),
        "last_push": last or None, "in_sync": bool(doc.get("generated_hash"))
        and doc.get("generated_hash") == doc.get("pushed_hash"),
        "backup_taken_at": iso(doc.get("backup_taken_at")),
        # The page's confirm: "N collection links (was M at the last push)".
        "pushed_collection_nodes": doc.get("pushed_collection_nodes"),
        "pushed_live": bool(doc.get("pushed_hash")),
    }
