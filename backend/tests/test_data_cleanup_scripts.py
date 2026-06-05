"""
Unit tests for scripts/prod_data_cleanup.py -- PURE helper functions only.

No database is needed: the helpers under test (generate_stable_product_id,
plan_sku_merge, plan_customer_merge) are pure functions that operate only on
plain dicts. DB-touching code (run_inv2 / run_inv3 / run_inv4 / run_ops3) is
exercised separately in integration via Railway run; we only unit-test here.

Coverage
--------
  generate_stable_product_id
    - uses sku seed when sku is present
    - falls back to _id seed when sku is absent / empty
    - same doc -> same id (determinism / idempotency)
    - different docs -> different ids (collision-resistance for realistic inputs)
    - output format matches prod_{12-hex} template

  plan_sku_merge
    - canonical = the doc with a product_id already set
    - canonical = the most-complete doc when no product_id on any
    - duplicate docs get _dup_N suffix names
    - single-element list returns no duplicates
    - empty list returns empty dict

  plan_customer_merge
    - canonical = doc with most orders (via order_counts)
    - tie-break: most non-null fields
    - each duplicate gets a freshly-generated new_customer_id
    - new_customer_ids are unique (no two duplicates get the same id)
    - empty list returns empty dict
"""
from __future__ import annotations

import hashlib
import re
import sys
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Import path: make scripts/ importable from backend/tests/
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import pytest

