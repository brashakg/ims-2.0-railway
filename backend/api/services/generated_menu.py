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
stable content hash on the doc). LIVE: the shop's menu with the same handle
is read first -- the first push ADOPTS it (menuUpdate of its gid, its name
and its heading URLs; never a second menu with the same handle) -- and every
LIVE overwrite saves the tree it replaces in ``ecom_menu_backups`` and keeps
the live item ids of unchanged items. Before a LIVE push the tree is rebuilt
with Shopify's own productsCount (a collection Shopify shows empty or lacks
is left out), and an AUTOMATIC push is refused when IMS read no listing, the
tree has no collection link, or it would lose more than half of the last
pushed collection links -- only the human "Regenerate & push now" (force)
sends such a tree. The listings are read STRICTLY: an unreadable catalogue
skips the run and the last pushed menu stands. DARK / SIMULATED vs LIVE
follows ``_live_or_reason``; every attempt writes the same chained audit
row as the other pushes. A failed or refused push is recorded on the doc
(``last_push``) and retried on the next run.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def _usable(
    row: Optional[Dict[str, Any]], counts: Dict[str, Dict[str, Any]],
    shop_counts: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """(shown, why hidden) for one collection row. ``shop_counts`` ({gid:
    Shopify productsCount}, read right before a LIVE push) also hides what
    Shopify itself shows empty or does not have, and a row whose latest
    rules did not reach Shopify (the collection sync just tried and failed)."""
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
    if shop_counts is not None:
        if row.get("locally_modified"):
            return False, "not on the website yet (its latest rules have not reached Shopify)"
        on_shop = shop_counts.get(_as_shopify_gid(row["shopify_collection_id"], "Collection"))
        if on_shop is None:
            return False, "not found on Shopify"
        if not on_shop:
            return False, "Shopify shows no products in it"
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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(items, hidden): the menu tree in the ecom_menus item shape (what
    shopify_push.menus.build_menu_items sends) and every collection left out
    with why. ``collections`` are ecom_collections rows (registry rows carry
    ``ims_key``), ``counts`` is collection_membership.counts_by_handle,
    ``shop_counts`` Shopify's own productsCount per gid (LIVE push only).
    Pure and deterministic."""
    by_key = {str(c["ims_key"]): c for c in collections if c.get("ims_key") and not c.get("retired")}
    by_handle = {str(c["handle"]): c for c in collections if c.get("handle") and not c.get("retired")}
    items: List[Dict[str, Any]] = []
    hidden: List[Dict[str, Any]] = []

    def usable(row):
        return _usable(row, counts, shop_counts)

    def hide(section: str, title: str, row: Optional[Dict[str, Any]], why: str, handle: Optional[str] = None):
        hidden.append({"section": section, "title": title, "handle": (row or {}).get("handle") or handle, "reason": why})

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
            node = _link_node(title, str(s.get("url") or "#"))
            node["children"] = [_link_node(str(link["title"]), str(link["url"])) for link in s.get("links") or []]
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
                kids.extend(_link_node(str(link["title"]), str(link["url"])) for link in g.get("links") or [])
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
                node = _link_node(title, str(s.get("url") or "#"))
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


def regenerate(db, doc: Dict[str, Any], facts: List[Dict[str, Any]],
               shop_counts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fill ``doc`` from the registry + live membership over ``facts`` (a
    STRICT read by the caller -- an unreadable catalogue never becomes "no
    products") and save the result (items, hidden, generated_hash,
    last_generated_at). Returns the update."""
    from . import collection_membership

    collections = collection_membership.live_collections(db)
    counts = collection_membership.counts_by_handle(db, facts, collections)
    items, hidden = generate(doc.get("template") or {}, collections, counts, shop_counts)
    title = doc.get("title") or doc.get("handle") or MAIN_MENU
    upd = {
        "items": items, "hidden": hidden, "generated_hash": menu_hash(title, doc.get("handle") or MAIN_MENU, items),
        "last_generated_at": _now(),
    }
    db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": upd})
    doc.update(upd)
    return upd


# ---------------------------------------------------------------------------
# Shopify side: read the live menu, adopt it, back it up, push.
# ---------------------------------------------------------------------------

_MENU_BY_HANDLE = """
query imsMenuByHandle($after: String) {
  menus(first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { id handle title items { id title type url resourceId
      items { id title type url resourceId items { id title type url resourceId } } } }
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
    so menuUpdate keeps the ids of unchanged items (per-item mega-menu
    settings an app keys on them survive). Each live id is used once."""
    pool: Dict[Tuple[str, ...], str] = {}

    def walk(nodes, path):
        for n in nodes or []:
            p = path + (_key(n.get("title")),)
            if n.get("id"):
                pool.setdefault(p, n["id"])
            walk(n.get("items"), p)

    def attach(nodes, path):
        out = []
        for n in nodes or []:
            p = path + (_key(n.get("title")),)
            m = dict(n)
            if pool.get(p):
                m["shopify_item_id"] = pool.pop(p)
            if n.get("children"):
                m["children"] = attach(n["children"], p)
            out.append(m)
        return out

    walk(shop_items, ())
    return attach(items, ())


DROP_LIMIT = 0.5  # an auto push may not lose more than half the collection links


def _refusal(doc: Dict[str, Any], facts: List[Dict[str, Any]]) -> Optional[str]:
    """Why an AUTOMATIC live push of ``doc``'s tree is refused (None = send
    it). A menu that suddenly has no or far fewer collection links is far
    likelier a bad read than a real catalogue -- only a human pressing
    "Regenerate & push now" (force) sends it."""
    nodes = collection_nodes(doc.get("items") or [])
    prev = int(doc.get("pushed_collection_nodes") or 0)
    if not facts:
        return "refused: IMS read no listing on the website (the menu would lose every collection)"
    if nodes == 0:
        return "refused: the generated menu has no collection with products"
    if prev and nodes < prev * DROP_LIMIT:
        return f"refused: the menu would drop from {prev} to {nodes} collection links (more than half)"
    return None


async def push_generated(db, doc: Dict[str, Any], actor: Optional[Dict[str, Any]] = None,
                         force: bool = False, facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Push ``doc``'s generated tree when it differs from what was last
    pushed (or ``force``: the human "Regenerate & push now"). Records
    ``last_push`` on the doc and an audit row. Never raises.

    LIVE: read the shop's menu (adopt it the first time) and every
    collection's Shopify productsCount, rebuild hiding what Shopify shows
    empty, refuse a suspicious automatic push (_refusal), back the live tree
    up, then menuUpdate carrying the live item ids. Any failure pushes
    nothing and is retried next run."""
    from .shopify_live_sync import write_push_audit
    from .shopify_push import MODE_LIVE, PushResult, _live_or_reason, push_menu
    from .shopify_push.collection_registry import shop_collections

    live, _reason = _live_or_reason(db)
    if not force and doc.get("generated_hash") and doc.get("generated_hash") == doc.get("pushed_hash"):
        return {"handle": doc.get("handle"), "changed": False}
    if not live and not force and doc.get("generated_hash") == doc.get("simulated_hash"):
        # DARK: this exact tree was already simulated -- no new audit row.
        return {"handle": doc.get("handle"), "changed": False, "mode": "SIMULATED"}
    error, shop, send = None, None, doc
    if live:
        try:
            if facts is None:
                from . import collection_membership

                facts = collection_membership.online_facts(db, strict=True)
            shop = await _shop_menu(db, doc.get("handle") or MAIN_MENU)
            shop_counts = {str(c.get("id")): c.get("products") for c in await shop_collections(db)}
        except Exception as e:  # noqa: BLE001 -- no read => no overwrite
            error = f"the listings or the existing Shopify menu could not be read or backed up: {e}"
        if error is None:
            if shop is not None and not doc.get("shopify_menu_id"):
                _adopt(db, doc, shop)
            regenerate(db, doc, facts, shop_counts)
            if not force and doc.get("generated_hash") == doc.get("pushed_hash"):
                return {"handle": doc.get("handle"), "changed": False}
            error = None if force else _refusal(doc, facts)
        if error is None and shop is not None:
            try:  # every LIVE overwrite saves the tree it replaces
                db[BACKUPS].insert_one({
                    "menu_id": doc["menu_id"], "handle": shop.get("handle"), "shopify_menu_id": shop.get("id"),
                    "title": shop.get("title"), "items": shop.get("items") or [], "taken_at": _now(),
                })
                db[MENUS].update_one({"menu_id": doc["menu_id"]}, {"$set": {"backup_taken_at": _now()}})
                send = dict(doc, items=_carry_ids(doc.get("items") or [], shop.get("items") or []))
            except Exception as e:  # noqa: BLE001
                error = f"the existing Shopify menu could not be read or backed up: {e}"
    if error:
        result = PushResult(mode=MODE_LIVE, entity="menu", action="update", target_id=doc.get("menu_id"),
                            ok=False, error=error).to_dict()
    else:
        result = (await push_menu(db, send)).to_dict()
    at = _now()
    upd: Dict[str, Any] = {"last_push": {"at": at, "mode": result.get("mode"), "ok": bool(result.get("ok")),
                                         "action": result.get("action"), "error": result.get("error"),
                                         "reason": result.get("reason")}}
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


async def regenerate_and_push(db, doc: Dict[str, Any], actor: Optional[Dict[str, Any]] = None,
                              force: bool = False, facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Rebuild ``doc`` from a STRICT read of the listings and push it when it
    changed. An unreadable catalogue changes nothing (the last generated
    and pushed menu stand) and is reported. Never raises."""
    from . import collection_membership

    doc.pop("_id", None)
    try:
        if facts is None:
            facts = collection_membership.online_facts(db, strict=True)
        regenerate(db, doc, facts)
        return await push_generated(db, doc, actor, force=force, facts=facts)
    except Exception as e:  # noqa: BLE001
        logger.warning("[MENUS] %s skipped: %s", doc.get("handle"), e)
        return {"handle": doc.get("handle"), "ok": False, "changed": False,
                "error": f"skipped, the last menu stands: {e}"}


async def sync_generated_menus(db, actor: Optional[Dict[str, Any]] = None, force: bool = False,
                               facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Regenerate every generated menu and push the changed ones -- the live
    sync's last step. The listings are read ONCE, STRICTLY (or handed in by
    the live sync, shared with the membership view): an unreadable catalogue
    skips the run and keeps the last pushed menu. Never raises."""
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
    return {"menus": [await regenerate_and_push(db, doc, actor, force=force, facts=facts) for doc in docs]}


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
    }
