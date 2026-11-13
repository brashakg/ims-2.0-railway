"""
Codex whole-app audit, batch b10 -- approvals and the audit chain
=================================================================
L3-01  a second concurrent pending approval failed (503 on a second journal
       submission, 400 write_failed on a second petty-cash request): the
       pending row stored an explicit ``approval_token: null`` and the unique
       SPARSE index on that field indexes an explicit null.
L3-25  approving a journal's request in the approvals inbox left the journal
       SUBMITTED: Post refused not_approved, the journal's own Approve refused
       already_reviewed.
R19    two legitimate concurrent audit writes both chained from the same
       predecessor, so verify reported a break nobody had made.
R20    an audit trail that could not be read was reported intact.

Every DB-backed test runs TWICE: against an in-memory double that enforces
the unique indexes real Mongo holds (strict_fakes + _UniqueColl), and against
a real throwaway MongoDB (CI: localhost:27017; skipped when none answers).
The real services run in both -- ApprovalEngine, je_service, AuditRepository
and the route handlers themselves.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ENABLE_MANUAL_JE", "1")

from strict_fakes import StrictCollection, StrictDB  # noqa: E402

from api.routers import approvals as approvals_router  # noqa: E402
from api.routers import audit as audit_router  # noqa: E402
from api.routers.auth import hash_password  # noqa: E402
from api.routers.finance import journal_entries as je_router  # noqa: E402
from api.services import approvals as appr  # noqa: E402
from api.services import je_service  # noqa: E402
from database.repositories import audit_chain  # noqa: E402
from database.repositories.audit_chain import (  # noqa: E402
    append_audit_entry,
    verify_chain,
)
from database.repositories.audit_repository import AuditRepository  # noqa: E402

PIN = "4821"


# ===========================================================================
# The two databases
# ===========================================================================


class _UniqueColl(StrictCollection):
    """StrictCollection + the unique SPARSE indexes real Mongo holds on the
    collection. Sparse skips a row only when the field is MISSING: an explicit
    null is indexed like any other value (the L3-01 root cause)."""

    def __init__(self, name: str, unique_sparse=()):
        super().__init__(name)
        self._unique_sparse = tuple(unique_sparse)

    def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError

        for field in self._unique_sparse:
            if field in doc and any(
                field in d and d[field] == doc[field] for d in self.docs
            ):
                raise DuplicateKeyError(
                    f"E11000 duplicate key error: {field}: {doc[field]!r}", 11000
                )
        return super().insert_one(doc)


class _FakeDB(StrictDB):
    _UNIQUE = {"approval_requests": ("approval_token",), "audit_logs": ("seq",)}

    def get_collection(self, name):
        if name not in self._collections:
            coll = _UniqueColl(name, self._UNIQUE.get(name, ()))
            coll.database = self  # AuditRepository reaches the chain head through it
            self._collections[name] = coll
        return self._collections[name]


_NO_MONGO = []  # the reason, once a probe has failed (probe once per session)


def _real_mongo():
    """(client, db, name) on a real MongoDB, or skip. IMS_TEST_MONGO_DB pins
    the database name (a local probe database); the default is a throwaway."""
    if _NO_MONGO:
        pytest.skip(_NO_MONGO[0])
    try:
        from pymongo import MongoClient

        client = MongoClient(
            os.getenv("MONGO_HOST", "127.0.0.1"),
            int(os.getenv("MONGO_PORT", "27017")),
            serverSelectionTimeoutMS=1500,
        )
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        _NO_MONGO.append(f"no real mongo ({str(exc)[:80]})")
        pytest.skip(_NO_MONGO[0])
    name = os.getenv("IMS_TEST_MONGO_DB") or f"ims_test_b10_{uuid.uuid4().hex[:8]}"
    assert name.startswith(("ims_test_", "ims_claude_")), name  # never a real database
    client.drop_database(name)
    return client, client[name], name


@pytest.fixture
def mongo_db():
    client, real, name = _real_mongo()
    appr.ApprovalEngine(db=real).ensure_indexes()  # as app startup does (api/main.py)
    try:
        yield real
    finally:
        client.drop_database(name)
        client.close()


@pytest.fixture(params=["fake", "mongo"])
def db(request):
    """Both databases. The real one is fetched lazily, so the in-memory half
    still runs on a PC with no MongoDB (conftest skips real-Mongo fixtures)."""
    if request.param == "fake":
        return _FakeDB()
    return request.getfixturevalue("mongo_db")


# ===========================================================================
# Helpers
# ===========================================================================


def _user(db, user_id: str, roles) -> Dict[str, Any]:
    """Seed a user with an approver PIN; returns the current_user dict a route
    handler receives."""
    db.get_collection("users").insert_one({
        "user_id": user_id, "is_active": True, "roles": list(roles),
        "store_ids": ["S1"],
        "approval_pin_hash": hash_password(PIN),
        "pin_attempts": {"count": 0, "window_start": appr._now()},
    })
    return {"user_id": user_id, "roles": list(roles), "store_ids": ["S1"],
            "active_store_id": "S1"}


def _engine(db) -> appr.ApprovalEngine:
    eng = appr.ApprovalEngine(db=db)
    eng.ensure_indexes()
    return eng


def _submitted_journal(db, maker_id: str = "maker", amount: float = 100.0) -> Dict[str, str]:
    """A balanced journal, created and submitted by ``maker_id`` through the
    real service. Returns {"je_id", "request_id"}."""
    if db.get_collection("chart_of_accounts").find_one({}) is None:
        je_service.seed_chart_of_accounts(db)
    res = je_service.create_je(
        db, store_id="S1", entity_id=None, entry_date=datetime(2026, 5, 15),
        description="Depreciation for May 2026",
        lines=[{"account_code": "5001", "debit": amount, "credit": 0},
               {"account_code": "2001", "debit": 0, "credit": amount}],
        maker_id=maker_id, maker_name=maker_id,
    )
    assert res["ok"], res
    je_id = res["je"]["je_id"]
    sub = je_service.submit_je(db, je_id=je_id, maker_id=maker_id, maker_roles=["ACCOUNTANT"])
    assert sub["ok"], sub
    return {"je_id": je_id, "request_id": sub["request_id"]}


def _je(db, je_id: str) -> Dict[str, Any]:
    return je_service.get_je(db, je_id)


def _request(db, request_id: str) -> Dict[str, Any]:
    return db.get_collection("approval_requests").find_one({"request_id": request_id})


def _call(coro):
    """Run one async route handler; returns (status_code, body)."""
    try:
        return 200, asyncio.run(coro)
    except HTTPException as exc:
        return exc.status_code, exc.detail


def _inbox_approve(db, monkeypatch, request_id: str, user: Dict[str, Any]):
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    return _call(approvals_router.approve_request(
        request_id, approvals_router.PinAction(pin=PIN), current_user=user))


def _inbox_reject(db, monkeypatch, request_id: str, user: Dict[str, Any], reason: str):
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    return _call(approvals_router.reject_request(
        request_id, approvals_router.RejectAction(pin=PIN, reason=reason), current_user=user))


def _post(db, je_id: str, poster: str = "checker") -> Dict[str, Any]:
    return je_service.post_je(db, je_id=je_id, poster_id=poster)


def _journal_approve(db, je_id: str, approver: str = "checker", roles=("ADMIN",)) -> Dict[str, Any]:
    return je_service.approve_je(db, je_id=je_id, approver_id=approver,
                                 approver_roles=list(roles), pin=PIN)


def _audit_row(i: int) -> Dict[str, Any]:
    return {"log_id": f"B10-{uuid.uuid4().hex[:12]}", "action": "AUDIT_SYNTHETIC_ONLY",
            "user_id": "audit-probe", "entity_type": "probe", "entity_id": f"P-{i}",
            "severity": "INFO"}


def _chained(db):
    return list(db.get_collection("audit_logs").find({"seq": {"$exists": True}}).sort("seq", 1))


# ===========================================================================
# L3-01 -- a second pending approval
# ===========================================================================


def test_l3_01_second_pending_request_is_recorded(db):
    """Root's proof: first pending petty-cash request 200, second 400
    write_failed. Two requests must be able to wait at once."""
    eng = _engine(db)
    first = eng.request(action_type="petty_cash", requested_by="u1", store_id="S1", amount=300)
    second = eng.request(action_type="petty_cash", requested_by="u2", store_id="S1", amount=400)
    assert first.get("ok") is True, first
    assert second.get("ok") is True, second  # was {"ok": False, "error": "write_failed"}
    assert first["request_id"] != second["request_id"]
    rows = list(db.get_collection("approval_requests").find({"status": "REQUESTED"}))
    assert len(rows) == 2
    # The root cause, pinned: a pending row carries NO approval_token key (the
    # unique sparse index indexes an explicit null).
    assert all("approval_token" not in r for r in rows)


def test_l3_01_two_journals_can_wait_for_approval_together(db):
    """Lane 3's proof: the second journal submission answered 503
    approval_request_failed while the first was still pending."""
    first = _submitted_journal(db, "maker")
    second = _submitted_journal(db, "maker")  # asserts submit ok
    assert first["request_id"] != second["request_id"]
    assert _je(db, first["je_id"])["status"] == "SUBMITTED"
    assert _je(db, second["je_id"])["status"] == "SUBMITTED"


def test_l3_01_row_stored_before_the_fix_blocks_nothing(db):
    """A rejected request written before the fix keeps its explicit null. New
    pending requests carry no token key, so they do not meet it in the index
    (no stored row is rewritten)."""
    eng = _engine(db)
    db.get_collection("approval_requests").insert_one({
        "request_id": "REQ-legacy", "action_type": "petty_cash", "status": "REJECTED",
        "requested_by": "old", "approval_token": None, "consumed": False,
        "created_at": appr._now(), "expires_at": appr._now(),
    })
    a = eng.request(action_type="petty_cash", requested_by="u1", store_id="S1", amount=300)
    b = eng.request(action_type="journal_entry", requested_by="u2", store_id="S1", amount=300)
    assert a.get("ok") is True and b.get("ok") is True, (a, b)
    assert _request(db, "REQ-legacy")["approval_token"] is None  # untouched


def test_l3_01_each_approval_still_mints_its_own_token(db):
    """The token index still does its job: two approvals, two distinct tokens,
    each readable back by its token."""
    eng = _engine(db)
    _user(db, "boss", ["ADMIN"])
    ids = [eng.request(action_type="petty_cash", requested_by=f"u{i}", store_id="S1",
                       amount=300)["request_id"] for i in (1, 2)]
    tokens = []
    for rid in ids:
        res = eng.approve(rid, approver_user_id="boss", approver_roles=["ADMIN"], pin=PIN)
        assert res.get("ok") is True, res
        tokens.append(res["approval_token"])
    assert tokens[0] != tokens[1]
    for rid, tok in zip(ids, tokens):
        assert db.get_collection("approval_requests").find_one(
            {"approval_token": tok})["request_id"] == rid


# ===========================================================================
# L3-25 -- the approvals inbox and the journal screen are one decision
# ===========================================================================


def test_l3_25_inbox_approval_moves_the_journal_and_it_posts_once(db, monkeypatch):
    """The audit's sequence: approve the journal's request in the generic
    inbox, then Post. Post answered 409 not_approved (journal still SUBMITTED)
    and the journal's own Approve answered 409 already_reviewed."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")

    code, body = _inbox_approve(db, monkeypatch, j["request_id"], checker)
    assert code == 200, body

    je = _je(db, j["je_id"])
    assert je["status"] == "APPROVED"       # was SUBMITTED
    assert je["checker_id"] == "checker"    # a different person from the maker
    assert je["maker_id"] == "maker"
    assert je["checked_at"] is not None

    posted = _post(db, j["je_id"])
    assert posted.get("ok") is True, posted  # was 409 not_approved
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert _request(db, j["request_id"])["status"] == "CONSUMED"

    # Exactly one post.
    again = _post(db, j["je_id"])
    assert (again["http"], again["error"]) == (409, "already_posted")


