"""
Codex whole-app audit, batch b09 (payroll): L3-02, L3-05, L3-06, L3-23
======================================================================
Each test drives the REAL payroll / self-service routers (or the finance P&L
payroll readers) over a strict in-memory Mongo double and asserts on what ended
up STORED, not only on the reply.

L3-02  an unapproved payroll DRAFT was a cost in the P&L, and it could be
       rerun (rewritten) inside a locked accounting month, which moved that
       month's P&L. Decided 2026-11-11: a month's salary is a cost once the
       run is APPROVED (APPROVED and PAID count, a DRAFT never does); the
       Tally salary voucher and the PF file export the same rows; a month
       cannot be locked while its payroll is still a draft, and a month whose
       payroll has not been run for someone with an active salary setup locks
       only when the admin says "lock anyway".
L3-05  the advance-history read answered 500 after the first advance existed
       (raw Mongo ``_id`` in the reply).
L3-06  a NEGATIVE advance recovery was accepted and raised net pay.
L3-23  the payslip was materialised once and served stale forever after the
       payroll row changed (rerun, approve, pay). Decided 2026-11-11: staff
       see a payslip only once it is APPROVED or PAID (a draft answers "not
       ready"); an administrator preparing payroll still sees the draft; every
       payslip carries its status.

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

import asyncio  # noqa: E402
from datetime import datetime  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from api.routers import hr_self_service  # noqa: E402
from api.routers import payroll as payroll_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import _shared as finance_shared  # noqa: E402
from api.routers.finance import period_lock  # noqa: E402
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
# L3-02 -- a draft is not a cost; a locked month cannot be rewritten; a month
#          with a draft cannot be locked
# ===========================================================================

SCOPE = {"month": MONTH, "year": YEAR, "entity_id": ENTITY}
OCTOBER = f"?month={MONTH}&year={YEAR}&entity_id={ENTITY}"


def _lock(db):
    db.get_collection("period_locks").insert_one({"month": MONTH, "year": YEAR})


def _approve(c):
    r = c.post("/api/v1/payroll/approve", json=SCOPE)
    assert r.status_code == 200, r.text


def _pay(c):
    r = c.post("/api/v1/payroll/lock", json=SCOPE)
    assert r.status_code == 200, r.text


def _lock_month_through_the_route(db, monkeypatch, month=MONTH, year=YEAR, **said):
    """POST /finance/period-lock, as the Finance screen calls it. Returns
    (status, detail). ``without_payroll=True`` is the admin's "lock anyway"."""
    monkeypatch.setattr(period_lock, "_get_db", lambda: db)
    monkeypatch.setattr("api.utils.ist.now_ist_naive", lambda: datetime(2026, 11, 12, 12))
    try:
        asyncio.run(period_lock.lock_period(month, year, ADMIN, **said))
        return 200, ""
    except HTTPException as exc:
        return exc.status_code, str(exc.detail)


