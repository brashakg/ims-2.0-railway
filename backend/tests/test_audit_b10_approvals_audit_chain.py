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
D54    (review round 1, PR-1411) an approval spent by Post whose journal write
       was lost, or spent through the generic consume door, stranded the
       journal APPROVED for good; an undecided request that lapsed left the
       journal SUBMITTED for good; an approval that lapsed before Post left it
       APPROVED for good; a previous-version audit writer still running during
       a rolling deploy lost its row to a new writer that took its seq.

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

    # D54: that spend never posted anything, so a checker's fresh approval
    # lets the journal post -- once. The spent request stays as stored.
    spent_row = _request(db, j["request_id"])
    assert je_service.needs_reapproval(db, _je(db, j["je_id"])) is True
    _user(db, "boss", ["SUPERADMIN"])
    res = _journal_approve(db, j["je_id"], "boss", ("SUPERADMIN",))
    assert res.get("ok") is True, res
    assert je_service.needs_reapproval(db, _je(db, j["je_id"])) is False
    assert _post(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"])["error"] == "already_posted"
    assert _request(db, j["request_id"]) == spent_row
    assert len(_posted_rows(db, j["je_id"])) == 1


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
    # (D54: the generic consume door never spends a journal approval for
    # anyone; for a manager the answer is the one a missing id gets, whatever
    # action type the call names.)
    assert _inbox_approve(db, monkeypatch, rid, admin)[0] == 200
    for action in ("journal_entry", "petty_cash"):
        answers = [_call(approvals_router.consume_request(
            req_id, approvals_router.ConsumeAction(action_type=action), current_user=store_mgr))
            for req_id in (rid, "REQ-does-not-exist")]
        assert answers[0] == answers[1], (action, answers)
    assert _call(approvals_router.consume_request(
        rid, approvals_router.ConsumeAction(action_type="petty_cash"), current_user=store_mgr))[0] == 404
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


def test_d54_a_request_that_lapsed_undecided_is_raised_again_by_a_checker(db):
    """Codex round 1, P2: the request lapsed with nobody deciding it; the
    journal's Approve answered 410 expired and the maker's Submit 409
    not_draft -- SUBMITTED for good. D54: an authorised checker approves it
    again. The journal raises its own request again and the checker decides
    that one with their PIN; the lapsed request is not reopened or rewritten
    (its own expiry is the engine's), and the maker still cannot approve."""
    _user(db, "maker", ["ADMIN"])
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    requests = db.get_collection("approval_requests")
    requests.update_one({"request_id": j["request_id"]},
                        {"$set": {"expires_at": appr._now() - timedelta(minutes=5)}})

    res = _journal_approve(db, j["je_id"], "maker")
    assert (res["http"], res["error"]) == (403, "cannot_approve_own")
    assert requests.count_documents({}) == 1                 # the maker raised nothing

    res = _journal_approve(db, j["je_id"])
    assert res.get("ok") is True, res                        # was 410 expired
    je = _je(db, j["je_id"])
    assert (je["status"], je["checker_id"]) == ("APPROVED", "checker")
    assert je["approval_request_id"] != j["request_id"]
    old = _request(db, j["request_id"])
    assert (old["status"], old.get("reviewed_by")) == ("EXPIRED", None)   # never decided
    new = _request(db, je["approval_request_id"])
    assert (new["status"], new["reviewed_by"], new["requested_by"]) == ("APPROVED", "checker", "maker")
    assert _post(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"])["error"] == "already_posted"


def test_d54_a_lapsed_request_can_also_be_rejected_by_a_checker(db):
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]}, {"$set": {"expires_at": appr._now() - timedelta(minutes=5)}})
    res = je_service.reject_je(db, je_id=j["je_id"], approver_id="checker",
                               approver_roles=["ADMIN"], pin=PIN, note="Wrong account head")
    assert res.get("ok") is True, res                        # was 410 expired
    assert _je(db, j["je_id"])["status"] == "REJECTED"


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

    def _locked(_db, _day, **_k):
        raise HTTPException(status_code=423, detail="Accounting period 05/2026 is locked")

    monkeypatch.setattr(je_router, "check_period_locked", _locked)
    code, _body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 423
    assert _je(db, j["je_id"])["status"] == "APPROVED"
    req = _request(db, j["request_id"])
    assert (req["status"], req["consumed"]) == ("APPROVED", False)

    monkeypatch.setattr(je_router, "check_period_locked", lambda _db, _day, **_k: None)
    code, body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 200, body
    assert _je(db, j["je_id"])["status"] == "POSTED"


# ===========================================================================
# D54 -- a journal that could no longer post (Codex review round 1)
# ===========================================================================


def _posted_rows(db, je_id: str):
    return [r for r in db.get_collection("audit_logs").find({"action": "je_posted"})
            if r.get("entity_id") == je_id]


class _PostedWriteLost:
    """The journal collection, with the journal's APPROVED -> POSTED write
    failing at the database (AutoReconnect) -- after Post has spent the
    approval. Every other read and write goes through."""

    def __init__(self, coll):
        self._coll = coll

    def find_one_and_update(self, flt, update, *a, **k):
        from pymongo.errors import AutoReconnect

        if (update.get("$set") or {}).get("status") == "POSTED":
            raise AutoReconnect("journal write lost")
        return self._coll.find_one_and_update(flt, update, *a, **k)

    def __getattr__(self, name):
        return getattr(self._coll, name)


