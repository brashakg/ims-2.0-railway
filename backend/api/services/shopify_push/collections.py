"""Shopify push -- collections

Collections: CollectionInput builder, smart-rule mapping,
manual membership resolution/push, and `push_collection`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_BLOCKED,
    MODE_LIVE,
    MODE_SIMULATED,
    PushResult,
    _blocked_result,
    _live_or_reason,
    push_lock_reason,
)
from .transport import _graphql, _user_errors
from .queries import (
    _COLLECTION_ADD_PRODUCTS,
    _COLLECTION_CREATE,
    _COLLECTION_PRODUCTS_PER_CALL,
    _COLLECTION_UPDATE,
)
from .writeback import _writeback_simple

logger = logging.getLogger(__name__)

# ecom_collections field: the brand slug a brand collection is kept for.
AUTO_BRAND = "auto_brand"

# Shopify GraphQL CollectionSortOrder. The stored value is often REST-style
# ("manual", "best-selling", "alpha-asc"); GraphQL rejects anything but these.
_SORT_ORDERS = frozenset(
    {
        "ALPHA_ASC",
        "ALPHA_DESC",
        "BEST_SELLING",
        "CREATED",
        "CREATED_DESC",
        "MANUAL",
        "PRICE_ASC",
        "PRICE_DESC",
    }
)


def graphql_sort_order(value: Any) -> Optional[str]:
    """REST spelling or any case / hyphen / underscore variant -> the GraphQL
    CollectionSortOrder enum. Unknown -> None (caller omits it; never fails)."""
    key = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if key in _SORT_ORDERS:
        return key
    if key:
        logger.warning("collection sort_order %r is not a Shopify sort order; not sent", value)
    return None


def build_collection_input(collection: Dict[str, Any]) -> Dict[str, Any]:
    """Build a Shopify CollectionInput from an ecom_collections doc. A SMART
    collection's rules become a ruleSet; a CUSTOM collection's manual SKU list is
    carried as a separate add step (not modelled in CollectionInput here -- the
    Phase-6 membership push handles collectionAddProducts)."""
    inp: Dict[str, Any] = {
        "title": collection.get("title") or collection.get("handle"),
        "handle": collection.get("handle"),
    }
    sid = collection.get("shopify_collection_id")
    if sid:
        inp["id"] = _as_shopify_gid(sid, "Collection")
    desc = collection.get("description_html") or collection.get("description")
    if desc:
        inp["descriptionHtml"] = desc
    if collection.get("template_suffix"):
        inp["templateSuffix"] = collection["template_suffix"]
    if collection.get("seo_title") or collection.get("seo_description"):
        inp["seo"] = {
            "title": collection.get("seo_title") or collection.get("title") or "",
            "description": collection.get("seo_description") or "",
        }
    sort_order = graphql_sort_order(collection.get("sort_order"))
    if sort_order:
        inp["sortOrder"] = sort_order
    # Collection hero image (parity with BVI's updateCollection, which pushed
    # image:{src,altText}). `image_url` is the stored collection image; the
    # `banner_image` metafield is a separate storefront concern, not the
    # Shopify CollectionInput.image.
    image_src = collection.get("image_url")
    if image_src:
        inp["image"] = {
            "src": image_src,
            "altText": collection.get("image_alt") or collection.get("title") or "",
        }
    if (collection.get("collection_type") or "").upper() == "SMART":
        # ALL OR NOTHING: a ruleSet REPLACES the collection's rules on Shopify,
        # so a subset (price / stock rules dropped) would quietly change who is
        # in it. Rules IMS cannot carry faithfully -- or none at all (price
        # bands imported without rules) -- leave Shopify's rules untouched.
        rules, lost = convert_rule_set(collection.get("rules") or [])
        if rules and not lost:
            inp["ruleSet"] = {
                "appliedDisjunctively": bool(collection.get("disjunctive", False)),
                "rules": rules,
            }
    return inp


def rules_left_alone(collection: Dict[str, Any]) -> Optional[str]:
    """Why a SMART collection's push leaves its rules on Shopify as they are,
    or None (the rules are sent). Pure."""
    if (collection.get("collection_type") or "").upper() != "SMART":
        return None
    rules, lost = convert_rule_set(collection.get("rules") or [])
    if lost:
        return "rules kept as they are on Shopify: IMS cannot send " + "; ".join(lost)
    if not rules:
        return "rules kept as they are on Shopify: IMS holds none for this collection"
    return None


# IMS rule field (ecom_smart_rules.normalize_rule's) -> Shopify
# CollectionRuleColumn. Shopify's own column spellings pass straight through.
_RULE_COLUMN = {
    "brand": "VENDOR",
    "vendor": "VENDOR",
    "category": "TYPE",
    "product_type": "TYPE",
    "type": "TYPE",
    "tag": "TAG",
    "tags": "TAG",
    "title": "TITLE",
    "price": "VARIANT_PRICE",
    "variant_price": "VARIANT_PRICE",
    "variant_compare_at_price": "VARIANT_COMPARE_AT_PRICE",
    "variant_inventory": "VARIANT_INVENTORY",
    "inventory": "VARIANT_INVENTORY",
    "variant_weight": "VARIANT_WEIGHT",
    "variant_title": "VARIANT_TITLE",
    "is_price_reduced": "IS_PRICE_REDUCED",
}
_SHOPIFY_RELATIONS = frozenset(
    {"EQUALS", "NOT_EQUALS", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH", "ENDS_WITH",
     "GREATER_THAN", "LESS_THAN", "IS_SET", "IS_NOT_SET"}
)


def convert_rule_set(rules: List[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """IMS smart rules (either stored shape) -> Shopify CollectionRuleInput
    list, plus the rules that could NOT be carried faithfully (unknown column
    or relation, a list value). The caller sends nothing when anything is
    lost. Pure."""
    from ..ecom_smart_rules import normalize_rule

    out: List[Dict[str, Any]] = []
    lost: List[str] = []
    for raw in rules:
        if not isinstance(raw, dict):
            lost.append(repr(raw)[:40])  # never a partial set
            continue
        r = normalize_rule(raw)
        field = str(r.get("column") or r.get("field") or "").strip()
        col = _RULE_COLUMN.get(field.lower().replace(" ", "_")) or (
            field.upper() if field.upper() in _RULE_COLUMN.values() else None
        )
        rel = str(r.get("relation") or "EQUALS").strip().upper()
        value = r.get("value")
        if not col or rel not in _SHOPIFY_RELATIONS or isinstance(value, (list, tuple, dict)):
            lost.append(f"{field or '?'} {rel} {value}")
            continue
        out.append({"column": col, "relation": rel, "condition": "" if value is None else str(value)})
    return out, lost


def _member_product_gids(db, collection: Dict[str, Any]) -> Tuple[List[str], int]:
    """Resolve a CUSTOM collection's manual member SKUs -> their parent products'
    Shopify gids (catalog_products.ecom.shopify_product_id), in position order.

    SMART collections have NO manual membership (Shopify derives their set from
    the ruleSet) -> ([], 0). A member whose product isn't on Shopify yet is
    SKIPPED (counted): it joins the collection when that product first syncs; we
    never create a product from here. Fail-soft -> ([], 0)."""
    if str(collection.get("collection_type") or "CUSTOM").upper() != "CUSTOM":
        return [], 0
    members = sorted(
        (collection.get("products") or []),
        key=lambda p: int((p or {}).get("position", 0) or 0),
    )
    skus = [p.get("sku") for p in members if isinstance(p, dict) and p.get("sku")]
    gids: List[str] = []
    skipped = 0
    for sku in skus:
        gid = None
        try:
            doc = db["catalog_products"].find_one({"sku": sku}) if db is not None else None
            # A colour/size of a model (G1) is on its MODEL's listing: the
            # collection holds that listing (once).
            link = ((doc or {}).get("ecom") or {}).get("variant_of")
            if isinstance(link, dict) and link.get("twin_id"):
                doc = db["catalog_products"].find_one({"id": link["twin_id"]})
            if doc is not None:
                gid = (doc.get("ecom") or {}).get("shopify_product_id")
        except Exception:  # noqa: BLE001 -- one bad lookup never blocks the rest
            gid = None
        if gid and _as_shopify_gid(gid, "Product") in gids:
            continue
        if gid:
            gids.append(_as_shopify_gid(gid, "Product"))
        else:
            skipped += 1
    return gids, skipped


async def _push_collection_membership(
    db, collection_gid: str, product_gids: List[str]
) -> Dict[str, Any]:
    """LIVE-only: attach a CUSTOM collection's manual members via
    collectionAddProducts, chunked at the per-call cap. Fail-SOFT side channel --
    a membership error is reported but NEVER flips the collection push's ok
    (mirrors the metafields / variant-prices contract). Returns a summary dict."""
    added = 0
    errors: List[str] = []
    for i in range(0, len(product_gids), _COLLECTION_PRODUCTS_PER_CALL):
        chunk = product_gids[i : i + _COLLECTION_PRODUCTS_PER_CALL]
        try:
            body = await _graphql(
                db, _COLLECTION_ADD_PRODUCTS, {"id": collection_gid, "productIds": chunk}
            )
            err = _user_errors(body, "collectionAddProducts")
            if err:
                errors.append(err)
            else:
                added += len(chunk)
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            errors.append(str(e))
    return {"added": added, "errors": errors}



async def push_collection(db, collection: Dict[str, Any]) -> PushResult:
    """Push an ecom_collections doc to Shopify (collectionCreate / collectionUpdate,
    + smart ruleSet when SMART, + manual membership when CUSTOM). DARK by default;
    LIVE behind the gates with gid write-back. Never raises.

    MEMBERSHIP (parity fix): a CUSTOM collection's manual member list is NOT part
    of CollectionInput, so after the collection upsert its members are attached
    via collectionAddProducts (mirrors BVI's addProductsToCollection). SMART
    membership is derived by Shopify from the ruleSet -- no add step. Members
    whose product isn't on Shopify yet are skipped (they join on the product's
    first sync). The membership push is a fail-soft side channel: an error is
    reported in `membership` but never flips the collection push's ok."""
    cid = collection.get("collection_id")
    # Hub Phase 5: push-lock first -- a locked collection handle is NEVER pushed.
    _lock = push_lock_reason(db, "collection", collection)
    if _lock:
        return _blocked_result("collection", cid, _lock)
    existing_gid = collection.get("shopify_collection_id")
    payload = build_collection_input(collection)
    action = "update" if existing_gid else "create"
    rules_note = rules_left_alone(collection)
    if rules_note and not existing_gid:
        # A NEW smart collection without its full rules would be born an
        # empty manual one on Shopify: refused, nothing sent.
        return PushResult(
            mode=MODE_BLOCKED, entity="collection", action="skip", target_id=cid, ok=False,
            payload=payload, error="refused: " + rules_note.replace("kept as they are on Shopify", "missing"),
            reason="rules_unsendable", rules_note=rules_note,
        )
    # CUSTOM manual membership plan (empty for SMART).
    member_gids, member_skipped = _member_product_gids(db, collection)
    is_custom = str(collection.get("collection_type") or "CUSTOM").upper() == "CUSTOM"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="collection",
            action=action,
            target_id=cid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
            membership=(
                {"product_ids": member_gids, "skipped_not_on_shopify": member_skipped}
                if is_custom
                else None
            ),
            rules_note=rules_note,
        )

    query = _COLLECTION_UPDATE if existing_gid else _COLLECTION_CREATE
    field_name = "collectionUpdate" if existing_gid else "collectionCreate"
    try:
        body = await _graphql(db, query, {"input": payload})
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="collection",
                action=action,
                target_id=cid,
                ok=False,
                payload=payload,
                error=err,
            )
        coll_obj = ((body.get("data") or {}).get(field_name) or {}).get(
            "collection"
        ) or {}
        new_gid = coll_obj.get("id") or existing_gid
        # Keyed by collection_id, else (a collection imported from the old
        # app without one) by its Shopify id -- so a clean push always leaves
        # the queue instead of being re-sent by every push-all.
        key = ("collection_id", cid) if cid else ("shopify_collection_id", existing_gid)
        if new_gid and key[1]:
            _writeback_simple(
                db,
                "ecom_collections",
                key[0],
                key[1],
                "shopify_collection_id",
                new_gid,
                sent_rev=collection.get("edit_rev"),
            )
        # A collection IMS just CREATED, and every brand collection, goes on
        # the Online Store (idempotent; fail-soft, never flips ok). An existing
        # collection keeps whatever channels Shopify has it on.
        publication = None
        if new_gid and collection.get("published") is not False and (
            action == "create" or collection.get(AUTO_BRAND)
        ):
            from .publish import _publish_to_online_store

            publication = await _publish_to_online_store(db, new_gid)
        # CUSTOM manual membership rides AFTER the collection upsert (the gid must
        # exist to attach products to). Fail-soft side channel: reported in
        # `membership`, never flips the collection push's ok. push never raises.
        membership_summary = None
        if is_custom and new_gid and member_gids:
            mres = await _push_collection_membership(db, new_gid, member_gids)
            membership_summary = {**mres, "skipped_not_on_shopify": member_skipped}
        elif is_custom:
            membership_summary = {
                "added": 0,
                "errors": [],
                "skipped_not_on_shopify": member_skipped,
            }
        return PushResult(
            mode=MODE_LIVE,
            entity="collection",
            action=action,
            target_id=cid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
            membership=membership_summary,
            rules_note=rules_note,
            collection_publication=publication,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="collection",
            action=action,
            target_id=cid,
            ok=False,
            payload=payload,
            error=str(e),
        )



