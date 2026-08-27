"""Six more screens must name the person, not print their user id.

Follow-up to the AP fix (test_ap_actor_names): a full sweep found six more
places where a raw internal user id reached a screen -- the order Status
Timeline ("Changed by: 97d2a24c-..."), the task drawer's "Created ... by" line
and escalation-ladder rung 1, the workshop board / job drawer / the PRINTED
JOB CARD's "Assigned To", the petty-cash settlement history's "Settled by"
column, the incentive-settings footer's "Last updated ... by", and the
workshop-productivity scorecard's Technician column.

All are fixed with the ONE existing mechanism (name_resolver.stamp_user_names),
resolved on the way OUT. Two invariants, both defects caught during the AP
round, are pinned per screen:

  * the STORED document keeps the raw id and nothing else -- a display name
    stamped into storage freezes into the audit trail and goes stale on rename;
  * an id that resolves to nobody (deleted QA logins exist in prod) gets NO
    ``_name`` sibling and the screen prints the id VERBATIM -- never an
    invented name.

These drive the real endpoint functions over strict fakes (no stubbed subject).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api.routers.expenses as expenses_mod  # noqa: E402
import api.routers.orders as orders_mod  # noqa: E402
import api.routers.points as points_mod  # noqa: E402
import api.routers.reports as reports_mod  # noqa: E402
import api.routers.tasks as tasks_mod  # noqa: E402
import api.routers.workshop as workshop_mod  # noqa: E402
from strict_fakes import StrictCollection, StrictDB  # noqa: E402


class _UsersColl(StrictCollection):
    """users collection that counts reads (an N+1 must FAIL, not merely lag)."""

    def __init__(self, users):
        super().__init__("users", users)
        self.reads = 0

    def find(self, *a, **k):
        self.reads += 1
        return super().find(*a, **k)


# Mirrors production: the SUPERADMIN account's only name is the username
# "admin"; a technician and a manager have real full names; and a retired QA
# login ("qa-deadbeef") resolves to NOBODY -- there is no person to name.
_USERS = [
    {"user_id": "user-superadmin", "username": "admin"},
    {"user_id": "tech-1", "full_name": "Ramesh Kumar"},
    {"user_id": "mgr-1", "full_name": "Sunil Gupta"},
]

_GONE = "qa-deadbeef"


def _db(**collections) -> StrictDB:
    db = StrictDB()
    db._collections["users"] = _UsersColl(_USERS)
    for name, docs in collections.items():
        db.seed(name, docs)
    # Some callers unwrap the dependency handle via getattr(db, "db", db);
    # StrictDB.__getattr__ would otherwise mint a collection literally named
    # "db". Point the attribute back at the database itself, like the real
    # connection wrapper does.
    db.db = db
    return db


def _admin(uid="user-superadmin"):
    return {
        "user_id": uid,
        "roles": ["SUPERADMIN"],
        "active_store_id": "BV-01",
        "store_ids": ["BV-01"],
    }


# ---------------------------------------------------------------------------
# 1. Orders -- the Status Timeline ("Changed by: ...")
# ---------------------------------------------------------------------------


class _OrderRepo:
    """find_* returns fresh top-level dicts, exactly like pymongo decoding
    BSON -- but the NESTED status_history entries stay shared with storage,
    so a fix that stamps the nested entries in place (instead of on copies)
    pollutes the stored history and fails the audit-trail test below."""

    def __init__(self, docs):
        self.docs = docs

    def find_by_store(self, store_id, from_date=None, to_date=None, status=None):
        return [dict(d) for d in self.docs]


def _order(order_id="O1", changed_by="user-superadmin"):
    return {
        "order_id": order_id,
        "store_id": "BV-01",
        "status": "CONFIRMED",
        "created_by": "user-superadmin",
        "created_at": "2026-08-25T10:00:00",
        "status_history": [
            {
                "status": "DRAFT",
                "timestamp": "2026-08-25T10:00:00",
                "changed_by": changed_by,
            }
        ],
    }


def _wire_orders(monkeypatch, docs):
    db = _db()
    repo = _OrderRepo(docs)
    monkeypatch.setattr(orders_mod, "get_order_repository", lambda: repo)
    monkeypatch.setattr(orders_mod, "validate_store_access", lambda s, u: "BV-01")
    monkeypatch.setattr(orders_mod, "_get_db", lambda: db)
    return db, repo


def _list_orders():
    return asyncio.run(
        orders_mod.list_orders(
            store_id="BV-01",
            status=None,
            customer_id=None,
            from_date=None,
            to_date=None,
            skip=0,
            limit=50,
            current_user=_admin(),
        )
    )


def test_order_timeline_names_the_status_changer(monkeypatch):
    """OrderStatusTimeline prints "Changed by:" for every history entry, and
    the DRAFT row prints the order's creator -- both must carry the name."""
    _wire_orders(monkeypatch, [_order()])
    row = _list_orders()["orders"][0]
    entry = row["statusHistory"][0]
    assert entry["changedByName"] == "admin"
    assert entry["changedBy"] == "user-superadmin"
    assert row["createdByName"] == "admin"
    assert row["createdBy"] == "user-superadmin"


