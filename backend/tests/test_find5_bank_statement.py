"""
FIND-5: Bank statement CSV parser + auto-reconciliation matcher.

Pure unit tests -- no DB, no app.  We import only the helper functions by
extracting them from finance.py after setting the required env var.
"""

import os
import sys

# Set required env var BEFORE any imports from the app
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-for-find5-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.finance import _parse_bank_csv, _auto_match_statement  # noqa: E402


# ---------------------------------------------------------------------------
# _parse_bank_csv
# ---------------------------------------------------------------------------


HDFC_CSV = """Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
15/05/2026,UPI/VENDOR PAYMENT/REF123,,15/05/2026,10000.00,,90000.00
16/05/2026,UPI/CUSTOMER RECEIPT/REF456,,16/05/2026,,25000.00,115000.00
18/05/2026,NEFT/SALARY PAYOUT,,18/05/2026,50000.00,,65000.00
"""

ICICI_CSV = """Transaction Date,Description,Debit,Credit,Balance
12/05/2026,Vendor payment,5000.00,,95000.00
13/05/2026,Customer payment,,12000.00,107000.00
"""

SBI_AMOUNT_CSV = """Date,Particulars,Amount,Balance
10/05/2026,Payment to vendor,-8000,92000
11/05/2026,Receipt from customer,+15000,107000
"""

CUSTOM_DATE_CSV = """date,description,debit,credit,balance
2026-05-20,Outward payment,3000,,97000
2026-05-21,Inward receipt,,7000,104000
"""


class TestParseBankCsv:
    """FIND-5: CSV parsing."""

    def test_hdfc_style_separate_debit_credit(self):
        rows = _parse_bank_csv(HDFC_CSV)
        assert len(rows) == 3
        assert rows[0]["debit"] == 10000.0
        assert rows[0]["credit"] == 0.0
        assert rows[1]["credit"] == 25000.0
        assert rows[1]["debit"] == 0.0
        assert rows[2]["debit"] == 50000.0

    def test_icici_style(self):
        rows = _parse_bank_csv(ICICI_CSV)
        assert len(rows) == 2
        assert rows[0]["debit"] == 5000.0
        assert rows[1]["credit"] == 12000.0

    def test_sbi_single_amount_column(self):
        rows = _parse_bank_csv(SBI_AMOUNT_CSV)
        assert len(rows) == 2
        assert rows[0]["debit"] == 8000.0
        assert rows[0]["credit"] == 0.0
        assert rows[1]["credit"] == 15000.0
        assert rows[1]["debit"] == 0.0

    def test_iso_date_format(self):
        rows = _parse_bank_csv(CUSTOM_DATE_CSV)
        assert rows[0]["date"] == "2026-05-20"
        assert rows[1]["date"] == "2026-05-21"

    def test_empty_csv_returns_empty(self):
        rows = _parse_bank_csv("")
        assert rows == []

    def test_no_parseable_rows_returns_empty(self):
        csv = "Junk,Headers\nmore,junk\n"
        rows = _parse_bank_csv(csv)
        assert rows == []

    def test_balance_parsed(self):
        rows = _parse_bank_csv(ICICI_CSV)
        assert rows[0]["balance"] == 95000.0
        assert rows[1]["balance"] == 107000.0

    def test_balance_none_when_not_present(self):
        csv = "date,description,debit,credit\n2026-05-01,Pay,1000,\n"
        rows = _parse_bank_csv(csv)
        assert rows[0]["balance"] is None

    def test_description_preserved(self):
        rows = _parse_bank_csv(ICICI_CSV)
        assert rows[0]["description"] == "Vendor payment"

    def test_commas_in_amounts_handled(self):
        csv = "date,description,debit,credit,balance\n2026-05-01,Big pay,\"1,00,000.00\",,\"9,00,000.00\"\n"
        rows = _parse_bank_csv(csv)
        assert rows[0]["debit"] == 100000.0


# ---------------------------------------------------------------------------
# _auto_match_statement
# ---------------------------------------------------------------------------


