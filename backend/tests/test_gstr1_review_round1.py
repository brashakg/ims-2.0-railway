"""
GST batch 1, money-review round 1 (PR #1253 at 14b7ff62 -> BLOCK).
==================================================================
Each test FAILS on 14b7ff62 and passes here (REVERT CHECK in each docstring):

1. Credit notes reverse the HSN rows their SALE filed (the parent order's own
   line HSN and rate), not a hard-coded 9004 that no sale row carries; a note
   matching no sale line, or exceeding the month's row, is a reported error --
   never a silent skip or clamp.
2. The credit-note CGST/SGST split is THE invoice split (gst_rates.split_gst):
   the heads add up to the note's tax to the paisa.
3. ONE place-of-supply rule (org_validation.supply_interstate) for the
   invoice, GSTR-1, GSTR-3B: state CODES ('JH' at a Jharkhand shop is intra),
   the customer's billing-address state counts.
4. Owner ruling 2026-10-22: a VOID order that was invoiced or shipped is still
   a sale (returns AND books); a never-invoiced VOID is in neither.
5. Credit-note helper reads raise (503) instead of guessing a head / GSTIN.
6. A Decimal128 / '1,050.00' grand_total reads; an unreadable one is a 422
   naming the order, not "please retry".
7. No-usable-lines fallback uses the order's own rate, never a forced 5%.
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
from api.services.gst_rates import split_gst  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

WHEN = datetime(2026, 6, 15, 6, 0)
MONTH = "2026-06"
GSTIN = "20AAACR0000A1ZZ"
ADMIN = {"user_id": "u-admin", "roles": ["SUPERADMIN"], "active_store_id": "S1"}
SHOP = {"store_id": "S1", "gstin": GSTIN, "store_name": "S-one", "state": "Jharkhand"}


def _db(**seed) -> StrictDB:
    db = StrictDB()
    db.seed("stores", [dict(SHOP)])
    for name, docs in seed.items():
        db.seed(name, docs)
    return db


def _use(monkeypatch, db):
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    return db


def _line(rate, taxable, tax, hsn):
    return {"gst_rate": rate, "taxable_value": taxable, "tax_amount": tax,
            "hsn_code": hsn, "item_total": round(taxable + tax, 2)}


def _order(oid, items, *, customer="C1", status="COMPLETED", **extra):
    taxable = round(sum(i["taxable_value"] for i in items), 2)
    tax = round(sum(i["tax_amount"] for i in items), 2)
    d = {"order_id": oid, "order_number": oid, "invoice_number": f"BV/26/{oid}",
         "store_id": "S1", "status": status, "created_at": WHEN,
         "grand_total": round(taxable + tax, 2), "tax_amount": tax,
         "customer_id": customer, "items": items}
    d.update(extra)
    return d


B2B = {"customer_id": "C1", "name": "Acme Opticals", "gstin": "20AAACA1111A1ZZ",
       "state": "Jharkhand"}


def _return(rid, oid, taxable, tax, rate=5.0, customer="C1"):
    return {"return_id": rid, "store_id": "S1", "order_id": oid, "status": "COMPLETED",
            "created_at": WHEN, "customer_id": customer, "credit_note_number": f"BV/C/{rid[-4:]}",
            "gst_breakup": {"gross": taxable + tax, "taxable": taxable, "tax": tax,
                            "gst_rate": rate,
                            "by_rate": {str(rate): {"gross": taxable + tax, "taxable": taxable, "tax": tax}}}}


def _ledger(rid, taxable, tax, rate=5.0, customer="C1", **extra):
    d = {"store_id": "S1", "type": "ISSUED", "created_at": WHEN.isoformat(), "ref": rid,
         "reason": f"Credit note for return {rid}", "customer_id": customer,
         "tax": tax, "taxable": taxable, "amount": taxable, "gst_rate": rate,
         "gross_refund": taxable + tax, "net_refund": taxable}
    d.update(extra)
    return d


def _hsn(rep):
    return {(h["hsnCode"], h["gstRate"]): h for h in rep["hsnSummary"]}


def _errors(rep):
    return [i for i in rep["validation"]["issues"] if i["level"] == "error"]


# ===========================================================================
# 1. Credit notes reverse their SALE's HSN rows
# ===========================================================================


def test_credit_notes_reduce_the_hsn_rows_their_sales_filed(monkeypatch):
    """A B2B frame bill (HSN 900311, 5%) with a store-credit note against it,
    and a B2C frame bill with a cash return: both notes come off the 900311 @
    5% row -- 5000 + 2000 - 1000 - 1000 = 5000 taxable.

    REVERT CHECK: at 14b7ff62 both notes reduce key '9004|5.0', which no sale
    row carries, so they are skipped silently and the row files 7000."""
    _use(monkeypatch, _db(
        customers=[B2B],
        orders=[_order("O1", [_line(5, 5000.0, 250.0, "900311")]),
                _order("O2", [_line(5, 2000.0, 100.0, "900311")], customer="")],
        returns=[_return("RET-260615-0001", "O1", 1000.0, 50.0),
                 _return("RET-260615-0002", "O2", 1000.0, 50.0, customer="")],
        credit_note_ledger=[_ledger("RET-260615-0001", 1000.0, 50.0)],
    ))
    rep = r._compute_gstr1(MONTH, "S1")
    assert len(rep["cdnr"]) == 2, rep["cdnr"]
    hsn = _hsn(rep)
    assert set(hsn) == {("900311", 5)}, hsn
    row = hsn[("900311", 5)]
    assert (row["taxableValue"], round(row["cgst"] + row["sgst"], 2)) == (5000.0, 250.0), row
    assert _errors(rep) == [], _errors(rep)


def test_a_credit_note_matching_no_sale_line_is_reported_not_guessed(monkeypatch):
    """A manual credit note naming no order cannot be placed on an HSN: it is
    an error on the report, and no HSN row is touched.

    REVERT CHECK: at 14b7ff62 it silently reduced nothing (key '9004|5.0'
    absent) and said nothing."""
    _use(monkeypatch, _db(
        customers=[B2B],
        orders=[_order("O1", [_line(5, 5000.0, 250.0, "900311")])],
        credit_note_ledger=[_ledger("CN-MANUAL-7", 1000.0, 50.0, reason="goodwill")],
    ))
    rep = r._compute_gstr1(MONTH, "S1")
    assert _hsn(rep)[("900311", 5)]["taxableValue"] == 5000.0
    errs = _errors(rep)
    assert len(errs) == 1 and "matches no line" in errs[0]["issue"], errs


def test_credit_notes_exceeding_the_month_are_reported_not_clamped_silently(monkeypatch):
    """A note for a sale filed in MAY, issued in June with no June sale on that
    HSN: the row is filed at 0 (no negative rows) and the excess is an error.

    REVERT CHECK: at 14b7ff62 the note's key is absent, so nothing is
    reported at all."""
    may = _order("O-MAY", [_line(5, 3000.0, 150.0, "900311")], created_at=datetime(2026, 5, 10, 6, 0))
    _use(monkeypatch, _db(customers=[B2B], orders=[may],
                          returns=[_return("RET-260615-0009", "O-MAY", 1000.0, 50.0)]))
    rep = r._compute_gstr1(MONTH, "S1")
    assert rep["hsnSummary"] == []
    errs = _errors(rep)
    assert any("exceed this month's sales" in e["issue"] for e in errs), errs


# ===========================================================================
# 2. Credit-note split = the invoice split, to the paisa
# ===========================================================================


def test_credit_note_split_is_the_invoice_split_to_the_paisa(monkeypatch):
    """REVERT CHECK: at 14b7ff62 the ledger pass halves 142.81 into 71.41 +
    71.41 = 142.82 -- a paisa of tax reversed that was never charged."""
    _use(monkeypatch, _db(
        customers=[B2B],
        orders=[_order("O1", [_line(5, 5000.0, 250.0, "900311")])],
        returns=[_return("RET-260615-0003", "O1", 2856.2, 142.81)],
        credit_note_ledger=[_ledger("RET-260615-0003", 2856.2, 142.81)],
    ))
    (cn,) = r._compute_gstr1(MONTH, "S1")["cdnr"]
    c, s, i = split_gst(142.81, False)
    assert (cn["cgst"], cn["sgst"], cn["igst"]) == (c, s, i)
    assert round(cn["cgst"] + cn["sgst"], 2) == 142.81


def test_split_heads_is_gone_split_gst_is_the_rule():
    import api.routers.reports.gst_base as gb
    assert not hasattr(gb, "_split_heads")


# ===========================================================================
# 3. One place-of-supply rule
# ===========================================================================


def test_customer_state_jh_at_a_jharkhand_shop_is_intra_in_both_returns(monkeypatch):
    """REVERT CHECK: at 14b7ff62 GSTR-1 compares 'jh' with 'jharkhand' as raw
    strings -> inter-state IGST, while the invoice printed CGST + SGST."""
    cust = {"customer_id": "CJ", "name": "Walk-in", "state": "JH"}
    db = _use(monkeypatch, _db(customers=[cust],
                               orders=[_order("O1", [_line(5, 1000.0, 50.0, "900311")], customer="CJ")]))
    (row,) = r._compute_gstr1(MONTH, "S1")["b2cs"]
    assert (row["igst"], round(row["cgst"] + row["sgst"], 2)) == (0.0, 50.0), row
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    t = r._compute_gstr3b(MONTH, "S1")["outwardTaxableSupplies"]
    assert (t["integratedTax"], round(t["centralTax"] + t["stateTax"], 2)) == (0.0, 50.0)


def test_billing_address_state_above_threshold_files_what_the_invoice_printed(monkeypatch):
    """Blank customers.state, Maharashtra billing address, Rs 3 lakh: the
    invoice prints IGST (place of supply 27), so GSTR-1 files B2CL IGST.

    REVERT CHECK: at 14b7ff62 the blank state reads as the shop's own ->
    intra-state B2CS, against an IGST invoice."""
    from api.routers.orders.invoices import _build_invoice_gst_split

    cust = {"customer_id": "CM", "name": "Buyer", "state": "",
            "billing_address": {"state": "Maharashtra"}}
    items = [_line(5, 285714.29, 14285.71, "900311")]
    _use(monkeypatch, _db(customers=[cust], orders=[_order("O1", items, customer="CM")]))
    printed = _build_invoice_gst_split(items, SHOP, cust)
    assert printed["interstate"] is True and printed["place_of_supply"] == "27"
    rep = r._compute_gstr1(MONTH, "S1")
    assert rep["b2cs"] == []
    (b2cl,) = rep["b2cl"]
    assert (b2cl["igst"], b2cl["cgst"]) == (printed["totals"]["igst"], printed["totals"]["cgst"])
    assert r.gstr1.resolve_state_code(b2cl["placeOfSupply"]) == "27"


def test_invoice_and_returns_ask_one_rule(monkeypatch):
    """One function: making supply_interstate say inter-state flips the
    invoice and the returns together."""
    import api.services.org_validation as ov
    from api.routers.orders.invoices import _build_invoice_gst_split

    monkeypatch.setattr(ov, "supply_interstate", lambda *a, **k: (True, "27", False))
    assert _build_invoice_gst_split([_line(5, 100.0, 5.0, "900311")], SHOP, {})["interstate"] is True
    assert r._order_supply({}, SHOP, {})[0] is True


# ===========================================================================
# 4. VOID: owner ruling 2026-10-22 -- returns and books agree
# ===========================================================================


def _void_db():
    live = _order("O-LIVE", [_line(5, 1000.0, 50.0, "900311")], customer="")
    shipped = _order("O-SHIP", [_line(18, 10000.0, 1800.0, "900410")], customer="",
                     status="VOID", status_before_void="SHIPPED")
    invoiced = _order("O-INV", [_line(5, 2000.0, 100.0, "900311")], customer="",
                      status="VOIDED", status_before_void="CONFIRMED")
    never = _order("O-NEVER", [_line(18, 5000.0, 900.0, "900410")], customer="",
                   status="VOID", status_before_void="CONFIRMED", invoice_number=None)
    return _db(orders=[live, shipped, invoiced, never])


def test_invoiced_or_shipped_void_is_a_sale_never_invoiced_void_is_not(monkeypatch):
    """REVERT CHECK: 14b7ff62 drops every VOID from the returns (1000 taxable)
    while the books keep every VOID (18000) -- both wrong under the ruling."""
    db = _use(monkeypatch, _void_db())
    g1 = r._compute_gstr1(MONTH, "S1")
    assert g1["totalTaxableValue"] == 13000.0 and g1["totalTax"] == 1950.0
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    g3 = r._compute_gstr3b(MONTH, "S1")
    assert g3["outwardTaxableValue"] == 13000.0

    from api.routers.finance.gst_crosscheck import _books_and_tally_for_stores
    from api.utils.ist import ist_month_window_utc

    books, tally = _books_and_tally_for_stores(db, ["S1"], *ist_month_window_utc(2026, 6))
    assert books["sales_taxable"] == 13000.0 and books["sales_tax"] == 1950.0, books
    out = g3["outwardTaxableSupplies"]
    assert round(out["integratedTax"] + out["centralTax"] + out["stateTax"], 2) == books["sales_tax"]


def test_books_and_returns_ask_one_order_rule():
    from api.routers.finance import _shared as fin
    from api.routers.reports import gstr1, gstr3b
    from api.utils.real_sale import REAL_SALE_MATCH

    assert fin.REAL_SALE_MATCH is REAL_SALE_MATCH is gstr1.REAL_SALE_MATCH is gstr3b.REAL_SALE_MATCH


# ===========================================================================
# 5. Credit-note helper reads raise into the 503 path
# ===========================================================================


def test_a_failed_parent_read_is_503_not_a_guessed_head(monkeypatch):
    """REVERT CHECK: at 14b7ff62 _return_interstate_flag swallows the parent
    order read and guesses a head from the customer's state."""
    db = _db(customers=[B2B], orders=[_order("O1", [_line(5, 5000.0, 250.0, "900311")])],
             returns=[_return("RET-260615-0004", "O1", 1000.0, 50.0)])
    real_find_one = db.get_collection("orders").find_one
    calls = {"n": 0}

    def flaky(flt=None, *a, **k):
        if flt and "order_id" in flt:
            calls["n"] += 1
            raise RuntimeError("primary stepped down")
        return real_find_one(flt, *a, **k)

    db.get_collection("orders").find_one = flaky
    _use(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.gstr1_report(month=MONTH, store_id="S1", current_user=ADMIN))
    assert exc.value.status_code == 503 and calls["n"] >= 1