def test_d54_post_that_spent_its_approval_then_lost_the_journal_write_finishes(db, monkeypatch):
    """Codex round 1, P2: Post spent the approval, then the journal's own
    write was lost. Every retry answered 409 already_consumed and the journal
    stayed APPROVED with nothing posted, for good. Now the spend is bound to
    this journal's Post in the same write (consumed_for), and a retry finishes
    the posting -- exactly once."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True

    real_coll = je_service._je_coll
    monkeypatch.setattr(je_service, "_je_coll", lambda d: _PostedWriteLost(real_coll(d)))
    res = _post(db, j["je_id"])
    assert res.get("ok") is not True
    monkeypatch.setattr(je_service, "_je_coll", real_coll)
    req = _request(db, j["request_id"])
    assert (req["status"], req["consumed_for"]) == ("CONSUMED", f"je_post:{j['je_id']}")
    assert _je(db, j["je_id"])["status"] == "APPROVED"
    assert _posted_rows(db, j["je_id"]) == []
    # Nothing for a checker to re-approve: the retry is the way forward.
    assert je_service.needs_reapproval(db, _je(db, j["je_id"])) is False
    res = _journal_approve(db, j["je_id"])
    assert (res["http"], res["error"]) == (409, "not_submitted")
    assert db.get_collection("approval_requests").count_documents({}) == 1

    res = _post(db, j["je_id"])
    assert res.get("ok") is True, res                        # was 409 already_consumed
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert _post(db, j["je_id"])["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1            # one posting, however many retries


def test_d54_a_post_bound_spend_finishes_only_its_own_journal(db):
    """The binding is exact: an approval spent for ANOTHER journal's Post, or
    spent by Post before the binding existed (no consumed_for), never posts
    this journal."""
    _user(db, "checker", ["ADMIN"])
    for mark in ("je_post:JE-someone-else", None):
        j = _submitted_journal(db, "maker")
        assert _journal_approve(db, j["je_id"]).get("ok") is True
        spent = appr.ApprovalEngine(db=db).consume_approval(
            consumed_by="checker", action_type="journal_entry", request_id=j["request_id"],
            amount=100.0, consumed_for=mark)
        assert spent.get("ok") is True
        res = _post(db, j["je_id"])
        assert (res["http"], res["error"]) == (409, "already_consumed"), mark
        assert _je(db, j["je_id"])["status"] == "APPROVED"
        assert je_service.needs_reapproval(db, _je(db, j["je_id"])) is True


class _PostedAckLost:
    """The journal collection, with the APPROVED -> POSTED write landing at
    the database but its reply lost (AutoReconnect after the write): the
    journal IS posted, the caller is told it failed."""

    def __init__(self, coll):
        self._coll = coll

    def find_one_and_update(self, flt, update, *a, **k):
        from pymongo.errors import AutoReconnect

        out = self._coll.find_one_and_update(flt, update, *a, **k)
        if (update.get("$set") or {}).get("status") == "POSTED":
            raise AutoReconnect("journal reply lost")
        return out

    def __getattr__(self, name):
        return getattr(self._coll, name)


def test_d68_a_post_whose_reply_was_lost_still_gets_its_posted_event(db, monkeypatch):
    """Codex round 2 (d54-additional): the POSTED write landed, its reply was
    lost, and every later Post answered already_posted -- the journal stayed
    POSTED with no je_posted audit event, for good. D68: a Post that finds the
    journal already POSTED writes the missing event exactly once (keyed by the
    journal id) and then answers already_posted."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True

    real_coll = je_service._je_coll
    monkeypatch.setattr(je_service, "_je_coll", lambda d: _PostedAckLost(real_coll(d)))
    res = _post(db, j["je_id"])
    monkeypatch.setattr(je_service, "_je_coll", real_coll)
    assert (res.get("ok"), res.get("error")) == (False, "already_posted")
    assert _je(db, j["je_id"])["status"] == "POSTED"
    rows = _posted_rows(db, j["je_id"])
    assert len(rows) == 1                                       # was 0
    assert rows[0]["user_id"] == "checker"                      # the poster, from the journal
    assert rows[0]["after_state"]["status"] == "POSTED"

    for _ in range(2):                                          # retries: one event, ever
        assert _post(db, j["je_id"], poster="someone-else")["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert verify_chain(db.get_collection("audit_logs"))["intact"] is True


def test_d68_a_retry_repairs_a_posted_journal_missing_its_event(db):
    """A journal left POSTED without its event (the reply-lost case before
    D68, or an audit write that failed) is repaired by the next Post retry --
    once -- and a journal that already has its event (written before D68,
    under a random log id) is never given a second one."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"]).get("ok") is True
    coll = db.get_collection("audit_logs")
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert _post(db, j["je_id"])["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1               # legacy/normal event kept, no second

    coll.delete_many({"action": "je_posted"})                   # the event that never got written
    assert _post(db, j["je_id"], poster="retrier")["error"] == "already_posted"
    rows = _posted_rows(db, j["je_id"])
    assert len(rows) == 1                                       # was 0
    assert _post(db, j["je_id"])["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1


def test_d68_racing_posts_and_repairs_write_one_event(mongo_db):
    """Real MongoDB, two requests at once: two Posts of one APPROVED journal,
    then (event removed) four repairs released together. One posting, and one
    je_posted event each time: the event's log id is the journal id."""
    db = mongo_db
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True

    def _together(n, act=lambda: _post(db, j["je_id"])):
        gate, got = threading.Barrier(n), []

        def _go():
            gate.wait(timeout=20)
            got.append(act())
        threads = [threading.Thread(target=_go) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return got

    got = _together(2)
    assert sorted(bool(r.get("ok")) for r in got) == [False, True]
    assert len(_posted_rows(db, j["je_id"])) == 1
    db.get_collection("audit_logs").delete_many({"action": "je_posted"})
    assert all(r["error"] == "already_posted" for r in _together(4))
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert db.get_collection("journal_entries").count_documents({"je_id": j["je_id"]}) == 1
    # D84: four reads of the journal at once (the screen's list/detail),
    # mixed with two Post retries, each finding the event missing.
    db.get_collection("audit_logs").delete_many({"action": "je_posted"})
    _aged(db, j["je_id"])
    turn = iter(range(6))
    got = _together(6, lambda: (je_service.repair_posted_events(db, [_je(db, j["je_id"])])
                                if next(turn) >= 2 else _post(db, j["je_id"])))
    assert len(got) == 6
    assert len(_posted_rows(db, j["je_id"])) == 1


def test_d68_a_period_locked_after_posting_does_not_block_the_repair(db, monkeypatch):
    """The month was locked after the journal posted (reply lost). The Post
    route's lock gate stopped the retry with 423 before the repair could run.
    A journal already POSTED posts nothing more, so the route lets the retry
    through: 409 already_posted, event written; an APPROVED journal in a
    locked month is still refused."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"]).get("ok") is True
    db.get_collection("audit_logs").delete_many({"action": "je_posted"})
    monkeypatch.setattr(je_router, "_get_db", lambda: db)

    def _locked(_db, _day, **_k):
        raise HTTPException(status_code=423, detail="Accounting period 05/2026 is locked")

    monkeypatch.setattr(je_router, "check_period_locked", _locked)
    code, _body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 409                                          # was 423, event never written
    assert len(_posted_rows(db, j["je_id"])) == 1

    other = _submitted_journal(db, "maker")
    assert _journal_approve(db, other["je_id"]).get("ok") is True
    code, _body = _call(je_router.post_journal_entry(other["je_id"], current_user=checker))
    assert code == 423
    assert _je(db, other["je_id"])["status"] == "APPROVED"


def test_d68_an_unreadable_trail_never_writes_a_second_event(db, monkeypatch):
    """When the trail cannot be read the repair is skipped (the next retry
    makes it), so an event already there is never doubled."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"]).get("ok") is True
    real_get = db.get_collection
    monkeypatch.setattr(db, "get_collection",
                        lambda n: _PostedEventReadFails(real_get(n)) if n == "audit_logs" else real_get(n))
    assert _post(db, j["je_id"])["error"] == "already_posted"
    monkeypatch.setattr(db, "get_collection", real_get)
    assert len(_posted_rows(db, j["je_id"])) == 1


# ---------------------------------------------------------------------------
# D84 -- reading a POSTED journal repairs its missing je_posted event
# ---------------------------------------------------------------------------


def _read_journal(db, monkeypatch, route: str, je_id: str, user: Dict[str, Any]):
    """One read of the journal through the route the screen (or the Tally
    export) uses; returns the status code."""
    monkeypatch.setattr(je_router, "_get_db", lambda: db)
    if route == "list":
        code, body = _call(je_router.list_journal_entries(
            store_id=None, status=None, maker_id=None, current_user=user))
        assert code != 200 or any(r["je_id"] == je_id for r in body["journal_entries"])
    elif route == "detail":
        code, _body = _call(je_router.get_journal_entry(je_id, current_user=user))
    else:
        code, _body = _call(je_router.get_tally_journal_jv(
            from_date=None, to_date=None, store_id=None, current_user=user))
    return code


class _AuditInsertsFail:
    """audit_logs whose inserts fail at the database (the Post's own audit
    write lost; the posting stands, fail-soft)."""

    def __init__(self, coll):
        self._coll = coll

    def insert_one(self, *a, **k):
        from pymongo.errors import AutoReconnect

        raise AutoReconnect("audit insert lost")

    def __getattr__(self, name):
        return getattr(self._coll, name)


