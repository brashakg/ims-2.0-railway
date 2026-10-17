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
from datetime import datetime

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
from test_purchase_invoice import BUY_JH, BUY_MH, SUP_MH, _restore_router  # noqa: E402,F401

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


def _summary():
    return asyncio.run(gst_mod.get_gst_summary(month=5, year=2026, current_user=dict(_ADMIN)))


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

    def test_the_previewed_total_is_the_total_the_booking_accepts(self):
        """`total` means one thing: the preview's total, echoed back, books;
        the supplier's taxable+tax (1180) is refused, never booked at 1000."""
        _db, cli = _world()
        pv = cli.post(f"{_PI}/preview", json=_bill("GTA-1", rcm=True)).json()
        assert pv["total"] == 1000.0
        doc = _book(cli, {**_bill("GTA-1", rcm=True), "total": pv["total"]})
        assert doc["total_amount"] == 1000.0
        r = cli.post(_PI, json={**_bill("GTA-2", rcm=True), "total": 1180.0})
        assert r.status_code == 400, r.text
        assert "what the supplier is owed, 1000.0" in r.json()["detail"]
        # A normal bill's total stays taxable + tax.
        assert _book(cli, {**_bill("FR-1", rcm=False), "total": 1180.0})["total_amount"] == 1180.0
        assert cli.post(_PI, json={**_bill("FR-2", rcm=False), "total": 1000.0}).status_code == 400

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
        # The portal upload: 3.1(d) declares the liability (never the credit),
        # and Table 4(A)(3) / (A)(5) claim nothing (never the liability).
        out = to_gstr3b_json(g)
        assert out["sup_details"]["isup_rev"]["iamt"] == 180.0
        avl = {row["ty"]: row for row in out["itc_elg"]["itc_avl"]}
        assert (avl["ISRC"]["iamt"], avl["OTH"]["iamt"]) == (0.0, 0.0)

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
            {"bill_id": "LEG-1", "vendor_id": "V1", "store_id": "S1", "bill_number": "GTA-0", "bill_date": "2026-05-02",
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
            {"bill_id": "LEG-1", "vendor_id": "V1", "store_id": "S1", "bill_number": "GTA-0", "bill_date": "2026-05-02",
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
                {"bill_id": "R", "vendor_id": "V1", "store_id": "S1", "bill_date": f"{month}-01", "reverse_charge": True,
                 "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1180.0},
                {"bill_id": "N", "vendor_id": "V1", "store_id": "S1", "bill_date": f"{month}-01",
                 "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1180.0},
            ]
        )
        assert performance._vendor_mtd_spend(db, "V1", dict(_ADMIN)) == 2180.0

    def test_gst_summary_owes_the_rcm_tax_in_cash_whatever_the_credit(self):
        """No sales in May, one RCM freight bill of 1000 @ 18% with credit on.
        Credit can never set off reverse-charge tax: the 180 is paid in cash,
        the 180 of credit carries forward -- the figure GSTR-3B and the
        Cross-Check give for the same month (it read 0)."""
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        out = _summary()
        assert out["gst_input_credit"] == 180.0
        assert out["reverse_charge_tax"] == 180.0
        assert out["gst_input_credit_carried_forward"] == 180.0
        assert out["net_gst_payable"] == 180.0
        cash = _gstr3b()["taxPaidCash"]
        assert out["net_gst_payable"] == cash["integratedTax"] + cash["centralTax"] + cash["stateTax"]
        assert out["net_gst_payable"] == _run_gst_cross_check(db, 5, 2026, "E1")["summary"]["gst_payable"]

    def test_gst_summary_still_owes_the_rcm_tax_with_credit_switched_off(self):
        """A blocked category: credit off. The 180 is still owed, none of it
        is credit -- on the summary as on GSTR-3B."""
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True, credit=False))
        out = _summary()
        assert out["gst_input_credit"] == 0.0
        assert out["reverse_charge_tax"] == 180.0
        assert out["net_gst_payable"] == 180.0
        assert out["net_gst_payable"] == _run_gst_cross_check(db, 5, 2026, "E1")["summary"]["gst_payable"]

    def test_gst_summary_with_sales_adds_up(self):
        """Collected 1000; credit 360 (180 RCM + 180 normal); RCM tax 180:
        1000 - 360 + 180 = 820, and every line of it is on the summary."""
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))
        _book(cli, _bill("FR-1", rcm=False))
        db["orders"].insert_one(
            {"order_id": "O1", "store_id": "S1", "status": "COMPLETED", "tax_amount": 1000.0,
             "grand_total": 6000.0, "created_at": datetime(2026, 5, 10, 6, 0)}
        )
        out = _summary()
        assert (out["gst_collected"], out["gst_input_credit"], out["reverse_charge_tax"]) == (1000.0, 360.0, 180.0)
        assert out["gst_input_credit_carried_forward"] == 0.0
        assert out["net_gst_payable"] == 820.0

    def test_gst_summary_owes_nothing_on_a_cancelled_rcm_bill(self):
        db, _cli = _world()
        db["vendor_bills"].insert_one(
            {"bill_id": "X", "vendor_id": "V1", "bill_date": "2026-05-04", "status": "CANCELLED",
             "reverse_charge": True, "taxable_amount": 1000.0, "tax_amount": 180.0, "total_amount": 1000.0}
        )
        out = _summary()
        assert out["reverse_charge_tax"] == 0.0
        assert out["net_gst_payable"] == 0.0

    def test_gstr3b_payment_table_adds_up_on_the_owners_case(self):
        """No sales, one RCM freight bill, credit on. Table 6.1 must read:
        tax payable 0 + reverse charge 180 - credit used 0 = cash 180. The
        credit is NOT used (reverse-charge tax is never set off by it); it
        carries forward."""
        _db, _cli = _world()
        _book(_cli, _bill("GTA-1", rcm=True))
        g = _gstr3b()
        assert g["itcUtilized"] == {"integratedTax": 0.0, "centralTax": 0.0, "stateTax": 0.0, "cess": 0.0}
        for h in ("integratedTax", "centralTax", "stateTax"):
            assert (g["taxPayable"][h] + g["inwardSuppliesReverseCharge"][h] - g["itcUtilized"][h]
                    == g["taxPaidCash"][h])

    def _sales_month(self, *, rcm):
        """Rs 1000 GST on Jharkhand sales (CGST 500 + SGST 500) and Rs 180 of
        IGST credit from a Maharashtra supplier -- the plain month of a shop
        that buys out of state."""
        db, cli = _world()
        _book(cli, _bill("FR-1", rcm=rcm))
        db["orders"].insert_one(
            {"order_id": "O1", "store_id": "S1", "status": "COMPLETED", "tax_amount": 1000.0,
             "grand_total": 6000.0, "created_at": datetime(2026, 5, 10, 6, 0)}
        )
        return db

    def test_igst_credit_pays_cgst_and_sgst_on_every_gst_screen(self):
        """CGST Act s.49(5): IGST credit IGST does not need pays CGST and SGST.
        GSTR-3B used to set off head by head only, so it asked for Rs 1000 in
        cash where the law (and the Finance GST panel) owe Rs 820."""
        db = self._sales_month(rcm=False)
        g = _gstr3b()
        cash = g["taxPaidCash"]
        assert (cash["integratedTax"], cash["centralTax"], cash["stateTax"]) == (0.0, 320.0, 500.0)
        assert g["itcUtilized"]["centralTax"] == 180.0
        total = cash["integratedTax"] + cash["centralTax"] + cash["stateTax"]
        assert total == _summary()["net_gst_payable"] == 820.0
        xc = _run_gst_cross_check(db, 5, 2026, "E1")
        assert total == xc["summary"]["gst_payable"]
        # The Cross-Check's own recomputation agrees too (same rule, no copy).
        row = next(r for r in xc["comparisons"] if r["metric"] == "Net GST payable (cash)")
        assert row["status"] == "MATCH"

    def test_a_reverse_charge_sales_month_agrees_on_every_gst_screen(self):
        """The same month with the bill under reverse charge: 1000 - 180 of
        credit + 180 reverse charge in cash = 1000, on GSTR-3B, the Finance
        panel and the Cross-Check alike."""
        db = self._sales_month(rcm=True)
        cash = _gstr3b()["taxPaidCash"]
        total = cash["integratedTax"] + cash["centralTax"] + cash["stateTax"]
        assert total == _summary()["net_gst_payable"] == 1000.0
        assert total == _run_gst_cross_check(db, 5, 2026, "E1")["summary"]["gst_payable"]

    def test_the_finance_summary_sets_off_by_the_bills_own_heads(self):
        """CGST credit never pays SGST, on the Finance panel as on GSTR-3B:
        CGST credit 100 against sales of CGST 50 + SGST 50 leaves SGST 50 in
        cash (a plain total would read 0)."""
        db, _cli = _world()
        db["vendor_bills"].insert_one(
            {"bill_id": "C", "vendor_id": "V1", "store_id": "S1", "bill_date": "2026-05-04", "status": "RECEIVED",
             "taxable_amount": 1000.0, "tax_amount": 100.0, "cgst_total": 100.0, "sgst_total": 0.0}
        )
        db["orders"].insert_one(
            {"order_id": "O1", "store_id": "S1", "status": "COMPLETED", "tax_amount": 100.0,
             "grand_total": 1100.0, "created_at": datetime(2026, 5, 10, 6, 0)}
        )
        out = _summary()
        assert out["net_gst_payable"] == 50.0
        assert out["gst_input_credit_carried_forward"] == 50.0