# ===========================================================================
# 6. Money read tolerantly; unusable data names the order
# ===========================================================================


def test_decimal128_and_formatted_strings_read(monkeypatch):
    """REVERT CHECK: at 14b7ff62 float(Decimal128) / float('1,050.00') raise
    and the whole return is a 503."""
    from bson.decimal128 import Decimal128

    o1 = _order("O1", [_line(5, 1000.0, 50.0, "900311")], customer="")
    o1["grand_total"] = Decimal128("1050.00")
    o2 = _order("O2", [_line(5, 1000.0, 50.0, "900311")], customer="")
    o2["grand_total"] = "1,050.00"
    _use(monkeypatch, _db(orders=[o1, o2]))
    assert r._compute_gstr1(MONTH, "S1")["totalTaxableValue"] == 2000.0


def test_unreadable_money_is_422_naming_the_order(monkeypatch):
    """REVERT CHECK: at 14b7ff62 it is a 503 'please retry' -- a retry can
    never fix it, and nobody is told which order."""
    bad = _order("O-BAD-42", [_line(5, 1000.0, 50.0, "900311")], customer="")
    bad["grand_total"] = "N/A"
    _use(monkeypatch, _db(orders=[bad]))
    for door in (r.gstr1_report, r.gstr3b_report):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(door(month=MONTH, store_id="S1", current_user=ADMIN))
        assert exc.value.status_code == 422 and "O-BAD-42" in exc.value.detail, exc.value.detail


