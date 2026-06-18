"""
IMS 2.0 - Feature F8: PO vs GRN variance / backorder
====================================================
INTENT-LEVEL acceptance tests (a hollow shell must FAIL). Covers:

  * variance math: ordered vs ACCEPTED-received per line, open_qty, multi-GRN
  * aging_status enum (ON_TIME / OVERDUE / CRITICALLY_OVERDUE), never a colour
  * TASKMASTER aged-backorder sweep: create, dedupe, 14-day P2->P1 escalation,
    skip-dismissed
  * dismiss endpoint: >=10 char reason gate, single-doc PO $push, one audit row,
    no cross-collection atomic write, debit-note suggestion math
  * variance report: fully-received lines absent
  * regression guard: the existing 3-way match engine is unchanged

CI-ROBUSTNESS: these tests do NOT depend on a real Mongo. The pure engine has
no DB at all; the TASKMASTER + router tests monkeypatch EVERY repo/db accessor
the handler touches and SEED every doc a query reads, so the result is identical
locally (no mongod) and in CI (real mongod) -- there is no fail-soft "no Mongo"
branch left to diverge.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.services import po_variance_engine as eng  # noqa: E402
from api.services import purchase_match  # noqa: E402
from api.routers import vendors  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from agents.implementations.taskmaster import TaskmasterAgent  # noqa: E402


NOW = datetime(2026, 6, 9, 12, 0, 0)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _po(po_id="PO-1", product_id="P1", ordered=10, expected_offset_days=None,
        status="PARTIALLY_RECEIVED", dismissed=None, base=NOW):
    # `base` anchors expected_date. Engine unit tests pass now=NOW to the engine
    # so they keep base=NOW (clock-stable). Tests that exercise the PRODUCTION
    # aging path (the variance-report endpoint + the TaskMaster sweep, which read
    # the real wall clock) must pass base=datetime.now() so a -N-day offset lands
    # in the intended aging bucket no matter what calendar date the suite runs.
    expected_date = None
    if expected_offset_days is not None:
        expected_date = (base + timedelta(days=expected_offset_days)).isoformat()
    doc = {
        "po_id": po_id,
        "po_number": f"PO-NUM-{po_id}",
        "vendor_id": "V1",
        "vendor_name": "Acme Optics",
        "delivery_store_id": "S1",
        "status": status,
        "expected_date": expected_date,
        "items": [
            {"product_id": product_id, "product_name": "Frame X", "sku": "SKU-1",
             "quantity": ordered, "unit_price": 100.0}
        ],
    }
    if dismissed:
        doc["dismissed_variances"] = dismissed
    return doc


def _grn(po_id="PO-1", product_id="P1", accepted=0, received=None, rejected=0,
         status="ACCEPTED"):
    if received is None:
        received = accepted + rejected
    return {
        "grn_id": f"GRN-{po_id}-{accepted}",
        "po_id": po_id,
        "status": status,
        "items": [
            {"product_id": product_id, "received_qty": received,
             "accepted_qty": accepted, "rejected_qty": rejected}
        ],
    }


# ===========================================================================
# 1-2, 15: open-qty engine
# ===========================================================================


def test_open_qty_engine_short_delivery():
    """PO for 10, GRN accepted 6 -> open_qty 4, SHORT."""
    po = _po(ordered=10)
    rows = eng.open_qty_per_line(po, [_grn(accepted=6)], now=NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["ordered_qty"] == 10
    assert row["accepted_qty"] == 6
    assert row["open_qty"] == 4
    assert row["variance_status"] == "SHORT"


def test_open_qty_engine_multiple_grns():
    """Two ACCEPTED GRNs (4 + 4) against a PO for 10 -> open_qty 2."""
    po = _po(ordered=10)
    grns = [_grn(accepted=4), _grn(accepted=4)]
    rows = eng.open_qty_per_line(po, grns, now=NOW)
    assert rows[0]["accepted_qty"] == 8
    assert rows[0]["open_qty"] == 2


def test_only_accepted_grns_close_the_line():
    """A non-ACCEPTED GRN (e.g. DISPUTED) must NOT reduce open_qty."""
    po = _po(ordered=10)
    grns = [_grn(accepted=6, status="DISPUTED")]
    rows = eng.open_qty_per_line(po, grns, now=NOW)
    assert rows[0]["accepted_qty"] == 0
    assert rows[0]["open_qty"] == 10


def test_variance_report_filters_fully_received():
    """A fully-received PO (open 0, EXACT) is absent from the report."""
    po = _po(po_id="PO-FULL", ordered=10, expected_offset_days=-2)
    grns_by_po = {"PO-FULL": [_grn(po_id="PO-FULL", accepted=10)]}
    lines = eng.variance_report_lines([po], grns_by_po, now=NOW)
    assert lines == []


def test_variance_report_keeps_short_line():
    """A short line IS surfaced."""
    po = _po(po_id="PO-SHORT", ordered=10, expected_offset_days=-2)
    grns_by_po = {"PO-SHORT": [_grn(po_id="PO-SHORT", accepted=6)]}
    lines = eng.variance_report_lines([po], grns_by_po, now=NOW)
    assert len(lines) == 1
    assert lines[0]["open_qty"] == 4


# ===========================================================================
# 3-5: aging status enum
# ===========================================================================


def test_aging_status_enum_on_time():
    """expected_date tomorrow -> ON_TIME (an enum, not None/colour)."""
    po = _po(expected_offset_days=1)
    rows = eng.open_qty_per_line(po, [_grn(accepted=6)], now=NOW)
    assert rows[0]["aging_status"] == "ON_TIME"
    assert rows[0]["days_overdue"] == 0


def test_aging_status_overdue():
    """expected_date 5 days ago -> OVERDUE."""
    po = _po(expected_offset_days=-5)
    rows = eng.open_qty_per_line(po, [_grn(accepted=6)], now=NOW)
    assert rows[0]["aging_status"] == "OVERDUE"
    assert rows[0]["days_overdue"] == 5


def test_aging_status_critically_overdue():
    """expected_date 14 days ago -> CRITICALLY_OVERDUE (distinct threshold)."""
    po = _po(expected_offset_days=-14)
    rows = eng.open_qty_per_line(po, [_grn(accepted=6)], now=NOW)
    assert rows[0]["aging_status"] == "CRITICALLY_OVERDUE"
    assert rows[0]["days_overdue"] == 14


def test_aging_status_is_never_a_colour():
    """The aging enum is one of the three explicit statuses, never a colour."""
    for off in (3, -3, -20):
        po = _po(expected_offset_days=off)
        rows = eng.open_qty_per_line(po, [_grn(accepted=1)], now=NOW)
        assert rows[0]["aging_status"] in (
            "ON_TIME", "OVERDUE", "CRITICALLY_OVERDUE"
        )


# ===========================================================================
# aged_backorder_tasks_needed (pure)
# ===========================================================================


def test_backorder_specs_skip_on_time_lines():
    """An open but not-yet-overdue PO produces no backorder task spec."""
    po = _po(expected_offset_days=2)
    specs = eng.aged_backorder_tasks_needed([po], {"PO-1": [_grn(accepted=6)]}, now=NOW)
    assert specs == []


def test_backorder_specs_priority_p2_then_p1():
    """OVERDUE -> P2; CRITICALLY_OVERDUE -> P1 + escalate flag."""
    po2 = _po(po_id="PO-A", expected_offset_days=-5)
    po1 = _po(po_id="PO-B", expected_offset_days=-20)
    specs = eng.aged_backorder_tasks_needed(
        [po2, po1],
        {"PO-A": [_grn(po_id="PO-A", accepted=6)],
         "PO-B": [_grn(po_id="PO-B", accepted=6)]},
        now=NOW,
    )
    by_po = {s["po_id"]: s for s in specs}
    assert by_po["PO-A"]["priority"] == "P2"
    assert by_po["PO-A"]["escalate"] is False
    assert by_po["PO-B"]["priority"] == "P1"
    assert by_po["PO-B"]["escalate"] is True
    assert by_po["PO-A"]["source_ref"] == "backorder:PO-A:P1"


def test_backorder_specs_skip_dismissed():
    """A dismissed product produces no backorder task spec."""
    po = _po(expected_offset_days=-20,
             dismissed=[{"product_id": "P1", "reason": "vendor short closed line"}])
    specs = eng.aged_backorder_tasks_needed([po], {"PO-1": [_grn(accepted=6)]}, now=NOW)
    assert specs == []


# ===========================================================================
# TASKMASTER sweep - fake collections (no Mongo)
# ===========================================================================


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, _n):
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None):
        query = query or {}
        out = []
        for d in self.docs:
            ok = True
            for k, v in query.items():
                dv = d.get(k)
                if isinstance(v, dict) and "$in" in v:
                    if dv not in v["$in"]:
                        ok = False
                        break
                elif isinstance(v, dict) and "$ne" in v:
                    if dv == v["$ne"]:
                        ok = False
                        break
                elif dv != v:
                    ok = False
                    break
            if ok:
                out.append(d)
        return _FakeCursor(out)

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("task_id")})()

    def find_one_and_update(self, query, update):
        for d in self.docs:
            ok = True
            for k, v in query.items():
                dv = d.get(k)
                if isinstance(v, dict) and "$in" in v:
                    if dv not in v["$in"]:
                        ok = False
                        break
                elif isinstance(v, dict) and "$ne" in v:
                    if dv == v["$ne"]:
                        ok = False
                        break
                elif dv != v:
                    ok = False
                    break
            if ok:
                for sk, sv in update.get("$set", {}).items():
                    d[sk] = sv
                for pk, pv in update.get("$push", {}).items():
                    d.setdefault(pk, []).append(pv)
                return d
        return None


class _FakeDb:
    def __init__(self, collections):
        self._collections = collections

    def get_collection(self, name):
        return self._collections.get(name)


def _run(coro):
    """Run a coroutine on a fresh event loop (Py3.12+/3.14 no longer auto-create
    one in the main thread, so get_event_loop() raises)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sweep(po_docs, grn_docs, task_docs):
    tasks = _FakeColl(task_docs)
    db = _FakeDb({
        "purchase_orders": _FakeColl(po_docs),
        "grns": _FakeColl(grn_docs),
        "tasks": tasks,
    })
    agent = TaskmasterAgent(db=db)
    actions = _run(agent._sweep_aged_backorders())
    return actions, tasks


