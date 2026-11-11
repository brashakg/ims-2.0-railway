"""
Codex whole-app audit, batch b09 (payroll): L3-02, L3-05, L3-06, L3-23
======================================================================
Each test drives the REAL payroll / self-service routers (or the finance P&L
payroll readers) over a strict in-memory Mongo double and asserts on what ended
up STORED, not only on the reply.

L3-02  a payroll DRAFT reduced the P&L, and a draft could be rerun (rewritten)
       inside a locked accounting month.
L3-05  the advance-history read answered 500 after the first advance existed
       (raw Mongo ``_id`` in the reply).
L3-06  a NEGATIVE advance recovery was accepted and raised net pay.
L3-23  the payslip was materialised once and served stale forever after the
       payroll row changed (rerun, approve, pay).

The frontend halves (L3-06 input, L3-07, L3-14, L3-24) are covered by vitest:
frontend/src/pages/hr/__tests__/auditB09Payroll.test.tsx.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_audit_b09_payroll.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from strict_fakes import StrictCollection, StrictDB  # noqa: E402

from api.routers import hr_self_service  # noqa: E402
from api.routers import payroll as payroll_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import _shared as finance_shared  # noqa: E402
from api.services.payroll_engine import compute_payroll  # noqa: E402


# ---------------------------------------------------------------------------
# A Mongo double that stamps ``_id`` on insert, as the real driver does. The
# strict fake leaves it off, which is exactly what hid L3-05 from every earlier
# test: the reply only breaks once the stored row carries an ObjectId.
# ---------------------------------------------------------------------------


class _MongoLikeCollection(StrictCollection):
    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())  # pymongo mutates the caller's dict
        return super().insert_one(doc)


class _MongoLikeDB(StrictDB):
    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = _MongoLikeCollection(name)
        return self._collections[name]


EMP = "EMP-B09"
ENTITY = "ENT-B09"
STORE = "ST-B09"
MONTH, YEAR = 10, 2026

ADMIN = {
    "user_id": "admin-b09",
    "username": "admin",
    "roles": ["ADMIN"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
STAFF = {
    "user_id": EMP,
    "username": "staff",
    "roles": ["SALES_STAFF"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}


def _db() -> _MongoLikeDB:
    db = _MongoLikeDB()
    db.seed("users", [{"user_id": EMP, "full_name": "ZZ Audit Staff"}])
    db.seed(
        "salary_config",
        [
            {
                "employee_id": EMP,
                "entity_id": ENTITY,
                "store_id": STORE,
                "basic": 15000,
                "hra": 5000,
                "tds_monthly": 100,
                "is_active": True,
            }
        ],
    )
    return db


def _client(db, monkeypatch, user=None) -> TestClient:
    monkeypatch.setattr(payroll_router, "_get_db", lambda: db)
    monkeypatch.setattr(hr_self_service, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(payroll_router.router, prefix="/api/v1/payroll")
    app.include_router(hr_self_service.router, prefix="/api/v1/hr")
    app.dependency_overrides[get_current_user] = lambda: (user or ADMIN)
    return TestClient(app)


def _run(c, **extra):
    body = {"month": MONTH, "year": YEAR, "entity_id": ENTITY, **extra}
    return c.post("/api/v1/payroll/run", json=body)


def _stored_row(db):
    return db.get_collection("payroll").find_one(
        {"employee_id": EMP, "month": MONTH, "year": YEAR}
    )


def _pnl_payroll(db):
    """Both P&L payroll readers, for October 2026."""
    rng = ("2026-10-01", "2026-10-31")
    return (
        finance_shared._payroll_cost(db, None, *rng),
        finance_shared._payroll_cost(db, STORE, *rng),
        finance_shared._payroll_by_store(db, *rng),
    )


# ===========================================================================
# L3-02 -- a draft is not a posting
# ===========================================================================


def test_l3_02_draft_payroll_does_not_reduce_pnl(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    row = _stored_row(db)
    assert row["status"] == "DRAFT"
    assert row["breakdown"]["ctc_cost"] > 0

    # A draft is a working calculation nobody has signed off: no P&L door may
    # count it (org-wide total, one store's total, the per-store table).
    assert _pnl_payroll(db) == (0.0, 0.0, {})


def test_l3_02_approved_and_paid_payroll_still_count(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    cost = _stored_row(db)["breakdown"]["ctc_cost"]
    scope = {"month": MONTH, "year": YEAR, "entity_id": ENTITY}

    assert c.post("/api/v1/payroll/approve", json=scope).json()["approved"] == 1
    assert _pnl_payroll(db) == (cost, cost, {STORE: cost})

    assert c.post("/api/v1/payroll/lock", json=scope).json()["locked"] == 1
    assert _stored_row(db)["status"] == "PAID"
    assert _pnl_payroll(db) == (cost, cost, {STORE: cost})


def test_l3_02_draft_rerun_refused_in_a_locked_month(monkeypatch):
    """The audit's exact move: draft with 3 unpaid days, month locked, rerun
    with 0 unpaid days. Approve/pay already answered 423; the rerun did not."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c, lwp_days={EMP: 3}).status_code == 200
    before = dict(_stored_row(db))
    db.get_collection("period_locks").insert_one({"month": MONTH, "year": YEAR})

    r = _run(c, lwp_days={EMP: 0})
    assert r.status_code == 423, r.text
    assert _stored_row(db) == before  # the stored draft is byte-for-byte unchanged


