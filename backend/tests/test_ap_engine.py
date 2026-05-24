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
    assert ag["total_outstanding"] == 1000.0
    assert ag["unallocated_credits"] == 300.0
    assert ag["net_payable"] == 700.0


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