def test_taskmaster_sweep_creates_task():
    """A 5-day-overdue PO with 4 open units -> one P2 backorder task."""
    # base=now(): the sweep reads the real clock, so anchor to today so the PO
    # is exactly 5 days overdue (OVERDUE -> P2), not aged into the 14d+ band.
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-5, base=datetime.now())
    actions, tasks = _sweep([po], [_grn(accepted=6)], [])
    created = [t for t in tasks.docs if t.get("source_ref") == "backorder:PO-1:P1"]
    assert len(created) == 1
    assert created[0]["priority"] == "P2"
    assert created[0]["status"] == "OPEN"
    assert any(a["action"] == "backorder_task_created" for a in actions)


def test_taskmaster_sweep_uses_canonical_task_shape():
    """The sweep routes through the canonical system-task creator: a created
    backorder task carries due_at (priority SLA grace) + a task_number, like
    every other SYSTEM task -- not the old bespoke dict (no due_at, id-only)."""
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-5)
    _actions, tasks = _sweep([po], [_grn(accepted=6)], [])
    created = [t for t in tasks.docs if t.get("source_ref") == "backorder:PO-1:P1"]
    assert len(created) == 1
    task = created[0]
    assert task.get("due_at") is not None
    assert isinstance(task.get("task_number"), str) and task["task_number"]
    assert task.get("task_id")
    assert task["source"] == "SYSTEM"
    assert task["category"] == "Purchase"