def _receipt(db, subtype="STANDARD"):
    """An ACCEPTED receipt G1 at S1 from V1: one frame, Rs 1000 @ 18% on its
    PO (a "Bought without PO" receipt has none)."""
    db["products"].insert_one({"product_id": "P1", "name": "Frame X", "hsn_code": "9003"})
    grn = {"grn_id": "G1", "grn_number": "GRN-1", "grn_subtype": subtype, "vendor_id": "V1",
           "store_id": "S1", "status": "ACCEPTED", "vendor_invoice_no": "MLH-7",
           "vendor_invoice_date": "2026-05-03",
           "items": [{"product_id": "P1", "product_name": "Frame X", "accepted_qty": 1}]}
    if subtype != ap_engine.GRN_SUBTYPE_NO_PO:
        grn["po_id"] = "PO1"
    pi_router.get_grn_repository = lambda: _Repo([grn], "grn_id")
    pi_router.get_purchase_order_repository = lambda: _Repo(
        [{"po_id": "PO1", "vendor_id": "V1",
          "items": [{"product_id": "P1", "hsn": "9003", "unit_price": 1000.0, "tax_rate": 18.0}]}],
        "po_id",
    )
    return grn


class TestOneMeaningOfTotal:
    def test_the_draft_total_is_what_the_supplier_is_owed(self):
        """GET /from-grn's `total` means what POST / and /preview mean: what
        the supplier is owed. The draft is not reverse charge (1180, as the
        preview of the same form says); ticking it, the preview owes 1000."""
        db, cli = _world()
        _receipt(db)
        draft = cli.get(f"{_PI}/from-grn/G1").json()
        assert (draft["reverse_charge"], draft["taxable_total"], draft["total"]) == (False, 1000.0, 1180.0)
        form = {"vendor_id": "V1", "grn_id": "G1", "store_id": "S1", "lines": draft["lines"]}
        assert cli.post(f"{_PI}/preview", json=form).json()["total"] == draft["total"]
        assert cli.post(f"{_PI}/preview", json={**form, "reverse_charge": True}).json()["total"] == 1000.0


