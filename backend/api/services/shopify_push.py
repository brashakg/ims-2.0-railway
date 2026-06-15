"""
IMS 2.0 - Shopify GraphQL push engine  (BVI Phase 5 -- IMS -> Shopify PUSH)
==========================================================================
The IMS -> Shopify PUSH side of the "Online Store" module: take the IMS ecom
models (catalog_products + ecom sub-doc, catalog_variants, ecom_collections,
ecom_menus, APPROVED product_images) and push them to the Shopify storefront
(bettervision.in) via the Shopify GraphQL Admin API.

Full target architecture + the single-writer invariant: docs/reference/BVI_MERGE_PLAN.md
section A.3 + Phase 5.

***** THIS IS BUILT DARK (the non-negotiable safety contract) *****
Every push is SIMULATED -- it returns a dry-run PLAN and makes NO network call --
UNLESS ALL THREE hold:
  1. ims_shopify_writes_enabled()  -- IMS_SHOPIFY_WRITES is on (default OFF). Per
     #262 BVI is the SINGLE Shopify writer; the IMS push stays retired until the
     owner flips this gate as part of the Phase-6 baton cutover.
  2. dispatch_mode() == "live"     -- the same destructive-write gate the rest of
     NEXUS uses (nexus_providers / providers.py).
  3. Shopify creds present         -- shop_url + access_token in the `integrations`
     Mongo collection (NOT env): _load_integration_config(db, "shopify").
Default / missing-creds / gate-off  ->  mode="SIMULATED", no Shopify call.

We REUSE the existing, code-verified safety primitives rather than reinvent them:
  - nexus_providers.ims_shopify_writes_enabled()  (the single-writer kill-switch)
  - nexus_providers._load_integration_config(db, "shopify")  (creds from Mongo)
  - nexus_providers.dispatch_mode() / _as_shopify_gid()  (live gate + GID helper)

IDEMPOTENT: on a LIVE push the Shopify gid returned by the mutation is written
BACK onto the IMS doc (ecom.shopify_product_id / shopify_variant_id /
shopify_collection_id / shopify_menu_id / shopify_image_id), keyed on the IMS
join key (never Mongo _id), so a re-push UPDATES the same Shopify object instead
of creating a duplicate. The presence of a stored Shopify id is what selects
create-vs-update in the mutation.

FAIL-SOFT: every function returns a structured PushResult and NEVER raises. A
Shopify/GraphQL error becomes {ok: False, error: ...}; a missing doc becomes a
404-style {ok: False}. A push must never take down the caller.

The single network boundary is `_graphql()`. It is the ONLY thing that talks to
Shopify; tests monkeypatch it so no real Shopify call ever happens in a default
or test code path (belt-and-suspenders on top of the gate, which already blocks
the live branch by default).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import logging
import os

import httpx

# Reuse the existing Shopify safety primitives -- do NOT fork the writer.
from agents.nexus_providers import (
    _load_integration_config,
    ims_shopify_writes_enabled,
    _as_shopify_gid,
    SHOPIFY_API_VERSION,
)
from agents.providers import dispatch_mode

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT = float(os.getenv("NEXUS_PROVIDER_TIMEOUT", "30.0"))

# Push modes returned in every PushResult.mode.
MODE_SIMULATED = "SIMULATED"
MODE_LIVE = "LIVE"
MODE_BLOCKED = "BLOCKED"  # Hub Phase 5: push refused -- brand/collection push-locked


@dataclass
class PushResult:
    """Structured result of one push attempt. Returned by every push_* function
    and recorded verbatim on the chained audit row by the router.

    mode         SIMULATED (dry-run, no network) | LIVE (a real Shopify write).
    entity       product | variant | collection | menu | image.
    action       create | update | skip | noop (what we did / would do).
    target_id    the IMS doc id we were asked to push.
    ok           True unless an error occurred (a SIMULATED dry-run is ok=True).
    shopify_id   the Shopify gid (set on a LIVE write OR echoed if already mapped).
    payload      the dry-run plan (SIMULATED) or the mutation variables (LIVE).
    error        a human string when ok=False; None otherwise.
    reason       why we are SIMULATED (gate-off / dispatch / no-creds) -- advisory.
    """

    mode: str
    entity: str
    action: str
    target_id: Optional[str] = None
    ok: bool = True
    shopify_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ===========================================================================
# Gating -- the single source of truth for "are we DARK or LIVE?"
# ===========================================================================


def push_mode_status(db) -> Dict[str, Any]:
    """Report the CURRENT push posture without pushing anything (drives GET
    /online-store/push/status). Pure read; never raises.

    Returns the three gate components + the derived effective mode so the owner
    (and the UI banner) can see exactly why pushes are DARK or LIVE:
      writes_enabled  -- IMS_SHOPIFY_WRITES on?
      dispatch_mode   -- off / test / live
      creds_present   -- shop_url + access_token in the integrations collection?
      mode            -- LIVE only when all three align, else SIMULATED.
    """
    writes = ims_shopify_writes_enabled()
    disp = dispatch_mode()
    creds = _has_shopify_creds(db)
    live = bool(writes and disp == "live" and creds)
    return {
        "mode": MODE_LIVE if live else MODE_SIMULATED,
        "writes_enabled": writes,
        "dispatch_mode": disp,
        "creds_present": creds,
        "is_live": live,
        "api_version": SHOPIFY_API_VERSION,
        "single_writer_note": (
            "BVI is the single Shopify writer until the Phase-6 baton cutover; "
            "IMS push is DARK until IMS_SHOPIFY_WRITES=1 AND DISPATCH_MODE=live."
        ),
    }


def _has_shopify_creds(db) -> bool:
    """True iff the `integrations` Shopify config carries shop_url + access_token.
    Fail-soft -> False (treated as DARK)."""
    try:
        cfg = _load_integration_config(db, "shopify") or {}
        return bool(cfg.get("shop_url") and cfg.get("access_token"))
    except Exception:  # noqa: BLE001 -- a config read must never raise into a push
        return False


def _live_or_reason(db) -> Tuple[bool, Optional[str]]:
    """Decide LIVE vs SIMULATED and, when SIMULATED, WHY. The three gates are
    checked in a fixed order so the reason is deterministic + actionable."""
    if not ims_shopify_writes_enabled():
        return (
            False,
            "writes_disabled (IMS_SHOPIFY_WRITES off -- BVI is the single writer)",
        )
    if dispatch_mode() != "live":
        return False, f"dispatch_mode={dispatch_mode()} (need live)"
    if not _has_shopify_creds(db):
        return False, "shopify creds not configured (shop_url/access_token)"
    return True, None


def push_lock_reason(db, entity: str, doc: Dict[str, Any]) -> Optional[str]:
    """Hub Phase 5 (owner DECISION C): return a reason if this entity is push-
    LOCKED, else None. A locked brand (product) or collection handle in the
    `ecom.shopify_push_locks` E2 config may NEVER be pushed -- this is checked as
    the FIRST statement inside every push fn, BEFORE the dark/live gate, so a lock
    is absolute (fail-closed). Matching is case-insensitive. Fail-SOFT on a config-
    read error -> None (a read blip must not block every push; the normal gate
    still applies)."""
    try:
        from .policy_engine import get_policy

        locks = get_policy("ecom.shopify_push_locks", default={}) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(locks, dict):
        return None

    def _norm(v: Any) -> str:
        return str(v or "").strip().lower()

    if entity == "product":
        attrs = doc.get("attributes") or {}
        brand = _norm(doc.get("brand") or doc.get("vendor") or attrs.get("brand_name"))
        if brand and brand in {_norm(b) for b in (locks.get("brands") or [])}:
            return "brand '%s' is push-locked" % brand
    elif entity == "collection":
        handle = _norm(doc.get("handle") or doc.get("title"))
        if handle and handle in {_norm(c) for c in (locks.get("collections") or [])}:
            return "collection '%s' is push-locked" % handle
    return None


def _blocked_result(entity: str, target_id: Optional[str], reason: str) -> "PushResult":
    """A fail-closed push refusal (brand/collection push-locked)."""
    return PushResult(
        mode=MODE_BLOCKED,
        entity=entity,
        action="skip",
        target_id=target_id,
        ok=False,
        error="push-locked: " + reason,
        reason=reason,
    )


# ===========================================================================
# The single Shopify network boundary -- monkeypatched in tests
# ===========================================================================


async def _graphql(db, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    """POST one GraphQL operation to the Shopify Admin API and return the parsed
    JSON body. This is the ONLY function that performs a Shopify network call --
    it is reached ONLY on the LIVE branch (all three gates passed). Tests
    monkeypatch this so no real call is ever made.

    Returns the raw GraphQL response dict ({"data": ...} and/or {"errors": ...}).
    Raises httpx/ValueError on a transport-level failure; the caller catches and
    converts to a fail-soft PushResult.
    """
    cfg = _load_integration_config(db, "shopify") or {}
    shop_url = cfg.get("shop_url")
    access_token = cfg.get("access_token")
    if not shop_url or not access_token:
        # Should never happen (gate checked creds) but guard anyway.
        raise ValueError("shopify creds missing at GraphQL call time")
    url = f"https://{shop_url}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=PROVIDER_TIMEOUT) as client:
        resp = await client.post(
            url, headers=headers, json={"query": query, "variables": variables}
        )
    if resp.status_code not in (200, 201):
        raise ValueError(f"status {resp.status_code}: {resp.text[:200]}")
    return resp.json() or {}


def _user_errors(body: Dict[str, Any], mutation_field: str) -> Optional[str]:
    """Extract a Shopify error string from a GraphQL response, or None if clean.

    A transport-200 can still carry top-level `errors` OR per-field `userErrors`;
    both are failures. We look at the named mutation field's userErrors plus any
    top-level errors so nothing is silently swallowed (Fail Loudly)."""
    if not isinstance(body, dict):
        return "malformed graphql response"
    if body.get("errors"):
        return f"graphql errors: {str(body['errors'])[:300]}"
    data = body.get("data") or {}
    field_obj = data.get(mutation_field) or {}
    ue = field_obj.get("userErrors") or []
    if ue:
        return f"userErrors: {str(ue)[:300]}"
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# GraphQL operations (the Phase-5 push set; BVI_MERGE_PLAN.md A.3)
# ===========================================================================
# Pinned, minimal mutations. We keep them small + explicit so a Shopify default
# bump can't silently change the contract. Each create returns the new gid which
# we write back for idempotency; each update is selected when a gid already exists.

_PRODUCT_CREATE = """
mutation imsProductCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product { id handle }
    userErrors { field message }
  }
}
"""

_PRODUCT_UPDATE = """
mutation imsProductUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id handle }
    userErrors { field message }
  }
}
"""

_COLLECTION_CREATE = """
mutation imsCollectionCreate($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id handle }
    userErrors { field message }
  }
}
"""

_COLLECTION_UPDATE = """
mutation imsCollectionUpdate($input: CollectionInput!) {
  collectionUpdate(input: $input) {
    collection { id handle }
    userErrors { field message }
  }
}
"""

_MENU_CREATE = """
mutation imsMenuCreate($title: String!, $handle: String!, $items: [MenuItemCreateInput!]!) {
  menuCreate(title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

_MENU_UPDATE = """
mutation imsMenuUpdate($id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!) {
  menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

_PRODUCT_CREATE_MEDIA = """
mutation imsProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { ... on MediaImage { id } }
    mediaUserErrors { field message }
  }
}
"""


# ===========================================================================
# Payload builders -- map IMS ecom docs -> Shopify GraphQL input. Pure; testable.
# ===========================================================================


def build_product_input(
    product: Dict[str, Any], variants: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build a Shopify ProductInput from a catalog_products doc (+ its ecom
    sub-doc) and its catalog_variants. The Shopify gid (if already mapped) is set
    so the SAME object is updated rather than duplicated."""
    ecom = product.get("ecom") or {}
    seo = ecom.get("seo") or {}
    title = (
        product.get("title")
        or product.get("name")
        or product.get("model")
        or product.get("sku")
        or "Untitled product"
    )
    status_map = {"DRAFT": "DRAFT", "PUBLISHED": "ACTIVE", "ARCHIVED": "ARCHIVED"}
    inp: Dict[str, Any] = {
        "title": title,
        "status": status_map.get(str(ecom.get("status") or "").upper(), "DRAFT"),
    }
    sid = ecom.get("shopify_product_id")
    if sid:
        inp["id"] = _as_shopify_gid(sid, "Product")
    if ecom.get("handle"):
        inp["handle"] = ecom["handle"]
    if product.get("brand"):
        inp["vendor"] = product["brand"]
    if product.get("category"):
        inp["productType"] = str(product["category"])
    body_html = seo.get("html") or product.get("description")
    if body_html:
        inp["descriptionHtml"] = body_html
    if ecom.get("theme_suffix"):
        inp["templateSuffix"] = ecom["theme_suffix"]
    if seo.get("title") or seo.get("description"):
        inp["seo"] = {
            "title": seo.get("title") or title,
            "description": seo.get("description") or "",
        }
    if seo.get("tags"):
        inp["tags"] = list(seo["tags"])
    # Variant identity is carried as options/skus only (price/qty stay BVI/stock
    # owned -- online qty is the derived allocation, not pushed from here).
    if variants:
        inp["productOptions"] = _derive_options(variants)
    return inp