def test_taskmaster_sweep_dedupe():
    """An existing OPEN task for the same backorder -> no duplicate."""
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-5)
    existing = {"task_id": "T-OLD", "source_ref": "backorder:PO-1:P1",
                "status": "OPEN", "priority": "P2"}
    _actions, tasks = _sweep([po], [_grn(accepted=6)], [existing])
    refs = [t for t in tasks.docs if t.get("source_ref") == "backorder:PO-1:P1"]
    assert len(refs) == 1  # still only the original


def test_taskmaster_sweep_14day_escalation():
    """An OPEN P2 task whose PO is now 14d+ overdue -> bumped to P1."""
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-20)
    existing = {"task_id": "T-OLD", "source_ref": "backorder:PO-1:P1",
                "status": "OPEN", "priority": "P2"}
    actions, tasks = _sweep([po], [_grn(accepted=6)], [existing])
    bumped = next(t for t in tasks.docs if t["task_id"] == "T-OLD")
    assert bumped["priority"] == "P1"
    assert any(a["action"] == "backorder_task_escalated" for a in actions)


def test_taskmaster_sweep_skips_dismissed():
    """A dismissed product line -> no task is created."""
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-20,
             dismissed=[{"product_id": "P1", "reason": "short-closed by vendor"}])
    _actions, tasks = _sweep([po], [_grn(accepted=6)], [])
    assert [t for t in tasks.docs if t.get("source_ref") == "backorder:PO-1:P1"] == []


