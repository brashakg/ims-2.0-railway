"""
Decision D15 (L3-02 follow-up): an administrator can DISCARD a payroll draft
============================================================================
Why it exists: a month cannot be locked while a payroll row is still a DRAFT
(L3-02), and a draft is rewritten only by rerunning payroll, which covers
ACTIVE salary setups only. So a draft whose salary setup was switched off
afterwards could neither be rerun nor leave the month lockable: the only way
out was to approve a figure known to be wrong. A draft is never a cost (L3-02),
so removing one cannot lose money.

POST /api/v1/payroll/discard-draft, ADMIN / SUPERADMIN only:
  * removes DRAFT rows only: one employee's, or every draft of one shop, for
    one month; an APPROVED or PAID row is refused in plain words;
  * never in a locked month;
  * writes an audit-log row: who discarded what (employee, month, net figure).

Every test drives the REAL routers over the strict in-memory Mongo double of
the L3-02 tests and asserts on what is STORED, not only on the reply.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_audit_b27_payroll_discard_draft.py -q
"""

from __future__ import annotations

import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import test_audit_b09_payroll as b09  # noqa: E402  (the L3-02 fixtures)
from test_audit_b09_payroll import (  # noqa: E402
    ADMIN,
    EMP,
    ENTITY,
    MONTH,
    STAFF,
    STORE,
    YEAR,
)

from api.routers import payroll as payroll_router  # noqa: E402
from api.services import rbac_policy  # noqa: E402
from api.services.capabilities import capability_for, capability_roles  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402

DISCARD = "/api/v1/payroll/discard-draft"
ONE = {"month": MONTH, "year": YEAR, "employee_id": EMP}
SHOP = {"month": MONTH, "year": YEAR, "store_id": STORE}
EMP2 = "EMP-B09-2"


def _client(db, monkeypatch, user=None):
    """The L3-02 client, with the audit log kept in the SAME database."""
    monkeypatch.setattr(
        payroll_router,
        "get_audit_repository",
        lambda: AuditRepository(db.get_collection("audit_logs")),
        raising=False,
    )
    return b09._client(db, monkeypatch, user=user)


def _rows(db, **query):
    return list(db.get_collection("payroll").find(query))


def _audits(db):
    return list(db.get_collection("audit_logs").find({}))


def _second_employee(db, store=STORE, entity=ENTITY, emp=EMP2):
    db.get_collection("users").insert_one({"user_id": emp, "full_name": "ZZ Second Staff"})
    db.get_collection("salary_config").insert_one(
        {"employee_id": emp, "entity_id": entity, "store_id": store,
         "basic": 12000, "is_active": True}
    )


def _drafted(monkeypatch):
    db = b09._db()
    c = _client(db, monkeypatch)
    assert b09._run(c).status_code == 200
    assert b09._stored_row(db)["status"] == "DRAFT"
    return db, c


# ---------------------------------------------------------------------------
# It removes the draft, and only the draft
# ---------------------------------------------------------------------------


def test_discard_removes_only_that_employees_draft(monkeypatch):
    db = b09._db()
    c = _client(db, monkeypatch)
    assert b09._run(c).status_code == 200
    b09._approve(c)  # EMP is APPROVED
    _second_employee(db)
    assert b09._run(c).status_code == 200  # EMP skipped, EMP2 drafted
    # The same employee's draft in ANOTHER month is not this request's business.
    other_month = {**{k: v for k, v in _rows(db, employee_id=EMP2)[0].items() if k != "_id"},
                   "payroll_id": "PR-SEPT", "month": 9}
    db.get_collection("payroll").insert_one(other_month)
    approved_before = copy.deepcopy(b09._stored_row(db))

    r = c.post(DISCARD, json={**ONE, "employee_id": EMP2})

    assert r.status_code == 200, r.text
    assert r.json()["discarded"] == 1
    assert _rows(db, employee_id=EMP2, month=MONTH, year=YEAR) == []
    assert b09._stored_row(db) == approved_before  # the approved row, untouched
    assert [x["payroll_id"] for x in _rows(db, employee_id=EMP2)] == ["PR-SEPT"]


