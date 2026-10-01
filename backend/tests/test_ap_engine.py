"""
Unit tests for the accounts-payable engine (services/ap_engine.py).

Pure math/date helpers -- no DB, no app. Covers due-date, aging buckets, TDS,
per-bill outstanding, the aging report (single + by-vendor) and the ledger.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.ap_engine import (  # noqa: E402
    compute_due_date,
    aging_bucket,
    compute_tds,
    bill_outstanding,
    build_aging,
    build_aging_by_vendor,
    build_ledger,
)


# --- due date --------------------------------------------------------------


def test_due_date_adds_credit_days():
    assert compute_due_date("2026-01-01", 30) == "2026-01-31"


def test_due_date_zero_credit_is_same_day():
    assert compute_due_date("2026-03-15", 0) == "2026-03-15"


def test_due_date_handles_full_iso_datetime():
    assert compute_due_date("2026-01-01T10:30:00", 10) == "2026-01-11"


def test_due_date_bad_input_is_none():
    assert compute_due_date("not-a-date", 30) is None
    assert compute_due_date(None, 30) is None


# --- aging buckets ---------------------------------------------------------


def test_aging_bucket_boundaries():
    assert aging_bucket(-5) == "current"
    assert aging_bucket(0) == "current"
    assert aging_bucket(1) == "1_30"
    assert aging_bucket(30) == "1_30"
    assert aging_bucket(31) == "31_60"
    assert aging_bucket(60) == "31_60"
    assert aging_bucket(61) == "61_90"
    assert aging_bucket(90) == "61_90"
    assert aging_bucket(91) == "90_plus"
    assert aging_bucket(365) == "90_plus"


def test_aging_bucket_garbage_is_current():
    assert aging_bucket(None) == "current"
    assert aging_bucket("x") == "current"


# --- TDS -------------------------------------------------------------------


def test_tds_194c_other_is_two_percent():
    r = compute_tds(1000, "194C_OTHER")
    assert r["rate"] == 2.0
    assert r["tds_amount"] == 20.0
    assert r["net_payable"] == 980.0


def test_tds_194j_is_ten_percent():
    r = compute_tds(1000, "194J")
    assert r["tds_amount"] == 100.0
    assert r["net_payable"] == 900.0


def test_tds_194q_is_point_one_percent():
    r = compute_tds(100000, "194Q")
    assert r["tds_amount"] == 100.0


def test_tds_none_and_unknown_are_zero():
    assert compute_tds(1000, "NONE")["tds_amount"] == 0.0
    out = compute_tds(1000, "BOGUS")
    assert out["tds_amount"] == 0.0
    assert out["section"] == "NONE"


# --- per-bill outstanding --------------------------------------------------


def _bill(bid="b1", total=1000.0, vendor="v1", due="2026-01-31"):
    return {
        "bill_id": bid,
        "vendor_id": vendor,
        "total_amount": total,
        "due_date": due,
        "bill_date": "2026-01-01",
    }


def test_bill_outstanding_partial_payment():
    bill = _bill(total=1000)
    pays = [{"bill_id": "b1", "amount": 600, "tds_amount": 0}]
    assert bill_outstanding(bill, pays, []) == 400.0


def test_bill_outstanding_payment_plus_tds_settles_full():
    # Pay Rs 900 cash + withhold Rs 100 TDS -> discharges the full Rs 1000.
    bill = _bill(total=1000)
    pays = [{"bill_id": "b1", "amount": 900, "tds_amount": 100}]
    assert bill_outstanding(bill, pays, []) == 0.0


def test_bill_outstanding_debit_note_reduces():
    bill = _bill(total=1000)
    dns = [{"bill_id": "b1", "amount": 250}]
    assert bill_outstanding(bill, [], dns) == 750.0


def test_bill_outstanding_ignores_other_bills_payments():
    bill = _bill(bid="b1", total=1000)
    pays = [{"bill_id": "b2", "amount": 999, "tds_amount": 0}]
    assert bill_outstanding(bill, pays, []) == 1000.0


def test_bill_outstanding_never_negative():
    bill = _bill(total=1000)
    pays = [{"bill_id": "b1", "amount": 5000, "tds_amount": 0}]
    assert bill_outstanding(bill, pays, []) == 0.0


# --- aging report ----------------------------------------------------------


def test_build_aging_buckets_by_due_date():
    # as_of 2026-03-01: b_current due in future, b_overdue due 60 days back.
    bills = [
        _bill(bid="b_current", total=500, due="2026-04-01"),
        _bill(bid="b_overdue", total=1000, due="2026-01-01"),
    ]
    ag = build_aging(bills, [], [], as_of_iso="2026-03-01")
    assert ag["buckets"]["current"] == 500.0
    # 2026-01-01 -> 2026-03-01 is 59 days past due -> 31_60 bucket
    assert ag["buckets"]["31_60"] == 1000.0
    assert ag["total_outstanding"] == 1500.0


def test_build_aging_unallocated_credit_nets_off():
    bills = [_bill(bid="b1", total=1000, due="2026-01-01")]
    # An on-account payment (no bill_id) is an unallocated credit.
    pays = [{"vendor_id": "v1", "amount": 300, "tds_amount": 0}]
    ag = build_aging(bills, pays, [], as_of_iso="2026-02-01")
    # F56: on-account money settles the vendor's oldest bill, so the buckets,
    # the items and total_outstanding owe what the headline owes.
    assert ag["total_outstanding"] == 700.0
    assert sum(ag["buckets"].values()) == 700.0
    assert [it["outstanding"] for it in ag["items"]] == [700.0]
    assert ag["unallocated_credits"] == 0.0
    assert ag["net_payable"] == 700.0


def test_build_aging_on_account_money_settles_oldest_due_first_and_never_another_vendor():
    """F56 (Cash Flow card): a Rs 1,77,896 bill due 2026-08-15 and a Rs 33,440
    on-account payment once read 'Payables Rs 1,44,456' over 'Rs 1,77,896
    overdue' with aging bars summing Rs 1,77,896. Now every figure is 1,44,456.
    The credit settles the OLDEST due bill first and only its own vendor's."""
    bills = [
        _bill(bid="new", total=1000, due="2026-09-30"),
        _bill(bid="old", total=177896, due="2026-08-15"),
        _bill(bid="other", total=500, vendor="v2", due="2026-08-01"),
    ]
    pays = [{"vendor_id": "v1", "amount": 33440, "tds_amount": 0}]
    ag = build_aging(bills, pays, [], as_of_iso="2026-09-29")
    out = {it["bill_id"]: it["outstanding"] for it in ag["items"]}
    assert out == {"old": 144456.0, "new": 1000.0, "other": 500.0}
    assert ag["total_outstanding"] == ag["net_payable"] == 145956.0
    assert sum(ag["buckets"].values()) == 145956.0
    overdue = ag["total_outstanding"] - ag["buckets"]["current"]
    assert overdue == 144956.0 and overdue <= ag["net_payable"]


