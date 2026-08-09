"""
IMS 2.0 - NEXUS autonomous Tally export must book output GST (P1 money/GST)
===========================================================================
DEFECT
------
`NexusAgent._build_tally_export` (the UNATTENDED 23:00 tick) passed RAW order
documents straight into `tally_build_day_voucher_xml`, which is a dumb
formatter: it reads `subtotal` / `cgst_amount` / `sgst_amount` / `igst_amount`
straight off each dict. A raw IMS order carries NONE of the GST-head fields and
its `subtotal` is the pre-cart-discount, tax-INCLUSIVE gross. The nightly
voucher therefore booked the FULL order value as Sales with ZERO output GST:
sales overstated, GST liability understated, and the accountant's books could
never tie to the filed GSTR-1.

Every human-driven Tally path already reshapes first
(`finance.get_tally_sales_jv`, `finance._b2b_fetch_orders`). The fix routes
NEXUS through `tally_build_day_voucher_xml_checked`, which reuses the CANONICAL
finance tax rules (`_order_is_interstate` + `_jv_cgst_sgst_split`) and then
gates the result on `assert_voucher_balanced` + a tax-coverage check.

Rupee anchor used throughout: a Rs 1,180.00 intra-state sale carrying Rs 180.00
GST must export as Sales Rs 1,000.00 + CGST Rs 90.00 + SGST Rs 90.00 against a
party leg of Rs -1,180.00.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-nexus-tally-gst")

from agents.nexus_providers import (  # noqa: E402
    TallyExportError,
    tally_build_day_voucher_xml,
    tally_build_day_voucher_xml_checked,
    tally_reshape_orders_for_voucher,
    validate_voucher_balance,
)


# ===========================================================================
# Fixtures - a RAW order document, exactly the shape orders.py persists
# ===========================================================================


def _raw_order(
    *,
    order_id: str = "ORD-1",
    store_id: str = "BV-GK1",
    grand_total: float = 1180.0,
    tax_amount: float = 180.0,
    gross_subtotal: float = 1180.0,
    interstate=None,
    created=None,
    with_items: bool = True,
):
    """A RAW order as `orders.py` writes it.

    Note what is DELIBERATELY absent: no `taxable`, no `cgst_amount`, no
    `sgst_amount`, no `igst_amount`. And `subtotal` is the tax-INCLUSIVE gross
    before the cart discount, NOT the taxable value.
    """
    doc = {
        "order_id": order_id,
        "store_id": store_id,
        "status": "COMPLETED",
        "created_at": created
        or datetime.now(timezone.utc).replace(
            hour=10, minute=0, second=0, microsecond=0, tzinfo=None
        ),
        "customer_name": "Walk-in Customer",
        "customer_id": None,
        "subtotal": gross_subtotal,
        "tax_rate": 18.0,
        "tax_amount": tax_amount,
        "total_discount": 0.0,
        "grand_total": grand_total,
        "pricing_model": "inclusive",
    }
    if interstate is not None:
        doc["interstate"] = interstate
    if with_items:
        doc["items"] = [
            {
                "item_id": "L1",
                "item_total": gross_subtotal,
                "gst_rate": 18.0,
                "taxable_value": round(grand_total - tax_amount, 2),
                "tax_amount": tax_amount,
            }
        ]
    return doc


def _ledger_amounts(xml: str, voucher_index: int = 0) -> dict:
    """{ledger name -> signed rupee amount} for one VOUCHER in the export."""
    root = ET.fromstring(xml)
    vouchers = root.findall(".//VOUCHER")
    assert vouchers, "export carried no VOUCHER block"
    out = {}
    for entry in vouchers[voucher_index].findall("ALLLEDGERENTRIES.LIST"):
        name = entry.findtext("LEDGERNAME")
        out[name] = float(entry.findtext("AMOUNT"))
    return out


# ===========================================================================
# 1. The defect itself - raw orders through the dumb formatter
# ===========================================================================


def test_raw_order_through_dumb_formatter_is_the_defect():
    """Regression anchor: this is EXACTLY what NEXUS used to emit.

    Sales books the Rs 1,180 gross and output GST is Rs 0.00. Kept as a test so
    nobody 'simplifies' the orchestrator back onto the raw formatter.
    """
    legs = _ledger_amounts(tally_build_day_voucher_xml([_raw_order()]))
    assert legs["Sales A/c"] == 1180.00, "the gross, not the taxable value"
    assert legs["CGST Output"] == 0.00
    assert legs["SGST Output"] == 0.00


# ===========================================================================
# 2. Intra-state: Sales = TAXABLE, CGST + SGST = the order's tax
# ===========================================================================


def test_intrastate_order_books_taxable_sales_and_cgst_sgst():
    order = _raw_order()
    xml, priced, rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)

    assert legs["Sales A/c"] == 1000.00, "Sales leg must be the TAXABLE value"
    assert legs["CGST Output"] == 90.00
    assert legs["SGST Output"] == 90.00
    assert legs["Walk-in Customer"] == -1180.00, "party leg is the gross"
    # CGST + SGST equals the order's own total GST to the paisa.
    assert round(legs["CGST Output"] + legs["SGST Output"], 2) == 180.00
    # ...and no IGST head is emitted for an intra-state sale.
    assert "IGST Output" not in legs
    assert priced[0]["subtotal"] == 1000.00
    assert rejected == []


def test_intrastate_voucher_balances():
    xml, _priced, _rejected = tally_build_day_voucher_xml_checked(None, [_raw_order()])
    legs = _ledger_amounts(xml)
    assert round(sum(legs.values()), 2) == 0.00, "debits must equal credits"


# ===========================================================================
# 3. Inter-state: the whole tax goes to IGST
# ===========================================================================


def test_interstate_order_books_igst_not_cgst_sgst():
    """OS-008: the order's own `interstate` flag decides the GST head."""
    order = _raw_order(order_id="ORD-INTER", interstate=True)
    xml, _priced, _rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)

    assert legs["Sales A/c"] == 1000.00
    assert legs["IGST Output"] == 180.00
    assert "CGST Output" not in legs and "SGST Output" not in legs
    assert round(sum(legs.values()), 2) == 0.00