def test_discard_all_drafts_of_one_shop_leaves_other_shops_and_approved_rows(monkeypatch):
    db = b09._db()
    c = _client(db, monkeypatch)
    assert b09._run(c).status_code == 200
    b09._approve(c)  # EMP (STORE) is APPROVED
    _second_employee(db)  # STORE, will be a draft
    _second_employee(db, store="ST-OTHER", emp="EMP-OTHER")  # another shop's draft
    assert b09._run(c).status_code == 200

    r = c.post(DISCARD, json=SHOP)

    assert r.status_code == 200, r.text
    assert r.json()["discarded"] == 1
    left = {(x["employee_id"], x["status"]) for x in _rows(db, month=MONTH, year=YEAR)}
    assert left == {(EMP, "APPROVED"), ("EMP-OTHER", "DRAFT")}


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["APPROVED", "PAID"])
@pytest.mark.parametrize("scope", [ONE, SHOP])
def test_an_approved_or_paid_row_is_never_discarded(monkeypatch, status, scope):
    db, c = _drafted(monkeypatch)
    b09._approve(c)
    if status == "PAID":
        b09._pay(c)
    before = copy.deepcopy(b09._stored_row(db))
    assert before["status"] == status

    r = c.post(DISCARD, json=scope)

    assert r.status_code == 409, r.text
    assert status in r.json()["detail"] and "draft" in r.json()["detail"]
    assert b09._stored_row(db) == before
    assert _audits(db) == []


@pytest.mark.parametrize(
    "roles", [["SALES_STAFF"], ["STORE_MANAGER"], ["AREA_MANAGER"], ["ACCOUNTANT"]]
)
def test_only_an_administrator_can_discard(monkeypatch, roles):
    db, _ = _drafted(monkeypatch)
    before = copy.deepcopy(b09._stored_row(db))
    # Even their OWN draft: discarding is preparation, not self-service.
    someone = _client(db, monkeypatch, user={**STAFF, "roles": roles})

    r = someone.post(DISCARD, json=ONE)

    assert r.status_code == 403, r.text
    assert b09._stored_row(db) == before
    assert _audits(db) == []


def test_superadmin_can_discard(monkeypatch):
    db, _ = _drafted(monkeypatch)
    boss = _client(db, monkeypatch, user={**ADMIN, "user_id": "boss", "roles": ["SUPERADMIN"]})
    assert boss.post(DISCARD, json=ONE).status_code == 200
    assert b09._stored_row(db) is None


def test_a_locked_month_is_refused(monkeypatch):
    """A draft left in a month that was locked before the lock rule existed."""
    db, c = _drafted(monkeypatch)
    before = copy.deepcopy(b09._stored_row(db))
    b09._lock(db)

    r = c.post(DISCARD, json=ONE)

    assert r.status_code == 423, r.text
    assert b09._stored_row(db) == before
    assert _audits(db) == []


@pytest.mark.parametrize(
    "body",
    [
        {"month": MONTH, "year": YEAR},  # whose?
        {"month": MONTH, "year": YEAR, "employee_id": EMP, "store_id": STORE},
        {"month": MONTH, "year": YEAR, "employee_id": ""},
        {"month": 13, "year": YEAR, "employee_id": EMP},
    ],
)
def test_the_request_must_name_one_employee_or_one_shop(monkeypatch, body):
    db, c = _drafted(monkeypatch)
    assert c.post(DISCARD, json=body).status_code == 422
    assert b09._stored_row(db)["status"] == "DRAFT"


def test_nothing_to_discard_is_said_plainly(monkeypatch):
    db, c = _drafted(monkeypatch)
    r = c.post(DISCARD, json={**ONE, "employee_id": "EMP-NOBODY"})
    assert r.status_code == 404 and "no payroll draft" in r.json()["detail"].lower()
    assert b09._stored_row(db)["status"] == "DRAFT"
    assert _audits(db) == []


# ---------------------------------------------------------------------------
# The reason it exists: the month can be locked afterwards
# ---------------------------------------------------------------------------


def test_month_locks_without_the_question_once_the_stuck_draft_is_discarded(monkeypatch):
    """The dead end: a draft was saved, then that salary setup was switched
    off. A rerun no longer covers the employee, and the draft stops the lock."""
    db, c = _drafted(monkeypatch)
    db.get_collection("salary_config").update_one(
        {"employee_id": EMP}, {"$set": {"is_active": False}}
    )
    stuck = copy.deepcopy(b09._stored_row(db))
    rerun = b09._run(c)
    assert rerun.status_code == 200 and rerun.json()["count"] == 0
    assert b09._stored_row(db) == stuck  # the rerun cannot reach it
    for said in ({}, {"without_payroll": True}):  # "lock anyway" does not help
        status, detail = b09._lock_month_through_the_route(db, monkeypatch, **said)
        assert status == 409 and "draft" in detail
    assert "discard" in detail.lower()  # the lock says there is a way out

    assert c.post(DISCARD, json=ONE).status_code == 200

    # Nobody else is unrun, so the month locks with no "lock anyway" question.
    assert b09._lock_month_through_the_route(db, monkeypatch) == (200, "")
    lock = db.get_collection("period_locks").find_one({"month": MONTH, "year": YEAR})
    assert lock and "without_payroll_for" not in lock
    assert b09._pnl_payroll(db) == (0.0, 0.0, {})  # a draft never was a cost


