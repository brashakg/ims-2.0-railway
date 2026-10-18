"""A12: explicit invoice settlement, unapplied advances, unchanged supplier net.

Synthetic dictionaries and mongomock only; no application lifespan/live services.
"""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import mongomock
import pytest
from fastapi import FastAPI

from api.routers import finance as finance_pkg, vendors as vendors_pkg
from api.services import ap_engine
from api.services.supplier_credit_reads import payable_credit_rows
from test_finance_ap_scope_round2 import (
    ADMIN, ACCT_PUNE, PUN, DHN, _DBProxy, _World, _survival_ap_paise, _survival_bills,
)
from test_return_provenance import db  # noqa: F401
from test_supplier_credit_confirmation import payload, post, return_doc  # noqa: F401
from test_supplier_credit_evidence import ready  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


def bill(identifier="B1", total=1000, vendor="V1", **extra):
    return {"bill_id": identifier, "vendor_id": vendor, "total_amount": total,
            "bill_date": "2026-09-01", "due_date": "2026-10-10", **extra}


def payment(amount=400, identifier=None, vendor="V1", **extra):
    return {"bill_id": identifier, "vendor_id": vendor, "amount": amount,
            "payment_date": "2026-09-15", **extra}


def check_balances(bills, payments, notes, gross, unapplied, net):
    before = deepcopy((bills, payments, notes))
    aging = ap_engine.build_aging(bills, payments, notes, "2026-10-15")
    assert aging["total_outstanding"] == aging["owed"] == aging["net_payable"] == gross
    assert sum(aging["buckets"].values()) == gross
    assert sum(it["outstanding"] for it in aging["items"]) == gross
    assert aging["advances"] == aging["unallocated_credits"] == unapplied
    assert ap_engine.build_ledger(bills, payments, notes)["closing_balance"] == net
    grouped = ap_engine.build_aging_by_vendor(bills, payments, notes, "2026-10-15")
    assert grouped["totals"]["owed"] == gross
    assert grouped["totals"]["advances"] == unapplied
    assert sum(v["balance"] for v in grouped["vendors"]) == net
    assert (bills, payments, notes) == before
    return aging


@pytest.mark.parametrize("amount", [400, 1000, 1200])
def test_unallocated_advance_never_settles_an_invoice(amount):
    b = bill()
    p = payment(amount)
    assert ap_engine.bill_as_of(b, [p], [], "2026-10-15")["status"] == "OUTSTANDING"
    check_balances([b], [p], [], 1000, amount, 1000 - amount)


def test_allocated_payment_and_no_bill_advance_controls():
    b, p = bill(), payment(identifier="B1")
    assert ap_engine.bill_as_of(b, [p], [], "2026-10-15")["status"] == "PARTIAL"
    check_balances([b], [p], [], 600, 0, 600)
    check_balances([], [payment()], [], 0, 400, -400)


@pytest.mark.parametrize("identifier", [None, "", "   "])
def test_missing_bill_identity_cannot_match_unallocated_money(identifier):
    b = bill(identifier)
    pays = [payment(identifier=identifier), payment(50, identifier, payment_date="2026-11-01")]
    state = ap_engine.bill_as_of(b, pays, [], "2026-10-15")
    assert (state["outstanding"], state["post_dated_money"], state["post_dated_until"]) == (1000, 0, None)
    check_balances([b], pays[:1], [], 1000, 400, 600)


def test_unknown_nonempty_reference_and_unallocated_note_stay_unapplied():
    check_balances([bill()], [payment(identifier="missing")], [], 1000, 400, 600)
    check_balances([bill()], [], [{"vendor_id": "V1", "amount": 400}], 1000, 400, 600)
    check_balances([bill()], [], [{"vendor_id": "V1", "bill_id": "B1", "amount": 400}], 600, 0, 600)


def test_excess_allocated_money_does_not_spill_to_another_invoice():
    aging = check_balances([bill(), bill("B2", 500)], [payment(1200, "B1")], [], 500, 200, 300)
    assert [(it["bill_id"], it["outstanding"]) for it in aging["items"]] == [("B2", 500)]