class TestBoughtWithoutPo:
    """Decided in #1183: a bill for goods bought without a purchase order is
    never reverse charge -- every bill door refuses it in plain words."""

    def _body(self, **over):
        return {"vendor_id": "V1", "invoice_number": "WALK-1", "invoice_date": "2026-05-03",
                "grn_id": "G1", "store_id": "S1",
                "lines": [{"description": "Frame X", "qty": 1,
                           "unit_price": 1000, "gst_rate": 18}], **over}

    def test_every_door_refuses_reverse_charge(self):
        db, cli = _world()
        _receipt(db, ap_engine.GRN_SUBTYPE_NO_PO)
        for url in (_PI, f"{_PI}/preview"):
            r = cli.post(url, json=self._body(reverse_charge=True))
            assert r.status_code == 400, (url, r.text)
            assert r.json()["detail"] == (
                "Goods bought without a purchase order are never reverse charge. "
                "Turn off 'Reverse charge' on this bill to book it."
            )
        assert db["vendor_bills"].count_documents({}) == 0

    def test_without_reverse_charge_it_books_and_a_po_receipt_may_be_reverse_charge(self):
        db, cli = _world()
        _receipt(db, ap_engine.GRN_SUBTYPE_NO_PO)
        assert cli.post(f"{_PI}/preview", json=self._body()).status_code == 200
        db2, cli2 = _world()
        _receipt(db2)
        r = cli2.post(f"{_PI}/preview", json=self._body(reverse_charge=True))
        assert (r.status_code, r.json().get("total")) == (200, 1000.0), r.text

    def test_the_rule_is_one_helper(self):
        no_po = {"grn_subtype": ap_engine.GRN_SUBTYPE_NO_PO}
        assert ap_engine.reverse_charge_refusal(True, no_po)
        assert ap_engine.reverse_charge_refusal(False, no_po) is None
        assert ap_engine.reverse_charge_refusal(True, {"grn_subtype": "STANDARD"}) is None
        assert ap_engine.reverse_charge_refusal(True, None) is None


