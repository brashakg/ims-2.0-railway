"""The GST summary and GSTR-3B are the accounts roles' alone (owner ruling
2026-10-07, R1).

Ruling: the GST summary and the GSTR-3B report show input-tax totals summed
from supplier bills -- managers do NOT need them; ONLY admins (SUPERADMIN,
ADMIN) and accounts (ACCOUNTANT) see them. (It replaces 2026-10-12 R5, under
which managers kept the sales-side figures with the input side stripped.)

ONE rule decides: cost_mask "payables" (AP_ROLES + SUPERADMIN, asked as
payables_mask.can_see_payables). For STORE_MANAGER / AREA_MANAGER every one of
GET /finance/gst/summary, /reports/gstr3b and /reports/gstr3b/gstn-json is a
403 -- by the route's own gate, by the rbac_policy row, and through the live
app's middleware -- and nothing is read for them. The accounts roles read
every figure unchanged. payables_mask.strip_input_tax (R5) still drops the
input side for any reader outside the rule, should a route ever admit one.

Handlers run for real against mongomock; no network. No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, Iterator

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import finance  # noqa: E402
from api.routers import reports  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402

MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
ACCOUNTS = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")
ALL_ROLES = (
    "SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT",
    "CATALOG_MANAGER", "OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF",
    "CASHIER", "WORKSHOP_STAFF", "DESIGN_MANAGER",
)

# Every input-tax key, by name...
SUMMARY_INPUT_KEYS = {
    "gst_input_credit", "gst_input_credit_excluded", "net_gst_payable",
    "tax_paid", "net_gst_liability",
    "gst_input_credit_carried_forward", "reverse_charge_tax",
}
GSTR3B_INPUT_KEYS = {
    "itcAvailable", "itcAvailableRegular", "itcAvailableGstin", "itcUtilized",
    "inwardSuppliesReverseChargeValue", "inwardSuppliesReverseCharge",
    "inwardSuppliesReverseChargeGstin", "taxPaidCash",
    "itcReverseCharge",
    "itcIneligible", "inwardExemptNilNonGst",
}
# ...and by shape, so one added later under another name is caught too.
_INPUTISH = re.compile(r"itc|input|reverse|inward|paidcash|tax_?paid|net_?gst", re.I)


def _keys(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def _inputish(body: Any) -> set:
    return {k for k in _keys(body) if isinstance(k, str) and _INPUTISH.search(k)}


def _user(role: str) -> Dict[str, Any]:
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": ["S1"],
        "active_store_id": "S1",
    }


# ============================================================================
# The month's books: one sale, two supplier bills (+ one blocked for ITC)
# ============================================================================


def _world() -> Any:
    db = mongomock.MongoClient().db
    db["entities"].insert_one({
        "entity_id": "E1", "legal_name": "BV Optics",
        "gstins": [{"gstin": "20AAACB1234C1Z5", "state_code": "20", "is_primary": True}],
    })
    db["stores"].insert_one(
        {"store_id": "S1", "store_name": "Bokaro", "entity_id": "E1",
         "state": "Jharkhand", "gstin": "20AAACB1234C1Z5"}
    )
    # One intra-state sale in May 2026 (IST): taxable 1000, GST 180 -> CGST 90
    # + SGST 90 of output tax.
    db["orders"].insert_one(
        {"order_id": "O-1", "store_id": "S1", "status": "DELIVERED",
         "created_at": datetime(2026, 5, 10, 6, 0), "grand_total": 1180.0,
         "tax_amount": 180.0}
    )
    # A purchase bill booked at Bokaro (F63: every bill carries its shop; it
    # names no GSTIN, so its verified shop registration places it) -- 50 + 50.
    db["vendor_bills"].insert_one(
        {"bill_id": "B-1", "store_id": "S1", "bill_number": "PB-1", "vendor_id": "V1",
         "bill_date": "2026-05-04", "invoice_date": "2026-05-04",
         "recipient_entity_id": "E1", "recipient_gstin": None,
         "taxable_amount": 1111.0, "tax_amount": 100.0, "cgst_total": 50.0,
         "sgst_total": 50.0, "igst_total": 0.0, "status": "OUTSTANDING"}
    )
    # A reverse-charge services bill (freight) -- Table 3.1(d) and, being
    # claimable, Table 4 too: 10 + 10 on a taxable value of 400.
    db["vendor_bills"].insert_one(
        {"bill_id": "B-2", "store_id": "S1", "bill_number": "RCM-1", "vendor_id": "V2",
         "bill_date": "2026-05-06", "invoice_date": "2026-05-06",
         "reverse_charge": True, "recipient_entity_id": "E1",
         "recipient_gstin": None, "taxable_amount": 400.0, "tax_amount": 20.0,
         "cgst_total": 10.0, "sgst_total": 10.0, "igst_total": 0.0,
         "status": "OUTSTANDING"}
    )
    # A 17(5)-blocked bill: held back from the summary's credit (shown to
    # accounts as gst_input_credit_excluded).
    db["vendor_bills"].insert_one(
        {"bill_id": "B-3", "store_id": "S1", "bill_number": "PB-3", "vendor_id": "V1",
         "bill_date": "2026-05-08", "invoice_date": "2026-05-08",
         "recipient_entity_id": "E1", "recipient_gstin": None,
         "taxable_amount": 300.0, "tax_amount": 33.0, "cgst_total": 16.5,
         "sgst_total": 16.5, "igst_total": 0.0, "itc_blocked": True,
         "itc_eligible": False, "status": "OUTSTANDING"}
    )
    return db


# ============================================================================
# GET /finance/gst/summary
# ============================================================================

SUMMARY_URL = "/finance/gst/summary?month=5&year=2026"


@pytest.fixture
def finance_db():
    original = finance._get_db
    db = _world()
    finance._get_db = lambda: db
    yield db
    finance._get_db = original


def _finance_client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(finance.router, prefix="/finance")

    async def _fake_user():
        return _user(role)

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


class TestGstSummary:
    @pytest.mark.parametrize("role", ACCOUNTS)
    def test_accounts_read_the_input_credit_and_the_net(self, finance_db, role):
        r = _finance_client(role).get(SUMMARY_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["gst_collected"] == 180.0
        # B-1 + B-2 eligible (100 + 20); B-3 is 17(5)-blocked.
        assert body["gst_input_credit"] == 120.0
        assert body["gst_input_credit_excluded"] == 33.0
        # Output 180 - credit 120 + reverse-charge cash 20.
        assert body["reverse_charge_tax"] == 20.0
        assert body["net_gst_payable"] == 80.0

    @pytest.mark.parametrize("role", MANAGERS)
    def test_managers_are_refused_and_nothing_is_read(self, finance_db, role):
        reads = []
        real = finance_db.get_collection

        def _spy(name, *a, **k):
            reads.append(name)
            return real(name, *a, **k)

        finance_db.get_collection = _spy
        r = _finance_client(role).get(SUMMARY_URL)
        assert r.status_code == 403, r.text
        assert reads == []

    @pytest.mark.parametrize("role", MANAGERS)
    def test_managers_are_refused_with_no_db_too(self, role):
        original = finance._get_db
        finance._get_db = lambda: None
        try:
            assert _finance_client(role).get(SUMMARY_URL).status_code == 403
        finally:
            finance._get_db = original

    def test_accounts_no_db_skeleton_unchanged(self):
        original = finance._get_db
        finance._get_db = lambda: None
        try:
            body = _finance_client("ACCOUNTANT").get(SUMMARY_URL).json()
        finally:
            finance._get_db = original
        assert body == {
            "tax_collected": 0, "tax_paid": 0, "net_gst_liability": 0, "gst_by_rate": {},
        }


# ============================================================================
# GET /reports/gstr3b  +  /reports/gstr3b/gstn-json
# ============================================================================

GSTR3B_URL = "/reports/gstr3b?month=2026-05&store_id=S1"
GSTN_URL = "/reports/gstr3b/gstn-json?month=2026-05&store_id=S1"


@pytest.fixture
def reports_db():
    original = reports._get_raw_db
    db = _world()
    reports._get_raw_db = lambda: db
    yield db
    reports._get_raw_db = original


def _reports_client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(reports.router, prefix="/reports")

    async def _fake_user():
        return _user(role)

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _heads(i, c, s):
    return {"integratedTax": i, "centralTax": c, "stateTax": s, "cess": 0.0}


class TestGstr3b:
    @pytest.mark.parametrize("role", ACCOUNTS)
    def test_accounts_read_table_4_and_31d(self, reports_db, role):
        r = _reports_client(role).get(GSTR3B_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["outwardTaxableSupplies"] == _heads(0.0, 90.0, 90.0)
        # Table 4: B-1 + B-2 (B-3 is itc_eligible False).
        assert body["itcAvailable"] == _heads(0.0, 60.0, 60.0)
        assert body["itcUtilized"] == _heads(0.0, 60.0, 60.0)
        # Table 3.1(d): the reverse-charge bill.
        assert body["inwardSuppliesReverseChargeValue"] == 400.0
        assert body["inwardSuppliesReverseCharge"] == _heads(0.0, 10.0, 10.0)
        # Cash: (90 - 60) + 10 per head.
        assert body["taxPaidCash"] == _heads(0.0, 40.0, 40.0)
        assert GSTR3B_INPUT_KEYS <= set(body)

    @pytest.mark.parametrize("role", MANAGERS)
    def test_managers_get_neither_the_report_nor_the_portal_export(self, reports_db, role):
        for url in (GSTR3B_URL, GSTN_URL):
            r = _reports_client(role).get(url)
            assert r.status_code == 403, (url, r.text)

    @pytest.mark.parametrize("role", ACCOUNTS)
    def test_accounts_portal_export_unchanged(self, reports_db, role):
        r = _reports_client(role).get(GSTN_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ret_period"] == "052026"
        assert body["itc_elg"]["itc_net"]["camt"] == 60.0
        assert body["sup_details"]["osup_det"]["camt"] == 90.0


# ============================================================================
# The export's gate three ways: route dependency, policy row, live app
# ============================================================================


def _route_gate(router, method: str, path: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            for dep in route.dependant.dependencies:
                if getattr(dep.call, "__name__", "") == "_dep":
                    return dep.call
            raise AssertionError(f"{method} {path} carries no require_roles gate")
    raise AssertionError(f"no {method} {path}")


def _gate_admits(gate, role: str) -> bool:
    try:
        asyncio.run(gate(current_user=_user(role)))
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        return False


def test_every_gate_and_policy_row_is_the_accounts_rule(finance_db):
    """GSTR-3B, its portal file and the GST summary: the route's own gate and
    its rbac_policy row each admit exactly the accounts rule, role by role."""
    from api.services.payables_mask import can_see_payables

    for route in ("/gstr3b", "/gstr3b/gstn-json"):
        gate = _route_gate(reports.router, "GET", route)
        path = f"/api/v1/reports{route}"
        for role in ALL_ROLES:
            accounts = can_see_payables({"roles": [role]})
            assert _gate_admits(gate, role) is accounts, (route, role)
            assert rbac.check_access("GET", path, [role]) is accounts, (path, role)
    for role in ALL_ROLES:
        accounts = can_see_payables({"roles": [role]})
        assert (
            rbac.check_access("GET", "/api/v1/finance/gst/summary", [role]) is accounts
        ), role
        code = _finance_client(role).get(SUMMARY_URL).status_code
        assert code == (200 if accounts else 403), (role, code)


def _bearer(role: str) -> Dict[str, str]:
    from api.routers.auth import create_access_token

    token = create_access_token(
        {"user_id": f"u-{role.lower()}", "username": role.lower(), "roles": [role],
         "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("role", MANAGERS)
def test_live_app_refuses_a_manager_all_three(client, role):
    """Through the real app (middleware + routers, no DB)."""
    h = _bearer(role)
    for url in (
        "/api/v1/reports/gstr3b/gstn-json?month=2026-05",
        "/api/v1/reports/gstr3b?month=2026-05",
        "/api/v1/finance/gst/summary?month=5&year=2026",
    ):
        r = client.get(url, headers=h)
        assert r.status_code == 403, (url, r.text)


def test_live_app_accountant_still_exports(client):
    r = client.get(
        "/api/v1/reports/gstr3b/gstn-json?month=2026-05", headers=_bearer("ACCOUNTANT")
    )
    assert r.status_code == 200, r.text
    assert "itc_elg" in r.json()


# ============================================================================
# ONE strip (services/payables_mask.strip_input_tax) for both payloads
# ============================================================================


class TestOneInputTaxStrip:
    """R5 has one implementation: payables_mask.INPUT_TAX_KEYS + strip_input_tax,
    the strip both routes return through. No manager reaches either route now
    (R1), so the strip is pinned on the accounts' own bodies."""

    def test_the_one_key_set_is_both_payloads_input_side(self):
        from api.services import payables_mask

        assert payables_mask.INPUT_TAX_KEYS == SUMMARY_INPUT_KEYS | GSTR3B_INPUT_KEYS

    def test_the_strip_drops_both_bodies_input_side_outside_the_rule(self, finance_db, reports_db):
        from api.services.payables_mask import INPUT_TAX_KEYS, strip_input_tax

        summary = _finance_client("ACCOUNTANT").get(SUMMARY_URL).json()
        gstr3b = _reports_client("ACCOUNTANT").get(GSTR3B_URL).json()
        for body in (summary, gstr3b):
            assert _inputish(body), body
            out = strip_input_tax(body, _user("STORE_MANAGER"))
            assert out == {k: v for k, v in body.items() if k not in INPUT_TAX_KEYS}
            assert _inputish(out) == set(), out
            assert strip_input_tax(body, _user("ACCOUNTANT")) is body

    def test_no_router_keeps_a_copy_of_the_rule(self):
        api_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")
        copies = []
        for root, _dirs, files in os.walk(api_dir):
            for name in files:
                if not name.endswith(".py") or name == "payables_mask.py":
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                if re.search(r"_INPUT_TAX_KEYS\s*=|def _output_side_only\b", text):
                    copies.append(os.path.relpath(path, api_dir))
        assert copies == [], copies