def test_cash_and_tds_belong_to_gross_advance_without_reallocating():
    check_balances([bill()], [payment(390, tds_amount=10)], [], 1000, 400, 600)
    check_balances([bill()], [payment(390, "B1", tds_amount=10)], [], 600, 0, 600)


def test_vendor_separation_and_reverse_charge_payable_are_preserved():
    check_balances([bill()], [payment(vendor="V2")], [], 1000, 400, 600)
    check_balances([bill(total=1050, taxable_amount=1000, tax_amount=50, reverse_charge=True)],
                   [payment()], [], 1000, 400, 600)


@pytest.fixture
def report_world(monkeypatch):
    memory = mongomock.MongoClient().a12
    proxy = _DBProxy(memory)
    monkeypatch.setattr(finance_pkg, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: proxy)
    monkeypatch.setattr(finance_pkg.policy_engine, "get_policy", lambda key, scope=None, default=None: default)
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(finance_pkg.router, prefix="/finance")
    return memory, _World(app, monkeypatch).at(datetime(2026, 10, 15, 12))


@pytest.mark.parametrize("due,overdue,due7", [("2026-10-10", 1000, 0), ("2026-10-19", 0, 1000)])
def test_due_reports_keep_invoice_but_supplier_net_stays_zero(report_world, due, overdue, due7):
    memory, world = report_world
    memory.vendors.insert_one({"vendor_id": "V1"})
    memory.vendor_bills.insert_one(bill(store_id=PUN, due_date=due, vendor_critical=True))
    memory.vendor_payments.insert_one(payment(1000, store_id=PUN))
    aging = world.ok("/vendors/ap-aging", ACCT_PUNE)
    assert (aging["totals"]["owed"], aging["totals"]["advances"]) == (1000, 1000)
    assert aging["vendors"][0]["balance"] == 0
    dash = world.ok("/finance/owner-dashboard", ACCT_PUNE)
    assert (dash["payables"]["total"], dash["payables"]["advances"]) == (1000, 1000)
    assert (dash["payables"]["overdue"], dash["payables"]["due_7d"]) == (overdue, due7)
    assert dash["net_position"] == 0
    forecast = world.ok("/finance/cash-flow-forecast", ACCT_PUNE, days=90)
    assert forecast["totals"]["outflow"] == 1000
    survival = world.ok("/finance/survival-cashflow", ACCT_PUNE)
    assert _survival_ap_paise(survival) == 100000
    assert _survival_bills(survival) == {"B1"}
    assert survival["survival"]["must_pay_ap_paise"] == 100000
    cards = world.ok("/finance/vendor-payments", ACCT_PUNE)
    assert cards[0]["balance"] == 0
    report = world.ok("/vendors/purchases-this-month", ACCT_PUNE)
    assert len(report["vendors"]) == 1
    row = report["vendors"][0]
    assert (row["owed"], row["paid"], row["billed"], row["next_due_date"]) == (0, 0, 0, due)


def test_zero_net_undatable_invoice_is_not_dropped_from_monthly_report(report_world):
    memory, world = report_world
    memory.vendor_bills.insert_one(bill(store_id=PUN, due_date=None, bill_date=None))
    memory.vendor_payments.insert_one(payment(1000, store_id=PUN))
    report = world.ok("/vendors/purchases-this-month", ACCT_PUNE)
    assert len(report["vendors"]) == 1
    assert report["vendors"][0]["next_due_date"] is None
    assert report["vendors"][0]["owed"] == 0


