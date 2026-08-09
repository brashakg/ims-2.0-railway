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
    xml, priced = tally_build_day_voucher_xml_checked(None, [order])
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


def test_intrastate_voucher_balances():
    xml, _ = tally_build_day_voucher_xml_checked(None, [_raw_order()])
    legs = _ledger_amounts(xml)
    assert round(sum(legs.values()), 2) == 0.00, "debits must equal credits"


# ===========================================================================
# 3. Inter-state: the whole tax goes to IGST
# ===========================================================================


def test_interstate_order_books_igst_not_cgst_sgst():
    """OS-008: the order's own `interstate` flag decides the GST head."""
    order = _raw_order(order_id="ORD-INTER", interstate=True)
    xml, _ = tally_build_day_voucher_xml_checked(None, [order])
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
    xml, _ = tally_build_day_voucher_xml_checked(None, [order])
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
    xml, _ = tally_build_day_voucher_xml_checked(None, orders)

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
    legacy = _raw_order(order_id="ORD-LEGACY", with_items=False)
    report = validate_voucher_balance([legacy])
    assert report["mismatch_count"] == 0
    assert report["totals"]["unverified_count"] == 1


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


@pytest.fixture
def nexus_and_db(monkeypatch):
    from agents.implementations import nexus as nexus_module
    from api import dependencies as deps_module
    from database.repositories.store_repository import StoreRepository

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
    agent = nexus_module.NexusAgent(db=db)
    agent.get_collection = lambda name: db.get_collection(name)
    monkeypatch.setattr(
        deps_module,
        "get_store_repository",
        lambda: StoreRepository(db.get_collection("stores")),
    )
    return agent, db


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


@pytest.mark.asyncio
async def test_nightly_export_writes_NOTHING_when_the_gate_trips(nexus_and_db):
    """A voucher that would under-book GST must not reach `tally_exports`."""
    agent, db = nexus_and_db
    broken = _raw_order(order_id="ORD-NIGHTLY-BAD", tax_amount=0.0)
    broken["items"][0]["tax_amount"] = 180.0
    broken["items"][0]["taxable_value"] = 1000.0
    db.get_collection("orders").insert_one(broken)

    result = await agent._build_tally_export()

    assert result.ok is False, "the export must fail loudly"
    assert "ORD-NIGHTLY-BAD" in (result.error or "")
    assert (
        db.get_collection("tally_exports").docs == []
    ), "no downloadable/importable voucher may be written"