# ===========================================================================
# 4. Zero-tax / exempt order still balances
# ===========================================================================


def test_zero_tax_order_still_balances():
    """An exempt / zero-rated sale: Sales == gross, no GST, voucher balances."""
    order = _raw_order(
        order_id="ORD-EXEMPT", grand_total=500.0, tax_amount=0.0, gross_subtotal=500.0
    )
    xml, _priced, _rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)

    assert legs["Sales A/c"] == 500.00
    assert legs["CGST Output"] == 0.00 and legs["SGST Output"] == 0.00
    assert legs["Walk-in Customer"] == -500.00
    assert round(sum(legs.values()), 2) == 0.00


# ===========================================================================
# 5. Odd-paise tax: the split must never imbalance the voucher
# ===========================================================================


def test_voucher_balances_across_every_odd_paise_tax():
    """Rs 1,000 gross, tax sweeping 0.00 -> 9.99 (every paise residue).

    A naive round(tax/2) on BOTH heads over-states by a paisa on odd-paise tax
    (100.01 -> 50.01 + 50.01), which imbalances the voucher and Tally rejects
    the whole import. One export, 1000 vouchers, all must net to zero.
    """
    orders = [
        _raw_order(
            order_id=f"O{paise}",
            grand_total=1000.0,
            tax_amount=round(paise / 100.0, 2),
            gross_subtotal=1000.0,
            with_items=False,
        )
        for paise in range(0, 1000)
    ]
    xml, _priced, _rejected = tally_build_day_voucher_xml_checked(None, orders)

    root = ET.fromstring(xml)
    vouchers = root.findall(".//VOUCHER")
    assert len(vouchers) == 1000
    for paise, voucher in enumerate(vouchers):
        tax = round(paise / 100.0, 2)
        legs = {}
        for entry in voucher.findall("ALLLEDGERENTRIES.LIST"):
            legs[entry.findtext("LEDGERNAME")] = float(entry.findtext("AMOUNT"))
        assert round(sum(legs.values()), 2) == 0.00, f"imbalance at tax={tax}"
        assert round(legs["CGST Output"] + legs["SGST Output"], 2) == tax
        assert legs["Sales A/c"] == round(1000.0 - tax, 2)


