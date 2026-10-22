"""
GST batch 1, money-review round 2 (PR #1253 at 46ae2f44 -> BLOCK).
==================================================================
1. A credit note files the place of supply its SALE filed (the parent's
   place-of-supply rule), never the customer's raw state or the shop's.
2. Finance and Tally split IGST vs CGST+SGST by THE place-of-supply rule
   (finance._order_is_interstate delegates to it): the Tally voucher, the
   cross-check books and the returns agree.
3. The VOID ruling holds on every reader of the real-sale rule.
4. A Rs 0 line never trips the rate checks; '5%' reads as 5; the GST report
   answers an unfileable order with a 422.
5. 'Void' in any case is the rule's VOID.
6. The report's issue list shows errors before warnings.
Each test fails at 46ae2f44 (REVERT CHECK in the docstrings).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import api.routers.reports as r  # noqa: E402
from api.services import gstn_export  # noqa: E402
from strict_fakes import StrictCollection, StrictDB, matches  # noqa: E402

from test_gstr1_review_round1 import (  # noqa: E402
    ADMIN, MONTH, SHOP, _db, _ledger, _line, _order, _return, _use,
)

GSTIN_MH = "27AAACG1234A1ZZ"


# ===========================================================================
# 1. Credit-note place of supply = the sale's
# ===========================================================================


def test_gstin_only_buyer_note_files_the_sales_place_of_supply(monkeypatch):
    """A Jharkhand shop sells to a buyer known only by a Maharashtra GSTIN.
    The sale files IGST to 27; its credit note must too.

    REVERT CHECK: at 46ae2f44 the note's place is the customer's blank state
    -> the shop's 'Jharkhand' -> pos 20 on an IGST note (portal rejects)."""
    cust = {"customer_id": "CG", "name": "MH Opticals", "gstin": GSTIN_MH, "state": ""}
    _use(monkeypatch, _db(customers=[cust],
                          orders=[_order("O1", [_line(5, 5000.0, 250.0, "900311")], customer="CG")],
                          returns=[_return("RET-260615-0101", "O1", 1000.0, 50.0, customer="CG")],
                          credit_note_ledger=[_ledger("RET-260615-0101", 1000.0, 50.0, customer="CG")]))
    rep = r._compute_gstr1(MONTH, "S1")
    (sale,) = rep["b2b"]
    (note,) = rep["cdnr"]
    assert sale["igst"] == 250.0 and note["igst"] == 50.0
    assert sale["placeOfSupplyCode"] == note["placeOfSupplyCode"] == "27"
    from api.services.org_validation import resolve_state_code

    assert resolve_state_code(note["placeOfSupply"]) == "27", note["placeOfSupply"]  # the screen too
    out = gstn_export.to_gstr1_json(rep, gstin=SHOP["gstin"], period=MONTH)
    assert out["b2b"][0]["inv"][0]["pos"] == out["cdnr"][0]["nt"][0]["pos"] == "27"


def test_billing_address_buyer_note_files_the_sales_place_of_supply(monkeypatch):
    """Blank state, Maharashtra billing address, Rs 3 lakh: the sale is B2CL
    to 27, and so is its credit note.

    REVERT CHECK: at 46ae2f44 the note's place is 'Jharkhand' (20)."""
    cust = {"customer_id": "CB", "name": "Buyer", "state": "", "billing_address": {"state": "Maharashtra"}}
    _use(monkeypatch, _db(customers=[cust],
                          orders=[_order("O1", [_line(5, 285714.29, 14285.71, "900311")], customer="CB")],
                          returns=[_return("RET-260615-0102", "O1", 1000.0, 50.0, customer="CB")]))
    rep = r._compute_gstr1(MONTH, "S1")
    (sale,) = rep["b2cl"]
    (note,) = rep["cdnr"]
    assert sale["placeOfSupplyCode"] == note["placeOfSupplyCode"] == "27"
    assert note["igst"] == 50.0


# ===========================================================================
# 2. Finance / Tally ask THE place-of-supply rule
# ===========================================================================

NOW = datetime.utcnow() - timedelta(minutes=5)