def test_build_aging_credit_beyond_the_bills_is_an_advance():
    bills = [_bill(bid="b1", total=1000, due="2026-01-01")]
    pays = [
        {"bill_id": "b1", "vendor_id": "v1", "amount": 1200, "tds_amount": 0},  # over-paid 200
        {"vendor_id": "v1", "amount": 300, "tds_amount": 0},  # on account
    ]
    ag = build_aging(bills, pays, [], as_of_iso="2026-02-01")
    assert ag["items"] == [] and ag["total_outstanding"] == 0.0
    assert ag["unallocated_credits"] == 500.0
    assert ag["net_payable"] == 0.0


def test_build_aging_by_vendor_counts_a_vendor_holding_only_an_advance():
    """F56: a Rs 1000 advance to a vendor with no bills dropped out of AP aging
    (grouped by the bills' vendors), so its total owed 1000 more than the
    ledgers, Cash Flow, the report and Vendor Payments."""
    bills = [_bill(bid="b1", total=7780, due="2026-01-01")]
    pays = [{"vendor_id": "v-new", "vendor_name": "New Co", "amount": 1000, "tds_amount": 0}]
    rep = build_aging_by_vendor(bills, pays, [], as_of_iso="2026-02-01")
    flat = build_aging(bills, pays, [], as_of_iso="2026-02-01")
    assert rep["totals"]["net_payable"] == flat["net_payable"] == 6780.0
    assert rep["totals"]["unallocated_credits"] == 1000.0
    assert {v["vendor_id"] for v in rep["vendors"]} == {"v1", "v-new"}
    assert next(v for v in rep["vendors"] if v["vendor_id"] == "v-new")["vendor_name"] == "New Co"


def test_supplier_ledger_rows_drop_transfer_mirror_bills_and_their_money():
    """F56: an inter-company transfer's mirror bill (vendor = our own sending
    company) is not a supplier purchase: the external bill already counts the
    frames. Every payable figure reads these rows, so none counts it."""
    from api.services.ap_engine import supplier_ledger_rows

    bills = [
        {"bill_id": "ext", "vendor_id": "v1", "store_id": "DHN", "total_amount": 3150},
        {"bill_id": "mbill_1", "vendor_id": "ENT-A", "store_id": "BOK", "total_amount": 3150, "source_transfer_id": "T1"},
    ]
    pays = [{"bill_id": "mbill_1", "vendor_id": "ENT-A", "amount": 100}]
    b, p, d = supplier_ledger_rows(bills, pays, [])
    assert [x["bill_id"] for x in b] == ["ext"] and p == [] and d == []