class TestTwoRegistrations:
    """One company, two GSTINs (Jharkhand + Maharashtra). Each registration
    files its own GSTR-3B and sets off on it alone, so one GSTIN's spare IGST
    credit never pays the other GSTIN's CGST/SGST -- on the Cross-Check and
    the sign-off it stores as on the per-shop GSTR-3B screens."""

    def test_one_gstins_spare_igst_never_pays_the_other_gstins_tax(self):
        db, cli = _world()
        db["entities"].update_one(
            {"entity_id": "E1"},
            {"$push": {"gstins": {"gstin": BUY_MH, "state_code": "27", "is_primary": False}}},
        )
        db["stores"].insert_one({"store_id": "S2", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH})
        # S1 (Jharkhand) buys from a Maharashtra supplier: Rs 1000 of IGST credit.
        doc = _book(cli, {**_bill("FR-1", rcm=False),
                          "lines": [{"description": "Freight", "qty": 1, "unit_price": 20000, "gst_rate": 5}]})
        assert (doc["igst_total"], doc["recipient_gstin"]) == (1000.0, BUY_JH)
        # S2 (Pune) sells intra-state: CGST 500 + SGST 500.
        db["orders"].insert_one(
            {"order_id": "O1", "store_id": "S2", "status": "COMPLETED", "tax_amount": 1000.0,
             "grand_total": 6000.0, "created_at": datetime(2026, 5, 10, 6, 0)}
        )

        def _cash(sid):
            c = reports._compute_gstr3b("2026-05", sid)["taxPaidCash"]
            return c["integratedTax"] + c["centralTax"] + c["stateTax"]

        assert (_cash("S1"), _cash("S2")) == (0.0, 1000.0)  # the per-shop screens
        xc = _run_gst_cross_check(db, 5, 2026, "E1")
        assert xc["gstr3b"]["netCash"]["total"] == 1000.0
        assert xc["summary"]["gst_payable"] == 1000.0
        row = next(c for c in xc["comparisons"] if c["metric"] == "Net GST payable (cash)")
        assert row["status"] == "INFO"  # one pool would read 0; advisory, never a green MATCH
        body = finance.GstCrossCheckSignoff(month=5, year=2026, entity_id="E1", note="ok")
        out = asyncio.run(finance.gst_cross_check_signoff(body, dict(_ADMIN)))
        stored = db[finance._GST_CROSSCHECK_SIGNOFFS].find_one({"entity_id": "E1"})
        assert stored["gst_payable"] == 1000.0, out