def _brand_tag(brand: Any) -> str:
    """The brand tag every product push sends (shopify_tag_gen's brand rule)."""
    from ..shopify_tag_gen import slugify_brand_value

    slug = slugify_brand_value(brand)
    return f"brand_{slug}" if slug else ""


def _rules_are_the_brands(rules: List[Dict[str, Any]], brand_tag: str) -> bool:
    """Every rule names this brand: its tag (or the storefront's spelling of
    it, shopify_tag_gen._TAG_ALIASES) or the brand as vendor -- so 'brand tag
    OR vendor' is the brand's, 'brand tag AND sunglass' is not."""
    from ..shopify_tag_gen import _TAG_ALIASES

    tags = (brand_tag, *_TAG_ALIASES.get(brand_tag, ()))

    def _one(r: Dict[str, Any]) -> bool:
        col = str(r.get("column") or "").upper()
        cond = str(r.get("condition") or "").strip().lower()
        if str(r.get("relation") or "").upper() != "EQUALS":
            return False
        return (col == "TAG" and cond in tags) or (col == "VENDOR" and _brand_tag(cond) == brand_tag)

    return bool(rules) and all(_one(r) for r in rules)


def _is_brand_collection(doc: Dict[str, Any], brand_tag: str, slug_handle: str) -> bool:
    """An IMS collection row that already IS this brand's: its handle is the
    brand's, or every one of its smart rules names the brand."""
    if str(doc.get("handle") or "").strip().lower() == slug_handle:
        return True
    if str(doc.get("collection_type") or "").upper() != "SMART":
        return False
    rules, lost = convert_rule_set(doc.get("rules") or [])
    return not lost and _rules_are_the_brands(rules, brand_tag)


