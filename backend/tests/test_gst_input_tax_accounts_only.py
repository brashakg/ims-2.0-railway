"""
IMS 2.0 -- INPUT TAX FROM SUPPLIER BILLS IS FOR THE ACCOUNTS ROLES ONLY (R1)
============================================================================
Owner ruling 2026-10-07 (R1): the GST summary and the GSTR-3B report show
input-tax totals summed from supplier bills -- managers do NOT need them;
ONLY admins (SUPERADMIN, ADMIN) and accounts (ACCOUNTANT) see them.

  GET /finance/gst/summary          gst_input_credit (+ _excluded) and
                                    net_gst_payable = collected - input credit
  GET /reports/gstr3b               Table 4 ITC and the RCM block
  GET /reports/gstr3b/gstn-json     the same return, portal-shaped

Before, AREA_MANAGER and STORE_MANAGER read all three: by rbac_policy row and
by handler (/gst/summary had no handler gate at all; GSTR-3B used the reports
manager set). Now each asks the ONE accounts rule, services/cost_mask.AP_ROLES
(SUPERADMIN passes on its own), and its rbac row IS rbac_policy._core.ACCOUNTS.

GSTR-1 and /reports/finance/gst stay with the managers: they are sales
(output) GST only -- orders, credit notes and transfer deemed supplies, never a
supplier bill's tax.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_gst_input_tax_accounts_only.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import reports as reports_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy  # noqa: E402
from api.services.rbac_policy._core import ACCOUNTS  # noqa: E402

INPUT_TAX_READS = (
    ("/api/v1/finance/gst/summary", {}),
    ("/api/v1/reports/gstr3b", {"month": "2026-09"}),
    ("/api/v1/reports/gstr3b/gstn-json", {"month": "2026-09"}),
)
MANAGERS = ("STORE_MANAGER", "AREA_MANAGER")
ACCOUNTS_ROLES = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")
# Sales-only GST stays the managers' (it never sums a supplier bill).
OUTPUT_ONLY = ("/api/v1/reports/gstr1", "/api/v1/reports/gstr1/gstn-json",
               "/api/v1/reports/finance/gst")

ITC = 4321.0  # a figure only Table 4 / the input credit could carry


def _user(role: str) -> dict:
    return {"user_id": f"u-{role.lower()}", "roles": [role],
            "store_ids": ["BV-DHN-01"], "active_store_id": "BV-DHN-01"}


@pytest.fixture
def client(monkeypatch):
    """The real finance and reports routers, mounted as main.py mounts the
    finance one (behind its manager-inclusive _FINANCE_ROLES gate), so the
    handler -- not a router gate -- is what refuses a manager."""
    from api.main import _FINANCE_ROLES
    from api.routers.auth import require_roles
    from fastapi import Depends
    import api.routers.reports.gstr3b as gstr3b_mod

    monkeypatch.setattr(finance_pkg, "_get_db", lambda: None)
    monkeypatch.setattr(
        gstr3b_mod, "_compute_gstr3b",
        lambda month, store: {"itc": {"igst": ITC, "cgst": 0.0, "sgst": 0.0},
                              "rcm": {"tax": ITC}, "gstin": ""},
    )
    app = FastAPI()
    app.include_router(finance_pkg.router, prefix="/api/v1/finance",
                       dependencies=[Depends(require_roles(*_FINANCE_ROLES))])
    app.include_router(reports_pkg.router, prefix="/api/v1/reports")
    tc = TestClient(app)

    def get(path, role, params):
        app.dependency_overrides[get_current_user] = lambda: _user(role)
        return tc.get(path, params=params)

    return get


@pytest.mark.parametrize("path,params", INPUT_TAX_READS)
def test_r1_the_rbac_row_is_the_one_accounts_list(path, params):
    row = rbac_policy.policy_for("GET", path)
    assert row is not None, path
    assert row["allowed"] is ACCOUNTS, (path, row["allowed"])


@pytest.mark.parametrize("role", MANAGERS)
@pytest.mark.parametrize("path,params", INPUT_TAX_READS)
def test_r1_managers_never_read_input_tax(client, path, params, role):
    resp = client(path, role, params)
    assert resp.status_code == 403, (path, role, resp.status_code, resp.text[:200])
    assert str(int(ITC)) not in resp.text


@pytest.mark.parametrize("role", ACCOUNTS_ROLES)
@pytest.mark.parametrize("path,params", INPUT_TAX_READS)
def test_r1_admins_and_accounts_still_read_it(client, path, params, role):
    resp = client(path, role, params)
    assert resp.status_code == 200, (path, role, resp.text[:200])


@pytest.mark.parametrize("path", OUTPUT_ONLY)
def test_r1_sales_gst_stays_with_the_managers(path):
    allowed = rbac_policy.policy_for("GET", path)["allowed"]
    assert set(MANAGERS) <= set(allowed), (path, allowed)


# ---------------------------------------------------------------------------
# One shop rule for the input credit (R3): the GST summary and the ITC
# register read the same shop's supplier bills
# ---------------------------------------------------------------------------
# GET /finance/gst/summary summed EVERY shop's bills for any reader while the
# ITC register gave the same login its own shop's: a Pune accountant read
# 351 (Dhanbad's 240 in it) on one tab and 111 on the next, and a login with
# no shop got 200 there and 403 here. Both now ask resolve_store_scope.

DHN, PUN = "BV-DHN-01", "WO-PUN-01"


def _shop_user(role: str, store) -> dict:
    return {"user_id": f"u-{role.lower()}-{store}", "roles": [role],
            "store_ids": [store] if store else [], "active_store_id": store}


@pytest.fixture
def shops(monkeypatch):
    mongomock = pytest.importorskip("mongomock")
    db = mongomock.MongoClient().db
    db["vendor_bills"].insert_many([
        {"bill_id": "B1", "store_id": DHN, "bill_date": "2026-09-10",
         "taxable_amount": 2000.0, "tax_amount": 240.0, "status": "OUTSTANDING"},
        {"bill_id": "B2", "store_id": PUN, "bill_date": "2026-09-12",
         "taxable_amount": 1000.0, "tax_amount": 111.0, "status": "OUTSTANDING"},
    ])

    class _Proxy:
        is_connected = True

        def get_collection(self, name):
            return db[name]

        __getitem__ = get_collection

    monkeypatch.setattr(finance_pkg, "_get_db", lambda: _Proxy())
    app = FastAPI()
    app.include_router(finance_pkg.router, prefix="/api/v1/finance")
    tc = TestClient(app)

    def get(path, user, **params):
        app.dependency_overrides[get_current_user] = lambda: user
        return tc.get(f"/api/v1/finance{path}", params=params)

    return get


def _input_credit(shops, user):
    summary = shops("/gst/summary", user, month=9, year=2026)
    register = shops("/itc-register", user, period="2026-09")
    return summary, register


@pytest.mark.parametrize(
    "user,want",
    [
        (_shop_user("ACCOUNTANT", PUN), 111.0),
        (_shop_user("ACCOUNTANT", DHN), 240.0),
        (_shop_user("ADMIN", DHN), 351.0),  # admins read every shop
    ],
)
def test_the_summary_and_the_register_read_one_shops_input_credit(shops, user, want):
    summary, register = _input_credit(shops, user)
    assert summary.status_code == register.status_code == 200, (summary.text, register.text)
    assert summary.json()["gst_input_credit"] == want
    assert register.json()["total_itc"] == want


def test_a_login_with_no_shop_reads_neither(shops):
    summary, register = _input_credit(shops, _shop_user("ACCOUNTANT", None))
    assert summary.status_code == register.status_code == 403
    assert "351" not in summary.text