def test_order_timeline_unresolved_id_prints_verbatim(monkeypatch):
    """A status change logged by a deleted account keeps the id, gains no
    _name, and the screen falls back to printing the id it stored."""
    _wire_orders(monkeypatch, [_order(changed_by=_GONE)])
    entry = _list_orders()["orders"][0]["statusHistory"][0]
    assert entry["changedBy"] == _GONE
    assert "changedByName" not in entry


def test_order_stored_history_keeps_only_the_id(monkeypatch):
    """The stored status_history is an audit trail: resolving names for the
    response must never write a _name into it (names go on COPIES)."""
    _, repo = _wire_orders(monkeypatch, [_order()])
    _list_orders()
    stored = repo.docs[0]
    assert "created_by_name" not in stored
    assert stored["status_history"] == [
        {
            "status": "DRAFT",
            "timestamp": "2026-08-25T10:00:00",
            "changed_by": "user-superadmin",
        }
    ]


def test_order_names_are_batched_not_per_row(monkeypatch):
    """Fifty orders on the list page must cost ONE users read, not fifty."""
    db, _ = _wire_orders(monkeypatch, [_order(order_id="O%d" % i) for i in range(50)])
    out = _list_orders()
    assert len(out["orders"]) == 50
    assert all(
        o["statusHistory"][0]["changedByName"] == "admin" for o in out["orders"]
    )
    assert db._collections["users"].reads == 1


# ---------------------------------------------------------------------------
# 2. Tasks -- the drawer's "Created ... by" line + escalation-ladder rung 1
# ---------------------------------------------------------------------------


class _TaskRepo:
    def __init__(self, docs):
        self.docs = docs

    def find_many(self, filters, skip=0, limit=50):
        return [dict(d) for d in self.docs]

    def count(self, filters):
        return len(self.docs)


def _task(task_id="T1", assigned_by="user-superadmin"):
    return {
        "task_id": task_id,
        "title": "Clean the display shelf",
        "status": "OPEN",
        "priority": "P3",
        "assigned_to": "tech-1",
        "assigned_by": assigned_by,
        "store_id": "BV-01",
    }


def _wire_tasks(monkeypatch, docs):
    db = _db()
    repo = _TaskRepo(docs)
    monkeypatch.setattr(tasks_mod, "get_task_repository", lambda: repo)
    monkeypatch.setattr(tasks_mod, "validate_store_access", lambda s, u: "BV-01")
    monkeypatch.setattr(tasks_mod, "get_db", lambda: db)
    return db, repo


def _list_tasks():
    return asyncio.run(
        tasks_mod.list_tasks(
            status=None,
            priority=None,
            assigned_to=None,
            task_type=None,
            store_id="BV-01",
            skip=0,
            limit=50,
            current_user=_admin(),
        )
    )


def test_task_list_names_the_assigner_beside_the_assignee(monkeypatch):
    """assigned_to_name existed; assigned_by_name was stamped NOWHERE, so the
    drawer's "Created ... by" line printed the raw id the create door wrote."""
    _wire_tasks(monkeypatch, [_task()])
    row = _list_tasks()["tasks"][0]
    assert row["assigned_by_name"] == "admin"
    assert row["assigned_by"] == "user-superadmin"
    assert row["assigned_to_name"] == "Ramesh Kumar"