def test_the_lock_still_asks_when_the_discarded_employee_is_still_on_payroll(monkeypatch):
    """Discarding is not a way round the 'payroll not run' question."""
    db, c = _drafted(monkeypatch)
    assert c.post(DISCARD, json=ONE).status_code == 200
    status, detail = b09._lock_month_through_the_route(db, monkeypatch)
    assert status == 409 and "PAYROLL_NOT_RUN" in detail
    assert db.get_collection("period_locks").count_documents({}) == 0
    # ... and the draft can simply be run again in the open month.
    assert b09._run(c).status_code == 200
    assert b09._stored_row(db)["status"] == "DRAFT"


# ---------------------------------------------------------------------------
# The trace
# ---------------------------------------------------------------------------


def test_the_audit_log_says_who_discarded_what(monkeypatch):
    db, c = _drafted(monkeypatch)
    draft = b09._stored_row(db)

    assert c.post(DISCARD, json=ONE).status_code == 200

    [log] = _audits(db)
    assert log["action"] == "PAYROLL_DRAFT_DISCARDED"
    assert log["user_id"] == ADMIN["user_id"]
    assert log["entity_id"] == draft["payroll_id"]
    assert log["store_id"] == STORE
    assert log["detail"]["employee_id"] == EMP
    assert log["detail"]["employee_name"] == "ZZ Audit Staff"
    assert (log["detail"]["month"], log["detail"]["year"]) == (MONTH, YEAR)
    assert log["detail"]["net_salary"] == draft["net_salary"] > 0


def test_one_audit_row_per_discarded_draft_in_a_shop(monkeypatch):
    db = b09._db()
    _second_employee(db)
    c = _client(db, monkeypatch)
    assert b09._run(c).status_code == 200
    nets = {x["employee_id"]: x["net_salary"] for x in _rows(db)}

    assert c.post(DISCARD, json=SHOP).json()["discarded"] == 2

    assert _rows(db) == []
    assert {a["detail"]["employee_id"]: a["detail"]["net_salary"] for a in _audits(db)} == nets


@pytest.mark.parametrize("broken", ["no audit log", "write fails"])
def test_no_trace_no_discard(monkeypatch, broken):
    """A deletion nobody can see afterwards is worse than a stuck draft."""
    db, c = _drafted(monkeypatch)
    before = copy.deepcopy(b09._stored_row(db))

    class _Fails:
        def create(self, _doc):
            return None  # AuditRepository.create's answer when the write failed

    monkeypatch.setattr(
        payroll_router,
        "get_audit_repository",
        (lambda: None) if broken == "no audit log" else _Fails,
        raising=False,
    )

    r = c.post(DISCARD, json=ONE)

    assert r.status_code in (500, 503), r.text
    assert "nothing was discarded" in r.json()["detail"].lower()
    assert b09._stored_row(db) == before


def test_a_shop_discard_that_loses_the_audit_log_halfway_says_what_happened(monkeypatch):
    db = b09._db()
    _second_employee(db)
    c = _client(db, monkeypatch)
    assert b09._run(c).status_code == 200
    real = AuditRepository(db.get_collection("audit_logs"))

    class _FailsSecond:
        calls = 0

        def create(self, doc):
            _FailsSecond.calls += 1
            return real.create(doc) if _FailsSecond.calls == 1 else None

    monkeypatch.setattr(payroll_router, "get_audit_repository", _FailsSecond, raising=False)

    r = c.post(DISCARD, json=SHOP)

    assert r.status_code == 500 and "1 draft(s) before it" in r.json()["detail"]
    [kept] = _rows(db)
    [logged] = _audits(db)
    assert kept["status"] == "DRAFT"
    assert logged["detail"]["employee_id"] != kept["employee_id"]


def _lock_in_another_request(db, monkeypatch):
    """The Finance screen's "lock anyway", sent while the discard is mid-way.
    The route is async; this runs it on its own thread so it can be fired
    from inside the discard's audit write."""
    import threading

    said = {}
    t = threading.Thread(
        target=lambda: said.update(
            r=b09._lock_month_through_the_route(db, monkeypatch, without_payroll=True)
        )
    )
    t.start()
    t.join()
    return said["r"]