class TestLegacyCreditPlacement:
    @staticmethod
    def _two_returns():
        db, _cli = _world()
        db["entities"].update_one({"entity_id": "E1"}, {"$push": {"gstins": {"gstin": BUY_MH, "state_code": "27"}}})
        db["stores"].insert_one({"store_id": "S2", "entity_id": "E1", "state_code": "27", "gstin": BUY_MH})
        for sid in ("S1", "S2"):
            db["orders"].insert_one({"order_id": "O-" + sid, "store_id": sid, "status": "COMPLETED", "tax_amount": 1000.0, "grand_total": 6000.0, "created_at": datetime(2026, 5, 10, 6)})
        db["vendor_bills"].insert_one({"bill_id": "LEGACY", "bill_number": "LEGACY", "vendor_id": "V1", "vendor_gstin": SUP_MH, "recipient_entity_id": "E1", "bill_date": "2026-05-03", "taxable_amount": 20000.0, "tax_amount": 1000.0, "igst_total": 1000.0, "cgst_total": 0.0, "sgst_total": 0.0, "total_amount": 21000.0, "itc_eligible": True, "status": "OUTSTANDING"})
        return db

    def test_ambiguous_legacy_credit_is_not_spent_on_two_returns(self):
        db = self._two_returns()
        for sid in ("S1", "S2"):
            report = reports._compute_gstr3b("2026-05", sid)
            assert sum(report["taxPaidCash"].values()) == 1000.0
            assert sum(report["itcAvailable"].values()) == 0.0
            assert report["unallocatedLegacyBills"] == {"count": 1, "tax": 1000.0, "bill_numbers": ["LEGACY"]}
            assert to_gstr3b_json(report)["itc_elg"]["itc_net"]["iamt"] == 0.0
        cross = _run_gst_cross_check(db, 5, 2026, "E1")
        assert cross["gstr3b"]["netCash"]["total"] == 2000.0
        from api.routers.reports.gst_itc import _itc_unplaced
        assert _itc_unplaced(db, 2026, 5, 31, "E1")["bill_numbers"] == ["LEGACY"]
        from api.routers.reports.gstr3b import gstr3b_gstn_json
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as err:
            asyncio.run(gstr3b_gstn_json(month="2026-05", year=None, store_id="S1", entity_id=None, current_user=dict(_ADMIN)))
        assert err.value.status_code == 409
        assert "GSTIN" in err.value.detail

    @pytest.mark.parametrize("source", ["store", "receipt", "sole_company"])
    def test_verified_legacy_credit_is_allocated_exactly_once(self, source):
        db = self._two_returns()
        if source == "store":
            db["vendor_bills"].update_one({"bill_id": "LEGACY"}, {"$set": {"store_id": "S1"}})
        elif source == "receipt":
            db["grns"].insert_one({"grn_id": "G1", "store_id": "S1"})
            db["vendor_bills"].update_one({"bill_id": "LEGACY"}, {"$set": {"grn_id": "G1"}})
        else:
            db["entities"].update_one({"entity_id": "E1"}, {"$pull": {"gstins": {"gstin": BUY_MH}}})
            db["stores"].delete_one({"store_id": "S2"})
            db["orders"].delete_one({"store_id": "S2"})
        report = reports._compute_gstr3b("2026-05", "S1")
        assert report["itcAvailable"]["integratedTax"] == 1000.0
        assert report["itcAvailableGstin"]["integratedTax"] == 1000.0
        assert report["unallocatedLegacyBills"]["count"] == 0
        cross = _run_gst_cross_check(db, 5, 2026, "E1")
        assert cross["gstr3b"]["itc"]["total"] == 1000.0
        assert cross["gstr3b"]["netCash"]["total"] == (0.0 if source == "sole_company" else 1000.0)

    def test_conflicting_receipt_and_shop_stay_unallocated_even_without_credit(self):
        db = self._two_returns()
        db["grns"].insert_one({"grn_id": "G1", "store_id": "S2"})
        db["vendor_bills"].update_one({"bill_id": "LEGACY"}, {"$set": {"store_id": "S1", "grn_id": "G1", "reverse_charge": True, "itc_eligible": False}})
        for sid in ("S1", "S2"):
            assert reports._compute_gstr3b("2026-05", sid)["unallocatedLegacyBills"]["count"] == 1