def test_system_task_keeps_the_word_system_verbatim(monkeypatch):
    """Auto-generated tasks carry assigned_by="system" -- that names nobody
    and must pass through untouched, never dressed up as a person."""
    _wire_tasks(monkeypatch, [_task(assigned_by="system")])
    row = _list_tasks()["tasks"][0]
    assert row["assigned_by"] == "system"
    assert "assigned_by_name" not in row


def test_task_stored_doc_keeps_only_the_ids(monkeypatch):
    _, repo = _wire_tasks(monkeypatch, [_task()])
    _list_tasks()
    assert not [k for k in repo.docs[0] if k.endswith("_name")]


# ---------------------------------------------------------------------------
# 3 + 4. Workshop -- the board, the job drawer, and the PRINTED JOB CARD
# ---------------------------------------------------------------------------


class _JobRepo:
    def __init__(self, docs):
        self.docs = docs

    def find_many(self, filters, skip=0, limit=50, sort=None):
        return [dict(d) for d in self.docs]

    def find_by_id(self, job_id):
        for d in self.docs:
            if d.get("job_id") == job_id:
                return dict(d)
        return None


def _job(job_id="J1", technician_id="tech-1"):
    return {
        "job_id": job_id,
        "job_number": "WJ-0001",
        "store_id": "BV-01",
        "status": "IN_PROGRESS",
        "technician_id": technician_id,
        "created_at": "2026-08-25T10:00:00",
    }