def test_l3_25_journal_approve_after_inbox_approval_reports_approved(db, monkeypatch):
    """Repeated approval through the other door: no dead end, no second
    approval -- the journal is already APPROVED and says so."""
    checker = _user(db, "checker", ["ADMIN"])
    _user(db, "checker2", ["SUPERADMIN"])
    j = _submitted_journal(db, "maker")
    assert _inbox_approve(db, monkeypatch, j["request_id"], checker)[0] == 200

    res = _journal_approve(db, j["je_id"], "checker2", ("SUPERADMIN",))
    assert (res["http"], res["error"], res["status"]) == (409, "not_submitted", "APPROVED")
    assert _je(db, j["je_id"])["checker_id"] == "checker"  # the first decision stands
    # ... and approving the request again in the inbox is refused too.
    code, body = _inbox_approve(db, monkeypatch, j["request_id"],
                                {"user_id": "checker2", "roles": ["SUPERADMIN"], "store_ids": []})
    assert code == 409 and body["error"] == "already_reviewed"


def test_l3_25_inbox_rejection_moves_the_journal(db, monkeypatch):
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    code, body = _inbox_reject(db, monkeypatch, j["request_id"], checker, "Wrong account head")
    assert code == 200, body
    je = _je(db, j["je_id"])
    assert je["status"] == "REJECTED"       # was SUBMITTED
    assert je["checker_id"] == "checker"
    assert je["checker_note"] == "Wrong account head"
    assert _post(db, j["je_id"])["error"] == "not_approved"


