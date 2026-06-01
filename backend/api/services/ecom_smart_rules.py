"""
IMS 2.0 - E-commerce Smart-Collection Rule Resolver  (BVI Phase 2)
==================================================================
Pure, DB-free evaluator for SMART `ecom_collections`. Given a collection's
`rules` (a list of field rules) + `disjunctive` flag, it decides which catalog
products belong, mirroring Shopify smart-collection semantics
(BVI_MERGE_PLAN.md Phase 2; BVI Collection.rules / disjunctive).

A rule is ``{"field": <name>, "relation": <EQUALS|CONTAINS>, "value": <str>}``.
``disjunctive=True`` -> a product matches if ANY rule matches (OR);
``disjunctive=False`` (default) -> ALL rules must match (AND).

SUPPORTED FIELDS (case-insensitive, alias-folded) and where each is read from a
catalog_products doc -- IMS stores brand inside `attributes`, category top-level,
and storefront tags under the optional `ecom.seo.tags`:

    brand     -> doc["attributes"]["brand"]   (fallback: doc["brand"])
    category  -> doc["category"]               (e.g. FRAME / SUNGLASS)
    tag/tags  -> doc["ecom"]["seo"]["tags"]    (list; also accepts CSV string +
                                                top-level doc["tags"])
    title     -> doc["title"]
    sku       -> doc["sku"]

Anything else is treated as a direct dotted/loose key lookup against the doc
(top-level then inside `attributes`), so a niche attribute like "shape" still
works without a code change.

MATCH SEMANTICS:
  - EQUALS   : case-insensitive exact match (for a list field: any element
               equals the value).
  - CONTAINS : case-insensitive substring (for a list field: any element
               contains the value).
  - Unknown relation -> defaults to EQUALS (fail-soft, never raises).

FAIL-SOFT CONTRACT: a malformed rule (missing field/value) is SKIPPED. With no
valid rules, ``matches_product`` returns False (an empty SMART collection rather
than accidentally matching everything) -- the caller surfaces the empty result.
No exceptions escape; all inputs are defensively coerced.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Logical field name -> the catalog_products read path, tried in order. Each path
# is a tuple of keys to descend; the FIRST path that yields a non-None value wins.
_FIELD_PATHS: Dict[str, List[tuple]] = {
    "brand": [("attributes", "brand"), ("brand",)],
    "category": [("category",), ("attributes", "category")],
    "tag": [("ecom", "seo", "tags"), ("tags",), ("attributes", "tags")],
    "tags": [("ecom", "seo", "tags"), ("tags",), ("attributes", "tags")],
    "title": [("title",)],
    "sku": [("sku",)],
    "shape": [("attributes", "shape"), ("shape",)],
    "gender": [("attributes", "gender"), ("gender",)],
    "frame_material": [("attributes", "frame_material"), ("frame_material",)],
    "color": [("attributes", "color"), ("color",)],
}

_EQUALS = "EQUALS"
_CONTAINS = "CONTAINS"


def _norm_field(field: Optional[str]) -> str:
    """Normalise a rule field name: trim, lower, spaces/hyphens -> '_'."""
    if not field:
        return ""
    return field.strip().lower().replace("-", "_").replace(" ", "_")


def _descend(doc: Dict, path: tuple) -> Any:
    """Walk a key tuple into a dict, returning the leaf value or None. Stops (and
    returns None) the moment a non-dict is hit before the leaf."""
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _extract_values(doc: Dict, field: str) -> List[str]:
    """Resolve a logical field to the list of candidate string values from a
    product doc. A list field (tags) yields each element; a CSV string is split;
    a scalar yields a single-item list. Returns [] when the field is absent.
    """
    norm = _norm_field(field)
    paths = _FIELD_PATHS.get(norm)
    raw: Any = None
    if paths:
        for p in paths:
            raw = _descend(doc, p)
            if raw is not None:
                break
    else:
        # Unknown field -> loose lookup: top-level key, then inside attributes.
        raw = doc.get(norm)
        if raw is None and isinstance(doc.get("attributes"), dict):
            raw = doc["attributes"].get(norm)

    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        for item in raw:
            if item is None:
                continue
            out.append(str(item))
        return out
    if isinstance(raw, str):
        # A CSV string (e.g. tags "new,bestseller") splits into discrete values;
        # a plain scalar string is a single value (commas in a brand are rare and
        # splitting is harmless for matching).
        if "," in raw:
            return [tok.strip() for tok in raw.split(",") if tok.strip()]
        return [raw]
    # Numbers / bools -> stringify.
    return [str(raw)]


def _rule_matches(doc: Dict, rule: Dict) -> Optional[bool]:
    """Evaluate ONE rule against a product. Returns True/False, or None when the
    rule is malformed (missing field or value) so the caller can skip it."""
    if not isinstance(rule, dict):
        return None
    field = rule.get("field")
    value = rule.get("value")
    if not field or value is None or str(value).strip() == "":
        return None

    relation = str(rule.get("relation") or _EQUALS).strip().upper()
    needle = str(value).strip().lower()
    candidates = [c.lower() for c in _extract_values(doc, field)]
    if not candidates:
        return False

    if relation == _CONTAINS:
        return any(needle in c for c in candidates)
    # Default + EQUALS: exact (case-insensitive) match on any candidate.
    return any(c == needle for c in candidates)


def matches_product(product: Dict, rules: List[Dict], disjunctive: bool = False) -> bool:
    """True if `product` satisfies the SMART `rules` under the given combinator.

    disjunctive=True  -> OR  (any valid rule matches)
    disjunctive=False -> AND (every valid rule matches)

    Malformed rules are skipped. If NO valid rules remain, returns False (an
    empty SMART collection -- never an accidental match-all).
    """
    if not isinstance(product, dict) or not rules:
        return False

    results: List[bool] = []
    for rule in rules:
        res = _rule_matches(product, rule)
        if res is None:  # malformed -> skip
            continue
        results.append(res)

    if not results:
        return False
    return any(results) if disjunctive else all(results)


def resolve_skus(
    products: List[Dict],
    rules: List[Dict],
    disjunctive: bool = False,
    limit: Optional[int] = None,
) -> List[str]:
    """Return the SKUs of all products matching the rules, de-duplicated and in
    input order. `limit` caps the result (None = no cap). Products without a
    `sku` are skipped. Pure -- the caller supplies the product list (the router
    reads catalog_products and passes them in)."""
    out: List[str] = []
    seen: set = set()
    for product in products or []:
        if not isinstance(product, dict):
            continue
        sku = product.get("sku")
        if not sku or sku in seen:
            continue
        if matches_product(product, rules, disjunctive=disjunctive):
            out.append(sku)
            seen.add(sku)
            if limit is not None and len(out) >= limit:
                break
    return out


# Relations + fields the editor UI can offer (kept tiny + introspectable).
SUPPORTED_RELATIONS = [_EQUALS, _CONTAINS]


def supported_fields() -> List[str]:
    """The logical rule fields the resolver understands natively (for the UI)."""
    # De-dupe the tag/tags alias for a clean list.
    seen: set = set()
    out: List[str] = []
    for f in _FIELD_PATHS:
        key = "tag" if f in ("tag", "tags") else f
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out