def _derive_options(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify productOptions (Color / Size) from the variant option axes
    that are actually present. Returns [] when no variant carries an option."""
    colors = [v.get("option_color") for v in variants if v.get("option_color")]
    sizes = [v.get("option_size") for v in variants if v.get("option_size")]
    options: List[Dict[str, Any]] = []
    if colors:
        options.append(
            {"name": "Color", "values": [{"name": c} for c in _dedupe(colors)]}
        )
    if sizes:
        options.append(
            {"name": "Size", "values": [{"name": s} for s in _dedupe(sizes)]}
        )
    return options


def _dedupe(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


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
    if collection.get("sort_order"):
        inp["sortOrder"] = collection["sort_order"]
    if (collection.get("collection_type") or "").upper() == "SMART":
        rules = _build_rule_set(collection.get("rules") or [])
        if rules:
            inp["ruleSet"] = {
                "appliedDisjunctively": bool(collection.get("disjunctive", False)),
                "rules": rules,
            }
    return inp


# Map IMS smart-rule fields -> Shopify CollectionRuleColumn + relation.
_RULE_COLUMN = {
    "brand": "VENDOR",
    "vendor": "VENDOR",
    "category": "TYPE",
    "product_type": "TYPE",
    "type": "TYPE",
    "tag": "TAG",
    "title": "TITLE",
}
_RULE_RELATION = {
    "EQUALS": "EQUALS",
    "CONTAINS": "CONTAINS",
}


def _build_rule_set(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Translate IMS {field, relation, value} smart rules into Shopify
    CollectionRuleInput {column, relation, condition}. Unknown columns are
    skipped (we never push a rule Shopify can't evaluate)."""
    out: List[Dict[str, Any]] = []
    for r in rules:
        col = _RULE_COLUMN.get(str(r.get("field") or "").lower())
        if not col:
            continue
        rel = _RULE_RELATION.get(str(r.get("relation") or "EQUALS").upper(), "EQUALS")
        out.append(
            {"column": col, "relation": rel, "condition": str(r.get("value") or "")}
        )
    return out


def build_menu_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recursively map the IMS ecom_menus item tree -> Shopify MenuItem*Input.
    Each node carries title + type (+ url/resourceId) and its children. Used for
    BOTH create + update (the input shapes are field-compatible for our subset)."""
    out: List[Dict[str, Any]] = []
    for node in items or []:
        item: Dict[str, Any] = {
            "title": node.get("title") or "",
            "type": str(node.get("item_type") or "HTTP").upper(),
        }
        if node.get("url"):
            item["url"] = node["url"]
        if node.get("resource_id"):
            item["resourceId"] = _as_shopify_gid(node["resource_id"], "Collection")
        kids = node.get("children") or []
        if kids:
            item["items"] = build_menu_items(kids)
        out.append(item)
    return out


def build_media_inputs(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify CreateMediaInput[] from APPROVED product_images. Prefer the
    designer's edited asset; fall back to the source url."""
    out: List[Dict[str, Any]] = []
    for img in images:
        src = img.get("edited_url") or img.get("url")
        if not src:
            continue
        out.append(
            {
                "originalSource": src,
                "alt": img.get("alt_text") or "",
                "mediaContentType": "IMAGE",
            }
        )
    return out


# ===========================================================================
# Write-back helpers (idempotency) -- store the Shopify gid on the IMS doc
# ===========================================================================


def _writeback_product(db, product_id: str, shopify_id: str) -> None:
    """Persist ecom.shopify_product_id (+ stamps) on the catalog_products doc and
    clear the dirty flag, for idempotent re-push.

    We READ-MERGE-WRITE the whole `ecom` sub-doc (read the doc, mutate the ecom
    dict in Python, $set ecom back) rather than `$set {"ecom.shopify_product_id":
    ...}`. Both are correct on real Mongo, but the merge-write also works on the
    in-memory MockCollection (which doesn't model dot-notation $set) AND keeps the
    sibling ecom fields intact (status/handle/seo) because the merge is explicit.
    Fail-soft: any error is logged, never raised (the Shopify write already
    succeeded)."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        ecom["shopify_product_id"] = shopify_id
        ecom["last_pushed_at"] = _now()
        ecom["locally_modified"] = False
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] product write-back failed {product_id}: {e}")


def _writeback_simple(
    db,
    collection_name: str,
    id_field: str,
    doc_id: str,
    shopify_field: str,
    shopify_id: str,
) -> None:
    """Generic gid write-back for collection/menu docs: set the shopify id field,
    clear locally_modified, stamp last_synced_at. Fail-soft."""
    try:
        coll = db[collection_name]
        coll.update_one(
            {id_field: doc_id},
            {
                "$set": {
                    shopify_field: shopify_id,
                    "locally_modified": False,
                    "last_synced_at": _now(),
                }
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[SHOPIFY_PUSH] {collection_name} write-back failed {doc_id}: {e}"
        )


# ===========================================================================
# PUSH FUNCTIONS -- one per entity. DARK by default; LIVE only behind the gates.
# ===========================================================================


async def push_product(
    db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]] = None
) -> PushResult:
    """Push a catalog product (+ its ecom sub-doc + variants) to Shopify.

    DARK by default -> returns a SIMULATED dry-run plan with the full ProductInput
    and NO network call. LIVE only when all three gates pass: then productCreate
    (no stored gid) or productUpdate (gid present), with the new gid written back
    for idempotency. Never raises."""
    pid = product.get("id") or product.get("product_id")
    # Hub Phase 5: push-lock is the FIRST gate -- a locked brand is NEVER pushed,
    # before the dark/live gate (fail-closed).
    _lock = push_lock_reason(db, "product", product)
    if _lock:
        return _blocked_result("product", pid, _lock)
    variants = variants or []
    ecom = product.get("ecom") or {}
    existing_gid = ecom.get("shopify_product_id")
    payload = build_product_input(product, variants)
    action = "update" if existing_gid else "create"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="product",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    query = _PRODUCT_UPDATE if existing_gid else _PRODUCT_CREATE
    field_name = "productUpdate" if existing_gid else "productCreate"
    try:
        body = await _graphql(db, query, {"input": payload})
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="product",
                action=action,
                target_id=pid,
                ok=False,
                payload=payload,
                error=err,
            )
        prod = ((body.get("data") or {}).get(field_name) or {}).get("product") or {}
        new_gid = prod.get("id") or existing_gid
        if new_gid and pid:
            _writeback_product(db, pid, new_gid)
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action=action,
            target_id=pid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft, never propagate
        return PushResult(
            mode=MODE_LIVE,
            entity="product",
            action=action,
            target_id=pid,
            ok=False,
            payload=payload,
            error=str(e),
        )


async def push_collection(db, collection: Dict[str, Any]) -> PushResult:
    """Push an ecom_collections doc to Shopify (collectionCreate / collectionUpdate,
    + smart ruleSet when SMART). DARK by default; LIVE behind the gates with gid
    write-back. Never raises."""
    cid = collection.get("collection_id")
    # Hub Phase 5: push-lock first -- a locked collection handle is NEVER pushed.
    _lock = push_lock_reason(db, "collection", collection)
    if _lock:
        return _blocked_result("collection", cid, _lock)
    existing_gid = collection.get("shopify_collection_id")
    payload = build_collection_input(collection)
    action = "update" if existing_gid else "create"

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
            )
        return PushResult(
            mode=MODE_LIVE,
            entity="collection",
            action=action,
            target_id=cid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
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


async def push_menu(db, menu: Dict[str, Any]) -> PushResult:
    """Push an ecom_menus doc (the Online Store nav / mega-menu) to Shopify
    (menuCreate / menuUpdate) mapping the nested item tree. DARK by default; LIVE
    behind the gates with gid write-back. Never raises."""
    mid = menu.get("menu_id")
    existing_gid = menu.get("shopify_menu_id")
    items = build_menu_items(menu.get("items") or [])
    title = menu.get("title") or menu.get("handle")
    handle = menu.get("handle")
    action = "update" if existing_gid else "create"
    payload: Dict[str, Any] = {"title": title, "handle": handle, "items": items}
    if existing_gid:
        payload["id"] = _as_shopify_gid(existing_gid, "Menu")

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="menu",
            action=action,
            target_id=mid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    if existing_gid:
        query, field_name = _MENU_UPDATE, "menuUpdate"
        variables = {
            "id": _as_shopify_gid(existing_gid, "Menu"),
            "title": title,
            "handle": handle,
            "items": items,
        }
    else:
        query, field_name = _MENU_CREATE, "menuCreate"
        variables = {"title": title, "handle": handle, "items": items}
    try:
        body = await _graphql(db, query, variables)
        err = _user_errors(body, field_name)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="menu",
                action=action,
                target_id=mid,
                ok=False,
                payload=payload,
                error=err,
            )
        menu_obj = ((body.get("data") or {}).get(field_name) or {}).get("menu") or {}
        new_gid = menu_obj.get("id") or existing_gid
        if new_gid and mid:
            _writeback_simple(
                db, "ecom_menus", "menu_id", mid, "shopify_menu_id", new_gid
            )
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="menu",
            action=action,
            target_id=mid,
            ok=False,
            payload=payload,
            error=str(e),
        )


async def push_image(db, image: Dict[str, Any]) -> PushResult:
    """Push ONE APPROVED product image to Shopify (productCreateMedia) onto its
    parent product. DARK by default; LIVE behind the gates with the returned
    MediaImage gid written back to shopify_image_id. Never raises.

    GUARD: only an APPROVED image is push-eligible (the design queue gate).
    Anything else returns ok=False action=skip (Fail Loudly) without a network
    call. The parent product MUST already be on Shopify (ecom.shopify_product_id)
    -- without it there is nothing to attach the media to; that is a skip too."""
    iid = image.get("image_id")
    existing_gid = image.get("shopify_image_id")

    if str(image.get("status") or "").upper() != "APPROVED":
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload={"status": image.get("status")},
            error="only APPROVED images are push-eligible",
        )

    # Resolve the parent product's Shopify gid (media attaches to a product).
    product_gid = _resolve_product_gid(db, image.get("product_id"))
    media = build_media_inputs([image])
    payload: Dict[str, Any] = {"productId": product_gid, "media": media}
    action = "update" if existing_gid else "create"

    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="image",
            action=action,
            target_id=iid,
            ok=True,
            shopify_id=existing_gid,
            payload=payload,
            reason=reason,
        )

    if not product_gid:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error="parent product not on Shopify yet (push the product first)",
        )
    if not media:
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action="skip",
            target_id=iid,
            ok=False,
            payload=payload,
            error="no image url to push",
        )
    try:
        body = await _graphql(
            db, _PRODUCT_CREATE_MEDIA, {"productId": product_gid, "media": media}
        )
        err = _user_errors_media(body)
        if err:
            return PushResult(
                mode=MODE_LIVE,
                entity="image",
                action=action,
                target_id=iid,
                ok=False,
                payload=payload,
                error=err,
            )
        media_nodes = ((body.get("data") or {}).get("productCreateMedia") or {}).get(
            "media"
        ) or []
        new_gid = (media_nodes[0].get("id") if media_nodes else None) or existing_gid
        if new_gid and iid:
            _writeback_image(db, iid, new_gid)
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=action,
            target_id=iid,
            ok=True,
            shopify_id=new_gid,
            payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        return PushResult(
            mode=MODE_LIVE,
            entity="image",
            action=action,
            target_id=iid,
            ok=False,
            payload=payload,
            error=str(e),
        )


def _user_errors_media(body: Dict[str, Any]) -> Optional[str]:
    """productCreateMedia uses `mediaUserErrors` (not `userErrors`)."""
    if not isinstance(body, dict):
        return "malformed graphql response"
    if body.get("errors"):
        return f"graphql errors: {str(body['errors'])[:300]}"
    field_obj = (body.get("data") or {}).get("productCreateMedia") or {}
    ue = field_obj.get("mediaUserErrors") or []
    if ue:
        return f"mediaUserErrors: {str(ue)[:300]}"
    return None


def _resolve_product_gid(db, product_id: Optional[str]) -> Optional[str]:
    """Look up the parent catalog_products' ecom.shopify_product_id (the gid the
    image media attaches to). Fail-soft -> None."""
    if not product_id:
        return None
    try:
        doc = db["catalog_products"].find_one({"id": product_id})
        if doc is None:
            return None
        gid = (doc.get("ecom") or {}).get("shopify_product_id")
        return _as_shopify_gid(gid, "Product") if gid else None
    except Exception:  # noqa: BLE001
        return None


def _writeback_image(db, image_id: str, shopify_id: str) -> None:
    """Persist shopify_image_id on the product_images doc. Fail-soft. (The image
    has no locally_modified flag; the gid presence is the idempotency key.)"""
    try:
        db["product_images"].update_one(
            {"image_id": image_id},
            {"$set": {"shopify_image_id": shopify_id, "updated_at": _now()}},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[SHOPIFY_PUSH] image write-back failed {image_id}: {e}")
