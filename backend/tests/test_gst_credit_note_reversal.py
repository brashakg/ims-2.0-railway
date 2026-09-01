# ============================================================================
# A refund reverses output tax, whatever tender the money went back in
# ============================================================================
# GSTR-3B Table 3.1(a) is reported NET of credit notes: goods that came back
# are not an outward supply. Nothing subtracted them, so the payable figure the
# accountant reads off the screen - and re-types into the portal - was gross of
# every refund. He paid GST on money he had handed back.
#
# The root cause was a conflation. `credit_note_ledger` is the STORE-CREDIT
# ledger, and a cash / UPI / card refund correctly writes nothing to it: the
# customer got money, not a promise of money. GST was reading that ledger as
# its only source of credit notes, so a cash refund's tax reversal existed
# nowhere. Measured on production: ALL 20 completed returns had no ledger row
# and Rs 3,209.96 of output tax was still being declared.
#
# The fix reads the tax event from `returns.gst_breakup`, which the return door
# already stamps, and does NOT mint store credit for a cash refund - that would
# hand the customer spendable money they never earned, on top of their cash.

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime  # noqa: E402

import api.routers.reports as r  # noqa: E402

STORE = {"store_id": "S1", "gstin": "20AAACR0000A1ZZ", "store_name": "S",
         "state": "Jharkhand"}
# Mid-June 2026, safely inside the IST month window the report builds.
WHEN = datetime(2026, 6, 15, 6, 0)


class _Coll:
    def __init__(self, docs=None):
        self._docs = list(docs or [])

    def find(self, flt=None, projection=None):
        return iter(list(self._docs))

    def find_one(self, flt=None, projection=None):
        return self._docs[0] if self._docs else None

    def aggregate(self, pipeline):
        return iter([])


class _DB:
    def __init__(self, **colls):
        self._m = {"stores": _Coll([STORE]), "orders": _Coll(), "customers": _Coll(),
                   "vendor_bills": _Coll(), "returns": _Coll(),
                   "credit_note_ledger": _Coll()}
        self._m.update({k: _Coll(v) for k, v in colls.items()})

    def get_collection(self, name):
        return self._m.get(name, _Coll())

    def __getitem__(self, name):
        return self._m.get(name, _Coll())


def _order(tax=1800.0, gross=11800.0):
    return {"order_id": "O1", "store_id": "S1", "status": "COMPLETED",
            "created_at": WHEN, "grand_total": gross, "tax_amount": tax,
            "customer_id": "C1", "items": []}


def _cash_return(tax=1800.0, taxable=10000.0, rid="RET-260615-AAA111"):
    """A CASH refund. credit_entry is None - correctly, no store credit was
    minted - which is exactly why GST could not see it before."""
    return {"return_id": rid, "store_id": "S1", "status": "COMPLETED",
            "order_id": "O1",
            "created_at": WHEN, "return_type": "RETURN", "refund_method": "CASH",
            "credit_entry": None, "customer_id": "C1",
            "gst_breakup": {"gross": taxable + tax, "taxable": taxable,
                            "tax": tax, "gst_rate": 18.0}}


def _outward(rep):
    t = rep["outwardTaxableSupplies"]
    return round(t["centralTax"] + t["stateTax"] + t["integratedTax"], 2)


def test_a_cash_refund_reverses_the_output_tax(monkeypatch):
    """The whole bug in one assertion: sell 1,800 of tax, refund it all, owe 0."""
    db = _DB(orders=[_order()], returns=[_cash_return()])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert _outward(rep) == 0.0, (
        f"a fully refunded sale still declares {_outward(rep)} of output tax"
    )


def test_a_partial_refund_reverses_only_its_own_tax(monkeypatch):
    db = _DB(orders=[_order()], returns=[_cash_return(tax=500.0, taxable=2777.78)])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert _outward(rep) == 1300.0, _outward(rep)


def test_a_month_with_no_refunds_is_unchanged(monkeypatch):
    """Guards the fix against over-reaching: no returns -> the old number."""
    db = _DB(orders=[_order()])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert _outward(rep) == 1800.0


def test_a_store_credit_refund_is_not_counted_twice(monkeypatch):
    """A refund that DID mint store credit appears in BOTH sources. Counting it
    twice would over-reverse and UNDER-declare - worse than the original bug."""
    rid = "RET-260615-BBB222"
    ledger_row = {"store_id": "S1", "type": "ISSUED",
                  "created_at": WHEN.isoformat(),
                  "reason": f"Credit note for return {rid}",
                  "ref": rid, "tax": 1800.0, "taxable": 10000.0,
                  "amount": 10000.0, "gross_refund": 11800.0,
                  "net_refund": 10000.0, "customer_id": "C1"}
    db = _DB(orders=[_order()],
             returns=[_cash_return(rid=rid)],
             credit_note_ledger=[ledger_row])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    # 1800 sold - 1800 reversed ONCE = 0, never -1800 clamped to 0 by luck:
    # prove it by also checking a bigger sale.
    assert _outward(rep) == 0.0
    db2 = _DB(orders=[_order(tax=5000.0, gross=32777.78)],
              returns=[_cash_return(rid=rid)],
              credit_note_ledger=[ledger_row])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db2)
    rep2 = r._compute_gstr3b("2026-06", "S1")
    assert _outward(rep2) == 3200.0, (
        f"expected 5000 - 1800 = 3200, got {_outward(rep2)} "
        "(1400 means the refund was reversed twice)"
    )


def test_a_refund_outside_the_month_is_not_deducted(monkeypatch):
    late = dict(_cash_return())
    late["created_at"] = datetime(2026, 7, 15, 6, 0)
    db = _DB(orders=[_order()], returns=[late])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert _outward(rep) == 1800.0


def test_refunds_never_produce_a_negative_liability(monkeypatch):
    """More refunded than sold is a carry-forward question for the accountant,
    never a negative number on this screen."""
    db = _DB(orders=[_order(tax=100.0, gross=700.0)], returns=[_cash_return()])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert _outward(rep) == 0.0

def test_an_interstate_refund_reverses_IGST_not_CGST(monkeypatch):
    """Without this the IGST leg of the deduction is untested: a Jharkhand
    store selling to a Jharkhand customer never exercises it, and mutation
    testing showed removing the IGST deduction entirely left the suite green."""
    inter_order = dict(_order())
    inter_order["interstate"] = True
    ret = _cash_return()
    db = _DB(orders=[inter_order], returns=[ret],
             customers=[{"customer_id": "C1", "state": "Maharashtra",
                         "gstin": "", "name": "Out of state"}])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    t = rep["outwardTaxableSupplies"]
    # Sale booked to IGST; the refund must come out of IGST, not CGST/SGST.
    assert t["integratedTax"] == 0.0, t
    assert t["centralTax"] == 0.0 and t["stateTax"] == 0.0, t


def test_an_interstate_sale_with_no_refund_keeps_its_IGST(monkeypatch):
    """The positive control for the test above - proves the assertion is not
    passing merely because every head happens to be zero."""
    inter_order = dict(_order())
    inter_order["interstate"] = True
    db = _DB(orders=[inter_order],
             customers=[{"customer_id": "C1", "state": "Maharashtra",
                         "gstin": "", "name": "Out of state"}])
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    rep = r._compute_gstr3b("2026-06", "S1")
    assert rep["outwardTaxableSupplies"]["integratedTax"] == 1800.0