async def _shopify_collections(db) -> List[Dict[str, Any]]:
    """Every collection on the shop (read-only, paged). Raises on a read
    failure: no brand collection is created on a half-read shop."""
    from .queries import _COLLECTIONS_READ

    out: List[Dict[str, Any]] = []
    after = None
    while True:
        body = await _graphql(db, _COLLECTIONS_READ, {"after": after})
        page = ((body or {}).get("data") or {}).get("collections")
        if page is None:
            raise RuntimeError(f"collections read failed: {(body or {}).get('errors')}")
        out.extend(page.get("nodes") or [])
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out
        after = info.get("endCursor")


def _live_brands(db) -> Dict[str, str]:
    """{brand tag: brand} for every brand with a listing ON the website."""
    from ._shared import _product_brand, is_variant_of

    brands: Dict[str, str] = {}
    for doc in db["catalog_products"].find({"ecom.shopify_product_id": {"$nin": [None, ""]}}):
        ecom = doc.get("ecom") or {}
        if (
            is_variant_of(doc)
            or doc.get("deleted_at")
            or ecom.get("taken_down_at")
            or str(ecom.get("status") or "").upper() == "ARCHIVED"
        ):
            continue
        brand = str(_product_brand(doc) or "").strip()
        tag = _brand_tag(brand)
        if tag:
            brands.setdefault(tag, brand)
    return brands


