"""
IMS 2.0 - THE website collection membership rule  (owner 2026-10-29)
=====================================================================
"Collection C has products" and "product P is in collection C" are answered
HERE and nowhere else. The answer is Shopify's own reading of the
collection's rules, evaluated over what the product push SENDS for each
listing (productType, the generated tags, vendor, variant prices --
collection_registry._facts), so IMS and the website can never disagree.

    rule_set(collection)       the rules as Shopify receives them (or why
                               IMS cannot send / evaluate them)
    matches(fact, rules, dj)   Shopify's reading of one rule set (pure)
    online_facts(db, strict)   every listing ON the website (Active on
                               Shopify, not a Draft, not taken down,
                               archived or deleted) with the colour / size
                               SKUs online by online_catalog.skus_on_shopify
                               -- what a count counts; strict raises (every
                               writer uses it)
    catalogue_facts(db)        every listing IMS holds, on the website or
                               not (the online-block classifier's haystack)
    members(db, c, facts)      the listings of collection C (+ why empty)
    counts_by_handle(db)       {handle: live product count} for every
                               collection IMS holds (menus, Store Health)

The materialised ``collection_products`` view (collection_materializer) is a
CACHE of ``members`` and nothing else; every reader of membership --
Store Health, collection insights, the browse, the catalogue PDF, the
collections editor and the online-block classifier -- goes through here or
through that cache.

A rule IMS cannot send to Shopify (an IMS-only field such as shape, an IN
list, IS_SET ...) is never guessed: the collection is reported "not
evaluable" with the reason and counts 0, because that is what the website
can be relied on to show for it.

No emoji (Windows cp1252). Pure below the db readers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Shopify CollectionRuleColumn -> the fact the push sends for it.
_COLUMN_FIELD = {
    "TAG": "tag",
    "TYPE": "product_type",
    "VENDOR": "vendor",
    "VARIANT_PRICE": "variant_price",
}
_TEXT_RELATIONS = {"EQUALS", "NOT_EQUALS", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH", "ENDS_WITH"}
_TAG_RELATIONS = {"EQUALS", "NOT_EQUALS"}
_PRICE_RELATIONS = {"EQUALS", "NOT_EQUALS", "GREATER_THAN", "LESS_THAN"}

RETIRED = "retired"


def rule_set(collection: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[str]]:
    """A SMART collection's rules as ``{field, relation, value}`` over the
    pushed facts (field: tag | product_type | vendor | variant_price), plus
    every rule IMS cannot evaluate as Shopify would. Accepts every stored
    shape (registry, IMS editor, migrated Shopify) through the push's own
    converter, so a rule is read exactly as it is sent. Pure."""
    from .shopify_push.collections import convert_rule_set

    out: List[Dict[str, str]] = []
    lost: List[str] = []
    for raw in (collection or {}).get("rules") or []:
        sent, why = convert_rule_set([raw])
        if why or not sent:
            lost.extend(why or [repr(raw)[:40]])
            continue
        col, rel, value = sent[0]["column"], sent[0]["relation"], sent[0]["condition"]
        field = _COLUMN_FIELD.get(col)
        allowed = _TAG_RELATIONS if field == "tag" else _PRICE_RELATIONS if field == "variant_price" else _TEXT_RELATIONS
        if field is None or rel not in allowed:
            lost.append(f"{col} {rel} {value}")
            continue
        if field == "variant_price":
            try:
                float(value)
            except (TypeError, ValueError):
                lost.append(f"{col} {rel} {value}")
                continue
        out.append({"field": field, "relation": rel, "value": value})
    return out, lost


def _text(have: str, rel: str, want: str) -> bool:
    have, want = (have or "").strip().lower(), (want or "").strip().lower()
    if rel == "EQUALS":
        return have == want
    if rel == "NOT_EQUALS":
        return have != want
    if rel == "CONTAINS":
        return want in have
    if rel == "NOT_CONTAINS":
        return want not in have
    if rel == "STARTS_WITH":
        return have.startswith(want)
    return have.endswith(want)  # ENDS_WITH


def _one(fact: Dict[str, Any], rule: Dict[str, str]) -> bool:
    field, rel, value = rule["field"], str(rule.get("relation") or "EQUALS").upper(), rule["value"]
    if field == "tag":
        has = str(value).strip().lower() in {str(t).lower() for t in fact.get("tags") or ()}
        return has if rel == "EQUALS" else not has
    if field == "product_type":
        return _text(fact.get("type") or "", rel, value)
    if field == "vendor":
        return _text(fact.get("brand") or "", rel, value)
    # variant_price: Shopify matches when ANY variant satisfies the rule.
    v = float(value)
    prices = [float(p) for p in fact.get("prices") or ()]
    if rel == "GREATER_THAN":
        return any(p > v for p in prices)
    if rel == "LESS_THAN":
        return any(p < v for p in prices)
    if rel == "EQUALS":
        return any(abs(p - v) < 0.005 for p in prices)
    return any(abs(p - v) >= 0.005 for p in prices)  # NOT_EQUALS


def matches(fact: Dict[str, Any], rules: List[Dict[str, str]], disjunctive: bool = False) -> bool:
    """Shopify's reading of a rule set (all-AND, or ANY when ``disjunctive``)
    over what the push sends. No rules never matches. Pure."""
    if not rules:
        return False
    hits = (_one(fact, r) for r in rules)
    return any(hits) if disjunctive else all(hits)


# ---------------------------------------------------------------------------
# The haystacks.
# ---------------------------------------------------------------------------


def online_facts(db, strict: bool = False) -> List[Dict[str, Any]]:
    """Every listing ON the website: a lead or single product with a Shopify
    id whose Shopify status is not Draft, not taken down, archived or
    deleted (collection_registry.listing_facts).

    Fail-soft -> [] for a COUNT (a page, a preview). ``strict=True`` raises
    instead: every WRITER of what membership decides -- the generated menu
    push, the collection_products view -- must never read an unreadable
    catalogue as "no products" (that pushed an About-Us-only menu LIVE)."""
    if strict:
        return _online(db)
    try:
        return _online(db)
    except Exception as exc:  # noqa: BLE001 -- a count, never a write
        logger.warning("[MEMBERSHIP] online listings unreadable: %s", exc)
        return []


def _online(db) -> List[Dict[str, Any]]:
    """listing_facts, each listing's colour / size SKUs cut to the ones on
    the website by #1275's THE colour/size online rule
    (online_catalog.skus_on_shopify; the lead stands for its live page).
    Raises on a read failure."""
    from .online_catalog import skus_on_shopify
    from .shopify_push.collection_registry import listing_facts

    facts = listing_facts(db)
    rest = [s for f in facts for s in (f.get("skus") or [])[1:]]
    mapped = skus_on_shopify(db, rest) if rest else set()
    for f in facts:
        skus = f.get("skus") or []
        f["skus"] = skus[:1] + [s for s in skus[1:] if s in mapped]
    return facts


def facts_for(db, doc: Dict[str, Any]) -> Dict[str, Any]:
    """The facts the push would send for ONE product doc (on the website or
    not). Raises on a read failure (the caller decides)."""
    from .shopify_push.collection_registry import _facts

    return _facts(db, doc)


def catalogue_facts(db) -> List[Dict[str, Any]]:
    """Every listing IMS holds (lead or single product, not a size variant,
    not deleted), whether or not it is on the website. Fail-soft per doc."""
    from .shopify_push._shared import is_variant_of

    out: List[Dict[str, Any]] = []
    for doc in db["catalog_products"].find({}):
        if is_variant_of(doc) or doc.get("deleted_at"):
            continue
        try:
            out.append(facts_for(db, doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MEMBERSHIP] facts of %s unreadable: %s", doc.get("sku"), exc)
    return out


# ---------------------------------------------------------------------------
# Membership.
# ---------------------------------------------------------------------------


def _custom_members(db, collection: Dict[str, Any], facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A CUSTOM collection's manual members, as the listings that carry them
    (a colour / size resolves to its listing), in position order, once each."""
    by_sku: Dict[str, Dict[str, Any]] = {}
    for f in facts:
        for s in f.get("skus") or [f.get("sku")]:
            if s:
                by_sku.setdefault(str(s), f)
    rows = sorted(collection.get("products") or [], key=lambda p: int((p or {}).get("position", 0) or 0))
    out: List[Dict[str, Any]] = []
    seen = set()
    for p in rows:
        f = by_sku.get(str((p or {}).get("sku") or ""))
        if f is not None and id(f) not in seen:
            seen.add(id(f))
            out.append(f)
    return out


