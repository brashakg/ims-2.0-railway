"""
IMS 2.0 - E-commerce Smart-Collection RULE SHAPES  (normalisation only)
======================================================================
A SMART ``ecom_collections`` rule is stored in one of two shapes:

  IMS      ``{"field": <name>, "relation": <EQUALS|CONTAINS|...|IN>, "value": ...}``
  Shopify  ``{"column": <VENDOR|TYPE|TAG|...>, "relation": ..., "condition": ...}``
           (the collections migrated from BVI, scripts/migrate_bvi_pim.py)

``normalize_rule`` / ``normalize_rules`` fold the Shopify shape into the IMS
shape (column -> field via SHOPIFY_COLUMN_MAP, condition -> value),
IDEMPOTENTLY -- a rule already in the IMS shape passes through, so callers
normalise on every read and stored docs are never rewritten.

THIS MODULE NO LONGER DECIDES MEMBERSHIP (owner 2026-10-29). The old
evaluator here read brand / category / tags off the raw catalogue doc, which
is not what the push sends (the website's tags are generated at push time),
so IMS said "0 products" for every collection the registry makes. Which
products a collection holds is answered ONLY by
``services/collection_membership`` -- Shopify's reading of the rules (through
``shopify_push.collections.convert_rule_set``) over what the push sends.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


_EQUALS = "EQUALS"
_IN = "IN"

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


def _clean_in_values(value: Any) -> List[str]:
    """Coerce an IN rule's value into a list of trimmed, NON-EMPTY strings.

    Entries are stringified + trimmed; blanks and None entries are dropped. A
    scalar folds into a single-item list (fail-soft -- a one-value IN then
    behaves exactly like EQUALS). Returns [] for None / an empty or
    blank-only list -- an IN rule with [] never matches."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


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
    # IN (additive, native-shape only -- no legacy Shopify rule carries IN):
    # the value STAYS a list, normalised to trimmed non-empty strings (blanks
    # dropped; empty list kept -- a valid rule that never matches). Handled
    # BEFORE the generic passthrough so the list is always cleaned, and
    # idempotent: re-normalising a clean IN rule yields an equal rule.
    if (
        str(rule.get("relation") or "").strip().upper() == _IN
        and field not in (None, "")
    ):
        if value is None:
            return rule  # malformed (no value) -- the evaluator skips it
        out_in = dict(rule)
        out_in["relation"] = _IN
        out_in["value"] = _clean_in_values(value)
        return out_in
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
