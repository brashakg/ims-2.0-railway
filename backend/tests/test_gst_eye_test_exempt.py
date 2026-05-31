"""
IMS 2.0 — GST-exempt eye-test / consultation line (clinic initiative C5-B)
===========================================================================
An optometry consultation is a health service exempt under SAC 9993
(Notification 12/2017-CT(R) Sr. 74). The owner approved billing it at 0%
on the SAME invoice as taxable goods.

The contract these tests lock in:
  1. EYE_TEST / EYE_EXAM / CONSULT / CONSULTATION / OPTOMETRY resolve to 0%.
  2. A 0% consult line is purely ADDITIVE — it contributes taxable>0, tax=0
     and leaves the 5% (frame) and 18% (sunglass) rate buckets byte-identical
     to a cart that omits it. (The invoice tax split is rate-bucketed, so a
     0% line can never perturb another rate row.)
  3. The invoice CGST/SGST/IGST split surfaces three rate rows; the 0% row
     carries zero tax; the 5%/18% rows are unchanged.
  4. An eye-test line is non-serialized — it never demands stock to mark sold.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-eye-test-gst")


# ---------------------------------------------------------------------------
# 1. Rate resolution
# ---------------------------------------------------------------------------


def test_eye_test_aliases_resolve_to_exempt():
    from api.services.gst_rates import resolve_gst_rate

    for cat in ("EYE_TEST", "EYE_EXAM", "EYE_CHECKUP", "CONSULT", "CONSULTATION", "OPTOMETRY"):
        assert resolve_gst_rate(category=cat) == 0.0, f"{cat} should be GST-exempt"


def test_eye_test_hsn_is_sac_9993():
    from api.services.gst_rates import GST_CATEGORY_TABLE

    assert GST_CATEGORY_TABLE["EYE_TEST"] == ("9993", 0.0)
    assert GST_CATEGORY_TABLE["CONSULTATION"] == ("9993", 0.0)


# ---------------------------------------------------------------------------
# 2. Additivity — the consult line never perturbs the taxable rate buckets
# ---------------------------------------------------------------------------


def _frame_and_sunglass():
    # FRAME -> 5%, SUNGLASS -> 18%. Fresh dicts each call (helper mutates in place).
    return [
        {"item_total": 2000.0, "item_type": "FRAME"},
        {"item_total": 5000.0, "item_type": "SUNGLASS"},
    ]


def test_consult_line_is_purely_additive():
    from api.routers.orders import _compute_per_category_gst

    without = _compute_per_category_gst(_frame_and_sunglass(), 0)

    mixed_items = _frame_and_sunglass() + [
        {"item_total": 300.0, "item_type": "EYE_TEST"}
    ]
    mixed = _compute_per_category_gst(mixed_items, 0)

    # The consult line itself: rate 0, taxable is the full ₹300, tax is zero.
    consult = mixed_items[-1]
    assert consult["gst_rate"] == 0.0
    assert consult["taxable_value"] == 300.0
    assert consult["tax_amount"] == 0.0

    # The frame (5%) and sunglass (18%) lines are byte-identical with/without
    # the consult present — proving the 0% line perturbs NO other rate bucket.
    assert mixed_items[0]["gst_rate"] == 5.0
    assert mixed_items[1]["gst_rate"] == 18.0
    frame_without = _frame_and_sunglass()
    _compute_per_category_gst(frame_without, 0)
    assert mixed_items[0]["taxable_value"] == frame_without[0]["taxable_value"]
    assert mixed_items[0]["tax_amount"] == frame_without[0]["tax_amount"]
    assert mixed_items[1]["taxable_value"] == frame_without[1]["taxable_value"]
    assert mixed_items[1]["tax_amount"] == frame_without[1]["tax_amount"]

    # Cart-level tax is unchanged; only the taxable base grows by the ₹300.
    assert mixed["tax"] == without["tax"]
    assert round(mixed["taxable"] - without["taxable"], 2) == 300.0
    # grand_total (taxable + tax) == sum the customer pays, inclusive of consult.
    assert round(mixed["taxable"] + mixed["tax"], 2) == round(
        without["taxable"] + without["tax"] + 300.0, 2
    )


# ---------------------------------------------------------------------------
# 3. Invoice CGST/SGST/IGST split — three rate rows, 0% row carries no tax
# ---------------------------------------------------------------------------


def test_invoice_split_has_exempt_row_and_untouched_taxable_rows():
    from api.routers.orders import _build_invoice_gst_split, _compute_per_category_gst

    items = _frame_and_sunglass() + [{"item_total": 300.0, "item_type": "EYE_TEST"}]
    _compute_per_category_gst(items, 0)  # stamps gst_rate / taxable_value / tax_amount

    # Same-state store + customer -> intra-state -> CGST + SGST.
    store = {"state": "20", "gstin": "20ABCDE1234F1Z5"}
    customer = {"state": "20"}
    split = _build_invoice_gst_split(items, store, customer)

    rows_by_rate = {r["rate"]: r for r in split["rows"]}
    assert set(rows_by_rate) == {0.0, 5.0, 18.0}, "all three rate buckets present"

    # The exempt row: taxable is the ₹300 consult, every tax component is zero.
    exempt = rows_by_rate[0.0]
    assert exempt["taxable"] == 300.0
    assert exempt["cgst"] == 0.0 and exempt["sgst"] == 0.0 and exempt["igst"] == 0.0
    assert exempt["tax"] == 0.0

    # The taxable rows still split tax cleanly into CGST+SGST (=tax).
    for rate in (5.0, 18.0):
        row = rows_by_rate[rate]
        assert row["tax"] > 0.0
        assert round(row["cgst"] + row["sgst"], 2) == row["tax"]
        assert row["igst"] == 0.0

    # Invoice totals reconcile: total tax == frame tax + sunglass tax (+ 0).
    assert split["totals"]["tax"] == round(
        rows_by_rate[5.0]["tax"] + rows_by_rate[18.0]["tax"], 2
    )


# ---------------------------------------------------------------------------
# 4. Non-serialized — an eye-test line never demands stock
# ---------------------------------------------------------------------------


def test_eye_test_item_types_are_non_serialized():
    from api.routers.orders import _NON_SERIALIZED_ITEM_TYPES

    for t in ("EYE_TEST", "EYE_EXAM", "EYE_CHECKUP", "CONSULT", "CONSULTATION", "OPTOMETRY"):
        assert t in _NON_SERIALIZED_ITEM_TYPES
