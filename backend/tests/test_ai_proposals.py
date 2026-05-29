"""
IMS 2.0 - AI Change-Proposal Workflow tests
=============================================
Covers SYSTEM_INTENT.md section 8's loop + the reversible-whitelist carve-out:

  - A REVERSIBLE type (draft_po) AUTO-EXECUTES on approve: writes the domain
    record (DRAFT purchase order), captures before/after state, writes an
    immutable audit_logs row, and lands the proposal in EXECUTED.
  - A NON-REVERSIBLE type (price_ceiling_change) stays ADVISORY on approve:
    status -> APPROVED, NO domain side-effect, but the approval IS audited.
  - reject() -> status REJECTED with the reason recorded + audited.
  - Execution failure is FAIL-SOFT: status -> FAILED, an error captured, an
    audit row written, and NO exception escapes.
  - The HTTP endpoints are SUPERADMIN-only (404 for non-superadmin / anon).

No network and no live Mongo - a faithful in-memory fake DB stands in for
pymongo so the lifecycle + audit assertions run deterministically.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.proposals import (  # noqa: E402
    ProposalStore,
    ProposalStatus,
    REVERSIBLE_TYPES,
    create_proposal,
    is_reversible,
)


# ============================================================================
# In-memory fake Mongo (no network, no live DB)
# ============================================================================


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Tiny query matcher - only the equality + operators we actually use."""
    for k, v in query.items():
        # Support the {"$expr": {"$lt": ["$a", "$b"]}} shape used by stock scans
        if k == "$expr" and isinstance(v, dict) and "$lt" in v:
            left, right = v["$lt"]
            lval = doc.get(left[1:]) if isinstance(left, str) and left.startswith("$") else left
            rval = doc.get(right[1:]) if isinstance(right, str) and right.startswith("$") else right
            if not (lval is not None and rval is not None and lval < rval):
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