def test_l3_25_journal_catches_up_when_its_own_write_was_lost(db):
    """Lost response / retry. The request is APPROVED but the journal write
    never landed (two documents, no transaction) -- the state the inbox used to
    leave behind. Any journal door now catches the journal up."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    res = appr.ApprovalEngine(db=db).approve(
        j["request_id"], approver_user_id="checker", approver_roles=["ADMIN"], pin=PIN)
    assert res.get("ok") is True, res
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"  # the stranded state

    posted = _post(db, j["je_id"])
    assert posted.get("ok") is True, posted
    je = _je(db, j["je_id"])
    assert (je["status"], je["checker_id"]) == ("POSTED", "checker")


def test_l3_25_post_refuses_an_approval_spent_elsewhere(db):
    """Post spends the journal's approval itself, for the journal's own total.
    An approval already spent through another door (the generic consume route,
    which checks neither) posts nothing: 409 already_consumed, as before."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    spent = appr.ApprovalEngine(db=db).consume_approval(
        consumed_by="maker", action_type="journal_entry", request_id=j["request_id"])
    assert spent.get("ok") is True, spent

    res = _post(db, j["je_id"])
    assert (res["http"], res["error"]) == (409, "already_consumed")
    assert _je(db, j["je_id"])["status"] == "APPROVED"
    assert not [r for r in db.get_collection("audit_logs").find({"action": "je_posted"})
                if r.get("entity_id") == j["je_id"]]


def test_l3_25_maker_can_never_be_the_checker(db, monkeypatch):
    """The two-person rule survives the repair, through both doors and even
    against a request that names the maker as its reviewer."""
    maker = _user(db, "maker", ["ADMIN"])
    j = _submitted_journal(db, "maker")

    code, body = _inbox_approve(db, monkeypatch, j["request_id"], maker)
    assert code == 403 and body["error"] == "cannot_approve_own"
    res = _journal_approve(db, j["je_id"], "maker")
    assert (res["http"], res["error"]) == (403, "cannot_approve_own")
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"

    # A request row that (somehow) records the maker as its reviewer is never
    # taken as the journal's decision.
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]},
        {"$set": {"status": "APPROVED", "reviewed_by": "maker",
                  "reviewed_by_roles": ["ADMIN"], "approval_token": "APT-forged"}})
    assert _post(db, j["je_id"])["error"] == "not_approved"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"


def _bells(db, request_id: str) -> set:
    return {n["user_id"] for n in db.get_collection("notifications").find({"request_id": request_id})}


def _inbox_row(db, monkeypatch, request_id: str, user: Dict[str, Any]):
    """The caller's inbox row for the request (None when it is not listed)."""
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    code, body = _call(approvals_router.get_inbox(store_id=None, status=None, current_user=user))
    assert code == 200, body
    return next((r for r in body["requests"] if r["request_id"] == request_id), None)


def _pin_count(db, user_id: str) -> int:
    return db.get_collection("users").find_one({"user_id": user_id})["pin_attempts"]["count"]


def test_l3_25_a_journal_request_goes_to_admin_and_superadmin_only(db, monkeypatch):
    """Decision D4. Only ADMIN / SUPERADMIN may approve a journal, yet a small
    journal's request rang store and area managers and gave them Approve /
    Reject; their click decided the request and stranded the journal SUBMITTED
    for good. They now get neither the bell nor the buttons, and the engine
    refuses them. Every other kind of request reaches managers as before."""
    store_mgr = _user(db, "store-mgr", ["STORE_MANAGER"])
    area_mgr = _user(db, "area-mgr", ["AREA_MANAGER"])
    admin = _user(db, "admin", ["ADMIN"])
    boss = _user(db, "boss", ["SUPERADMIN"])
    j = _submitted_journal(db, "maker", amount=100.0)  # the manager amount tier
    rid = j["request_id"]

    assert set(_request(db, rid)["required_roles"]) == {"ADMIN", "SUPERADMIN"}
    assert _bells(db, rid) == {"admin", "boss"}          # was all four

    for manager in (store_mgr, area_mgr):
        assert _inbox_row(db, monkeypatch, rid, manager) is None   # no row, no buttons
        code, body = _inbox_approve(db, monkeypatch, rid, manager)
        assert (code, body["error"]) == (404, "not_found")   # was 200
        code, body = _inbox_reject(db, monkeypatch, rid, manager, "Not a valid entry")
        assert (code, body["error"]) == (404, "not_found")   # was 200
        assert _pin_count(db, manager["user_id"]) == 0
    assert _request(db, rid)["status"] == "REQUESTED"
    assert (_je(db, j["je_id"])["status"], _je(db, j["je_id"])["checker_id"]) == ("SUBMITTED", None)

    # The checker roles keep the buttons, and their decision is the journal's.
    assert _inbox_row(db, monkeypatch, rid, boss)["can_approve"] is True
    assert _inbox_row(db, monkeypatch, rid, admin)["can_approve"] is True
    assert _inbox_approve(db, monkeypatch, rid, admin)[0] == 200
    assert (_je(db, j["je_id"])["status"], _je(db, j["je_id"])["checker_id"]) == ("APPROVED", "admin")

    # Not widened: a journal above the Admin amount tier is still the
    # Superadmin's alone.
    big = _submitted_journal(db, "maker", amount=500000.0)
    assert _request(db, big["request_id"])["required_roles"] == ["SUPERADMIN"]
    code, body = _inbox_approve(db, monkeypatch, big["request_id"], admin)
    assert (code, body["error"]) == (403, "insufficient_tier")

    # Any other request still reaches the managers of its shop.
    other = _engine(db).request(action_type="petty_cash", requested_by="u1",
                                store_id="S1", amount=300)["request_id"]
    assert {"STORE_MANAGER", "AREA_MANAGER"} <= set(_request(db, other)["required_roles"])
    assert {"store-mgr", "area-mgr"} <= _bells(db, other)
    assert _inbox_row(db, monkeypatch, other, store_mgr)["can_approve"] is True
    assert _inbox_approve(db, monkeypatch, other, store_mgr)[0] == 200