def test_a_lock_cannot_slip_in_while_a_discard_is_putting_a_draft_back(monkeypatch):
    """The discard deletes the draft, then writes the audit row. If the month is
    locked in between and the audit write then fails, the draft is put back:
    without the shared payroll guard it lands INSIDE the locked month, where it
    can never be approved or discarded (423 both) -- the dead end D15 exists to
    remove. The discard holds the month exactly like a saving run does."""
    db, c = _drafted(monkeypatch)
    before = copy.deepcopy(b09._stored_row(db))
    seen = {}

    class _LockThenFail:
        def create(self, _doc):
            seen["lock"] = _lock_in_another_request(db, monkeypatch)
            return None  # the audit write fails

    monkeypatch.setattr(payroll_router, "get_audit_repository", _LockThenFail, raising=False)

    r = c.post(DISCARD, json=ONE)

    assert r.status_code == 500 and "nothing was discarded" in r.json()["detail"].lower()
    assert seen["lock"][0] == 409, seen  # the lock waited for the discard
    assert db.get_collection("period_locks").count_documents({}) == 0
    restored = b09._stored_row(db)
    assert {k: v for k, v in restored.items() if k != "_id"} == {
        k: v for k, v in before.items() if k != "_id"
    }
    # The month is free again: the draft can now be discarded and the month locked.
    monkeypatch.setattr(
        payroll_router, "get_audit_repository",
        lambda: AuditRepository(db.get_collection("audit_logs")), raising=False,
    )
    assert c.post(DISCARD, json=ONE).status_code == 200
    assert b09._lock_month_through_the_route(db, monkeypatch, without_payroll=True) == (200, "")


def test_a_discard_is_refused_once_the_month_guard_is_locked(monkeypatch):
    """A lock that committed its guard but stopped before writing the
    period_locks row: payroll is closed from that moment, and so is a discard."""
    from api.services import payroll_period_guard as guard

    db, c = _drafted(monkeypatch)
    before = copy.deepcopy(b09._stored_row(db))
    assert guard.commit_lock(db, YEAR, MONTH, guard.read(db, YEAR, MONTH), {"locked_by": "x"})

    r = c.post(DISCARD, json=ONE)

    assert r.status_code == 423, r.text
    assert b09._stored_row(db) == before
    assert _audits(db) == []


# ---------------------------------------------------------------------------
# The other doors: a discarded draft is not served from an old copy
# ---------------------------------------------------------------------------


def test_a_discarded_draft_is_not_served_to_staff_from_an_old_copy(monkeypatch):
    """The old code stored a copy of a payroll row in ``payslips`` on first
    read (L3-23), under the row's own id. With the row gone that copy would be
    the month's payslip again, at every door the employee has."""
    db, c = _drafted(monkeypatch)
    draft = b09._stored_row(db)
    copies = db.get_collection("payslips")
    copies.insert_one(
        {"payslip_id": draft["payroll_id"], "employee_id": EMP, "month": MONTH,
         "year": YEAR, "breakdown": {"net_pay": draft["net_salary"]}}
    )
    # A real older payslip (made from the legacy salary records) is history.
    copies.insert_one(
        {"payslip_id": "legacy-sept", "employee_id": EMP, "month": 9, "year": YEAR,
         "breakdown": {"net_pay": 111}}
    )

    assert c.post(DISCARD, json=ONE).status_code == 200

    assert [p["payslip_id"] for p in copies.find({})] == ["legacy-sept"]
    replies, staff_print = b09._staff_doors(db, monkeypatch)
    assert replies[0].json()["payslip"]["month"] == 9  # My Work: the last real one
    assert replies[1].json() == {"payslip": None}  # October: nothing
    assert replies[2].json()["payslip"]["month"] == 9
    assert staff_print.status_code == 404


# ---------------------------------------------------------------------------
# Permissions catalogue
# ---------------------------------------------------------------------------


def test_the_route_is_catalogued_admin_only_and_widens_nothing():
    entry = rbac_policy.policy_for("POST", DISCARD)
    assert entry is not None and entry["allowed"] == ["ADMIN"]
    # It lands on payroll:write, whose reach was ADMIN before and still is: a
    # manager or accountant is not taught to grant payroll writes by this row.
    assert capability_for("POST", DISCARD) == "payroll:write"
    assert capability_roles("payroll:write") == ["ADMIN"]