def members(
    db, collection: Dict[str, Any], facts: Optional[List[Dict[str, Any]]] = None
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """The listings of ``collection`` among ``facts`` (default: the listings
    on the website), plus why it has none when that is not simply "no
    product matches" (rules IMS cannot evaluate, a retired collection)."""
    if not isinstance(collection, dict):
        return [], "not a collection"
    if collection.get(RETIRED):
        return [], "retired"
    if facts is None:
        facts = online_facts(db)
    if str(collection.get("collection_type") or "CUSTOM").upper() != "SMART":
        return _custom_members(db, collection, facts), None
    rules, lost = rule_set(collection)
    if lost:
        return [], "rules IMS cannot evaluate as the website does: " + "; ".join(lost)
    if not rules:
        return [], "no rules"
    dj = bool(collection.get("disjunctive"))
    return [f for f in facts if matches(f, rules, dj)], None


def member_skus(
    db, collection: Dict[str, Any], facts: Optional[List[Dict[str, Any]]] = None, every_sku: bool = False
) -> List[str]:
    """The listing (lead) SKUs of ``collection``, in order; ``every_sku``
    also lists every colour / size SKU each listing carries, lead first (the
    collection_products view, collection insights)."""
    out: List[str] = []
    for f in members(db, collection, facts)[0]:
        for s in (f.get("skus") or [f.get("sku")]) if every_sku else [f.get("sku")]:
            if s and str(s) not in out:
                out.append(str(s))
    return out


def not_evaluable(collection: Dict[str, Any]) -> List[str]:
    """The rules of a SMART collection IMS cannot read as the website does
    ([] for a CUSTOM one). Pure."""
    if str((collection or {}).get("collection_type") or "CUSTOM").upper() != "SMART":
        return []
    return rule_set(collection)[1]


def live_collections(db) -> List[Dict[str, Any]]:
    """Every collection IMS holds that is not retired / temporary."""
    return [
        c for c in db["ecom_collections"].find({})
        if not c.get(RETIRED) and not c.get("is_temporary") and c.get("handle")
    ]


def counts_by_handle(
    db, facts: Optional[List[Dict[str, Any]]] = None, collections: Optional[Iterable[Dict[str, Any]]] = None
) -> Dict[str, Dict[str, Any]]:
    """{handle: {count, reason, collection_id, title, shopify_collection_id}}
    for every live collection, counting listings ON the website."""
    if facts is None:
        facts = online_facts(db)
    out: Dict[str, Dict[str, Any]] = {}
    for c in collections if collections is not None else live_collections(db):
        found, why = members(db, c, facts)
        out[c["handle"]] = {
            "count": len(found),
            "reason": why,
            "collection_id": c.get("collection_id"),
            "title": c.get("title"),
            "shopify_collection_id": c.get("shopify_collection_id"),
        }
    return out


def skus_in_any_collection(db, facts: Optional[List[Dict[str, Any]]] = None) -> set:
    """Every SKU that is on the website inside at least one collection: the
    listing's own SKU and every colour / size SKU it carries."""
    if facts is None:
        facts = online_facts(db)
    out: set = set()
    for c in live_collections(db):
        for f in members(db, c, facts)[0]:
            out.update(str(s) for s in (f.get("skus") or [f.get("sku")]) if s)
    return out