def test_l3_02_a_draft_is_not_a_pnl_cost_until_it_is_approved(monkeypatch):
    """The audit's first half: the October DRAFT already reduced the P&L."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c, lwp_days={EMP: 3}).status_code == 200
    cost = _stored_row(db)["breakdown"]["ctc_cost"]
    assert cost > 0 and _stored_row(db)["status"] == "DRAFT"
    assert _pnl_payroll(db) == (0.0, 0.0, {})  # a draft is nobody's cost yet

    # Rerunning the draft moves the draft, not the P&L.
    assert _run(c, lwp_days={EMP: 0}).status_code == 200
    cost = _stored_row(db)["breakdown"]["ctc_cost"]
    assert _pnl_payroll(db) == (0.0, 0.0, {})

    _approve(c)
    assert _stored_row(db)["status"] == "APPROVED"
    assert _pnl_payroll(db) == (cost, cost, {STORE: cost})
    _pay(c)
    assert _stored_row(db)["status"] == "PAID"
    assert _pnl_payroll(db) == (cost, cost, {STORE: cost})  # counted once, not twice


def test_l3_02_tally_voucher_and_pf_file_export_only_approved_or_paid(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200

    # All draft: nothing to send to Tally, and the PF file has no employee.
    jv = c.get("/api/v1/payroll/tally/salary-jv" + OCTOBER)
    assert jv.status_code == 404 and "Approve" in jv.json()["detail"]
    ecr = c.get("/api/v1/payroll/registers/pf-ecr" + OCTOBER)
    assert ecr.status_code == 200 and ecr.text.strip() == ""

    _approve(c)
    approved = _stored_row(db)
    # A second employee whose row is still a DRAFT in the same month + entity.
    draft = {
        **{k: v for k, v in approved.items() if k != "_id"},
        "payroll_id": "PR-DRAFT",
        "employee_id": "EMP-DRAFT",
        "employee_name": "ZZ Draft Only",
        "status": "DRAFT",
    }
    db.get_collection("payroll").insert_one(draft)

    for _ in ("APPROVED", "PAID"):
        jv = c.get("/api/v1/payroll/tally/salary-jv" + OCTOBER)
        assert jv.status_code == 200, jv.text
        # Salary Payable is ONE employee's net pay, not two.
        assert f"<AMOUNT>{approved['net_salary']:.2f}</AMOUNT>" in jv.text
        assert f"<AMOUNT>{2 * approved['net_salary']:.2f}</AMOUNT>" not in jv.text
        ecr = c.get("/api/v1/payroll/registers/pf-ecr" + OCTOBER)
        assert ecr.status_code == 200
        assert "ZZ Audit Staff" in ecr.text and "ZZ Draft Only" not in ecr.text
        assert len(ecr.text.strip().splitlines()) == 1
        _pay(c)  # second pass: the same row, now PAID
    assert _stored_row(db)["status"] == "PAID"

    # The preparation screen's register still lists the draft for the admin.
    rows = c.get("/api/v1/payroll/run/rows" + OCTOBER).json()["rows"]
    assert sorted(r["status"] for r in rows) == ["DRAFT", "PAID"]


def test_l3_02_a_month_cannot_be_locked_while_its_payroll_is_a_draft(monkeypatch):
    """Approve answers 423 in a locked month and nothing unlocks one, so a
    draft locked in would never become a cost. The lock is refused instead."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200

    status, detail = _lock_month_through_the_route(db, monkeypatch)
    assert status == 409, detail
    assert "draft" in detail and "Approve" in detail and STORE in detail
    assert db.get_collection("period_locks").count_documents({}) == 0

    _approve(c)
    assert _lock_month_through_the_route(db, monkeypatch) == (200, "")
    assert db.get_collection("period_locks").count_documents(
        {"month": MONTH, "year": YEAR}
    ) == 1
    cost = _stored_row(db)["breakdown"]["ctc_cost"]
    assert _pnl_payroll(db) == (cost, cost, {STORE: cost})  # the closed month has its wages


def test_l3_02_a_month_whose_payroll_was_not_run_is_not_locked_silently(monkeypatch):
    """The likelier order of events: the books are closed in the first days of
    the next month and payroll is run a few days later. A saving run answers
    423 in a locked month, so the lock would leave October with no wage cost
    for good. It is refused until the admin runs payroll or says "lock anyway"."""
    db = _db()
    c = _client(db, monkeypatch)

    status, detail = _lock_month_through_the_route(db, monkeypatch)
    assert status == 409, detail
    assert "PAYROLL_NOT_RUN" in detail and "1 employee" in detail and STORE in detail
    assert db.get_collection("period_locks").count_documents({}) == 0

    # The month is still open: payroll is run and approved, then it locks
    # with no question asked and the closed month carries its wages.
    assert _run(c).status_code == 200
    _approve(c)
    assert _lock_month_through_the_route(db, monkeypatch) == (200, "")
    cost = _stored_row(db)["breakdown"]["ctc_cost"]
    assert _pnl_payroll(db) == (cost, cost, {STORE: cost})
    assert "without_payroll_for" not in db.get_collection("period_locks").find_one({})