# ===========================================================================
# 6. The gate - a voucher that would under-book output GST is REFUSED
# ===========================================================================


def test_gate_refuses_voucher_that_would_under_book_gst():
    """The order's LINES declare Rs 180 GST but the order header says zero.

    The canonical split reads the header, so the voucher would carry Rs 0.00
    output GST with the full Rs 1,180 on Sales -- the original defect shape,
    and one that still BALANCES. The tax-coverage gate must catch it.
    """
    order = _raw_order(order_id="ORD-BROKEN", tax_amount=0.0)
    order["items"][0]["tax_amount"] = 180.0
    order["items"][0]["taxable_value"] = 1000.0

    with pytest.raises(TallyExportError) as exc:
        tally_build_day_voucher_xml_checked(None, [order])
    msg = str(exc.value)
    assert "ORD-BROKEN" in msg
    assert "0.00" in msg and "180.00" in msg


def test_gate_refuses_when_canonical_helpers_are_unavailable(monkeypatch):
    """A degraded import must FAIL LOUD, never silently fall back to no split.

    Falling back to a local copy of the GST maths is exactly the drift this
    module refuses to allow, and emitting nothing beats emitting a zero-GST
    voucher the CA would import.
    """
    monkeypatch.setitem(sys.modules, "api.routers.finance", None)
    with pytest.raises(TallyExportError):
        tally_build_day_voucher_xml_checked(None, [_raw_order()])


def test_reshape_does_not_mutate_the_caller_orders():
    order = _raw_order()
    tally_reshape_orders_for_voucher(None, [order])
    assert order["subtotal"] == 1180.0, "caller's document must be untouched"
    assert "cgst_amount" not in order


# ===========================================================================
# 6b. Non-POS document shapes - the defect used to SURVIVE all of these
# ===========================================================================


def test_legacy_total_tax_header_key_is_priced_not_ignored():
    """reports.py:218 _order_tax reads ("tax_amount", "total_tax", "tax"), so
    the shape exists. Reading only `tax_amount`/`tax_total` booked the whole
    Rs 1,180 as Sales with Rs 0.00 GST -- and the gate, reading the same narrow
    chain, blessed it."""
    order = {
        "order_id": "ORD-TOTALTAX",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "Walk-in Customer",
        "grand_total": 1180.0,
        "total_tax": 180.0,
        "items": [],
    }
    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)
    assert rejected == []
    assert legs["Sales A/c"] == 1000.00
    assert legs["CGST Output"] == 90.00 and legs["SGST Output"] == 90.00
    assert round(sum(legs.values()), 2) == 0.00


def test_ondc_shape_books_real_money_not_a_zero_voucher():
    """ondc_seller.py:851-885 writes `total_amount` + `gst_amount` and NO
    grand_total / tax_amount, with status DELIVERED (inside NEXUS's filter).
    Reading only `grand_total`/`total` resolved a gross of Rs 0.00 and emitted
    an entirely zero voucher for a Rs 1,180 sale -- and it passed the gate."""
    order = {
        "order_id": "ORD-ONDC",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "ONDC Buyer",
        "subtotal": 1000.0,
        "total_amount": 1180.0,
        "gst_amount": 180.0,
        "items": [],
    }
    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)
    assert rejected == []
    assert legs["ONDC Buyer"] == -1180.00
    assert legs["Sales A/c"] == 1000.00
    assert legs["CGST Output"] == 90.00 and legs["SGST Output"] == 90.00


