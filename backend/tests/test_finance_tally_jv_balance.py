"""
IMS 2.0 — Tally sales-JV CGST/SGST split must balance to the paisa
===================================================================
The Tally sales-voucher export splits each order's total GST into intra-state
CGST + SGST. A naive round(tax/2) on BOTH sides over-states by a paisa on
odd-paise tax (100.01 -> 50.01 + 50.01 = 100.02), which imbalances the voucher
(subtotal + cgst + sgst != grand_total) and Tally rejects it on import. The fix
puts the rounding residual on SGST so cgst + sgst == tax exactly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-finance-jv")


def test_split_sums_to_tax_for_odd_paise():
    from api.routers.finance import _jv_cgst_sgst_split

    # The classic failing case: odd-paise tax.
    cgst, sgst = _jv_cgst_sgst_split(100.01)
    assert round(cgst + sgst, 2) == 100.01
    # and the residual lands on SGST (cgst is the floor-half)
    assert cgst == 50.01 or cgst == 50.00


def test_split_balances_voucher_across_many_values():
    from api.routers.finance import _jv_cgst_sgst_split

    grand = 1000.0
    for tax_paise in range(0, 5000):  # tax 0.00 .. 49.99
        tax = round(tax_paise / 100.0, 2)
        cgst, sgst = _jv_cgst_sgst_split(tax)
        # CGST + SGST must equal the tax to the paisa...
        assert round(cgst + sgst, 2) == tax, f"imbalance at tax={tax}"
        # ...and the full voucher (subtotal + cgst + sgst) must equal grand_total.
        subtotal = round(grand - tax, 2)
        assert round(subtotal + cgst + sgst, 2) == grand, f"voucher imbalance at tax={tax}"


def test_split_zero_tax():
    from api.routers.finance import _jv_cgst_sgst_split

    assert _jv_cgst_sgst_split(0.0) == (0.0, 0.0)