def _inbox_ids(db, monkeypatch, user: Dict[str, Any], status=None) -> set:
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    code, body = _call(approvals_router.get_inbox(store_id=None, status=status, current_user=user))
    assert code == 200, body
    return {r["request_id"] for r in body["requests"]}


def _get_by_id(db, monkeypatch, request_id: str, user: Dict[str, Any]):
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    return _call(approvals_router.get_request(request_id, current_user=user))


def test_l3_25_a_journal_request_is_read_only_by_the_journal_readers(db, monkeypatch):
    """D4 follow-through (DECISION, owner ruling 2026-10-18 FU-17: store and
    area managers no longer read journal vouchers). A manager who may not act
    on a journal's request no longer finds it listed, counted or opened by id
    -- it answers what a missing request answers, as an 'rtv' does for them.
    The journal readers (Admin, Superadmin, Accountant) still see it; the
    maker still opens their own; every other request reaches managers as
    before."""
    store_mgr = _user(db, "store-mgr", ["STORE_MANAGER"])
    area_mgr = _user(db, "area-mgr", ["AREA_MANAGER"])
    staff = _user(db, "staff", ["SALES_STAFF"])
    accountant = _user(db, "acct", ["ACCOUNTANT"])
    admin = _user(db, "admin", ["ADMIN"])
    j = _submitted_journal(db, "maker", amount=100.0)
    rid = j["request_id"]
    other = _engine(db).request(action_type="petty_cash", requested_by="u1",
                                store_id="S1", amount=300)["request_id"]

    for manager in (store_mgr, area_mgr):
        for status in (None, "ALL"):
            ids = _inbox_ids(db, monkeypatch, manager, status)
            assert rid not in ids          # was listed (without buttons)
            assert other in ids            # unchanged
        assert _engine(db).pending_count(manager["roles"], ["S1"]) == 1   # was 2
    for reader in (store_mgr, area_mgr, staff):
        code, body = _get_by_id(db, monkeypatch, rid, reader)
        assert code == 404, body           # was 200 with number, description, amount

    # The journal readers and the maker keep what they had.
    assert _inbox_row(db, monkeypatch, rid, accountant)["can_approve"] is False
    assert _inbox_row(db, monkeypatch, rid, admin)["can_approve"] is True
    assert _get_by_id(db, monkeypatch, rid, accountant)[0] == 200
    assert _get_by_id(db, monkeypatch, rid,
                      {"user_id": "maker", "roles": ["ACCOUNTANT"], "store_ids": ["S1"]})[0] == 200

    # Spending it by id is refused to a manager the same way, approval untouched.
    assert _inbox_approve(db, monkeypatch, rid, admin)[0] == 200
    code, body = _call(approvals_router.consume_request(
        rid, approvals_router.ConsumeAction(action_type="journal_entry"), current_user=store_mgr))
    assert code == 404, body
    assert _request(db, rid)["status"] == "APPROVED"
    assert _post(db, j["je_id"], "admin").get("ok") is True


_ALL_FOUR = ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"]


def test_l3_25_a_request_raised_before_the_change_is_held_to_the_checkers_too(db, monkeypatch):
    """A journal request still waiting when the change lands is stored with
    the managers among its roles. The rule reads what the request is for, not
    those stored roles, so the manager has no buttons on it either -- and the
    row itself is left as it was stored."""
    manager = _user(db, "manager", ["STORE_MANAGER"])
    admin = _user(db, "admin", ["ADMIN"])
    j = _submitted_journal(db, "maker", amount=100.0)
    rid = j["request_id"]
    db.get_collection("approval_requests").update_one(
        {"request_id": rid}, {"$set": {"required_roles": _ALL_FOUR}})

    assert _inbox_row(db, monkeypatch, rid, manager) is None
    code, body = _inbox_approve(db, monkeypatch, rid, manager)
    assert (code, body["error"]) == (404, "not_found")
    code, body = _inbox_reject(db, monkeypatch, rid, manager, "Not a valid entry")
    assert (code, body["error"]) == (404, "not_found")
    assert _request(db, rid)["status"] == "REQUESTED"
    assert _request(db, rid)["required_roles"] == _ALL_FOUR   # stored history untouched

    # The Admin's own approval settles both the request and the journal.
    assert _inbox_approve(db, monkeypatch, rid, admin)[0] == 200
    assert _request(db, rid)["status"] == "APPROVED"
    assert (_je(db, j["je_id"])["status"], _je(db, j["je_id"])["checker_id"]) == ("APPROVED", "admin")
    assert _post(db, j["je_id"], "admin").get("ok") is True


def _strand(db, how: str) -> Dict[str, Any]:
    """A request / journal pair as the old inbox left it: the request decided,
    the journal still SUBMITTED. Rows as main stored them (the managers among
    the roles, no reviewed_by_roles). Returns the pair's ids plus the request
    row as stored."""
    j = _submitted_journal(db, "maker", amount=100.0)
    decided = {
        "manager approved": {"status": "APPROVED", "reviewed_by": "manager",
                             "approval_token": f"APT-{uuid.uuid4().hex}"},
        "manager rejected": {"status": "REJECTED", "reviewed_by": "manager", "reject_reason": ""},
        "admin approved": {"status": "APPROVED", "reviewed_by": "old-admin",
                           "approval_token": f"APT-{uuid.uuid4().hex}"},
        "admin rejected, no note": {"status": "REJECTED", "reviewed_by": "old-admin",
                                    "reject_reason": ""},
    }[how]
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]},
        {"$set": {"required_roles": _ALL_FOUR, "reviewed_at": appr._now(), **decided}})
    return {**j, "stored": _request(db, j["request_id"])}