def test_techcherry_blank_tax_column_is_REFUSED_not_booked_as_all_sales():
    """techcherry_import.py:335-343 -- a blank TaxAmount column yields
    tax_amount 0.0 while `subtotal` still carries the taxable Rs 1,000, status
    is hardcoded DELIVERED and created_at falls back to now(), so the row lands
    in TONIGHT's window. The document contradicts itself; booking Rs 1,180 as
    Sales with Rs 0.00 GST is the defect this module exists to kill."""
    order = {
        "order_id": "ORD-TC",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "Walk-in Customer",
        "grand_total": 1180.0,
        "subtotal": 1000.0,
        "tax_amount": 0.0,
        "items": [],
        "status": "DELIVERED",
    }
    with pytest.raises(TallyExportError) as exc:
        tally_build_day_voucher_xml_checked(None, [order])
    msg = str(exc.value)
    assert "ORD-TC" in msg and "1000.00" in msg and "1180.00" in msg


def test_discounted_pos_order_is_NOT_caught_by_the_subtotal_cross_check():
    """A real POS `subtotal` is the PRE-cart-discount tax-INCLUSIVE gross, so it
    is >= grand_total. The subtotal contradiction gate must never fire on a
    discounted bill -- Rs 8,000 of lines at a 20% cart discount.

    Rs 5,000 @5% + Rs 3,000 @18%, cart -20% -> gross 6,400.00, tax 556.58.
    """
    order = {
        "order_id": "ORD-DISCOUNTED",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "Walk-in Customer",
        "subtotal": 8000.0,  # pre-discount gross
        "cart_discount_percent": 20.0,
        "grand_total": 6400.0,
        "tax_amount": 556.58,
        "items": [
            {
                "item_total": 5000.0,
                "gst_rate": 5.0,
                "taxable_value": 3809.52,
                "tax_amount": 190.48,
            },
            {
                "item_total": 3000.0,
                "gst_rate": 18.0,
                "taxable_value": 2033.90,
                "tax_amount": 366.10,
            },
        ],
    }
    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)
    assert rejected == [], "a discounted POS bill must not be quarantined"
    assert legs["Sales A/c"] == 5843.42
    assert legs["CGST Output"] == 278.29 and legs["SGST Output"] == 278.29
    assert round(sum(legs.values()), 2) == 0.00


def test_unclassifiable_order_is_refused_rather_than_booked_as_all_sales():
    """No GST under ANY known key and no per-line tax -> hard stop."""
    order = {
        "order_id": "ORD-OPAQUE",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "Walk-in Customer",
        "grand_total": 1180.0,
        "items": [],
    }
    with pytest.raises(TallyExportError) as exc:
        tally_build_day_voucher_xml_checked(None, [order])
    assert "ORD-OPAQUE" in str(exc.value)


def test_explicit_zero_tax_is_still_allowed_as_exempt():
    """An affirmative `tax_amount: 0.0` with no contradicting subtotal is an
    exempt sale, not an unclassifiable one."""
    order = {
        "order_id": "ORD-EXEMPT2",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "Walk-in Customer",
        "grand_total": 500.0,
        "subtotal": 500.0,
        "tax_amount": 0.0,
        "items": [],
    }
    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [order])
    legs = _ledger_amounts(xml)
    assert rejected == []
    assert legs["Sales A/c"] == 500.00 and legs["CGST Output"] == 0.00


def test_non_numeric_amount_is_scoped_to_its_own_order_not_the_whole_chain():
    """A junk amount used to raise a bare ValueError out of the reshape, escape
    the per-store handler and take down ALL SIX stores' exports with an error
    naming neither store nor order."""
    good = _raw_order(order_id="ORD-GOOD")
    junk = _raw_order(order_id="ORD-JUNK")
    junk["tax_amount"] = "N/A"

    xml, priced, rejected = tally_build_day_voucher_xml_checked(None, [good, junk])
    assert [r["order_id"] for r in rejected] == ["ORD-JUNK"]
    assert "ORD-JUNK" in rejected[0]["reason"]
    assert [p["order_id"] for p in priced] == ["ORD-GOOD"]
    legs = _ledger_amounts(xml)
    assert legs["Sales A/c"] == 1000.00