def _supply_world():
    customers = [
        {"customer_id": "C-BILL", "name": "Bill MH", "state": "", "billing_address": {"state": "Maharashtra"}},
        {"customer_id": "C-GSTIN", "name": "GSTIN MH", "gstin": GSTIN_MH, "state": ""},
        {"customer_id": "C-RANCHI", "name": "Ranchi", "state": "Ranchi"},
    ]
    orders = [
        _order("O-BILL", [_line(18, 300000.0, 54000.0, "900410")], customer="C-BILL", created_at=NOW),
        _order("O-GSTIN", [_line(18, 10000.0, 1800.0, "900410")], customer="C-GSTIN", created_at=NOW),
        _order("O-RANCHI", [_line(5, 2000.0, 100.0, "900311")], customer="C-RANCHI", created_at=NOW),
    ]
    return _db(customers=customers, orders=orders)


EXPECT = {"O-BILL": ("igst", 54000.0), "O-GSTIN": ("igst", 1800.0), "O-RANCHI": ("cgst+sgst", 100.0)}


def _heads(o):
    return {"igst": o["igst_amount"], "cgst": o["cgst_amount"], "sgst": o["sgst_amount"]}


def test_tally_sales_voucher_splits_as_the_returns_file(monkeypatch):
    """Rs 3,54,000 to a billing-address-only Maharashtra buyer and a GSTIN-only
    Maharashtra buyer are IGST; a customer state 'Ranchi' at a Jharkhand shop
    is CGST + SGST -- in the Tally voucher exactly as in GSTR-1.

    REVERT CHECK: at 46ae2f44 finance read customers.state only: the two MH
    buyers are blank -> intra (CGST+SGST on an IGST invoice), and 'Ranchi'
    passed _norm_state unchanged -> 'Ranchi' != '20' -> IGST."""
    import agents.nexus_providers as nexus
    from api.routers import finance

    db = _supply_world()
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    seen = {}
    monkeypatch.setattr(nexus, "tally_build_day_voucher_xml",
                        lambda orders, meta: seen.setdefault("orders", list(orders)) and "<x/>")
    asyncio.run(finance.get_tally_sales_jv(from_date=None, to_date=None, store_id="S1",
                                           entity_id=None, current_user=ADMIN))
    tally = {o["order_id"]: _heads(o) for o in seen["orders"]}

    _use(monkeypatch, db)
    month = NOW.strftime("%Y-%m")
    rep = r._compute_gstr1(month, "S1")
    returns = {}
    for sec in ("b2b", "b2cl", "b2cs"):
        for row in rep[sec]:
            returns[row.get("invoiceNumber", "b2cs")] = row
    for oid, (head, tax) in EXPECT.items():
        t = tally[oid]
        if head == "igst":
            assert (t["igst"], t["cgst"], t["sgst"]) == (tax, 0.0, 0.0), (oid, t)
        else:
            assert t["igst"] == 0.0 and round(t["cgst"] + t["sgst"], 2) == tax, (oid, t)
    assert returns["BV/26/O-BILL"]["igst"] == 54000.0  # B2CL
    (b2cs,) = rep["b2cs"]
    assert b2cs["igst"] == 0.0 and round(b2cs["cgst"] + b2cs["sgst"], 2) == 100.0  # Ranchi


def test_cross_check_books_have_no_gap_with_the_return(monkeypatch):
    """REVERT CHECK: at 46ae2f44 the cross-check's Tally heads put the two MH
    sales in CGST/SGST and Ranchi in IGST -- a gap on every head."""
    from api.routers.finance.gst_crosscheck import _books_and_tally_for_stores
    from api.utils.ist import ist_month_window_utc

    db = _use(monkeypatch, _supply_world())
    g3 = r._compute_gstr3b(NOW.strftime("%Y-%m"), "S1")["outwardTaxableSupplies"]
    _books, tally = _books_and_tally_for_stores(db, ["S1"], *ist_month_window_utc(NOW.year, NOW.month))
    assert (tally["igst"], tally["cgst"], tally["sgst"]) == (
        g3["integratedTax"], g3["centralTax"], g3["stateTax"]), (tally, g3)


def test_finance_interstate_is_the_returns_rule():
    """One rule: finance._order_is_interstate answers what _order_supply does."""
    from api.routers import finance

    order = {"store_id": "S1", "customer_id": "C"}
    assert finance._order_is_interstate(order, {"S1": SHOP}, {"C": {"gstin": GSTIN_MH}}) is True
    assert finance._order_is_interstate(order, {"S1": SHOP}, {"C": {"state": "Ranchi"}}) is False
    assert finance._order_is_interstate(order, {"S1": "Jharkhand"}, {"C": "JH"}) is False


# ===========================================================================
# 3. The VOID ruling on every reader
# ===========================================================================