_STRANDED = ["manager approved", "manager rejected", "admin approved", "admin rejected, no note"]


@pytest.mark.parametrize("how", _STRANDED)
def test_l3_25_admin_approval_settles_a_pair_stranded_before_the_change(db, how):
    """The pair is stuck: Post refuses not_approved and the journal's own
    Approve refused already_reviewed for good. The Admin's own approval on the
    journal screen now raises the journal's request again and decides THAT one
    with their PIN: request and journal settle together, the journal posts
    once, and the old request row is left exactly as it was stored."""
    _user(db, "checker", ["ADMIN"])
    s = _strand(db, how)
    assert _post(db, s["je_id"])["error"] == "not_approved"
    assert _je(db, s["je_id"])["status"] == "SUBMITTED"     # the earlier decision is never taken

    res = _journal_approve(db, s["je_id"])
    assert res.get("ok") is True, res                        # was 409 already_reviewed
    je = _je(db, s["je_id"])
    assert (je["status"], je["checker_id"]) == ("APPROVED", "checker")
    new_id = je["approval_request_id"]
    assert new_id != s["request_id"]
    new = _request(db, new_id)
    assert (new["status"], new["reviewed_by"], new["requested_by"]) == ("APPROVED", "checker", "maker")
    assert (new["amount"], new["context"]["je_id"]) == (100.0, s["je_id"])
    assert set(new["required_roles"]) == {"ADMIN", "SUPERADMIN"}
    assert _request(db, s["request_id"]) == s["stored"]      # history not rewritten
    raised = [r for r in db.get_collection("audit_logs").find({"action": "je_request_raised_again"})
              if r.get("entity_id") == s["je_id"]]
    assert len(raised) == 1 and raised[0]["user_id"] == "checker"
    assert s["request_id"] in raised[0]["reason"]

    assert _post(db, s["je_id"]).get("ok") is True
    assert _je(db, s["je_id"])["status"] == "POSTED"
    assert _request(db, new_id)["status"] == "CONSUMED"
    assert _post(db, s["je_id"])["error"] == "already_posted"
    assert _request(db, s["request_id"]) == s["stored"]


@pytest.mark.parametrize("how", _STRANDED)
def test_l3_25_admin_rejection_settles_a_pair_stranded_before_the_change(db, how):
    _user(db, "checker", ["ADMIN"])
    s = _strand(db, how)

    res = je_service.reject_je(db, je_id=s["je_id"], approver_id="checker",
                               approver_roles=["ADMIN"], pin=PIN, note="Wrong account head")
    assert res.get("ok") is True, res                        # was 409 already_reviewed
    je = _je(db, s["je_id"])
    assert (je["status"], je["checker_id"], je["checker_note"]) == (
        "REJECTED", "checker", "Wrong account head")
    new = _request(db, je["approval_request_id"])
    assert je["approval_request_id"] != s["request_id"]
    assert (new["status"], new["reviewed_by"], new["reject_reason"]) == (
        "REJECTED", "checker", "Wrong account head")
    assert _request(db, s["request_id"]) == s["stored"]      # history not rewritten
    assert _post(db, s["je_id"])["error"] == "not_approved"


def test_l3_25_settling_a_stranded_pair_keeps_every_journal_gate(db, monkeypatch):
    """The way forward is the Admin's own decision and nothing less: not the
    maker's, not a manager's, not without the PIN; and a failed try leaves one
    open request behind, not a pile."""
    _user(db, "maker", ["ADMIN"])
    _user(db, "manager", ["STORE_MANAGER"])
    admin = _user(db, "checker", ["ADMIN"])
    s = _strand(db, "manager approved")
    requests = db.get_collection("approval_requests")

    res = _journal_approve(db, s["je_id"], "maker")
    assert (res["http"], res["error"]) == (403, "cannot_approve_own")
    res = _journal_approve(db, s["je_id"], "manager", ("STORE_MANAGER",))
    assert res.get("ok") is not True
    assert requests.count_documents({}) == 1                 # neither raised anything
    assert _je(db, s["je_id"])["approval_request_id"] == s["request_id"]

    res = je_service.approve_je(db, je_id=s["je_id"], approver_id="checker",
                                approver_roles=["ADMIN"], pin="0000")
    assert (res["http"], res["error"]) == (403, "wrong_pin")
    assert _je(db, s["je_id"])["status"] == "SUBMITTED"
    res = je_service.approve_je(db, je_id=s["je_id"], approver_id="checker",
                                approver_roles=["ADMIN"], pin="0000")
    assert (res["http"], res["error"]) == (403, "wrong_pin")
    assert requests.count_documents({}) == 2                 # the old one + one raised again
    new_id = _je(db, s["je_id"])["approval_request_id"]
    assert _request(db, new_id)["status"] == "REQUESTED"

    # The request raised again is an ordinary journal request: the Admin can
    # just as well decide it in the inbox, and the journal follows.
    assert _inbox_row(db, monkeypatch, new_id, admin)["can_approve"] is True
    assert _inbox_approve(db, monkeypatch, new_id, admin)[0] == 200
    assert (_je(db, s["je_id"])["status"], _je(db, s["je_id"])["checker_id"]) == ("APPROVED", "checker")
    assert _request(db, s["request_id"]) == s["stored"]