from prod_data_cleanup import (
    generate_stable_product_id,
    plan_sku_merge,
    plan_customer_merge,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _product(
    _id: str = "oid_001",
    sku: str = "ASPIRE-PRO-BLK",
    product_id: str = None,
    created_at: datetime = None,
    extra_fields: int = 0,
) -> dict:
    doc: dict = {"_id": _id, "sku": sku}
    if product_id is not None:
        doc["product_id"] = product_id
    if created_at is not None:
        doc["created_at"] = created_at
    for i in range(extra_fields):
        doc[f"field_{i}"] = f"value_{i}"
    return doc


def _customer(
    _id: str = "coid_001",
    customer_id: str = "CUST-001",
    name: str = "Test Customer",
    mobile: str = "9876543210",
    created_at: datetime = None,
    extra_fields: int = 0,
) -> dict:
    doc: dict = {
        "_id": _id,
        "customer_id": customer_id,
        "name": name,
        "mobile": mobile,
    }
    if created_at is not None:
        doc["created_at"] = created_at
    for i in range(extra_fields):
        doc[f"field_{i}"] = f"value_{i}"
    return doc


# ---------------------------------------------------------------------------
# Tests: generate_stable_product_id
# ---------------------------------------------------------------------------

class TestGenerateStableProductId:

    def test_format_matches_prod_prefix_12hex(self):
        doc = _product(_id="abc123", sku="FRAME-001")
        pid = generate_stable_product_id(doc)
        assert re.fullmatch(r"prod_[0-9a-f]{12}", pid), (
            f"Expected prod_{{12-hex}} format, got: {pid}"
        )

    def test_uses_sku_as_seed_when_present(self):
        sku = "ZEISS-LENS-UV"
        doc = _product(_id="different_id", sku=sku)
        expected_digest = hashlib.sha256(sku.encode()).hexdigest()[:12]
        assert generate_stable_product_id(doc) == f"prod_{expected_digest}"

    def test_falls_back_to_id_when_sku_absent(self):
        _id = "mongo_oid_999"
        doc = {"_id": _id}  # no sku key
        expected_digest = hashlib.sha256(_id.encode()).hexdigest()[:12]
        assert generate_stable_product_id(doc) == f"prod_{expected_digest}"

    def test_falls_back_to_id_when_sku_is_empty_string(self):
        _id = "mongo_oid_888"
        doc = {"_id": _id, "sku": ""}
        expected_digest = hashlib.sha256(_id.encode()).hexdigest()[:12]
        assert generate_stable_product_id(doc) == f"prod_{expected_digest}"

    def test_falls_back_to_id_when_sku_is_none(self):
        _id = "mongo_oid_777"
        doc = {"_id": _id, "sku": None}
        expected_digest = hashlib.sha256(_id.encode()).hexdigest()[:12]
        assert generate_stable_product_id(doc) == f"prod_{expected_digest}"

    def test_deterministic_same_doc_same_id(self):
        doc = _product(_id="stable_oid", sku="LENSCRAFT-BIFOCAL")
        id1 = generate_stable_product_id(doc)
        id2 = generate_stable_product_id(doc)
        assert id1 == id2

    def test_different_skus_give_different_ids(self):
        doc_a = _product(_id="oid_a", sku="SKU-ALPHA")
        doc_b = _product(_id="oid_b", sku="SKU-BETA")
        assert generate_stable_product_id(doc_a) != generate_stable_product_id(doc_b)

    def test_different_ids_when_no_sku_give_different_product_ids(self):
        doc_a = {"_id": "id_alpha"}
        doc_b = {"_id": "id_beta"}
        assert generate_stable_product_id(doc_a) != generate_stable_product_id(doc_b)

    def test_realistic_sku_collision_resistance(self):
        """10k distinct SKUs should produce 10k distinct product_ids."""
        skus = {f"SKU-{i:05d}" for i in range(10_000)}
        ids = {
            generate_stable_product_id({"_id": f"oid_{i}", "sku": sku})
            for i, sku in enumerate(skus)
        }
        assert len(ids) == len(skus), "Hash collision detected across 10k SKUs"


# ---------------------------------------------------------------------------
# Tests: plan_sku_merge
# ---------------------------------------------------------------------------

class TestPlanSkuMerge:

    def test_empty_list_returns_empty_dict(self):
        assert plan_sku_merge([]) == {}

    def test_single_doc_returns_no_duplicates(self):
        doc = _product(_id="oid_1", sku="SOLO-SKU", product_id="prod_abc")
        plan = plan_sku_merge([doc])
        assert plan["canonical_id"] == "oid_1"
        assert plan["duplicates"] == []

    def test_canonical_is_doc_with_product_id(self):
        """When one doc has product_id and another doesn't, the one with it wins."""
        doc_with_pid = _product(_id="oid_A", sku="DUP-SKU", product_id="prod_existing")
        doc_without_pid = _product(_id="oid_B", sku="DUP-SKU", product_id=None)
        plan = plan_sku_merge([doc_without_pid, doc_with_pid])  # order reversed
        assert plan["canonical_id"] == "oid_A"
        assert len(plan["duplicates"]) == 1
        assert plan["duplicates"][0]["_id"] == "oid_B"

    def test_canonical_falls_back_to_most_complete_when_no_product_id(self):
        """Without product_ids, the doc with the most non-null fields is canonical."""
        sparse = _product(_id="oid_sparse", sku="DUP-SKU", extra_fields=2)
        rich = _product(_id="oid_rich", sku="DUP-SKU", extra_fields=10)
        plan = plan_sku_merge([sparse, rich])
        assert plan["canonical_id"] == "oid_rich"

    def test_duplicate_gets_dup_suffix(self):
        doc_a = _product(_id="oid_A", sku="ASPIRE-PRO", product_id="prod_canonical")
        doc_b = _product(_id="oid_B", sku="ASPIRE-PRO")
        plan = plan_sku_merge([doc_a, doc_b])
        dup_skus = [d["new_sku"] for d in plan["duplicates"]]
        assert "ASPIRE-PRO_dup_1" in dup_skus

    def test_multiple_duplicates_get_sequential_suffixes(self):
        docs = [
            _product(_id=f"oid_{i}", sku="MULTI-DUP") for i in range(4)
        ]
        plan = plan_sku_merge(docs)
        dup_skus = sorted(d["new_sku"] for d in plan["duplicates"])
        assert dup_skus == [
            "MULTI-DUP_dup_1",
            "MULTI-DUP_dup_2",
            "MULTI-DUP_dup_3",
        ]

    def test_plan_contains_canonical_sku(self):
        docs = [_product(_id="oid_1", sku="FRAME-GOLD"), _product(_id="oid_2", sku="FRAME-GOLD")]
        plan = plan_sku_merge(docs)
        assert plan["sku"] == "FRAME-GOLD"

    def test_duplicate_ids_are_strings(self):
        """_id values in the plan must be strings (not ObjectId) for safe downstream use."""
        docs = [_product(_id="oid_X", sku="TEST"), _product(_id="oid_Y", sku="TEST")]
        plan = plan_sku_merge(docs)
        for dup in plan["duplicates"]:
            assert isinstance(dup["_id"], str)


# ---------------------------------------------------------------------------
# Tests: plan_customer_merge
# ---------------------------------------------------------------------------

class TestPlanCustomerMerge:

    def test_empty_list_returns_empty_dict(self):
        assert plan_customer_merge([], {}) == {}

    def test_canonical_is_doc_with_most_orders(self):
        doc_a = _customer(_id="coid_A", customer_id="CUST-DUP")
        doc_b = _customer(_id="coid_B", customer_id="CUST-DUP")
        order_counts = {"coid_A": 5, "coid_B": 0}
        plan = plan_customer_merge([doc_b, doc_a], order_counts)  # reversed order
        assert plan["canonical_oid"] == "coid_A"
        assert len(plan["duplicates"]) == 1
        assert plan["duplicates"][0]["_id"] == "coid_B"

    def test_tie_break_most_complete_doc(self):
        """When both have 0 orders, the richer doc wins."""
        sparse = _customer(_id="coid_sparse", customer_id="CUST-TIE", extra_fields=1)
        rich = _customer(_id="coid_rich", customer_id="CUST-TIE", extra_fields=8)
        plan = plan_customer_merge([sparse, rich], {})
        assert plan["canonical_oid"] == "coid_rich"

    def test_duplicates_get_new_customer_ids(self):
        doc_a = _customer(_id="coid_A", customer_id="CUST-DUP")
        doc_b = _customer(_id="coid_B", customer_id="CUST-DUP")
        plan = plan_customer_merge([doc_a, doc_b], {"coid_A": 3, "coid_B": 0})
        assert len(plan["duplicates"]) == 1
        new_cid = plan["duplicates"][0]["new_customer_id"]
        assert new_cid.startswith("cust_"), f"Expected cust_ prefix, got {new_cid}"
        assert len(new_cid) == len("cust_") + 12  # cust_ + 12 hex chars

    def test_new_customer_ids_are_unique_across_duplicates(self):
        """Each duplicate must receive a distinct new_customer_id (no collisions)."""
        docs = [_customer(_id=f"coid_{i}", customer_id="CUST-MULTI") for i in range(5)]
        order_counts = {}
        plan = plan_customer_merge(docs, order_counts)
        new_ids = [d["new_customer_id"] for d in plan["duplicates"]]
        assert len(new_ids) == len(set(new_ids)), "Duplicate new_customer_ids generated"

    def test_plan_preserves_canonical_customer_id(self):
        doc_a = _customer(_id="coid_A", customer_id="CUST-42", name="Ramesh")
        doc_b = _customer(_id="coid_B", customer_id="CUST-42")
        plan = plan_customer_merge([doc_a, doc_b], {"coid_A": 10})
        assert plan["customer_id"] == "CUST-42"

    def test_plan_includes_canonical_name_and_mobile(self):
        doc_a = _customer(_id="coid_A", customer_id="CUST-99", name="Suresh", mobile="9000000001")
        doc_b = _customer(_id="coid_B", customer_id="CUST-99", name="Suresh B", mobile="9000000002")
        plan = plan_customer_merge([doc_a, doc_b], {"coid_A": 7, "coid_B": 0})
        assert plan["canonical_name"] == "Suresh"
        assert plan["canonical_mobile"] == "9000000001"

    def test_single_doc_no_duplicates(self):
        doc = _customer(_id="coid_only", customer_id="CUST-SOLO")
        plan = plan_customer_merge([doc], {})
        assert plan["canonical_oid"] == "coid_only"
        assert plan["duplicates"] == []

    def test_duplicate_ids_are_strings(self):
        doc_a = _customer(_id="coid_A", customer_id="CUST-STR")
        doc_b = _customer(_id="coid_B", customer_id="CUST-STR")
        plan = plan_customer_merge([doc_a, doc_b], {})
        for dup in plan["duplicates"]:
            assert isinstance(dup["_id"], str)