def test_negative_gross_is_refused_and_zero_total_emits_a_parseable_amount():
    """The formatter used to prefix a literal '-', so a Rs 0 order emitted
    `-0.00` and a negative total `--1180.00` -- unparseable by Tally, while the
    numerically-negating gate blessed both."""
    zero = {
        "order_id": "ORD-ZERO",
        "created_at": "2026-08-09T10:00:00+00:00",
        "customer_name": "Walk-in Customer",
        "grand_total": 0.0,
        "tax_amount": 0.0,
        "items": [],
    }
    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [zero])
    assert rejected == []
    assert "-0.00" not in xml
    assert _ledger_amounts(xml)["Walk-in Customer"] == 0.00

    negative = dict(zero, order_id="ORD-NEG", grand_total=-1180.0, tax_amount=-180.0)
    with pytest.raises(TallyExportError) as exc:
        tally_build_day_voucher_xml_checked(None, [negative])
    assert "ORD-NEG" in str(exc.value)


# ===========================================================================
# 6c. Quarantine - one bad order must not veto a whole store
# ===========================================================================


def test_one_bad_order_is_quarantined_and_the_good_ones_still_export():
    good = [_raw_order(order_id=f"ORD-G{i}") for i in range(3)]
    bad = _raw_order(order_id="ORD-BAD-LINE", tax_amount=0.0)
    bad["items"][0]["tax_amount"] = 180.0
    bad["items"][0]["taxable_value"] = 1000.0
    bad.pop("subtotal")  # isolate the tax-coverage gate

    xml, priced, rejected = tally_build_day_voucher_xml_checked(None, good + [bad])

    assert [r["order_id"] for r in rejected] == ["ORD-BAD-LINE"]
    assert len(priced) == 3
    root = ET.fromstring(xml)
    assert len(root.findall(".//VOUCHER")) == 3, "the 3 good vouchers still ship"
    assert "ORD-BAD-LINE" not in xml


def test_all_orders_bad_raises_because_there_is_nothing_to_emit():
    bad = _raw_order(order_id="ORD-ONLY-BAD", tax_amount=0.0)
    bad["items"][0]["tax_amount"] = 180.0
    bad.pop("subtotal")
    with pytest.raises(TallyExportError) as exc:
        tally_build_day_voucher_xml_checked(None, [bad])
    assert "ORD-ONLY-BAD" in str(exc.value)


# ===========================================================================
# 7. validate_voucher_balance - honest taxable resolution
# ===========================================================================


def test_validator_resolves_taxable_from_line_items():
    """A real order has no order-level `taxable`; the lines carry it."""
    report = validate_voucher_balance([_raw_order()])
    assert report["ok"] is True
    assert report["totals"]["taxable"] == 1000.00
    assert report["totals"]["tax"] == 180.00
    assert report["totals"]["unverified_count"] == 0


def test_validator_no_longer_double_subtracts_discount():
    """grand_total is ALREADY net of discounts; subtracting again failed the
    batch identity for any day that had a single discounted sale."""
    order = _raw_order(order_id="ORD-DISC")
    order["total_discount"] = 250.0
    report = validate_voucher_balance([order])
    assert report["batch_delta"] == 0.0
    assert report["ok"] is True


def test_validator_reports_unverifiable_orders_instead_of_passing_them():
    """'Nothing was checked' must NEVER render as 'checked and fine'.

    The download endpoint reads only `balanced`, so an ok=True here would ship
    a green, unsuffixed file on a day where not one rupee was cross-checked --
    and that is exactly the voucher shape the emit gates are weakest against.
    """
    legacy = _raw_order(order_id="ORD-LEGACY", with_items=False)
    report = validate_voucher_balance([legacy])
    assert report["mismatch_count"] == 0
    assert report["unverified_count"] == 1
    assert report["totals"]["unverified_count"] == 1
    assert report["verified"] is False
    assert report["ok"] is False, "unverified must not report as balanced"