def test_shop_dates_mirrors_and_supplier_net_do_not_cross_net(report_world):
    memory, world = report_world
    memory.vendor_bills.insert_many([
        bill(store_id=PUN), bill("B2", vendor="V1", store_id=DHN),
        bill("B3", vendor="V2", store_id=PUN),
        bill("FUTURE", store_id=PUN, bill_date="2026-11-01"),
        bill("mbill_T1", store_id=PUN, source_transfer_id="T1", auto_generated=True),
    ])
    memory.vendor_payments.insert_many([
        payment(store_id=PUN), payment(1000, vendor="V2", store_id=PUN),
        payment(500, vendor="V3", store_id=PUN),
        payment(400, "B1", store_id=PUN, payment_date="2026-11-01"),
        payment(1000, "mbill_T1", store_id=PUN),
    ])
    dash = world.ok("/finance/owner-dashboard", ACCT_PUNE)
    assert (dash["payables"]["total"], dash["payables"]["advances"]) == (2000, 1900)
    assert dash["net_position"] == -600  # V3's advance cannot pay V1's debt.
    org = world.ok("/finance/owner-dashboard", ADMIN)
    assert (org["payables"]["total"], org["payables"]["advances"], org["net_position"]) == (3000, 1900, -1600)
    assert world.get("/vendors/ap-aging", ACCT_PUNE, store_id=DHN).status_code == 403
    historical = world.ok("/vendors/purchases-this-month", ACCT_PUNE, month="2026-08")
    assert historical["vendors"] == []


def test_bill_less_payment_entry_remains_allowed_and_named_bill_is_validated(report_world, monkeypatch):
    memory, world = report_world
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository", lambda: SimpleNamespace(
        find_by_id=lambda identifier: {"vendor_id": identifier, "legal_name": "Supplier"},
    ))
    memory.vendor_bills.insert_one(bill(store_id=PUN))
    body = {"amount": 400, "payment_date": "2026-10-15", "store_id": PUN}
    response = world.post("/vendors/V1/payments", ACCT_PUNE, body)
    assert response.status_code == 201, response.text
    saved = memory.vendor_payments.find_one({})
    assert saved["bill_id"] is None and saved["store_id"] == PUN
    aging = world.ok("/vendors/ap-aging", ACCT_PUNE)
    assert (aging["totals"]["owed"], aging["totals"]["advances"]) == (1000, 400)
    denied = world.post("/vendors/V1/payments", ACCT_PUNE, {**body, "bill_id": "not-a-bill"})
    assert denied.status_code == 404
    assert memory.vendor_payments.count_documents({}) == 1


def test_legacy_shop_attribution_and_postdated_notes_keep_existing_row_rules():
    bills = [bill(store_id=PUN), bill("B2", store_id=DHN, bill_date="2026-10-01")]
    pays = [payment(), payment(50, "B2", store_id=PUN, payment_date="2026-10-05")]
    notes = [{"vendor_id": "V1", "bill_id": "B1", "amount": 200, "date": "2026-11-01"}]
    pune = ap_engine.supplier_ledger_rows(bills, pays, notes, PUN, "2026-10-15")
    dhanbad = ap_engine.supplier_ledger_rows(bills, pays, notes, DHN, "2026-10-15")
    # Legacy unstamped advance belongs to the latest bill's shop on its date.
    # A named bill's shop wins over a mismatched legacy money stamp.
    check_balances(*pune, 1000, 400, 600)
    check_balances(*dhanbad, 950, 0, 950)
    assert ap_engine.bill_as_of(bills[0], [], notes, "2026-10-15")["post_dated_money"] == 200


@pytest.mark.parametrize("status,expected", [("POSTED", 336), ("PENDING_EVIDENCE", 420), ("PREPARING", 420)])
def test_canonical_bill_credit_reduces_only_its_bill_separately_from_advance(db, return_doc, status, expected):
    post(db, return_doc, payload(db, return_doc))
    db.supplier_credit_confirmations.update_one({}, {"$set": {"posting_status": status}})
    db.vendor_bills.update_one({}, {"$set": {"total_amount": 420}})
    db.debit_notes.insert_one({"rtv_ref_id": "R1", "status": "ISSUED", "amount": 999})
    original = db.vendor_bills.find_one({})
    notes = payable_credit_rows(db, vendor_id="V1")
    check_balances([original], [payment(100)], notes, expected, 100, expected - 100)