def _wire_workshop(monkeypatch, docs):
    db = _db()
    repo = _JobRepo(docs)
    monkeypatch.setattr(workshop_mod, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(workshop_mod, "validate_store_access", lambda s, u: "BV-01")
    monkeypatch.setattr(workshop_mod, "can_access_store_scoped", lambda s, u: True)
    monkeypatch.setattr(workshop_mod, "get_db", lambda: db)
    return db, repo


def _list_jobs():
    return asyncio.run(
        workshop_mod.list_jobs(
            status=None,
            technician_id=None,
            store_id="BV-01",
            skip=0,
            limit=50,
            current_user=_admin(),
        )
    )


def test_workshop_board_names_the_technician(monkeypatch):
    """The board's "Assigned:" line and the drawer's "Assigned To" render
    assignedTo -- the response must carry assignedToName beside it."""
    _wire_workshop(monkeypatch, [_job()])
    row = _list_jobs()["jobs"][0]
    assert row["assignedToName"] == "Ramesh Kumar"
    assert row["assignedTo"] == "tech-1"


def test_job_card_print_feed_names_the_technician(monkeypatch):
    """WorkshopJobCardPrint prints assignedTechnician ONTO PAPER from the
    single-job read -- the worst leak of the six leaves the building."""
    _wire_workshop(monkeypatch, [_job()])
    job = asyncio.run(workshop_mod.get_job("J1", _admin()))
    assert job["assignedToName"] == "Ramesh Kumar"


def test_workshop_unknown_technician_prints_verbatim(monkeypatch):
    _wire_workshop(monkeypatch, [_job(technician_id=_GONE)])
    row = _list_jobs()["jobs"][0]
    assert row["assignedTo"] == _GONE
    assert "assignedToName" not in row


def test_workshop_stored_job_keeps_only_the_id(monkeypatch):
    _, repo = _wire_workshop(monkeypatch, [_job()])
    _list_jobs()
    assert not [k for k in repo.docs[0] if k.endswith("_name")]


# ---------------------------------------------------------------------------
# 5. Expenses -- petty-cash settlement history's "Settled by" column
# ---------------------------------------------------------------------------


def _settlement(settlement_id="S1", settled_by="mgr-1"):
    return {
        "settlement_id": settlement_id,
        "store_id": "BV-01",
        "settle_date": "2026-08-25",
        "expected_closing": 5000.0,
        "counted_closing": 5000.0,
        "variance": 0.0,
        "variance_status": "BALANCED",
        "status": "SETTLED",
        "settled_by": settled_by,
    }


def _wire_expenses(monkeypatch, docs):
    db = _db(petty_cash_settlements=docs)
    monkeypatch.setattr(expenses_mod, "get_db", lambda: db)
    monkeypatch.setattr(expenses_mod, "validate_store_access", lambda s, u: "BV-01")
    return db


def _list_settlements():
    return asyncio.run(
        expenses_mod.list_petty_cash_settlements(
            store_id="BV-01",
            from_date=None,
            to_date=None,
            limit=50,
            current_user=_admin(),
        )
    )


def test_settlement_history_names_the_settler(monkeypatch):
    """ExpenseTracker's history table shows who counted the drawer -- the
    person, not the user id the settle door stamped."""
    _wire_expenses(monkeypatch, [_settlement()])
    row = _list_settlements()["settlements"][0]
    assert row["settled_by_name"] == "Sunil Gupta"
    assert row["settled_by"] == "mgr-1"


def test_settlement_unresolved_settler_prints_verbatim(monkeypatch):
    _wire_expenses(monkeypatch, [_settlement(settled_by=_GONE)])
    row = _list_settlements()["settlements"][0]
    assert row["settled_by"] == _GONE
    assert "settled_by_name" not in row


def test_settlement_stored_doc_keeps_only_the_id(monkeypatch):
    db = _wire_expenses(monkeypatch, [_settlement()])
    _list_settlements()
    stored = db.get_collection("petty_cash_settlements").docs[0]
    assert stored["settled_by"] == "mgr-1"
    assert not [k for k in stored if k.endswith("_name")]


# ---------------------------------------------------------------------------
# 6. Incentive settings -- the footer's "Last updated ... by" line
# ---------------------------------------------------------------------------


def _wire_points(monkeypatch, updated_by="user-superadmin"):
    db = _db(
        incentive_settings=[
            {
                "store_id": "BV-01",
                "updated_at": "2026-08-25T10:00:00",
                "updated_by": updated_by,
            }
        ]
    )
    monkeypatch.setattr(points_mod, "get_db", lambda: db)
    return db


def _get_settings():
    return asyncio.run(
        points_mod.get_settings(current_user=_admin(), store_id="BV-01")
    )


def test_incentive_settings_footer_names_the_last_updater(monkeypatch):
    _wire_points(monkeypatch)
    out = _get_settings()
    assert out["updated_by_name"] == "admin"
    assert out["updated_by"] == "user-superadmin"


def test_incentive_settings_unresolved_updater_prints_verbatim(monkeypatch):
    _wire_points(monkeypatch, updated_by=_GONE)
    out = _get_settings()
    assert out["updated_by"] == _GONE
    assert "updated_by_name" not in out


def test_incentive_settings_stored_doc_keeps_only_the_id(monkeypatch):
    db = _wire_points(monkeypatch)
    _get_settings()
    stored = db.get_collection("incentive_settings").docs[0]
    assert stored["updated_by"] == "user-superadmin"
    assert not [k for k in stored if k.endswith("_name")]


# ---------------------------------------------------------------------------
# 7. Reports -- the workshop-productivity scorecard's Technician column
# ---------------------------------------------------------------------------


def _closed_job(job_id, technician_id):
    return {
        "job_id": job_id,
        "store_id": "BV-01",
        "status": "COMPLETED",
        "technician_id": technician_id,
        "created_at": "2026-08-20T10:00:00",
        "completed_at": "2026-08-21T10:00:00",
        "expected_date": "2026-08-22",
    }


def _wire_reports(monkeypatch, docs):
    db = _db(workshop_jobs=docs)
    monkeypatch.setattr(reports_mod, "get_db", lambda: db)
    monkeypatch.setattr(reports_mod, "validate_store_access", lambda s, u: "BV-01")
    return db


def _productivity():
    return asyncio.run(
        reports_mod.workshop_productivity(
            from_date=date(2026, 8, 1),
            to_date=date(2026, 8, 27),
            store_id="BV-01",
            current_user=_admin(),
        )
    )


def test_productivity_scorecard_names_the_technician(monkeypatch):
    _wire_reports(monkeypatch, [_closed_job("J1", "tech-1")])
    rows = _productivity()["technicians"]
    assert rows and rows[0]["technician_id"] == "tech-1"
    assert rows[0]["technician_id_name"] == "Ramesh Kumar"


def test_productivity_unknown_technician_prints_verbatim(monkeypatch):
    _wire_reports(monkeypatch, [_closed_job("J1", _GONE)])
    rows = _productivity()["technicians"]
    assert rows and rows[0]["technician_id"] == _GONE
    assert "technician_id_name" not in rows[0]