def test_l3_02_one_shop_not_run_stops_the_lock_until_the_admin_says_so(monkeypatch):
    """Shop A is run and approved, shop B was never run: the lock is
    chain-wide, so shop B's wages would be the ones lost."""
    db = _db()
    db.get_collection("salary_config").insert_one(
        {"employee_id": "EMP-B", "entity_id": "ENT-B", "store_id": "ST-B",
         "basic": 12000, "is_active": True}
    )
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200  # ENTITY only
    _approve(c)

    status, detail = _lock_month_through_the_route(db, monkeypatch)
    assert status == 409, detail
    assert "PAYROLL_NOT_RUN" in detail and "1 employee" in detail
    assert "ST-B" in detail and STORE not in detail
    assert db.get_collection("period_locks").count_documents({}) == 0

    # "Lock anyway": the admin was told, and the lock row keeps the count.
    assert _lock_month_through_the_route(db, monkeypatch, without_payroll=True) == (200, "")
    assert db.get_collection("period_locks").find_one({})["without_payroll_for"] == 1
    r = c.post("/api/v1/payroll/run", json={"month": MONTH, "year": YEAR, "entity_id": "ENT-B"})
    assert r.status_code == 423  # what the admin was warned about


def test_l3_02_lock_anyway_never_overrides_a_draft(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200

    status, detail = _lock_month_through_the_route(db, monkeypatch, without_payroll=True)
    assert status == 409 and "draft" in detail
    assert db.get_collection("period_locks").count_documents({}) == 0


def test_l3_02_a_month_with_nobody_on_payroll_locks_as_before(monkeypatch):
    """No active salary setup: nothing to run, so nothing to ask."""
    db = _db()
    db.get_collection("salary_config").update_one(
        {"employee_id": EMP}, {"$set": {"is_active": False}}
    )
    assert _lock_month_through_the_route(db, monkeypatch, month=9) == (200, "")
    assert "without_payroll_for" not in db.get_collection("period_locks").find_one({})


def test_l3_02_draft_rerun_refused_in_a_locked_month(monkeypatch):
    """The audit's exact move: draft with 3 unpaid days, month locked, rerun
    with 0 unpaid days. Approve/pay already answered 423; the rerun answered
    200 and moved the locked month's P&L. (A month can no longer be locked
    over a draft; a database that already holds one is seeded directly.)"""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c, lwp_days={EMP: 3}).status_code == 200
    before = dict(_stored_row(db))
    _lock(db)

    r = _run(c, lwp_days={EMP: 0})
    assert r.status_code == 423, r.text
    assert _stored_row(db) == before  # the stored draft is byte-for-byte unchanged
    assert _pnl_payroll(db) == (0.0, 0.0, {})  # the closed P&L did not move