class _Recording(StrictCollection):
    """Records the order ids every READ selects (not the rule's own VOID read)."""

    def __init__(self, docs, seen):
        super().__init__("orders", docs)
        self.seen = seen

    def _rec(self, flt):
        st = (flt or {}).get("status")
        if isinstance(st, dict) and "void" in str(st.get("$regex", "")):
            return
        self.seen.update(d.get("order_id") for d in self.docs if matches(d, flt or {}))

    def find(self, filter=None, projection=None, **kw):
        self._rec(filter)
        return super().find(filter, projection, **kw)

    def find_one(self, filter=None, projection=None, sort=None, **kw):
        self._rec(filter)
        return super().find_one(filter, projection, sort=sort, **kw)

    def count_documents(self, filter=None, **kw):
        self._rec(filter)
        return super().count_documents(filter, **kw)

    def aggregate(self, pipeline, **kw):
        if pipeline and "$match" in pipeline[0]:
            self._rec(pipeline[0]["$match"])
        return super().aggregate(pipeline, **kw)


def _void_orders():
    base = dict(created_at=NOW, customer="")
    out = []
    for pay in ("PAID", "UNPAID"):
        out.append(_order(f"V-INV-{pay}", [_line(5, 1000.0, 50.0, "900311")], status="VOID",
                          status_before_void="CONFIRMED", payment_status=pay, balance_due=1050.0,
                          payments=[{"method": "CASH", "amount": 1050.0}], **base))
        out.append(_order(f"V-NEVER-{pay}", [_line(5, 1000.0, 50.0, "900311")], status="Void",
                          status_before_void="CONFIRMED", invoice_number=None, payment_status=pay,
                          balance_due=1050.0, payments=[{"method": "CASH", "amount": 1050.0}], **base))
    return out


def _reader_world():
    seen: set = set()
    db = _db()
    db._collections["orders"] = _Recording(_void_orders(), seen)
    return db, seen


TODAY = NOW.date().isoformat()
FROM = (NOW.date() - timedelta(days=1)).isoformat()


def _call(reader, db, monkeypatch):
    from api.routers import finance
    import api.routers.reports.finance_ops as fops
    import api.routers.reconciliation as recon

    monkeypatch.setattr(finance, "_get_db", lambda: db)
    if reader == "cash_flow":
        return asyncio.run(finance.get_cash_flow(period="month", store_id=None, current_user=ADMIN))
    if reader == "owner_dashboard":
        return asyncio.run(finance.owner_dashboard(current_user=ADMIN))
    if reader == "pnl_by_store":
        return asyncio.run(finance.get_pnl_by_store(from_date=FROM, to_date=TODAY, entity_id=None,
                                                    current_user=ADMIN))
    if reader == "pnl_by_category":
        return asyncio.run(finance.get_pnl_by_category(from_date=FROM, to_date=TODAY, store_id=None,
                                                       entity_id=None, current_user=ADMIN))
    if reader == "get_revenue":
        return asyncio.run(finance.get_revenue(period="month", store_id=None, current_user=ADMIN))
    if reader == "receivables":
        return asyncio.run(finance.get_outstanding(store_id=None, current_user=ADMIN))
    if reader == "survival":
        return asyncio.run(finance.get_survival_cashflow(store_id=None, current_user=ADMIN))
    if reader == "receipt_voucher":
        monkeypatch.setattr(recon, "tender_receipt_policy_enabled", lambda **k: True)
        monkeypatch.setattr(recon, "_get_db", lambda: db)
        return asyncio.run(recon.get_tally_tender_receipt_jv(from_date=FROM, to_date=TODAY, store_id="S1",
                                                             entity_id=None, current_user=ADMIN))
    if reader == "tally_sales_voucher":
        import agents.nexus_providers as nexus
        monkeypatch.setattr(nexus, "tally_build_day_voucher_xml", lambda orders, meta: "<x/>")
        return asyncio.run(finance.get_tally_sales_jv(from_date=FROM, to_date=TODAY, store_id="S1",
                                                      entity_id=None, current_user=ADMIN))
    if reader == "gst_report":
        orders = db.get_collection("orders")

        class _Repo:
            def find_many(self, query, limit=0, **_k):
                return list(orders.find(query))

        monkeypatch.setattr(fops, "get_order_repository", lambda: _Repo())
        _use(monkeypatch, db)
        return asyncio.run(fops.gst_report(from_date=date.fromisoformat(FROM), to_date=date.fromisoformat(TODAY),
                                           store_id="S1", current_user=ADMIN))
    if reader == "ticker":
        from api.services import ticker_service
        return ticker_service.mtd_revenue(db.get_collection("orders"), None)
    raise AssertionError(reader)