def _project(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    if not projection:
        return dict(doc)
    # We only ever use the exclude form {"_id": 0}
    if projection.get("_id") == 0:
        out = dict(doc)
        out.pop("_id", None)
        return out
    return dict(doc)


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field, direction=-1):
        rev = direction == -1
        self._docs = sorted(self._docs, key=lambda d: d.get(field) or "", reverse=rev)
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        # Mimic pymongo: inject an _id into the caller's dict
        doc.setdefault("_id", f"oid-{len(self.docs)}")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        matched = [
            _project(d, projection)
            for d in self.docs
            if _matches(d, query or {})
        ]
        return FakeCursor(matched)

    def update_one(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class FailingCollection(FakeCollection):
    """insert_one always raises - used to test fail-soft execution."""

    def insert_one(self, doc):
        raise RuntimeError("simulated DB write failure")


class FakeDB:
    def __init__(self, failing_collections=None):
        self._collections: Dict[str, FakeCollection] = {}
        self._failing = set(failing_collections or [])

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = (
                FailingCollection() if name in self._failing else FakeCollection()
            )
        return self._collections[name]


# ============================================================================
# Whitelist invariants
# ============================================================================


class TestWhitelist:
    def test_reversible_set_is_tier1(self):
        assert "draft_po" in REVERSIBLE_TYPES
        assert "mark_task" in REVERSIBLE_TYPES
        assert "rx_reminder" in REVERSIBLE_TYPES
        assert "inter_store_transfer_suggestion" in REVERSIBLE_TYPES

    def test_dangerous_types_are_not_reversible(self):
        for t in ("price_ceiling_change", "refund", "writeoff",
                  "staff_transfer", "po_send"):
            assert not is_reversible(t), f"{t} must NOT auto-execute"

    def test_create_stamps_reversible_flag(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        rev = store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y", payload={"sku": "S1"})
        adv = store.create(created_by_agent="oracle", proposal_type="refund",
                           title="x", rationale="y")
        assert rev["reversible"] is True
        assert adv["reversible"] is False


# ============================================================================
# Reversible -> auto-execute + before/after + audit
# ============================================================================


class TestReversibleAutoExecutes:
    def _make(self, db):
        store = ProposalStore(db=db)
        prop = store.create(
            created_by_agent="oracle",
            proposal_type="draft_po",
            title="Reorder SKU-1",
            rationale="stock low",
            payload={"sku": "SKU-1", "quantity": 12, "on_hand": 1,
                     "reorder_point": 6},
            before_state={"sku": "SKU-1", "on_hand": 1, "reorder_point": 6},
        )
        return store, prop

    def test_approve_executes_and_creates_draft_po(self):
        db = FakeDB()
        store, prop = self._make(db)
        result = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")

        assert result["ok"] is True
        assert result["executed"] is True
        assert result["advisory"] is False
        # Proposal lands EXECUTED
        assert result["proposal"]["status"] == ProposalStatus.EXECUTED.value
        assert result["proposal"]["reviewed_by"] == "ceo@bv.com"
        assert result["proposal"]["reviewed_at"] is not None

        # Domain side-effect: a DRAFT purchase order was created (reused shape)
        pos = db.get_collection("purchase_orders").docs
        assert len(pos) == 1
        assert pos[0]["status"] == "DRAFT"
        assert pos[0]["sku"] == "SKU-1"
        assert pos[0]["quantity"] == 12
        assert pos[0]["from_proposal_id"] == prop["proposal_id"]
        # Sending is NOT auto-done: it's a draft that still needs a human
        assert pos[0]["requires_approval"] is True

    def test_before_and_after_state_captured(self):
        db = FakeDB()
        store, prop = self._make(db)
        result = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")
        p = result["proposal"]
        assert p["before_state"]["on_hand"] == 1
        assert p["before_state"]["reorder_point"] == 6
        assert p["after_state"]["po_status"] == "DRAFT"
        assert p["after_state"]["quantity"] == 12

    def test_immutable_audit_row_written(self):
        db = FakeDB()
        store, prop = self._make(db)
        store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")

        audits = db.get_collection("audit_logs").docs
        assert len(audits) == 1
        a = audits[0]
        assert a["action"] == "ai_proposal_executed"
        assert a["entity_type"] == "ai_proposal"
        assert a["entity_id"] == prop["proposal_id"]
        assert a["executed"] is True
        assert a["user_id"] == "ceo@bv.com"
        assert a["before_state"]["on_hand"] == 1
        assert a["after_state"]["po_status"] == "DRAFT"
        assert "log_id" in a and a["log_id"].startswith("AUD-")

    def test_mark_task_executes_into_tasks_collection(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        prop = store.create(created_by_agent="taskmaster", proposal_type="mark_task",
                           title="Follow up with vendor", rationale="overdue",
                           payload={"priority": "P1"})
        result = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")
        assert result["executed"] is True
        tasks = db.get_collection("tasks").docs
        assert len(tasks) == 1
        assert tasks[0]["status"] == "OPEN"
        assert tasks[0]["priority"] == "P1"
        assert tasks[0]["from_proposal_id"] == prop["proposal_id"]


# ============================================================================
# Non-reversible -> advisory (NO execution), but approval IS audited
# ============================================================================


class TestAdvisoryNoExecution:
    def test_approve_advisory_records_but_does_not_execute(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        prop = store.create(
            created_by_agent="oracle",
            proposal_type="price_ceiling_change",
            title="Raise LUXURY cap to 8%",
            rationale="competitor pressure",
            payload={"category": "LUXURY", "new_cap": 8},
        )
        result = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")

        assert result["ok"] is True
        assert result["executed"] is False
        assert result["advisory"] is True
        assert result["proposal"]["status"] == ProposalStatus.APPROVED.value

        # No domain side-effects of ANY kind
        assert db.get_collection("purchase_orders").docs == []
        assert db.get_collection("tasks").docs == []

        # ... but the approval IS audited (Audit Everything)
        audits = db.get_collection("audit_logs").docs
        assert len(audits) == 1
        assert audits[0]["action"] == "ai_proposal_approved_advisory"
        assert audits[0]["executed"] is False
        assert audits[0]["reversible"] is False


# ============================================================================
# Reject
# ============================================================================


class TestReject:
    def test_reject_sets_status_and_reason(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        prop = store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y", payload={"sku": "S1"})
        result = store.reject(prop["proposal_id"], reviewed_by="ceo@bv.com",
                             reason="vendor on hold")
        assert result["ok"] is True
        assert result["proposal"]["status"] == ProposalStatus.REJECTED.value
        assert result["proposal"]["reject_reason"] == "vendor on hold"
        # No PO created
        assert db.get_collection("purchase_orders").docs == []
        # Audited
        audits = db.get_collection("audit_logs").docs
        assert len(audits) == 1
        assert audits[0]["action"] == "ai_proposal_rejected"
        assert audits[0]["reason"] == "vendor on hold"

    def test_cannot_reject_already_executed(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        prop = store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y", payload={"sku": "S1"})
        store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")
        result = store.reject(prop["proposal_id"], reviewed_by="ceo@bv.com",
                             reason="too late")
        assert result["ok"] is False
        assert result["error"] == "not_pending"


# ============================================================================
# Fail-soft execution failure -> FAILED, no crash
# ============================================================================


class TestFailSoftExecution:
    def test_executor_db_failure_lands_FAILED_without_raising(self):
        # purchase_orders insert raises -> approve() must NOT propagate it
        db = FakeDB(failing_collections=["purchase_orders"])
        store = ProposalStore(db=db)
        prop = store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y", payload={"sku": "S1"})

        result = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")

        assert result["ok"] is True          # the call itself succeeded
        assert result["executed"] is False
        assert "error" in result
        assert result["proposal"]["status"] == ProposalStatus.FAILED.value
        assert result["proposal"]["execution_error"]
        # The failed attempt is still audited
        audits = db.get_collection("audit_logs").docs
        assert len(audits) == 1
        assert audits[0]["action"] == "ai_proposal_execution_failed"
        assert audits[0]["severity"] == "WARNING"

    def test_missing_payload_sku_lands_FAILED(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        # draft_po with no sku -> executor raises ValueError -> FAILED
        prop = store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y", payload={})
        result = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")
        assert result["proposal"]["status"] == ProposalStatus.FAILED.value
        assert db.get_collection("purchase_orders").docs == []


# ============================================================================
# Lifecycle guards + listing + no-DB safety
# ============================================================================


class TestLifecycleAndSafety:
    def test_double_approve_is_blocked(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        prop = store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y", payload={"sku": "S1"})
        store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")
        again = store.approve(prop["proposal_id"], reviewed_by="ceo@bv.com")
        assert again["ok"] is False
        assert again["error"] == "not_pending"
        # Still only ONE purchase order (no double execution)
        assert len(db.get_collection("purchase_orders").docs) == 1

    def test_approve_unknown_id_is_not_found(self):
        store = ProposalStore(db=FakeDB())
        result = store.approve("PROP-does-not-exist", reviewed_by="ceo@bv.com")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    def test_list_filters_by_status(self):
        db = FakeDB()
        store = ProposalStore(db=db)
        p1 = store.create(created_by_agent="oracle", proposal_type="draft_po",
                         title="a", rationale="r", payload={"sku": "S1"})
        store.create(created_by_agent="oracle", proposal_type="draft_po",
                    title="b", rationale="r", payload={"sku": "S2"})
        store.approve(p1["proposal_id"], reviewed_by="ceo@bv.com")

        pending = store.list(status="PENDING")
        executed = store.list(status="EXECUTED")
        assert len(pending) == 1
        assert len(executed) == 1
        assert pending[0]["title"] == "b"
        assert executed[0]["title"] == "a"

    def test_no_db_is_fail_soft(self):
        store = ProposalStore(db=None)
        assert store.create(created_by_agent="oracle", proposal_type="draft_po",
                           title="x", rationale="y") is None
        assert store.list() == []
        assert store.get("PROP-x") is None
        # approve/reject of a missing proposal just report not_found, no crash
        assert store.approve("PROP-x", reviewed_by="ceo")["ok"] is False
        # helper also fail-soft
        assert create_proposal(None, created_by_agent="oracle",
                              proposal_type="draft_po", title="x",
                              rationale="y") is None

    def test_dedupe_key_prevents_duplicate_pending(self):
        db = FakeDB()
        first = create_proposal(db, created_by_agent="oracle",
                               proposal_type="draft_po", title="x", rationale="y",
                               payload={"sku": "S1"}, dedupe_key="draft_po:S1:today")
        second = create_proposal(db, created_by_agent="oracle",
                                proposal_type="draft_po", title="x2", rationale="y2",
                                payload={"sku": "S1"}, dedupe_key="draft_po:S1:today")
        assert first["proposal_id"] == second["proposal_id"]
        assert len(db.get_collection("ai_proposals").docs) == 1


# ============================================================================
# HTTP endpoints - SUPERADMIN only
# ============================================================================


class TestEndpointAuth:
    def test_list_anon_is_404(self, client):
        resp = client.get("/api/v1/jarvis/proposals")
        assert resp.status_code in (401, 404)

    def test_list_non_superadmin_is_404(self, client, staff_headers):
        resp = client.get("/api/v1/jarvis/proposals", headers=staff_headers)
        assert resp.status_code == 404

    def test_approve_non_superadmin_is_404(self, client, staff_headers):
        resp = client.post("/api/v1/jarvis/proposals/PROP-x/approve",
                           headers=staff_headers)
        assert resp.status_code == 404

    def test_reject_non_superadmin_is_404(self, client, staff_headers):
        resp = client.post("/api/v1/jarvis/proposals/PROP-x/reject",
                           headers=staff_headers, json={"reason": "no"})
        assert resp.status_code == 404

    def test_superadmin_list_returns_envelope(self, client, auth_headers):
        resp = client.get("/api/v1/jarvis/proposals", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "proposals" in body
        assert "total" in body
        assert "reversible_types" in body
        assert "draft_po" in body["reversible_types"]

    def test_superadmin_get_unknown_is_404(self, client, auth_headers):
        resp = client.get("/api/v1/jarvis/proposals/PROP-nope", headers=auth_headers)
        assert resp.status_code == 404
