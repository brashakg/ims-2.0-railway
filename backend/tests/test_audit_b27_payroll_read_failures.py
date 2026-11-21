"""
Batch b27, round 2 hunt: a payroll READ that fails is never a false "nothing"
=============================================================================
The payslip and salary-sheet reads this PR changed answered a failed or
missing database read as an ordinary empty answer (200, ``payslip: None`` /
``salaries: []``). The staff screen then said "No payslip generated yet" and
the Salary Sheet "No payroll run is saved for <month>" while the salary was
in fact saved. Now a read that could not be made answers 503 and the screens
say it could not be loaded.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_audit_b27_payroll_read_failures.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pymongo.errors import AutoReconnect  # noqa: E402

from strict_fakes import StrictDB  # noqa: E402

from api.routers import hr_self_service  # noqa: E402
from api.routers import payroll as payroll_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

EMP = "EMP-B27R"
STORE = "ST-B27R"
ADMIN = {"user_id": "admin-b27r", "username": "admin", "roles": ["ADMIN"],
         "store_ids": [STORE], "active_store_id": STORE}
STAFF = {"user_id": EMP, "username": "staff", "roles": ["SALES_STAFF"],
         "store_ids": [STORE], "active_store_id": STORE}


def _db() -> StrictDB:
    db = StrictDB()
    db.seed("users", [{"user_id": EMP, "full_name": "ZZ Read Failure"}])
    db.seed("payroll", [{
        "employee_id": EMP, "store_id": STORE, "month": 9, "year": 2026,
        "status": "APPROVED", "net_salary": 13247.0, "updated_at": "x",
        "breakdown": {"net_pay": 13247.0},
    }])
    return db


class _FailingReads(StrictDB):
    """Every payroll / payslip read fails the way a dropped Mongo link does."""

    def __init__(self, inner):
        self._inner = inner

    def get_collection(self, name):
        coll = self._inner.get_collection(name)
        if name not in ("payroll", "payslips"):
            return coll

        class _Coll:
            def __getattr__(self, attr):
                if attr in ("find", "find_one", "count_documents"):
                    def _boom(*a, **k):
                        raise AutoReconnect("connection reset")
                    return _boom
                return getattr(coll, attr)

        return _Coll()

    def __getattr__(self, attr):
        return getattr(self._inner, attr)


def _client(monkeypatch, db, user) -> TestClient:
    monkeypatch.setattr(payroll_router, "_get_db", lambda: db)
    monkeypatch.setattr(hr_self_service, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(payroll_router.router, prefix="/api/v1/payroll")
    app.include_router(hr_self_service.router, prefix="/api/v1/hr")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_positive_control_staff_see_their_approved_payslip(monkeypatch):
    r = _client(monkeypatch, _db(), STAFF).get("/api/v1/hr/me/payslip")
    assert r.status_code == 200, r.text
    assert r.json()["payslip"]["breakdown"]["net_pay"] == 13247.0


def test_staff_payslip_read_failure_is_503_not_no_payslip(monkeypatch):
    r = _client(monkeypatch, _FailingReads(_db()), STAFF).get("/api/v1/hr/me/payslip")
    assert r.status_code == 503, r.text
    assert "payslip" in r.json()["detail"].lower()


def test_staff_payslip_with_no_database_is_503_not_no_payslip(monkeypatch):
    r = _client(monkeypatch, None, STAFF).get("/api/v1/hr/me/payslip")
    assert r.status_code == 503, r.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/payroll/salary-sheet?month=9&year=2026",
        f"/api/v1/payroll/payslip/{EMP}",
        f"/api/v1/payroll/payslip/{EMP}/9/2026",
        # D83: the screen showed the built-in slabs as if they were stored.
        "/api/v1/payroll/pt-slabs",
        "/api/v1/payroll/pt-slabs/MH",
        # Salary Setup and Payroll Run: zeros / no rows read as "nothing saved".
        "/api/v1/payroll/config",
        "/api/v1/payroll/run/rows?month=9&year=2026",
        "/api/v1/payroll/registers/summary?month=9&year=2026",
    ],
)
def test_admin_payroll_reads_with_no_database_are_503_not_empty(monkeypatch, path):
    r = _client(monkeypatch, None, ADMIN).get(path)
    assert r.status_code == 503, r.text


def test_staff_with_no_database_is_still_refused_another_payslip_first(monkeypatch):
    """Authorization still runs before the database is touched."""
    r = _client(monkeypatch, None, STAFF).get("/api/v1/payroll/payslip/someone-else")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# The same staff door on a REAL Mongo database (raw pymongo Database, which
# refuses a truth test), the read failing inside pymongo itself.
# ``CI=1 IMS_TEST_MONGO_PORT=27084 IMS_TEST_MONGO_DB=<scratch>``; the test
# DROPS that database.
# ---------------------------------------------------------------------------
REAL_PORT = os.environ.get("IMS_TEST_MONGO_PORT") or ("27017" if os.environ.get("CI") else "")
REAL_DB = os.environ.get("IMS_TEST_MONGO_DB") or f"ims_test_b27_reads_{os.getpid()}"


@pytest.fixture
def real_db():
    if not REAL_PORT:
        pytest.skip("no local Mongo given (IMS_TEST_MONGO_PORT)")
    import pymongo

    client = pymongo.MongoClient("127.0.0.1", int(REAL_PORT), serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
    except Exception:  # pragma: no cover - environment
        pytest.skip("local Mongo not reachable")
    assert REAL_DB.startswith(("ims_test_", "ims_claude_")), REAL_DB
    client.drop_database(REAL_DB)
    db = client[REAL_DB]
    assert db.name == REAL_DB
    for name in ("users", "payroll"):
        db[name].insert_many(list(_db().get_collection(name).find({})))
    yield db
    client.drop_database(REAL_DB)
    client.close()


def test_native_staff_payslip_read_failure_is_503(monkeypatch, real_db):
    from pymongo.collection import Collection

    c = _client(monkeypatch, real_db, STAFF)
    ok = c.get("/api/v1/hr/me/payslip")
    assert ok.status_code == 200 and ok.json()["payslip"]["breakdown"]["net_pay"] == 13247.0

    real_find = Collection.find

    def find(self, *a, **k):
        if self.name == "payroll":
            raise AutoReconnect("connection reset")
        return real_find(self, *a, **k)

    monkeypatch.setattr(Collection, "find", find)
    r = c.get("/api/v1/hr/me/payslip")
    assert r.status_code == 503, r.text
