"""Audit F33 (2026-09-29): purchase tasks never reached the person who acts.

* 'Book purchase invoice for GRN ...' was assigned_to the role STRING
  'ACCOUNTANT'. /tasks?assigned_to=<user id> (the "Mine" list) matches a user
  id, so the accountant's inbox was empty.
* 'GRN discrepancy on PO PO/BV-DHN-02/...' had no assignee at all, and named
  the PO twice.

Owner rules: 2026-09-03 tasks go to PEOPLE, not titles; 2026-09-29 (D17) a
goods-received-with-a-problem task goes to THAT shop's store manager by name
(the staff-to-store assignment); the book-invoice task goes to the accountant
by name, else a visible unassigned bucket for the store's managers.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.dependencies as deps  # noqa: E402
from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import GRNCreate, GRNItemCreate, create_grn  # noqa: E402
from api.services.task_triggers import create_system_task  # noqa: E402
from tests.test_grn_duplicate_guard import _MemGRNRepo, _attach, _user, _wire  # noqa: E402

# The staff-to-store assignment: users.store_ids.
_STAFF = [
    {"user_id": "mgr-dhn2", "roles": ["STORE_MANAGER"], "store_ids": ["BV-TEST-01"], "is_active": True},
    {"user_id": "acc-bv", "roles": ["ACCOUNTANT"], "store_ids": ["BV-TEST-01"], "is_active": True},
    {"user_id": "mgr-other", "roles": ["STORE_MANAGER"], "store_ids": ["WO-JSR-01"], "is_active": True},
]


class _UserRepo:
    def find_by_role(self, role, store_id=None):
        return [
            u for u in _STAFF
            if role in u["roles"] and u["is_active"] and (store_id is None or store_id in u["store_ids"])
        ]


class _TaskRepo:
    def __init__(self):
        self.created = []

    def find_many(self, flt):
        return [t for t in self.created if t.get("source_ref") == flt.get("source_ref")]

    def create(self, doc):
        self.created.append(dict(doc))
        return doc


def _people(mp):
    mp.setattr(deps, "get_user_repository", lambda: _UserRepo())


def _task(role, store):
    repo = _TaskRepo()
    create_system_task(
        repo, title="t", description="d", priority="P2", category="Purchase",
        store_id=store, dedupe_ref=f"x:{role}:{store}", assigned_to=role,
    )
    return repo.created[0]


def test_role_assignee_becomes_the_named_person_at_that_store(monkeypatch):
    _people(monkeypatch)
    assert _task("ACCOUNTANT", "BV-TEST-01")["assigned_to"] == "acc-bv"
    assert _task("STORE_MANAGER", "BV-TEST-01")["assigned_to"] == "mgr-dhn2"
    assert _task("STORE_MANAGER", "WO-JSR-01")["assigned_to"] == "mgr-other"


def test_nobody_holds_the_role_there_leaves_it_unassigned_on_the_store(monkeypatch):
    """No accountant at this shop: the task is unassigned but store-stamped,
    so GET /tasks shows it on that store's Team list for its managers. Never
    the title string, which matches nobody."""
    _people(monkeypatch)
    t = _task("ACCOUNTANT", "WO-JSR-01")
    assert t["assigned_to"] is None
    assert t["store_id"] == "WO-JSR-01"
    assert _task("STORE_MANAGER", None)["assigned_to"] is None


def test_a_user_id_passes_through_untouched(monkeypatch):
    _people(monkeypatch)
    assert _task("user-123", "BV-TEST-01")["assigned_to"] == "user-123"


class _PORepo:
    def find_by_id(self, po_id):
        return {
            "po_id": po_id,
            "po_number": "PO/BV-TEST-01/26-27/0007",
            "vendor_id": "V1",
            "vendor_name": "Jot Optics",
            "status": "SENT",
            "delivery_store_id": "BV-TEST-01",
            "items": [{"product_id": "P1", "quantity": 10}],
        }


def test_grn_discrepancy_goes_to_that_shops_store_manager_by_name(monkeypatch):
    _people(monkeypatch)
    tasks = _TaskRepo()
    monkeypatch.setattr(deps, "get_task_repository", lambda: tasks)
    store = _wire(monkeypatch, _MemGRNRepo())
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo())
    short = GRNCreate(
        po_id="PO1",
        vendor_invoice_no="JOT/26-27/0451",
        items=[GRNItemCreate(product_id="P1", received_qty=8, accepted_qty=8, rejected_qty=0, tallied=True)],
        **_attach(store),
    )
    res = asyncio.run(create_grn(short, current_user=_user()))
    assert res["has_discrepancy"] is True
    (task,) = tasks.created
    assert task["assigned_to"] == "mgr-dhn2"
    assert task["store_id"] == "BV-TEST-01"
    assert task["title"] == "GRN discrepancy on PO/BV-TEST-01/26-27/0007"
    assert "PO PO/" not in task["description"]


# ---------------------------------------------------------------------------
# Verifier round 2
# ---------------------------------------------------------------------------

import mongomock  # noqa: E402

from agents.implementations.taskmaster import TaskmasterAgent  # noqa: E402


def _open_task(task_id, ref):
    return {
        "task_id": task_id, "task_number": task_id, "title": task_id, "status": "OPEN",
        "source": "SYSTEM", "source_ref": ref, "assigned_to": "acc-bv", "store_id": "BV-TEST-01",
        "history": [],
    }


def test_book_invoice_task_closes_once_the_bill_is_booked(monkeypatch):
    """'Book purchase invoice for GRN RCPT/...' stayed OPEN in the accountant's
    Mine list after the bill was booked. TASKMASTER's tick closes it once a
    bill carrying that grn_id exists, whichever door booked it; a receipt with
    no bill yet, and the receipt's other tasks, stay open."""
    db = mongomock.MongoClient().db
    db.tasks.insert_many([
        _open_task("T-BOOK-1", "express_invoice:G1"),
        _open_task("T-BOOK-2", "express_invoice:G2"),  # not booked yet
        _open_task("T-DISC-1", "grn:G1"),  # the discrepancy task is not the bill
    ])
    db.vendor_bills.insert_one(
        {"bill_id": "B1", "grn_id": "G1", "invoice_number": "JOT/26-27/0451", "status": "OUTSTANDING"}
    )
    agent = TaskmasterAgent(db=db)
    asyncio.run(agent._do_background_work())  # the real 5-minute tick

    status = {t["task_id"]: t for t in db.tasks.find()}
    assert status["T-BOOK-1"]["status"] == "COMPLETED"
    assert status["T-BOOK-1"]["completed_at"] is not None
    assert "JOT/26-27/0451" in status["T-BOOK-1"]["completion_notes"]
    assert status["T-BOOK-1"]["history"][-1]["action"] == "completed"
    assert status["T-BOOK-2"]["status"] == "OPEN"
    assert status["T-DISC-1"]["status"] == "OPEN"