def _make_receipt(order_id, amount, date):
    return {"order_id": order_id, "grand_total": amount, "created_at": date}


def _make_payment(payment_id, amount, date, vendor_id="V1"):
    return {"payment_id": payment_id, "amount": amount, "payment_date": date, "vendor_id": vendor_id}


class TestAutoMatchStatement:
    """FIND-5: Auto-matching logic."""

    def test_exact_credit_match(self):
        rows = [{"date": "2026-05-15", "credit": 25000.0, "debit": 0.0, "description": "Customer"}]
        receipts = [_make_receipt("O-001", 25000.0, "2026-05-15")]
        result = _auto_match_statement(rows, receipts, [])
        assert result[0]["match_type"] == "RECEIPT"
        assert result[0]["match"]["id"] == "O-001"

    def test_exact_debit_match(self):
        rows = [{"date": "2026-05-16", "debit": 10000.0, "credit": 0.0, "description": "Vendor"}]
        payments = [_make_payment("P-001", 10000.0, "2026-05-16")]
        result = _auto_match_statement(rows, [], payments)
        assert result[0]["match_type"] == "PAYMENT"
        assert result[0]["match"]["id"] == "P-001"

    def test_within_tolerance_matches(self):
        # Rs 0.50 rounding difference
        rows = [{"date": "2026-05-15", "credit": 25000.50, "debit": 0.0, "description": ""}]
        receipts = [_make_receipt("O-001", 25000.0, "2026-05-15")]
        result = _auto_match_statement(rows, receipts, [])
        assert result[0]["match_type"] == "RECEIPT"

    def test_outside_tolerance_no_match(self):
        # Rs 5 difference -- outside the Rs 1 tolerance
        rows = [{"date": "2026-05-15", "credit": 25005.0, "debit": 0.0, "description": ""}]
        receipts = [_make_receipt("O-001", 25000.0, "2026-05-15")]
        result = _auto_match_statement(rows, receipts, [])
        assert result[0]["match_type"] == "UNMATCHED"

    def test_date_within_window_matches(self):
        # Payment on 15th, bank clears on 17th (2 days -- within 3-day window)
        rows = [{"date": "2026-05-17", "debit": 8000.0, "credit": 0.0, "description": ""}]
        payments = [_make_payment("P-001", 8000.0, "2026-05-15")]
        result = _auto_match_statement(rows, [], payments)
        assert result[0]["match_type"] == "PAYMENT"

    def test_date_outside_window_no_match(self):
        # 5 days apart -- outside the 3-day window
        rows = [{"date": "2026-05-20", "debit": 8000.0, "credit": 0.0, "description": ""}]
        payments = [_make_payment("P-001", 8000.0, "2026-05-15")]
        result = _auto_match_statement(rows, [], payments)
        assert result[0]["match_type"] == "UNMATCHED"

    def test_no_double_match(self):
        # Two statement rows with same amount/date -- only one should match
        rows = [
            {"date": "2026-05-15", "credit": 10000.0, "debit": 0.0, "description": "A"},
            {"date": "2026-05-15", "credit": 10000.0, "debit": 0.0, "description": "B"},
        ]
        receipts = [_make_receipt("O-001", 10000.0, "2026-05-15")]
        result = _auto_match_statement(rows, receipts, [])
        match_types = [r["match_type"] for r in result]
        assert match_types.count("RECEIPT") == 1
        assert match_types.count("UNMATCHED") == 1

    def test_empty_statement_returns_empty(self):
        result = _auto_match_statement([], [], [])
        assert result == []

    def test_no_receipts_or_payments_all_unmatched(self):
        rows = [
            {"date": "2026-05-15", "credit": 5000.0, "debit": 0.0, "description": "X"},
            {"date": "2026-05-16", "debit": 3000.0, "credit": 0.0, "description": "Y"},
        ]
        result = _auto_match_statement(rows, [], [])
        assert all(r["match_type"] == "UNMATCHED" for r in result)
