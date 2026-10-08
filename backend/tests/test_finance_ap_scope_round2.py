"""
IMS 2.0 -- THE OWNER FINANCE VIEWS OBEY THE ONE SHOP RULE AND THE ONE LEDGER
=============================================================================
Review round 2 of the Purchases-this-month branch (audit F56 / F63), findings
#2, #3, #11, #18 and #26:

  #3 #18 #26  GET /finance/owner-dashboard, /finance/cash-flow-forecast and
              /finance/survival-cashflow handed a shop-bound ACCOUNTANT every
              shop's payables, while the AP Aging tab of the same page, the
              Suppliers card and the Purchases report gave him his own shop's.
              /finance/survival-cashflow?store_id=<another shop> answered 200
              and /finance/itc-register listed every shop's bills.
  #2          'Paid to vendors' (owner dashboard) and /finance/cash-flow's
              vendor_payment_outflow counted a post-dated cheque before its
              day and money naming an inter-company transfer's mirror bill --
              a raw payment_date >= aggregate, not the supplier ledger's rows.
  #11         'Due in 7 days (AP)' counted every OVERDUE bill as well.

The rules (owner rulings F63 + 2026-10-01): ADMIN / SUPERADMIN see every shop;
any other login sees its own shop only, however the request is edited. Every
payable figure is the supplier ledger's rows (ap_engine.supplier_ledger_rows
via finance._ap_rows): dated up to today, no transfer-mirror bill or money
naming one, the shop's share when one shop is in scope.

The world, by hand. The clock is frozen at 2026-10-15 12:00 IST.

  Pune Lens Co (V-PUN) supplies Pune (WO-PUN-01)
    BP1  bill 2026-09-01, due 2026-09-30 (OVERDUE)      4000
    BP2  bill 2026-10-02, due 2026-10-19 (in 4 days)    3000
    BP3  bill 2026-10-05, due 2026-11-05 (in 21 days)   1500
    PP0  200 on BP1, 2026-09-30 (September's last day)
    PP1  1000 on BP1, 2026-10-03
    PP3  900 cash + 100 TDS on BP1, 2026-10-08          (gross 1000)
    PP2  500 on BP2 by a cheque dated 2026-10-20        (POST-DATED)
    ledger today: 8500 - 200 - 1000 - 1000 = OWED 6300
      BP1 1800 overdue, BP2 3000 due in 7 days, BP3 1500 due in 30 days
    October, paid: gross 2000 (PP1 + PP3) = cash 1900 + TDS 100

  Jharkhand Optical (V-JHK) supplies Dhanbad (BV-DHN-01)
    BD1  bill 2026-09-10, due 2026-10-10 (OVERDUE)      5000
    PD1  2000 on BD1, 2026-10-04
    ledger: OWED 3000

  An inter-company transfer Dhanbad -> Bokaro: mirror bill mbill_T1 (vendor =
  our own company ENT-A, 3150, at Bokaro) and PM1 3150 paid against it on
  2026-10-07. Neither is a supplier purchase or a supplier payment.

  All shops owe 9300; paid to suppliers in October, cash: 1900 + 2000 = 3900.
  Receivables: Pune 800, Dhanbad 1200. Expenses: Pune 300, Dhanbad 400.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_finance_ap_scope_round2.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.vendors import purchases_report as report_mod  # noqa: E402
from api.services import ap_engine  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
BOK = "BV-BOK-01"
VP = "V-PUN"
VD = "V-JHK"

# IST wall clock, the frame now_ist_naive() returns.
NOW = datetime(2026, 10, 15, 12, 0, 0)
CHEQUE_DAY = datetime(2026, 10, 20, 12, 0, 0)

PUNE_OWED = 6300.0
DHN_OWED = 3000.0
PUNE_CASH_OCT = 1900.0
PUNE_GROSS_OCT = 2000.0
PUNE_TDS_OCT = 100.0
ALL_CASH_OCT = 3900.0


def _user(role: str, store=None, stores=()) -> dict:
    return {
        "user_id": f"u-{role.lower()}-{store or 'all'}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": store,
    }


ADMIN = _user("ADMIN")
SUPERADMIN = _user("SUPERADMIN")
ACCT_PUNE = _user("ACCOUNTANT", PUN, (PUN,))


# ============================================================================
# The engine: the real Mongo CI runs against; mongomock as a dev-box fallback
# ============================================================================


def _seed(db) -> None:
    def ins(coll, *docs):
        for d in docs:
            db[coll].insert_one(dict(d))

    ins(
        "vendors",
        {"vendor_id": VP, "legal_name": "Pune Lens Co"},
        {"vendor_id": VD, "legal_name": "Jharkhand Optical"},
    )

    def bill(bill_id, vendor, store, on, due, total, taxable, tax, **extra):
        return {
            "bill_id": bill_id, "vendor_id": vendor, "store_id": store,
            "bill_number": f"INV-{bill_id}", "bill_date": on, "due_date": due,
            "total_amount": total, "taxable_amount": taxable, "tax_amount": tax,
            "status": "OUTSTANDING", **extra,
        }

    ins(
        "vendor_bills",
        bill("BP1", VP, PUN, "2026-09-01", "2026-09-30", 4000.0, 1000.0, 50.0),
        bill("BP2", VP, PUN, "2026-10-02", "2026-10-19", 3000.0, 2000.0, 100.0),
        bill("BP3", VP, PUN, "2026-10-05", "2026-11-05", 1500.0, 1000.0, 50.0),
        bill("BD1", VD, DHN, "2026-09-10", "2026-10-10", 5000.0, 4000.0, 200.0),
        # transfers._book_mirror_purchase's shape: vendor = the sending company.
        bill("mbill_T1", "ENT-A", BOK, "2026-10-06", "2026-11-06", 3150.0, 3000.0, 150.0,
             source_transfer_id="T1", from_store_id=DHN, to_store_id=BOK,
             vendor_name="Better Vision Dhanbad", auto_generated=True),
    )

    def pay(pid, vendor, bill_id, amount, on, tds=0.0):
        return {
            "payment_id": pid, "vendor_id": vendor, "bill_id": bill_id,
            "amount": amount, "tds_amount": tds, "mode": "BANK",
            "payment_date": on,
        }

    ins(
        "vendor_payments",
        pay("PP0", VP, "BP1", 200.0, "2026-09-30"),
        pay("PP1", VP, "BP1", 1000.0, "2026-10-03"),
        pay("PP3", VP, "BP1", 900.0, "2026-10-08", tds=100.0),
        # Recorded today, the cheque is dated the 20th.
        dict(pay("PP2", VP, "BP2", 500.0, "2026-10-20"), mode="CHEQUE"),
        pay("PD1", VD, "BD1", 2000.0, "2026-10-04"),
        pay("PM1", "ENT-A", "mbill_T1", 3150.0, "2026-10-07"),
    )

    # Customer orders, created_at in the stored naive-UTC frame.
    def order(oid, store, total, status):
        return {
            "order_id": oid, "store_id": store, "grand_total": total,
            "amount_paid": total if status == "PAID" else 0.0,
            "payment_status": status, "status": "CONFIRMED",
            "created_at": datetime(2026, 10, 10, 5, 0, 0),
        }

    ins(
        "orders",
        order("O-PUN-DUE", PUN, 800.0, "UNPAID"),
        order("O-DHN-DUE", DHN, 1200.0, "UNPAID"),
        order("O-PUN-PAID", PUN, 700.0, "PAID"),
        order("O-DHN-PAID", DHN, 900.0, "PAID"),
    )
    ins(
        "expenses",
        {"expense_id": "E-PUN", "store_id": PUN, "category": "Rent", "amount": 300.0,
         "expense_date": "2026-10-02", "status": "APPROVED"},
        {"expense_id": "E-DHN", "store_id": DHN, "category": "Rent", "amount": 400.0,
         "expense_date": "2026-10-02", "status": "APPROVED"},
    )


@pytest.fixture(scope="module")
def mongo_db():
    """Seeded once: every test here only reads."""
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_apscope2_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return
        client = mongomock.MongoClient()
    db = client[db_name]
    _seed(db)
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
        client.close()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


def _freeze(monkeypatch, now: datetime) -> None:
    """One IST wall clock for every reader on these routes."""
    monkeypatch.setattr(finance_pkg, "now_ist_naive", lambda: now)
    monkeypatch.setattr(finance_pkg, "ist_today", lambda: now.date())
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: now)
    monkeypatch.setattr(report_mod, "now_ist_naive", lambda: now)


class _World:
    def __init__(self, app: FastAPI, monkeypatch):
        self._app = app
        self._client = TestClient(app)
        self._mp = monkeypatch

    def at(self, now: datetime) -> "_World":
        _freeze(self._mp, now)
        return self

    def _as(self, user: dict) -> None:
        async def _u():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _u

    def get(self, path: str, user: dict, **params):
        self._as(user)
        return self._client.get(path, params={k: v for k, v in params.items() if v is not None})

    def ok(self, path: str, user: dict, **params):
        resp = self.get(path, user, **params)
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
        return resp.json()

    def post(self, path: str, user: dict, body: dict, **params):
        self._as(user)
        return self._client.post(path, json=body, params=params)


@pytest.fixture
def world(mongo_db, monkeypatch):
    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(finance_pkg, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: proxy)
    # The survival view reads two E2 policy lists; the registry defaults.
    monkeypatch.setattr(
        finance_pkg.policy_engine,
        "get_policy",
        lambda key, scope=None, default=None: default,
    )
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(finance_pkg.router, prefix="/finance")
    return _World(app, monkeypatch).at(NOW)


# ============================================================================
# The anchor: the supplier ledger owes what the header says
# ============================================================================


def test_the_ledger_owes_what_the_header_says(world):
    aging = world.ok("/vendors/ap-aging", ADMIN)
    per_vendor = {v["vendor_id"]: v["net_payable"] for v in aging["vendors"]}
    assert per_vendor == {VP: PUNE_OWED, VD: DHN_OWED}, per_vendor
    report = world.ok("/vendors/purchases-this-month", ACCT_PUNE)
    assert report["month"] == "2026-10"
    assert report["totals"]["owed"] == pytest.approx(PUNE_OWED)
    assert report["totals"]["paid"] == pytest.approx(PUNE_GROSS_OCT)


# ============================================================================
# #3 / #18 / #26 -- one 'we owe' per login, and it is his own shop's
# ============================================================================


def test_a_pune_accountants_dashboard_owes_what_every_other_screen_owes(world):
    """The finding's own acceptance line: the Pune accountant's owner-dashboard
    payables equal his AP aging, his Suppliers card and his report."""
    dash = world.ok("/finance/owner-dashboard", ACCT_PUNE)
    aging = world.ok("/vendors/ap-aging", ACCT_PUNE)
    cards = world.ok("/finance/vendor-payments", ACCT_PUNE)
    report = world.ok("/vendors/purchases-this-month", ACCT_PUNE)

    owed = dash["payables"]["total"]
    assert owed == pytest.approx(PUNE_OWED), dash["payables"]
    assert aging["totals"]["net_payable"] == pytest.approx(owed)
    assert sum(r["balance"] for r in cards) == pytest.approx(owed)
    assert report["totals"]["owed"] == pytest.approx(owed)
    # Dhanbad's overdue BD1 is not his: overdue = BP1's 1800 only.
    assert dash["payables"]["overdue"] == pytest.approx(1800.0)
    assert dash["store_id"] == PUN


def test_every_figure_on_a_pune_accountants_dashboard_is_punes(world):
    """AR, revenue, expenses and supplier money in one scope, so net_position
    and net_cash_flow never subtract one shop's payables from every shop's
    receivables."""
    dash = world.ok("/finance/owner-dashboard", ACCT_PUNE)
    assert dash["receivables"]["total"] == pytest.approx(800.0)
    assert dash["net_position"] == pytest.approx(800.0 - PUNE_OWED)
    month = dash["this_month"]
    assert month["revenue"] == pytest.approx(700.0)
    assert month["expenses"] == pytest.approx(300.0)
    assert month["vendor_payments"] == pytest.approx(PUNE_CASH_OCT)
    assert month["net_cash_flow"] == pytest.approx(700.0 - 300.0 - PUNE_CASH_OCT)


@pytest.mark.parametrize("admin", [ADMIN, SUPERADMIN], ids=["ADMIN", "SUPERADMIN"])
def test_an_admin_still_sees_every_shop_on_the_dashboard(world, admin):
    dash = world.ok("/finance/owner-dashboard", admin)
    assert dash["store_id"] is None
    assert dash["payables"]["total"] == pytest.approx(PUNE_OWED + DHN_OWED)
    assert dash["receivables"]["total"] == pytest.approx(2000.0)
    assert dash["this_month"]["vendor_payments"] == pytest.approx(ALL_CASH_OCT)
    aging = world.ok("/vendors/ap-aging", admin)
    assert aging["totals"]["net_payable"] == pytest.approx(dash["payables"]["total"])


def _forecast_outflow(body) -> float:
    return round(body["totals"]["outflow"] + body["beyond_horizon"]["outflow"], 2)


def test_a_pune_accountants_forecast_projects_punes_bills_only(world):
    """Outflows = the AP aging items on their due dates + 3 recurring monthly
    estimates (1 Nov, 1 Dec, 1 Jan fall inside 90 days of 15 Oct). Pune's
    estimate is its own 90-day expenses / 3 = 300 / 3."""
    pune = world.ok("/finance/cash-flow-forecast", ACCT_PUNE, days=90)
    assert pune["assumptions"]["monthly_expense_estimate"] == pytest.approx(100.0)
    assert _forecast_outflow(pune) == pytest.approx(PUNE_OWED + 3 * 100.0)
    assert pune["totals"]["inflow"] == pytest.approx(800.0)

    org = world.ok("/finance/cash-flow-forecast", ADMIN, days=90)
    est = org["assumptions"]["monthly_expense_estimate"]
    assert est == pytest.approx(700.0 / 3, abs=0.01)
    assert _forecast_outflow(org) == pytest.approx(PUNE_OWED + DHN_OWED + 3 * est, abs=0.02)
    assert org["totals"]["inflow"] == pytest.approx(2000.0)


def _survival_bills(body) -> set:
    sv = body["survival"]
    return {
        r["bill_id"]
        for r in sv["essential_detail"] + sv["deferrable_detail"]
        if r.get("kind") == "ap_bill"
    }


def _survival_ap_paise(body) -> int:
    sv = body["survival"]
    return sv["must_pay_ap_paise"] + sv["deferrable_ap_paise"]


@pytest.mark.parametrize("asked", [None, PUN], ids=["store_id dropped", "own shop"])
def test_a_pune_accountants_survival_view_is_punes_ap(world, asked):
    body = world.ok("/finance/survival-cashflow", ACCT_PUNE, store_id=asked)
    assert body["store_id"] == PUN
    assert _survival_bills(body) == {"BP1", "BP2", "BP3"}
    assert _survival_ap_paise(body) == int(PUNE_OWED * 100)
    sv = body["survival"]
    assert sv["ap_scope"] == "STORE"
    assert sv["income_expense_scope"] == "STORE"
    assert sv["fixed_costs_paise"] == 30000  # Pune's rent only


def test_a_pune_accountant_cannot_ask_survival_for_dhanbad(world):
    resp = world.get("/finance/survival-cashflow", ACCT_PUNE, store_id=DHN)
    assert resp.status_code == 403, resp.text
    assert "BD1" not in resp.text and "5000" not in resp.text


def test_an_admins_survival_ap_stays_org_wide_under_a_shop_filter(world):
    whole = world.ok("/finance/survival-cashflow", ADMIN)
    pune = world.ok("/finance/survival-cashflow", ADMIN, store_id=PUN)
    for body in (whole, pune):
        assert _survival_bills(body) == {"BP1", "BP2", "BP3", "BD1"}
        assert _survival_ap_paise(body) == int((PUNE_OWED + DHN_OWED) * 100)
        assert body["survival"]["ap_scope"] == "ORG_WIDE"
    assert whole["survival"]["fixed_costs_paise"] == 70000  # both rents
    assert pune["survival"]["fixed_costs_paise"] == 30000  # income/expenses narrow


def test_the_itc_register_is_the_callers_shop(world):
    pune = world.ok("/finance/itc-register", ACCT_PUNE)
    assert pune["total_taxable"] == pytest.approx(4000.0)
    assert pune["total_itc"] == pytest.approx(200.0)
    org = world.ok("/finance/itc-register", ADMIN)
    # Every shop's bills, the transfer's mirror included (its ITC is the
    # receiving shop's -- reports._itc_from_vendor_bills, unchanged here).
    assert org["total_taxable"] == pytest.approx(11000.0)
    assert org["total_itc"] == pytest.approx(550.0)


def test_the_gstr2b_books_side_is_the_callers_shop(world):
    resp = world.post("/finance/gstr2b-reconcile", ACCT_PUNE, {"rows": [], "as_of": "2026-10-15"})
    assert resp.status_code == 200, resp.text
    booked = {r["invoice_no"] for r in resp.json()["only_in_books"]}
    assert booked == {"INV-BP1", "INV-BP2", "INV-BP3"}
    resp = world.post(
        "/finance/itc-export", ACCT_PUNE, {"rows": [], "as_of": "2026-10-15"},
        bucket="only_in_books",
    )
    assert resp.status_code == 200, resp.text
    assert "INV-BD1" not in resp.text and "INV-BP1" in resp.text
    resp = world.post("/finance/gstr2b-reconcile", ADMIN, {"rows": [], "as_of": "2026-10-15"})
    assert "INV-BD1" in {r["invoice_no"] for r in resp.json()["only_in_books"]}


# ============================================================================
# #2 -- 'Paid to vendors' is the ledger's payments for the month and shop
# ============================================================================


def test_paid_to_vendors_is_the_reports_paid_less_tds(world):
    """Same rows, same month, same shop. The report's `paid` is GROSS (cash +
    TDS, what discharged the bills); Cash Flow is the cash that left."""
    for user in (ACCT_PUNE, ADMIN):
        dash = world.ok("/finance/owner-dashboard", user)
        report = world.ok("/vendors/purchases-this-month", user)
        assert dash["this_month"]["vendor_payments"] == pytest.approx(
            report["totals"]["paid"] - PUNE_TDS_OCT
        ), user["roles"]
    pune = world.ok("/finance/owner-dashboard", ACCT_PUNE)["this_month"]
    assert pune["vendor_payments"] == pytest.approx(PUNE_CASH_OCT)


def test_a_post_dated_cheque_is_paid_on_its_day_and_not_before(world):
    """15 Oct: the cheque dated 20 Oct is neither paid nor off the payable
    (Cash Flow agrees with itself and with the report). 20 Oct: it is both."""
    before = world.ok("/finance/owner-dashboard", ACCT_PUNE)
    assert before["this_month"]["vendor_payments"] == pytest.approx(PUNE_CASH_OCT)
    assert before["payables"]["total"] == pytest.approx(PUNE_OWED)

    world.at(CHEQUE_DAY)
    after = world.ok("/finance/owner-dashboard", ACCT_PUNE)
    assert after["this_month"]["vendor_payments"] == pytest.approx(PUNE_CASH_OCT + 500.0)
    assert after["payables"]["total"] == pytest.approx(PUNE_OWED - 500.0)
    report = world.ok("/vendors/purchases-this-month", ACCT_PUNE)
    assert report["totals"]["paid"] == pytest.approx(PUNE_GROSS_OCT + 500.0)


def test_the_cash_flow_org_view_pays_out_the_ledgers_cash(world):
    """/finance/cash-flow's org view (an admin's: no shop asked): October's
    supplier cash -- not the post-dated cheque, not the transfer mirror's
    3150, not 30 September's 200 (IST midnight of the 1st is 30 Sep in UTC:
    the old bound's day)."""
    body = world.ok("/finance/cash-flow", ADMIN)
    assert body["vendor_payment_outflow"] == pytest.approx(ALL_CASH_OCT)
    assert body["outflows"] == pytest.approx(
        body["expense_outflow"] + body["purchase_outflow"] + ALL_CASH_OCT
    )