def test_advisory_task_goes_through_the_one_door_to_a_person(monkeypatch):
    """TASKMASTER's Rx-anomaly advisory task inserted itself with assigned_to
    'store_manager' (a lowercase title, matching nobody). It now goes through
    create_system_task: the named store manager of the Rx's shop, a
    task_number, deduped per anomaly."""
    _people(monkeypatch)
    db = mongomock.MongoClient().db
    agent = TaskmasterAgent(db=db)
    anomaly = {
        "kind": "rx_out_of_range", "severity": "HIGH", "summary": "Rx RX1 right_eye SPH=25.0 exceeds limit",
        "prescription_id": "RX1", "eye": "right_eye", "sph": 25.0, "store_id": "BV-TEST-01",
    }
    asyncio.run(agent.on_event("anomaly.detected", anomaly))
    asyncio.run(agent.on_event("anomaly.detected", anomaly))  # same anomaly again
    (task,) = list(db.tasks.find())
    assert task["assigned_to"] == "mgr-dhn2"
    assert task["task_number"] and task["source"] == "SYSTEM"
    assert task["source_ref"] == "anomaly:rx_out_of_range:RX1:right_eye"
    assert task["priority"] == "P1"


def test_a_lowercase_role_is_still_a_role(monkeypatch):
    _people(monkeypatch)
    assert _task("store_manager", "BV-TEST-01")["assigned_to"] == "mgr-dhn2"