def test_l3_25_a_request_that_lapsed_undecided_is_not_raised_again(db):
    """Only a request that was DECIDED without the journal is raised again. One
    that lapsed with nobody deciding it is refused as it always was (the
    engine's validity window is not reopened by this change)."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    requests = db.get_collection("approval_requests")
    requests.update_one({"request_id": j["request_id"]},
                        {"$set": {"expires_at": appr._now() - timedelta(minutes=5)}})

    res = _journal_approve(db, j["je_id"])
    assert (res["http"], res["error"]) == (410, "expired")
    je = _je(db, j["je_id"])
    assert (je["status"], je["approval_request_id"]) == ("SUBMITTED", j["request_id"])
    assert requests.count_documents({}) == 1


def test_l3_25_decision_with_no_recorded_checker_role_is_not_taken(db):
    """A request decided before deciders' roles were recorded on it (or by a
    row edit): the journal cannot tell an Admin's decision from a manager's,
    so it takes neither."""
    j = _submitted_journal(db, "maker")
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]},
        {"$set": {"status": "APPROVED", "reviewed_by": "someone", "approval_token": "APT-old"}})
    assert _post(db, j["je_id"])["error"] == "not_approved"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"


def _hijack_request(db, je_id: str, maker_id: str = "maker") -> str:
    """What the generic create route lets any signed-in user raise: a Rs 1
    journal_entry request carrying the journal's dedupe key and naming it."""
    res = _engine(db).request(
        action_type="journal_entry", requested_by=maker_id, store_id="S1", amount=1,
        required_tier="super", dedupe_key=f"je:{je_id}",
        context={"je_id": je_id, "description": "Round-off"})
    assert res.get("ok") is True, res
    return res["request_id"]


def _draft_journal(db, maker_id: str = "maker", amount: float = 500000.0) -> str:
    if db.get_collection("chart_of_accounts").find_one({}) is None:
        je_service.seed_chart_of_accounts(db)
    res = je_service.create_je(
        db, store_id="S1", entity_id=None, entry_date=datetime(2026, 5, 15),
        description="Depreciation for May 2026",
        lines=[{"account_code": "5001", "debit": amount, "credit": 0},
               {"account_code": "2001", "debit": 0, "credit": amount}],
        maker_id=maker_id, maker_name=maker_id)
    assert res["ok"], res
    return res["je"]["je_id"]


def test_l3_25_submit_never_links_a_request_it_did_not_raise(db, monkeypatch):
    """A Rs 5,00,000 journal must not ride a Rs 1 request. The maker plants a
    small request under the journal's dedupe key, then submits: the journal
    gets its OWN request (its total, its tier), and approving the planted one
    moves nothing."""
    boss = _user(db, "boss", ["SUPERADMIN"])
    je_id = _draft_journal(db)
    planted = _hijack_request(db, je_id)

    sub = je_service.submit_je(db, je_id=je_id, maker_id="maker", maker_roles=["ACCOUNTANT"])
    assert sub["ok"], sub
    assert sub["request_id"] != planted          # was the planted request
    own = _request(db, sub["request_id"])
    assert (own["amount"], own["required_tier"]) == (500000.0, "super")
    assert own["context"]["je_id"] == je_id

    assert _inbox_approve(db, monkeypatch, planted, boss)[0] == 200
    assert _je(db, je_id)["status"] == "SUBMITTED"
    assert _request(db, sub["request_id"])["status"] == "REQUESTED"


@pytest.mark.parametrize("field, value", [
    ("amount", 1.0),
    ("requested_by", "someone-else"),
    ("action_type", "petty_cash"),
    ("context", {"je_id": "JE-another"}),
])
def test_l3_25_journal_takes_a_decision_only_from_its_own_request(db, monkeypatch, field, value):
    """A journal already linked to a request that does not bind it (a link
    stored before the submit guard): an inbox approval of that request is not
    the journal's approval, and Post stays refused."""
    boss = _user(db, "boss", ["SUPERADMIN"])
    j = _submitted_journal(db, "maker")
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]}, {"$set": {field: value}})

    assert _inbox_approve(db, monkeypatch, j["request_id"], boss)[0] == 200
    je = _je(db, j["je_id"])
    assert (je["status"], je["checker_id"]) == ("SUBMITTED", None)
    assert _post(db, j["je_id"])["error"] == "not_approved"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"


@pytest.mark.parametrize("reason", ["", "   ", "too short"])
def test_l3_25_inbox_reject_of_a_journal_needs_the_journal_note(db, monkeypatch, reason):
    """One rule, two doors: the journal screen refuses a rejection without a
    checker note of at least 10 characters; the inbox rejected the same journal
    with no reason at all."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")

    code, body = _inbox_reject(db, monkeypatch, j["request_id"], checker, reason)
    assert (code, body["error"]) == (400, "note_required")
    assert _request(db, j["request_id"])["status"] == "REQUESTED"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    # The journal screen says the same thing.
    res = je_service.reject_je(db, je_id=j["je_id"], approver_id="checker",
                               approver_roles=["ADMIN"], pin=PIN, note=reason)
    assert (res["http"], res["error"]) == (400, "note_required")
    # A request that is no journal's approval keeps its optional reason.
    other = _engine(db).request(action_type="petty_cash", requested_by="u1",
                                store_id="S1", amount=300)["request_id"]
    assert _inbox_reject(db, monkeypatch, other, checker, reason)[0] == 200


def test_l3_25_maker_rejecting_their_own_request_does_not_strand_the_journal(db, monkeypatch):
    """An Admin maker may reject (withdraw) their own journal on the journal
    screen. The same rejection made on the request used to leave the journal
    SUBMITTED for good: no door could approve, reject or post it."""
    maker = _user(db, "maker", ["ADMIN"])
    j = _submitted_journal(db, "maker")

    code, body = _inbox_reject(db, monkeypatch, j["request_id"], maker,
                               "Entered against the wrong month")
    assert code == 200, body
    je = _je(db, j["je_id"])
    assert (je["status"], je["checker_id"]) == ("REJECTED", "maker")  # was SUBMITTED
    assert je["checker_note"] == "Entered against the wrong month"

    # ... and when only the request was rejected (the journal write lost), any
    # journal door catches the journal up.
    j2 = _submitted_journal(db, "maker")
    res = appr.ApprovalEngine(db=db).reject(
        j2["request_id"], approver_user_id="maker", approver_roles=["ADMIN"], pin=PIN,
        reason="Entered against the wrong month")
    assert res.get("ok") is True, res
    assert _post(db, j2["je_id"])["status"] == "REJECTED"
    assert _je(db, j2["je_id"])["status"] == "REJECTED"


def test_l3_25_inbox_leaves_the_journal_alone_while_manual_journals_are_off(db, monkeypatch):
    """ENABLE_MANUAL_JE off freezes the journal screen's approve / reject /
    post. The inbox must not move a journal behind that switch: it decides the
    request only, as it always did; the journal catches up once the switch is
    on again."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    monkeypatch.delenv("ENABLE_MANUAL_JE", raising=False)
    assert _inbox_approve(db, monkeypatch, j["request_id"], checker)[0] == 200
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"

    monkeypatch.setenv("ENABLE_MANUAL_JE", "1")
    assert _post(db, j["je_id"]).get("ok") is True
    assert _je(db, j["je_id"])["checker_id"] == "checker"


