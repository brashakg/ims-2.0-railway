"""
Hub Phase 3 -- vendor price-list import: the PURE match/map core.

Locks the headline owner requirement (docs/roadmap/PRODUCT_HUB_RECOMMENDATION.md
sec 5.2): a vendor SKU that differs from the IMS master only by punctuation /
leading zeros must MATCH -- owner example IMS "RB 3025 001/21" vs vendor
"0RB3025001/21". Also covers SUGGESTED (fuzzy) vs NEW classification, the alias
flywheel, the row->canonical-payload mapping (as_draft), and CSV parsing.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_hub_phase3_import.py -q
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")

from api.services import catalog_import as ci  # noqa: E402

# ---------------------------------------------------------------------------
# normalize_sku + similarity
# ---------------------------------------------------------------------------


def test_normalize_folds_punctuation_and_leading_zeros():
    # the owner's headline example
    assert ci.normalize_sku("RB 3025 001/21") == ci.normalize_sku("0RB3025001/21")
    assert ci.normalize_sku("RB 3025 001/21") == "RB302500121"
    # case + dashes + dots all fold
    assert ci.normalize_sku("rb-3025-001.21") == "RB302500121"


def test_normalize_blank_and_none():
    assert ci.normalize_sku(None) == ""
    assert ci.normalize_sku("   ") == ""
    assert ci.normalize_sku("000") == ""  # all-zeros -> empty after lstrip


def test_similarity_identical_normalized_is_one():
    assert ci.sku_similarity("RB 3025 001/21", "0RB3025001/21") == 1.0
    assert ci.sku_similarity("ABC", "") == 0.0
    assert 0.0 < ci.sku_similarity("RB3025", "RB3026") < 1.0


# ---------------------------------------------------------------------------
# classify_vendor_sku: MATCHED / SUGGESTED / NEW
# ---------------------------------------------------------------------------

_PRODUCTS = [
    {"product_id": "P-RB", "sku": "RB 3025 001/21"},
    {"product_id": "P-OAK", "sku": "OX8046-0152"},
]


def test_classify_alias_hit_is_matched():
    idx = ci.build_alias_index([{"vendor_sku": "0RB3025001/21", "product_id": "P-RB"}])
    out = ci.classify_vendor_sku("0RB3025001/21", alias_index=idx, products=[])
    assert out["status"] == ci.MATCH_MATCHED
    assert out["product_id"] == "P-RB"
    assert out["score"] == 1.0


def test_classify_normalized_exact_is_matched_without_alias():
    out = ci.classify_vendor_sku("0RB3025001/21", alias_index={}, products=_PRODUCTS)
    assert out["status"] == ci.MATCH_MATCHED
    assert out["product_id"] == "P-RB"  # normalized-exact against the spine SKU


def test_classify_fuzzy_is_suggested():
    # one transposed/extra char -> high similarity but not exact -> SUGGESTED
    out = ci.classify_vendor_sku(
        "RB 3025 001/2", alias_index={}, products=_PRODUCTS, threshold=0.8
    )
    assert out["status"] == ci.MATCH_SUGGESTED
    assert out["product_id"] == "P-RB"
    assert 0.8 <= out["score"] < 1.0


def test_classify_unrelated_is_new():
    out = ci.classify_vendor_sku("ZZZ-9999", alias_index={}, products=_PRODUCTS)
    assert out["status"] == ci.MATCH_NEW
    assert out["product_id"] is None


def test_classify_blank_is_new():
    out = ci.classify_vendor_sku("", alias_index={}, products=_PRODUCTS)
    assert out["status"] == ci.MATCH_NEW


def test_classify_is_deterministic_on_ties():
    # two products with the same SKU -> stable pick (smallest product_id)
    prods = [
        {"product_id": "P-Z", "sku": "AB1234"},
        {"product_id": "P-A", "sku": "AB1234"},
    ]
    out = ci.classify_vendor_sku("AB1234", alias_index={}, products=prods)
    assert out["status"] == ci.MATCH_MATCHED
    # normalized-exact returns on first hit; ensure repeated runs agree
    out2 = ci.classify_vendor_sku("AB1234", alias_index={}, products=prods)
    assert out["product_id"] == out2["product_id"]


# ---------------------------------------------------------------------------
# column mapping + row -> payload
# ---------------------------------------------------------------------------


def test_guess_column_map_detects_common_headers():
    headers = ["Item Code", "Brand", "Model No", "Colour", "MRP", "Dealer Price", "HSN"]
    m = ci.guess_column_map(headers)
    assert m["sku"] == "Item Code"
    assert m["brand_name"] == "Brand"
    assert m["model_no"] == "Model No"
    assert m["colour_code"] == "Colour"
    assert m["mrp"] == "MRP"
    assert m["cost_price"] == "Dealer Price"
    assert m["hsn_code"] == "HSN"


def test_map_row_builds_as_draft_payload_with_clean_money():
    row = {
        "Item Code": "0RB3025001/21",
        "Brand": "Ray-Ban",
        "Model No": "3025",
        "Colour": "001/21",
        "MRP": "Rs. 7,990.00",
        "Dealer Price": "4,500",
        "HSN": "9004",
    }
    m = ci.guess_column_map(list(row.keys()))
    payload = ci.map_row_to_product(row, m)
    assert payload["as_draft"] is True
    assert payload["sku"] == "0RB3025001/21"
    assert payload["attributes"]["brand_name"] == "Ray-Ban"
    assert payload["attributes"]["model_no"] == "3025"
    assert payload["attributes"]["colour_code"] == "001/21"
    assert payload["mrp"] == 7990.0  # currency + commas stripped
    assert payload["cost_price"] == 4500.0
    assert payload["hsn_code"] == "9004"


def test_map_row_omits_absent_fields():
    payload = ci.map_row_to_product({"sku": "X"}, {"sku": "sku"})
    assert payload["sku"] == "X"
    assert payload["as_draft"] is True
    assert "mrp" not in payload and "cost_price" not in payload


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def test_parse_csv_rows():
    text = "sku,brand,mrp\nRB-1,RayBan,5000\nOX-2,Oakley,6000\n"
    rows = ci.parse_csv(text)
    assert len(rows) == 2
    assert rows[0]["sku"] == "RB-1" and rows[0]["brand"] == "RayBan"
    assert rows[1]["mrp"] == "6000"


def test_parse_csv_empty():
    assert ci.parse_csv("") == []