@pytest.mark.parametrize("route", ["list", "detail", "export"])
@pytest.mark.parametrize("lost", ["audit_insert_failed", "died_before_audit"])
def test_d84_reading_a_posted_journal_writes_its_missing_event_once(db, monkeypatch, route, lost):
    """D84: Post answered 200 but its audit insert failed, or the process died
    between the POSTED write and the audit. The screen hides Post once the
    journal is POSTED, so no retry ever came and the journal stayed POSTED
    without je_posted for good. Reading it (list, detail, Tally export) now
    writes the event once, under the fixed id, naming the poster."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    if lost == "audit_insert_failed":
        real_get = db.get_collection
        monkeypatch.setattr(db, "get_collection",
                            lambda n: _AuditInsertsFail(real_get(n)) if n == "audit_logs" else real_get(n))
        assert _post(db, j["je_id"]).get("ok") is True          # 200 to the screen
        monkeypatch.setattr(db, "get_collection", real_get)
    else:
        real_audit = je_service._audit
        monkeypatch.setattr(je_service, "_audit", lambda *a, **k: None)
        assert _post(db, j["je_id"]).get("ok") is True
        monkeypatch.setattr(je_service, "_audit", real_audit)
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert _posted_rows(db, j["je_id"]) == []

    assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 200
    rows = _posted_rows(db, j["je_id"])                         # the FIRST read (D84), no grace:
    assert len(rows) == 1                                       # this version's Post stamped posted_by
    assert rows[0]["log_id"] == f"AUD-JE-POSTED-{j['je_id']}"
    assert rows[0]["user_id"] == "checker"                      # the poster, not the reader
    assert rows[0]["reason"] == "recorded_on_read"
    assert rows[0]["after_state"]["status"] == "POSTED"
    for again in ("list", "detail", "export"):                  # later reads: nothing more
        assert _read_journal(db, monkeypatch, again, j["je_id"], reader) == 200
    assert _post(db, j["je_id"])["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert verify_chain(db.get_collection("audit_logs"))["intact"] is True


class _PostedEventReadFails:
    """audit_logs whose je_posted existence read fails; the chain's own reads
    and every insert still go through."""

    def __init__(self, coll):
        self._coll = coll

    @staticmethod
    def _check(flt):
        if (flt or {}).get("action") == "je_posted":
            raise RuntimeError("audit read lost")

    def find_one(self, flt=None, *a, **k):
        self._check(flt)
        return self._coll.find_one(flt, *a, **k)

    def find(self, flt=None, *a, **k):
        self._check(flt)
        return self._coll.find(flt, *a, **k)

    def __getattr__(self, name):
        return getattr(self._coll, name)


def test_d84_a_journal_posted_before_d68_never_gets_a_second_event(db, monkeypatch):
    """The legacy guard: a journal posted before D68 has its je_posted event
    under a RANDOM log id, so the fixed id cannot refuse a second one -- only
    the existence check does. Reads and Post retries leave it at one event,
    and so does a read whose existence check fails (skipped, never assumed
    missing). Deleting the check, or treating a failed check as 'missing',
    writes a second event here."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    fixed_id = je_service._posted_event_id
    monkeypatch.setattr(je_service, "_posted_event_id",          # as the pre-D68 Post wrote it
                        lambda _je_id: f"AUD-{uuid.uuid4().hex[:12]}")
    assert _post(db, j["je_id"]).get("ok") is True
    monkeypatch.setattr(je_service, "_posted_event_id", fixed_id)
    coll = db.get_collection("audit_logs")
    legacy = _posted_rows(db, j["je_id"])[0]
    assert legacy["log_id"] != fixed_id(j["je_id"])
    _aged(db, j["je_id"])                                       # past the grace: the check guards

    def _one_legacy_event():
        rows = _posted_rows(db, j["je_id"])
        assert [r["log_id"] for r in rows] == [legacy["log_id"]]

    for route in ("list", "detail", "export"):
        assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 200
    assert _post(db, j["je_id"])["error"] == "already_posted"
    _one_legacy_event()

    real_get = db.get_collection
    monkeypatch.setattr(db, "get_collection",
                        lambda n: _PostedEventReadFails(real_get(n)) if n == "audit_logs" else real_get(n))
    for route in ("list", "detail", "export"):
        assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 200
    assert _post(db, j["je_id"])["error"] == "already_posted"
    monkeypatch.setattr(db, "get_collection", real_get)
    _one_legacy_event()
    assert verify_chain(coll)["intact"] is True


def _aged(db, je_id: str) -> None:
    """The read comes later than the posting's grace (its poster is done)."""
    db.get_collection("journal_entries").update_one(
        {"je_id": je_id}, {"$set": {"posted_at": datetime.now() - timedelta(minutes=10)}})


def _read_inside_audit(monkeypatch, action: str, read):
    """Run ``read`` when ``action``'s audit row is about to be written: a read
    landing between a journal's POSTED write and that write. Once: the
    read's own repair goes through _audit too."""
    real_audit, fired = je_service._audit, []

    def _audit(d, act, je, **k):
        if act == action and not fired:
            fired.append(act)
            read(je)
        return real_audit(d, act, je, **k)
    monkeypatch.setattr(je_service, "_audit", _audit)


def test_d84_a_read_during_a_reversal_leaves_the_mirror_one_posted_event(db, monkeypatch):
    """reverse_je inserts the mirror journal already POSTED, then writes
    je_reversed and the mirror's je_posted. A list read landing in between
    repaired the mirror under the fixed id, and the reversal's own je_posted
    (random id) landed too: two events for one posting. The reversal now
    writes under the fixed id, so exactly one survives, naming the reverser."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"]).get("ok") is True
    _aged(db, j["je_id"])
    monkeypatch.setattr(je_service, "_READ_REPAIR_GRACE", timedelta(0), raising=False)
    _read_inside_audit(monkeypatch, "je_reversed", lambda je: (
        _read_journal(db, monkeypatch, "list", je["reversed_by"], reader),
        _read_journal(db, monkeypatch, "detail", je["reversed_by"], reader)))
    res = je_service.reverse_je(db, je_id=j["je_id"], actor_id="checker")
    assert res.get("ok") is True, res
    mirror = res["reversal_je_id"]
    rows = _posted_rows(db, mirror)
    assert [r["log_id"] for r in rows] == [f"AUD-JE-POSTED-{mirror}"]   # was two
    assert rows[0]["user_id"] == "checker"                      # the reverser, not 'unrecorded'
    for route in ("list", "detail"):
        assert _read_journal(db, monkeypatch, route, mirror, reader) == 200
    assert len(_posted_rows(db, mirror)) == 1
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert verify_chain(db.get_collection("audit_logs"))["intact"] is True


def _previous_version_post(db, monkeypatch, je_id: str, inside):
    """Post as the previous version does: POSTED with no posted_by, then its
    je_posted under a RANDOM log id; ``inside`` runs between the two writes
    (on this version's code, as a request landing on a new worker)."""
    real_set, real_audit, fired = je_service._set, je_service._audit, []

    def _old_set(d, jid, fields, **k):
        return real_set(d, jid, {f: v for f, v in fields.items() if f != "posted_by"}, **k)

    def _audit(d, act, je, **k):
        if act == "je_posted" and not fired:
            fired.append(act)
            monkeypatch.setattr(je_service, "_set", real_set)
            monkeypatch.setattr(je_service, "_audit", real_audit)
            inside(je)
            k["log_id"] = f"AUD-{uuid.uuid4().hex[:12]}"
        return real_audit(d, act, je, **k)
    monkeypatch.setattr(je_service, "_set", _old_set)
    monkeypatch.setattr(je_service, "_audit", _audit)
    res = _post(db, je_id)
    monkeypatch.setattr(je_service, "_set", real_set)
    monkeypatch.setattr(je_service, "_audit", real_audit)
    assert fired
    return res


def test_d84_a_read_while_an_older_worker_posts_leaves_one_event(db, monkeypatch):
    """Rolling deploy: a previous-version worker writes POSTED (no posted_by),
    then its je_posted under a RANDOM log id. A new-version read landing in
    between wrote the fixed-id event and the old worker's row landed too: two
    events. A read now leaves a journal with no posted_by, posted within the
    grace, to its poster; a read after the grace finds the old worker's event
    and writes nothing."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    res = _previous_version_post(db, monkeypatch, j["je_id"], lambda je: [
        _read_journal(db, monkeypatch, route, j["je_id"], reader)
        for route in ("list", "detail", "export")])
    assert res.get("ok") is True
    rows = _posted_rows(db, j["je_id"])
    assert len(rows) == 1                                       # was two
    assert rows[0]["log_id"] != je_service._posted_event_id(j["je_id"])   # the old worker's own
    _aged(db, j["je_id"])
    for route in ("list", "detail", "export"):
        assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 200
    assert len(_posted_rows(db, j["je_id"])) == 1


def test_d84_a_post_retry_while_an_older_worker_posts_leaves_one_event(db, monkeypatch):
    """The same deploy window through Post: a second checker's Post (or a
    second tab) lands on a new worker between the old worker's POSTED write
    and its random-id je_posted. The retry repaired at once (no grace on
    Post) and the old row landed too: two events. A journal with no
    posted_by, posted within the grace, is left to its poster on every path;
    the retry still answers already_posted."""
    _user(db, "checker", ["ADMIN"])
    _user(db, "checker2", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    retries = []
    res = _previous_version_post(db, monkeypatch, j["je_id"], lambda je: retries.append(
        _post(db, j["je_id"], poster="checker2")))
    assert res.get("ok") is True
    assert retries[0]["error"] == "already_posted"
    rows = _posted_rows(db, j["je_id"])
    assert len(rows) == 1                                       # was two
    assert rows[0]["log_id"] != je_service._posted_event_id(j["je_id"])
    _aged(db, j["je_id"])
    assert _post(db, j["je_id"], poster="checker2")["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert verify_chain(db.get_collection("audit_logs"))["intact"] is True


def test_d84_a_post_retry_after_this_versions_lost_audit_repairs_at_once(db, monkeypatch):
    """D68 kept: this version's Post stamps posted_by, so a retry right after
    its lost audit insert repairs at once (no grace)."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    real_audit = je_service._audit
    monkeypatch.setattr(je_service, "_audit", lambda *a, **k: None)
    assert _post(db, j["je_id"]).get("ok") is True
    monkeypatch.setattr(je_service, "_audit", real_audit)
    assert _post(db, j["je_id"], poster="checker2")["error"] == "already_posted"
    rows = _posted_rows(db, j["je_id"])
    assert [r["log_id"] for r in rows] == [je_service._posted_event_id(j["je_id"])]
    assert rows[0]["user_id"] == "checker"