class TestCashRule:
    """THE set-off (gst_itc.net_cash_due), per head (igst, cgst, sgst)."""

    @staticmethod
    def _due(out, credit, rcm=(0.0, 0.0, 0.0)):
        from api.routers.reports.gst_itc import net_cash_due

        return net_cash_due(out, credit, rcm)

    def test_reverse_charge_is_paid_in_cash_whatever_the_credit(self):
        assert self._due((0, 0, 0), (180, 0, 0), (180, 0, 0)) == (180, 0, 0)

    def test_igst_credit_pays_igst_then_cgst_then_sgst(self):
        assert self._due((100, 500, 500), (700, 0, 0)) == (0, 0, 400)

    def test_cgst_and_sgst_credit_pay_igst_but_never_each_other(self):
        assert self._due((100, 0, 0), (0, 30, 40)) == (30, 0, 0)
        assert self._due((0, 0, 100), (0, 100, 0)) == (0, 0, 100)
        assert self._due((0, 100, 0), (0, 0, 100)) == (0, 100, 0)

    def test_spare_credit_carries_forward_never_below_zero(self):
        assert self._due((100, 50, 50), (300, 80, 80)) == (0, 0, 0)

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


# ===========================================================================
# Bills booked before the rule: the one-time repair (scripts/)
# ===========================================================================


def _repair():
    import importlib.util

    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "repair_rcm_bill_payable.py",
    )
    spec = importlib.util.spec_from_file_location("repair_rcm_bill_payable", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLegacyRepair:
    """A legacy RCM bill (taxable 1000, total 1180) whose supplier was paid
    the 1000 on his invoice was stored PARTIAL, 180 outstanding. Every
    screen must read it PAID once the repair runs."""

    def _legacy(self, db, **extra):
        doc = {"bill_id": "LEG-1", "doc_type": "PURCHASE_INVOICE", "vendor_id": "V1", "store_id": "S1",
               "bill_number": "GTA-0", "bill_date": "2026-05-02", "due_date": "2026-06-01",
               "reverse_charge": True, "taxable_amount": 1000.0, "tax_amount": 180.0,
               "igst_total": 180.0, "total_amount": 1180.0, "total": 1180.0,
               "outstanding": 180.0, "status": "PARTIAL"}
        doc.update(extra)
        db["vendor_bills"].insert_one(doc)
        db["vendor_payments"].insert_one(
            {"payment_id": "P1", "vendor_id": "V1", "bill_id": "LEG-1", "amount": 1000.0}
        )

    def test_the_repair_restamps_it_and_every_screen_agrees(self):
        db, cli = _world()
        self._legacy(db)
        mod = _repair()
        rows = mod.plan(db)
        assert [r["set"] for r in rows] == [
            {"total_amount": 1000.0, "total": 1000.0, "outstanding": 0.0, "status": "PAID"}
        ]
        assert db["vendor_bills"].find_one({"bill_id": "LEG-1"})["status"] == "PARTIAL"  # dry run wrote nothing
        # Before the repair the supplier's bill list already reads the payable.
        assert cli.get("/api/v1/vendors/V1/bills").json()["bills"][0]["total_amount"] == 1000.0
        assert mod.apply(db, rows) == {"bills": 1, "bills_written": 1}

        row = cli.get(_PI).json()["purchase_invoices"][0]
        got = cli.get(f"{_PI}/LEG-1").json()
        for d in (row, got):
            assert (d["total_amount"], d["total"], d["outstanding"], d["status"]) == (1000.0, 1000.0, 0.0, "PAID")
        assert cli.get(_PI, params={"status": "PAID"}).json()["total"] == 1
        vb = cli.get("/api/v1/vendors/V1/bills").json()["bills"][0]
        assert (vb["total_amount"], vb["outstanding"], vb["status"]) == (1000.0, 0.0, "PAID")
        assert _aging(cli)["totals"]["total_outstanding"] == 0.0
        assert mod.plan(db) == []  # a second run plans nothing

    def test_the_repair_leaves_normal_cancelled_and_right_bills_alone(self):
        db, cli = _world()
        _book(cli, _bill("GTA-1", rcm=True))  # booked under the rule: already right
        _book(cli, _bill("FR-1", rcm=False))
        self._legacy(db, bill_id="X", status="CANCELLED")
        assert _repair().plan(db) == []

    def test_a_payment_landing_after_the_plan_is_never_overwritten(self):
        db, _cli = _world()
        self._legacy(db)
        mod = _repair()
        rows = mod.plan(db)
        db["vendor_bills"].update_one({"bill_id": "LEG-1"}, {"$set": {"outstanding": 90.0}})
        assert mod.apply(db, rows)["bills_written"] == 0
        assert db["vendor_bills"].find_one({"bill_id": "LEG-1"})["outstanding"] == 90.0