# ===========================================================================
# 7. No-usable-lines fallback: the order's own rate, never a forced 5%
# ===========================================================================


def test_lineless_order_files_at_its_own_rate(monkeypatch):
    """REVERT CHECK: at 14b7ff62 an 18% order with no usable lines is filed
    in a 5% B2CS row."""
    o = {"order_id": "O-NL", "invoice_number": "BV/26/NL", "store_id": "S1",
         "status": "COMPLETED", "created_at": WHEN, "grand_total": 1180.0,
         "tax_amount": 180.0, "tax_rate": 18, "customer_id": "", "items": []}
    _use(monkeypatch, _db(orders=[o]))
    (row,) = r._compute_gstr1(MONTH, "S1")["b2cs"]
    assert (row["gstRate"], row["taxableValue"]) == (18, 1000.0)


def test_zero_rated_line_is_flagged_for_table_8(monkeypatch):
    """A 0% (exempt eye test) line is flagged: GSTR-1 Table 8 is not built yet.
    REVERT CHECK: at 14b7ff62 it files in a 0% B2CS row without a word."""
    o = _order("O-EYE", [_line(0, 300.0, 0.0, "999316"), _line(5, 1000.0, 50.0, "900311")], customer="")
    _use(monkeypatch, _db(orders=[o]))
    rep = r._compute_gstr1(MONTH, "S1")
    assert any("Table 8" in i["issue"] for i in rep["validation"]["issues"]), rep["validation"]
