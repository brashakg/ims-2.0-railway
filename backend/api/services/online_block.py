"""
IMS 2.0 - "Block a collection from online sale"  (BVI-retirement, SUPERADMIN)
============================================================================
Some brands contractually forbid ONLINE sale. The owner (SUPERADMIN) flags a
collection with ``online_sync_blocked=True`` on its ``ecom_collections`` doc;
from then on EVERY product that belongs to that collection is excluded from
Shopify -- never pushed, and (if already synced) delisted.

THE MULTI-COLLECTION RULE (a HARD block):
  A product is blocked from online if it belongs to AT LEAST ONE
  ``online_sync_blocked`` collection -- REGARDLESS of its other (unblocked)
  collection memberships. A brand ban wins: being in one clean collection does
  NOT rescue a product that is also in a banned brand's collection.

MEMBERSHIP RESOLUTION -- asked of the LISTING the product sells on (a
colour / size resolves to its model's lead: collection_membership
.listing_facts_of), exactly as the collection_products view holds it:
  * CUSTOM -> the listing is in when ANY SKU it carries is in the embedded
    manual ``products[].sku`` list (a picked colour puts its listing in).
  * SMART  -> THE website membership rule (``collection_membership``): the
    rules read exactly as Shopify reads them, over what the push would send
    for that listing (its generated tags, productType, vendor, prices). A
    blocked collection whose rules IMS cannot evaluate that way is UNKNOWN for
    the products only such a rule could be about -- fail CLOSED on the strict
    push path and the sweep, never a silent pass. Such a collection cannot be
    blocked (POST /block, a rule save on a blocked one) and Store Health lists
    any that already are.
  The materialised ``collection_products`` view is NOT read here: it is a
  cache refreshed by the sync, so a product edited since would be judged by a
  stale row. The rule is asked directly, every time (one implementation).

CONTRACT: 100% FAIL-SOFT. Any error / no DB -> ``False`` (never wrongly HIDE a
product on a lookup blip; the owner re-runs the block sweep). No exceptions
escape. No Shopify network here -- this is a pure classifier the push +
availability paths call. No emoji (Windows cp1252).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from . import collection_membership

logger = logging.getLogger(__name__)


def _blocked_collections(db, strict: bool = False) -> List[Dict[str, Any]]:
    """All ``ecom_collections`` docs flagged ``online_sync_blocked=True``.

    Usually a SMALL set (a handful of contract-banned brands), so iterating them
    per product is cheap. Fail-soft -> [] by default.

    ``strict=True`` RE-RAISES a read error instead of swallowing it -- the push
    chokepoint uses this so a transient DB blip becomes an UNKNOWN classification
    (fail CLOSED / skip the push) rather than a false 'not blocked' that could let
    a contractually-banned product reach Shopify (finding #18)."""
    if db is None:
        return []
    try:
        return list(db["ecom_collections"].find({"online_sync_blocked": True}))
    except Exception as exc:  # noqa: BLE001 -- a config read must never raise into a push
        logger.warning("online-block: reading blocked collections failed: %s", exc)
        if strict:
            raise
        return []


def _listing(db, product: Dict[str, Any], strict: bool = False) -> Dict[str, Any]:
    """The facts of the listing ``product`` sells on (a spine doc reads its
    catalogue twin first). Raises on a read failure."""
    return collection_membership.listing_facts_of(db, _catalog_doc(db, product), strict)


def _in_custom(collection: Dict[str, Any], fact: Dict[str, Any]) -> bool:
    """True if the listing carries ANY manual member SKU of a CUSTOM collection."""
    manual = {str(p.get("sku")) for p in collection.get("products") or [] if isinstance(p, dict) and p.get("sku")}
    return any(str(s) in manual for s in (fact.get("skus") or [fact.get("sku")]) if s)


def _catalog_doc(db, product: Dict[str, Any]) -> Dict[str, Any]:
    """The catalog_products doc the push reads for ``product`` (a spine doc
    resolves to its catalogue twin by sku); the doc itself otherwise."""
    if db is not None and product.get("sku") and "ecom" not in product:
        twin = db["catalog_products"].find_one({"sku": product["sku"]})
        if twin is not None:
            return twin
    return product


def _smart_verdict(collection: Dict[str, Any], fact: Dict[str, Any]) -> Optional[bool]:
    """Is the listing ``fact`` in this SMART collection? True / False when
    the rules IMS can read decide it; None (UNKNOWN) when only a rule IMS
    cannot read (an IN list, shape, gender ... -- collection_membership
    .rule_set) could.

    Decided per listing, never guessed: an all-AND set whose readable rule
    fails is a definite NO (the unreadable rule cannot rescue it), an ANY set
    whose readable rule holds is a definite YES -- so a blocked collection
    with one unreadable rule only ever stalls the products it may be about."""
    rules, lost = collection_membership.rule_set(collection)
    if not rules and not lost:
        return False
    dj = bool(collection.get("disjunctive", False))
    hits = [collection_membership.matches(fact, [r]) for r in rules]
    if dj and any(hits):
        return True
    if not dj and not all(hits):
        return False
    if lost:
        return None
    return not dj  # AND: every rule held; ANY: none did


def _in_collection(collection: Dict[str, Any], fact: Dict[str, Any], strict: bool = False) -> bool:
    """True if the listing ``fact`` is a member of ``collection`` (CUSTOM or
    SMART). UNKNOWN (a SMART rule IMS cannot read decides it): strict ->
    raises (fail closed); otherwise False, logged."""
    if str(collection.get("collection_type") or "CUSTOM").upper() != "SMART":
        return _in_custom(collection, fact)
    verdict = _smart_verdict(collection, fact)
    if verdict is None:
        logger.warning(
            "online-block: blocked collection %s has rules IMS cannot evaluate: %s",
            collection.get("handle") or collection.get("collection_id"),
            "; ".join(collection_membership.not_evaluable(collection)),
        )
        if strict:
            raise RuntimeError("blocked collection rules not evaluable")
        return False
    return verdict


def unverifiable_blocks(db) -> List[Dict[str, Any]]:
    """Every blocked SMART collection with a rule IMS cannot read as the
    website does -- a block that cannot be enforced with certainty (Store
    Health lists them; scripts/check_blocked_collection_rules.py reads them
    on prod). Raises on a read error (the caller reports it)."""
    return [
        {"collection_id": c.get("collection_id"), "handle": c.get("handle"), "title": c.get("title"),
         "rules": collection_membership.not_evaluable(c)}
        for c in _blocked_collections(db, strict=True)
        if collection_membership.not_evaluable(c)
    ]


def _membership_hit(
    product: Dict[str, Any], db, blocked: List[Dict[str, Any]], strict: bool = False
) -> bool:
    """True iff the listing ``product`` sells on is a member of ANY collection
    in ``blocked`` (assumed already loaded + non-empty), by THE membership
    rule asked directly (never the materialised view: a stale cache)."""
    fact = _listing(db, product, strict)
    return any(_in_collection(c, fact, strict) for c in blocked)


def is_blocked_from_online(product: Dict[str, Any], db) -> bool:
    """Is this product blocked from Shopify / online sale?

    True iff it belongs to AT LEAST ONE ``online_sync_blocked`` collection (the
    HARD multi-collection rule documented at module top -- one banned membership
    is enough, other clean memberships do NOT rescue it). Fail-soft -> False.

    This is the FAIL-OPEN variant (availability/display callers): a lookup blip
    never wrongly HIDES a product. The push chokepoint must instead use
    ``is_blocked_from_online_strict`` so a blip fails CLOSED (finding #18).
    """
    if not isinstance(product, dict):
        return False
    try:
        blocked = _blocked_collections(db)
        if not blocked:
            return False
        return _membership_hit(product, db, blocked)
    except Exception as exc:  # noqa: BLE001 -- classifier must never raise
        logger.warning("online-block: is_blocked_from_online failed open (False): %s", exc)
        return False


def is_blocked_from_online_strict(product: Dict[str, Any], db) -> Optional[bool]:
    """Push-path classifier that FAILS CLOSED (finding #18).

    Returns True/False normally, but ``None`` (UNKNOWN) when the block
    CONFIGURATION cannot be read (a transient DB error). The push path must treat
    UNKNOWN as 'skip this push and retry later' -- better to defer a push than to
    risk shipping a contractually-banned product because a read blip made a hard
    block look clean.

    ``db is None`` -> False: no connection means nothing is configured AND the
    push is DARK anyway, so there is no compliance risk to guard against.
    Membership resolution within a readable config is best-effort; a resolution
    error also collapses to UNKNOWN (fail closed) rather than a false clean.
    """
    if not isinstance(product, dict):
        return False
    if db is None:
        return False
    try:
        blocked = _blocked_collections(db, strict=True)
    except Exception:  # noqa: BLE001 -- config unreadable -> UNKNOWN (fail closed)
        logger.warning(
            "online-block: block config unreadable in push path -> UNKNOWN (fail closed)"
        )
        return None
    if not blocked:
        return False
    try:
        return _membership_hit(product, db, blocked, strict=True)
    except Exception as exc:  # noqa: BLE001 -- resolution error -> UNKNOWN (fail closed)
        logger.warning(
            "online-block: strict membership resolution failed -> UNKNOWN "
            "(fail closed): %s",
            exc,
        )
        return None


def classify_blocked_skus(db, skus: List[str]) -> Tuple[Set[str], bool]:
    """Batch classifier for the /all-pending push sweep (findings #17 + #20).

    Returns ``(blocked_set, verifiable)``:
      * ``verifiable=False`` -- the block CONFIG or a membership read failed, or
        a product's membership in a blocked collection rests on a rule IMS
        cannot read (an IN list, shape ...), so the caller MUST fail CLOSED (treat every product as unverifiable and skip the push)
        rather than push possibly-banned products (finding #18).
      * ``verifiable=True`` -- ``blocked_set`` is the subset of ``skus`` that is
        blocked, resolved ONCE for the whole sweep so ``push_product`` never has
        to re-scan the block config per product (kills the N+1, finding #20).

    ``db is None`` -> ``(empty, True)``: the push is DARK; nothing is configured.
    """
    clean = [s for s in (skus or []) if s]
    if db is None:
        return set(), True
    try:
        # STRICT: a read error, or a product only an unreadable rule of a
        # blocked collection could be about, is UNKNOWN -> the sweep fails
        # CLOSED (never pushes a possibly-banned product).
        return blocked_skus(db, clean, strict=True), True
    except Exception as exc:  # noqa: BLE001 -- not verifiable
        logger.warning("online-block: sweep block membership not verifiable -> fail closed: %s", exc)
        return set(), False


def blocked_skus(db, skus: List[str], *, strict: bool = False) -> Set[str]:
    """Batch form of ``is_blocked_from_online`` for the availability paths: given
    a list of SKUs, return the subset that is blocked from online sale.

    Efficient: loads the blocked collections ONCE and the products in one
    batched load, then asks THE membership rule of each SKU's listing (the
    same one ``is_blocked_from_online`` asks). Fail-soft -> empty set (never
    wrongly mark a SKU blocked on error).

    ``strict=True`` RE-RAISES instead: every membership source -- the config,
    the product load and each listing's facts -- becomes UNKNOWN rather than
    "not blocked". THE online quantity rule uses it (audit round-6 P4).
    There the block is the only thing holding a blocked collection at 0 online,
    so a swallowed read error published the SHELF count for a banned product as
    an absolute quantity and recorded it as the baseline.
    """
    out: Set[str] = set()
    clean = [s for s in (skus or []) if s]
    if not clean or db is None:
        return out
    try:
        blocked = _blocked_collections(db, strict=strict)
        if not blocked:
            return out
        prod_by_sku = _load_products_by_sku(db, clean, strict=strict)
        for s in dict.fromkeys(clean):
            doc = prod_by_sku.get(s)
            try:
                # A SKU with no product doc is judged as itself (a manual
                # CUSTOM entry can still name it).
                fact = _listing(db, doc, strict) if doc else {"sku": s, "skus": [s]}
                hit = any(_in_collection(c, fact, strict) for c in blocked)
            except Exception:  # noqa: BLE001
                if strict:
                    raise
                hit = False
            if hit:
                out.add(s)
        return out
    except Exception:  # noqa: BLE001
        if strict:
            raise
        return out


def _load_products_by_sku(
    db, skus: List[str], *, strict: bool = False
) -> Dict[str, Dict[str, Any]]:
    """sku -> a product doc (`catalog_products` then the spine), for SMART
    rule evaluation. Fail-soft -> {}; ``strict=True`` re-raises (see
    ``blocked_skus``)."""
    out: Dict[str, Dict[str, Any]] = {}
    if db is None or not skus:
        return out
    # The catalogue doc first: it is what the push reads (and sends).
    for coll_name in ("catalog_products", "products"):
        try:
            for d in db[coll_name].find({"sku": {"$in": list(skus)}}):
                s = d.get("sku")
                if s and s not in out:
                    out[s] = {k: v for k, v in d.items() if k != "_id"}
        except Exception:  # noqa: BLE001
            if strict:
                raise
            continue
    return out
