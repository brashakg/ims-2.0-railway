"""
IMS 2.0 - Bill round off in the accountant's books (owner ruling 2026-10-08)
============================================================================
A rounded bill stores the rupee total in `grand_total` and the adjustment in
`round_off`. In Tally the round off is its OWN ledger leg: the party owes the
rounded total, Sales A/c carries the taxable value (never the round off), the
GST heads carry the tax, and "Round Off" carries the paise -- so every voucher
still balances to the paisa and Sales still ties to GSTR-1.

Anchor: a 5% frame at Rs 1,000.50 pays Rs 1,001.00 -- taxable 952.86, GST
47.64 (CGST 23.82 + SGST 23.82), round off +0.50. At Rs 1,000.49 it pays
Rs 1,000.00 -- taxable 952.85, GST 47.64, round off -0.49.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-key-bill-round-off-tally")

from agents.nexus_providers import (  # noqa: E402
    tally_build_day_voucher_xml,
    tally_build_day_voucher_xml_checked,
    validate_voucher_balance,
)
from tests.test_e5_tally_jv_wiring import (  # noqa: E402,F401  (fixtures)
    SALES_JV,
    FakeDB,
    _hdr,
    _pin_db_and_policy,
    _vouchers,
    client,
    db,
    flag,
)
from tests.test_nexus_tally_gst_reshape import _ledger_amounts  # noqa: E402


def _rounded_order(order_id="ORD-RO", *, up=True, **over):
    if up:
        gross, grand, ro, taxable = 1000.50, 1001.0, 0.50, 952.86
    else:
        gross, grand, ro, taxable = 1000.49, 1000.0, -0.49, 952.85
    doc = {
        "order_id": order_id,
        "store_id": "BV-GK1",
        "status": "COMPLETED",
        "created_at": datetime(2026, 6, 8, 10, 0, 0),
        "customer_name": "Test Customer",
        "customer_id": "CUST-1",
        "subtotal": gross,
        "tax_rate": 5.0,
        "tax_amount": 47.64,
        "grand_total": grand,
        "round_off": ro,
        "pricing_model": "inclusive",
        "items": [
            {"item_id": "L1", "item_total": gross, "gst_rate": 5.0,
             "taxable_value": taxable, "tax_amount": 47.64},
        ],
    }
    doc.update(over)
    return doc


@pytest.mark.parametrize("up", [True, False])
def test_nightly_voucher_books_round_off_on_its_own_leg(up):
    order = _rounded_order(up=up)
    xml, priced, rejected = tally_build_day_voucher_xml_checked(None, [order])
    assert rejected == []
    legs = _ledger_amounts(xml)
    assert legs["Test Customer"] == -order["grand_total"]
    assert legs["Sales A/c"] == order["items"][0]["taxable_value"]
    assert round(legs["CGST Output"] + legs["SGST Output"], 2) == 47.64
    assert legs["Round Off"] == order["round_off"]
    assert round(sum(legs.values()), 2) == 0.0


def test_round_off_leg_sign_follows_debit_and_credit():
    import xml.etree.ElementTree as ET

    up = tally_build_day_voucher_xml([dict(_rounded_order(up=True), subtotal=952.86)])
    down = tally_build_day_voucher_xml([dict(_rounded_order(up=False), subtotal=952.85)])

    def _deemed(xml):
        for e in ET.fromstring(xml).iter("ALLLEDGERENTRIES.LIST"):
            if e.findtext("LEDGERNAME") == "Round Off":
                return e.findtext("ISDEEMEDPOSITIVE")
        return None

    assert _deemed(up) == "No"     # extra paise collected -> a credit
    assert _deemed(down) == "Yes"  # paise given up -> a debit


def test_whole_bill_voucher_has_no_round_off_leg():
    order = _rounded_order(grand_total=1000.50, round_off=0.0)
    xml, _p, rejected = tally_build_day_voucher_xml_checked(None, [order])
    assert rejected == []
    assert "Round Off" not in _ledger_amounts(xml)


def test_voucher_balance_check_reads_the_value_before_round_off():
    """+0.50 is the largest round off there is; the 50-paise identity check
    must not flag it, and a day of them must not trip the batch check."""
    day = [_rounded_order(order_id=f"ORD-{i}") for i in range(5)]
    report = validate_voucher_balance(day)
    assert report["mismatch_count"] == 0, report["mismatches"]
    assert report["batch_ok"] is True
    assert report["ok"] is True


def test_sales_jv_download_books_round_off_leg(client, db, flag):
    db.get_collection("orders").insert_one(_rounded_order())
    r = client.get(SALES_JV, params={"store_id": "BV-GK1"}, headers=_hdr())
    assert r.status_code == 200, r.text
    v = _vouchers(r.text)[0]
    legs = {leg["ledger"]: leg["amount"] for leg in v["legs"]}
    assert legs["Sales A/c"] == 952.86
    assert legs["Round Off"] == 0.50
    assert round(sum(legs.values()), 2) == 0.0


def test_b2b_tally_voucher_balances_with_round_off(db):
    from api.routers.finance.tally import _b2b_fetch_orders

    db.get_collection("customers").insert_one(
        {"customer_id": "CUST-1", "customer_type": "B2B", "gstin": "20ABCDE1234F1Z5",
         "state": "Jharkhand", "name": "Acme Optics"}
    )
    db.get_collection("orders").insert_one(_rounded_order(up=False))
    orders = _b2b_fetch_orders(db, ["ORD-RO"])
    assert orders, "the B2B order was not fetched"
    legs = _ledger_amounts(tally_build_day_voucher_xml(orders))
    assert legs["Sales A/c"] == 952.85
    assert legs["Round Off"] == -0.49
    assert round(sum(legs.values()), 2) == 0.0


class _ProjectingDB(FakeDB):
    """FakeDB whose find() honours an INCLUSION projection, like Mongo does --
    so a reader that forgets to ask for `round_off` really does not get it."""

    def get_collection(self, name):
        coll = super().get_collection(name)
        if getattr(coll, "_projecting", False):
            return coll
        plain_find = coll.find

        def find(query=None, projection=None):
            rows = list(plain_find(query, projection))
            keep = [k for k, v in (projection or {}).items() if v and k != "_id"]
            if keep:
                rows = [{k: r[k] for k in keep if k in r} for r in rows]
            return iter(rows)

        coll.find = find
        coll._projecting = True
        return coll


def test_gst_cross_check_books_taxable_excludes_round_off():
    from api.routers.finance.gst_crosscheck import _books_and_tally_for_stores

    pdb = _ProjectingDB()
    pdb.get_collection("orders").insert_one(_rounded_order(order_id="A", up=True))
    pdb.get_collection("orders").insert_one(_rounded_order(order_id="B", up=False))
    books, tally = _books_and_tally_for_stores(
        pdb, None, datetime(2026, 6, 1), datetime(2026, 7, 1)
    )
    assert books["sales_grand_total"] == 2001.0
    assert books["sales_taxable"] == round(952.86 + 952.85, 2)
    assert tally["taxable"] == round(952.86 + 952.85, 2)