def test_validator_marks_a_mixed_day_unverified_too():
    """One good order does not launder a day that also carries unverifiable ones."""
    report = validate_voucher_balance(
        [_raw_order(order_id="GOOD"), _raw_order(order_id="OPAQUE", with_items=False)]
    )
    assert report["mismatch_count"] == 0
    assert report["unverified_count"] == 1
    assert report["ok"] is False


def test_validator_resolves_gross_from_legacy_total_key():
    """A legacy order carrying `total` instead of `grand_total` exports a
    CORRECT voucher, so the validator must not false-flag it UNBALANCED. The
    reshape and the validator must resolve the gross through the SAME chain --
    two resolvers in one file is the drift this module exists to prevent."""
    legacy = _raw_order(order_id="ORD-TOTAL")
    legacy["total"] = legacy.pop("grand_total")
    report = validate_voucher_balance([legacy])
    assert report["ok"] is True
    assert report["totals"]["grand_total"] == 1180.00

    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [legacy])
    legs = _ledger_amounts(xml)
    assert rejected == []
    assert legs["Sales A/c"] == 1000.00 and legs["CGST Output"] == 90.00


def test_validator_still_catches_a_real_mismatch():
    """Header says Rs 1,200 but the lines only add up to Rs 1,000 + Rs 180."""
    bad = _raw_order(order_id="ORD-BAD")
    bad["grand_total"] = 1200.0
    report = validate_voucher_balance([bad])
    assert report["ok"] is False
    assert report["mismatches"][0]["order_id"] == "ORD-BAD"
    assert report["mismatches"][0]["delta"] == 20.0


# ===========================================================================
# 8. End-to-end through the autonomous orchestrator
# ===========================================================================


def _cmp(actual, op, op_val) -> bool:
    if actual is None:
        return op in ("$ne", "$nin")
    try:
        if op == "$gte":
            return actual >= op_val
        if op == "$lte":
            return actual <= op_val
        if op == "$lt":
            return actual < op_val
    except TypeError:
        return False
    if op == "$in":
        return actual in op_val
    if op == "$nin":
        return actual not in op_val
    if op == "$ne":
        return actual != op_val
    if op == "$exists":
        return (actual is not None) == bool(op_val)
    return False


