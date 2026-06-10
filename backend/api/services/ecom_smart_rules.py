"""
IMS 2.0 - E-commerce Smart-Collection Rule Resolver  (BVI Phase 2)
==================================================================
Pure, DB-free evaluator for SMART `ecom_collections`. Given a collection's
`rules` (a list of field rules) + `disjunctive` flag, it decides which catalog
products belong, mirroring Shopify smart-collection semantics
(BVI_MERGE_PLAN.md Phase 2; BVI Collection.rules / disjunctive).

A rule is ``{"field": <name>, "relation": <EQUALS|CONTAINS|...>, "value": <str>}``.
``disjunctive=True`` -> a product matches if ANY rule matches (OR);
``disjunctive=False`` (default) -> ALL rules must match (AND).

SHOPIFY-SHAPE NORMALIZATION (BVI revive): the 1,160 collections migrated from
BVI (scripts/migrate_bvi_pim.py) carry rules in Shopify's smart-collection
shape ``{"column": <VENDOR|TYPE|TAG|...>, "relation": ..., "condition": ...}``.
``normalize_rule`` / ``normalize_rules`` fold that shape into the IMS shape
above (column -> field via SHOPIFY_COLUMN_MAP, condition -> value), IDEMPOTENTLY
-- a rule already in the IMS shape passes through untouched, so callers
normalize on EVERY read and the stored docs are never rewritten. The evaluator
(`_rule_matches`) normalizes internally, so migrated collections resolve
without any caller change.

SUPPORTED FIELDS (case-insensitive, alias-folded) and where each is read from a
catalog_products doc -- IMS stores brand inside `attributes`, category top-level,
and storefront tags under the optional `ecom.seo.tags`:

    brand     -> doc["attributes"]["brand"]   (fallbacks: attributes.brand_name,
                                               top-level doc["brand"])
    category  -> doc["category"]               (e.g. FRAME / SUNGLASS)
    tag/tags  -> doc["ecom"]["seo"]["tags"]    (list; also accepts CSV string +
                                                top-level doc["tags"])
    title     -> doc["title"]
    sku       -> doc["sku"]
    price     -> doc["pricing"]["offer_price"] (fallbacks: offer_price, price,
                                                pricing.mrp, mrp)

Anything else is treated as a direct dotted/loose key lookup against the doc
(top-level then inside `attributes`), so a niche attribute like "shape" still
works without a code change.

MATCH SEMANTICS:
  - EQUALS        : case-insensitive exact match (for a list field: any element
                    equals the value).
  - NOT_EQUALS    : NO element equals the value (vacuously True when the field
                    is absent -- a product with no tags does NOT have tag X).
  - CONTAINS      : case-insensitive substring (for a list field: any element
                    contains the value).
  - NOT_CONTAINS  : NO element contains the value (vacuously True when absent).
  - STARTS_WITH / ENDS_WITH : case-insensitive prefix / suffix on any element.
  - GREATER_THAN / LESS_THAN : numeric compare (any element parseable as a
                    number satisfies it; non-numeric -> no match, never raises).
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
    # brand_name: BVI-migrated catalog docs store the display brand there.
    "brand": [("attributes", "brand"), ("attributes", "brand_name"), ("brand",)],
    "category": [("category",), ("attributes", "category")],
    "tag": [("ecom", "seo", "tags"), ("tags",), ("attributes", "tags")],
    "tags": [("ecom", "seo", "tags"), ("tags",), ("attributes", "tags")],
    "title": [("title",)],
    "sku": [("sku",)],
    "price": [
        ("pricing", "offer_price"),
        ("offer_price",),
        ("price",),
        ("pricing", "mrp"),
        ("mrp",),
    ],
    "shape": [("attributes", "shape"), ("shape",)],
    "gender": [("attributes", "gender"), ("gender",)],
    "frame_material": [("attributes", "frame_material"), ("frame_material",)],
    "color": [("attributes", "color"), ("color",)],
}

_EQUALS = "EQUALS"
_NOT_EQUALS = "NOT_EQUALS"
_CONTAINS = "CONTAINS"
_NOT_CONTAINS = "NOT_CONTAINS"
_STARTS_WITH = "STARTS_WITH"
_ENDS_WITH = "ENDS_WITH"
_GREATER_THAN = "GREATER_THAN"
_LESS_THAN = "LESS_THAN"

# Shopify smart-collection rule column -> IMS logical field. Lookup key is the
# _norm_field()'d column, so VENDOR / vendor / Vendor all fold the same way.
SHOPIFY_COLUMN_MAP: Dict[str, str] = {
    "vendor": "brand",
    "type": "category",
    "product_type": "category",
    "tag": "tag",
    "title": "title",
    "variant_sku": "sku",
    "variant_price": "price",
}


def _norm_field(field: Optional[str]) -> str:
    """Normalise a rule field name: trim, lower, spaces/hyphens -> '_'."""
    if not field:
        return ""
    return field.strip().lower().replace("-", "_").replace(" ", "_")


def _to_float(value: Any) -> Optional[float]:
    """Best-effort numeric coercion ('7,999.00' -> 7999.0), None on failure."""
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def normalize_rule(rule: Any) -> Any:
    """Normalise ONE rule to the IMS shape ``{field, relation, value}``.

    The BVI-migrated collections store rules in Shopify's shape
    ``{"column": "VENDOR", "relation": "EQUALS", "condition": "Ray-Ban"}``.
    This translates: column -> field (via SHOPIFY_COLUMN_MAP), condition ->
    value, relation upper-cased. IDEMPOTENT + READ-SIDE ONLY: a rule already in
    the IMS shape is returned untouched (same object), so callers can normalise
    on every read and the migrated docs are never rewritten on disk.

    An UNKNOWN Shopify column (e.g. VARIANT_WEIGHT) is passed through as a
    loose-lookup field (lower-snake of the column) and marked
    ``passthrough=True`` with the original ``column`` preserved for fidelity --
    the evaluator's loose attribute lookup may still match it; it never raises.
    Non-dict input is returned as-is (the evaluator skips it).
    """
    if not isinstance(rule, dict):
        return rule
    field = rule.get("field")
    value = rule.get("value")
    if field not in (None, "") and value is not None:
        return rule  # already IMS-shaped -- idempotent passthrough
    if "column" not in rule and "condition" not in rule:
        return rule  # neither shape; the evaluator skips it as malformed

    column = rule.get("column")
    norm_col = _norm_field(str(column)) if column not in (None, "") else ""
    mapped = SHOPIFY_COLUMN_MAP.get(norm_col)
    # Prefer the Shopify condition; tolerate a half-shaped {field, condition}.
    raw_value = rule.get("condition")
    if raw_value is None:
        raw_value = value
    out: Dict[str, Any] = {
        "field": mapped or norm_col or _norm_field(str(field or "")),
        "relation": str(rule.get("relation") or _EQUALS).strip().upper(),
        "value": "" if raw_value is None else str(raw_value),
    }
    if norm_col and mapped is None:
        # Unknown column: keep it resolvable (loose lookup) + traceable.
        out["passthrough"] = True
        out["column"] = column
    return out


def normalize_rules(rules: Any) -> List[Any]:
    """Normalise a rule LIST via ``normalize_rule``. Fail-soft: a non-list
    input yields ``[]``; list members that are not dicts pass through (the
    evaluator skips them)."""
    if not isinstance(rules, (list, tuple)):
        return []
    return [normalize_rule(r) for r in rules]


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
    rule is malformed (missing field or value) so the caller can skip it.

    Shopify-shape rules ({column, relation, condition} -- the BVI-migrated
    docs) are normalised on the fly; native IMS rules pass through untouched.
    """
    rule = normalize_rule(rule)
    if not isinstance(rule, dict):
        return None
    field = rule.get("field")
    value = rule.get("value")
    if not field or value is None or str(value).strip() == "":
        return None

    relation = str(rule.get("relation") or _EQUALS).strip().upper()
    needle = str(value).strip().lower()
    candidates = [c.lower() for c in _extract_values(doc, field)]

    # Negative relations are vacuously TRUE on an absent field (a product with
    # no tags does NOT have tag X) -- mirrors Shopify smart-collection logic.
    if relation == _NOT_EQUALS:
        return all(c != needle for c in candidates)
    if relation == _NOT_CONTAINS:
        return all(needle not in c for c in candidates)

    if not candidates:
        return False

    if relation == _CONTAINS:
        return any(needle in c for c in candidates)
    if relation == _STARTS_WITH:
        return any(c.startswith(needle) for c in candidates)
    if relation == _ENDS_WITH:
        return any(c.endswith(needle) for c in candidates)
    if relation in (_GREATER_THAN, _LESS_THAN):
        needle_num = _to_float(needle)
        if needle_num is None:
            return False  # fail-soft: non-numeric bound never matches
        nums = [n for n in (_to_float(c) for c in candidates) if n is not None]
        if not nums:
            return False
        if relation == _GREATER_THAN:
            return any(n > needle_num for n in nums)
        return any(n < needle_num for n in nums)
    # Default + EQUALS: exact (case-insensitive) match on any candidate.
    return any(c == needle for c in candidates)


def matches_product(
    product: Dict, rules: List[Dict], disjunctive: bool = False
) -> bool:
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
SUPPORTED_RELATIONS = [
    _EQUALS,
    _NOT_EQUALS,
    _CONTAINS,
    _NOT_CONTAINS,
    _STARTS_WITH,
    _ENDS_WITH,
    _GREATER_THAN,
    _LESS_THAN,
]


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
