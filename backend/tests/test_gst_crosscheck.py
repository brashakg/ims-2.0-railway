"""
IMS 2.0 - GST cross-check comparison logic
==========================================
Covers the PURE comparison + aggregation layer in
api/services/gst_crosscheck.py (no DB, no tax math):

  * aggregate_gstr1 -- merges N per-store GSTR-1 dicts: totals, gross
    CGST/SGST/IGST across all rows, per-rate breakup, CDNR totals, and the
    separated transfer deemed-supply rows.
  * aggregate_gstr3b -- merges N per-store GSTR-3B dicts.
  * build_crosscheck -- side-by-side comparison rows, MATCH within tolerance
    vs MISMATCH, INFO for single-source rows, and the summary rollup.

The tax figures fed in mirror reports.py::_compute_gstr1 / _compute_gstr3b
output shapes exactly (see test_gstn_export.py for the same sample shapes).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.gst_crosscheck import (  # noqa: E402
    aggregate_gstr1,
    aggregate_gstr3b,
    build_crosscheck,
    _cmp_row,
)


# ---------------------------------------------------------------------------
# Sample per-store GSTR-1 / GSTR-3B dicts (reports.py output shapes)
# ---------------------------------------------------------------------------


def _store_gstr1(deemed=False):
    rep = {
        "period": "2026-04",
        "totalTaxableValue": 20000.0,
        # totalTax == sum of section totalTax (1800 b2b + 500 b2cs), as
        # reports.py::_compute_gstr1 derives it.
        "totalTax": 2300.0,
        "b2b": [
            {
                "invoiceNumber": "INV-001",
                "taxableValue": 10000.0,
                "cgst": 900.0,
                "sgst": 900.0,
                "igst": 0.0,
                "totalTax": 1800.0,
                "hsnCode": "9004",
                "gstRate": 18,
            }
        ],
        "b2cl": [],
        "b2cs": [
            {
                "placeOfSupply": "Jharkhand",
                "gstRate": 5,
                "taxableValue": 10000.0,
                "cgst": 250.0,
                "sgst": 250.0,
                "igst": 0.0,
                "totalTax": 500.0,
            }
        ],
        "cdnr": [
            {
                "refReference": "RET-001",
                "taxableValue": 1000.0,
                "cgst": 90.0,
                "sgst": 90.0,
                "igst": 0.0,
                "taxValue": 180.0,
                "gstRate": 18,
                "hsnCode": "9004",
            }
        ],
        "hsnSummary": [
            {"hsnCode": "9004", "gstRate": 18, "taxableValue": 9000.0,
             "cgst": 810.0, "sgst": 810.0, "igst": 0.0},
            {"hsnCode": "9004", "gstRate": 5, "taxableValue": 10000.0,
             "cgst": 250.0, "sgst": 250.0, "igst": 0.0},
        ],
        "validation": {"ok": True, "issueCount": 0, "issues": []},
    }
    if deemed:
        rep["b2b"].append(
            {
                "invoiceNumber": "TRF-001",
                "taxableValue": 5000.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 900.0,
                "totalTax": 900.0,
                "hsnCode": "9004",
                "gstRate": 18,
                "deemedSupply": True,
            }
        )
        rep["totalTaxableValue"] += 5000.0
        rep["totalTax"] += 900.0
    return rep


def _store_gstr3b(rcm_c=0.0, rcm_s=0.0, rcm_taxable=0.0):
    """One store's GSTR-3B (reports.py::_compute_gstr3b shape).

    IMPORTANT: itcAvailable and inwardSuppliesReverseCharge are ENTITY-scoped in
    reports.py -- every sibling store of the same GSTIN returns the SAME values.
    Tests that aggregate sibling stores MUST pass entity_ids so they are counted
    once, not multiplied (the P1 defect the fixtures previously masked)."""
    return {
        "period": "2026-04",
        "outwardTaxableValue": 20000.0,
        "outwardTaxableSupplies": {
            "integratedTax": 0.0,
            "centralTax": 1150.0,
            "stateTax": 1150.0,
            "cess": 0.0,
        },
        "itcAvailable": {
            "integratedTax": 0.0,
            "centralTax": 400.0,
            "stateTax": 400.0,
            "cess": 0.0,
        },
        "taxPaidCash": {
            "integratedTax": 0.0,
            "centralTax": 750.0,
            "stateTax": 750.0,
            "cess": 0.0,
        },
        "inwardSuppliesReverseChargeValue": rcm_taxable,
        "inwardSuppliesReverseCharge": {
            "integratedTax": 0.0,
            "centralTax": rcm_c,
            "stateTax": rcm_s,
            "cess": 0.0,
        },
    }


# ---------------------------------------------------------------------------
# aggregate_gstr1
# ---------------------------------------------------------------------------


def test_aggregate_gstr1_sums_two_stores():
    agg = aggregate_gstr1([_store_gstr1(), _store_gstr1()])
    assert agg["totalTaxableValue"] == 40000.0
    assert agg["totalTax"] == 4600.0
    # Gross CGST/SGST across every b2b + b2cs row, both stores.
    assert agg["cgst"] == (900 + 250) * 2
    assert agg["sgst"] == (900 + 250) * 2
    assert agg["igst"] == 0.0
    assert len(agg["b2b"]) == 2
    assert len(agg["cdnr"]) == 2


def test_aggregate_gstr1_rate_breakup_merges_by_rate():
    agg = aggregate_gstr1([_store_gstr1(), _store_gstr1()])
    by_rate = {r["gstRate"]: r for r in agg["rate_breakup"]}
    assert set(by_rate) == {5, 18}
    # 18% HSN taxable: 9000 x 2 stores.
    assert by_rate[18]["taxableValue"] == 18000.0
    assert by_rate[18]["tax"] == (810 + 810) * 2
    # 5% HSN taxable: 10000 x 2 stores.
    assert by_rate[5]["taxableValue"] == 20000.0
    # rate_breakup sorted ascending by rate.
    assert [r["gstRate"] for r in agg["rate_breakup"]] == [5, 18]


def test_aggregate_gstr1_cdnr_totals():
    agg = aggregate_gstr1([_store_gstr1()])
    assert agg["cdnr_totals"]["count"] == 1
    assert agg["cdnr_totals"]["taxableValue"] == 1000.0
    assert agg["cdnr_totals"]["tax"] == 180.0


def test_aggregate_gstr1_separates_deemed_supply_rows():
    agg = aggregate_gstr1([_store_gstr1(deemed=True)])
    ds = agg["deemed_supply"]
    assert ds["count"] == 1
    assert ds["taxableValue"] == 5000.0
    assert ds["tax"] == 900.0
    assert ds["rows"][0]["invoiceNumber"] == "TRF-001"
    # Deemed-supply IGST still counts toward the gross IGST total.
    assert agg["igst"] == 900.0


def test_aggregate_gstr1_empty_is_safe():
    agg = aggregate_gstr1([])
    assert agg["totalTax"] == 0.0
    assert agg["rate_breakup"] == []
    assert agg["cdnr_totals"]["count"] == 0
    assert agg["deemed_supply"]["count"] == 0


def test_aggregate_gstr1_collects_validation_issues():
    s = _store_gstr1()
    s["validation"] = {"ok": False, "issueCount": 1,
                       "issues": [{"level": "warn", "issue": "missing GSTIN"}]}
    agg = aggregate_gstr1([s])
    assert agg["validation"]["ok"] is False
    assert agg["validation"]["issueCount"] == 1


# ---------------------------------------------------------------------------
# aggregate_gstr3b
# ---------------------------------------------------------------------------


def test_aggregate_gstr3b_untagged_sums_per_report():
    # Legacy fallback (entity_ids=None): each report treated as its own entity.
    agg = aggregate_gstr3b([_store_gstr3b(), _store_gstr3b()])
    assert agg["outwardTaxableValue"] == 40000.0
    assert agg["cgst"] == 2300.0
    assert agg["outwardTax"] == 4600.0
    assert agg["itc"]["total"] == 1600.0
    # Net cash per report: max(0, 1150-400) x2 heads x2 reports = 3000.
    assert agg["netCash"]["total"] == 3000.0


def test_aggregate_gstr3b_dedups_entity_itc_rcm():
    # P1-1: two stores of the SAME entity. Outward is store-scoped -> summed;
    # ITC/RCM are entity-scoped -> counted ONCE (not doubled).
    agg = aggregate_gstr3b([_store_gstr3b(), _store_gstr3b()], ["E1", "E1"])
    assert agg["outwardTaxableValue"] == 40000.0  # summed
    assert agg["cgst"] == 2300.0                  # summed
    assert agg["outwardTax"] == 4600.0
    assert agg["itc"]["total"] == 800.0           # counted once, not 1600
    # Net cash entity-level: max(0, 2300-400) per head x2 = 3800 (not the old
    # 3000, which double-subtracted the entity ITC on each store).
    assert agg["netCash"]["total"] == 3800.0


def test_aggregate_gstr3b_multi_entity_itc_summed_across_entities():
    # Two DISTINCT entities -> each entity's ITC counted once, then summed;
    # net cash clamped PER entity (no cross-entity ITC offset).
    agg = aggregate_gstr3b([_store_gstr3b(), _store_gstr3b()], ["E1", "E2"])
    assert agg["itc"]["total"] == 1600.0
    assert agg["netCash"]["total"] == 3000.0


def test_aggregate_gstr3b_storeless_contributes_no_itc():
    # A store with a falsy entity_id: its outward is real and counted, but its
    # per-store ITC is org-wide garbage and must be dropped to zero.
    agg = aggregate_gstr3b([_store_gstr3b()], [None])
    assert agg["outwardTax"] == 2300.0
    assert agg["itc"]["total"] == 0.0
    assert agg["netCash"]["total"] == 2300.0  # max(0, 1150-0) x2 heads


def test_aggregate_gstr3b_rcm_counted_once_and_added_to_cash():
    # RCM is entity-scoped like ITC: two siblings must not double it, and it is
    # always added to net cash (it cannot be set off against ITC).
    agg = aggregate_gstr3b(
        [_store_gstr3b(rcm_c=100.0, rcm_s=100.0, rcm_taxable=2000.0),
         _store_gstr3b(rcm_c=100.0, rcm_s=100.0, rcm_taxable=2000.0)],
        ["E1", "E1"],
    )
    assert agg["rcm"]["total"] == 200.0           # once, not 400
    assert agg["rcm"]["taxableValue"] == 2000.0
    # Net cash: max(0, 2300-400) per head x2 + RCM 200 = 3800 + 200 = 4000.
    assert agg["netCash"]["total"] == 4000.0


def test_aggregate_gstr3b_empty_is_safe():
    agg = aggregate_gstr3b([])
    assert agg["outwardTax"] == 0.0
    assert agg["itc"]["total"] == 0.0


# ---------------------------------------------------------------------------
# _cmp_row
# ---------------------------------------------------------------------------


def test_cmp_row_match_within_tolerance():
    row = _cmp_row("X", {"A": 100.0, "B": 100.5}, tolerance=1.0)
    assert row["status"] == "MATCH"
    assert row["variance"] == 0.5


def test_cmp_row_mismatch_beyond_tolerance():
    row = _cmp_row("X", {"A": 100.0, "B": 105.0}, tolerance=1.0)
    assert row["status"] == "MISMATCH"
    assert row["variance"] == 5.0


def test_cmp_row_single_source_is_info():
    row = _cmp_row("X", {"A": 100.0, "B": None}, tolerance=1.0)
    assert row["status"] == "INFO"
    assert row["sources"] == {"A": 100.0}
    assert row["variance"] == 0.0


# ---------------------------------------------------------------------------
# build_crosscheck
# ---------------------------------------------------------------------------


def _matched_inputs():
    """Two aggregated stores where GSTR-1 / GSTR-3B / books / tally all agree."""
    gstr1 = aggregate_gstr1([_store_gstr1(), _store_gstr1()])
    gstr3b = aggregate_gstr3b([_store_gstr3b(), _store_gstr3b()])
    # Books mirror the GST totals (taxable 40000, tax 4600, grand 44600).
    books = {
        "sales_taxable": 40000.0,
        "sales_tax": 4600.0,
        "sales_grand_total": 44600.0,
        "payments_collected": 44600.0,
        "input_credit": 1600.0,
    }
    tally = {
        "taxable": 40000.0,
        "tax": 4600.0,
        "cgst": 2300.0,
        "sgst": 2300.0,
        "igst": 0.0,
    }
    return gstr1, gstr3b, books, tally


def test_build_crosscheck_all_matched():
    res = build_crosscheck(*_matched_inputs())
    assert res["summary"]["all_matched"] is True
    assert res["summary"]["mismatch_count"] == 0
    # Every multi-source comparison row is MATCH.
    for row in res["comparisons"]:
        assert row["status"] in ("MATCH", "INFO")


def test_build_crosscheck_flags_output_tax_mismatch():
    gstr1, gstr3b, books, tally = _matched_inputs()
    # Books under-report GST by 500 -> a mismatch on taxable + output-GST rows.
    books["sales_tax"] = 4100.0
    res = build_crosscheck(gstr1, gstr3b, books, tally)
    assert res["summary"]["all_matched"] is False
    metrics = res["summary"]["mismatch_metrics"]
    assert "Total output GST" in metrics


def test_build_crosscheck_itc_row_uses_both_sources():
    gstr1, gstr3b, books, tally = _matched_inputs()
    books["input_credit"] = 1000.0  # POs booked less than vendor bills -> gap 600
    res = build_crosscheck(gstr1, gstr3b, books, tally)
    itc_row = next(c for c in res["comparisons"] if c["metric"] == "Input tax credit (ITC)")
    assert itc_row["status"] == "MISMATCH"
    assert itc_row["variance"] == 600.0


def test_build_crosscheck_itc_row_info_when_books_missing():
    gstr1, gstr3b, books, tally = _matched_inputs()
    books["input_credit"] = None  # purchase side unavailable
    res = build_crosscheck(gstr1, gstr3b, books, tally)
    itc_row = next(c for c in res["comparisons"] if c["metric"] == "Input tax credit (ITC)")
    assert itc_row["status"] == "INFO"


def test_build_crosscheck_carries_rate_and_cdnr_detail():
    res = build_crosscheck(*_matched_inputs())
    assert len(res["rate_breakup"]) == 2
    assert res["cdnr"]["count"] == 2
    assert "rows" in res["cdnr"]
    assert res["summary"]["gst_payable"] == 3000.0


def test_build_crosscheck_collections_gap_is_advisory():
    # P3-1: a collections gap is normal on the credit/advance-payment model, so
    # the row is INFO (variance still shown) and is EXCLUDED from the mismatch
    # rollup -- otherwise alarm fatigue buries real GST breaks.
    gstr1, gstr3b, books, tally = _matched_inputs()
    books["payments_collected"] = 40000.0  # 4600 uncollected (credit sales)
    res = build_crosscheck(gstr1, gstr3b, books, tally)
    row = next(
        c for c in res["comparisons"] if c["metric"] == "Sales: invoiced vs collected"
    )
    assert row["status"] == "INFO"
    assert row["variance"] == 4600.0
    assert "Sales: invoiced vs collected" not in res["summary"]["mismatch_metrics"]
    assert res["summary"]["all_matched"] is True


def _aggregated_gstr3b(out_c, out_s, out_i, itc_total=0.0, rcm_total=0.0):
    """Hand-built aggregate_gstr3b-shaped dict for build_crosscheck tests."""
    return {
        "outwardTaxableValue": round((out_c + out_s + out_i) * 100.0, 2),
        "cgst": out_c,
        "sgst": out_s,
        "igst": out_i,
        "outwardTax": round(out_c + out_s + out_i, 2),
        "itc": {"cgst": itc_total, "sgst": 0.0, "igst": 0.0, "total": itc_total},
        "netCash": {
            "cgst": max(0.0, out_c - itc_total),
            "sgst": out_s,
            "igst": out_i,
            "total": round(max(0.0, out_c - itc_total) + out_s + out_i + rcm_total, 2),
        },
        "rcm": {"taxableValue": 0.0, "cgst": rcm_total, "sgst": 0.0, "igst": 0.0,
                "total": rcm_total},
    }


def test_build_crosscheck_deemed_supply_aligns_outward_rows():
    # P2-2: GSTR-1/3B include inter-GSTIN transfer deemed supply; the orders-only
    # Books/Tally do not. build_crosscheck must ADD deemed to Books/Tally so the
    # outward rows do not flag a phantom MISMATCH on a routine transfer month.
    gstr1 = aggregate_gstr1([_store_gstr1(deemed=True)])
    # gstr1: taxable 25000, tax 3200, cgst 1150, sgst 1150, igst 900 (deemed).
    gstr3b = {
        "outwardTaxableValue": 25000.0,
        "cgst": 1150.0, "sgst": 1150.0, "igst": 900.0,
        "outwardTax": 3200.0,
        "itc": {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "total": 0.0},
        "netCash": {"cgst": 1150.0, "sgst": 1150.0, "igst": 900.0, "total": 3200.0},
        "rcm": {"taxableValue": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0, "total": 0.0},
    }
    # Books/Tally exclude the 5000 / 900 transfer.
    books = {
        "sales_taxable": 20000.0, "sales_tax": 2300.0,
        "sales_grand_total": 22300.0, "payments_collected": 22300.0,
        "input_credit": 0.0,
    }
    tally = {"taxable": 20000.0, "tax": 2300.0, "cgst": 1150.0, "sgst": 1150.0, "igst": 0.0}
    res = build_crosscheck(gstr1, gstr3b, books, tally)
    by_metric = {c["metric"]: c for c in res["comparisons"]}
    for metric in (
        "Taxable value (outward)", "Total output GST",
        "CGST (output)", "SGST (output)", "IGST (output)",
    ):
        assert by_metric[metric]["status"] == "MATCH", metric
    # The IGST row is the one that would go red without alignment (Tally 0 vs 900).
    assert by_metric["IGST (output)"]["sources"]["Tally Sales JV"] == 900.0
    assert res["deemed_supply"]["taxableValue"] == 5000.0
    assert res["summary"]["all_matched"] is True


def test_build_crosscheck_net_cash_row_like_for_like():
    # P2-1: the 'Net GST payable (cash)' comparator must recompute per head with
    # clamp + RCM so it matches gstr3b.netCash instead of raw output-minus-ITC.
    gstr1 = aggregate_gstr1([_store_gstr1()])
    gstr3b = _aggregated_gstr3b(1150.0, 1150.0, 0.0, itc_total=400.0, rcm_total=100.0)
    books = {"sales_taxable": 20000.0, "sales_tax": 2300.0,
             "sales_grand_total": 22300.0, "payments_collected": 22300.0,
             "input_credit": 400.0}
    tally = {"taxable": 20000.0, "tax": 2300.0, "cgst": 1150.0, "sgst": 1150.0, "igst": 0.0}
    res = build_crosscheck(gstr1, gstr3b, books, tally)
    row = next(c for c in res["comparisons"] if c["metric"] == "Net GST payable (cash)")
    # netCash = max(0,1150-400)+1150+100 = 2000 ; comparator recomputes the same.
    assert row["sources"]["Output - ITC + RCM"] == 2000.0
    assert row["status"] == "MATCH"


def test_build_crosscheck_empty_inputs_safe():
    res = build_crosscheck({}, {}, {}, {})
    assert res["summary"]["all_matched"] is True
    assert res["rate_breakup"] == []
