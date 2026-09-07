"""Shopify push -- inventory (make the website's QUANTITIES real)

Owner ruling 2026-09-07 (sync-audit gap #1, first of five): the storefront
must sell only what the shops can ship. Measured on prod the day before: every
IMS-pushed product was ``inventoryItem.tracked = false`` with a policy that
allows selling, quantity 0 at every location -- the website sold without
limit -- and the old write-back path was DEAD (its location env unset, its
credential a stale vault token, no stock hook reached it).

Four things live here, all behind the same three gates as every other push
(DARK -> SIMULATED, zero network):

  * ``resolve_online_location_id`` -- WHICH Shopify location the online
    quantity lives at. Pinned env wins; else the storefront registry row (where
    a previous resolution was persisted, so the sync page can show it); else
    ONE ``locations`` lookup: the single active location that fulfils online
    orders. Two candidates -> prefer the one named after the online store's
    name/city, else REFUSE with ONLINE_LOCATION_AMBIGUOUS. None -> refuse with
    ONLINE_LOCATION_UNRESOLVED. Never guessed.
  * ``sync_product_stock`` -- the per-product side channel push_product runs
    after the variants are seeded: tracked=true + inventoryPolicy DENY (or
    CONTINUE when the product's ``ecom.allow_oversell`` says so) on every
    variant gid the product owns, then the pooled quantity per SKU written
    with inventorySetQuantities at that location.
  * ``sync_stock_levels`` -- the whole-catalogue pass the manual "Push stock"
    button, the all-pending sweep and (later) the scheduled live sync call:
    every product with a Shopify gid whose pooled quantity CHANGED since the
    last write (or was never written / never tracked) is re-sent. The diff is
    against ``ecom.online_stock.quantities``, the number we last sent.
  * ``online_quantities_for_skus`` lives in online_stock_writeback -- THE ONE
    quantity rule (pooled physical on-hand minus the safety buffer, the same
    number the POS-sale write-back and the nightly parity check use). This
    module never computes a second one.

WHY A DIFF AND NOT A DIRTY FLAG: on-hand is written by fourteen files
(GRN mint, returns, stock count reconcile, transfers, write-offs, opening
stock, the POS claim, the online-order claim, three agents, ...) and the POS
sell path explicitly refuses the item_events ledger, so there is NO single
choke point to hook a flag into. Diffing the pooled number against the last
one sent is one rule in one place, catches every writer including a manual
Mongo fix, and costs one aggregate over stock_units per run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import os

from agents.nexus_providers import _as_shopify_gid

from ._shared import (
    MODE_LIVE,
    MODE_SIMULATED,
    PushResult,
    _live_or_reason,
    is_variant_of,
    logger,
)
from .transport import _graphql, _now, _user_errors
from .queries import (
    _INVENTORY_SET_MAX,
    _INVENTORY_SET_QUANTITIES,
    _LOCATIONS_LIST_QUERY,
    _LOCATIONS_QUERY,
    _VARIANTS_INVENTORY_UPDATE,
    _VARIANTS_PER_CALL,
    _online_location_cache,
)

# Stable machine codes (the #1105 pattern: `code` for the operator, `error`
# for the plain-language line).
ONLINE_LOCATION_UNRESOLVED = "ONLINE_LOCATION_UNRESOLVED"
ONLINE_LOCATION_AMBIGUOUS = "ONLINE_LOCATION_AMBIGUOUS"
STOCK_ONHAND_UNKNOWN = "STOCK_ONHAND_UNKNOWN"
STOCK_TARGET_MISSING = "STOCK_TARGET_MISSING"

_STOREFRONT_ID = "BV"
_POLICY_DENY = "DENY"
_POLICY_CONTINUE = "CONTINUE"


# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------


def _storefront_coll(db):
    try:
        from ..online_catalog import _coll

        return _coll(db, "storefronts")
    except Exception:  # noqa: BLE001
        return None


def stored_online_location_id(db) -> Tuple[Optional[str], Optional[str]]:
    """``(location_gid, source)`` with NO network call: the pinned
    SHOPIFY_ONLINE_LOCATION_ID env ("pinned"), else the per-process cache or
    the storefront registry row a previous lookup persisted ("stored"), else
    ``(None, None)``. This is the reader push_mode_status and the POS-sale
    write-back's target resolver use."""
    pinned = (os.getenv("SHOPIFY_ONLINE_LOCATION_ID") or "").strip()
    if pinned:
        return _as_shopify_gid(pinned, "Location"), "pinned"
    cached = _online_location_cache.get(_STOREFRONT_ID)
    if cached:
        return cached, "stored"
    try:
        coll = _storefront_coll(db)
        row = (
            coll.find_one({"storefront_id": _STOREFRONT_ID}) if coll is not None else None
        ) or {}
        gid = str(row.get("online_location_id") or "").strip()
        if gid:
            _online_location_cache[_STOREFRONT_ID] = gid
            return gid, "stored"
    except Exception as exc:  # noqa: BLE001 -- a registry read must never raise
        logger.debug("[SHOPIFY_STOCK] storefront row read failed: %s", exc)
    return None, None