READERS = ["cash_flow", "owner_dashboard", "pnl_by_store", "pnl_by_category", "get_revenue",
           "receivables", "survival", "receipt_voucher", "tally_sales_voucher", "gst_report", "ticker"]


@pytest.mark.parametrize("reader", READERS)
def test_every_reader_keeps_invoiced_voids_and_drops_never_invoiced(reader, monkeypatch):
    """REVERT CHECK: with main's status-only filter every reader reads the
    never-invoiced VOIDs; with 14b7ff62's VOID exclusion none reads the
    invoiced ones."""
    db, seen = _reader_world()
    _call(reader, db, monkeypatch)
    assert not {"V-NEVER-PAID", "V-NEVER-UNPAID"} & seen, (reader, seen)
    assert {"V-INV-PAID", "V-INV-UNPAID"} & seen, (reader, seen)


def test_analytics_billable_is_the_rule():
    """REVERT CHECK: analytics' old status set counted every VOID."""
    from api.routers.analytics import _is_billable

    inv, never = _void_orders()[0], _void_orders()[1]
    assert _is_billable(inv) is True and _is_billable(never) is False


# ===========================================================================
# 4-6. Cheap ones
# ===========================================================================


def test_zero_value_line_without_a_rate_is_skipped_not_refused(monkeypatch):
    """REVERT CHECK: at 46ae2f44 a Rs 0 freebie line with no rate and an
    unknown category raises (422) before the zero-value skip."""
    freebie = {"taxable_value": 0.0, "tax_amount": 0.0, "item_total": 0.0, "category": "FREEBIE"}
    o = _order("O1", [_line(5, 1000.0, 50.0, "900311")], customer="")
    o["items"].append(freebie)
    _use(monkeypatch, _db(orders=[o]))
    assert r._compute_gstr1(MONTH, "S1")["totalTax"] == 50.0


def test_percent_rate_string_reads(monkeypatch):
    """REVERT CHECK: at 46ae2f44 gst_rate '5%' is 'unreadable' (422)."""
    line = _line(5, 1000.0, 50.0, "900311")
    line["gst_rate"] = "5%"
    _use(monkeypatch, _db(orders=[_order("O1", [line], customer="")]))
    assert r._compute_gstr1(MONTH, "S1")["b2cs"][0]["gstRate"] == 5


def test_gst_report_answers_422_naming_the_order(monkeypatch):
    """REVERT CHECK: at 46ae2f44 the GST report lets UnfileableOrder escape
    as a 500."""
    import api.routers.reports.finance_ops as fops

    bad = _order("O-BAD-7", [_line(5, 1000.0, 50.0, "900311")], customer="")
    bad["grand_total"] = "N/A"
    db = _db(orders=[bad])

    class _Repo:
        def find_many(self, query, limit=0, **_k):
            return list(db.get_collection("orders").find(query))

    monkeypatch.setattr(fops, "get_order_repository", lambda: _Repo())
    _use(monkeypatch, db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(fops.gst_report(from_date=date(2026, 6, 1), to_date=date(2026, 6, 30),
                                    store_id="S1", current_user=ADMIN))
    assert exc.value.status_code == 422 and "O-BAD-7" in exc.value.detail


def test_mixed_case_void_follows_the_rule(monkeypatch):
    """REVERT CHECK: at 46ae2f44 the VOID read matched 'VOID'/'void' only, so
    a never-invoiced 'Void' order was filed."""
    never = _order("O-V", [_line(18, 1000.0, 180.0, "900410")], customer="", status="Void",
                   invoice_number=None)
    live = _order("O-L", [_line(5, 1000.0, 50.0, "900311")], customer="")
    _use(monkeypatch, _db(orders=[never, live]))
    assert r._compute_gstr1(MONTH, "S1")["totalTax"] == 50.0


def test_errors_are_listed_before_warnings(monkeypatch):
    """60 flagged 0% lines (warnings) and one unplaceable credit note (an
    error): the error must be in the 50 shown.

    REVERT CHECK: at 46ae2f44 the first 50 issues were all 0% warnings."""
    orders = [_order(f"O{i}", [_line(0, 100.0, 0.0, "999316")], customer="") for i in range(60)]
    _use(monkeypatch, _db(orders=orders, credit_note_ledger=[_ledger("CN-MANUAL-9", 100.0, 5.0, customer="")]))
    shown = r._compute_gstr1(MONTH, "S1")["validation"]["issues"]
    assert shown[0]["level"] == "error", shown[:2]