def test_d84_a_reversed_journal_missing_its_posted_event_is_repaired(db, monkeypatch):
    """A journal whose Post audit was lost and which was reversed before any
    read became REVERSED, and the repair only looked at POSTED journals: it
    never got its je_posted. A REVERSED journal was posted, so a read writes
    the event once (fixed id, the poster, recording the posting)."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    real_audit = je_service._audit
    monkeypatch.setattr(je_service, "_audit", lambda *a, **k: None)
    assert _post(db, j["je_id"]).get("ok") is True
    monkeypatch.setattr(je_service, "_audit", real_audit)
    assert je_service.reverse_je(db, je_id=j["je_id"], actor_id="reverser").get("ok") is True
    assert _je(db, j["je_id"])["status"] == "REVERSED"
    assert _posted_rows(db, j["je_id"]) == []
    _aged(db, j["je_id"])
    for route in ("list", "detail"):
        assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 200
    rows = _posted_rows(db, j["je_id"])
    assert len(rows) == 1                                       # was 0, for good
    assert rows[0]["log_id"] == f"AUD-JE-POSTED-{j['je_id']}"
    assert rows[0]["user_id"] == "checker"
    assert rows[0]["after_state"]["status"] == "POSTED"
    assert verify_chain(db.get_collection("audit_logs"))["intact"] is True


class _JournalsUnreadable:
    def __init__(self, coll):
        self._coll = coll

    def find(self, *a, **k):
        raise RuntimeError("journal read lost")

    def find_one(self, *a, **k):
        raise RuntimeError("journal read lost")

    def __getattr__(self, name):
        return getattr(self._coll, name)


_JOURNALS_DOWN = {
    "read_fails": lambda real: (lambda d: _JournalsUnreadable(real(d))),
    "no_store": lambda real: (lambda d: None),          # no database handle (connect failed)
}


@pytest.mark.parametrize("down", sorted(_JOURNALS_DOWN))
@pytest.mark.parametrize("route", ["list", "detail", "export"])
def test_d84_a_journal_read_that_fails_is_an_error_not_an_empty_answer(db, monkeypatch, route, down):
    """A journal read that failed at the database, or found no database at
    all, answered 200 with an empty list / an empty Tally file, or 404 'not
    found' for a journal that exists. Now it answers 503 (try again), and
    the same read afterwards works."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    assert _post(db, j["je_id"]).get("ok") is True
    real_coll = je_service._je_coll
    monkeypatch.setattr(je_service, "_je_coll", _JOURNALS_DOWN[down](real_coll))
    assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 503   # was 200 / 404
    monkeypatch.setattr(je_service, "_je_coll", real_coll)
    assert _read_journal(db, monkeypatch, route, j["je_id"], reader) == 200


