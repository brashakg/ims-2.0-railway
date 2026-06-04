"""
GST summary CGST/SGST/IGST split
================================
get_gst_summary used to split the period's output tax 50/50 into CGST+SGST and
never report IGST, mis-stating every inter-state sale. _split_output_tax now
classifies per order by the same store-state-vs-customer-state rule as
gst_reconciliation()/GSTR-1, paise-balanced so cgst+sgst+igst == the total.
"""

from api.routers import finance


def test_intrastate_only_splits_cgst_sgst():
    orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 100.0}]
    cgst, sgst, igst = finance._split_output_tax(
        orders, {"S1": "Jharkhand"}, {"C1": "Jharkhand"}
    )
    assert igst == 0.0
    assert cgst == 50.0 and sgst == 50.0


def test_interstate_goes_to_igst():
    orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 100.0}]
    cgst, sgst, igst = finance._split_output_tax(
        orders, {"S1": "Jharkhand"}, {"C1": "Maharashtra"}
    )
    assert igst == 100.0
    assert cgst == 0.0 and sgst == 0.0


def test_unknown_state_treated_intrastate_and_tax_total_fallback():
    # No state maps -> intra-state (prior behaviour). tax_total used when
    # tax_amount is absent (mirrors _TAX_EXPR's $ifNull).
    orders = [{"store_id": "S1", "customer_id": "C1", "tax_total": 90.0}]
    cgst, sgst, igst = finance._split_output_tax(orders, {}, {})
    assert igst == 0.0
    assert cgst == 45.0 and sgst == 45.0


def test_mixed_is_paise_balanced():
    # Odd-paise intra + inter mix: the three must sum to the exact total, and
    # the intra portion (cgst+sgst) must equal the intra tax exactly.
    orders = [
        {"store_id": "S1", "customer_id": "C1", "tax_amount": 33.33},  # intra
        {"store_id": "S1", "customer_id": "C2", "tax_amount": 10.01},  # inter
    ]
    store = {"S1": "JH"}
    cust = {"C1": "JH", "C2": "MH"}
    cgst, sgst, igst = finance._split_output_tax(orders, store, cust)
    assert igst == 10.01
    assert round(cgst + sgst, 2) == 33.33
    assert round(cgst + sgst + igst, 2) == 43.34


def test_same_state_case_insensitive_is_intrastate():
    orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 20.0}]
    cgst, sgst, igst = finance._split_output_tax(
        orders, {"S1": "Maharashtra"}, {"C1": "maharashtra "}
    )
    assert igst == 0.0
    assert cgst == 10.0 and sgst == 10.0
