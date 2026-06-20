"""
Unit tests for scripts/bvi_parity_check.py -- the PURE comparison core.

No database is needed: build_parity_report() is a pure function of two in-memory
snapshots (BviSnapshot, ImsSnapshot), so we exercise every branch with hand-built
fixtures and assert the exact report shape + the gate verdict.

Cases covered:
  1. perfect match      -- equal counts, every SKU + barcode aligned -> gate PASS.
  2. BVI-only SKU       -- a SKU in BVI variants absent from IMS -> missing list, gate FAIL.
  3. mismatched barcode -- same SKU, different storeBarcode vs IMS barcode -> gate FAIL.
  4. count delta        -- product/variant counts differ -> match=False, gate FAIL.
  5. image storage      -- local-disk vs durable url classification + Phase-4 flag.
  6. is_durable_image_url helper -- the local/durable URL classifier directly.
"""
from __future__ import annotations

import os
import sys

# Make the scripts directory importable without installing.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import pytest  # noqa: E402
from bvi_parity_check import (  # noqa: E402
    BviSnapshot,
    ImsSnapshot,
    build_parity_report,
    is_durable_image_url,
    render_text_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _matched_pair():
    """A BVI snapshot and an IMS snapshot that agree on everything."""
    bvi = BviSnapshot(
        products=2,
        variants=3,
        collections=1,
        menus=1,
        customers=5,
        orders=4,
        variant_skus=["FR-RB-2140-086-55", "FR-RB-2140-086-52", "SG-OAK-009-BLK"],
        variant_barcode_by_sku={
            "FR-RB-2140-086-55": "8901234567890",
            "SG-OAK-009-BLK": "8909999999999",
        },
        image_urls=[
            "https://cdn.shopify.com/s/files/1/img1.png",
            "https://cdn.shopify.com/s/files/1/img2.png",
        ],
    )
    ims = ImsSnapshot(
        catalog_products=2,
        catalog_variants=3,
        ecom_collections=1,
        ecom_menus=1,
        customers=5,
        online_orders=4,
        variant_skus=["FR-RB-2140-086-55", "FR-RB-2140-086-52", "SG-OAK-009-BLK"],
        barcode_by_sku={
            "FR-RB-2140-086-55": "8901234567890",
            "SG-OAK-009-BLK": "8909999999999",
        },
    )
    return bvi, ims


# ---------------------------------------------------------------------------
# Case 1: perfect match -> gate PASS
# ---------------------------------------------------------------------------

def test_perfect_match_passes_gate():
    bvi, ims = _matched_pair()
    report = build_parity_report(bvi, ims)

    assert report["gate_pass"] is True
    # every count row matches
    for entity, row in report["counts"].items():
        assert row["match"] is True, f"{entity} should match"
        assert row["delta"] == 0
    # no missing SKUs, no barcode mismatch
    assert report["sku_diff"]["missing_count"] == 0
    assert report["sku_diff"]["missing_in_ims"] == []
    assert report["barcode_diff"]["mismatched_count"] == 0
    # both storeBarcode-bearing variants were checked
    assert report["barcode_diff"]["checked"] == 2
    # render must not raise
    assert "GATE: PASS" in render_text_report(report)


# ---------------------------------------------------------------------------
# Case 2: a SKU present in BVI but missing from IMS -> gate FAIL
# ---------------------------------------------------------------------------

def test_bvi_only_sku_is_flagged_missing():
    bvi, ims = _matched_pair()
    # add a 4th BVI variant that IMS does not have
    bvi.variants = 4
    bvi.variant_skus.append("FR-NEW-ONLY-IN-BVI")
    # IMS variant count stays 3 (the new one never migrated)

    report = build_parity_report(bvi, ims)

    assert report["gate_pass"] is False
    sd = report["sku_diff"]
    assert sd["missing_count"] == 1
    assert "FR-NEW-ONLY-IN-BVI" in sd["missing_in_ims"]
    assert "FR-NEW-ONLY-IN-BVI" in sd["sample_missing"]
    # the variants count row should also show the delta
    assert report["counts"]["variants"]["match"] is False
    assert report["counts"]["variants"]["delta"] == -1  # ims has one fewer


def test_sku_comparison_is_case_insensitive():
    """A casing-only difference must NOT be flagged as missing."""
    bvi = BviSnapshot(products=1, variants=1, variant_skus=["fr-rb-2140"])
    ims = ImsSnapshot(catalog_products=1, catalog_variants=1, variant_skus=["FR-RB-2140"])
    report = build_parity_report(bvi, ims)
    assert report["sku_diff"]["missing_count"] == 0
    assert report["gate_pass"] is True


# ---------------------------------------------------------------------------
# Case 3: mismatched barcode -> gate FAIL
# ---------------------------------------------------------------------------

def test_mismatched_barcode_is_flagged():
    bvi, ims = _matched_pair()
    # IMS has a DIFFERENT barcode for an existing SKU
    ims.barcode_by_sku["FR-RB-2140-086-55"] = "0000000000000"

    report = build_parity_report(bvi, ims)

    assert report["gate_pass"] is False
    bd = report["barcode_diff"]
    assert bd["mismatched_count"] == 1
    m = bd["mismatched"][0]
    assert m["sku"] == "FR-RB-2140-086-55"
    assert m["bvi"] == "8901234567890"
    assert m["ims"] == "0000000000000"


def test_missing_ims_barcode_is_a_mismatch():
    """A BVI storeBarcode whose SKU has NO barcode in IMS counts as mismatched."""
    bvi, ims = _matched_pair()
    # drop the IMS barcode for one SKU entirely
    del ims.barcode_by_sku["FR-RB-2140-086-55"]

    report = build_parity_report(bvi, ims)
    bd = report["barcode_diff"]
    assert bd["mismatched_count"] == 1
    assert bd["mismatched"][0]["ims"] == ""
    assert report["gate_pass"] is False


# ---------------------------------------------------------------------------
# Case 4: count delta -> match=False, gate FAIL
# ---------------------------------------------------------------------------

def test_count_delta_fails_gate():
    bvi, ims = _matched_pair()
    bvi.products = 10  # IMS still has 2
    report = build_parity_report(bvi, ims)
    assert report["counts"]["products"]["match"] is False
    assert report["counts"]["products"]["delta"] == -8
    assert report["gate_pass"] is False


def test_extra_in_ims_is_informational_not_gate_failure():
    """An IMS SKU not present in BVI is reported but, on its own with matching
    counts, does not fail the gate via the missing-SKU arm."""
    bvi = BviSnapshot(products=1, variants=1, variant_skus=["A"])
    ims = ImsSnapshot(
        catalog_products=1,
        catalog_variants=1,
        variant_skus=["A", "B-EXTRA"],
    )
    report = build_parity_report(bvi, ims)
    assert report["sku_diff"]["missing_count"] == 0  # nothing missing FROM ims
    assert report["sku_diff"]["extra_in_ims_count"] == 1
    assert "B-EXTRA" in report["sku_diff"]["extra_in_ims"]


# ---------------------------------------------------------------------------
# Case 5: image storage classification + Phase-4 flag
# ---------------------------------------------------------------------------

def test_image_storage_counts_local_vs_durable():
    bvi, ims = _matched_pair()
    bvi.image_urls = [
        "https://cdn.shopify.com/s/files/1/durable1.png",  # durable
        "https://cdn.shopify.com/s/files/1/durable2.png",  # durable
        "/uploads/abc123-169.png",                          # local disk
        "/uploads/def456-170-nobg.png",                     # local disk
        "",                                                  # blank -> non-durable
    ]
    report = build_parity_report(bvi, ims)
    img = report["image_storage"]
    assert img["total"] == 5
    assert img["durable"] == 2
    assert img["local_disk"] == 3  # 2 /uploads/ + 1 blank
    assert img["phase4_rehost_needed"] is True
    assert img["local_disk_pct"] == 60.0


def test_image_storage_all_durable_needs_no_rehost():
    bvi, ims = _matched_pair()
    bvi.image_urls = [
        "https://cdn.shopify.com/a.png",
        "http://example.com/b.png",
    ]
    report = build_parity_report(bvi, ims)
    img = report["image_storage"]
    assert img["local_disk"] == 0
    assert img["phase4_rehost_needed"] is False


def test_image_storage_empty_is_safe():
    bvi, ims = _matched_pair()
    bvi.image_urls = []
    report = build_parity_report(bvi, ims)
    img = report["image_storage"]
    assert img["total"] == 0
    assert img["local_disk_pct"] == 0.0
    assert img["phase4_rehost_needed"] is False


# ---------------------------------------------------------------------------
# Case 6: the URL classifier directly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://cdn.shopify.com/x.png", True),
        ("http://example.com/x.png", True),
        ("HTTPS://CDN.SHOPIFY.COM/X.PNG", True),
        ("/uploads/abc.png", False),
        ("uploads/abc.png", False),
        ("public/uploads/abc.png", False),
        ("", False),
        (None, False),
        ("   ", False),
    ],
)
def test_is_durable_image_url(url, expected):
    assert is_durable_image_url(url) is expected


# ---------------------------------------------------------------------------
# Pre-migration baseline: BVI full, IMS empty -> everything missing, gate FAIL
# (this is the EXPECTED state BEFORE migration; the report makes it explicit)
# ---------------------------------------------------------------------------

def test_pre_migration_empty_ims_reports_all_missing():
    bvi = BviSnapshot(
        products=100,
        variants=300,
        variant_skus=[f"SKU-{i}" for i in range(300)],
        variant_barcode_by_sku={f"SKU-{i}": f"BC-{i}" for i in range(50)},
    )
    ims = ImsSnapshot()  # empty -- nothing migrated yet
    report = build_parity_report(bvi, ims)
    assert report["gate_pass"] is False
    assert report["sku_diff"]["missing_count"] == 300
    assert report["barcode_diff"]["mismatched_count"] == 50
    assert report["counts"]["products"]["delta"] == -100
