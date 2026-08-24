"""Class-wide guards over EVERY schema in ``COLLECTIONS``.

SCOPE -- READ THIS BEFORE TRUSTING IT
=====================================
This file does NOT claim that all 36 schemas match their writers. Only
``EXPENSE_SCHEMA`` has been driven end-to-end against its real router
(``test_expense_schema_parity.py``); several others are known-rotten and are
recorded as such in ``WRITER_PARITY_STATUS`` below. Claiming more coverage than
that is exactly the mistake this repo has already made twice.

What IS proved here, for every collection:

  1. Structural sanity -- the schema is a well-formed ``$jsonSchema`` object,
     every ``required`` name is a declared property, and every keyword and
     ``bsonType`` is one that ``bson_schema_check`` genuinely implements (so a
     parity test can never be silently short-circuited by an unknown keyword).
  2. A RATCHET on ``bsonType: "decimal"``. Nothing in ``backend/`` ever
     constructs a ``Decimal128`` -- the money paths are Python floats, which
     serialise to BSON doubles. So every field still declared ``"decimal"`` is
     guaranteed to reject its own writer's value the moment migrations run. The
     exact surviving set is frozen below: a NEW one cannot be added, and fixing
     one forces this list to shrink deliberately.
  3. Every collection is classified in ``WRITER_PARITY_STATUS``, and the
     classification keys must equal the COLLECTIONS keys -- so a new collection
     cannot be added without someone stating whether its schema was checked.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from database.schemas import COLLECTIONS  # noqa: E402
from bson_schema_check import _SUPPORTED_KEYWORDS  # noqa: E402

_KNOWN_BSON_TYPES = {
    "string", "object", "array", "bool", "int", "long",
    "double", "decimal", "date", "null", "number", "objectId",
}


# ---------------------------------------------------------------------------
# Writer-parity status. VERIFIED means a test drives the real writer and
# validates the stored document. ROTTEN means an audit found concrete
# schema-vs-writer disagreements that are NOT yet fixed. UNAUDITED means nobody
# has checked -- treat it as unknown, not as safe.
# ---------------------------------------------------------------------------
VERIFIED = "VERIFIED"
ROTTEN = "ROTTEN"
UNAUDITED = "UNAUDITED"
# Deliberately validates nothing but "is an object" -- vacuously safe to apply,
# so there is no writer parity to check. See the note above CATALOG_PRODUCT_SCHEMA.
SCHEMALESS = "SCHEMALESS"

# Collections whose schema is intentionally `{"bsonType": "object"}`.
SCHEMALESS_COLLECTIONS = {"catalog_products"}

WRITER_PARITY_STATUS = {
    # Fixed + pinned by test_expense_schema_parity.py.
    "expenses": VERIFIED,
    # Audited 2026-08-24; concrete disagreements recorded in the PR. NOT fixed.
    "users": ROTTEN,
    "products": ROTTEN,
    "customers": ROTTEN,
    "prescriptions": ROTTEN,
    "orders": ROTTEN,
    "vendors": ROTTEN,
    "purchase_orders": ROTTEN,
    "tasks": ROTTEN,
    "audit_logs": ROTTEN,
    "notifications": ROTTEN,
    # Audited and found to AGREE with its writers apart from three optional
    # string fields that can be written as null (display_fixtures) -- see PR.
    "display_fixtures": ROTTEN,
    "display_placements": ROTTEN,
    # Partially audited (the audit was truncated) or not audited at all.
    "stores": UNAUDITED,
    "stock_units": UNAUDITED,
    "grns": UNAUDITED,
    "attendance": UNAUDITED,
    "leaves": UNAUDITED,
    "shifts": UNAUDITED,
    "weekoff_swaps": UNAUDITED,
    "payroll": UNAUDITED,
    "workshop_jobs": UNAUDITED,
    "advances": UNAUDITED,
    "entities": UNAUDITED,
    "salary_config": UNAUDITED,
    "pt_slabs": UNAUDITED,
    "lens_catalog": UNAUDITED,
    "lens_stock_lines": UNAUDITED,
    "lens_stock_audit": UNAUDITED,
    "lens_enum_config": UNAUDITED,
    "catalog_products": SCHEMALESS,
    "catalog_variants": UNAUDITED,
    "ecom_collections": UNAUDITED,
    "ecom_menus": UNAUDITED,
    "product_images": UNAUDITED,
    "vendor_bills": UNAUDITED,
}


def _walk(node, path, collection, visit):
    if not isinstance(node, dict):
        raise AssertionError(f"{collection}{path}: schema node is not a dict")
    visit(node, path, collection)
    for key, child in (node.get("properties") or {}).items():
        _walk(child, f"{path}.{key}", collection, visit)
    if "items" in node:
        _walk(node["items"], f"{path}[]", collection, visit)


def _all_nodes():
    found = []
    for name, config in COLLECTIONS.items():
        _walk(config["schema"], "", name,
              lambda node, path, coll: found.append((coll, path, node)))
    return found


# ===========================================================================
# 1. Structural sanity
# ===========================================================================


@pytest.mark.parametrize("name", sorted(COLLECTIONS))
def test_collection_entry_is_well_formed(name):
    config = COLLECTIONS[name]
    assert set(config) >= {"schema", "indexes"}
    schema = config["schema"]
    assert isinstance(schema, dict)
    assert schema.get("bsonType") == "object", f"{name}: root must be an object schema"
    if name in SCHEMALESS_COLLECTIONS:
        # Intentionally permissive: registered for tooling parity only.
        assert set(schema) == {"bsonType"}, f"{name}: schemaless entry grew keywords"
        return
    assert isinstance(schema.get("properties"), dict), f"{name}: no properties declared"


def test_every_required_field_is_a_declared_property():
    """A `required` name with no property declaration is a schema that demands a
    field it says nothing about -- almost always a leftover from a design that
    was never built."""
    offenders = []
    for collection, path, node in _all_nodes():
        properties = node.get("properties") or {}
        for field in node.get("required") or []:
            if field not in properties:
                offenders.append(f"{collection}{path}.{field}")
    assert offenders == [], f"required-but-undeclared: {offenders}"


def test_every_keyword_and_bson_type_is_one_the_checker_implements():
    """Guarantees the parity checker can never be silently short-circuited: if a
    schema starts using a keyword the checker skips, this fails here instead of
    quietly weakening every assert_valid in the suite."""
    bad_keywords, bad_types = [], []
    for collection, path, node in _all_nodes():
        for keyword in set(node) - _SUPPORTED_KEYWORDS:
            bad_keywords.append(f"{collection}{path}: {keyword}")
        declared = node.get("bsonType")
        for bson_type in (declared if isinstance(declared, list) else [declared] if declared else []):
            if bson_type not in _KNOWN_BSON_TYPES:
                bad_types.append(f"{collection}{path}: {bson_type}")
    assert bad_keywords == [], f"unimplemented keywords: {bad_keywords}"
    assert bad_types == [], f"unknown bsonTypes: {bad_types}"


def test_enums_are_non_empty_lists_of_scalars():
    offenders = []
    for collection, path, node in _all_nodes():
        if "enum" not in node:
            continue
        values = node["enum"]
        if not isinstance(values, list) or not values:
            offenders.append(f"{collection}{path}: {values!r}")
        elif len(set(map(repr, values))) != len(values):
            offenders.append(f"{collection}{path}: duplicate enum values")
    assert offenders == []


# ===========================================================================
# 2. The Decimal128 ratchet
# ===========================================================================

# Every entry here is a KNOWN DEFECT: the field is declared Decimal128 but its
# writer stores a Python float (a BSON double), so applying the validator would
# reject the write. Frozen so no new one can appear; shrink it as they are fixed.
KNOWN_DECIMAL_FIELDS = frozenset({
    "products.mrp", "products.offer_price", "products.cost_price",
    "customers.store_credit", "customers.total_purchases",
    "orders.items[].unit_price", "orders.items[].discount_amount",
    "orders.items[].tax_amount", "orders.items[].total",
    "orders.subtotal", "orders.discount_total", "orders.tax_total",
    "orders.grand_total", "orders.payments[].amount",
    "orders.amount_paid", "orders.balance_due",
    "vendors.opening_balance", "vendors.current_balance",
    "purchase_orders.items[].unit_price", "purchase_orders.subtotal",
    "purchase_orders.tax_amount", "purchase_orders.total_amount",
    "payroll.basic_salary", "payroll.allowances", "payroll.deductions",
    "payroll.incentives", "payroll.advance_deduction", "payroll.net_salary",
    "advances.amount", "advances.settled_amount",
})


def _decimal_fields():
    found = set()
    for collection, path, node in _all_nodes():
        declared = node.get("bsonType")
        types = declared if isinstance(declared, list) else [declared] if declared else []
        if "decimal" in types:
            found.add(f"{collection}{path}")
    return found


def test_no_new_decimal_typed_field_is_introduced():
    current = _decimal_fields()
    added = current - KNOWN_DECIMAL_FIELDS
    assert added == set(), (
        "New bsonType 'decimal' field(s) declared: "
        f"{sorted(added)}. Nothing in backend/ constructs a Decimal128, so a "
        "'decimal' field is guaranteed to reject its own writer's float. Use "
        "'double'."
    )


def test_the_decimal_baseline_shrinks_deliberately():
    """Fixing one of the known-bad fields must UPDATE this list, so the count
    stays an honest record of remaining debt rather than drifting."""
    current = _decimal_fields()
    removed = KNOWN_DECIMAL_FIELDS - current
    assert removed == set(), (
        f"These 'decimal' fields were fixed but are still listed as known debt: "
        f"{sorted(removed)}. Remove them from KNOWN_DECIMAL_FIELDS."
    )


def test_backend_never_constructs_a_decimal128():
    """The premise the whole ratchet rests on. If this ever fails, the money
    paths grew real Decimal128 support and the 'decimal' declarations may have
    become correct -- re-audit rather than assume."""
    backend = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for source in backend.rglob("*.py"):
        parts = source.parts
        if "tests" in parts or "__pycache__" in parts:
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if "Decimal128(" in text:
            offenders.append(str(source.relative_to(backend)))
    assert offenders == [], f"Decimal128 is now constructed in: {offenders}"


# ===========================================================================
# 3. Every collection is classified
# ===========================================================================


def test_every_collection_has_a_stated_writer_parity_status():
    assert set(WRITER_PARITY_STATUS) == set(COLLECTIONS), (
        "COLLECTIONS and WRITER_PARITY_STATUS disagree. Missing a status: "
        f"{sorted(set(COLLECTIONS) - set(WRITER_PARITY_STATUS))}; stale entries: "
        f"{sorted(set(WRITER_PARITY_STATUS) - set(COLLECTIONS))}"
    )


def test_only_expenses_is_claimed_verified():
    """Honest-coverage tripwire. Marking a collection VERIFIED means a test
    drives its REAL writer and validates the stored document -- if you flip one
    of these, add that test in the same change."""
    verified = {n for n, s in WRITER_PARITY_STATUS.items() if s == VERIFIED}
    assert verified == {"expenses"}


def test_statuses_use_the_defined_vocabulary():
    assert set(WRITER_PARITY_STATUS.values()) <= {VERIFIED, ROTTEN, UNAUDITED, SCHEMALESS}


def test_schemaless_collections_are_labelled_as_such():
    labelled = {n for n, s in WRITER_PARITY_STATUS.items() if s == SCHEMALESS}
    assert labelled == SCHEMALESS_COLLECTIONS