def _key(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {"_id": doc["_id"]} if "_id" in doc else {"handle": doc.get("handle")}


async def ensure_brand_collections(db) -> Dict[str, Any]:
    """ONE brand collection per brand with at least one listing ON the website
    (owner 2026-10-19: 'auto populate brands and publish collections'). Runs
    on every push-all, before the collections are sent, so a brand's first
    live product brings its collection -- never an empty one.

    A brand's EXISTING collection is never overwritten (review of #1241): one
    in IMS (its handle, or every rule naming the brand), else one found on
    Shopify itself (read-only, by handle or rules -- made in the admin after
    the import), is only MARKED as the brand's and put on the Online Store;
    its content is never sent. A collection another brand already claims is
    never taken. Only when none exists in IMS or on Shopify is one created:
    SMART on the brand tag the product push sends, titled from Brand Master,
    queued (the push creates and publishes it).

    LIVE only: a dark press sends nothing, so nothing is matched or made (it
    returns the brands that would be checked). One brand's failure is logged
    and never stops the rest. Returns {created, reused, adopted, errors}."""
    from ..shopify_tag_gen import slugify_tag_value
    from .publish import _publish_to_online_store
    from database.repositories.ecom_collection_repository import EcomCollectionRepository

    brands = _live_brands(db)
    live, _reason = _live_or_reason(db)
    if not live or not brands:
        return {"dark": not live, "brands": sorted(brands)}

    names: Dict[str, str] = {}
    for row in db["brand_masters"].find({}):
        if row.get("is_active") is not False and row.get("name"):
            names.setdefault(_brand_tag(row["name"]), str(row["name"]).strip())

    colls = db["ecom_collections"]
    repo = EcomCollectionRepository(colls)
    existing = [d for d in colls.find({}) if not d.get("is_temporary")]
    shop = await _shopify_collections(db)
    out: Dict[str, List[str]] = {"created": [], "reused": [], "adopted": [], "errors": []}

    def _gid(value: Any) -> str:
        return _as_shopify_gid(value, "Collection") if value else ""

    async def _publish(gid: str) -> None:
        pub = await _publish_to_online_store(db, gid)
        if not pub.get("published"):
            raise RuntimeError(pub.get("error") or "not published to the Online Store")

    def _mark(doc: Dict[str, Any], tag: str) -> None:
        colls.update_one(_key(doc), {"$set": {AUTO_BRAND: tag}})
        doc[AUTO_BRAND] = tag

    for tag, brand in sorted(brands.items()):
        try:
            if any(d.get(AUTO_BRAND) == tag for d in existing):
                continue
            title = names.get(tag) or brand
            handle = slugify_tag_value(title)
            free = [d for d in existing if d.get(AUTO_BRAND) in (None, "")]
            claimed = {_gid(d.get("shopify_collection_id")) for d in existing if d.get(AUTO_BRAND)}
            found = next((d for d in free if _is_brand_collection(d, tag, handle)), None)
            if found is not None:
                if found.get("shopify_collection_id"):
                    await _publish(_gid(found["shopify_collection_id"]))
                else:
                    # In IMS only (never pushed): nothing on Shopify to overwrite.
                    colls.update_one(_key(found), {"$set": {"locally_modified": True}})
                _mark(found, tag)
                out["reused"].append(str(found.get("handle") or found.get("title")))
                continue
            on_shop = next((
                c for c in shop
                if _gid(c.get("id")) not in claimed and (
                    str(c.get("handle") or "").lower() == handle
                    or _rules_are_the_brands(((c.get("ruleSet") or {}).get("rules")) or [], tag))
            ), None)
            if on_shop is not None:
                await _publish(on_shop["id"])
                mine = next((d for d in existing if _gid(d.get("shopify_collection_id")) == _gid(on_shop["id"])), None)
                if mine is not None:
                    _mark(mine, tag)
                else:
                    rule_set = on_shop.get("ruleSet") or {}
                    doc = repo.create({
                        "title": on_shop.get("title"),
                        "handle": on_shop.get("handle"),
                        "shopify_collection_id": on_shop["id"],
                        "collection_type": "SMART" if rule_set.get("rules") else "CUSTOM",
                        "rules": list(rule_set.get("rules") or []),
                        "disjunctive": bool(rule_set.get("appliedDisjunctively")),
                        "locally_modified": False,
                        AUTO_BRAND: tag,
                    })
                    if not doc:
                        raise RuntimeError(f"could not record collection '{on_shop.get('handle')}'")
                    existing.append(doc)
                out["adopted"].append(str(on_shop.get("handle")))
                continue
            doc = repo.create({
                "title": title,
                "handle": handle,
                "collection_type": "SMART",
                "rules": [{"field": "tag", "relation": "EQUALS", "value": tag}],
                "disjunctive": False,
                "sort_order": "BEST_SELLING",
                AUTO_BRAND: tag,
            })
            if not doc:
                raise RuntimeError(f"could not save collection '{handle}'")
            existing.append(doc)
            out["created"].append(handle)
        except Exception as exc:  # noqa: BLE001 -- one brand never stops the rest
            logger.warning("[SHOPIFY_PUSH] brand collection for %s skipped: %s", tag, exc)
            out["errors"].append(f"{brand}: {exc}")
    return out