def _doc_matches(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_doc_matches(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if not _cmp(actual, op, op_val):
                    return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._skip = 0
        self._limit = None

    def sort(self, *a, **k):
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def __iter__(self):
        out = self._docs[self._skip :]
        if self._limit:
            out = out[: self._limit]
        return iter(out)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("order_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _doc_matches(d, filter_):
                return d
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor(d for d in self.docs if _doc_matches(d, filter_))

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _doc_matches(d, filter_):
                d.update((update or {}).get("$set", {}) or {})
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            new_doc = dict((update or {}).get("$set", {}))
            new_doc.update(filter_)
            self.docs.append(new_doc)
            return type("R", (), {"modified_count": 0, "matched_count": 0})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def count_documents(self, filter_=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter_))


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        if name.startswith("_") or name in {"is_connected"}:
            raise AttributeError(name)
        return self.get_collection(name)


def _seeded_db(extra_stores=(), customers=()):
    db = FakeDB()
    db.get_collection("stores").insert_one(
        {
            "store_id": "BV-GK1",
            "store_code": "GK1",
            "store_name": "GK-I Flagship",
            "state": "Jharkhand",
            "is_active": True,
        }
    )
    for s in extra_stores:
        db.get_collection("stores").insert_one(s)
    for c in customers:
        db.get_collection("customers").insert_one(c)
    return db


@pytest.fixture
def nexus_and_db(monkeypatch):
    return _wire_nexus(monkeypatch, _seeded_db())


def _wire_nexus(monkeypatch, db):
    from agents.implementations import nexus as nexus_module
    from api import dependencies as deps_module
    from database.repositories.store_repository import StoreRepository

    agent = nexus_module.NexusAgent(db=db)
    agent.get_collection = lambda name: db.get_collection(name)
    monkeypatch.setattr(
        deps_module,
        "get_store_repository",
        lambda: StoreRepository(db.get_collection("stores")),
    )
    return agent, db


# ===========================================================================
# 8a. Inter-state split resolved through the REAL state maps (no flag)
# ===========================================================================


@pytest.mark.parametrize(
    "seller_state,buyer_state",
    [
        ("Jharkhand", "Maharashtra"),  # plain names
        ("JH", "27"),  # mismatched formats -> _norm_state
        ("20", "Maharashtra"),  # GST code vs name
    ],
)
def test_interstate_resolved_from_state_maps_without_any_flag(
    seller_state, buyer_state
):
    """POS orders NEVER persist `interstate` (orders.py:2383), so the whole
    intra/inter decision rides on the store-vs-customer state fallback. Passing
    db=None makes both maps EMPTY, which silently proves nothing -- every such
    'intra-state' assertion is really just the default. This drives the REAL
    maps."""
    db = _seeded_db(
        customers=[{"customer_id": "CUST-MH", "state": buyer_state}],
    )
    db.get_collection("stores").docs[0]["state"] = seller_state

    order = _raw_order(order_id="ORD-MH")
    order["customer_id"] = "CUST-MH"
    assert "interstate" not in order

    xml, _p, rejected = tally_build_day_voucher_xml_checked(db, [order])
    legs = _ledger_amounts(xml)
    assert rejected == []
    assert legs["IGST Output"] == 180.00
    assert "CGST Output" not in legs and "SGST Output" not in legs
    assert legs["Sales A/c"] == 1000.00
    assert legs["Walk-in Customer"] == -1180.00
    assert round(sum(legs.values()), 2) == 0.00


def test_same_state_customer_resolved_from_state_maps_books_cgst_sgst():
    """The intra-state mirror, proved by real data rather than an empty map."""
    db = _seeded_db(customers=[{"customer_id": "CUST-JH", "state": "Jharkhand"}])
    order = _raw_order(order_id="ORD-JH")
    order["customer_id"] = "CUST-JH"

    xml, _p, _r = tally_build_day_voucher_xml_checked(db, [order])
    legs = _ledger_amounts(xml)
    assert legs["CGST Output"] == 90.00 and legs["SGST Output"] == 90.00
    assert "IGST Output" not in legs


@pytest.mark.asyncio
async def test_nightly_export_writes_a_voucher_with_real_output_gst(nexus_and_db):
    """The autonomous 23:00 path, end to end, on a RAW order document."""
    agent, db = nexus_and_db
    db.get_collection("orders").insert_one(_raw_order(order_id="ORD-NIGHTLY"))

    result = await agent._build_tally_export()

    assert result.ok is True
    rows = db.get_collection("tally_exports").docs
    assert len(rows) == 1
    legs = _ledger_amounts(rows[0]["xml"])
    assert legs["Sales A/c"] == 1000.00
    assert legs["CGST Output"] == 90.00 and legs["SGST Output"] == 90.00
    assert legs["Walk-in Customer"] == -1180.00
    assert round(sum(legs.values()), 2) == 0.00
    assert rows[0]["balanced"] is True

    assert rows[0]["unverified_count"] == 0
    assert rows[0]["gate_skipped_orders"] == []
    assert rows[0]["builder_version"] == 2


def _broken_order(order_id="ORD-NIGHTLY-BAD"):
    """Header declares no GST while the lines declare Rs 180 -- unpriceable."""
    broken = _raw_order(order_id=order_id, tax_amount=0.0)
    broken["items"][0]["tax_amount"] = 180.0
    broken["items"][0]["taxable_value"] = 1000.0
    broken.pop("subtotal")
    return broken


@pytest.mark.asyncio
async def test_nightly_export_poisons_a_STALE_row_when_nothing_can_be_priced(
    nexus_and_db,
):
    """The old code just `continue`d, which left ANY pre-existing row for that
    (export_date, store_id) untouched -- and admin.py:670 streams it with
    `suffix = "" if row.get("balanced", True)`, i.e. a stale or PRE-FIX
    zero-GST voucher downloads green and unmarked. Seeding that prior row is
    the only way to see the defect; asserting `docs == []` from an empty
    collection structurally cannot."""
    agent, db = nexus_and_db
    export_date = (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )
    # A PRE-FIX row: the whole Rs 1,180 booked as Sales, Rs 0.00 GST, green.
    db.get_collection("tally_exports").insert_one(
        {
            "export_date": export_date,
            "store_id": "BV-GK1",
            "store_code": "GK1",
            "voucher_count": 1,
            "xml": "<ENVELOPE><BODY>stale pre-fix zero-GST voucher</BODY></ENVELOPE>",
            "balanced": True,
        }
    )
    db.get_collection("orders").insert_one(_broken_order())

    result = await agent._build_tally_export()

    assert result.ok is False, "the export must fail loudly"
    assert "ORD-NIGHTLY-BAD" in (result.error or "")
    rows = db.get_collection("tally_exports").docs
    assert len(rows) == 1
    row = rows[0]
    assert row["balanced"] is False, "a download must be suffixed _UNBALANCED"
    assert row["xml"] == "", "the stale zero-GST XML must not survive"
    assert row["voucher_count"] == 0
    assert "ORD-NIGHTLY-BAD" in row["gate_error"]


@pytest.mark.asyncio
async def test_one_bad_order_does_not_black_out_the_store_or_its_siblings(
    monkeypatch,
):
    """MIXED BATCH. Store A: 3 good orders + 1 unpriceable. Store B: clean.

    A whole-store abort meant one bad imported row withheld the store's genuine
    vouchers EVERY night, with /regenerate re-running the identical gate and no
    operator escape short of hand-editing Mongo.
    """
    db = _seeded_db(
        extra_stores=[
            {
                "store_id": "BV-LAJ",
                "store_code": "LAJ",
                "store_name": "Lajpat Nagar",
                "state": "Jharkhand",
                "is_active": True,
            }
        ]
    )
    agent, db = _wire_nexus(monkeypatch, db)
    orders = db.get_collection("orders")
    for i in range(3):
        orders.insert_one(_raw_order(order_id=f"ORD-A{i}", store_id="BV-GK1"))
    orders.insert_one(_broken_order("ORD-A-BAD"))
    orders.insert_one(_raw_order(order_id="ORD-B1", store_id="BV-LAJ"))

    result = await agent._build_tally_export()

    rows = {r["store_id"]: r for r in db.get_collection("tally_exports").docs}
    assert set(rows) == {"BV-GK1", "BV-LAJ"}

    a = rows["BV-GK1"]
    assert a["voucher_count"] == 3, "the 3 good vouchers still ship"
    assert a["gate_skipped_orders"] == ["ORD-A-BAD"]
    assert a["balanced"] is False, "an incomplete file must download _UNBALANCED"
    assert ET.fromstring(a["xml"]).findall(".//VOUCHER").__len__() == 3
    assert round(sum(_ledger_amounts(a["xml"]).values()), 2) == 0.00

    b = rows["BV-LAJ"]
    assert b["voucher_count"] == 1 and b["balanced"] is True
    assert _ledger_amounts(b["xml"])["Sales A/c"] == 1000.00

    assert result.ok is False, "the quarantine must still be reported loudly"
    assert "BV-GK1" in (result.error or "")
    assert "BV-LAJ" not in (result.error or "")


@pytest.mark.asyncio
async def test_a_day_of_unverifiable_orders_is_flagged_not_blessed(nexus_and_db):
    """An import-only day exports vouchers the validator cannot cross-check.
    It must NOT download as a clean green file."""
    agent, db = nexus_and_db
    opaque = _raw_order(order_id="ORD-OPAQUE-DAY", with_items=False)
    db.get_collection("orders").insert_one(opaque)

    await agent._build_tally_export()

    row = db.get_collection("tally_exports").docs[0]
    assert row["unverified_count"] == 1
    assert row["balanced"] is False, "nothing verified must not read as verified"
