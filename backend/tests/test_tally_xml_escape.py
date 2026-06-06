"""
Test XML escaping in Tally sales JV builder (BUG-141).

Verifies that customer_name, order_id, and store metadata are properly
XML-escaped to prevent injection of &, <, > characters into the XML.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.nexus_providers import tally_build_day_voucher_xml  # noqa: E402


def test_xml_escape_customer_name_with_ampersand():
    """Customer name with & should be escaped to &amp;."""
    orders = [
        {
            "order_id": "O1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "Smith & Company",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    xml = tally_build_day_voucher_xml(orders)
    # Party name should appear in PARTYLEDGERNAME and LEDGERNAME
    assert "<PARTYLEDGERNAME>Smith &amp; Company</PARTYLEDGERNAME>" in xml
    assert "<LEDGERNAME>Smith &amp; Company</LEDGERNAME>" in xml
    # Ensure raw & is not present in the XML tags
    assert "<PARTYLEDGERNAME>Smith & Company</PARTYLEDGERNAME>" not in xml


def test_xml_escape_customer_name_with_less_than():
    """Customer name with < should be escaped to &lt;."""
    orders = [
        {
            "order_id": "O1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "Value <100",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    xml = tally_build_day_voucher_xml(orders)
    assert "<PARTYLEDGERNAME>Value &lt;100</PARTYLEDGERNAME>" in xml
    assert "<LEDGERNAME>Value &lt;100</LEDGERNAME>" in xml
    assert "<PARTYLEDGERNAME>Value <100</PARTYLEDGERNAME>" not in xml


def test_xml_escape_customer_name_with_greater_than():
    """Customer name with > should be escaped to &gt;."""
    orders = [
        {
            "order_id": "O1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "Amount >500",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    xml = tally_build_day_voucher_xml(orders)
    assert "<PARTYLEDGERNAME>Amount &gt;500</PARTYLEDGERNAME>" in xml
    assert "<LEDGERNAME>Amount &gt;500</LEDGERNAME>" in xml
    assert "<PARTYLEDGERNAME>Amount >500</PARTYLEDGERNAME>" not in xml


def test_xml_escape_order_id_with_special_chars():
    """Order ID with special characters should be escaped in VOUCHERNUMBER."""
    orders = [
        {
            "order_id": "ORD&<>123",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "Test Customer",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    xml = tally_build_day_voucher_xml(orders)
    assert "<VOUCHERNUMBER>ORD&amp;&lt;&gt;123</VOUCHERNUMBER>" in xml
    assert "<VOUCHERNUMBER>ORD&<>123</VOUCHERNUMBER>" not in xml


def test_xml_escape_store_code_in_narration_and_costcentre():
    """Store code with special characters should be escaped in both NARRATION and COSTCENTRECATEGORY."""
    orders = [
        {
            "order_id": "O1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "Test Customer",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    store_meta = {
        "store_id": "BV-GK1",
        "store_code": "GK&1",
        "store_name": "Store <Main>",
    }
    xml = tally_build_day_voucher_xml(orders, store_meta=store_meta)
    # In narration: "GK&1 · Store <Main>" should become "GK&amp;1 · Store &lt;Main&gt;"
    assert "<NARRATION>GK&amp;1 · Store &lt;Main&gt;</NARRATION>" in xml
    assert "<COSTCENTRECATEGORY>GK&amp;1</COSTCENTRECATEGORY>" in xml
    # Ensure raw characters don't appear
    assert "<NARRATION>GK&1 · Store <Main></NARRATION>" not in xml
    assert "<COSTCENTRECATEGORY>GK&1</COSTCENTRECATEGORY>" not in xml


def test_xml_escape_walk_in_customer_default():
    """Walk-in Customer default value should also be escaped if it contained special chars."""
    orders = [
        {
            "order_id": "O1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": None,  # This triggers the "Walk-in Customer" default
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    xml = tally_build_day_voucher_xml(orders)
    # The default "Walk-in Customer" should be present and properly escaped
    assert "<PARTYLEDGERNAME>Walk-in Customer</PARTYLEDGERNAME>" in xml
    assert "<LEDGERNAME>Walk-in Customer</LEDGERNAME>" in xml


def test_xml_escape_combined_special_chars():
    """All special characters & < > in one customer name."""
    orders = [
        {
            "order_id": "O1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "A&B<C>D",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    xml = tally_build_day_voucher_xml(orders)
    assert "<PARTYLEDGERNAME>A&amp;B&lt;C&gt;D</PARTYLEDGERNAME>" in xml
    assert "<LEDGERNAME>A&amp;B&lt;C&gt;D</LEDGERNAME>" in xml


def test_xml_well_formed_after_escaping():
    """Escaped XML should remain well-formed — no tag structure corruption."""
    orders = [
        {
            "order_id": "O&1",
            "store_id": "BV-GK1",
            "status": "COMPLETED",
            "created_at": "2026-05-08T10:00:00+00:00",
            "customer_name": "Smith & Sons <Ltd>",
            "grand_total": 118.0,
            "taxable": 100.0,
            "tax": 18.0,
            "subtotal": 100.0,
            "total_discount": 0.0,
            "cgst_amount": 9.0,
            "sgst_amount": 9.0,
        }
    ]
    store_meta = {
        "store_id": "BV-GK1",
        "store_code": "GK&1",
        "store_name": "Main <Store>",
    }
    xml = tally_build_day_voucher_xml(orders, store_meta=store_meta)
    # Basic XML structure should be intact
    assert "<ENVELOPE>" in xml
    assert "<VOUCHER VCHTYPE=\"Sales\"" in xml
    assert "<PARTYLEDGERNAME>" in xml and "</PARTYLEDGERNAME>" in xml
    assert "<COSTCENTRECATEGORY>" in xml and "</COSTCENTRECATEGORY>" in xml
    assert "<NARRATION>" in xml and "</NARRATION>" in xml
    # The real well-formedness check: the whole document must parse. (Counting
    # "<VOUCHER" by substring is wrong -- it also matches <VOUCHERTYPENAME> etc.)
    import xml.etree.ElementTree as ET

    ET.fromstring(xml)  # raises ParseError if escaping corrupted the structure
    assert xml.count("<VOUCHER ") == xml.count("</VOUCHER>")
    assert xml.count("<NARRATION>") == xml.count("</NARRATION>")
