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
from datetime import datetime
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
    return {"log_id": f"B10-{uuid.uuid4().hex[:10]}", "action": "AUDIT_SYNTHETIC_ONLY",
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


def test_l3_25_post_resumes_after_its_journal_write_was_lost(db):
    """A Post that spent the approval and then lost its journal write left the
    journal APPROVED for good (every retry: already_consumed). The retry now
    finishes that post -- once."""
    _user(db, "checker", ["ADMIN"])
    j = _submitted_journal(db, "maker")
    assert _journal_approve(db, j["je_id"]).get("ok") is True
    spent = appr.ApprovalEngine(db=db).consume_approval(
        consumed_by="checker", action_type="journal_entry",
        request_id=j["request_id"], amount=100.0)
    assert spent.get("ok") is True, spent
    assert _je(db, j["je_id"])["status"] == "APPROVED"  # the interrupted state

    assert _post(db, j["je_id"]).get("ok") is True
    assert _je(db, j["je_id"])["status"] == "POSTED"
    assert _post(db, j["je_id"])["error"] == "already_posted"
    posted_rows = [r for r in db.get_collection("audit_logs").find({"action": "je_posted"})
                   if r.get("entity_id") == j["je_id"]]
    assert len(posted_rows) == 1


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
        {"$set": {"status": "APPROVED", "reviewed_by": "maker", "approval_token": "APT-forged"}})
    assert _post(db, j["je_id"])["error"] == "not_approved"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"


def test_l3_25_inbox_holds_a_journal_to_the_journal_checker_roles(db, monkeypatch):
    """The journal screen lets only ADMIN / SUPERADMIN approve a journal. A
    small journal's request used to be routed to store and area managers too;
    the repair must not turn a manager's inbox click into a journal approval."""
    manager = _user(db, "manager", ["STORE_MANAGER"])
    j = _submitted_journal(db, "maker", amount=100.0)  # the manager amount tier
    assert set(_request(db, j["request_id"])["required_roles"]) == {"ADMIN", "SUPERADMIN"}

    code, body = _inbox_approve(db, monkeypatch, j["request_id"], manager)
    assert code == 403 and body["error"] == "insufficient_tier"
    code, body = _inbox_reject(db, monkeypatch, j["request_id"], manager, "no")
    assert code == 403 and body["error"] == "insufficient_tier"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"
    assert _request(db, j["request_id"])["status"] == "REQUESTED"


def test_l3_25_manager_decision_on_an_older_request_is_not_taken(db):
    """A request raised before the routing was narrowed (stored roles include
    the managers) and approved by one: the journal does not take it."""
    j = _submitted_journal(db, "maker")
    db.get_collection("approval_requests").update_one(
        {"request_id": j["request_id"]},
        {"$set": {"required_roles": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
                  "status": "APPROVED", "reviewed_by": "manager",
                  "approval_token": "APT-old"}})
    assert _post(db, j["je_id"])["error"] == "not_approved"
    assert _je(db, j["je_id"])["status"] == "SUBMITTED"


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