def test_l3_02_first_run_refused_in_a_locked_month(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    db.get_collection("period_locks").insert_one({"month": MONTH, "year": YEAR})

    r = _run(c)
    assert r.status_code == 423, r.text
    assert _stored_row(db) is None  # nothing was written into the closed month


def test_l3_02_preview_still_works_in_a_locked_month(monkeypatch):
    """A preview writes nothing, so the lock has nothing to protect it from."""
    db = _db()
    c = _client(db, monkeypatch)
    db.get_collection("period_locks").insert_one({"month": MONTH, "year": YEAR})

    r = _run(c, dry_run=True)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    assert _stored_row(db) is None


def test_l3_02_draft_rerun_still_allowed_in_an_open_month(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c, lwp_days={EMP: 3}).status_code == 200
    net_with_lwp = _stored_row(db)["net_salary"]

    assert _run(c, lwp_days={EMP: 0}).status_code == 200
    assert _stored_row(db)["net_salary"] > net_with_lwp
    assert db.get_collection("payroll").count_documents({}) == 1


# ===========================================================================
# L3-05 -- advance history after the first advance
# ===========================================================================


def test_l3_05_advance_history_readable_after_recording(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert c.get(f"/api/v1/payroll/advances/{EMP}").json()["total"] == 0

    r = c.post("/api/v1/payroll/advances", json={"employee_id": EMP, "amount": 500})
    assert r.status_code == 201, r.text
    stored = db.get_collection("salary_advances").find_one({"employee_id": EMP})
    assert isinstance(stored["_id"], ObjectId)  # what real Mongo stores

    r = c.get(f"/api/v1/payroll/advances/{EMP}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["advances"][0]["amount"] == 500
    assert body["advances"][0]["advance_id"] == stored["advance_id"]
    assert "_id" not in body["advances"][0]

    # The employee's own read (self-or-admin) goes through the same route.
    c_self = _client(db, monkeypatch, user=STAFF)
    assert c_self.get(f"/api/v1/payroll/advances/{EMP}").status_code == 200


def test_l3_05_incentive_summary_readable_when_a_row_exists(monkeypatch):
    """Same defect, sibling route in the same file: a stored row went back raw."""
    db = _db()
    db.get_collection("incentives").insert_one(
        {"staff_id": EMP, "month": MONTH, "year": YEAR, "amount": 750}
    )
    c = _client(db, monkeypatch)
    r = c.get(f"/api/v1/payroll/incentive-summary/{EMP}/{MONTH}/{YEAR}")
    assert r.status_code == 200, r.text
    assert r.json()["incentive"]["amount"] == 750
    assert "_id" not in r.json()["incentive"]


# ===========================================================================
# L3-06 -- a recovery is never negative
# ===========================================================================


@pytest.mark.parametrize("dry_run", [True, False])
def test_l3_06_negative_advance_recovery_refused(monkeypatch, dry_run):
    db = _db()
    c = _client(db, monkeypatch)
    r = _run(c, advances={EMP: -500}, dry_run=dry_run)
    assert r.status_code == 422, r.text
    assert "negative" in r.text.lower()
    assert _stored_row(db) is None


def test_l3_06_engine_refuses_a_negative_recovery():
    """The engine is the shared rule: no caller can use it to inflate pay."""
    cfg = {"employee_id": EMP, "basic": 15000, "hra": 5000}
    with pytest.raises(ValueError):
        compute_payroll(cfg, month=MONTH, year=YEAR, advance_recovery=-500)
    with pytest.raises(ValueError):
        compute_payroll(cfg, month=MONTH, year=YEAR, advance_recovery=float("nan"))


def test_l3_06_positive_recovery_still_lowers_net_pay(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    plain = _run(c, dry_run=True).json()["rows"][0]["net_salary"]
    r = _run(c, advances={EMP: 500})
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["net_salary"] == plain - 500
    row = _stored_row(db)
    assert row["advance_deduction"] == 500
    assert row["net_salary"] == plain - 500


# ===========================================================================
# L3-23 -- the payslip is read from the payroll row, every time
# ===========================================================================


def _payslip_doors(db, monkeypatch):
    """Net pay + status from every payslip door for the same payroll row."""
    admin = _client(db, monkeypatch)
    by_month = admin.get(f"/api/v1/payroll/payslip/{EMP}/{MONTH}/{YEAR}").json()["payslip"]
    latest = admin.get(f"/api/v1/payroll/payslip/{EMP}").json()["payslip"]
    printed = admin.get(f"/api/v1/payroll/payslip/{EMP}/{MONTH}/{YEAR}/print")
    assert printed.status_code == 200
    mine = _client(db, monkeypatch, user=STAFF).get("/api/v1/hr/me/payslip").json()["payslip"]
    return [(p["breakdown"]["net_pay"], p["status"]) for p in (by_month, latest, mine)]


def test_l3_23_payslip_follows_a_draft_rerun_approval_and_payment(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    net = _stored_row(db)["net_salary"]
    assert _payslip_doors(db, monkeypatch) == [(net, "DRAFT")] * 3

    # Rerun the draft with a 100 recovery (the audit's move).
    assert _run(c, advances={EMP: 100}).status_code == 200
    assert _stored_row(db)["net_salary"] == net - 100
    assert _payslip_doors(db, monkeypatch) == [(net - 100, "DRAFT")] * 3

    scope = {"month": MONTH, "year": YEAR, "entity_id": ENTITY}
    assert c.post("/api/v1/payroll/approve", json=scope).status_code == 200
    assert _payslip_doors(db, monkeypatch) == [(net - 100, "APPROVED")] * 3
    assert c.post("/api/v1/payroll/lock", json=scope).status_code == 200
    assert _payslip_doors(db, monkeypatch) == [(net - 100, "PAID")] * 3


def test_l3_23_copy_materialised_by_the_old_code_is_not_served(monkeypatch):
    """Databases already hold payslip copies the old code wrote on first read.
    They are not rewritten; they are simply never preferred to the payroll row."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    net = _stored_row(db)["net_salary"]
    db.get_collection("payslips").insert_one(
        {
            "payslip_id": "old-copy",
            "employee_id": EMP,
            "month": MONTH,
            "year": YEAR,
            "status": "DRAFT",
            "breakdown": {"net_pay": net + 999, "gross_salary": 0, "total_deductions": 0},
        }
    )
    assert _payslip_doors(db, monkeypatch) == [(net, "DRAFT")] * 3


def test_l3_23_reading_a_payslip_writes_nothing(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    _payslip_doors(db, monkeypatch)
    assert db.get_collection("payslips").count_documents({}) == 0


def test_l3_23_latest_payslip_is_the_newest_month(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200  # October 2026
    r = c.post(
        "/api/v1/payroll/run",
        json={"month": 11, "year": YEAR, "entity_id": ENTITY, "advances": {EMP: 250}},
    )
    assert r.status_code == 200
    latest = c.get(f"/api/v1/payroll/payslip/{EMP}").json()["payslip"]
    assert (latest["month"], latest["year"]) == (11, YEAR)
    assert latest["breakdown"]["advance_deduction"] == 250


def test_l3_23_payslip_with_no_payroll_row_keeps_its_old_sources(monkeypatch):
    """Earlier behaviour kept: a month that only the old salary writer or an
    already-materialised payslip knows about is still shown."""
    db = _db()
    db.get_collection("salary_records").insert_one(
        {
            "salary_record_id": "SR-OLD",
            "employee_id": EMP,
            "month": 3,
            "year": 2025,
            "breakdown": {"net_pay": 11111},
        }
    )
    db.get_collection("payslips").insert_one(
        {
            "payslip_id": "PS-OLD",
            "employee_id": EMP,
            "month": 2,
            "year": 2025,
            "breakdown": {"net_pay": 22222},
        }
    )
    c = _client(db, monkeypatch)
    assert c.get(f"/api/v1/payroll/payslip/{EMP}/3/2025").json()["payslip"]["breakdown"]["net_pay"] == 11111
    assert c.get(f"/api/v1/payroll/payslip/{EMP}/2/2025").json()["payslip"]["breakdown"]["net_pay"] == 22222
    assert c.get(f"/api/v1/payroll/payslip/{EMP}/1/2025").json()["payslip"] is None
    # No payroll row at all: the latest read still finds the newest materialised
    # one (March, written by the month read above -- the legacy path as it was).
    assert c.get(f"/api/v1/payroll/payslip/{EMP}").json()["payslip"]["breakdown"]["net_pay"] == 11111
    mine = _client(db, monkeypatch, user=STAFF).get("/api/v1/hr/me/payslip").json()
    assert mine["payslip"]["breakdown"]["net_pay"] == 11111


def test_l3_23_a_newer_month_without_a_payroll_row_is_still_the_latest(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200  # October 2026 run row
    db.get_collection("payslips").insert_one(
        {
            "payslip_id": "PS-DEC",
            "employee_id": EMP,
            "month": 12,
            "year": YEAR,
            "breakdown": {"net_pay": 33333},
        }
    )
    assert c.get(f"/api/v1/payroll/payslip/{EMP}").json()["payslip"]["payslip_id"] == "PS-DEC"


def test_l3_23_another_employees_payslip_stays_refused(monkeypatch):
    """Salary stays ADMIN/SUPERADMIN only for anyone but yourself."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    other = {**STAFF, "user_id": "someone-else"}
    c_other = _client(db, monkeypatch, user=other)
    assert c_other.get(f"/api/v1/payroll/payslip/{EMP}/{MONTH}/{YEAR}").status_code == 403
    assert c_other.get(f"/api/v1/payroll/payslip/{EMP}").status_code == 403
    assert c_other.get(f"/api/v1/payroll/advances/{EMP}").status_code == 403
    assert c_other.get("/api/v1/hr/me/payslip").json() == {"payslip": None}
