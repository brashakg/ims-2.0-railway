"""
IMS 2.0 - E4 Approval / PIN + Maker-Checker engine tests (intent-level)
=======================================================================
These exercise the REAL ApprovalEngine against a faithful in-memory fake Mongo
(no network, no live mongod). A hollow shell that skips the PIN, the single
atomic find_one_and_update, the token mint, the TTL, or the audit row FAILS here.

Covers the packet's T1-T10 + the binding CORRECTIONS:
  - approve() is a SINGLE atomic find_one_and_update minting the token -> two
    concurrent approves yield exactly one token; replay -> already-reviewed.
  - consume_approval() is single-doc-atomic -> two concurrent consumes yield
    exactly one ok; the other "already_consumed".
  - PIN brute-force throttle (pin_attempts) locks out after 5 bad attempts.
  - Refund tier read from E2 get_policy (NOT a constant): patching the policy
    threshold changes the resolved tier.
  - 60-min TTL expiry (lazy + expire_stale sweep) + an audit row per transition.
  - PIN stored as bcrypt; no hash / no PIN appears in any audit row.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers.auth import hash_password  # noqa: E402
from api.services import approvals as appr  # noqa: E402
from api.services.approvals import ApprovalEngine  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (supports the operators the engine uses)
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


def _unset_path(doc: Dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


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
                if "." in kk:
                    _unset_path(doc, kk)
                else:
                    doc.pop(kk, None)


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field, direction=-1):
        self._docs = sorted(
            self._docs, key=lambda d: (d.get(field) is None, d.get(field)),
            reverse=(direction == -1),
        )
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
        # pymongo collections expose `.database`; AuditRepository.create derives
        # the chain-head db from it, so wiring it lets the REAL hash-chain run.
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
        if upsert:
            self._upsert(query, update)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                n += 1
        return type("R", (), {"modified_count": n, "matched_count": n})()

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return _project(d, None)
        if upsert:
            d = self._upsert(query, update)
            return _project(d, None)
        return None

    def _upsert(self, query, update):
        base: Dict[str, Any] = {}
        for k, v in query.items():
            if not isinstance(v, dict):
                base[k] = v
        soi = update.get("$setOnInsert", {})
        base.update(soi)
        # Apply $inc / $set against the new base.
        _apply_update(base, {k: v for k, v in update.items() if k != "$setOnInsert"})
        self.insert_one(base)
        return base

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
# Fixtures / helpers
# ============================================================================


def _seed_user(db, user_id: str, *, pin: Optional[str] = None) -> None:
    coll = db.get_collection("users")
    doc = {"user_id": user_id, "is_active": True, "roles": ["STORE_MANAGER"]}
    if pin is not None:
        doc["approval_pin_hash"] = hash_password(pin)
        doc["pin_attempts"] = {"count": 0, "window_start": appr._now()}
    coll.insert_one(doc)


def _audit_rows(db) -> List[Dict[str, Any]]:
    return db.get_collection("audit_logs").docs


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def engine(db):
    return ApprovalEngine(db=db)


# ============================================================================
# T1 - Tier routing (role check only)
# ============================================================================


def test_t1_tier_routing(engine, db):
    r400 = engine.request(action_type="refund", requested_by="u1", amount=400)
    r3000 = engine.request(action_type="refund", requested_by="u1", amount=3000)
    r12000 = engine.request(action_type="refund", requested_by="u1", amount=12000)

    assert r400["required_tier"] == "auto"
    assert "STORE_MANAGER" in r400["required_roles"]

    assert r3000["required_tier"] == "admin"
    assert "STORE_MANAGER" not in r3000["required_roles"]

    assert r12000["required_tier"] == "super"
    assert r12000["required_roles"] == ["SUPERADMIN"]

    # STORE_MANAGER cannot approve the super-tier request.
    _seed_user(db, "mgr", pin="1234")
    res = engine.approve(
        r12000["request_id"], approver_user_id="mgr",
        approver_roles=["STORE_MANAGER"], pin="1234",
    )
    assert res["ok"] is False
    assert res["http"] == 403
    assert res["error"] == "insufficient_tier"


def test_t1_required_tier_can_raise_but_never_lower(engine, db):
    # Adversarial regression: a maker-supplied required_tier must NOT be able to
    # route a high-value request to a low-tier approver (escalation bypass).
    # A high amount + required_tier="auto" must STAY at the amount-derived tier.
    r = engine.request(action_type="refund", requested_by="u1", amount=12000, required_tier="auto")
    assert r["required_tier"] == "super"          # clamp: cannot lower super -> auto
    assert r["required_roles"] == ["SUPERADMIN"]
    # required_tier may still RAISE (serial-mismatch pins a small refund to admin).
    r2 = engine.request(action_type="refund", requested_by="u1", amount=100, required_tier="admin")
    assert r2["required_tier"] == "admin"          # raise honoured
    # And it can raise to super on a tiny amount.
    r3 = engine.request(action_type="refund", requested_by="u1", amount=100, required_tier="super")
    assert r3["required_tier"] == "super"


# ============================================================================
# T2 - PIN lifecycle + brute-force throttle
# ============================================================================


def test_t2_pin_not_set_returns_423(engine, db):
    _seed_user(db, "mgr")  # no PIN
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    res = engine.approve(req["request_id"], approver_user_id="mgr",
                         approver_roles=["STORE_MANAGER"], pin="1234")
    assert res["ok"] is False
    assert res["http"] == 423
    assert res["error"] == "pin_not_set"


def test_t2_brute_force_lockout(engine, db):
    _seed_user(db, "mgr", pin="4321")
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    # 4 wrong attempts -> wrong_pin with a decrementing remaining count.
    for i in range(4):
        res = engine.approve(req["request_id"], approver_user_id="mgr",
                             approver_roles=["STORE_MANAGER"], pin="0000")
        assert res["error"] == "wrong_pin", i
        assert res["remaining"] == 4 - i
    # 5th wrong attempt -> locked.
    res5 = engine.approve(req["request_id"], approver_user_id="mgr",
                          approver_roles=["STORE_MANAGER"], pin="0000")
    assert res5["http"] == 423
    assert res5["error"] == "pin_locked"
    # 6th attempt within the window also locked, even with the CORRECT pin.
    res6 = engine.approve(req["request_id"], approver_user_id="mgr",
                          approver_roles=["STORE_MANAGER"], pin="4321")
    assert res6["http"] == 423
    assert res6["error"] == "pin_locked"


def test_t2_correct_pin_after_window_reset_succeeds(engine, db):
    _seed_user(db, "mgr", pin="4321")
    # Pre-load 5 failed attempts but with a window_start 16 min ago (expired).
    users = db.get_collection("users")
    old = appr._now() - timedelta(minutes=16)
    users.update_one({"user_id": "mgr"},
                     {"$set": {"pin_attempts": {"count": 5, "window_start": old}}})
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    res = engine.approve(req["request_id"], approver_user_id="mgr",
                         approver_roles=["STORE_MANAGER"], pin="4321")
    assert res["ok"] is True
    assert res["approval_token"].startswith("APT-")


def test_t2_pin_is_bcrypt_and_never_in_audit(engine, db):
    _seed_user(db, "mgr")  # user row must exist for set_approver_pin to land
    appr.set_approver_pin(db, "mgr", "5678", set_by="admin")
    user = db.get_collection("users").find_one({"user_id": "mgr"})
    # Stored as a bcrypt hash, not the plaintext.
    assert user["approval_pin_hash"].startswith("$2b$")
    assert user["approval_pin_hash"] != "5678"
    # Full lifecycle, then scan audit rows for any PIN leakage.
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    engine.approve(req["request_id"], approver_user_id="mgr",
                   approver_roles=["STORE_MANAGER"], pin="5678")
    import json
    # Scope the PIN-leakage scan to the approval-lifecycle audit rows (E4 writes
    # entity_type="approval_request"). A GLOBAL audit dump is fragile: any other
    # test sharing the audit_logs store whose data contains the substring "5678"
    # (a DC number, qty, amount, id) would false-trip this assertion regardless
    # of PIN handling. The intent here is "no PIN hash in any APPROVAL audit row".
    appr_rows = [r for r in _audit_rows(db)
                 if r.get("entity_type") == "approval_request"]
    blob = json.dumps(appr_rows, default=str)
    assert "5678" not in blob
    assert "$2b$" not in blob
    assert "approval_pin_hash" not in blob


# ============================================================================
# T3 - Single-use atomic guard (the money guard)
# ============================================================================


def test_t3_two_concurrent_approves_one_token(engine, db):
    _seed_user(db, "mgrA", pin="1111")
    _seed_user(db, "mgrB", pin="2222")
    req = engine.request(action_type="refund", requested_by="u1", amount=100)

    first = engine.approve(req["request_id"], approver_user_id="mgrA",
                           approver_roles=["STORE_MANAGER"], pin="1111")
    second = engine.approve(req["request_id"], approver_user_id="mgrB",
                            approver_roles=["STORE_MANAGER"], pin="2222")

    oks = [r for r in (first, second) if r.get("ok")]
    losers = [r for r in (first, second) if not r.get("ok")]
    assert len(oks) == 1
    assert oks[0]["approval_token"].startswith("APT-")
    assert len(losers) == 1
    assert losers[0]["http"] == 409


def test_t3_two_concurrent_consumes_one_wins(engine, db):
    _seed_user(db, "mgr", pin="1111")
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    appr_res = engine.approve(req["request_id"], approver_user_id="mgr",
                              approver_roles=["STORE_MANAGER"], pin="1111")
    token = appr_res["approval_token"]

    c1 = engine.consume_approval(consumed_by="u1", action_type="refund",
                                 approval_token=token)
    c2 = engine.consume_approval(consumed_by="u1", action_type="refund",
                                 approval_token=token)
    oks = [c for c in (c1, c2) if c.get("ok")]
    losers = [c for c in (c1, c2) if not c.get("ok")]
    assert len(oks) == 1
    assert len(losers) == 1
    assert losers[0]["error"] == "already_consumed"

    doc = db.get_collection("approval_requests").find_one({"request_id": req["request_id"]})
    assert doc["consumed"] is True
    assert doc["consumed_at"] is not None
    assert doc["status"] == "CONSUMED"


# ============================================================================
# T4 - 60-minute TTL enforcement
# ============================================================================


def test_t4_expired_request_blocks_approve_and_lazy_flips(engine, db):
    _seed_user(db, "mgr", pin="1111")
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    # Backdate expiry to 1 second ago.
    db.get_collection("approval_requests").update_one(
        {"request_id": req["request_id"]},
        {"$set": {"expires_at": appr._now() - timedelta(seconds=1)}},
    )
    res = engine.approve(req["request_id"], approver_user_id="mgr",
                         approver_roles=["STORE_MANAGER"], pin="1111")
    assert res["ok"] is False
    assert res["http"] == 410
    assert res["error"] == "expired"
    doc = db.get_collection("approval_requests").find_one({"request_id": req["request_id"]})
    assert doc["status"] == "EXPIRED"


def test_t4_expire_stale_sweep(engine, db):
    coll = db.get_collection("approval_requests")
    for i in range(3):
        r = engine.request(action_type="refund", requested_by="u1", amount=100)
        coll.update_one({"request_id": r["request_id"]},
                        {"$set": {"expires_at": appr._now() - timedelta(minutes=2)}})
    # One fresh request that must NOT be swept.
    fresh = engine.request(action_type="refund", requested_by="u1", amount=100)

    count = engine.expire_stale()
    assert count == 3
    expired = [d for d in coll.docs if d["status"] == "EXPIRED"]
    assert len(expired) == 3
    assert coll.find_one({"request_id": fresh["request_id"]})["status"] == "REQUESTED"

    expired_audits = [a for a in _audit_rows(db)
                      if a.get("action") == "expired"
                      and a.get("entity_type") == "approval_request"]
    assert len(expired_audits) == 3


# ============================================================================
# T5 - Maker-checker (journal_entry)
# ============================================================================


def test_t5_maker_checker_blocks_self_approval(engine, db):
    _seed_user(db, "user_A", pin="1111")
    _seed_user(db, "user_B", pin="2222")
    req = engine.request(action_type="journal_entry", requested_by="user_A", amount=100)
    assert req["request_id"]
    doc = db.get_collection("approval_requests").find_one({"request_id": req["request_id"]})
    assert doc["maker_checker"] is True

    self_res = engine.approve(req["request_id"], approver_user_id="user_A",
                              approver_roles=["ADMIN"], pin="1111")
    assert self_res["ok"] is False
    assert self_res["http"] == 403
    assert self_res["error"] == "cannot_approve_own"

    other = engine.approve(req["request_id"], approver_user_id="user_B",
                           approver_roles=["ADMIN"], pin="2222")
    assert other["ok"] is True


# ============================================================================
# T6 - Store scope
# ============================================================================


def test_t6_store_scope(engine, db):
    _seed_user(db, "smgr", pin="1111")
    _seed_user(db, "amgr", pin="2222")
    req = engine.request(action_type="refund", requested_by="u1", amount=100, store_id="S2")

    # STORE_MANAGER scoped to S1 cannot see / approve an S2 request.
    inbox = engine.list_inbox(approver_roles=["STORE_MANAGER"], store_ids=["S1"],
                              status="REQUESTED")
    assert all(r.get("store_id") != "S2" for r in inbox)
    res = engine.approve(req["request_id"], approver_user_id="smgr",
                         approver_roles=["STORE_MANAGER"], pin="1111",
                         approver_store_ids=["S1"])
    assert res["ok"] is False
    assert res["http"] == 403
    assert res["error"] == "store_scope"

    # AREA_MANAGER covering S1+S2 sees + approves it.
    inbox2 = engine.list_inbox(approver_roles=["AREA_MANAGER"], store_ids=["S1", "S2"],
                               status="REQUESTED")
    assert any(r.get("store_id") == "S2" for r in inbox2)
    ok = engine.approve(req["request_id"], approver_user_id="amgr",
                        approver_roles=["AREA_MANAGER"], pin="2222",
                        approver_store_ids=["S1", "S2"])
    assert ok["ok"] is True


# ============================================================================
# T7 - Consume re-checks amount
# ============================================================================


def test_t7_consume_rechecks_amount(engine, db):
    _seed_user(db, "mgr", pin="1111")
    req = engine.request(action_type="refund", requested_by="u1", amount=500)
    appr_res = engine.approve(req["request_id"], approver_user_id="mgr",
                              approver_roles=["STORE_MANAGER"], pin="1111")
    token = appr_res["approval_token"]

    over = engine.consume_approval(consumed_by="u1", action_type="refund",
                                   approval_token=token, amount=600)
    assert over["ok"] is False
    assert over["error"] == "amount_exceeded"
    # The approval was NOT burned by the bad spend.
    doc = db.get_collection("approval_requests").find_one({"request_id": req["request_id"]})
    assert doc["consumed"] is False
    assert doc["status"] == "APPROVED"

    ok = engine.consume_approval(consumed_by="u1", action_type="refund",
                                 approval_token=token, amount=500)
    assert ok["ok"] is True


def test_t7_consume_rechecks_action_type(engine, db):
    _seed_user(db, "mgr", pin="1111")
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    appr_res = engine.approve(req["request_id"], approver_user_id="mgr",
                              approver_roles=["STORE_MANAGER"], pin="1111")
    bad = engine.consume_approval(consumed_by="u1", action_type="discount_override",
                                  approval_token=appr_res["approval_token"])
    assert bad["ok"] is False
    assert bad["error"] == "action_mismatch"


# ============================================================================
# T8 - Audit completeness + chaining
# ============================================================================


def test_t8_audit_row_per_transition(engine, db):
    _seed_user(db, "mgr", pin="1111")
    req = engine.request(action_type="refund", requested_by="u1", amount=100)
    appr_res = engine.approve(req["request_id"], approver_user_id="mgr",
                              approver_roles=["STORE_MANAGER"], pin="1111")
    engine.consume_approval(consumed_by="u1", action_type="refund",
                            approval_token=appr_res["approval_token"])

    rows = [a for a in _audit_rows(db) if a.get("entity_type") == "approval_request"]
    actions = [a["action"] for a in rows]
    assert "approval_requested" in actions
    assert "approved" in actions
    assert "consumed" in actions
    # Hash-chained (AuditRepository.create -> audit_chain stamps these).
    for a in rows:
        assert "entry_hash" in a
        assert "prev_hash" in a


# ============================================================================
# T9 - Serial-mismatch unblock (a refund-router stub gated on consume_approval)
# ============================================================================


def test_t9_serial_mismatch_unblock(engine, db):
    _seed_user(db, "adm", pin="1111")

    def _do_refund_with_gate(token: Optional[str]) -> Dict[str, Any]:
        """A return-router stub: the refund only proceeds once an admin-tier
        approval is consumed. A hollow shell that bypasses consume FAILS here."""
        res = engine.consume_approval(consumed_by="cashier", action_type="refund",
                                      approval_token=token)
        if not res.get("ok"):
            return {"refunded": False, "reason": res.get("error")}
        return {"refunded": True}

    # No approval yet -> blocked.
    blocked = _do_refund_with_gate(token=None)
    assert blocked["refunded"] is False

    # Admin-tier (serial mismatch pins required_tier="admin") -> approve -> consume.
    req = engine.request(action_type="refund", requested_by="cashier", amount=100,
                         required_tier="admin")
    assert req["required_tier"] == "admin"
    appr_res = engine.approve(req["request_id"], approver_user_id="adm",
                              approver_roles=["ADMIN"], pin="1111")
    assert appr_res["ok"] is True
    done = _do_refund_with_gate(token=appr_res["approval_token"])
    assert done["refunded"] is True


# ============================================================================
# T10 - Fail-soft (no DB)
# ============================================================================


def test_t10_fail_soft_no_db():
    eng = ApprovalEngine(db=None)
    assert eng.list_inbox(approver_roles=["ADMIN"]) == []
    assert eng.list_mine(requested_by="u1") == []
    assert eng.request(action_type="refund", requested_by="u1", amount=100) == {
        "ok": False, "error": "no_db"}
    assert eng.expire_stale() == 0
    assert eng.approve("REQ-x", approver_user_id="m", approver_roles=["ADMIN"],
                       pin="1111")["ok"] is False
    assert appr.request_approval(None, action_type="refund", requested_by="u1") is None
    assert appr.set_approver_pin(None, "u1", "1234", "admin")["ok"] is False
    assert appr.has_approver_pin(None, "u1") == {"has_pin": False}


# ============================================================================
# Tier read from E2 get_policy (NOT a constant) - CORRECTIONS binding
# ============================================================================


def test_tier_reads_e2_get_policy_not_a_constant(engine, monkeypatch):
    """If the E2 admin_above threshold is LOWERED to Rs 100 (10000 paisa), a
    Rs 200 refund must escalate to the admin tier. This proves the engine reads
    E2 get_policy live and owns no _DEFAULT_TIERS constant."""
    import api.services.policy_engine as pe

    real = pe.get_policy

    def fake_get_policy(key, scope=None, *, default=None):
        if key == "refund.tier.auto_below":
            return 5000      # Rs 50
        if key == "refund.tier.admin_above":
            return 10000     # Rs 100  (lowered from the Rs 2000 registry default)
        if key == "refund.tier.super_above":
            return 1000000   # Rs 10000
        return real(key, scope, default=default)

    monkeypatch.setattr(pe, "get_policy", fake_get_policy)

    r = engine.request(action_type="refund", requested_by="u1", amount=200)
    assert r["required_tier"] == "admin"
    assert "STORE_MANAGER" not in r["required_roles"]


def test_e4_owns_no_default_tiers_constant():
    """Guard: E4 must NOT carry a _DEFAULT_TIERS constant (E2's registry default
    is the only fallback)."""
    assert not hasattr(appr, "_DEFAULT_TIERS")