# ============================================================================
# #11 -- 'Due in 7 days' is what falls due in the next 7 days
# ============================================================================


def test_due_in_7_days_leaves_the_overdue_bills_out(world):
    pune = world.ok("/finance/owner-dashboard", ACCT_PUNE)["payables"]
    assert pune["due_7d"] == pytest.approx(3000.0)  # BP2 only; BP1 is overdue
    assert pune["due_30d"] == pytest.approx(4500.0)  # BP2 + BP3
    org = world.ok("/finance/owner-dashboard", ADMIN)
    assert org["payables"]["due_7d"] == pytest.approx(3000.0)  # BD1 is overdue too
    assert org["payables"]["overdue"] == pytest.approx(1800.0 + DHN_OWED)
    info = [a for a in org["alerts"] if "within 7 days" in a["label_template"]]
    assert [a["amount"] for a in info] == [pytest.approx(3000.0)]


def test_the_budget_hooks_survival_view_is_the_callers_shop_too(world):
    """GET /finance/budget?mode=survival embeds the same survival view: a Pune
    accountant reads Pune's AP there exactly as on /finance/survival-cashflow;
    an admin's AP stays org-wide."""
    pune = world.ok("/finance/budget", ACCT_PUNE, mode="survival")
    assert _survival_bills(pune) == {"BP1", "BP2", "BP3"}
    assert _survival_ap_paise(pune) == int(PUNE_OWED * 100)
    assert pune["survival"]["ap_scope"] == "STORE"
    whole = world.ok("/finance/budget", ADMIN, mode="survival")
    assert _survival_bills(whole) == {"BP1", "BP2", "BP3", "BD1"}
