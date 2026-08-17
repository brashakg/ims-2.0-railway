"""BUG-104: the IST calendar day on dates that LEAVE the system.

`created_at` is stored as a NAIVE `datetime.now()` == the UTC wall clock, and
Railway runs UTC. Any instant between 00:00 and 05:30 IST therefore carries the
PREVIOUS calendar day. Four outbound dates were reading that raw UTC day:

  1 the courier's order_date          (api/services/shiprocket.py)
  2 the Tally SALES voucher <DATE>    (agents/nexus_providers.py)
  3 the Tally RECEIPT voucher <DATE>  (api/services/tally_tender_receipt.py)
  4 the invoice date on the GST / Tally reconciliation row an accountant reads
                                       (api/routers/finance.py)

Every assertion here reads the RETURNED payload / emitted XML, never a log line.
Each shifted-window case is paired with a POSITIVE CONTROL (an ordinary
afternoon order whose date must come back UNCHANGED) -- without that pairing,
a hypothetical "add a day to everything" implementation would pass every case.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.utils.ist import ist_date_str  # noqa: E402
from api.services.shiprocket import build_shipment_payload  # noqa: E402
from api.services.tally_tender_receipt import (  # noqa: E402
    tally_build_tender_receipt_xml,
)
from agents.nexus_providers import tally_build_day_voucher_xml  # noqa: E402
from api.routers.finance import _b2b_invoice_row  # noqa: E402


# ---------------------------------------------------------------------------
# The two instants every case below is built from.
# ---------------------------------------------------------------------------
# 22:30 UTC on the 20th == 04:00 IST on the 21st -> the IST day is the 21st.
IN_WINDOW = datetime(2026, 8, 20, 22, 30, 0)
IN_WINDOW_IST_DAY = "2026-08-21"
IN_WINDOW_UTC_DAY = "2026-08-20"  # what the defect produced

# POSITIVE CONTROL: 11:00 UTC == 16:30 IST, same day in both frames.
AFTERNOON = datetime(2026, 8, 20, 11, 0, 0)
AFTERNOON_DAY = "2026-08-20"

# The financial-year hop: 20:00 UTC on 31-Mar == 01:30 IST on 1-Apr.
FY_EVE = datetime(2026, 3, 31, 20, 0, 0)
FY_EVE_IST_DAY = "2026-04-01"
FY_EVE_UTC_DAY = "2026-03-31"  # PRIOR financial year -- the worst outcome


# ===========================================================================
# 1. The helper itself
# ===========================================================================


def test_helper_shifts_the_0000_0530_ist_window_onto_the_next_day():
    assert ist_date_str(IN_WINDOW) == IN_WINDOW_IST_DAY


def test_helper_leaves_an_ordinary_afternoon_alone_positive_control():
    """Without this, 'add one day to everything' passes every other case."""
    assert ist_date_str(AFTERNOON) == AFTERNOON_DAY


def test_helper_boundary_pair_either_side_of_ist_midnight():
    """IST midnight is 18:30 UTC. One second either side must differ by a day."""
    before = datetime(2026, 8, 20, 18, 29, 59)
    after = datetime(2026, 8, 20, 18, 30, 0)
    assert ist_date_str(before) == "2026-08-20"
    assert ist_date_str(after) == "2026-08-21"


def test_helper_exact_ist_midnight_belongs_to_the_new_day():
    assert ist_date_str(datetime(2026, 8, 20, 18, 30, 0)) == "2026-08-21"


def test_helper_handles_the_financial_year_boundary():
    assert ist_date_str(FY_EVE) == FY_EVE_IST_DAY
    # positive control on the same boundary: an afternoon 31-Mar sale stays 31-Mar
    assert ist_date_str(datetime(2026, 3, 31, 11, 0, 0)) == "2026-03-31"


def test_helper_converts_an_aware_datetime_via_its_own_offset():
    aware = datetime(2026, 8, 20, 22, 30, 0, tzinfo=timezone.utc)
    assert ist_date_str(aware) == IN_WINDOW_IST_DAY


def test_helper_leaves_non_instant_shapes_unshifted():
    # A `date` carries no instant; a legacy ISO string carries no reliable frame.
    assert ist_date_str(date(2026, 8, 20)) == "2026-08-20"
    assert ist_date_str("2026-08-20T22:30:00") == "2026-08-20"
    assert ist_date_str(None) == ""
    assert ist_date_str(12345) == ""


def test_reports_router_uses_the_shared_helper_not_a_private_copy():
    """One definition. The private `_ist_date_str` in reports.py is gone."""
    from api.routers import reports as reports_mod

    assert not hasattr(reports_mod, "_ist_date_str")
    assert reports_mod.ist_date_str is ist_date_str


# ===========================================================================
# 2. Site 1 - the courier
# ===========================================================================


def _ship_order(created_at):
    return {
        "order_id": "ORD-IST-1",
        "order_number": "BV/26-27/0001",
        "created_at": created_at,
        "grand_total": 4500.0,
        "items": [{"product_name": "Frame", "sku": "SKU1", "quantity": 1,
                   "item_total": 4500.0}],
    }


def _ship_address():
    return {"name": "Test", "address": "1 Road", "city": "Ranchi",
            "pincode": "834001", "state": "Jharkhand", "phone": "9876543210"}


def test_shiprocket_order_date_is_the_ist_day_for_a_small_hours_order():
    payload = build_shipment_payload(_ship_order(IN_WINDOW), _ship_address())
    assert payload["order_date"] == IN_WINDOW_IST_DAY
    assert payload["order_date"] != IN_WINDOW_UTC_DAY


def test_shiprocket_order_date_unchanged_for_an_afternoon_order_positive_control():
    payload = build_shipment_payload(_ship_order(AFTERNOON), _ship_address())
    assert payload["order_date"] == AFTERNOON_DAY


def test_shiprocket_order_date_boundary_pair():
    a = build_shipment_payload(
        _ship_order(datetime(2026, 8, 20, 18, 29, 59)), _ship_address()
    )["order_date"]
    b = build_shipment_payload(
        _ship_order(datetime(2026, 8, 20, 18, 30, 0)), _ship_address()
    )["order_date"]
    assert (a, b) == ("2026-08-20", "2026-08-21")


# ===========================================================================
# 3. Site 2 - the Tally SALES voucher (a dated accounting document)
# ===========================================================================


def _sales_order(created_at, order_number="BV/26-27/0007"):
    return {
        "order_number": order_number,
        "created_at": created_at,
        "customer_name": "Walk-in Customer",
        "subtotal": 1000.0,
        "cgst_amount": 25.0,
        "sgst_amount": 25.0,
        "igst_amount": 0.0,
        "grand_total": 1050.0,
    }


def _voucher_dates(xml: str):
    """Every <DATE> in the emitted XML, in document order."""
    return re.findall(r"<DATE>(\d+)</DATE>", xml)


def test_tally_sales_voucher_date_is_the_ist_yyyymmdd():
    xml = tally_build_day_voucher_xml([_sales_order(IN_WINDOW)])
    assert _voucher_dates(xml) == ["20260821"]
    assert "20260820" not in xml


def test_tally_sales_voucher_date_unchanged_for_afternoon_positive_control():
    xml = tally_build_day_voucher_xml([_sales_order(AFTERNOON)])
    assert _voucher_dates(xml) == ["20260820"]


def test_tally_sales_voucher_boundary_pair_in_one_export():
    xml = tally_build_day_voucher_xml(
        [
            _sales_order(datetime(2026, 8, 20, 18, 29, 59), "BV/26-27/0008"),
            _sales_order(datetime(2026, 8, 20, 18, 30, 0), "BV/26-27/0009"),
        ]
    )
    assert _voucher_dates(xml) == ["20260820", "20260821"]


def test_tally_sales_voucher_1_april_does_not_book_into_the_prior_fy():
    """The worst outcome: production already holds one order in this window."""
    xml = tally_build_day_voucher_xml([_sales_order(FY_EVE)])
    assert _voucher_dates(xml) == ["20260401"]
    # 20260331 is FY 2025-26; 20260401 is FY 2026-27.
    assert "20260331" not in xml


def test_tally_sales_voucher_31_march_afternoon_stays_in_its_own_fy():
    """Positive control on the FY boundary -- the shift must not run backwards."""
    xml = tally_build_day_voucher_xml([_sales_order(datetime(2026, 3, 31, 11, 0))])
    assert _voucher_dates(xml) == ["20260331"]


def test_tally_sales_voucher_date_is_still_8_digits():
    """Convert BEFORE formatting: a shift applied after .replace('-','') would
    either fail or leave dashes in the yyyymmdd string Tally parses."""
    xml = tally_build_day_voucher_xml([_sales_order(IN_WINDOW)])
    (got,) = _voucher_dates(xml)
    assert len(got) == 8 and got.isdigit()
    assert "-" not in got


# ===========================================================================
# 4. Site 3 - the Tally RECEIPT voucher
# ===========================================================================


def _tender_order(created_at, order_id="ORD-RCPT-1"):
    return {
        "order_id": order_id,
        "created_at": created_at,
        "customer_name": "Walk-in Customer",
        "payments": [{"method": "CASH", "amount": 1050.0}],
    }


def test_tally_receipt_voucher_date_is_the_ist_yyyymmdd():
    xml = tally_build_tender_receipt_xml([_tender_order(IN_WINDOW)])
    assert _voucher_dates(xml) == ["20260821"]
    assert "20260820" not in xml


def test_tally_receipt_voucher_date_unchanged_for_afternoon_positive_control():
    xml = tally_build_tender_receipt_xml([_tender_order(AFTERNOON)])
    assert _voucher_dates(xml) == ["20260820"]


def test_tally_receipt_voucher_boundary_pair_in_one_export():
    xml = tally_build_tender_receipt_xml(
        [
            _tender_order(datetime(2026, 8, 20, 18, 29, 59), "ORD-RCPT-A"),
            _tender_order(datetime(2026, 8, 20, 18, 30, 0), "ORD-RCPT-B"),
        ]
    )
    assert _voucher_dates(xml) == ["20260820", "20260821"]


def test_tally_receipt_voucher_1_april_does_not_book_into_the_prior_fy():
    xml = tally_build_tender_receipt_xml([_tender_order(FY_EVE)])
    assert _voucher_dates(xml) == ["20260401"]
    assert "20260331" not in xml


def test_tally_receipt_voucher_date_is_still_8_digits():
    xml = tally_build_tender_receipt_xml([_tender_order(IN_WINDOW)])
    (got,) = _voucher_dates(xml)
    assert len(got) == 8 and got.isdigit()


# ===========================================================================
# 5. Site 4 - the accountant's GST / Tally reconciliation row
# ===========================================================================


_SPLIT = {
    "interstate": False,
    "place_of_supply": "20",
    "customer_gstin": "20AAAAA0000A1Z5",
    "totals": {"taxable": 1000.0, "cgst": 25.0, "sgst": 25.0, "igst": 0.0},
}
_CUST = {"name": "Acme Opticals", "gstin": "20AAAAA0000A1Z5"}
_NOW = datetime(2026, 8, 25, 6, 0, 0)


def _recon_row(created_at):
    order = {
        "order_id": "ORD-REC-1",
        "order_number": "BV/26-27/0011",
        "created_at": created_at,
        "grand_total": 1050.0,
    }
    return _b2b_invoice_row(order, _CUST, _SPLIT, _NOW)


def test_recon_row_date_is_the_ist_day_for_a_small_hours_invoice():
    assert _recon_row(IN_WINDOW)["date"] == IN_WINDOW_IST_DAY


def test_recon_row_date_unchanged_for_an_afternoon_invoice_positive_control():
    assert _recon_row(AFTERNOON)["date"] == AFTERNOON_DAY


def test_recon_row_boundary_pair():
    a = _recon_row(datetime(2026, 8, 20, 18, 29, 59))["date"]
    b = _recon_row(datetime(2026, 8, 20, 18, 30, 0))["date"]
    assert (a, b) == ("2026-08-20", "2026-08-21")


def test_recon_row_1_april_invoice_is_not_dated_into_the_prior_fy():
    assert _recon_row(FY_EVE)["date"] == FY_EVE_IST_DAY
    assert _recon_row(FY_EVE)["date"] != FY_EVE_UTC_DAY


def test_recon_row_age_days_is_NOT_shifted():
    """age_days measures elapsed time between two stored instants -- it must
    keep using the raw instant. A helper applied there too would be a bug."""
    row = _recon_row(_NOW - timedelta(days=3))
    assert row["age_days"] == 3


# ===========================================================================
# 6. The reports dashboard the helper was promoted OUT of must not regress
# ===========================================================================


def test_analytics_period_window_stays_in_the_stored_naive_frame():
    """Site 5 ruling: the new-vs-returning split is LEFT ALONE ON PURPOSE.

    Both sides of that comparison come from the box clock -- the stored
    `created_at` and the `start_date` bound this builds -- so the split is at
    least self-consistent. Wrapping only the STORED side in ``ist_date_str``
    would leave the bound on UTC midnight and the customers on IST midnight.
    This pins the FRAME (naive, box-clock), so anyone making the window
    IST-aware has to come here, read the note, and move the Mongo range bounds
    with it instead of shipping the half-fix.
    """
    from api.routers.analytics import get_date_range

    start, end = get_date_range("today")
    assert start.tzinfo is None and end.tzinfo is None
    # IST would be 19,800s away; a slow host is not.
    assert abs((end - datetime.now()).total_seconds()) < 120
    assert start == end.replace(hour=0, minute=0, second=0, microsecond=0)


def test_reports_dashboard_day_bucketing_still_ist():
    from api.routers.reports import ist_date_str as reports_helper

    assert reports_helper(IN_WINDOW) == IN_WINDOW_IST_DAY
    assert reports_helper(AFTERNOON) == AFTERNOON_DAY
    assert reports_helper("2026-08-20T22:30:00") == "2026-08-20"


# ===========================================================================
# 7. /reports/finance/gst -- the SAME requirement as the reconciliation row,
#    and it lived in the very file this change consolidated. `/dashboard` two
#    screens above already went through the helper; this row did not, so the
#    same file answered the same question two different ways.
# ===========================================================================

class _GstOrderRepo:
    def __init__(self, orders):
        self._orders = orders

    def find_many(self, _filter=None, limit=0, **_kw):
        # Deliberately ignores the created_at bounds: the subject here is the
        # DATE ON THE ROW, not the range filter. Returning the planted orders
        # unconditionally keeps a bound-vs-row mismatch from masking the very
        # thing under test.
        return list(self._orders)


def _gst_rows(created_at):
    """Drive the REAL gst_report and return its rows."""
    import asyncio

    from api.routers import reports as _reports

    order = {
        "order_id": "ZZ-GST-1",
        "order_number": "INV/ZZ/0001",
        "store_id": "ZZ-STORE",
        "customer_id": "ZZ-CUST",
        "created_at": created_at,
        "status": "COMPLETED",
        "grand_total": 1180.0,
        "tax_amount": 180.0,
    }

    real_repo = _reports.get_order_repository
    real_raw = _reports._get_raw_db
    _reports.get_order_repository = lambda: _GstOrderRepo([order])
    _reports._get_raw_db = lambda: None  # no store/customer state -> intra-state
    try:
        body = asyncio.run(
            _reports.gst_report(
                from_date=date(2020, 1, 1),
                to_date=date(2030, 1, 1),
                store_id=None,
                current_user={
                    "user_id": "u1",
                    "roles": ["ADMIN"],
                    "active_store_id": "ZZ-STORE",
                    "store_ids": ["ZZ-STORE"],
                },
            )
        )
    finally:
        _reports.get_order_repository = real_repo
        _reports._get_raw_db = real_raw
    return body["data"]


def test_gst_row_date_is_the_ist_day_for_a_small_hours_invoice():
    assert _gst_rows(IN_WINDOW)[0]["date"] == IN_WINDOW_IST_DAY


def test_gst_row_date_unchanged_for_an_afternoon_invoice_positive_control():
    assert _gst_rows(AFTERNOON)[0]["date"] == AFTERNOON_DAY


def test_gst_row_1_april_does_not_report_into_the_prior_fy():
    """The worst outcome on this surface: an invoice the accountant reads as
    belonging to the previous financial year. Production holds one order in
    exactly this shape."""
    assert _gst_rows(FY_EVE)[0]["date"] == FY_EVE_IST_DAY


def test_gst_row_boundary_pair_either_side_of_ist_midnight():
    a = _gst_rows(datetime(2026, 8, 20, 18, 29, 59))[0]["date"]
    b = _gst_rows(datetime(2026, 8, 20, 18, 30, 0))[0]["date"]
    assert (a, b) == ("2026-08-20", "2026-08-21")