def test_l3_25_request_naming_someone_elses_journal_changes_nothing(db, monkeypatch):
    """The inbox syncs a journal from the journal's OWN linked request. A
    stray journal_entry request that merely names the journal in its context
    moves nothing when it is approved."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    stray = _engine(db).request(
        action_type="journal_entry", requested_by="someone", store_id="S1", amount=1,
        context={"je_id": j["je_id"]})
    assert stray.get("ok") is True, stray
    assert _inbox_approve(db, monkeypatch, stray["request_id"], checker)[0] == 200
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    assert _request(db, j["request_id"])["status"] == "REQUESTED"


def test_l3_25_period_locked_after_inbox_approval_still_blocks_post(db, monkeypatch):
    """The period-lock race, through the inbox door: approved, then the month
    is locked before Post. Post answers 423, the journal stays APPROVED and the
    approval stays unspent; once the period reopens it posts."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _inbox_approve(db, monkeypatch, j["request_id"], checker)[0] == 200

    monkeypatch.setattr(je_router, "_get_db", lambda: db)

    def _locked(_db, _day):
        raise HTTPException(status_code=423, detail="Accounting period 05/2026 is locked")

    monkeypatch.setattr(je_router, "check_period_locked", _locked)
    code, _body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 423
    assert _je(db, j["je_id"])["status"] == "APPROVED"
    req = _request(db, j["request_id"])
    assert (req["status"], req["consumed"]) == ("APPROVED", False)

    monkeypatch.setattr(je_router, "check_period_locked", lambda _db, _day: None)
    code, body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 200, body
    assert _je(db, j["je_id"])["status"] == "POSTED"


# ===========================================================================
# R19 -- concurrent audit writes
# ===========================================================================


class _OverlapFirstInsert:
    """The audit collection, with the audit's forced interleaving: the first
    writer is held at its insert until ``overlap`` (a second, complete audit
    write) has run. No write result is mocked."""

    def __init__(self, coll, overlap):
        self._coll = coll
        self._overlap = overlap

    def insert_one(self, doc):
        if self._overlap is not None:
            overlap, self._overlap = self._overlap, None
            overlap()
        return self._coll.insert_one(doc)

    def __getattr__(self, name):
        return getattr(self._coll, name)


def test_r19_overlapping_writers_still_form_one_valid_chain(db):
    """Root's proof, deterministic: writer 2 runs start to finish while writer
    1 is between claiming its place and inserting its row. Both rows used to
    chain from the same predecessor -> verify broken_at_seq 2, nothing edited."""
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)

    second = []
    held = _OverlapFirstInsert(
        coll, lambda: second.append(append_audit_entry(coll, _audit_row(2), db=db)))
    first = append_audit_entry(held, _audit_row(1), db=db)

    assert first is not None and second and second[0] is not None
    rows = _chained(db)
    assert [r["seq"] for r in rows] == [1, 2]
    assert rows[1]["prev_hash"] == rows[0]["entry_hash"]  # was: both chained from genesis
    res = verify_chain(coll)
    assert (res["intact"], res["broken_at_seq"], res["entries_checked"]) == (True, None, 2)