# ===========================================================================
# Router tests - dismiss + report (all accessors monkeypatched, no Mongo)
# ===========================================================================


class _FakeRepoColl:
    """Stand-in for a repository's underlying .collection with the one method
    the dismiss handler uses: find_one_and_update."""

    def __init__(self):
        self.updates = []

    def find_one_and_update(self, query, update):
        self.updates.append((query, update))
        return {"po_id": query.get("po_id")}


class _FakePoRepo:
    def __init__(self, pos):
        self._pos = {p["po_id"]: p for p in pos}
        self.collection = _FakeRepoColl()

    def find_by_id(self, po_id):
        return self._pos.get(po_id)

    def find_pending(self, vendor_id=None, limit=100):
        # Mirror the real signature: the variance report passes limit=0 (all) so a
        # >100-PO chain isn't silently capped. The fake returns all matching here.
        rows = [
            p for p in self._pos.values()
            if p.get("status") in ("SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED")
            and (vendor_id is None or p.get("vendor_id") == vendor_id)
        ]
        return rows if not limit else rows[:limit]


class _FakeGrnRepo:
    def __init__(self, grns_by_po=None, grns_by_id=None):
        self._by_po = grns_by_po or {}
        self._by_id = grns_by_id or {}

    def find_by_po(self, po_id):
        return self._by_po.get(po_id, [])

    def find_by_id(self, grn_id):
        return self._by_id.get(grn_id)


class _RecordingAuditRepo:
    def __init__(self):
        self.rows = []

    def create(self, data):
        self.rows.append(data)
        return data


class _FakeMongo:
    def __init__(self, bills=None):
        self._bills = {b["bill_id"]: b for b in (bills or [])}

    def get_collection(self, name):
        bills = self._bills

        class _C:
            def find_one(self, query, _proj=None):
                return bills.get(query.get("bill_id"))

            def find(self, query=None, _proj=None):
                query = query or {}
                rows = [dict(b) for b in bills.values()]
                po_cond = query.get("po_id")
                if isinstance(po_cond, dict) and "$in" in po_cond:
                    rows = [b for b in rows if b.get("po_id") in po_cond["$in"]]
                elif po_cond is not None:
                    rows = [b for b in rows if b.get("po_id") == po_cond]
                return rows

        return _C()