def test_supplier_ledger_rows_give_every_row_one_shop_and_the_shops_add_up():
    """F56/F63: a shop's view is its share of the supplier ledger. Money that
    names no bill is the shop of the supplier's latest bill on or before it
    (else its earliest bill); a supplier that never billed has no shop."""
    from api.services.ap_engine import build_ledger, supplier_ledger_rows

    bills = [
        {"bill_id": "d1", "vendor_id": "v1", "store_id": "DHN", "bill_date": "2026-08-01", "total_amount": 5000},
        {"bill_id": "p1", "vendor_id": "v1", "store_id": "PUN", "bill_date": "2026-09-01", "total_amount": 2000},
    ]
    pays = [
        {"payment_id": "x", "vendor_id": "v1", "bill_id": "p1", "amount": 300, "payment_date": "2026-08-20"},
        {"payment_id": "a", "vendor_id": "v1", "amount": 400, "payment_date": "2026-08-20"},  # -> DHN
        {"payment_id": "b", "vendor_id": "v1", "amount": 600, "payment_date": "2026-09-05"},  # -> PUN
        {"payment_id": "c", "vendor_id": "v1", "amount": 50, "payment_date": "2026-07-01"},  # before any bill -> earliest, DHN
        {"payment_id": "z", "vendor_id": "v-never", "amount": 900, "payment_date": "2026-09-05"},  # no shop
    ]
    shop_pays = {s: [x["payment_id"] for x in supplier_ledger_rows(bills, pays, [], s)[1]] for s in ("DHN", "PUN")}
    assert shop_pays == {"DHN": ["a", "c"], "PUN": ["x", "b"]}

    whole = build_ledger(*supplier_ledger_rows(bills, pays, []))["closing_balance"]
    parts = sum(build_ledger(*supplier_ledger_rows(bills, pays, [], s))["closing_balance"] for s in ("DHN", "PUN"))
    assert parts == 5650.0 and whole == parts - 900.0  # only the never-billed advance is all-stores-only


def test_build_aging_excludes_settled_bills():
    bills = [_bill(bid="b1", total=1000, due="2026-01-01")]
    pays = [{"bill_id": "b1", "amount": 1000, "tds_amount": 0}]
    ag = build_aging(bills, pays, [], as_of_iso="2026-02-01")
    assert ag["total_outstanding"] == 0.0
    assert ag["items"] == []


def test_build_aging_by_vendor_groups_and_totals():
    bills = [
        {"bill_id": "a1", "vendor_id": "v1", "vendor_name": "Alpha", "total_amount": 1000, "due_date": "2026-01-01"},
        {"bill_id": "b1", "vendor_id": "v2", "vendor_name": "Beta", "total_amount": 500, "due_date": "2026-01-01"},
    ]
    rep = build_aging_by_vendor(bills, [], [], as_of_iso="2026-02-01")
    assert rep["totals"]["total_outstanding"] == 1500.0
    assert len(rep["vendors"]) == 2
    # sorted by net_payable desc -> Alpha (1000) first
    assert rep["vendors"][0]["vendor_name"] == "Alpha"
    assert rep["vendors"][0]["net_payable"] == 1000.0


# --- ledger ----------------------------------------------------------------


def test_build_ledger_running_balance_and_totals():
    bills = [_bill(bid="b1", total=1000)]
    pays = [
        {
            "payment_id": "p1",
            "bill_id": "b1",
            "amount": 600,
            "tds_amount": 0,
            "payment_date": "2026-02-01",
            "mode": "BANK",
        }
    ]
    dns = [
        {
            "debit_note_id": "d1",
            "bill_id": "b1",
            "amount": 100,
            "date": "2026-02-15",
            "reason": "Rejected goods",
        }
    ]
    led = build_ledger(bills, pays, dns)
    # bill (+1000) -> pay (-600) -> debit note (-100) = 300 closing
    assert led["closing_balance"] == 300.0
    assert led["total_billed"] == 1000.0
    assert led["total_paid"] == 600.0
    assert led["total_debit_notes"] == 100.0
    # entries are chronological with a running balance
    assert [e["type"] for e in led["entries"]] == ["BILL", "PAYMENT", "DEBIT_NOTE"]
    assert led["entries"][0]["balance"] == 1000.0
    assert led["entries"][1]["balance"] == 400.0
    assert led["entries"][2]["balance"] == 300.0


def test_build_ledger_tds_counts_toward_settlement():
    bills = [_bill(bid="b1", total=1000)]
    pays = [
        {
            "payment_id": "p1",
            "bill_id": "b1",
            "amount": 900,
            "tds_amount": 100,
            "payment_date": "2026-02-01",
        }
    ]
    led = build_ledger(bills, pays, [])
    # gross discharge = 900 + 100 TDS = 1000 -> fully settled
    assert led["closing_balance"] == 0.0
    assert led["total_tds"] == 100.0


def test_build_ledger_empty():
    led = build_ledger([], [], [])
    assert led["closing_balance"] == 0.0
    assert led["entries"] == []
