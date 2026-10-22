"""
GSTR-1 rows correct (GST batch 1, audit 2026-10-22).
=====================================================
Four filing defects, each test written to FAIL on main (3f247afe) and pass
with the fix -- the REVERT CHECK in each docstring names what breaks it:

1. Fail loudly. GSTR-1 swallowed read and shaping errors: an unreadable order
   dropped every later order, the credit-note ledger read raised on EVERY real
   pymongo Collection (`get_collection(..) or db[..]` -- a Collection has no
   truth value) so no credit note was ever filed, and a shaping error in the
   portal export handed back an EMPTY upload file. Now: a 503 "please retry",
   GSTR-3B's rule (gstr3b._gstin_return).
2. B2CL is INTER-state B2C above the threshold only; an intra-state B2C
   invoice of any value is B2CS (one constant, gst_base.B2CL_INVOICE_THRESHOLD).
3. A B2B / B2CL invoice mixing 5% and 18% lines is filed with one item row
   per rate (GSTN itms[]), from the tax stamped on its lines, and the HSN
   summary per line and rate -- not one row at the first line's rate.
4. An online order deleted in Shopify (VOID / VOIDED) is filed in neither
   GSTR-1 nor GSTR-3B -- one status rule (gst_base.GST_RETURN_ORDER_STATUS_FILTER).

Doubles: strict_fakes.StrictDB (its matcher honours `$nin` or raises; the
permissive fakes elsewhere ignore a status filter, so they cannot see #4).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import api.routers.reports as r  # noqa: E402
from api.routers.reports import gstr1 as gstr1_mod  # noqa: E402
from api.routers.reports import gstr3b as gstr3b_mod  # noqa: E402
from api.services import gstn_export  # noqa: E402
from strict_fakes import StrictCollection, StrictDB  # noqa: E402

WHEN = datetime(2026, 6, 15, 6, 0)  # mid-June 2026, inside the IST month window
MONTH = "2026-06"
GSTIN = "20AAACR0000A1ZZ"
ADMIN = {"user_id": "u-admin", "roles": ["SUPERADMIN"], "active_store_id": "S1"}


def _db(**seed) -> StrictDB:
    db = StrictDB()
    db.seed("stores", [{"store_id": "S1", "gstin": GSTIN, "store_name": "S-one",
                        "state": "Jharkhand"}])
    for name, docs in seed.items():
        db.seed(name, docs)
    return db


def _use(monkeypatch, db):
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)


def _line(rate, taxable, tax, hsn, item_total=None):
    """An order line as the till / Shopify ingest stamp it."""
    return {"gst_rate": rate, "taxable_value": taxable, "tax_amount": tax,
            "hsn_code": hsn, "item_total": taxable + tax if item_total is None else item_total}


def _order(oid, items, *, customer="C1", status="COMPLETED", **extra):
    taxable = round(sum(i["taxable_value"] for i in items), 2)
    tax = round(sum(i["tax_amount"] for i in items), 2)
    d = {"order_id": oid, "order_number": oid, "invoice_number": f"BV/26/{oid}",
         "store_id": "S1", "status": status, "created_at": WHEN,
         "grand_total": round(taxable + tax, 2), "tax_amount": tax,
         "customer_id": customer, "items": items}
    d.update(extra)
    return d


B2B_CUSTOMER = {"customer_id": "C1", "name": "Acme Opticals", "gstin": "20AAACA1111A1ZZ",
                "state": "Jharkhand"}
FRAME_5 = _line(5, 5000.0, 250.0, "900311")
SUNGLASS_18 = _line(18, 10000.0, 1800.0, "900410")


# ===========================================================================
# 3. Mixed-rate B2B invoice: one item row per rate, HSN per line and rate
# ===========================================================================


def test_mixed_rate_b2b_invoice_files_one_item_row_per_rate(monkeypatch):
    """A 5% frame (5000 + 250) and an 18% sunglass (10000 + 1800) on ONE B2B
    invoice: the portal invoice must carry two itm_det blocks, 5% and 18%,
    each its own taxable and tax.

    REVERT CHECK: on main the row has no rateLines, so the export files ONE
    block rt=5 txval=15000 camt=1025 -- the 18% sunglass declared at 5%.
    """
    _use(monkeypatch, _db(orders=[_order("O1", [FRAME_5, SUNGLASS_18])],
                          customers=[B2B_CUSTOMER]))
    rep = r._compute_gstr1(MONTH, "S1")
    (row,) = rep["b2b"]
    assert row["taxableValue"] == 15000.0 and row["totalTax"] == 2050.0
    assert row["rateLines"] == [
        {"rate": 5, "taxable": 5000.0, "cgst": 125.0, "sgst": 125.0, "igst": 0.0},
        {"rate": 18, "taxable": 10000.0, "cgst": 900.0, "sgst": 900.0, "igst": 0.0},
    ]
    assert (row["cgst"], row["sgst"], row["igst"]) == (1025.0, 1025.0, 0.0)

    out = gstn_export.to_gstr1_json(rep, gstin=GSTIN, period=MONTH)
    (inv,) = out["b2b"][0]["inv"]
    dets = [i["itm_det"] for i in inv["itms"]]
    assert [(d["rt"], d["txval"], d["camt"], d["samt"], d["iamt"]) for d in dets] == [
        (5.0, 5000.0, 125.0, 125.0, 0.0),
        (18.0, 10000.0, 900.0, 900.0, 0.0),
    ], dets


def test_mixed_rate_b2b_invoice_hsn_summary_is_per_line_and_rate(monkeypatch):
    """The HSN summary files the frame under its HSN at 5% and the sunglass
    under its HSN at 18%.

    REVERT CHECK: on main the whole invoice lands in ONE HSN row (900311 at
    5%, taxable 15000) and there is no 18% row at all.
    """
    _use(monkeypatch, _db(orders=[_order("O1", [FRAME_5, SUNGLASS_18])],
                          customers=[B2B_CUSTOMER]))
    hsn = {(h["hsnCode"], h["gstRate"]): h for h in r._compute_gstr1(MONTH, "S1")["hsnSummary"]}
    assert set(hsn) == {("900311", 5), ("900410", 18)}, hsn
    assert (hsn[("900311", 5)]["taxableValue"], hsn[("900311", 5)]["cgst"]) == (5000.0, 125.0)
    assert (hsn[("900410", 18)]["taxableValue"], hsn[("900410", 18)]["cgst"]) == (10000.0, 900.0)


def test_rate_rows_use_the_tax_stamped_on_the_lines(monkeypatch):
    """The per-rate split takes each line's STAMPED taxable / tax -- it never
    re-derives them from the price. Here the lines were priced tax-on-top
    (item_total is the taxable value), so re-extracting GST from item_total
    as if inclusive would misstate both rates.

    REVERT CHECK: deriving from item_total (the old B2CS helper's rule,
    scaled to the order total) gives 5% taxable 5396.20, not 5000.
    """
    frame = _line(5, 5000.0, 250.0, "900311", item_total=5000.0)
    sun = _line(18, 10000.0, 1800.0, "900410", item_total=10000.0)
    _use(monkeypatch, _db(orders=[_order("O1", [frame, sun])], customers=[B2B_CUSTOMER]))
    (row,) = r._compute_gstr1(MONTH, "S1")["b2b"]
    assert [(rl["rate"], rl["taxable"], rl["cgst"] + rl["sgst"]) for rl in row["rateLines"]] == [
        (5, 5000.0, 250.0), (18, 10000.0, 1800.0)]


# ===========================================================================
# 2. B2CL is inter-state only
# ===========================================================================

_BIG = _line(5, 285714.29, 14285.71, "900311")  # a Rs 3,00,000 invoice


def test_intra_state_b2c_over_threshold_is_b2cs_not_b2cl(monkeypatch):
    """A walk-in (no GSTIN) in the shop's own state buys for Rs 3 lakh: an
    INTRA-state supply -- B2CS, CGST + SGST.

    REVERT CHECK: on main it lands in B2CL and the export files it as IGST
    (iamt 14285.71) on an intra-state sale.
    """
    _use(monkeypatch, _db(orders=[_order("O1", [_BIG], customer="")]))
    rep = r._compute_gstr1(MONTH, "S1")
    assert rep["b2cl"] == [], rep["b2cl"]
    (b2cs,) = rep["b2cs"]
    assert (b2cs["gstRate"], b2cs["taxableValue"], b2cs["igst"]) == (5, 285714.29, 0.0)
    assert round(b2cs["cgst"] + b2cs["sgst"], 2) == 14285.71

    out = gstn_export.to_gstr1_json(rep, gstin=GSTIN, period=MONTH)
    assert out["b2cl"] == []
    (row,) = out["b2cs"]
    assert (row["sply_ty"], row["iamt"]) == ("INTRA", 0.0)


def test_inter_state_b2c_over_threshold_is_b2cl_igst(monkeypatch):
    """The same Rs 3 lakh invoice delivered to another state (the order's
    own interstate stamp): B2CL, all IGST, one item row per rate."""
    _use(monkeypatch, _db(orders=[_order("O1", [_BIG], customer="", interstate=True,
                                         place_of_supply="27")]))
    rep = r._compute_gstr1(MONTH, "S1")
    assert rep["b2cs"] == []
    (row,) = rep["b2cl"]
    assert (row["igst"], row["cgst"], row["sgst"]) == (14285.71, 0.0, 0.0)
    out = gstn_export.to_gstr1_json(rep, gstin=GSTIN, period=MONTH)
    (inv,) = out["b2cl"][0]["inv"]
    (itm,) = inv["itms"]
    assert (itm["itm_det"]["rt"], itm["itm_det"]["iamt"], itm["itm_det"]["camt"]) == (5.0, 14285.71, 0.0)


def test_mixed_rate_b2cl_invoice_files_one_igst_item_row_per_rate(monkeypatch):
    """A Rs 3.3 lakh inter-state invoice of frames (5%) and sunglasses (18%):
    B2CL, two IGST item rows, each its own rate.

    REVERT CHECK: main's B2CL export files ONE block at the first line's rate
    with the whole tax (rt=5, txval 291000, iamt 32800)."""
    frames = _line(5, 200000.0, 10000.0, "900311")
    suns = _line(18, 100000.0, 18000.0, "900410")
    _use(monkeypatch, _db(orders=[_order("O1", [frames, suns], customer="", interstate=True,
                                         place_of_supply="27")]))
    rep = r._compute_gstr1(MONTH, "S1")
    out = gstn_export.to_gstr1_json(rep, gstin=GSTIN, period=MONTH)
    (inv,) = out["b2cl"][0]["inv"]
    assert [(i["itm_det"]["rt"], i["itm_det"]["txval"], i["itm_det"]["iamt"], i["itm_det"]["camt"])
            for i in inv["itms"]] == [(5.0, 200000.0, 10000.0, 0.0), (18.0, 100000.0, 18000.0, 0.0)]


def test_b2cl_threshold_is_one_named_constant(monkeypatch):
    """The CA may rule the limit is Rs 1 lakh: changing the ONE constant moves
    a Rs 1.5 lakh inter-state invoice into B2CL (the package setattr reaches
    every sub-module that binds the name).

    REVERT CHECK: with the literal 250000 back in _compute_gstr1 the invoice
    stays in B2CS."""
    monkeypatch.setattr(r, "B2CL_INVOICE_THRESHOLD", 100000)
    mid = _line(5, 142857.14, 7142.86, "900311")
    _use(monkeypatch, _db(orders=[_order("O1", [mid], customer="", interstate=True)]))
    rep = r._compute_gstr1(MONTH, "S1")
    assert len(rep["b2cl"]) == 1 and rep["b2cs"] == []


# ===========================================================================
# 4. VOID (deleted in Shopify) is filed in neither return
# ===========================================================================


def _void_fixture():
    live = _order("O-LIVE", [_line(5, 1000.0, 50.0, "900311")], customer="")
    # Never invoiced, never shipped: not a sale (owner ruling 2026-10-22; an
    # invoiced or shipped VOID IS one -- test_gstr1_review_round1).
    void = _order("O-VOID", [_line(18, 10000.0, 1800.0, "900410")], customer="", status="VOID",
                  invoice_number=None, status_before_void="CONFIRMED")
    voided = _order("O-VOIDED", [_line(18, 2000.0, 360.0, "900410")], customer="",
                    status="VOIDED", invoice_number=None)
    return _db(orders=[live, void, voided])


def test_void_order_is_not_filed_in_gstr1(monkeypatch):
    """REVERT CHECK: with main's status filter the VOID orders add an 18%
    B2CS row (12000 taxable) to GSTR-1."""
    _use(monkeypatch, _void_fixture())
    rep = r._compute_gstr1(MONTH, "S1")
    assert [(b["gstRate"], b["taxableValue"]) for b in rep["b2cs"]] == [(5, 1000.0)]
    assert rep["totalTax"] == 50.0


def test_void_order_is_not_filed_in_gstr3b(monkeypatch):
    """REVERT CHECK: with main's status filter Table 3.1(a) declares 2210 of
    tax (the VOID orders' 2160 on top of the live 50)."""
    _use(monkeypatch, _void_fixture())
    rep = r._compute_gstr3b(MONTH, "S1")
    t = rep["outwardTaxableSupplies"]
    assert rep["outwardTaxableValue"] == 1000.0
    assert round(t["centralTax"] + t["stateTax"] + t["integratedTax"], 2) == 50.0


def test_gstr1_and_gstr3b_ask_one_order_rule():
    """One rule, not two synced copies: both returns ask the same function."""
    assert gstr1_mod.real_sale_match is gstr3b_mod.real_sale_match


# ===========================================================================
# 1. Fail loudly -- never a partial report or an empty upload file
# ===========================================================================


def _corrupt_fixture():
    good = _order("O-GOOD", [_line(5, 1000.0, 50.0, "900311")], customer="")
    bad = _order("O-BAD", [_line(5, 1000.0, 50.0, "900311")], customer="")
    bad["grand_total"] = "N/A"  # unreadable money on a stored order
    return _db(orders=[good, bad])


def test_corrupt_order_raises_instead_of_a_partial_gstr1(monkeypatch):
    """REVERT CHECK: on main the ValueError is swallowed and the report comes
    back with only O-GOOD -- a GSTR-1 short of an invoice, with no warning."""
    _use(monkeypatch, _corrupt_fixture())
    with pytest.raises(ValueError):
        r._compute_gstr1(MONTH, "S1")


def test_gstr1_doors_answer_503_please_retry(monkeypatch):
    """Both doors -- the report and the portal JSON -- turn a read failure
    into GSTR-3B's 503 "please retry" (an unreadable ORDER is a 422 naming it:
    test_gstr1_review_round1).

    REVERT CHECK: on main /gstr1 returns a report without the unread credit
    notes (200) and /gstr1/gstn-json a portal file without them."""
    db = _corrupt_fixture()
    db.get_collection("orders").docs = [d for d in db.get_collection("orders").docs if d["order_id"] == "O-GOOD"]

    def _down(*_a, **_k):
        raise RuntimeError("primary stepped down")

    db.get_collection("credit_note_ledger").find = _down
    _use(monkeypatch, db)
    for door in (r.gstr1_report, r.gstr1_gstn_json):
        kwargs = {"month": MONTH, "store_id": "S1", "current_user": ADMIN}
        if door is r.gstr1_gstn_json:
            kwargs.update(year=None, entity_id=None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(door(**kwargs))
        assert exc.value.status_code == 503, door
        assert "retry" in exc.value.detail


def test_export_shaping_failure_is_503_not_an_empty_file(monkeypatch):
    """REVERT CHECK: on main a shaping error returns to_gstr1_json({}) -- an
    importable file with NO invoices, which uploads as a nil return."""
    _use(monkeypatch, _db(orders=[_order("O1", [FRAME_5], customer="")]))
    real = gstn_export.to_gstr1_json

    def _breaks_on_data(data, **kw):
        if data:
            raise KeyError("shaping bug")
        return real(data, **kw)

    monkeypatch.setattr(gstn_export, "to_gstr1_json", _breaks_on_data)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.gstr1_gstn_json(month=MONTH, year=None, store_id="S1",
                                      entity_id=None, current_user=ADMIN))
    assert exc.value.status_code == 503


def test_no_database_is_503_not_a_nil_return(monkeypatch):
    """REVERT CHECK: on main _gstr1_shop lets a missing database through and
    the door returns an all-empty GSTR-1 -- a nil return."""
    monkeypatch.setattr(r, "_get_raw_db", lambda: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.gstr1_report(month=MONTH, store_id="S1", current_user=ADMIN))
    assert exc.value.status_code == 503


class _PymongoLikeCollection(StrictCollection):
    """A real pymongo Collection refuses truth-value testing."""

    def __bool__(self):
        raise NotImplementedError(
            "Collection objects do not implement truth value testing or bool()"
        )


def test_credit_notes_are_filed_from_a_real_pymongo_collection(monkeypatch):
    """The credit-note ledger read must not depend on a Collection's truth
    value (pymongo raises on it).

    REVERT CHECK: main's `get_collection(..) or db[..]` raises
    NotImplementedError here, the swallow eats it, and CDNR comes back EMPTY
    -- the ledger note AND the cash-refund return (whose pass then read the
    unbound `ledger_col` and was swallowed too) both missing from the return."""
    ret = {"return_id": "RET-260615-PYMG01", "store_id": "S1", "order_id": "O1",
           "status": "COMPLETED", "created_at": WHEN, "customer_id": "C1",
           "credit_note_number": "BV/C/26/0002",
           "gst_breakup": {"gross": 525.0, "taxable": 500.0, "tax": 25.0, "gst_rate": 5.0}}
    db = _db(orders=[_order("O1", [FRAME_5])], customers=[B2B_CUSTOMER], returns=[ret])
    ledger = _PymongoLikeCollection("credit_note_ledger", [{
        "store_id": "S1", "type": "ISSUED", "created_at": WHEN.isoformat(),
        "ref": "CN-1", "note_number": "BV/C/26/0001", "customer_id": "C1",
        "tax": 50.0, "taxable": 1000.0, "amount": 1000.0, "gst_rate": 5.0,
        "gross_refund": 1050.0, "net_refund": 1000.0}])
    db._collections["credit_note_ledger"] = ledger
    _use(monkeypatch, db)
    cdnr = r._compute_gstr1(MONTH, "S1")["cdnr"]
    assert [(c["taxableValue"], c["taxValue"]) for c in cdnr] == [(1000.0, 50.0), (500.0, 25.0)], cdnr