def _client(roles=("ADMIN",)):
    app = FastAPI()
    app.include_router(vendors.router, prefix="/api/v1/vendors")

    async def _u():
        return {"user_id": "u1", "roles": list(roles), "store_ids": ["S1"],
                "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _patch_all(monkeypatch, po_repo=None, grn_repo=None, audit_repo=None,
               db=None, store=None):
    """Monkeypatch EVERY accessor the handlers touch -- deterministic in CI."""
    monkeypatch.setattr(vendors, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(vendors, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vendors, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(vendors, "_get_db", lambda: db)
    # validate_store_access is imported into vendors' namespace.
    monkeypatch.setattr(vendors, "validate_store_access", lambda s, u: store)


def test_dismiss_requires_reason(monkeypatch):
    """reason < 10 chars -> 422/400 (pydantic min_length rejects it)."""
    po = _po(po_id="PO-1")
    audit = _RecordingAuditRepo()
    _patch_all(monkeypatch, po_repo=_FakePoRepo([po]), grn_repo=_FakeGrnRepo(),
               audit_repo=audit, db=None)
    cli = _client()
    r = cli.post("/api/v1/vendors/purchase-orders/PO-1/dismiss-variance",
                 json={"product_id": "P1", "reason": "ok"})
    assert r.status_code in (400, 422)
    # No audit row, no PO update for a rejected reason.
    assert audit.rows == []


def test_dismiss_writes_audit_row(monkeypatch):
    """A valid dismiss writes exactly one audit row with the right action."""
    po = _po(po_id="PO-1")
    po_repo = _FakePoRepo([po])
    audit = _RecordingAuditRepo()
    _patch_all(monkeypatch, po_repo=po_repo, grn_repo=_FakeGrnRepo(),
               audit_repo=audit, db=None)
    cli = _client()
    r = cli.post("/api/v1/vendors/purchase-orders/PO-1/dismiss-variance",
                 json={"product_id": "P1", "reason": "vendor short-closed line"})
    assert r.status_code == 200, r.text
    assert r.json()["dismissed"] is True
    assert len(audit.rows) == 1
    assert audit.rows[0]["action"] == "po_variance_dismiss"


def test_dismiss_single_doc_update(monkeypatch):
    """Exactly one find_one_and_update on purchase_orders ($push), nothing else."""
    po = _po(po_id="PO-1")
    po_repo = _FakePoRepo([po])
    audit = _RecordingAuditRepo()
    _patch_all(monkeypatch, po_repo=po_repo, grn_repo=_FakeGrnRepo(),
               audit_repo=audit, db=None)
    cli = _client()
    r = cli.post("/api/v1/vendors/purchase-orders/PO-1/dismiss-variance",
                 json={"product_id": "P1", "reason": "vendor short-closed line"})
    assert r.status_code == 200, r.text
    assert len(po_repo.collection.updates) == 1
    query, update = po_repo.collection.updates[0]
    assert query == {"po_id": "PO-1"}
    assert "$push" in update and "dismissed_variances" in update["$push"]


def test_dismiss_404_unknown_po(monkeypatch):
    audit = _RecordingAuditRepo()
    _patch_all(monkeypatch, po_repo=_FakePoRepo([]), grn_repo=_FakeGrnRepo(),
               audit_repo=audit, db=None)
    cli = _client()
    r = cli.post("/api/v1/vendors/purchase-orders/NOPE/dismiss-variance",
                 json={"product_id": "P1", "reason": "this is a long enough reason"})
    assert r.status_code == 404


def test_debit_note_suggested_when_invoice_overbills(monkeypatch):
    """GRN accepted 6, invoice billed 10 @100 -> suggest debit note for 4*100."""
    po = _po(po_id="PO-1")
    grn = {"grn_id": "G1", "items": [
        {"product_id": "P1", "accepted_qty": 6, "received_qty": 6, "rejected_qty": 0}]}
    bill = {"bill_id": "B1", "lines": [
        {"product_id": "P1", "qty": 10, "unit_price": 100.0}]}
    _patch_all(
        monkeypatch,
        po_repo=_FakePoRepo([po]),
        grn_repo=_FakeGrnRepo(grns_by_id={"G1": grn}),
        audit_repo=_RecordingAuditRepo(),
        db=_FakeMongo(bills=[bill]),
    )
    cli = _client()
    r = cli.post(
        "/api/v1/vendors/purchase-orders/PO-1/dismiss-variance",
        json={"product_id": "P1", "reason": "billed more than received",
              "grn_id": "G1", "bill_id": "B1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["debit_note_suggested"] is True
    assert body["suggested_amount"] == 400.0


def test_debit_note_not_suggested_when_no_invoice(monkeypatch):
    """No bill_id -> no debit-note suggestion."""
    po = _po(po_id="PO-1")
    _patch_all(monkeypatch, po_repo=_FakePoRepo([po]), grn_repo=_FakeGrnRepo(),
               audit_repo=_RecordingAuditRepo(), db=_FakeMongo())
    cli = _client()
    r = cli.post("/api/v1/vendors/purchase-orders/PO-1/dismiss-variance",
                 json={"product_id": "P1", "reason": "no invoice linked here"})
    assert r.status_code == 200, r.text
    assert r.json()["debit_note_suggested"] is False


def test_variance_report_endpoint_omits_fully_received(monkeypatch):
    """The report endpoint surfaces a short line and omits a fully-received PO."""
    # base=now(): the variance-report endpoint computes aging off the real clock,
    # so anchor to today -> PO-SHORT is exactly 5 days overdue (OVERDUE).
    po_short = _po(po_id="PO-SHORT", ordered=10, expected_offset_days=-5, base=datetime.now())
    po_full = _po(po_id="PO-FULL", ordered=10, expected_offset_days=-2, base=datetime.now())
    po_repo = _FakePoRepo([po_short, po_full])
    grn_repo = _FakeGrnRepo(grns_by_po={
        "PO-SHORT": [_grn(po_id="PO-SHORT", accepted=6)],
        "PO-FULL": [_grn(po_id="PO-FULL", accepted=10)],
    })
    _patch_all(monkeypatch, po_repo=po_repo, grn_repo=grn_repo,
               audit_repo=_RecordingAuditRepo(), db=None, store=None)
    cli = _client()
    r = cli.get("/api/v1/vendors/variance-report")
    assert r.status_code == 200, r.text
    lines = r.json()["lines"]
    pos_in_report = {ln["po_id"] for ln in lines}
    assert "PO-SHORT" in pos_in_report
    assert "PO-FULL" not in pos_in_report
    short = next(ln for ln in lines if ln["po_id"] == "PO-SHORT")
    assert short["open_qty"] == 4
    assert short["aging_status"] == "OVERDUE"


# ===========================================================================
# F8 P3: the debit-note prompt must be REACHABLE -- report rows resolve the
# GRN + booked-invoice links the Dismiss call needs.
# ===========================================================================


def test_engine_stamps_latest_accepted_grn_id():
    """Rows carry the grn_id of the NEWEST ACCEPTED GRN covering the product
    (created_at wins; non-ACCEPTED GRNs are ignored)."""
    po = _po(ordered=10)
    g_old = _grn(accepted=3)
    g_old["grn_id"] = "G-OLD"
    g_old["created_at"] = "2026-05-01T10:00:00"
    g_new = _grn(accepted=3)
    g_new["grn_id"] = "G-NEW"
    g_new["created_at"] = "2026-06-01T10:00:00"
    g_disputed = _grn(accepted=2, status="DISPUTED")
    g_disputed["grn_id"] = "G-DISPUTED"
    g_disputed["created_at"] = "2026-06-05T10:00:00"
    # Order shuffled on purpose: created_at must decide, not list position.
    rows = eng.open_qty_per_line(po, [g_new, g_disputed, g_old], now=NOW)
    assert rows[0]["latest_accepted_grn_id"] == "G-NEW"


def test_engine_latest_grn_none_when_no_accepted_receipt():
    po = _po(ordered=10)
    rows = eng.open_qty_per_line(po, [], now=NOW)
    assert rows[0]["latest_accepted_grn_id"] is None


def test_variance_report_rows_carry_grn_and_bill_links(monkeypatch):
    """The report endpoint resolves latest_accepted_grn_id + booked_bill_id so
    the FE Dismiss call can pass grn_id + bill_id and the debit-note prompt
    actually fires (it used to be unreachable: rows carried neither id)."""
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-5)
    grn = _grn(po_id="PO-1", accepted=6)
    bill = {
        "bill_id": "B1",
        "po_id": "PO-1",
        "doc_type": "PURCHASE_INVOICE",
        "invoice_date": "2026-06-01",
        "lines": [{"product_id": "P1", "qty": 10, "unit_price": 100.0}],
    }
    # An older bill for the same PO/product -- the NEWEST must win.
    bill_old = {
        "bill_id": "B0",
        "po_id": "PO-1",
        "doc_type": "PURCHASE_INVOICE",
        "invoice_date": "2026-05-01",
        "lines": [{"product_id": "P1", "qty": 2, "unit_price": 100.0}],
    }
    _patch_all(
        monkeypatch,
        po_repo=_FakePoRepo([po]),
        grn_repo=_FakeGrnRepo(grns_by_po={"PO-1": [grn]}),
        audit_repo=_RecordingAuditRepo(),
        db=_FakeMongo(bills=[bill_old, bill]),
    )
    cli = _client()
    r = cli.get("/api/v1/vendors/variance-report")
    assert r.status_code == 200, r.text
    line = next(ln for ln in r.json()["lines"] if ln["po_id"] == "PO-1")
    assert line["latest_accepted_grn_id"] == grn["grn_id"]
    assert line["booked_bill_id"] == "B1"


def test_variance_report_links_fail_soft_without_db(monkeypatch):
    """No DB -> booked_bill_id is present-but-null; the report still serves."""
    po = _po(po_id="PO-1", ordered=10, expected_offset_days=-5)
    _patch_all(
        monkeypatch,
        po_repo=_FakePoRepo([po]),
        grn_repo=_FakeGrnRepo(grns_by_po={"PO-1": [_grn(po_id="PO-1", accepted=6)]}),
        audit_repo=_RecordingAuditRepo(),
        db=None,
    )
    cli = _client()
    r = cli.get("/api/v1/vendors/variance-report")
    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert "booked_bill_id" in line and line["booked_bill_id"] is None


# ===========================================================================
# 16: regression guard on the existing 3-way match engine
# ===========================================================================


def test_match_engine_unchanged():
    """A price-variance invoice still produces ON_HOLD_EXCEPTION (F8 must not
    alter the existing purchase_match engine)."""
    po = {"items": [{"product_id": "P1", "quantity": 10, "unit_price": 100.0}]}
    grn = {"items": [{"product_id": "P1", "accepted_qty": 10}]}
    invoice_lines = [{"product_id": "P1", "qty": 10, "unit_price": 130.0,
                      "taxable": 1300.0}]
    result = purchase_match.three_way_match(po, grn, invoice_lines, tolerance_pct=5.0)
    assert result["match_status"] == "ON_HOLD_EXCEPTION"
    assert result["summary"]["exception_lines"] >= 1


def test_match_engine_clean_match_still_matches():
    po = {"items": [{"product_id": "P1", "quantity": 10, "unit_price": 100.0}]}
    grn = {"items": [{"product_id": "P1", "accepted_qty": 10}]}
    invoice_lines = [{"product_id": "P1", "qty": 10, "unit_price": 100.0,
                      "taxable": 1000.0}]
    result = purchase_match.three_way_match(po, grn, invoice_lines, tolerance_pct=5.0)
    assert result["match_status"] == "MATCHED"
