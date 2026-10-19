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

# ecom_collections fields of the IMS collection registry (collection_registry):
# IMS_MANAGED marks a collection IMS derives and keeps; IMS_KEY is its stable
# key, also written on Shopify as the ims.collection_key metafield so a
# rebuild tells IMS's collections from anyone else's. RETIRED marks an old
# collection (deleted on Shopify by scripts/rebuild_collections.py) that is
# never pushed again.
IMS_MANAGED = "ims_managed"
IMS_KEY = "ims_key"
RETIRED = "retired"

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
    if collection.get(IMS_MANAGED) and collection.get(IMS_KEY):
        inp["metafields"] = [{
            "namespace": "ims", "key": "collection_key",
            "type": "single_line_text_field", "value": str(collection[IMS_KEY]),
        }]
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
    if collection.get(RETIRED):
        return PushResult(
            mode=MODE_BLOCKED, entity="collection", action="skip", target_id=cid, ok=False,
            error="retired: deleted from the website by the collection rebuild", reason="retired",
        )
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
        if new_gid and cid:
            _writeback_simple(
                db,
                "ecom_collections",
                "collection_id",
                cid,
                "shopify_collection_id",
                new_gid,
                sent_rev=collection.get("edit_rev"),
            )
        # A collection IMS just CREATED, and every registry collection, goes on
        # the Online Store (idempotent; fail-soft, never flips ok). An existing
        # collection keeps whatever channels Shopify has it on.
        publication = None
        if new_gid and collection.get("published") is not False and (
            action == "create" or collection.get(IMS_MANAGED)
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