def _online_store_name_hints(db) -> List[str]:
    """Lower-cased words (3+ chars) from the ONLINE store rows' name/city --
    the tie-breaker when Shopify has more than one online-fulfilling
    location. Fail-soft []."""
    hints: List[str] = []
    try:
        from ..stores_util import KNOWN_ONLINE_STORE_IDS, ONLINE_STORE_TYPE
        from ..online_catalog import _coll

        coll = _coll(db, "stores")
        if coll is None:
            return []
        rows = coll.find(
            {
                "$or": [
                    {"store_type": ONLINE_STORE_TYPE},
                    {"store_id": {"$in": sorted(KNOWN_ONLINE_STORE_IDS)}},
                ]
            }
        )
        for row in rows:
            for field in ("name", "city"):
                for word in str((row or {}).get(field) or "").lower().split():
                    if len(word) >= 3 and word not in hints:
                        hints.append(word)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SHOPIFY_STOCK] store hint read failed: %s", exc)
    return hints


def pick_online_location(
    nodes: List[Dict[str, Any]], hints: Optional[List[str]] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """PURE: ``(node, code, error)``. Exactly one active location that fulfils
    online orders is picked. More than one -> the single one whose name carries
    an online-store name/city word, else ONLINE_LOCATION_AMBIGUOUS. None ->
    ONLINE_LOCATION_UNRESOLVED. Never guesses."""
    candidates = [
        n
        for n in (nodes or [])
        if isinstance(n, dict)
        and n.get("id")
        and n.get("isActive")
        and n.get("fulfillsOnlineOrders")
    ]
    if not candidates:
        return (
            None,
            ONLINE_LOCATION_UNRESOLVED,
            "no active Shopify location fulfils online orders -- enable one in "
            "Shopify admin (Settings > Locations) or pin SHOPIFY_ONLINE_LOCATION_ID",
        )
    if len(candidates) == 1:
        return candidates[0], None, None
    hinted = [
        c
        for c in candidates
        if any(h in str(c.get("name") or "").lower() for h in (hints or []))
    ]
    if len(hinted) == 1:
        return hinted[0], None, None
    names = ", ".join(str(c.get("name") or c.get("id")) for c in candidates)
    return (
        None,
        ONLINE_LOCATION_AMBIGUOUS,
        f"{len(candidates)} active Shopify locations fulfil online orders ({names}) "
        "-- pin SHOPIFY_ONLINE_LOCATION_ID to the one the website should sell from",
    )


def _persist_location(db, gid: str, name: Optional[str]) -> None:
    """Remember the resolved location on the storefront registry row so the
    sync page can show it and every worker reads the same answer. Fail-soft."""
    try:
        coll = _storefront_coll(db)
        if coll is None:
            return
        coll.update_one(
            {"storefront_id": _STOREFRONT_ID},
            {
                "$set": {
                    "online_location_id": gid,
                    "online_location_name": name,
                    "online_location_resolved_at": _now(),
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] location persist failed: %s", exc)


async def resolve_online_location_id(db) -> Dict[str, Any]:
    """``{location_id, source, name?, code?, error?}``. The stored answer when
    there is one; else ONE `locations` lookup (LIVE creds required -- the
    caller has already passed the gates) whose pick is cached and persisted.
    Fail-soft: never raises; an unresolvable location carries a stable code."""
    gid, source = stored_online_location_id(db)
    if gid:
        return {"location_id": gid, "source": source}
    try:
        body = await _graphql(db, _LOCATIONS_QUERY, {})
    except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
        return {
            "location_id": None,
            "source": "unresolved",
            "code": ONLINE_LOCATION_UNRESOLVED,
            "error": f"location lookup failed: {exc}",
        }
    nodes = ((body.get("data") or {}).get("locations") or {}).get("nodes") or []
    node, code, error = pick_online_location(nodes, _online_store_name_hints(db))
    if node is None:
        return {"location_id": None, "source": "unresolved", "code": code, "error": error}
    gid = _as_shopify_gid(node["id"], "Location")
    _online_location_cache[_STOREFRONT_ID] = gid
    _persist_location(db, gid, node.get("name"))
    return {"location_id": gid, "source": "looked_up", "name": node.get("name")}


async def list_locations(db) -> Dict[str, Any]:
    """Every Shopify location, for the Organization page's per-store "Shopify
    location" dropdown (GET /online-store/push/locations) and the store save
    that copies the display name: ``{mode, reason, locations: [{id, name,
    isActive, fulfillsOnlineOrders, shipsInventory, city, province}]}``.

    DARK (any of the three gates off) -> ``locations == []`` plus the gate
    reason and ZERO network. A Shopify error -> ``[]`` plus the error text;
    never raises. Read-only (read_locations); nothing is cached or persisted
    -- the mapping lives on the store record, not here.
    """
    live, reason = _live_or_reason(db)
    if not live:
        return {"mode": MODE_SIMULATED, "reason": reason, "locations": []}
    try:
        body = await _graphql(db, _LOCATIONS_LIST_QUERY, {})
    except Exception as exc:  # noqa: BLE001 -- fail-soft read
        return {"mode": MODE_LIVE, "reason": f"location lookup failed: {exc}", "locations": []}
    nodes = ((body.get("data") or {}).get("locations") or {}).get("nodes") or []
    out: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        addr = n.get("address") if isinstance(n.get("address"), dict) else {}
        out.append(
            {
                "id": _as_shopify_gid(n["id"], "Location"),
                "name": n.get("name"),
                "isActive": bool(n.get("isActive")),
                "fulfillsOnlineOrders": bool(n.get("fulfillsOnlineOrders")),
                "shipsInventory": bool(n.get("shipsInventory")),
                "city": addr.get("city"),
                "province": addr.get("province"),
            }
        )
    return {"mode": MODE_LIVE, "reason": None, "locations": out}


# ---------------------------------------------------------------------------
# Per-product helpers (pure)
# ---------------------------------------------------------------------------


def inventory_policy_for(product: Dict[str, Any]) -> str:
    """DENY (never oversell) unless the product explicitly opts out via
    ``ecom.allow_oversell`` (a made-to-order line, say)."""
    return _POLICY_CONTINUE if (product.get("ecom") or {}).get("allow_oversell") else _POLICY_DENY


def product_skus(product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]) -> List[str]:
    """The SKUs whose pooled quantity this product lists: one per variant row,
    or the product's own SKU when it has no variant rows."""
    out: List[str] = []
    for v in variants or []:
        sku = str((v or {}).get("sku") or "").strip()
        if sku and sku not in out:
            out.append(sku)
    if not out:
        sku = str(product.get("sku") or "").strip()
        if sku:
            out.append(sku)
    return out


def product_variant_gids(
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    extra: Optional[List[Optional[str]]] = None,
) -> List[str]:
    """Every ProductVariant gid known for this product: the stored default
    (ecom.shopify_variant_id), each variant row's, plus whatever the caller just
    got back from Shopify. De-duplicated, gid-normalised."""
    raw: List[Optional[str]] = [(product.get("ecom") or {}).get("shopify_variant_id")]
    raw += [(v or {}).get("shopify_variant_id") for v in variants or []]
    raw += list(extra or [])
    out: List[str] = []
    for r in raw:
        if not r:
            continue
        gid = _as_shopify_gid(r, "ProductVariant")
        if gid and gid not in out:
            out.append(gid)
    return out


def _last_sent(product: Dict[str, Any]) -> Dict[str, Any]:
    stock = (product.get("ecom") or {}).get("online_stock")
    return stock if isinstance(stock, dict) else {}


def stock_changed(product: Dict[str, Any], quantities: Dict[str, int]) -> bool:
    """True when the pooled quantities differ from the ones last sent, or the
    product was never sent / never had tracking switched on."""
    last = _last_sent(product)
    if not last.get("tracked"):
        return True
    return dict(last.get("quantities") or {}) != dict(quantities)


def plan_product_stock(db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """The dry-run stock plan (SIMULATED branch): policy, the SKU -> quantity
    rows that WOULD be written, and the location as far as it is known with no
    network. Read-only."""
    from ..online_stock_writeback import online_quantities_for_skus

    skus = product_skus(product, variants)
    gid, source = stored_online_location_id(db)
    return {
        "tracked": True,
        "policy": inventory_policy_for(product),
        "quantities": online_quantities_for_skus(db, skus) if skus else {},
        "location_id": gid,
        "location_source": source or "unresolved",
    }


# ---------------------------------------------------------------------------
# LIVE writers
# ---------------------------------------------------------------------------


async def _set_variant_tracking(
    db, product_gid: str, variant_gids: List[str], policy: str
) -> Dict[str, Any]:
    """tracked=true + inventoryPolicy on every variant, chunked. Fail-soft."""
    out: Dict[str, Any] = {"updated": 0, "errors": []}
    rows = [
        {"id": g, "inventoryPolicy": policy, "inventoryItem": {"tracked": True}}
        for g in variant_gids
    ]
    for i in range(0, len(rows), _VARIANTS_PER_CALL):
        chunk = rows[i : i + _VARIANTS_PER_CALL]
        try:
            body = await _graphql(
                db, _VARIANTS_INVENTORY_UPDATE, {"productId": product_gid, "variants": chunk}
            )
            err = _user_errors(body, "productVariantsBulkUpdate")
            if err:
                out["errors"].append(err)
            else:
                out["updated"] += len(chunk)
        except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
            out["errors"].append(str(exc))
    return out


async def set_inventory_quantities(
    db, location_id: str, by_inventory_item: Dict[str, int]
) -> Dict[str, Any]:
    """ABSOLUTE available quantity per InventoryItem gid at ONE location via
    inventorySetQuantities (ignoreCompareQuantity: IMS is the master). Chunked
    at Shopify's cap. Fail-soft ``{set, errors}``."""
    out: Dict[str, Any] = {"set": 0, "errors": []}
    rows = [
        {
            "inventoryItemId": _as_shopify_gid(inv, "InventoryItem"),
            "locationId": _as_shopify_gid(location_id, "Location"),
            "quantity": max(0, int(qty)),
        }
        for inv, qty in by_inventory_item.items()
    ]
    for i in range(0, len(rows), _INVENTORY_SET_MAX):
        chunk = rows[i : i + _INVENTORY_SET_MAX]
        variables = {
            "input": {
                "name": "available",
                "reason": "correction",
                "ignoreCompareQuantity": True,
                "quantities": chunk,
            }
        }
        try:
            body = await _graphql(db, _INVENTORY_SET_QUANTITIES, variables)
            err = _user_errors(body, "inventorySetQuantities")
            if err:
                out["errors"].append(err)
            else:
                out["set"] += len(chunk)
        except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
            out["errors"].append(str(exc))
    return out


def _writeback_stock(db, product_id: str, summary: Dict[str, Any]) -> None:
    """Persist what was just sent (ecom.online_stock) so the next levels pass
    can diff against it. Read-merge-write of the ecom sub-doc, the
    _writeback_product idiom; NEVER touches locally_modified. Fail-soft."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        prev = ecom.get("online_stock") if isinstance(ecom.get("online_stock"), dict) else {}
        ecom["online_stock"] = {
            "quantities": dict(summary.get("quantities") or {}),
            "location_id": summary.get("location_id"),
            "policy": summary.get("policy"),
            "tracked": bool(summary.get("tracked")) or bool(prev.get("tracked")),
            "synced_at": _now(),
        }
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] stock write-back failed %s: %s", product_id, exc)


def zero_stock_ledger_entry(db, product_id: str, sku: str) -> None:
    """After a variant-level delist wrote 0 for ``sku`` on Shopify, record it in
    the PARENT's ledger (ecom.online_stock.quantities) so a later reactivation
    -- pooled 1 vs sent 0 -- DIFFS and is re-sent by the next stock pass.
    Without this the ledger still says 1, the reactivated size compares equal
    and stays sold out on the website until its stock genuinely moves. A
    product never sent (no ledger) is left alone: stock_changed already
    returns True for it. Read-merge-write; fail-soft."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        stock = ecom.get("online_stock")
        if not isinstance(stock, dict) or not stock:
            return
        stock = dict(stock)
        quantities = dict(stock.get("quantities") or {})
        quantities[sku] = 0
        stock["quantities"] = quantities
        ecom["online_stock"] = stock
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] ledger zero failed %s/%s: %s", product_id, sku, exc)


async def sync_product_stock(
    db,
    product: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]],
    product_gid: str,
    *,
    extra_variant_gids: Optional[List[Optional[str]]] = None,
    quantities: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates): tracking + policy on every
    known variant, then the pooled quantity per SKU at the online location.
    ``quantities`` may be precomputed by a batch caller (one aggregate for the
    whole catalogue); else computed here. Fail-soft summary, never raises; a
    SKU whose on-hand is UNKNOWN is never written as 0."""
    from ..online_catalog import inventory_items_for_skus
    from ..online_stock_writeback import online_quantities_for_skus

    pid = product.get("id") or product.get("product_id")
    policy = inventory_policy_for(product)
    summary: Dict[str, Any] = {
        "ok": False,
        "policy": policy,
        "tracked": 0,
        "set": 0,
        "quantities": {},
        "errors": [],
    }
    loc = await resolve_online_location_id(db)
    if not loc.get("location_id"):
        summary["code"] = loc.get("code")
        summary["error"] = loc.get("error")
        return summary
    summary["location_id"] = loc["location_id"]

    gids = product_variant_gids(product, variants, extra_variant_gids)
    if gids:
        tracked = await _set_variant_tracking(db, product_gid, gids, policy)
        summary["tracked"] = tracked["updated"]
        summary["errors"].extend(tracked["errors"])
    else:
        summary["errors"].append("no variant gid known -- tracking not set")

    skus = product_skus(product, variants)
    targets = inventory_items_for_skus(db, skus) if skus else {}
    qty = quantities if quantities is not None else online_quantities_for_skus(db, skus)
    rows: Dict[str, int] = {}
    for sku in skus:
        inv = targets.get(sku)
        if not inv:
            summary["code"] = summary.get("code") or STOCK_TARGET_MISSING
            summary["errors"].append(f"{sku}: no Shopify inventory item mapped")
            continue
        if sku not in qty:
            summary["code"] = summary.get("code") or STOCK_ONHAND_UNKNOWN
            summary["errors"].append(f"{sku}: on-hand unknown -- not written")
            continue
        rows[inv] = int(qty[sku])
        summary["quantities"][sku] = int(qty[sku])
    if rows:
        written = await set_inventory_quantities(db, loc["location_id"], rows)
        summary["set"] = written["set"]
        summary["errors"].extend(written["errors"])
    summary["ok"] = not summary["errors"] and summary["set"] > 0
    if summary["set"] and pid:
        _writeback_stock(db, pid, summary)
    if summary["errors"] and not summary.get("error"):
        summary["error"] = "; ".join(str(e) for e in summary["errors"][:5])
    return summary


# ---------------------------------------------------------------------------
# The whole-catalogue pass
# ---------------------------------------------------------------------------


def _gid_products_with_variants(db) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """Every catalog product already on Shopify, with its variant rows."""
    out: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    try:
        # A size variant (is_variant_of) never owns a listing: its SKU rides
        # the parent's row set below. Filtered even if a repair script ever
        # stamps the parent gid on the child twin (a double stock write and a
        # second ledger otherwise).
        products = [
            d
            for d in db["catalog_products"].find({})
            if (d.get("ecom") or {}).get("shopify_product_id") and not is_variant_of(d)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] catalog read failed: %s", exc)
        return []
    if not products:
        return []
    by_pid: Dict[str, List[Dict[str, Any]]] = {}
    by_sku: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for v in db["catalog_variants"].find({}):
            if v.get("parent_product_id"):
                by_pid.setdefault(str(v["parent_product_id"]), []).append(v)
            if v.get("parent_sku"):
                by_sku.setdefault(str(v["parent_sku"]), []).append(v)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_STOCK] variant read failed: %s", exc)
    for p in products:
        pid = str(p.get("id") or p.get("product_id") or "")
        rows = by_pid.get(pid) or by_sku.get(str(p.get("sku") or "")) or []
        out.append((p, rows))
    return out


async def sync_stock_levels(db) -> PushResult:
    """Send the pooled quantity of every product on Shopify whose number
    CHANGED since it was last sent (or was never sent / never tracked). ONE
    aggregate for the whole catalogue, then one tracking + one quantity write
    per changed product. DARK -> a SIMULATED plan and zero network. Never
    raises. entity="stock", action="sync" (or "noop" when nothing changed)."""
    from ..online_stock_writeback import online_quantities_for_skus

    pairs = _gid_products_with_variants(db)
    all_skus: List[str] = []
    for product, variants in pairs:
        for sku in product_skus(product, variants):
            if sku not in all_skus:
                all_skus.append(sku)
    quantities = online_quantities_for_skus(db, all_skus) if all_skus else {}
    if all_skus and not quantities:
        # STRICT: an absolute writer never fails soft to 0 for a whole batch.
        return PushResult(
            mode=MODE_SIMULATED,
            entity="stock",
            action="sync",
            ok=False,
            code=STOCK_ONHAND_UNKNOWN,
            error="pooled on-hand unknown for every listed SKU (spine/stock read "
            "failed) -- nothing written",
            payload={"candidates": len(pairs)},
        )
    changed = []
    for product, variants in pairs:
        skus = product_skus(product, variants)
        mine = {s: quantities[s] for s in skus if s in quantities}
        if stock_changed(product, mine):
            changed.append((product, variants, mine))
    location_id, location_source = stored_online_location_id(db)
    payload: Dict[str, Any] = {
        "candidates": len(pairs),
        "changed": len(changed),
        "unchanged": len(pairs) - len(changed),
        "location_id": location_id,
        "location_source": location_source or "unresolved",
        "plan": [
            {"product_id": p.get("id") or p.get("product_id"), "quantities": q}
            for p, _v, q in changed[:50]
        ],
    }
    live, reason = _live_or_reason(db)
    if not live:
        return PushResult(
            mode=MODE_SIMULATED,
            entity="stock",
            action="sync" if changed else "noop",
            ok=True,
            payload=payload,
            reason=reason,
        )
    synced = 0
    failed = 0
    errors: List[str] = []
    code: Optional[str] = None
    for product, variants, mine in changed:
        gid = (product.get("ecom") or {}).get("shopify_product_id")
        res = await sync_product_stock(
            db, product, variants, _as_shopify_gid(gid, "Product"), quantities=mine
        )
        if res.get("ok"):
            synced += 1
        else:
            failed += 1
            code = code or res.get("code")
            pid = product.get("id") or product.get("product_id")
            errors.append(f"{pid}: {res.get('error') or 'stock not written'}")
    payload.update({"synced": synced, "failed": failed, "errors": errors[:20]})
    loc = await resolve_online_location_id(db) if changed else {}
    if loc.get("location_id"):
        payload["location_id"] = loc["location_id"]
        payload["location_source"] = loc.get("source")
    return PushResult(
        mode=MODE_LIVE,
        entity="stock",
        action="sync" if changed else "noop",
        ok=failed == 0,
        payload=payload,
        code=code,
        error=("; ".join(errors[:3]) if errors else None),
    )