def test_r19_parallel_workers_on_real_mongo(mongo_db):
    """Ordinary concurrency on a real MongoDB: 8 workers x 15 audit rows, all
    released together. Every row is kept, every seq is used once, and the
    chain verifies end to end."""
    coll = mongo_db.get_collection("audit_logs")
    repo = AuditRepository(coll)
    workers, each = 8, 15
    gate = threading.Barrier(workers)
    failures = []

    def _work(w: int) -> None:
        gate.wait(timeout=20)
        for i in range(each):
            if repo.create(_audit_row(w * 100 + i)) is None:
                failures.append((w, i))

    threads = [threading.Thread(target=_work, args=(w,)) for w in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not failures
    total = workers * each
    assert [r["seq"] for r in _chained(mongo_db)] == list(range(1, total + 1))
    res = verify_chain(coll)
    assert res["intact"] is True, res
    assert (res["entries_checked"], res["unchained_rows"]) == (total, 0)


def test_r19_refused_row_leaves_the_chain_whole(db):
    """A row the database refuses (here: a duplicate log id) is lost exactly
    as before -- but it takes no place in the chain, so the rows after it
    still link."""
    coll = db.get_collection("audit_logs")
    repo = AuditRepository(coll)
    kept = repo.create(dict(_audit_row(1), log_id="B10-SAME"))
    refused = repo.create(dict(_audit_row(2), log_id="B10-SAME"))
    after = repo.create(_audit_row(3))
    assert kept is not None and refused is None and after is not None
    assert [r["seq"] for r in _chained(db)] == [1, 2]
    assert verify_chain(coll)["intact"] is True


# ===========================================================================
# R20 -- an unreadable trail is not an intact trail
# ===========================================================================


class _Unavailable:
    """Root's stand-in for an audit collection whose reads fail."""

    def find(self, *_a, **_k):
        raise RuntimeError("AUDIT simulated unavailable")

    def count_documents(self, *_a, **_k):
        raise RuntimeError("AUDIT simulated unavailable")


def test_r20_unreadable_trail_is_not_reported_intact():
    res = verify_chain(_Unavailable())
    assert res["intact"] is False   # was True
    assert res["verified"] is False
    assert res["reason"] == "unreadable"
    assert res["entries_checked"] == 0


def test_r20_verify_route_answers_503_when_nothing_could_be_read(monkeypatch):
    superadmin = {"user_id": "owner", "roles": ["SUPERADMIN"]}

    # No audit repository at all (no database) -- used to answer 200 intact.
    monkeypatch.setattr(audit_router, "get_audit_repository", lambda: None)
    code, body = _call(audit_router.verify_audit_trail(current_user=superadmin))
    assert code == 503
    assert (body["intact"], body["verified"], body["reason"]) == (False, False, "unreadable")

    # A repository whose reads fail.
    broken = type("Repo", (), {"collection": _Unavailable()})()
    monkeypatch.setattr(audit_router, "get_audit_repository", lambda: broken)
    code, body = _call(audit_router.verify_audit_trail(current_user=superadmin))
    assert code == 503 and body["intact"] is False


def test_r20_readable_trail_reports_what_it_checked(db, monkeypatch):
    """A trail that WAS read answers 200 with the whole picture: an empty one
    is verified-empty, and rows outside the hash chain are counted, not
    silently skipped."""
    coll = db.get_collection("audit_logs")
    res = verify_chain(coll)
    assert (res["intact"], res["verified"], res["entries_checked"]) == (True, True, 0)

    repo = AuditRepository(coll)
    for i in range(3):
        assert repo.create(_audit_row(i)) is not None
    coll.insert_one({"log_id": "B10-UNCHAINED", "action": "legacy.unchained"})  # no seq

    monkeypatch.setattr(audit_router, "get_audit_repository", lambda: repo)
    code, body = _call(audit_router.verify_audit_trail(
        current_user={"user_id": "owner", "roles": ["SUPERADMIN"]}))
    assert code == 200, body
    assert (body["intact"], body["verified"], body["reason"]) == (True, True, None)
    assert (body["entries_checked"], body["unchained_rows"]) == (3, 1)


def test_r20_trail_missing_its_last_rows_is_not_intact(db):
    """A readable prefix is not the whole trail: with the newest row removed
    the remaining rows still link perfectly, and verify used to call that
    intact. The chain head remembers the newest row."""
    coll = db.get_collection("audit_logs")
    repo = AuditRepository(coll)
    for i in range(4):
        assert repo.create(_audit_row(i)) is not None
    assert verify_chain(coll)["intact"] is True

    coll.delete_one({"seq": 4})  # out-of-band removal of the newest row
    res = verify_chain(coll)
    assert res["intact"] is False   # was True, entries_checked 3
    assert (res["verified"], res["reason"], res["entries_checked"]) == (
        True, "head_not_in_trail", 3)


def _head(db) -> Dict[str, Any]:
    return db.get_collection("audit_chain_head").find_one({"_id": "primary"}) or {}


@pytest.mark.parametrize("removed", [1, 2])
def test_r20_removed_tail_stays_reported_after_later_writes(db, removed):
    """Review round: the newest rows are removed, then the shop carries on.
    The new rows link cleanly from the surviving tail and reuse the removed
    seqs, so once the chain regrew past the old head verify answered intact
    again (delete K rows, wait K+1 writes). A writer must never move the head
    off a row that is no longer in the trail: the break stays reported for
    good, as it did before this batch."""
    coll = db.get_collection("audit_logs")
    repo = AuditRepository(coll)
    for i in range(4):
        assert repo.create(_audit_row(i)) is not None
    lost = _chained(db)[-1]
    assert (_head(db)["seq"], _head(db)["last_hash"]) == (4, lost["entry_hash"])

    coll.delete_many({"seq": {"$gt": 4 - removed}})  # out-of-band removal of the tail
    for n in range(removed + 3):                      # well past the old head
        assert repo.create(_audit_row(10 + n)) is not None
        res = verify_chain(coll)
        assert (res["intact"], res["verified"], res["reason"]) == (
            False, True, "head_not_in_trail"), (n, res)
    # The surviving + new rows do link; the head still names the removed row.
    assert [r["seq"] for r in _chained(db)] == list(range(1, 4 + 3 + 1))
    assert (_head(db)["seq"], _head(db)["last_hash"]) == (4, lost["entry_hash"])


@pytest.mark.parametrize("legacy", ["failed_inserts", "writer_died_before_head", "head_lagging"])
def test_r20_a_head_left_behind_or_ahead_by_old_writes_is_not_a_removed_tail(db, legacy):
    """The other side of the same rule: a head document that does not name
    the tail for an INNOCENT reason must still move on, or a later removal
    would go unseen (and an honest trail must never be called broken).
      failed_inserts           the old writer claimed seqs on the head, the
                               inserts failed: head.seq is ahead of the tail,
                               its hash is the tail's
      writer_died_before_head  the old writer inserted its row and died before
                               committing the hash: head.seq is the tail's,
                               its hash is the row before
      head_lagging             a writer died between its row and the head note
    """
    coll = db.get_collection("audit_logs")
    heads = db.get_collection("audit_chain_head")
    repo = AuditRepository(coll)
    for i in range(3):
        assert repo.create(_audit_row(i)) is not None
    rows = _chained(db)
    stale = {
        "failed_inserts": {"seq": 5, "last_hash": rows[2]["entry_hash"]},
        "writer_died_before_head": {"seq": 3, "last_hash": rows[1]["entry_hash"]},
        "head_lagging": {"seq": 1, "last_hash": rows[0]["entry_hash"]},
    }[legacy]
    heads.update_one({"_id": "primary"}, {"$set": stale})
    assert verify_chain(coll)["intact"] is True

    newest = None
    for n in range(3):
        newest = repo.create(_audit_row(20 + n))
        assert newest is not None
        res = verify_chain(coll)
        assert (res["intact"], res["entries_checked"]) == (True, 4 + n), (n, res)
    # The head caught up with the newest row ...
    assert (_head(db)["seq"], _head(db)["last_hash"]) == (6, newest["entry_hash"])
    # ... so removing that row is seen, and stays seen.
    coll.delete_one({"seq": 6})
    assert verify_chain(coll)["reason"] == "head_not_in_trail"
    assert repo.create(_audit_row(30)) is not None
    assert repo.create(_audit_row(31)) is not None
    assert verify_chain(coll)["reason"] == "head_not_in_trail"


def test_r20_a_slower_writer_never_moves_the_head_back(db):
    """Two writers finish out of order: the one that wrote the earlier row
    notes the head last. The head stays on the newer row."""
    coll = db.get_collection("audit_logs")
    repo = AuditRepository(coll)
    for i in range(3):
        assert repo.create(_audit_row(i)) is not None
    rows = _chained(db)
    audit_chain._note_head(db, coll, 2, rows[0]["entry_hash"], rows[1]["entry_hash"])
    assert (_head(db)["seq"], _head(db)["last_hash"]) == (3, rows[2]["entry_hash"])
    assert verify_chain(coll)["intact"] is True
