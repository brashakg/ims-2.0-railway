"""
Reverse charge (RCM) on a purchase bill, end to end (owner 2026-10-08).

Some shops buy under reverse charge. #1167 took the form's tick away because
accounts payable booked the GST into the vendor payable: a Rs 1000 @ 18% RCM
bill said the supplier was owed Rs 1180, when the Rs 180 is the shop's OWN
liability to the government (GSTR-3B Table 3.1(d)).

The rules pinned here, each through the real doors and readers:
  * the supplier is owed the TAXABLE value only -- total_amount, outstanding,
    the vendor ledger, AP aging, payments, the cash-flow payables and the
    supplier balance all read 1000, through ONE helper (ap_engine.
    vendor_payable), so none can add the RCM tax back;
  * the GST is the shop's RCM liability: GSTR-3B 3.1(d) 180, and the portal
    upload carries it (it was hard-wired to zero);
  * credit on it follows THE credit rule (org_validation.itc_claimable):
    claimable under RCM (also from a supplier with no GSTIN), never when the
    user switched credit off (a blocked category), and it sits in Table
    4(A)(3) "inward supplies liable to reverse charge" on the upload;
  * the preview and the booking agree; a normal bill is unchanged; a mixed
    history of RCM and normal bills adds up.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_purchase_reverse_charge.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import finance  # noqa: E402
from api.routers import purchase_invoices as pi_router  # noqa: E402
from api.routers import reports  # noqa: E402
from api.routers import vendors as vend  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import cash_flow, gst as gst_mod, receivables  # noqa: E402
from api.routers.finance.gst_crosscheck import _run_gst_cross_check  # noqa: E402
from api.routers.vendors import performance  # noqa: E402
from api.services import ap_engine  # noqa: E402
from api.services.gstn_export import to_gstr3b_json  # noqa: E402
from api.utils.ist import now_ist  # noqa: E402
from test_purchase_invoice import BUY_JH, SUP_MH, _restore_router  # noqa: E402,F401

_PI = "/api/v1/vendors/purchase-invoices"
_ADMIN = {"user_id": "u1", "roles": ["ACCOUNTANT"], "store_ids": ["S1"], "active_store_id": "S1"}


class _Repo:
    def __init__(self, docs, key):
        self._docs = {d[key]: d for d in docs}

    def find_by_id(self, _id):
        d = self._docs.get(_id)
        return dict(d) if d else None


@pytest.fixture(autouse=True)
def _restore_globals():
    saved = (
        vend._get_db,
        vend.get_vendor_repository,
        vend.get_grn_repository,
        finance._get_db,
        reports._get_raw_db,
    )
    yield
    (
        vend._get_db,
        vend.get_vendor_repository,
        vend.get_grn_repository,
        finance._get_db,
        reports._get_raw_db,
    ) = saved


def _world():
    """Better Vision (E1, Jharkhand 20...) with its Dhanbad shop S1; V1 is a
    Maharashtra supplier (IGST), V3 a supplier with no GSTIN on file."""
    db = mongomock.MongoClient().db
    db["entities"].insert_one(
        {"entity_id": "E1", "name": "Better Vision",
         "gstins": [{"gstin": BUY_JH, "state_code": "20", "is_primary": True}]}
    )
    db["stores"].insert_one({"store_id": "S1", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH})
    db["vendors"].insert_many(
        [
            {"vendor_id": "V1", "trade_name": "GTA Freight", "gstin": SUP_MH, "credit_days": 30},
            {"vendor_id": "V3", "trade_name": "Local Advocate", "gstin": "", "credit_days": 30},
        ]
    )
    vendors = _Repo(list(db["vendors"].find({}, {"_id": 0})), "vendor_id")
    app = FastAPI()
    app.include_router(pi_router.router, prefix=_PI)
    app.include_router(vend.router, prefix="/api/v1/vendors")

    async def _u():
        return dict(_ADMIN)

    app.dependency_overrides[get_current_user] = _u
    pi_router._get_db = lambda: db
    pi_router.get_vendor_repository = lambda: vendors
    pi_router.get_purchase_order_repository = lambda: None
    pi_router.get_grn_repository = lambda: None
    pi_router.get_audit_repository = lambda: None
    vend._get_db = lambda: db
    vend.get_vendor_repository = lambda: vendors
    vend.get_grn_repository = lambda: None
    finance._get_db = lambda: db
    reports._get_raw_db = lambda: db
    return db, TestClient(app)


def _bill(number, *, rcm, vendor="V1", credit=True, date="2026-05-03"):
    """Freight Rs 1000 @ 18%, as the form books it (a services bill)."""
    return {
        "vendor_id": vendor,
        "invoice_number": number,
        "invoice_date": date,
        "bill_kind": "SERVICES",
        "store_id": "S1",
        "reverse_charge": rcm,
        "itc_eligible": credit,
        "lines": [{"description": "Freight", "qty": 1, "unit_price": 1000, "gst_rate": 18}],
    }


def _book(cli, body):
    r = cli.post(_PI, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _pay(cli, bill, amount, vendor="V1"):
    r = cli.post(
        f"/api/v1/vendors/{vendor}/payments",
        json={"amount": amount, "payment_date": "2026-05-20", "bill_id": bill["bill_id"]},
    )
    assert r.status_code == 201, r.text


def _aging(cli):
    r = cli.get("/api/v1/vendors/ap-aging")
    assert r.status_code == 200, r.text
    return r.json()


def _ledger(cli, vendor="V1"):
    r = cli.get(f"/api/v1/vendors/{vendor}/ledger")
    assert r.status_code == 200, r.text
    return r.json()


def _supplier_balance(vendor="V1"):
    rows = asyncio.run(receivables.get_vendor_payments(current_user=dict(_ADMIN)))
    return next(r for r in rows if r["vendor_id"] == vendor)["balance"]


def _payables():
    out = asyncio.run(cash_flow.owner_dashboard(current_user={**_ADMIN, "roles": ["ADMIN"]}))
    return out["payables"]["total"]


def _gstr3b():
    return reports._compute_gstr3b("2026-05", "S1")


# ===========================================================================
# The one helper
# ===========================================================================


class TestVendorPayableHelper:
    def test_rcm_bill_owes_the_taxable_value(self):
        bill = {"reverse_charge": True, "taxable_amount": 1000, "tax_amount": 180, "total_amount": 1000}
        assert ap_engine.vendor_payable(bill) == 1000.0

    def test_a_legacy_rcm_bill_stored_with_the_gst_still_owes_the_taxable_value(self):
        """Booked before this fix: total_amount 1180 on an RCM bill."""
        bill = {"reverse_charge": True, "taxable_amount": 1000, "tax_amount": 180, "total_amount": 1180}
        assert ap_engine.vendor_payable(bill) == 1000.0

    def test_a_normal_bill_owes_its_total(self):
        assert ap_engine.vendor_payable({"taxable_amount": 1000, "total_amount": 1180}) == 1180.0
        assert ap_engine.vendor_payable({"reverse_charge": False, "taxable_amount": 1000, "total_amount": 1180}) == 1180.0

    def test_only_a_true_flag_is_reverse_charge(self):
        """GSTR-3B 3.1(d) counts reverse_charge == True only; the payable reads
        the flag the same way, so a stray 'yes' is never half-RCM."""
        assert ap_engine.vendor_payable({"reverse_charge": "yes", "taxable_amount": 1000, "total_amount": 1180}) == 1180.0

    def test_ledger_aging_and_outstanding_read_it(self):
        legacy = {"bill_id": "B1", "vendor_id": "V1", "bill_date": "2026-05-03", "due_date": "2026-06-02",
                  "reverse_charge": True, "taxable_amount": 1000, "tax_amount": 180, "total_amount": 1180}
        assert ap_engine.bill_outstanding(legacy, [], []) == 1000.0
        assert ap_engine.build_ledger([legacy], [], [])["closing_balance"] == 1000.0
        aging = ap_engine.build_aging([legacy], [], [], "2026-05-10")
        assert aging["total_outstanding"] == 1000.0
        assert aging["items"][0]["total_amount"] == 1000.0


# ===========================================================================
# An RCM bill of Rs 1000 @ 18%
# ===========================================================================


class TestReverseChargeBill:
    def test_booking_owes_the_supplier_1000_and_records_the_180(self):
        db, cli = _world()
        doc = _book(cli, _bill("GTA-1", rcm=True))
        assert doc["reverse_charge"] is True
        assert (doc["taxable_amount"], doc["tax_amount"], doc["igst_total"]) == (1000.0, 180.0, 180.0)
        assert doc["total_amount"] == 1000.0
        assert doc["total"] == 1000.0
        assert doc["outstanding"] == 1000.0
        assert doc["itc_eligible"] is True
        stored = db["vendor_bills"].find_one({"bill_id": doc["bill_id"]})
        assert stored["total_amount"] == 1000.0 and stored["outstanding"] == 1000.0

    def test_preview_and_booking_agree(self):
        _db, cli = _world()
        r = cli.post(f"{_PI}/preview", json=_bill("GTA-1", rcm=True))
        assert r.status_code == 200, r.text
        pv = r.json()
        doc = _book(cli, _bill("GTA-1", rcm=True))
        assert pv["reverse_charge"] is True
        assert pv["total"] == doc["total_amount"] == 1000.0
        assert pv["tax_total"] == doc["tax_amount"] == 180.0
        assert pv["itc_eligible"] is doc["itc_eligible"] is True
        for k in ("igst_total", "cgst_total", "sgst_total", "taxable_total"):
            assert pv[k] == doc[k], k

    def test_ap_aging_ledger_supplier_balance_and_payables_read_1000(self):
        _db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        aging = _aging(cli)
        assert aging["totals"]["total_outstanding"] == 1000.0
        led = _ledger(cli)
        assert led["ledger"]["total_billed"] == 1000.0
        assert led["ledger"]["closing_balance"] == 1000.0
        assert led["aging"]["total_outstanding"] == 1000.0
        assert _supplier_balance() == 1000.0
        assert _payables() == 1000.0

    def test_paying_1000_settles_it(self):
        db, cli = _world()
        doc = _book(cli, _bill("GTA-1", rcm=True))
        _pay(cli, doc, 400)
        stored = db["vendor_bills"].find_one({"bill_id": doc["bill_id"]})
        assert (stored["status"], stored["outstanding"]) == ("PARTIAL", 600.0)
        assert _aging(cli)["totals"]["total_outstanding"] == 600.0
        _pay(cli, doc, 600)
        stored = db["vendor_bills"].find_one({"bill_id": doc["bill_id"]})
        assert (stored["status"], stored["outstanding"]) == ("PAID", 0.0)
        assert _aging(cli)["totals"]["total_outstanding"] == 0.0
        assert _ledger(cli)["ledger"]["closing_balance"] == 0.0

    def test_gstr3b_carries_the_180_as_rcm_liability_and_as_credit(self):
        _db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        g = _gstr3b()
        assert g["inwardSuppliesReverseChargeValue"] == 1000.0
        assert g["inwardSuppliesReverseCharge"]["integratedTax"] == 180.0
        assert g["itcAvailable"]["integratedTax"] == 180.0
        assert g["itcReverseCharge"]["integratedTax"] == 180.0
        # RCM is paid in cash, whatever credit there is.
        assert g["taxPaidCash"]["integratedTax"] == 180.0

    def test_the_portal_upload_carries_3_1_d_and_table_4_a_3(self):
        _db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        out = to_gstr3b_json(_gstr3b())
        rev = out["sup_details"]["isup_rev"]
        assert (rev["txval"], rev["iamt"], rev["camt"], rev["samt"]) == (1000.0, 180.0, 0.0, 0.0)
        avl = {row["ty"]: row for row in out["itc_elg"]["itc_avl"]}
        assert avl["ISRC"]["iamt"] == 180.0
        assert avl["OTH"]["iamt"] == 0.0
        assert out["itc_elg"]["itc_net"]["iamt"] == 180.0

    def test_cross_check_books_it_once(self):
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        xc = _run_gst_cross_check(db, 5, 2026, "E1")
        assert xc["gstr3b"]["rcm"]["total"] == 180.0
        assert xc["gstr3b"]["itc"]["total"] == 180.0

    def test_credit_switched_off_keeps_the_liability_and_claims_nothing(self):
        """A blocked category: the user switches credit off. The 180 is still
        owed to the government; none of it is credit."""
        _db, cli = _world()
        doc = _book(cli, _bill("GTA-1", rcm=True, credit=False))
        assert doc["itc_eligible"] is False
        assert doc["total_amount"] == 1000.0
        g = _gstr3b()
        assert g["inwardSuppliesReverseCharge"]["integratedTax"] == 180.0
        assert g["itcAvailable"]["integratedTax"] == 0.0
        assert g["itcReverseCharge"]["integratedTax"] == 0.0

    def test_a_supplier_with_no_gstin_may_be_reverse_charge(self):
        """The rule allows it: the buyer pays the tax and may claim it."""
        db, cli = _world()
        rcm = _book(cli, _bill("ADV-1", rcm=True, vendor="V3"))
        normal = _book(cli, _bill("ADV-2", rcm=False, vendor="V3"))
        assert (rcm["itc_eligible"], rcm["total_amount"]) == (True, 1000.0)
        assert (normal["itc_eligible"], normal["total_amount"]) == (False, 1180.0)
        xc = _run_gst_cross_check(db, 5, 2026, "E1")
        row = next(c for c in xc["comparisons"] if c["metric"] == "Input credit from suppliers with no GSTIN")
        assert row["status"] == "MATCH"


# ===========================================================================
# A normal bill is unchanged; a mixed history adds up
# ===========================================================================


class TestNormalAndMixed:
    def test_a_normal_bill_is_unchanged(self):
        _db, cli = _world()
        doc = _book(cli, _bill("FR-1", rcm=False))
        assert doc["reverse_charge"] is False
        assert (doc["total_amount"], doc["total"], doc["outstanding"]) == (1180.0, 1180.0, 1180.0)
        assert _aging(cli)["totals"]["total_outstanding"] == 1180.0
        assert _ledger(cli)["ledger"]["closing_balance"] == 1180.0
        g = _gstr3b()
        assert g["inwardSuppliesReverseCharge"]["integratedTax"] == 0.0
        assert g["itcReverseCharge"]["integratedTax"] == 0.0
        assert g["itcAvailable"]["integratedTax"] == 180.0
        avl = {row["ty"]: row for row in to_gstr3b_json(g)["itc_elg"]["itc_avl"]}
        assert (avl["OTH"]["iamt"], avl["ISRC"]["iamt"]) == (180.0, 0.0)

    def test_a_mixed_history_of_rcm_and_normal_bills(self):
        db, cli = _world()
        rcm = _book(cli, _bill("GTA-1", rcm=True))
        normal = _book(cli, _bill("FR-1", rcm=False))
        # A legacy RCM bill booked before this fix, with the GST in its total.
        db["vendor_bills"].insert_one(
            {"bill_id": "LEG-1", "vendor_id": "V1", "bill_number": "GTA-0", "bill_date": "2026-05-02",
             "invoice_date": "2026-05-02", "due_date": "2026-06-01", "reverse_charge": True,
             "itc_eligible": True, "recipient_entity_id": "E1", "recipient_gstin": BUY_JH,
             "taxable_amount": 1000.0, "tax_amount": 180.0, "igst_total": 180.0, "cgst_total": 0.0,
             "sgst_total": 0.0, "total_amount": 1180.0, "outstanding": 1180.0, "status": "OUTSTANDING"}
        )
        assert _aging(cli)["totals"]["total_outstanding"] == 3180.0
        assert _ledger(cli)["ledger"]["total_billed"] == 3180.0
        assert _supplier_balance() == 3180.0
        assert _payables() == 3180.0

        _pay(cli, rcm, 1000)
        _pay(cli, normal, 1180)
        _pay(cli, {"bill_id": "LEG-1"}, 1000)
        for bid in (rcm["bill_id"], normal["bill_id"], "LEG-1"):
            assert db["vendor_bills"].find_one({"bill_id": bid})["status"] == "PAID", bid
        assert _aging(cli)["totals"]["total_outstanding"] == 0.0
        assert _ledger(cli)["ledger"]["closing_balance"] == 0.0

        g = _gstr3b()
        assert g["inwardSuppliesReverseCharge"]["integratedTax"] == 360.0
        assert g["inwardSuppliesReverseChargeValue"] == 2000.0
        assert g["itcAvailable"]["integratedTax"] == 540.0
        assert g["itcReverseCharge"]["integratedTax"] == 360.0

    def test_the_invoice_list_shows_what_the_supplier_is_owed(self):
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        db["vendor_bills"].insert_one(
            {"bill_id": "LEG-1", "vendor_id": "V1", "bill_number": "GTA-0", "bill_date": "2026-05-02",
             "reverse_charge": True, "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1180.0}
        )
        rows = cli.get(_PI).json()["purchase_invoices"]
        assert sorted(r["total_amount"] for r in rows) == [1000.0, 1000.0]
        assert cli.get(f"{_PI}/LEG-1").json()["total_amount"] == 1000.0


# ===========================================================================
# The other readers of a bill's money
# ===========================================================================


class TestOtherReaders:
    def test_vendor_month_spend_reads_the_payable(self):
        db, _cli = _world()
        month = now_ist().strftime("%Y-%m")
        db["vendor_bills"].insert_many(
            [
                {"bill_id": "R", "vendor_id": "V1", "bill_date": f"{month}-01", "reverse_charge": True,
                 "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1180.0},
                {"bill_id": "N", "vendor_id": "V1", "bill_date": f"{month}-01",
                 "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1180.0},
            ]
        )
        assert performance._vendor_mtd_spend(db, "V1") == 2180.0

    def test_gst_summary_adds_the_rcm_liability_to_what_is_payable(self):
        """The summary took the 180 as credit and never owed it: net payable
        read 180 short."""
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        out = asyncio.run(gst_mod.get_gst_summary(month=5, year=2026, current_user=dict(_ADMIN)))
        assert out["gst_input_credit"] == 180.0
        assert out["reverse_charge_tax"] == 180.0
        assert out["net_gst_payable"] == 0.0

    def test_gst_summary_owes_nothing_on_a_cancelled_rcm_bill(self):
        db, _cli = _world()
        db["vendor_bills"].insert_one(
            {"bill_id": "X", "vendor_id": "V1", "bill_date": "2026-05-04", "status": "CANCELLED",
             "reverse_charge": True, "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1000.0}
        )
        out = asyncio.run(gst_mod.get_gst_summary(month=5, year=2026, current_user=dict(_ADMIN)))
        assert out["reverse_charge_tax"] == 0.0
        assert out["net_gst_payable"] == 0.0

    def test_the_po_timeline_names_what_the_supplier_is_owed(self):
        from api.routers.vendors import po_detail

        db, _cli = _world()
        db["vendor_bills"].insert_one(
            {"bill_id": "LEG-1", "doc_type": "PURCHASE_INVOICE", "po_id": "PO1", "vendor_id": "V1",
             "invoice_number": "GTA-0", "reverse_charge": True, "taxable_amount": 1000.0,
             "tax_amount": 180.0, "total_amount": 1180.0, "total": 1180.0}
        )
        saved = vend.get_purchase_order_repository
        vend.get_purchase_order_repository = lambda: _Repo(
            [{"po_id": "PO1", "po_number": "PO-1", "delivery_store_id": "S1", "vendor_id": "V1"}], "po_id"
        )
        try:
            out = asyncio.run(po_detail.get_po_timeline("PO1", current_user={**_ADMIN, "roles": ["ADMIN"]}))
        finally:
            vend.get_purchase_order_repository = saved
        assert [i["total"] for i in out["invoices"]] == [1000.0]

    def test_a_bill_with_nothing_paid_against_it_stays_outstanding(self):
        """The status rule compares what is left to what the supplier is
        owed: a legacy RCM bill (GST in total_amount) with nothing paid is
        OUTSTANDING at 1000, never PARTIAL against 1180."""
        from api.routers.vendors import ap_bills

        db, _cli = _world()
        db["vendor_bills"].insert_one(
            {"bill_id": "LEG-1", "vendor_id": "V1", "reverse_charge": True, "taxable_amount": 1000.0,
             "tax_amount": 180.0, "total_amount": 1180.0, "outstanding": 1180.0, "status": "OUTSTANDING"}
        )
        ap_bills._recompute_bill_status(db, "LEG-1")
        stored = db["vendor_bills"].find_one({"bill_id": "LEG-1"})
        assert (stored["status"], stored["outstanding"]) == ("OUTSTANDING", 1000.0)