def test_l3_02_first_run_refused_in_a_locked_month(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    _lock(db)

    r = _run(c)
    assert r.status_code == 423, r.text
    assert _stored_row(db) is None  # nothing was written into the closed month


def test_l3_02_preview_still_works_in_a_locked_month(monkeypatch):
    """A preview writes nothing, so the lock has nothing to protect it from."""
    db = _db()
    c = _client(db, monkeypatch)
    _lock(db)

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
# L3-23 -- the payslip is read from the payroll row, every time; staff see it
#          once it is APPROVED or PAID; an administrator also sees the draft
# ===========================================================================

BY_MONTH = f"/api/v1/payroll/payslip/{EMP}/{MONTH}/{YEAR}"
LATEST = f"/api/v1/payroll/payslip/{EMP}"
PRINT = BY_MONTH + "/print"
MINE = "/api/v1/hr/me/payslip"
NOT_READY = {"payslip": None, "not_ready": {"month": MONTH, "year": YEAR}}


def _seen(reply):
    p = reply.json()["payslip"]
    return (p["breakdown"]["net_pay"], p["status"])


def _admin_doors(db, monkeypatch):
    """(net pay, status) from every payslip door an ADMINISTRATOR uses
    (HR > Payroll > Payslips, the latest read) plus the printed status."""
    admin = _client(db, monkeypatch)
    printed = admin.get(PRINT)
    assert printed.status_code == 200
    return [_seen(admin.get(BY_MONTH)), _seen(admin.get(LATEST))], printed.text


def _staff_doors(db, monkeypatch):
    """Every door the EMPLOYEE has to their own payslip: My Work, the two
    self-or-admin reads, and the print."""
    staff = _client(db, monkeypatch, user=STAFF)
    return [staff.get(MINE), staff.get(BY_MONTH), staff.get(LATEST)], staff.get(PRINT)


def test_l3_23_payslip_follows_a_draft_rerun_approval_and_payment(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    net = _stored_row(db)["net_salary"]
    doors, printed = _admin_doors(db, monkeypatch)
    assert doors == [(net, "DRAFT")] * 2
    assert "Draft - not yet approved" in printed

    # Rerun the draft with a 100 recovery (the audit's move).
    assert _run(c, advances={EMP: 100}).status_code == 200
    assert _stored_row(db)["net_salary"] == net - 100
    assert _admin_doors(db, monkeypatch)[0] == [(net - 100, "DRAFT")] * 2

    for move, status, label in ((_approve, "APPROVED", "Approved"), (_pay, "PAID", "Paid")):
        move(c)
        doors, printed = _admin_doors(db, monkeypatch)
        assert doors == [(net - 100, status)] * 2
        assert label in printed and "Draft" not in printed
        replies, staff_print = _staff_doors(db, monkeypatch)
        assert [_seen(r) for r in replies] == [(net - 100, status)] * 3
        assert all("not_ready" not in r.json() for r in replies)
        assert staff_print.status_code == 200 and label in staff_print.text


def test_l3_23_staff_do_not_see_a_draft_payslip_at_any_door(monkeypatch):
    """A draft is the administrator's working figure. The employee is told the
    month is not ready -- and is given no figure -- on My Work, on the two
    self-or-admin reads and on the print."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    net = _stored_row(db)["net_salary"]

    replies, staff_print = _staff_doors(db, monkeypatch)
    assert [r.json() for r in replies] == [NOT_READY] * 3
    assert staff_print.status_code == 404
    assert "not ready" in staff_print.json()["detail"]
    assert str(int(net)) not in "".join(r.text for r in replies) + staff_print.text

    # An ADMIN on My Work is on the staff door too: no draft there either.
    admin_as_staff = _client(db, monkeypatch, user={**ADMIN, "user_id": EMP})
    assert admin_as_staff.get(MINE).json() == NOT_READY
    # ...but preparing payroll (HR > Payroll > Payslips) they see it, as Draft.
    assert _admin_doors(db, monkeypatch)[0] == [(net, "DRAFT")] * 2


def test_l3_23_staff_keep_their_last_approved_payslip_while_the_next_is_a_draft(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200  # October 2026
    _approve(c)
    october_net = _stored_row(db)["net_salary"]
    november = {"month": 11, "year": YEAR, "entity_id": ENTITY, "advances": {EMP: 250}}
    assert c.post("/api/v1/payroll/run", json=november).status_code == 200  # a DRAFT

    staff = _client(db, monkeypatch, user=STAFF)
    for door in (MINE, LATEST):
        body = staff.get(door).json()
        assert body["not_ready"] == {"month": 11, "year": YEAR}
        assert (body["payslip"]["month"], body["payslip"]["status"]) == (MONTH, "APPROVED")
        assert body["payslip"]["breakdown"]["net_pay"] == october_net
    assert staff.get(f"/api/v1/payroll/payslip/{EMP}/11/{YEAR}").json() == {
        "payslip": None,
        "not_ready": {"month": 11, "year": YEAR},
    }
    # The administrator's latest read is the November draft.
    latest = c.get(LATEST).json()
    assert (latest["payslip"]["month"], latest["payslip"]["status"]) == (11, "DRAFT")
    assert "not_ready" not in latest


def test_l3_23_copy_materialised_by_the_old_code_is_not_served(monkeypatch):
    """Databases already hold payslip copies the old code wrote on first read
    (of a DRAFT too). They are not rewritten; they are never preferred to the
    payroll row, and never shown to staff in place of a withheld draft."""
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
    assert _admin_doors(db, monkeypatch)[0] == [(net, "DRAFT")] * 2
    replies, staff_print = _staff_doors(db, monkeypatch)
    assert [r.json() for r in replies] == [NOT_READY] * 3
    assert staff_print.status_code == 404


def test_l3_23_reading_a_payslip_writes_nothing(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    assert _run(c).status_code == 200
    before = dict(_stored_row(db))
    _admin_doors(db, monkeypatch)
    _staff_doors(db, monkeypatch)
    assert db.get_collection("payslips").count_documents({}) == 0
    assert _stored_row(db) == before


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
    # A month the run engine never computed has no approval step: the staff
    # door shows it as before.
    mine = _client(db, monkeypatch, user=STAFF).get("/api/v1/hr/me/payslip").json()
    assert mine["payslip"]["breakdown"]["net_pay"] == 11111
    assert "not_ready" not in mine


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
