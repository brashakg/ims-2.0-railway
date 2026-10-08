"""
Owner rulings 2026-10-08 - settings that duplicated a rule IMS already
enforces are gone, the chain default credit limit is live, and the
auto-logout setting is saved and read under ONE id.

  1. A customer with no credit limit of their own gets the chain default
     (Rs 1,50,000 unless SUPERADMIN/ADMIN saved another one on
     Settings > Operational Rules). Their own limit always wins. A credit
     sale over the limit stays BLOCKED - at the ONE check, the till's
     POST /orders/{id}/payments.
  2. /admin/system/settings and /settings/system write the same document
     the /health auto-logout reader reads.
  3. The removed duplicates stay removed.

Every Mongo answer comes from mongomock - no live DB.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sys

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import admin_extras  # noqa: E402
from api.routers import customers as customers_module  # noqa: E402
from api.routers import orders as orders_module  # noqa: E402
from api.routers import settings as settings_module  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.orders import PaymentCreate, add_payment  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402


# ---------------------------------------------------------------------------
# One mongomock database behind every settings door
# ---------------------------------------------------------------------------


class _Db:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]


@pytest.fixture()
def db(monkeypatch):
    mdb = mongomock.MongoClient().db
    # Patch the copy imported above AND the one live in sys.modules: another
    # test pops api.routers.settings, and the till's call-time import of the
    # credit default then resolves to a fresh module object.
    for name, mod in (("settings", settings_module), ("admin_extras", admin_extras)):
        attr = "_get_settings_collection" if name == "settings" else "_coll"
        for m in {mod, importlib.import_module(f"api.routers.{name}")}:
            monkeypatch.setattr(m, attr, lambda n: mdb[n])
    return mdb


class _Customers:
    def __init__(self, *docs):
        self._docs = {d["customer_id"]: d for d in docs}

    def find_by_id(self, cid):
        return self._docs.get(cid)


def _user(*roles):
    return {"user_id": "U-1", "username": "asha", "roles": list(roles), "active_store_id": "BV-PUN-01"}


def _client(user):
    app = FastAPI()
    app.include_router(settings_module.router, prefix="/settings")
    app.include_router(customers_module.router, prefix="/customers")
    app.include_router(admin_extras.router, prefix="/admin")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _rules(**kw):
    return {"operational_rules": {"auto_round_off": True, "default_credit_limit": 150000, **kw}}


# ===========================================================================
# 1. The chain default credit limit
# ===========================================================================


def test_no_saved_default_means_rs_150000(db):
    assert customers_module.effective_credit_limit({"customer_id": "C1"}) == 150000.0


@pytest.mark.parametrize("own", [None, 0, 0.0])
def test_a_customer_with_no_limit_of_their_own_gets_the_default(db, own):
    doc = {"customer_id": "C1"} if own is None else {"customer_id": "C1", "credit_limit": own}
    assert customers_module.effective_credit_limit(doc) == 150000.0


@pytest.mark.parametrize("own", [5000, 900000])
def test_the_customers_own_limit_wins_over_the_default(db, own):
    assert customers_module.effective_credit_limit({"customer_id": "C1", "credit_limit": own}) == float(own)


def test_admin_saves_the_default_and_the_check_reads_it(db):
    put = _client(_user("ADMIN")).put("/settings/admin-controls", json=_rules(default_credit_limit=20000))
    assert put.status_code == 200, put.text
    assert customers_module.effective_credit_limit({"customer_id": "C1"}) == 20000.0
    # ...and the customer's own limit still wins over the saved default
    assert customers_module.effective_credit_limit({"customer_id": "C1", "credit_limit": 70000}) == 70000.0


def test_get_shows_the_default_in_force_and_nothing_removed(db):
    db["admin_controls"].insert_one({
        "_id": "default",
        "store_modules": {"S1": {"clinical": False}},
        "role_permissions": {"STORE_MANAGER": {"void_orders": True}},
        "discount_limits": [{"roleId": "SALES_STAFF"}],
        "operational_rules": {"auto_round_off": False, "credit_limit": 50000, "password_expiry": 90},
    })
    body = _client(_user("ADMIN")).get("/settings/admin-controls").json()
    # The stale 50000 under the old key never becomes the enforced default.
    assert body == {"operational_rules": {"auto_round_off": False, "default_credit_limit": 150000.0}}


def test_superadmin_and_admin_may_edit_the_default_store_manager_may_not(db):
    for role in ("SUPERADMIN", "ADMIN"):
        assert _client(_user(role)).put("/settings/admin-controls", json=_rules()).status_code == 200, role
    for role in ("STORE_MANAGER", "ACCOUNTANT", "SALES_STAFF"):
        c = _client(_user(role))
        assert c.get("/settings/admin-controls").status_code == 403, role
        assert c.put("/settings/admin-controls", json=_rules()).status_code == 403, role


@pytest.mark.parametrize("body", [
    {"store_modules": {"S1": {"clinical": False}}, **_rules()},
    {"role_permissions": {}, **_rules()},
    {"discount_limits": [], **_rules()},
    _rules(password_expiry=90),
    _rules(credit_approval=True),
    _rules(require_customer=True),
    _rules(default_credit_limit=0),
    _rules(default_credit_limit=-5),
    {"operational_rules": {"auto_round_off": True, "default_credit_limit": "Infinity"}},
])
def test_removed_settings_and_a_non_positive_default_are_refused(db, body):
    r = _client(_user("SUPERADMIN")).put("/settings/admin-controls", json=body)
    assert r.status_code == 422, r.text
    assert db["admin_controls"].find_one({"_id": "default"}) is None


def test_a_save_keeps_only_the_live_settings(db):
    db["admin_controls"].insert_one({
        "_id": "default",
        "store_modules": {"S1": {"clinical": False}},
        "operational_rules": {"credit_limit": 50000, "geo_fence_radius": 200},
    })
    assert _client(_user("ADMIN")).put("/settings/admin-controls", json=_rules(default_credit_limit=90000)).status_code == 200
    stored = db["admin_controls"].find_one({"_id": "default"})
    stored.pop("_id")
    assert stored == {"operational_rules": {"auto_round_off": True, "default_credit_limit": 90000.0}}


def test_credit_summary_reports_the_limit_in_force(db, monkeypatch):
    monkeypatch.setattr(customers_module, "get_customer_repository", lambda: _Customers({"customer_id": "C1"}))
    monkeypatch.setattr(customers_module, "_ar_outstanding", lambda cid, doc: 40000.0)
    body = _client(_user("ADMIN")).get("/customers/C1/credit-summary").json()
    assert body["credit_limit"] == 150000.0
    assert body["ar_available"] == 110000.0
    assert body["limit_exceeded"] is False


# --- the ONE check: the till's CREDIT tender --------------------------------


@pytest.fixture()
def till(db, monkeypatch):
    """A Rs 3,00,000 order for customer C1 who already owes Rs 10,000."""

    def build(customer):
        coll = db["orders"]
        coll.insert_one({
            "order_id": "ORD-1", "order_number": "ORD-1", "store_id": "BV-PUN-01",
            "customer_id": customer["customer_id"], "status": "CONFIRMED",
            "grand_total": 300000.0, "amount_paid": 0.0, "balance_due": 300000.0,
            "payment_status": "UNPAID", "payments": [], "items": [],
            "created_at": "2026-10-08T10:00:00",
        })
        monkeypatch.setattr(orders_module, "get_order_repository", lambda: OrderRepository(coll))
        monkeypatch.setattr(orders_module, "get_customer_repository", lambda: _Customers(customer))
        monkeypatch.setattr(customers_module, "_ar_outstanding", lambda cid, doc: 10000.0)
        return coll

    return build


def _credit(amount):
    return asyncio.run(add_payment("ORD-1", PaymentCreate(method="CREDIT", amount=amount), current_user=_user("SALES_STAFF")))


def test_over_the_default_is_blocked_and_nothing_is_recorded(till):
    coll = till({"customer_id": "C1"})
    with pytest.raises(HTTPException) as exc:
        _credit(140001.0)  # 10,000 owed + 1,40,001 > 1,50,000
    assert exc.value.status_code == 400
    assert "Credit limit exceeded" in exc.value.detail
    assert coll.find_one({"order_id": "ORD-1"})["payments"] == []


def test_up_to_the_default_goes_through(till):
    coll = till({"customer_id": "C1", "credit_limit": 0})
    _credit(140000.0)  # exactly at the limit
    assert [p["method"] for p in coll.find_one({"order_id": "ORD-1"})["payments"]] == ["CREDIT"]


def test_a_higher_limit_of_their_own_lets_the_sale_through(till):
    coll = till({"customer_id": "C1", "credit_limit": 250000})
    _credit(200000.0)
    assert len(coll.find_one({"order_id": "ORD-1"})["payments"]) == 1


def test_a_lower_limit_of_their_own_blocks_under_the_default(till):
    till({"customer_id": "C1", "credit_limit": 15000})
    with pytest.raises(HTTPException) as exc:
        _credit(6000.0)
    assert exc.value.status_code == 400


def test_the_saved_default_is_the_one_the_till_enforces(till):
    till({"customer_id": "C1"})
    assert _client(_user("ADMIN")).put("/settings/admin-controls", json=_rules(default_credit_limit=12000)).status_code == 200
    with pytest.raises(HTTPException):
        _credit(3000.0)  # 10,000 + 3,000 > 12,000


# ===========================================================================
# 2. Auto-logout: one document for both writers and the reader
# ===========================================================================


@pytest.fixture()
def health(db, monkeypatch):
    import api.main as main

    monkeypatch.setattr(main, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(main, "get_db", lambda: _Db(db))

    def read():
        main._reset_auto_logout_cache()
        return main._get_auto_logout_policy()

    yield read
    main._reset_auto_logout_cache()


def test_admin_system_settings_save_is_what_health_reads(health):
    admin = _client(_user("ADMIN"))
    assert admin.put("/admin/system/settings", json={"auto_logout_minutes": 30}).status_code == 200
    assert health()["minutes"] == 30


def test_settings_system_save_shows_on_admin_system_settings(health):
    sa = _client(_user("SUPERADMIN"))
    assert sa.put("/settings/system", json={"auto_logout_minutes": 25}).status_code == 200
    assert health()["minutes"] == 25
    got = sa.get("/admin/system/settings").json()
    assert got["auto_logout_minutes"] == 25


def test_admin_system_settings_drops_the_duplicate_defaults(db):
    got = _client(_user("ADMIN")).get("/admin/system/settings").json()
    assert "low_stock_alert_enabled" not in got
    # No second auto-logout default that disagrees with the 15 /health enforces.
    assert "auto_logout_minutes" not in got
    # Round-off is a separate job - its keys are untouched.
    assert got["round_off_enabled"] is True and got["round_off_paise"] == 50


def _load_script():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "migrate_system_settings_one_id.py")
    spec = importlib.util.spec_from_file_location("migrate_system_settings_one_id", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_values_saved_under_the_old_id_are_kept(db, health):
    coll = db["system_settings"]
    coll.insert_one({"_id": "default", "auto_logout_minutes": 20})
    coll.insert_one({"_id": "system_settings", "auto_logout_minutes": 99, "auto_logout_warn_seconds": 45,
                     "round_off_paise": 100, "low_stock_alert_enabled": False})
    script = _load_script()

    dry = script.run(coll, apply=False)
    assert dry["copied"] == {"auto_logout_warn_seconds": 45, "round_off_paise": 100}
    assert dry["kept"] == {"auto_logout_minutes": 20}
    assert coll.find_one({"_id": "default"}) == {"_id": "default", "auto_logout_minutes": 20}

    script.run(coll, apply=True)
    one = coll.find_one({"_id": "default"})
    assert one == {"_id": "default", "auto_logout_minutes": 20, "auto_logout_warn_seconds": 45, "round_off_paise": 100}
    assert coll.find_one({"_id": "system_settings"}) is None
    assert health()["warn_seconds"] == 45 and health()["minutes"] == 20
    assert script.run(coll, apply=True)["copied"] == {}  # idempotent


# ===========================================================================
# 3. The removed duplicates stay removed
# ===========================================================================


def test_feature_toggles_no_longer_carry_eye_test_or_workshop():
    assert "eye-test-module" not in settings_module.DEFAULT_FEATURE_TOGGLES
    assert "workshop-module" not in settings_module.DEFAULT_FEATURE_TOGGLES


def test_approval_workflows_drop_the_discount_and_credit_rows(db):
    admin = _client(_user("ADMIN"))
    types = {w["type"] for w in admin.get("/settings/approval-workflows").json()["workflows"]}
    assert types == {"REFUND_APPROVAL", "PO_APPROVAL", "STOCK_ADJUSTMENT"}
    # A copy saved before the removal is not shown either.
    db["approval_workflows"].insert_one({"_id": "default", "workflows": [
        {"id": "wf-001", "type": "DISCOUNT_APPROVAL"}, {"id": "wf-002", "type": "REFUND_APPROVAL"},
        {"id": "wf-005", "type": "CREDIT_SALE"},
    ]})
    types = {w["type"] for w in admin.get("/settings/approval-workflows").json()["workflows"]}
    assert types == {"REFUND_APPROVAL"}


def test_the_inert_role_caps_copy_is_gone():
    from api.main import app
    from api.services.rbac_policy import POLICY

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/admin/discounts/role-caps" not in paths
    assert "/api/v1/admin/discounts/enforced-caps" in paths  # the one rule stays
    assert not [row for row in POLICY if row["path"] == "/api/v1/admin/discounts/role-caps"]
