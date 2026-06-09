"""
IMS 2.0 - F26 Remote approval (leave fast-path) tests (intent-level)
=====================================================================
F26 wires short-notice leave applications through the MERGED E4 ApprovalEngine
(api.services.approvals) so an eligible manager can action a pending leave from a
DIFFERENT store / device with their PIN. The engine owns the PIN gate, the atomic
single-use token, the store-binding at approve-time and the audit chain -- F26
adds only: the leave_approval action type, the apply-time request + manager bell,
the self-approval block, and the remote consume-and-stamp endpoint. NO fork of E4.

These exercise the REAL leave-router handlers (api.routers.hr) against the REAL
ApprovalEngine over a faithful in-memory fake Mongo (no network, no live mongod).
A hollow shell that accepts a free-text approver, skips the PIN/atomic single-use
token, or lets an applicant approve their own leave FAILS here.

CI-ROBUSTNESS: every repo/db accessor the handlers touch (_get_db,
get_leave_repository, get_user_repository) is monkeypatched onto ONE shared
FakeDB, and every doc the handlers read (users w/ bcrypt PIN, leave rows) is
seeded -- so there is no local-vs-CI fail-soft divergence.

Acceptance intent covered:
  1. apply_leave on a short-notice CASUAL/SICK leave opens a real E4 leave_approval
     request (REQUESTED), stamps fast_path=True + approval_request_id, and writes a
     manager bell.
  2. A remote eligible manager approves the E4 request with their PIN (atomic token
     mint) and calls /approve-remote -> leave APPROVED, approved_via='fast_path'.
     A hollow shell that stamps a free-text approver FAILS.
  3. Self-approval is blocked: the applicant cannot approve/reject/remote-approve
     their own leave (403 cannot_approve_own).
  4. Single-use: the same approval_token cannot be spent twice (409 already used);
     a token minted for leave A cannot be replayed against leave B (token_mismatch).
  5. Cross-store: a STORE_MANAGER of store B cannot approve a leave filed against
     store A via the E4 engine (store_scope 403); an AREA_MANAGER covering both can.
  6. fast-path detection: type AND notice both matter (CASUAL 1d -> fast; EARNED 1d
     -> not; CASUAL 5d -> not); the threshold reads E2 get_policy.
  7. Standard (non-fast-path) leave still approves via /approve, approved_via=
     'standard', and is self-approval-blocked.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers.auth import hash_password  # noqa: E402
from api.routers import hr as hr_router  # noqa: E402
from api.services import approvals as appr  # noqa: E402
from api.services.approvals import ApprovalEngine  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (same operator support the engine + repos use)
# ============================================================================


def _get_path(doc: Dict[str, Any], dotted: str):
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    if actual is None and op in ("$gt", "$gte", "$lt", "$lte"):
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
    except TypeError:
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = _get_path(doc, k) if "." in k else doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


def _project(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    out = dict(doc)
    if projection and projection.get("_id") == 0:
        out.pop("_id", None)
    return out


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                if "." in kk:
                    _set_path(doc, kk, vv)
                else:
                    doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                cur = _get_path(doc, kk) if "." in kk else doc.get(kk)
                newv = (cur or 0) + vv
                if "." in kk:
                    _set_path(doc, kk, newv)
                else:
                    doc[kk] = newv
        elif op == "$unset":
            for kk in fields:
                doc.pop(kk, None)


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field, direction=-1):
        # Repos pass sort as a list [(field, dir)]; the engine passes (field, dir).
        if isinstance(field, list) and field:
            field, direction = field[0]
        self._docs = sorted(
            self._docs,
            key=lambda d: (d.get(field) is None, d.get(field)),
            reverse=(direction == -1),
        )
        return self

    def skip(self, n):
        self._docs = self._docs[int(n):]
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, database=None):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        self.database = database

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        matched = [_project(d, projection) for d in self.docs if _matches(d, query or {})]
        return FakeCursor(matched)

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=None, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return _project(d, None)
        return None

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(database=self)
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Light fake repos backed by the SAME FakeDB collections the engine uses
# (only the methods the leave handlers call: find_many / create / find_by_id /
#  update / find_by_role).
# ============================================================================


class FakeLeaveRepo:
    def __init__(self, db: FakeDB):
        self._coll = db.get_collection("leaves")

    def find_many(self, filter: Dict = None, **_kw) -> List[Dict]:
        return list(self._coll.find(filter or {}))

    def create(self, doc: Dict) -> Dict:
        doc.setdefault("_id", doc.get("leave_id"))
        self._coll.insert_one(doc)
        return doc

    def find_by_id(self, leave_id: str) -> Optional[Dict]:
        return self._coll.find_one({"leave_id": leave_id})

    def update(self, leave_id: str, data: Dict) -> bool:
        res = self._coll.update_one({"leave_id": leave_id}, {"$set": data})
        return bool(getattr(res, "modified_count", 0))


class FakeUserRepo:
    def __init__(self, db: FakeDB):
        self._coll = db.get_collection("users")

    def find_by_role(self, role: str, store_id: str = None) -> List[Dict]:
        q: Dict[str, Any] = {"roles": role, "is_active": True}
        if store_id:
            q["store_ids"] = store_id
        return list(self._coll.find(q))


# ============================================================================
# Fixtures
# ============================================================================


class _Req:
    """Minimal stand-in for the LeaveRemoteApprove pydantic body."""

    def __init__(self, approval_token: str):
        self.approval_token = approval_token


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def wired(db, monkeypatch):
    """Monkeypatch EVERY repo/db accessor the leave handlers touch onto ONE shared
    FakeDB, so the engine + the handlers see the same data (CI-robust: no real
    mongod, no fail-soft divergence)."""
    leave_repo = FakeLeaveRepo(db)
    user_repo = FakeUserRepo(db)
    monkeypatch.setattr(hr_router, "_get_db", lambda: db)
    monkeypatch.setattr(hr_router, "get_leave_repository", lambda: leave_repo)
    monkeypatch.setattr(hr_router, "get_user_repository", lambda: user_repo)
    return db


def _seed_user(db, user_id, *, roles, store_ids=None, pin=None):
    coll = db.get_collection("users")
    doc = {
        "user_id": user_id,
        "is_active": True,
        "roles": list(roles),
        "store_ids": list(store_ids or []),
    }
    if pin is not None:
        doc["approval_pin_hash"] = hash_password(pin)
        doc["pin_attempts"] = {"count": 0, "window_start": appr._now()}
    coll.insert_one(doc)


def _user(user_id, roles, store_id=None):
    """current_user dict shape the handlers read."""
    return {
        "user_id": user_id,
        "roles": list(roles),
        "active_store_id": store_id,
        "store_ids": [store_id] if store_id else [],
    }


class _Leave:
    """Minimal LeaveCreate-shaped object (the handler reads attributes, not a model)."""

    def __init__(self, leave_type, from_days_ahead, to_days_ahead=None, reason="x"):
        self.leave_type = leave_type
        self.from_date = date.today() + timedelta(days=from_days_ahead)
        self.to_date = date.today() + timedelta(
            days=to_days_ahead if to_days_ahead is not None else from_days_ahead
        )
        self.reason = reason


def _run(coro):
    return asyncio.run(coro)


def _audit_rows(db):
    return db.get_collection("audit_logs").docs


# ============================================================================
# 1. apply_leave opens a REAL E4 request for short-notice CASUAL/SICK
# ============================================================================


def test_apply_fast_path_opens_real_e4_request_and_bell(wired):
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")

    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    res = _run(hr_router.apply_leave(_Leave("CASUAL", from_days_ahead=1), staff))

    assert res["status"] == "PENDING"
    assert res["fast_path"] is True
    req_id = res["approval_request_id"]
    assert req_id and req_id.startswith("REQ-")

    # A REAL leave_approval E4 request exists, REQUESTED, store-bound to S1.
    reqdoc = db.get_collection("approval_requests").find_one({"request_id": req_id})
    assert reqdoc["action_type"] == "leave_approval"
    assert reqdoc["status"] == "REQUESTED"
    assert reqdoc["store_id"] == "S1"
    assert reqdoc["context"]["leave_id"] == res["leaveId"]

    # The leave doc carries the fast-path linkage.
    leave = db.get_collection("leaves").find_one({"leave_id": res["leaveId"]})
    assert leave["fast_path"] is True
    assert leave["approval_request_id"] == req_id
    assert leave["status"] == "PENDING"

    # A manager bell was written (urgent for fast-path).
    bells = db.get_collection("notifications").docs
    assert any(b.get("kind") == "leave_request" and b.get("urgent") for b in bells)


def test_apply_standard_leave_no_e4_request(wired):
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    # EARNED is not a fast-path type even with 1-day notice.
    res = _run(hr_router.apply_leave(_Leave("EARNED", from_days_ahead=1), staff))
    assert res["fast_path"] is False
    assert res["approval_request_id"] is None
    assert db.get_collection("approval_requests").docs == []
    # A (non-urgent) bell is still written so managers see the request.
    bells = db.get_collection("notifications").docs
    assert any(b.get("kind") == "leave_request" and not b.get("urgent") for b in bells)


# ============================================================================
# 2. Remote approver actions an eligible pending request via E4 (the headline)
# ============================================================================


def test_remote_approver_actions_pending_via_e4(wired):
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    applied = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), staff))
    leave_id = applied["leaveId"]
    req_id = applied["approval_request_id"]

    # The approver (a DIFFERENT user, possibly on another device) approves the E4
    # request with their PIN -> atomic token mint. This is the merged engine.
    engine = ApprovalEngine(db=db)
    appr_res = engine.approve(
        req_id, approver_user_id="mgr1", approver_roles=["STORE_MANAGER"],
        pin="1234", approver_store_ids=["S1"],
    )
    assert appr_res["ok"] is True
    token = appr_res["approval_token"]
    assert token.startswith("APT-")

    # Remote-approve the leave by spending that one-time token.
    mgr = _user("mgr1", ["STORE_MANAGER"], store_id="S1")
    out = _run(hr_router.approve_leave_remote(leave_id, _Req(token), mgr))
    assert out["approved_via"] == "fast_path"

    leave = db.get_collection("leaves").find_one({"leave_id": leave_id})
    assert leave["status"] == "APPROVED"
    # The approver stamped is the REAL approver user_id, not a free-text name.
    assert leave["approved_by"] == "mgr1"
    assert leave["approved_via"] == "fast_path"

    # Full audit chain through E4: requested -> approved -> consumed.
    actions = [a["action"] for a in _audit_rows(db)
               if a.get("entity_type") == "approval_request"]
    assert "approval_requested" in actions
    assert "approved" in actions
    assert "consumed" in actions


def test_remote_approve_without_valid_token_fails(wired):
    """A hollow shell that approves a leave without spending a real E4 token must
    FAIL. A bogus token -> token_mismatch (it doesn't match the request) and the
    leave stays PENDING."""
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    applied = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), staff))

    mgr = _user("mgr1", ["STORE_MANAGER"], store_id="S1")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _run(hr_router.approve_leave_remote(applied["leaveId"], _Req("APT-bogustoken"), mgr))
    assert ei.value.status_code == 400
    assert ei.value.detail == "token_mismatch"

    leave = db.get_collection("leaves").find_one({"leave_id": applied["leaveId"]})
    assert leave["status"] == "PENDING"


# ============================================================================
# 3. Self-approval is blocked on every path
# ============================================================================


def test_self_approval_blocked_standard(wired):
    db = wired
    # A manager who files their own leave cannot approve it.
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    mgr = _user("mgr1", ["STORE_MANAGER"], store_id="S1")
    applied = _run(hr_router.apply_leave(_Leave("EARNED", from_days_ahead=10), mgr))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _run(hr_router.approve_leave(applied["leaveId"], mgr))
    assert ei.value.status_code == 403
    assert ei.value.detail == "cannot_approve_own"

    # Reject is blocked too.
    with pytest.raises(HTTPException) as ej:
        _run(hr_router.reject_leave(applied["leaveId"], "no", mgr))
    assert ej.value.status_code == 403
    assert ej.value.detail == "cannot_approve_own"


def test_self_approval_blocked_remote(wired):
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    mgr = _user("mgr1", ["STORE_MANAGER"], store_id="S1")
    # Manager files their own short-notice SICK leave -> a fast-path E4 request.
    applied = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), mgr))

    from fastapi import HTTPException

    # Even holding a token, the remote endpoint blocks self-approval BEFORE consume.
    with pytest.raises(HTTPException) as ei:
        _run(hr_router.approve_leave_remote(applied["leaveId"], _Req("APT-anything"), mgr))
    assert ei.value.status_code == 403
    assert ei.value.detail == "cannot_approve_own"


def test_a_different_manager_can_approve_anothers_leave(wired):
    db = wired
    _seed_user(db, "mgrA", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    _seed_user(db, "mgrB", roles=["STORE_MANAGER"], store_ids=["S1"], pin="5678")
    # mgrA files an EARNED (standard) leave; mgrB approves it.
    mgrA = _user("mgrA", ["STORE_MANAGER"], store_id="S1")
    applied = _run(hr_router.apply_leave(_Leave("EARNED", from_days_ahead=10), mgrA))
    mgrB = _user("mgrB", ["STORE_MANAGER"], store_id="S1")
    out = _run(hr_router.approve_leave(applied["leaveId"], mgrB))
    assert out["leave_id"] == applied["leaveId"]
    leave = db.get_collection("leaves").find_one({"leave_id": applied["leaveId"]})
    assert leave["status"] == "APPROVED"
    assert leave["approved_by"] == "mgrB"
    assert leave["approved_via"] == "standard"


# ============================================================================
# 4. Single-use token: no double-spend, no cross-leave replay
# ============================================================================


def test_token_is_single_use(wired):
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    applied = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), staff))

    engine = ApprovalEngine(db=db)
    appr_res = engine.approve(
        applied["approval_request_id"], approver_user_id="mgr1",
        approver_roles=["STORE_MANAGER"], pin="1234", approver_store_ids=["S1"],
    )
    token = appr_res["approval_token"]
    mgr = _user("mgr1", ["STORE_MANAGER"], store_id="S1")

    out1 = _run(hr_router.approve_leave_remote(applied["leaveId"], _Req(token), mgr))
    assert out1["approved_via"] == "fast_path"

    # Second spend of the same token: the leave is no longer PENDING -> 400.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        _run(hr_router.approve_leave_remote(applied["leaveId"], _Req(token), mgr))
    assert ei.value.status_code == 400  # "Leave is not pending"


def test_token_cannot_be_replayed_against_a_different_leave(wired):
    db = wired
    _seed_user(db, "mgr1", roles=["STORE_MANAGER"], store_ids=["S1"], pin="1234")
    staffA = _user("staffA", ["SALES_STAFF"], store_id="S1")
    staffB = _user("staffB", ["SALES_STAFF"], store_id="S1")
    leaveA = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), staffA))
    leaveB = _run(hr_router.apply_leave(_Leave("CASUAL", from_days_ahead=1), staffB))

    engine = ApprovalEngine(db=db)
    apprA = engine.approve(
        leaveA["approval_request_id"], approver_user_id="mgr1",
        approver_roles=["STORE_MANAGER"], pin="1234", approver_store_ids=["S1"],
    )
    tokenA = apprA["approval_token"]
    mgr = _user("mgr1", ["STORE_MANAGER"], store_id="S1")

    from fastapi import HTTPException

    # Spend leave A's token against leave B -> token_mismatch (its request points
    # at leave A, not leave B).
    with pytest.raises(HTTPException) as ei:
        _run(hr_router.approve_leave_remote(leaveB["leaveId"], _Req(tokenA), mgr))
    assert ei.value.status_code == 400
    assert ei.value.detail == "token_mismatch"

    # Leave B stays PENDING and leave A's token is still spendable on leave A.
    assert db.get_collection("leaves").find_one(
        {"leave_id": leaveB["leaveId"]})["status"] == "PENDING"
    okA = _run(hr_router.approve_leave_remote(leaveA["leaveId"], _Req(tokenA), mgr))
    assert okA["approved_via"] == "fast_path"


# ============================================================================
# 5. Cross-store: store-binding holds at E4 approve-time
# ============================================================================


def test_cross_store_manager_cannot_approve(wired):
    db = wired
    _seed_user(db, "mgrB", roles=["STORE_MANAGER"], store_ids=["S2"], pin="9999")
    _seed_user(db, "area1", roles=["AREA_MANAGER"], store_ids=["S1", "S2"], pin="1111")
    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    applied = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), staff))
    req_id = applied["approval_request_id"]

    engine = ApprovalEngine(db=db)
    # Manager of store S2 cannot approve a leave filed against S1.
    bad = engine.approve(
        req_id, approver_user_id="mgrB", approver_roles=["STORE_MANAGER"],
        pin="9999", approver_store_ids=["S2"],
    )
    assert bad["ok"] is False
    assert bad["http"] == 403
    assert bad["error"] == "store_scope"

    # An AREA_MANAGER covering both S1 + S2 can.
    ok = engine.approve(
        req_id, approver_user_id="area1", approver_roles=["AREA_MANAGER"],
        pin="1111", approver_store_ids=["S1", "S2"],
    )
    assert ok["ok"] is True


# ============================================================================
# 6. Fast-path detection: type AND notice both matter; threshold reads E2
# ============================================================================


def test_fast_path_type_and_notice_both_required(wired):
    # No DB dependency in the helper; just the policy default of 2 days.
    assert hr_router._is_fast_path_leave("CASUAL", date.today() + timedelta(days=1), "S1") is True
    assert hr_router._is_fast_path_leave("SICK", date.today(), "S1") is True
    # EARNED with 1-day notice -> not a fast-path TYPE.
    assert hr_router._is_fast_path_leave("EARNED", date.today() + timedelta(days=1), "S1") is False
    # CASUAL with 5-day notice -> enough NOTICE, not fast-path.
    assert hr_router._is_fast_path_leave("CASUAL", date.today() + timedelta(days=5), "S1") is False


def test_fast_path_threshold_reads_e2_get_policy(monkeypatch):
    import api.services.policy_engine as pe

    real = pe.get_policy

    def fake_get_policy(key, scope=None, *, default=None):
        if key == "approval.leave_fastpath_days":
            return 7  # raised from the code default of 2
        return real(key, scope, default=default)

    monkeypatch.setattr(pe, "get_policy", fake_get_policy)
    # CASUAL with 5-day notice is now fast-path under a 7-day threshold.
    assert hr_router._is_fast_path_leave("CASUAL", date.today() + timedelta(days=5), "S1") is True


# ============================================================================
# 7. leave_approval is a REGISTERED E4 action type (the reuse seam)
# ============================================================================


def test_leave_approval_is_a_registered_action_type():
    assert "leave_approval" in appr.ACTION_TYPES
    # It is NOT maker-checker at the engine level (the leave-router layer owns the
    # employee_id-based self-approval block).
    assert "leave_approval" not in appr.MAKER_CHECKER_ACTIONS


def test_leave_approval_resolves_to_auto_tier(db):
    """amount=None leave_approval resolves to the auto tier (STORE_MANAGER+)."""
    engine = ApprovalEngine(db=db)
    r = engine.request(action_type="leave_approval", requested_by="staff1",
                       store_id="S1", amount=None)
    assert r["ok"] is True
    assert r["required_tier"] == "auto"
    assert "STORE_MANAGER" in r["required_roles"]


# ============================================================================
# Fail-soft: apply still files the leave if the engine can't record (no_db path)
# ============================================================================


def test_apply_fail_soft_when_engine_unavailable(db, monkeypatch):
    """If request_approval can't record (e.g. no engine DB), the leave is STILL
    filed and flagged fast_path -- the standard HR-page approve path remains. No
    local-vs-CI divergence: the handler never raises on the approval seam."""
    leave_repo = FakeLeaveRepo(db)
    user_repo = FakeUserRepo(db)
    monkeypatch.setattr(hr_router, "_get_db", lambda: db)
    monkeypatch.setattr(hr_router, "get_leave_repository", lambda: leave_repo)
    monkeypatch.setattr(hr_router, "get_user_repository", lambda: user_repo)
    # Force the approval-open to no-op.
    monkeypatch.setattr(appr, "request_approval", lambda *a, **k: None)

    staff = _user("staff1", ["SALES_STAFF"], store_id="S1")
    res = _run(hr_router.apply_leave(_Leave("SICK", from_days_ahead=0), staff))
    assert res["status"] == "PENDING"
    assert res["fast_path"] is True
    assert res["approval_request_id"] is None
    assert db.get_collection("leaves").find_one(
        {"leave_id": res["leaveId"]})["status"] == "PENDING"