@pytest.mark.parametrize("down", sorted(_JOURNALS_DOWN))
def test_d84_post_route_read_that_fails_is_not_not_found(db, monkeypatch, down):
    """The Post route's own read of the journal failing (or finding no
    database) answered 404 'not found'; it answers 503 and the retry posts."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    monkeypatch.setattr(je_router, "_get_db", lambda: db)
    monkeypatch.setattr(je_router, "_je_require_enabled", lambda: None)
    real_coll = je_service._je_coll
    monkeypatch.setattr(je_service, "_je_coll", _JOURNALS_DOWN[down](real_coll))
    code, _body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 503                                          # was 404
    monkeypatch.setattr(je_service, "_je_coll", real_coll)
    code, _body = _call(je_router.post_journal_entry(j["je_id"], current_user=checker))
    assert code == 200
    assert len(_posted_rows(db, j["je_id"])) == 1


# ---------------------------------------------------------------------------
# Codex round 3 (PR-1411-round3.md), R3-A -- a read that fails INSIDE a
# journal door is 503 (try again): never "not found", "not approved" or
# "approve again" for a record that is there
# ---------------------------------------------------------------------------


def _db_faults(monkeypatch, db, name: str, **faults):
    """Collection ``name`` of ``db`` fails at the database (AutoReconnect) from
    the methods named: ``find_one=fn`` fails the calls where fn(call_no, *args)
    is true; ``find_one_and_update=(fn, "landed")`` runs the call at the
    database first, then loses its reply. Every other call goes through.
    Returns undo()."""
    from pymongo.errors import AutoReconnect

    real_get = db.get_collection
    calls: Dict[str, int] = {}

    class _Coll:
        def __init__(self, coll):
            self._coll = coll

        def __getattr__(self, attr):
            real = getattr(self._coll, attr)
            if attr not in faults:
                return real
            fail, landed = faults[attr] if isinstance(faults[attr], tuple) else (faults[attr], "")

            def call(*a, **k):
                calls[attr] = calls.get(attr, 0) + 1
                if fail(calls[attr], *a, **k):
                    if landed:
                        real(*a, **k)
                    raise AutoReconnect(f"{name}.{attr} lost")
                return real(*a, **k)
            return call

    monkeypatch.setattr(db, "get_collection",
                        lambda n: _Coll(real_get(n)) if n == name else real_get(n))
    return lambda: monkeypatch.setattr(db, "get_collection", real_get)


def _every_call(_n, *_a, **_k):
    return True


def _sets_status(status: str):
    return lambda _n, _flt, upd, *a, **k: (upd.get("$set") or {}).get("status") == status


_MAKER = {"user_id": "maker", "roles": ["ACCOUNTANT"], "store_ids": ["S1"], "active_store_id": "S1"}


def _door(db, monkeypatch, door: str, je_id: str, user: Dict[str, Any]):
    """One journal door through its route handler; (status code, body)."""
    monkeypatch.setattr(je_router, "_get_db", lambda: db)
    monkeypatch.setattr(je_router, "_je_require_enabled", lambda: None)
    if door == "post":
        coro = je_router.post_journal_entry(je_id, current_user=user)
    elif door == "submit":
        coro = je_router.submit_journal_entry(je_id, current_user=user)
    elif door == "approve":
        coro = je_router.approve_journal_entry(je_id, je_router.JePinBody(pin=PIN), current_user=user)
    elif door == "reject":
        coro = je_router.reject_journal_entry(
            je_id, je_router.JeRejectBody(pin=PIN, note="Wrong account, not wanted"), current_user=user)
    else:
        coro = je_router.reverse_journal_entry(je_id, current_user=user)
    return _call(coro)


@pytest.mark.parametrize("down", ["read_fails", "no_store"])
def test_r3a_post_whose_inner_journal_read_fails_is_503_not_404(db, monkeypatch, down):
    """Codex round 3, R3-A (read_repair_independent.py, 'inner journal lookup
    unavailable'): the Post route read the journal, then the service's own
    read of it failed and Post answered 404 not_found for an APPROVED journal
    that was there. It answers 503, nothing is spent or moved, and the same
    Post afterwards posts once."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    before = _je(db, j["je_id"])
    if down == "read_fails":
        undo = _db_faults(monkeypatch, db, "journal_entries", find_one=lambda n, *a, **k: n == 2)
    else:
        real_coll, seen = je_service._je_coll, []

        def _second_time_none(d):
            seen.append(1)
            return None if len(seen) == 2 else real_coll(d)

        monkeypatch.setattr(je_service, "_je_coll", _second_time_none)

        def undo():
            monkeypatch.setattr(je_service, "_je_coll", real_coll)
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    undo()
    assert code == 503, body                                    # was 404 not_found
    assert _je(db, j["je_id"]) == before                        # nothing moved
    assert _request(db, j["request_id"])["status"] == "APPROVED"    # nothing spent
    assert _posted_rows(db, j["je_id"]) == []
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    assert code == 200, body
    assert len(_posted_rows(db, j["je_id"])) == 1


@pytest.mark.parametrize("landed", ["", "landed"])
def test_r3a_post_read_back_that_fails_is_503(db, monkeypatch, landed):
    """The second window Codex named (source-reviewed): the POSTED write was
    refused, or landed with its reply lost, and the read-back that tells the
    two apart failed too. Post answered 409 not_approved with no status --
    for a journal that may already be POSTED. It answers 503; the retry
    posts (refused) or records the event and answers already_posted
    (landed): one posting, one event."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    undo = _db_faults(monkeypatch, db, "journal_entries",
                      find_one_and_update=(_sets_status("POSTED"), landed),
                      find_one=lambda n, *a, **k: n == 3)
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    undo()
    assert code == 503, body                                    # was 409 not_approved, status None
    assert _je(db, j["je_id"])["status"] == ("POSTED" if landed else "APPROVED")
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    if landed:
        assert (code, body["error"]) == (409, "already_posted")
    else:
        assert code == 200, body
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert len(_posted_rows(db, j["je_id"])) == 1


@pytest.mark.parametrize("case", ["catch_up", "bound_spend", "no_request_store"])
def test_r3a_post_that_cannot_read_the_approval_request_is_503(db, monkeypatch, case):
    """Post's other inner reads are of the journal's approval request. An
    unreadable request was answered as 'no request':
      catch_up          the request was approved in the inbox, the journal's
                        own write never ran; Post reads the request to catch
                        the journal up -> was 409 not_approved (SUBMITTED);
      bound_spend       the approval is spent by this journal's Post (D54),
                        the retry reads the request to finish -> was 404
                        not_found;
      no_request_store  no approval collection at all -> was 409 no_db.
    Each is 503 and the same Post afterwards posts once."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    if case == "catch_up":
        assert _engine(db).approve(j["request_id"], approver_user_id="checker",
                                   approver_roles=["ADMIN"], pin=PIN).get("ok") is True
        assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    else:
        assert _journal_approve(db, j["je_id"]).get("ok") is True
    if case == "bound_spend":
        undo = _db_faults(monkeypatch, db, "journal_entries",
                          find_one_and_update=_sets_status("POSTED"))
        assert _door(db, monkeypatch, "post", j["je_id"], checker)[0] == 409
        undo()
        assert _request(db, j["request_id"])["consumed_for"] == f"je_post:{j['je_id']}"
    if case == "no_request_store":
        real_coll = appr.ApprovalEngine._coll
        monkeypatch.setattr(appr.ApprovalEngine, "_coll", lambda self: None)

        def undo():
            monkeypatch.setattr(appr.ApprovalEngine, "_coll", real_coll)
    else:
        undo = _db_faults(monkeypatch, db, "approval_requests", find_one=_every_call)
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    undo()
    assert code == 503, body                                    # was 409 / 404 / 409
    assert _posted_rows(db, j["je_id"]) == []
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    assert code == 200, body
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert len(_posted_rows(db, j["je_id"])) == 1


def test_r3a_a_spend_whose_reply_was_lost_is_503_and_the_retry_posts_once(db, monkeypatch):
    """Post's spend of the approval landed, its reply was lost: the database
    error went out unhandled (HTTP 500). It answers 503; the spend is bound
    to this journal's Post, so the retry finishes the posting once (D54)."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    undo = _db_faults(monkeypatch, db, "approval_requests",
                      find_one_and_update=(_sets_status("CONSUMED"), "landed"))
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    undo()
    assert code == 503, body                                    # was an unhandled AutoReconnect
    req = _request(db, j["request_id"])
    assert (req["status"], req["consumed_for"]) == ("CONSUMED", f"je_post:{j['je_id']}")
    assert _je(db, j["je_id"])["status"] == "APPROVED"
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    assert code == 200, body
    code, body = _door(db, monkeypatch, "post", j["je_id"], checker)
    assert (code, body["error"]) == (409, "already_posted")
    assert len(_posted_rows(db, j["je_id"])) == 1


@pytest.mark.parametrize("route", ["list", "detail"])
def test_r3a_a_journal_read_never_says_approve_again_for_an_unreadable_request(db, monkeypatch, route):
    """The list and the detail flag an APPROVED journal needs_reapproval from
    its request. An unreadable request was read as 'no request', so a journal
    with a good approval was shown 'Approve again' instead of Post. The read
    answers 503; once readable the flag is false."""
    _user(db, "checker", ["ADMIN"])
    reader = _user(db, "accounts", ["ACCOUNTANT"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    undo = _db_faults(monkeypatch, db, "approval_requests", find_one=_every_call)
    code = _read_journal(db, monkeypatch, route, j["je_id"], reader)
    undo()
    assert code == 503                                          # was 200, needs_reapproval true
    assert _list_flag(db, monkeypatch, j["je_id"], reader) is False


def test_r3a_journal_approve_never_raises_a_second_request_beside_one_it_could_not_read(db, monkeypatch):
    """A checker presses Approve on an APPROVED journal whose (good, unspent)
    request cannot be read. It was read as 'no request, cannot post': a new
    request was raised and approved and the journal moved onto it, leaving
    the good approval behind. It answers 503 and writes nothing."""
    _user(db, "checker", ["ADMIN"])
    checker2 = _user(db, "checker2", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    requests = db.get_collection("approval_requests").count_documents({})
    undo = _db_faults(
        monkeypatch, db, "approval_requests",
        find_one=lambda n, flt=None, *a, **k: (flt or {}).get("request_id") == j["request_id"])
    code, body = _door(db, monkeypatch, "approve", j["je_id"], checker2)
    undo()
    assert code == 503, body                                    # was 200 on a second request
    assert db.get_collection("approval_requests").count_documents({}) == requests
    assert _je(db, j["je_id"])["approval_request_id"] == j["request_id"]
    assert _door(db, monkeypatch, "post", j["je_id"], checker2)[0] == 200
    assert len(_posted_rows(db, j["je_id"])) == 1


@pytest.mark.parametrize("door", ["create", "post", "reverse"])
def test_r3a_a_period_lock_that_cannot_be_read_never_lets_a_journal_through(db, monkeypatch, door):
    """The journal doors check the month's lock before they write. A lock
    that could not be read was read as 'not locked', so a journal could be
    created in, posted into or reversed into a closed month. A manual journal
    now answers 503; the same door afterwards answers the lock's own 423.
    Sales, returns and bills keep the shared fail-soft gate, unchanged."""
    from api.routers.finance import _shared as finance_shared
    from api.utils.ist import ist_today

    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")                         # dated 15 May 2026
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    if door == "reverse":
        assert _post(db, j["je_id"]).get("ok") is True
    monkeypatch.setattr(je_router, "_get_db", lambda: db)
    monkeypatch.setattr(je_router, "_je_require_enabled", lambda: None)

    def _go():
        if door == "create":                                    # dated today (IST)
            return _call(je_router.create_journal_entry(je_router.JeCreateBody(
                description="Rent", store_id="S1",
                lines=[je_router.JeLineBody(account_code="5003", debit=100),
                       je_router.JeLineBody(account_code="2001", credit=100)]),
                current_user=_MAKER))
        return _door(db, monkeypatch, door, j["je_id"], checker)

    journals = db.get_collection("journal_entries").count_documents({})
    status = _je(db, j["je_id"])["status"]
    undo = _db_faults(monkeypatch, db, "period_locks", find_one=_every_call)
    code, body = _go()
    assert finance_shared.check_period_locked(db, datetime(2026, 5, 15).date()) is None   # sales: as before
    undo()
    assert code == 503, body                                    # was 200: through, unchecked
    assert db.get_collection("journal_entries").count_documents({}) == journals
    assert _je(db, j["je_id"])["status"] == status
    assert _request(db, j["request_id"])["status"] == ("CONSUMED" if door == "reverse" else "APPROVED")

    today = ist_today()
    month, year = (5, 2026) if door == "post" else (today.month, today.year)
    db.get_collection("period_locks").insert_one({"month": month, "year": year})
    code, body = _go()
    assert code == 423, body
    assert db.get_collection("journal_entries").count_documents({}) == journals


def test_r3a_inbox_reject_that_cannot_read_the_journal_decides_nothing(db, monkeypatch):
    """The inbox holds a journal's rejection to the journal's note rule, which
    needs the journal. An unreadable journal was read as 'not a journal's
    request': a short-note rejection went through on the request and the
    journal stayed SUBMITTED beside it. It answers 503 and decides nothing."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    undo = _db_faults(monkeypatch, db, "journal_entries", find_one=_every_call)
    code, body = _inbox_reject(db, monkeypatch, j["request_id"], checker, "short")
    undo()
    assert code == 503, body                                    # was 200, request REJECTED
    assert _request(db, j["request_id"])["status"] == "REQUESTED"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    code, body = _inbox_reject(db, monkeypatch, j["request_id"], checker, "short")
    assert (code, body["error"]) == (400, "note_required")


@pytest.mark.parametrize("door", ["submit", "approve", "reject", "reverse"])
def test_r3a_every_journal_door_answers_503_for_a_journal_it_could_not_read(db, monkeypatch, door):
    """The alternate routes of R3-A: Submit, Approve, Reject and Reverse read
    the journal through the same reader and answered 404 not_found when that
    read failed. Each answers 503, changes nothing, and works afterwards."""
    checker = _user(db, "checker", ["ADMIN"])
    if door == "submit":
        je_id, who, want = _draft_journal(db, "maker", amount=100.0), _MAKER, "DRAFT"
    else:
        je_id, who, want = _submitted_journal(db, "maker")["je_id"], checker, "SUBMITTED"
    if door == "reverse":
        assert _journal_approve(db, je_id).get("ok") is True
        assert _post(db, je_id).get("ok") is True
        want = "POSTED"
    undo = _db_faults(monkeypatch, db, "journal_entries", find_one=lambda n, *a, **k: n == 1)
    code, body = _door(db, monkeypatch, door, je_id, who)
    undo()
    assert code == 503, body                                    # was 404 not_found
    assert _je(db, je_id)["status"] == want
    code, body = _door(db, monkeypatch, door, je_id, who)
    assert code == 200, body


@pytest.mark.parametrize("fault", ["inner_read", "reply_lost"])
@pytest.mark.parametrize("door", ["approve", "reject"])
def test_r3a_a_journal_decision_that_cannot_reach_its_request_is_503(db, monkeypatch, door, fault):
    """Approve / Reject on the journal screen decide the journal's request
    inside the approval engine. inner_read (the R3-A shape on these doors):
    the door read the request, then the engine's own read of it failed and
    the door answered 404 not_found -- now 503, nothing decided, the same
    press then works. reply_lost: the decision landed and its reply was lost,
    which went out as an unhandled database error -- now 503, and the same
    press afterwards finds the journal moved with its request (L3-25). One
    request either way."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    decided = "APPROVED" if door == "approve" else "REJECTED"
    if fault == "inner_read":
        undo = _db_faults(monkeypatch, db, "approval_requests", find_one=lambda n, *a, **k: n == 3)
    else:
        undo = _db_faults(monkeypatch, db, "approval_requests",
                          find_one_and_update=(_sets_status(decided), "landed"))
    code, body = _door(db, monkeypatch, door, j["je_id"], checker)
    undo()
    assert code == 503, body                                    # was 404 / an unhandled AutoReconnect
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    code, body = _door(db, monkeypatch, door, j["je_id"], checker)
    if fault == "inner_read":
        assert code == 200, body
    else:
        assert (code, body["error"], body["status"]) == (409, "not_submitted", decided)
    assert _je(db, j["je_id"])["status"] == decided
    assert _request(db, j["request_id"])["status"] == decided
    assert db.get_collection("approval_requests").count_documents({}) == 1


def test_r3a_inbox_approval_stands_when_the_journal_cannot_be_read_and_post_catches_up(db, monkeypatch):
    """Kept: an inbox approval whose journal sync cannot read the journal is
    still the request's decision (it is already written); the journal catches
    up at its own doors (L3-25). The strict reader must not turn that 200
    into an error after the request has been decided."""
    checker = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    undo = _db_faults(monkeypatch, db, "journal_entries", find_one=_every_call)
    code, body = _inbox_approve(db, monkeypatch, j["request_id"], checker)
    undo()
    assert code == 200, body
    assert _request(db, j["request_id"])["status"] == "APPROVED"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    assert _door(db, monkeypatch, "post", j["je_id"], checker)[0] == 200
    assert len(_posted_rows(db, j["je_id"])) == 1


def test_r3a_the_approval_engines_other_callers_keep_their_reads(db, monkeypatch):
    """The strict request read is the journal doors' alone (opt-in): refunds,
    till discounts, supplier returns and the generic inbox read the engine as
    before."""
    from pymongo.errors import AutoReconnect

    j = _submitted_journal(db, "maker")
    undo = _db_faults(monkeypatch, db, "approval_requests", find_one=_every_call)
    assert appr.ApprovalEngine(db=db).get(j["request_id"]) is None
    with pytest.raises(AutoReconnect):
        appr.ApprovalEngine(db=db, strict=True).get(j["request_id"])
    undo()
    assert appr.ApprovalEngine(db=db, strict=True).get(j["request_id"])["status"] == "REQUESTED"


def _consume_route(db, monkeypatch, request_id: str, user: Dict[str, Any]):
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    return _call(approvals_router.consume_request(
        request_id, approvals_router.ConsumeAction(action_type="journal_entry", amount=100.0),
        current_user=user))


def test_d54_the_generic_consume_door_never_spends_a_journal_approval(db, monkeypatch):
    """Codex round 1, P2 (alternate path): the maker's POST
    /approvals/requests/{id}/consume answered 200 and spent the journal's
    approval; Post then answered 409 already_consumed for good. A journal
    approval is spent only by the journal's own Post (domain-only)."""
    maker = _user(db, "maker", ["ACCOUNTANT"])
    admin = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True

    for who in (maker, admin):
        code, body = _consume_route(db, monkeypatch, j["request_id"], who)
        assert code == 400, (who["user_id"], code, body)    # maker was 200
    req = _request(db, j["request_id"])
    assert (req["status"], req["consumed"]) == ("APPROVED", False)
    # Named as another action type, the request itself is still a journal's.
    monkeypatch.setattr(approvals_router, "_get_db", lambda: db)
    code, _ = _call(approvals_router.consume_request(
        j["request_id"], approvals_router.ConsumeAction(action_type="petty_cash"), current_user=maker))
    assert code == 400
    assert _request(db, j["request_id"])["consumed"] is False

    assert _post(db, j["je_id"]).get("ok") is True
    assert _je(db, j["je_id"])["status"] == "POSTED"


def _list_flag(db, monkeypatch, je_id: str, user: Dict[str, Any]):
    monkeypatch.setattr(je_router, "_get_db", lambda: db)
    code, body = _call(je_router.list_journal_entries(
        store_id=None, status=None, maker_id=None, current_user=user))
    assert code == 200, body
    row = next(r for r in body["journal_entries"] if r["je_id"] == je_id)
    code, one = _call(je_router.get_journal_entry(je_id, current_user=user))
    assert code == 200, one
    assert one.get("needs_reapproval") == row.get("needs_reapproval")
    return row.get("needs_reapproval")


def test_d54_an_approval_that_lapsed_before_post_is_approved_again(db, monkeypatch):
    """D54: an APPROVED journal whose approval lapsed before Post (410
    expired, Approve 409 not_submitted -- APPROVED for good) is approved again
    by a checker other than the maker, with their PIN. The list and the
    journal itself say so (needs_reapproval). A wrong PIN leaves one request
    waiting, which the inbox can decide too; the lapsed approval is left as
    stored; the journal posts once."""
    _user(db, "maker", ["ADMIN"])
    admin = _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    requests = db.get_collection("approval_requests")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    assert _list_flag(db, monkeypatch, j["je_id"], admin) is False
    requests.update_one({"request_id": j["request_id"]},
                        {"$set": {"expires_at": appr._now() - timedelta(minutes=5)}})
    lapsed = _request(db, j["request_id"])

    res = _post(db, j["je_id"])
    assert (res["http"], res["error"]) == (410, "expired")
    assert _list_flag(db, monkeypatch, j["je_id"], admin) is True

    res = _journal_approve(db, j["je_id"], "maker")
    assert (res["http"], res["error"]) == (403, "cannot_approve_own")
    res = _journal_approve(db, j["je_id"], "manager", ("STORE_MANAGER",))
    assert (res["http"], res["error"]) == (409, "not_submitted")
    assert requests.count_documents({}) == 1                 # neither raised anything

    for _ in range(2):
        res = je_service.approve_je(db, je_id=j["je_id"], approver_id="checker",
                                    approver_roles=["ADMIN"], pin="0000")
        assert (res["http"], res["error"]) == (403, "wrong_pin")
    assert requests.count_documents({}) == 2                 # the lapsed one + one raised again
    new_id = _je(db, j["je_id"])["approval_request_id"]
    assert _request(db, new_id)["status"] == "REQUESTED"
    assert _je(db, j["je_id"])["status"] == "APPROVED"
    assert _list_flag(db, monkeypatch, j["je_id"], admin) is True
    assert _post(db, j["je_id"])["error"] == "not_approved"

    assert _inbox_approve(db, monkeypatch, new_id, admin)[0] == 200
    assert _list_flag(db, monkeypatch, j["je_id"], admin) is False
    assert _post(db, j["je_id"]).get("ok") is True
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert _post(db, j["je_id"])["error"] == "already_posted"
    assert len(_posted_rows(db, j["je_id"])) == 1
    assert _request(db, j["request_id"]) == lapsed           # history not rewritten
    raised = [r for r in db.get_collection("audit_logs").find({"action": "je_request_raised_again"})
              if r.get("entity_id") == j["je_id"]]
    assert len(raised) == 1 and j["request_id"] in raised[0]["reason"]


def test_d54_journal_screen_reapproval_moves_the_checker(db):
    _user(db, "checker", ["ADMIN"])
    _user(db, "boss", ["SUPERADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]}, {"$set": {"expires_at": appr._now() - timedelta(minutes=5)}})
    res = _journal_approve(db, j["je_id"], "boss", ("SUPERADMIN",))
    assert res.get("ok") is True, res
    je = _je(db, j["je_id"])
    assert (je["status"], je["checker_id"]) == ("APPROVED", "boss")
    assert len([r for r in db.get_collection("audit_logs").find({"action": "je_reapproved"})
                if r.get("entity_id") == j["je_id"]]) == 1
    # A valid approval is never replaced.
    res = _journal_approve(db, j["je_id"], "checker")
    assert (res["http"], res["error"]) == (409, "not_submitted")
    assert _post(db, j["je_id"]).get("ok") is True


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


def _previous_version_append(db, coll, doc, *, between=None):
    """The audit writer of the version this PR replaces (origin/main before
    #1411): claim the seq by $inc on the head counter, chain from the head's
    last_hash, insert, then commit the hash. ``between`` runs after the claim
    and before the insert (a slow round trip). Returns the row or None
    (refused), exactly as that writer did."""
    from pymongo import ReturnDocument

    head = db.get_collection("audit_chain_head").find_one_and_update(
        {"_id": "primary"}, {"$inc": {"seq": 1}, "$setOnInsert": {"last_hash": audit_chain.GENESIS_HASH}},
        upsert=True, return_document=ReturnDocument.AFTER)
    seq, prev = int(head["seq"]), head.get("last_hash") or audit_chain.GENESIS_HASH
    doc.update(seq=seq, prev_hash=prev, entry_hash=audit_chain.compute_entry_hash(prev, seq, doc))
    if between is not None:
        between()
    try:
        coll.insert_one(doc)
    except Exception:  # noqa: BLE001
        return None
    db.get_collection("audit_chain_head").update_one(
        {"_id": "primary", "seq": seq}, {"$set": {"last_hash": doc["entry_hash"]}})
    return doc


def test_d54_a_previous_version_writer_mid_deploy_keeps_its_row(db):
    """Rolling deploy (D54): a worker still on the previous version claims its
    seq on the head counter and is slow to insert; a new worker writes
    meanwhile. The new writer took the same seq first and the old row was
    refused (lost). Now the new writer takes its seq in the same counter, waits
    for the old writer's row and chains from it: both rows kept, one chain."""
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    repo = AuditRepository(coll)
    assert repo.create(_audit_row(0)) is not None            # an ordinary first row
    new = {}

    def _new_writer_meanwhile():
        new["thread"] = threading.Thread(target=lambda: new.update(row=repo.create(_audit_row(2))))
        new["thread"].start()
        threading.Event().wait(0.1)                            # the old writer's slow round trip

    old = _previous_version_append(db, coll, _audit_row(1), between=_new_writer_meanwhile)
    new["thread"].join(timeout=30)
    assert old is not None                                     # was None: E11000 on seq 2
    assert new.get("row") is not None
    rows = _chained(db)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert [r["entity_id"] for r in rows] == ["P-0", "P-1", "P-2"]
    assert verify_chain(coll)["intact"] is True


class _FlakyRows:
    """The audit collection as one writer sees it: ``find_one`` on a seq fails
    ``fail_reads`` times (a transient read error), ``insert_one`` waits
    ``insert_delay`` seconds first (a slow round trip). Everything else is the
    real collection."""

    def __init__(self, coll, *, fail_reads: int = 0, insert_delay: float = 0.0):
        self._coll, self._fail, self._delay = coll, fail_reads, insert_delay

    def __getattr__(self, name):
        return getattr(self._coll, name)

    def find_one(self, flt=None, *a, **k):
        if self._fail and "seq" in (flt or {}):
            self._fail -= 1
            from pymongo.errors import AutoReconnect
            raise AutoReconnect("audit read lost")
        return self._coll.find_one(flt, *a, **k)

    def insert_one(self, doc, *a, **k):
        if self._delay:
            threading.Event().wait(self._delay)
        return self._coll.insert_one(doc, *a, **k)


def _old_writer_with_new_writer_meanwhile(db, new_repo, stall: float):
    """An ordinary first row, then a previous-version writer that claims its
    seq and takes ``stall`` seconds to insert while ``new_repo`` writes."""
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    assert AuditRepository(coll).create(_audit_row(0)) is not None
    new = {}

    def _meanwhile():
        new["t"] = threading.Thread(target=lambda: new.update(row=new_repo.create(_audit_row(2))))
        new["t"].start()
        threading.Event().wait(stall)

    old = _previous_version_append(db, coll, _audit_row(1), between=_meanwhile)
    new["t"].join(timeout=30)
    return old, new.get("row"), coll


def test_d54_a_previous_version_writer_stalled_past_the_grace_keeps_its_row(db, monkeypatch):
    """Review round 2 (D54 'must not lose a chain row'): the new writer waited
    _HOLDER_GRACE_S for the old writer's row, then took its seq over, so an
    old writer slower than the grace had its insert refused (E11000) and its
    audit row lost. Now a seq an old writer holds is never taken: the new
    writer claims a fresh seq above the counter. Both rows are stored; a late
    old row can at worst show as a chain break, never as a lost row."""
    monkeypatch.setattr(audit_chain, "_HOLDER_GRACE_S", 0.1)
    coll = db.get_collection("audit_logs")
    old, new, coll = _old_writer_with_new_writer_meanwhile(db, AuditRepository(coll), stall=0.6)
    assert old is not None                                     # was None: refused, row lost
    assert new is not None
    seqs = [r["seq"] for r in _chained(db)]
    assert sorted(r["entity_id"] for r in _chained(db)) == ["P-0", "P-1", "P-2"]
    assert len(set(seqs)) == 3


def test_d54_a_read_error_while_waiting_is_not_an_abandoned_seq(db, monkeypatch):
    """Review round 2: one failed read of the old writer's seq made the new
    writer treat that seq as abandoned at once and take it, losing the old
    row. A read error now just means 'not seen yet': the new writer keeps
    waiting, the old row lands, and the chain stays one chain."""
    monkeypatch.setattr(audit_chain, "_HOLDER_GRACE_S", 2.0)
    flaky = _FlakyRows(db.get_collection("audit_logs"), fail_reads=1)
    old, new, coll = _old_writer_with_new_writer_meanwhile(db, AuditRepository(flaky), stall=0.3)
    assert old is not None                                     # was None: taken over on the read error
    assert new is not None
    assert [r["entity_id"] for r in _chained(db)] == ["P-0", "P-1", "P-2"]
    assert verify_chain(coll)["intact"] is True


def test_d54_a_slow_new_writer_still_hands_its_seq_on_without_a_fork(db, monkeypatch):
    """Only a PREVIOUS-version writer's seq is never taken over. A new writer
    whose insert is slower than the grace is still taken over: its own insert
    is refused on the seq, it re-reads the tail and chains after the row that
    took its place -- every row kept, one unbroken chain (R19)."""
    monkeypatch.setattr(audit_chain, "_HOLDER_GRACE_S", 0.1)
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    assert AuditRepository(coll).create(_audit_row(0)) is not None
    slow = AuditRepository(_FlakyRows(coll, insert_delay=0.6))
    got = {}
    t = threading.Thread(target=lambda: got.update(slow=slow.create(_audit_row(1))))
    t.start()
    threading.Event().wait(0.2)                                # the slow writer holds seq 2
    fast = AuditRepository(coll).create(_audit_row(2))
    t.join(timeout=30)
    assert fast is not None and got.get("slow") is not None
    rows = _chained(db)
    assert [r["entity_id"] for r in rows] == ["P-0", "P-2", "P-1"]
    assert verify_chain(coll)["intact"] is True


class _HeadFails:
    """The database as the NEW writer sees it, with its claim-counter head
    failing once: ``fault="update"`` -- the guarded reservation update raises
    (AutoReconnect); ``fault="read"`` -- the head read after it raises;
    ``fault="update_landed"`` -- the update applies but its reply is lost."""

    def __init__(self, db, fault: str):
        self._db, self.fault, self.fired = db, fault, 0

    def get_collection(self, name):
        coll = self._db.get_collection(name)
        return self._Head(coll, self) if name == audit_chain.HEAD_COLLECTION else coll

    class _Head:
        def __init__(self, coll, owner):
            self._coll, self._o = coll, owner

        def __getattr__(self, name):
            return getattr(self._coll, name)

        def _fire(self, kind):
            from pymongo.errors import AutoReconnect

            if self._o.fault == kind and not self._o.fired:
                self._o.fired += 1
                raise AutoReconnect(f"head {kind} lost")

        def update_one(self, flt, update, *a, **k):
            if isinstance((flt or {}).get("seq"), dict):
                self._fire("update")
                out = self._coll.update_one(flt, update, *a, **k)
                self._fire("update_landed")
                return out
            return self._coll.update_one(flt, update, *a, **k)

        def find_one(self, *a, **k):
            self._fire("read")
            return self._coll.find_one(*a, **k)


class _RowsOn(_FlakyRows):
    """The audit collection, reaching the head through ``db``."""

    def __init__(self, coll, db, **kw):
        super().__init__(coll, **kw)
        self.database = db


@pytest.mark.parametrize("fault", ["update", "read"])
def test_d54_a_head_error_never_takes_a_previous_version_writers_seq(db, monkeypatch, fault):
    """Codex round 2 (mixed_head_failure): one transient error on the claim
    counter (its guarded update, or the read after it) made the new writer
    treat the seq as its own -- 'could not reserve' answered like 'reserved'
    -- so it took the seq a previous-version writer held, that writer's insert
    was refused and its audit row lost. An unknown owner is now never
    permission: the writer asks again, finds the old writer's claim, waits for
    its row and chains after it. Every row kept."""
    flaky = _HeadFails(db, fault)
    new_repo = AuditRepository(_RowsOn(db.get_collection("audit_logs"), flaky))
    old, new, coll = _old_writer_with_new_writer_meanwhile(db, new_repo, stall=0.3)
    assert flaky.fired == 1
    assert old is not None                                     # was None: E11000, row lost
    assert new is not None
    assert [r["entity_id"] for r in _chained(db)] == ["P-0", "P-1", "P-2"]
    assert verify_chain(coll)["intact"] is True


def test_d54_a_reservation_whose_reply_was_lost_still_writes_the_row(db, monkeypatch):
    """The reservation landed but its reply was lost: the writer does not know
    it holds the seq. Asking again finds its own claim in the new-writer ring;
    after the grace it takes the seq it already holds -- one row, one chain."""
    monkeypatch.setattr(audit_chain, "_HOLDER_GRACE_S", 0.05)
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    assert AuditRepository(coll).create(_audit_row(0)) is not None
    flaky = _HeadFails(db, "update_landed")
    assert AuditRepository(_RowsOn(coll, flaky)).create(_audit_row(1)) is not None
    assert flaky.fired == 1
    assert [r["entity_id"] for r in _chained(db)] == ["P-0", "P-1"]
    assert verify_chain(coll)["intact"] is True


def test_d54_a_head_that_stays_unreachable_keeps_the_row_unchained(db, monkeypatch):
    """A head that never answers: ownership is never known, so no seq is
    taken -- the row is still written (fail-soft, counted as unchained)."""
    monkeypatch.setattr(audit_chain, "_MAX_CLAIM_ATTEMPTS", 3)
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    assert AuditRepository(coll).create(_audit_row(0)) is not None
    class _HeadDown:
        def get_collection(self, name):
            if name != audit_chain.HEAD_COLLECTION:
                return db.get_collection(name)
            from pymongo.errors import AutoReconnect

            class _Down:
                def __getattr__(self, _n):
                    def _fail(*_a, **_k):
                        raise AutoReconnect("head down")
                    return _fail
            return _Down()

    row = AuditRepository(_RowsOn(coll, _HeadDown())).create(_audit_row(1))
    assert row is not None and "seq" not in row
    assert [r["entity_id"] for r in _chained(db)] == ["P-0"]
    assert coll.count_documents({"seq": {"$exists": False}}) == 1


def test_d54_a_seq_check_that_fails_after_a_refused_insert_keeps_the_row(db, monkeypatch):
    """A new writer slower than the grace is taken over (R19); its own insert
    is refused on the seq. If the read confirming that then failed, the row
    was given up as 'refused for another reason' and lost. A duplicate-key
    refusal now chains again from the new tail whatever that read said."""
    monkeypatch.setattr(audit_chain, "_HOLDER_GRACE_S", 0.1)
    coll = db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    assert AuditRepository(coll).create(_audit_row(0)) is not None
    slow = AuditRepository(_FlakyRows(coll, insert_delay=0.6, fail_reads=1))
    got = {}
    t = threading.Thread(target=lambda: got.update(slow=slow.create(_audit_row(1))))
    t.start()
    threading.Event().wait(0.2)                                # the slow writer holds seq 2
    fast = AuditRepository(coll).create(_audit_row(2))
    t.join(timeout=30)
    assert fast is not None
    assert got.get("slow") is not None                         # was None: row lost
    assert [r["entity_id"] for r in _chained(db)] == ["P-0", "P-2", "P-1"]
    assert verify_chain(coll)["intact"] is True


def test_d54_previous_and_new_writers_side_by_side_lose_nothing(mongo_db):
    """Real MongoDB: 3 previous-version and 3 new workers x 20 rows, released
    together. Every row is stored and no seq is used twice. (Rows of two
    previous-version writers can still chain from the same stale head -- that
    writer's own R19 break, gone once the deploy finishes.)"""
    coll = mongo_db.get_collection("audit_logs")
    coll.create_index("seq", unique=True, sparse=True)
    repo = AuditRepository(coll)
    gate = threading.Barrier(6)
    lost = []

    def _work(kind: str, w: int) -> None:
        gate.wait(timeout=20)
        for i in range(20):
            row = _audit_row(w * 100 + i)
            done = (_previous_version_append(mongo_db, coll, row) if kind == "old"
                    else repo.create(row))
            if done is None:
                lost.append((kind, w, i))

    threads = [threading.Thread(target=_work, args=(k, w)) for k in ("old", "new") for w in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert lost == []                                          # was 20+ old rows refused
    seqs = [r["seq"] for r in _chained(mongo_db)]
    assert len(seqs) == 120 and len(set(seqs)) == 120


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
    assert (_head(db)["last_seq"], _head(db)["last_hash"]) == (4, lost["entry_hash"])

    coll.delete_many({"seq": {"$gt": 4 - removed}})  # out-of-band removal of the tail
    for n in range(removed + 3):                      # well past the old head
        assert repo.create(_audit_row(10 + n)) is not None
        res = verify_chain(coll)
        assert (res["intact"], res["verified"], res["reason"]) == (
            False, True, "head_not_in_trail"), (n, res)
    # The surviving + new rows do link; the head still names the removed row.
    assert [r["seq"] for r in _chained(db)] == list(range(1, 4 + 3 + 1))
    assert (_head(db)["last_seq"], _head(db)["last_hash"]) == (4, lost["entry_hash"])


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
    # As the previous version left it: no last_seq on the head.
    heads.update_one({"_id": "primary"}, {"$set": stale, "$unset": {"last_seq": ""}})
    assert verify_chain(coll)["intact"] is True

    newest = None
    for n in range(3):
        newest = repo.create(_audit_row(20 + n))
        assert newest is not None
        res = verify_chain(coll)
        assert (res["intact"], res["entries_checked"]) == (True, 4 + n), (n, res)
    # The head caught up with the newest row. (failed_inserts: seqs 4 and 5
    # stay with the old writer that claimed them -- never reused, D54 -- so
    # the new rows are 6, 7, 8.)
    assert newest["seq"] == (8 if legacy == "failed_inserts" else 6)
    assert (_head(db)["last_seq"], _head(db)["last_hash"]) == (newest["seq"], newest["entry_hash"])
    # ... so removing that row is seen, and stays seen.
    coll.delete_one({"seq": newest["seq"]})
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
    assert (_head(db)["last_seq"], _head(db)["last_hash"]) == (3, rows[2]["entry_hash"])
    assert _head(db)["seq"] == 3                               # the claim counter never moves back
    assert verify_chain(coll)["intact"] is True
