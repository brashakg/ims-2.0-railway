"""
IMS 2.0 - F49 family/household loyalty wallet tests (intent-level)
==================================================================
Exercises the REAL family_wallet service + router + money_guard FAMILY_WALLET
account + the REAL reminder_rail OTP slice against a faithful in-memory fake
Mongo (no network, no live mongod, NO live SMS -- notification dispatch is
captured, never sent). A shell that lets a customer join two households, an
8th member slip past the in-filter cap, a redeem skip the OTP, two concurrent
redeems overdraw the pool, or a non-member spend the family's points FAILS
here.

Maps to the F49 acceptance intents:
  * household lifecycle  -- create (primary = member[0]), add to max (E2
                            policy, default 7), remove (primary irremovable)
  * unique membership    -- a customer belongs to at most ONE ACTIVE household
  * max-7 IN THE FILTER  -- two concurrent adds at capacity-1 -> exactly one
                            winner (the $expr $size guard, not a Python check)
  * pool earn/redeem     -- money_guard FAMILY_WALLET account, integer POINTS
                            unit, guarded debit floor IN the filter
  * OTP gate             -- redeem REQUIRES the rail's verified consume-once
                            OTP (wrong/expired/mismatched household or amount
                            all rejected); OTP rides the captured SMS path
                            (no provider call ever fires in tests)
  * voucher mint          -- a successful redeem mints a store-credit voucher
                            via the canonical vouchers.mint_voucher
  * chain-wide lookup    -- BY OWNER DECISION no store 403 on household reads
  * RBAC                 -- cashier cannot create a household; manager can
  * fail-soft            -- db=None -> 503 envelopes, never a crash
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DISPATCH_MODE", "off")  # belt-and-braces: never live SMS

from api.services import family_wallet as svc  # noqa: E402
from api.services import money_guard as mg  # noqa: E402
from api.services import reminder_rail as rail  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ============================================================================
# Faithful in-memory fake Mongo (the operators F49 + money_guard + the OTP
# rail use: dotted paths, array semantics, $ne/$gte/$or, $expr $size, $push/
# $pull, atomic find_one_and_update under a lock)
# ============================================================================


def _get_path(doc: Any, path: str) -> List[Any]:
    """Resolve a (possibly dotted) path; fan out over arrays like Mongo.
    Returns the LIST of leaf values found (empty when missing)."""
    parts = path.split(".")
    current: List[Any] = [doc]
    for part in parts:
        nxt: List[Any] = []
        for node in current:
            if isinstance(node, dict) and part in node:
                nxt.append(node[part])
            elif isinstance(node, list):
                for el in node:
                    if isinstance(el, dict) and part in el:
                        nxt.append(el[part])
        current = nxt
    return current


def _cmp_op(values: List[Any], op: str, expected: Any) -> bool:
    """Mongo comparison semantics over the resolved leaf values. For arrays the
    doc matches when ANY element satisfies the op -- EXCEPT $ne, which (like
    Mongo) matches only when NO element equals the expected value."""
    flat: List[Any] = []
    for v in values:
        if isinstance(v, list):
            flat.extend(v)
            flat.append(v)  # whole-array equality also possible
        else:
            flat.append(v)
    if op == "$ne":
        return all(v != expected for v in flat)
    if op == "$exists":
        return bool(flat) == bool(expected)
    for v in flat:
        try:
            if op == "$gt" and v is not None and v > expected:
                return True
            if op == "$gte" and v is not None and v >= expected:
                return True
            if op == "$lt" and v is not None and v < expected:
                return True
            if op == "$lte" and v is not None and v <= expected:
                return True
            if op == "$in" and v in expected:
                return True
        except TypeError:
            continue
    return False


def _eval_expr(doc: Dict[str, Any], expr: Any) -> Any:
    """Tiny $expr evaluator: $size, "$field" refs, $lt/$lte/$gt/$gte/$eq."""
    if isinstance(expr, str) and expr.startswith("$"):
        vals = _get_path(doc, expr[1:])
        return vals[0] if vals else None
    if isinstance(expr, dict):
        if "$size" in expr:
            arr = _eval_expr(doc, expr["$size"])
            return len(arr) if isinstance(arr, list) else 0
        for op, fn in (
            ("$lt", lambda a, b: a < b),
            ("$lte", lambda a, b: a <= b),
            ("$gt", lambda a, b: a > b),
            ("$gte", lambda a, b: a >= b),
            ("$eq", lambda a, b: a == b),
        ):
            if op in expr:
                a, b = (_eval_expr(doc, x) for x in expr[op])
                try:
                    return fn(a, b)
                except TypeError:
                    return False
    return expr


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        if k == "$expr":
            if not _eval_expr(doc, v):
                return False
            continue
        values = _get_path(doc, k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(values, op, expected):
                    return False
            continue
        # Equality: scalar == scalar, OR array-contains (Mongo multikey).
        hit = False
        for actual in values:
            if actual == v or (isinstance(actual, list) and v in actual):
                hit = True
                break
        if not hit:
            return False
    return True


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                if "." in kk:  # positional-free dotted set (not needed here)
                    continue
                doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                doc[kk] = (doc.get(kk) or 0) + vv
        elif op == "$push":
            for kk, vv in fields.items():
                doc.setdefault(kk, []).append(vv)
        elif op == "$pull":
            for kk, vv in fields.items():
                doc[kk] = [x for x in (doc.get(kk) or []) if x != vv]


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    """Document-level atomicity modeled with a lock (find_one_and_update /
    update_one match+mutate under the lock, like Mongo's single-doc guarantee)."""

    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        self._lock = threading.Lock()

    def insert_one(self, doc):
        with self._lock:
            doc.setdefault("_id", f"oid-{self._n}")
            self._n += 1
            if any(d.get("_id") == doc["_id"] for d in self.docs):
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError(f"E11000 duplicate key: _id {doc['_id']}")
            self.docs.append(dict(doc))
            return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])

    def find_one_and_update(
        self, query, update, return_document=None, upsert=False, **_kw
    ):
        with self._lock:
            for d in self.docs:
                if _matches(d, query):
                    _apply_update(d, update)
                    return dict(d)
        return None

    def update_one(self, query, update, upsert=False):
        with self._lock:
            for d in self.docs:
                if _matches(d, query):
                    _apply_update(d, update)
                    return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Fixtures + helpers
# ============================================================================


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def _manager(uid="M1", store="BV-1"):
    return {
        "user_id": uid,
        "full_name": "Manager One",
        "roles": ["STORE_MANAGER"],
        "store_ids": [store],
        "active_store_id": store,
    }


def _cashier(uid="K1", store="BV-1"):
    return {
        "user_id": uid,
        "full_name": "Cashier",
        "roles": ["SALES_CASHIER"],
        "store_ids": [store],
        "active_store_id": store,
    }


def _staff_bv2(uid="S2"):
    return {
        "user_id": uid,
        "full_name": "Staff Two",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-2"],
        "active_store_id": "BV-2",
    }


def _seed_customers(db: FakeDB, *ids: str):
    coll = db.get_collection("customers")
    for cid in ids:
        coll.insert_one(
            {"customer_id": cid, "name": f"Cust {cid}", "mobile": "9000000001"}
        )


def _mk_household(db: FakeDB, primary="C1", store="BV-1") -> str:
    out = svc.create_household(
        db, primary_customer_id=primary, actor=_manager(), store_id=store
    )
    assert out["ok"], out
    return out["household"]["household_id"]


def _capture_otp(monkeypatch) -> Dict[str, Any]:
    """Replace the notification dispatch with a capture: the OTP code is
    grabbed for the test, a PENDING notification row is recorded, and NO
    provider is ever called (the function that talks to MSG91 is gone)."""
    box: Dict[str, Any] = {"calls": 0}
    import api.services.notification_service as ns

    async def _cap(**kw):
        box["calls"] += 1
        box["category"] = kw.get("category")
        if kw.get("category") == "OTP":
            box["code"] = (kw.get("variables") or {}).get("otp")
        return {"notification_id": "N1", "dispatched": False, "status": "PENDING"}

    monkeypatch.setattr(ns, "send_notification", _cap)
    return box


def _issue_otp(
    db, monkeypatch, *, household_id: str, primary="C1", points=200
) -> Dict[str, Any]:
    box = _capture_otp(monkeypatch)
    out = _run(
        rail.send_pool_redemption_otp(
            db,
            primary_customer_id=primary,
            household_id=household_id,
            amount=points,
            requested_by="U1",
        )
    )
    assert out["ok"], out
    return {"otp_id": out["otp_id"], "code": box["code"], "box": box}


# ============================================================================
# Household lifecycle
# ============================================================================


def test_create_household_primary_is_member_zero(db):
    _seed_customers(db, "C1")
    out = svc.create_household(
        db, primary_customer_id="C1", actor=_manager(), store_id="BV-1"
    )
    assert out["ok"]
    hh = out["household"]
    assert hh["household_id"].startswith("HH-")
    assert hh["primary_customer_id"] == "C1"
    assert hh["member_customer_ids"] == ["C1"]
    assert hh["status"] == "ACTIVE"
    assert hh["store_id"] == "BV-1"  # provenance only; lookup is chain-wide


def test_create_household_unknown_primary_404(db):
    out = svc.create_household(db, primary_customer_id="GHOST", actor=_manager())
    assert out == {"ok": False, "http": 404, "error": "customer_not_found"}


def test_double_enroll_same_customer_blocked(db):
    """Unique membership: a customer in one ACTIVE household can neither found
    a second household nor be added to another one."""
    _seed_customers(db, "C1", "C2")
    h1 = _mk_household(db, primary="C1")
    # Founding a second household with the same primary -> 409.
    again = svc.create_household(db, primary_customer_id="C1", actor=_manager())
    assert again["http"] == 409 and again["error"] == "already_in_household"
    # Adding an existing member of H1 into another household -> 409.
    _seed_customers(db, "C9")
    h2 = _mk_household(db, primary="C9")
    cross = svc.add_member(db, h2, "C1", actor=_manager())
    assert cross["http"] == 409 and cross["error"] == "already_in_household"
    assert cross["household_id"] == h1


def test_add_member_to_seven_ok_eighth_409(db):
    ids = [f"C{i}" for i in range(1, 10)]
    _seed_customers(db, *ids)
    hid = _mk_household(db, primary="C1")
    for cid in ids[1:7]:  # members 2..7
        out = svc.add_member(db, hid, cid, actor=_manager(), max_members=7)
        assert out["ok"], out
    assert len(svc.get_household(db, hid)["member_customer_ids"]) == 7
    eighth = svc.add_member(db, hid, "C8", actor=_manager(), max_members=7)
    assert eighth == {
        "ok": False,
        "http": 409,
        "error": "household_full",
        "max_members": 7,
    }
    assert len(svc.get_household(db, hid)["member_customer_ids"]) == 7


def test_add_same_member_twice_409(db):
    _seed_customers(db, "C1", "C2")
    hid = _mk_household(db)
    assert svc.add_member(db, hid, "C2", actor=_manager())["ok"]
    dup = svc.add_member(db, hid, "C2", actor=_manager())
    assert dup["http"] == 409 and dup["error"] == "already_member"
    assert svc.get_household(db, hid)["member_customer_ids"].count("C2") == 1


def test_concurrent_adds_at_capacity_one_winner(db):
    """Two racing adds at 6/7: the max-7 lives IN the find_one_and_update
    filter, so exactly ONE wins -- never an 8-member household."""
    ids = [f"C{i}" for i in range(1, 9)]
    _seed_customers(db, *ids)
    hid = _mk_household(db, primary="C1")
    for cid in ids[1:6]:  # 6 members total
        assert svc.add_member(db, hid, cid, actor=_manager(), max_members=7)["ok"]

    results: List[Dict[str, Any]] = []

    def _add(cid):
        results.append(svc.add_member(db, hid, cid, actor=_manager(), max_members=7))

    t1 = threading.Thread(target=_add, args=("C7",))
    t2 = threading.Thread(target=_add, args=("C8",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winners = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0]["error"] == "household_full"
    assert len(svc.get_household(db, hid)["member_customer_ids"]) == 7


def test_remove_member_ok_primary_irremovable(db):
    _seed_customers(db, "C1", "C2")
    hid = _mk_household(db)
    assert svc.add_member(db, hid, "C2", actor=_manager())["ok"]
    out = svc.remove_member(db, hid, "C2", actor=_manager())
    assert out["ok"]
    assert out["household"]["member_customer_ids"] == ["C1"]
    # The primary can NEVER be removed (filter excludes it).
    prim = svc.remove_member(db, hid, "C1", actor=_manager())
    assert prim == {"ok": False, "http": 409, "error": "primary_irremovable"}
    assert svc.get_household(db, hid)["member_customer_ids"] == ["C1"]
    # Removing a non-member -> 404.
    ghost = svc.remove_member(db, hid, "C5", actor=_manager())
    assert ghost["http"] == 404 and ghost["error"] == "not_a_member"


# ============================================================================
# Pool earn (money_guard FAMILY_WALLET; integer POINTS unit; lazy wallet)
# ============================================================================


def test_pool_earn_credits_pool_and_creates_wallet_lazily(db):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    assert svc.pool_balance(db, hid) == 0  # no wallet doc yet -> 0, no crash
    out = svc.pool_earn(db, hid, 250, actor=_manager(), source_order_id="ORD-1")
    assert out["ok"] and out["balance"] == 250 and out["duplicate"] is False
    wallet = db.get_collection("family_wallets").find_one({"household_id": hid})
    assert wallet is not None
    assert wallet["balance_points"] == 250  # integer POINTS, not paise/rupees
    assert wallet["status"] == "ACTIVE"
    assert svc.pool_balance(db, hid) == 250
    # Loyalty-txn audit row mirrors loyalty.py's ledger shape.
    txn = db.get_collection("loyalty_transactions").find_one({"household_id": hid})
    assert txn["type"] == "POOL_EARN" and txn["points"] == 250
    assert txn["order_id"] == "ORD-1" and txn["txn_id"] == out["txn_id"]


def test_pool_earn_idempotent_per_order(db):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    first = svc.pool_earn(db, hid, 100, actor=_manager(), source_order_id="ORD-9")
    retry = svc.pool_earn(db, hid, 100, actor=_manager(), source_order_id="ORD-9")
    assert first["ok"] and first["duplicate"] is False
    assert retry["ok"] and retry["duplicate"] is True
    assert svc.pool_balance(db, hid) == 100  # NOT 200


def test_pool_earn_validation(db):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    assert svc.pool_earn(db, hid, 0, actor=_manager())["http"] == 422
    assert svc.pool_earn(db, hid, -5, actor=_manager())["http"] == 422
    assert svc.pool_earn(db, hid, "junk", actor=_manager())["http"] == 422
    assert svc.pool_earn(db, "HH-NOPE", 10, actor=_manager())["http"] == 404


# ============================================================================
# Pool redeem -- OTP gate (REAL rail verify) + guarded floor
# ============================================================================


def test_redeem_without_otp_rejected_balance_untouched(db):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    out = svc.pool_redeem(
        db, hid, 200, redeeming_customer_id="C1", actor=_manager(), require_otp=True
    )
    assert out == {"ok": False, "http": 400, "error": "otp_required"}
    assert svc.pool_balance(db, hid) == 500


def test_redeem_with_verified_otp_debits_pool(db, monkeypatch):
    _seed_customers(db, "C1", "C2")
    hid = _mk_household(db)
    assert svc.add_member(db, hid, "C2", actor=_manager())["ok"]
    svc.pool_earn(db, hid, 500, actor=_manager())

    otp = _issue_otp(db, monkeypatch, household_id=hid, points=200)
    # The OTP rode the captured SMS path: PENDING, never dispatched, exactly
    # one category=OTP call, raw code never stored.
    assert otp["box"]["calls"] == 1 and otp["box"]["category"] == "OTP"
    stored = db.get_collection("pool_otp").find_one({"otp_id": otp["otp_id"]})
    assert stored["code_hash"] != otp["code"]

    out = svc.pool_redeem(
        db,
        hid,
        200,
        redeeming_customer_id="C2",
        actor=_cashier(),
        otp_id=otp["otp_id"],
        otp_code=otp["code"],
        require_otp=True,
    )
    assert out["ok"] and out["balance"] == 300 and out["points"] == 200
    assert svc.pool_balance(db, hid) == 300
    txn = db.get_collection("loyalty_transactions").find_one({"type": "POOL_REDEEM"})
    assert txn["household_id"] == hid and txn["customer_id"] == "C2"
    assert txn["points"] == 200
    # The consumed OTP cannot be replayed (atomic consume-once + idempotent debit).
    again = svc.pool_redeem(
        db,
        hid,
        200,
        redeeming_customer_id="C2",
        actor=_cashier(),
        otp_id=otp["otp_id"],
        otp_code=otp["code"],
        require_otp=True,
    )
    assert again["ok"] is False or again.get("duplicate") is True
    assert svc.pool_balance(db, hid) == 300  # never double-debited


def test_redeem_wrong_otp_code_rejected(db, monkeypatch):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    otp = _issue_otp(db, monkeypatch, household_id=hid, points=200)
    out = svc.pool_redeem(
        db,
        hid,
        200,
        redeeming_customer_id="C1",
        actor=_cashier(),
        otp_id=otp["otp_id"],
        otp_code="000000-WRONG",
        require_otp=True,
    )
    assert out["http"] == 403 and out["error"] == "otp_wrong_code"
    assert svc.pool_balance(db, hid) == 500


def test_redeem_expired_otp_rejected(db, monkeypatch):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    otp = _issue_otp(db, monkeypatch, household_id=hid, points=200)
    # Force expiry (the rail's REAL expiry check flips the doc to EXPIRED).
    db.get_collection("pool_otp").update_one(
        {"otp_id": otp["otp_id"]}, {"$set": {"expires_at": "2000-01-01T00:00:00+00:00"}}
    )
    out = svc.pool_redeem(
        db,
        hid,
        200,
        redeeming_customer_id="C1",
        actor=_cashier(),
        otp_id=otp["otp_id"],
        otp_code=otp["code"],
        require_otp=True,
    )
    assert out["http"] == 403 and out["error"] == "otp_expired"
    assert svc.pool_balance(db, hid) == 500


def test_redeem_otp_household_mismatch_403(db, monkeypatch):
    _seed_customers(db, "C1", "C9")
    h1 = _mk_household(db, primary="C1")
    h2 = _mk_household(db, primary="C9")
    svc.pool_earn(db, h2, 500, actor=_manager())
    # OTP issued for H1, replayed against H2 -> rejected.
    otp = _issue_otp(db, monkeypatch, household_id=h1, points=200)
    out = svc.pool_redeem(
        db,
        h2,
        200,
        redeeming_customer_id="C9",
        actor=_cashier(),
        otp_id=otp["otp_id"],
        otp_code=otp["code"],
        require_otp=True,
    )
    assert out["http"] == 403 and out["error"] == "otp_household_mismatch"
    assert svc.pool_balance(db, h2) == 500


def test_redeem_otp_amount_mismatch_403(db, monkeypatch):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    otp = _issue_otp(db, monkeypatch, household_id=hid, points=100)  # authorizes 100
    out = svc.pool_redeem(
        db,
        hid,
        400,
        redeeming_customer_id="C1",
        actor=_cashier(),
        otp_id=otp["otp_id"],
        otp_code=otp["code"],
        require_otp=True,
    )
    assert out["http"] == 403 and out["error"] == "otp_amount_mismatch"
    assert svc.pool_balance(db, hid) == 500


def test_cross_household_redeem_403_not_a_member(db):
    _seed_customers(db, "C1", "C9")
    h1 = _mk_household(db, primary="C1")
    _mk_household(db, primary="C9")
    svc.pool_earn(db, h1, 500, actor=_manager())
    out = svc.pool_redeem(
        db, h1, 100, redeeming_customer_id="C9", actor=_cashier(), require_otp=False
    )
    assert out == {"ok": False, "http": 403, "error": "not_a_member"}
    assert svc.pool_balance(db, h1) == 500


def test_redeem_insufficient_balance_409(db):
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 50, actor=_manager())
    out = svc.pool_redeem(
        db, hid, 100, redeeming_customer_id="C1", actor=_cashier(), require_otp=False
    )
    assert out["http"] == 409 and out["error"] == "insufficient_balance"
    assert svc.pool_balance(db, hid) == 50
    # A never-funded pool reads as insufficient too (not a crash).
    _seed_customers(db, "C9")
    h2 = _mk_household(db, primary="C9")
    out2 = svc.pool_redeem(
        db, h2, 10, redeeming_customer_id="C9", actor=_cashier(), require_otp=False
    )
    assert out2["http"] == 409 and out2["error"] == "insufficient_balance"


def test_concurrent_redeems_near_floor_one_winner(db):
    """Two racing redeems of 80 against a 100-point pool: the floor
    (balance_points >= amount) lives IN the money_guard debit filter, so
    exactly ONE wins -- the pool can never go negative."""
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 100, actor=_manager())

    results: List[Dict[str, Any]] = []

    def _redeem():
        results.append(
            svc.pool_redeem(
                db,
                hid,
                80,
                redeeming_customer_id="C1",
                actor=_cashier(),
                require_otp=False,
            )
        )

    t1 = threading.Thread(target=_redeem)
    t2 = threading.Thread(target=_redeem)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winners = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0]["error"] == "insufficient_balance"
    assert svc.pool_balance(db, hid) == 20  # 100 - 80, exactly once


# ============================================================================
# DB-absent fail-soft + policy-driven max
# ============================================================================


def test_db_absent_failsoft():
    actor = _manager()
    assert (
        svc.create_household(None, primary_customer_id="C1", actor=actor)["http"] == 503
    )
    assert svc.add_member(None, "H", "C", actor=actor)["http"] == 503
    assert svc.remove_member(None, "H", "C", actor=actor)["http"] == 503
    assert svc.pool_earn(None, "H", 10, actor=actor)["http"] == 503
    assert (
        svc.pool_redeem(None, "H", 10, redeeming_customer_id="C", actor=actor)["http"]
        == 503
    )
    assert svc.get_household(None, "H") is None
    assert svc.get_household_by_customer(None, "C") is None
    assert svc.pool_balance(None, "H") == 0
    svc.ensure_indexes(None)  # no raise


def test_max_members_honors_policy_override(db):
    """The cap is the E2 policy value the caller resolves -- a 3-member policy
    means the 4th add 409s even though the code default is 7."""
    ids = ["C1", "C2", "C3", "C4"]
    _seed_customers(db, *ids)
    hid = _mk_household(db, primary="C1")
    assert svc.add_member(db, hid, "C2", actor=_manager(), max_members=3)["ok"]
    assert svc.add_member(db, hid, "C3", actor=_manager(), max_members=3)["ok"]
    fourth = svc.add_member(db, hid, "C4", actor=_manager(), max_members=3)
    assert fourth["http"] == 409 and fourth["error"] == "household_full"
    assert fourth["max_members"] == 3


# ============================================================================
# ROUTER -- RBAC gates, chain-wide lookup, policy plumb-through, voucher mint
# ============================================================================


def _wire(monkeypatch, db):
    from api.routers import family_wallet as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    return r


def test_router_cashier_cannot_create_household_manager_can(db, monkeypatch):
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1")
    body = r.HouseholdCreateBody(primary_customer_id="C1")
    with pytest.raises(HTTPException) as exc:
        _run(r.create_household(body, current_user=_cashier()))
    assert exc.value.status_code == 403
    out = _run(r.create_household(body, current_user=_manager()))
    assert out["household_id"].startswith("HH-")


def test_router_chain_wide_lookup_no_store_403(db, monkeypatch):
    """BY OWNER DECISION: a BV-2 staffer reads a household created at BV-1 --
    no store fence on lookup (mirrors chain-wide customer lookup)."""
    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1", "C2")
    hid = _mk_household(db, primary="C1", store="BV-1")
    assert svc.add_member(db, hid, "C2", actor=_manager())["ok"]
    svc.pool_earn(db, hid, 75, actor=_manager())

    by_cust = _run(r.get_by_customer("C2", current_user=_staff_bv2()))
    assert by_cust["household_id"] == hid
    assert by_cust["pool_balance_points"] == 75

    direct = _run(r.get_household(hid, current_user=_staff_bv2()))
    assert direct["pool_balance_points"] == 75 and direct["store_id"] == "BV-1"


def test_router_max_members_honors_e2_policy(db, monkeypatch):
    r = _wire(monkeypatch, db)
    monkeypatch.setattr(
        r,
        "_get_policy",
        lambda key, scope=None, *, default=None: (
            3 if key == "loyalty.pool_max_members" else default
        ),
    )
    ids = ["C1", "C2", "C3", "C4"]
    _seed_customers(db, *ids)
    hid = _mk_household(db, primary="C1")
    for cid in ("C2", "C3"):
        out = _run(
            r.add_member(hid, r.MemberAddBody(customer_id=cid), current_user=_manager())
        )
        assert cid in out["member_customer_ids"]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _run(
            r.add_member(
                hid, r.MemberAddBody(customer_id="C4"), current_user=_manager()
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "household_full"


def test_router_redeem_full_flow_mints_store_credit_voucher(db, monkeypatch):
    """request-otp (captured SMS, PENDING, no provider) -> redeem with the code
    -> pool debited + canonical store-credit voucher minted (rupee conversion
    happens HERE; the pool stays in points)."""
    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1", "C2")
    hid = _mk_household(db, primary="C1")
    assert svc.add_member(db, hid, "C2", actor=_manager())["ok"]
    svc.pool_earn(db, hid, 500, actor=_manager())

    box = _capture_otp(monkeypatch)
    req = _run(
        r.request_redeem_otp(hid, r.RequestOtpBody(points=200), current_user=_cashier())
    )
    assert req["otp_id"].startswith("OTP-") and "code" not in req
    assert box["calls"] == 1 and box["category"] == "OTP"  # captured, never live

    out = _run(
        r.redeem(
            hid,
            r.RedeemBody(
                points=200,
                redeeming_customer_id="C2",
                otp_id=req["otp_id"],
                otp_code=box["code"],
            ),
            current_user=_cashier(),
        )
    )
    assert out["ok"] is True
    assert out["points_redeemed"] == 200
    assert out["pool_balance_points"] == 300
    assert out["rupee_value"] == 200.0  # default redeem_rupee_per_point = 1.0
    assert out["voucher"]["code"].startswith("GC-")
    voucher = db.get_collection("vouchers").find_one(
        {"voucher_id": out["voucher"]["voucher_id"]}
    )
    assert voucher["status"] == "ACTIVE" and voucher["balance"] == 200.0
    assert voucher["source"] == "family_wallet_pool"
    assert voucher["household_id"] == hid
    assert voucher["issued_to_customer_id"] == "C2"
    assert voucher["pool_txn_id"] == out["txn_id"]


def test_router_redeem_without_otp_400_when_policy_requires(db, monkeypatch):
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    with pytest.raises(HTTPException) as exc:
        _run(
            r.redeem(
                hid,
                r.RedeemBody(points=100, redeeming_customer_id="C1"),
                current_user=_cashier(),
            )
        )
    assert exc.value.status_code == 400 and exc.value.detail == "otp_required"
    assert svc.pool_balance(db, hid) == 500


def test_router_redeem_policy_can_waive_otp(db, monkeypatch):
    """E2 loyalty.pool_redeem_requires_otp=False waives the gate (owner
    policy); the guarded debit + voucher mint still run."""
    r = _wire(monkeypatch, db)
    monkeypatch.setattr(
        r,
        "_get_policy",
        lambda key, scope=None, *, default=None: (
            False if key == "loyalty.pool_redeem_requires_otp" else default
        ),
    )
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    out = _run(
        r.redeem(
            hid,
            r.RedeemBody(points=150, redeeming_customer_id="C1"),
            current_user=_cashier(),
        )
    )
    assert out["ok"] and out["pool_balance_points"] == 350
    assert out["voucher"]["code"].startswith("GC-")


def test_router_request_otp_insufficient_pool_409_no_sms(db, monkeypatch):
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 50, actor=_manager())
    box = _capture_otp(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _run(
            r.request_redeem_otp(
                hid, r.RequestOtpBody(points=100), current_user=_cashier()
            )
        )
    assert exc.value.status_code == 409
    assert box["calls"] == 0  # no SMS burnt on an obviously-insufficient pool


# ============================================================================
# Adversarial-pass fixes -- replay never re-mints, checked compensation,
# earn endpoint funds the pool, atomic OTP attempt ceiling
# ============================================================================


def test_router_duplicate_redeem_returns_same_voucher_never_remints(db, monkeypatch):
    """One debit -> exactly one voucher. With the OTP policy waived (the leak
    path), a retried redeem hits the debit dedupe (duplicate=True) -- the
    router must return the ORIGINAL voucher, never mint a second one."""
    r = _wire(monkeypatch, db)
    monkeypatch.setattr(r, "_require_otp", lambda user: False)
    _seed_customers(db, "C1", "C2")
    hid = _mk_household(db)
    assert svc.add_member(db, hid, "C2", actor=_manager())["ok"]
    svc.pool_earn(db, hid, 500, actor=_manager())

    body = r.RedeemBody(
        points=200, redeeming_customer_id="C2", otp_id="OTPX-1", otp_code=None
    )
    first = _run(r.redeem(hid, body, current_user=_cashier()))
    assert first["ok"] and first["voucher"]["voucher_id"]
    vouchers = db.get_collection("vouchers")
    assert len(vouchers.docs) == 1

    second = _run(r.redeem(hid, body, current_user=_cashier()))
    assert second["ok"] and second.get("duplicate") is True
    assert second["voucher"]["voucher_id"] == first["voucher"]["voucher_id"]
    assert len(vouchers.docs) == 1  # NO second voucher minted
    assert svc.pool_balance(db, hid) == 300  # NO second debit


def test_router_compensation_failure_is_loud_and_audited(db, monkeypatch):
    """If the voucher mint fails AND the reversal credit also fails, the route
    503s with a manual-credit flag and writes an audit row -- points never
    vanish silently."""
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    otp = _issue_otp(db, monkeypatch, household_id=hid, points=100)

    import api.routers.vouchers as vouchers_mod

    def _boom(*a, **k):
        raise RuntimeError("mint down")

    monkeypatch.setattr(vouchers_mod, "mint_voucher", _boom)

    class _Fail:
        ok = False
        reason = "unavailable"

    monkeypatch.setattr(mg, "credit", lambda *a, **k: _Fail())

    body = r.RedeemBody(
        points=100,
        redeeming_customer_id="C1",
        otp_id=otp["otp_id"],
        otp_code=otp["code"],
    )
    with pytest.raises(HTTPException) as exc:
        _run(r.redeem(hid, body, current_user=_cashier()))
    assert exc.value.status_code == 503
    assert "manual credit" in str(exc.value.detail)
    audits = [
        x
        for x in db.get_collection("audit_logs").docs
        if x.get("action") == "family_wallet.compensation_failed"
    ]
    assert audits and (audits[0].get("after_state") or {}).get("points") == 100


def test_earn_endpoint_funds_pool_idempotent_and_role_gated(db, monkeypatch):
    """The pool is fundable in production: POST /earn credits (manager+), a
    retried earn for the same order credits exactly once, floor staff 403."""
    from fastapi import HTTPException

    r = _wire(monkeypatch, db)
    _seed_customers(db, "C1")
    hid = _mk_household(db)

    body = r.EarnBody(points=250, source_order_id="ORD-1")
    out = _run(r.earn(hid, body, current_user=_manager()))
    assert out["ok"] and out["pool_balance_points"] == 250
    again = _run(r.earn(hid, body, current_user=_manager()))
    assert again["duplicate"] is True
    assert svc.pool_balance(db, hid) == 250  # exactly once per order ref

    with pytest.raises(HTTPException) as exc:
        _run(r.earn(hid, r.EarnBody(points=10), current_user=_cashier()))
    assert exc.value.status_code == 403


def test_otp_attempt_ceiling_is_atomic_in_filter(db, monkeypatch):
    """Wrong-guess bumps live IN the guarded filter: attempts can never exceed
    the budget, the max-th wrong guess flips FAILED exactly once, and the real
    code is refused after the budget is burned."""
    _seed_customers(db, "C1")
    hid = _mk_household(db)
    svc.pool_earn(db, hid, 500, actor=_manager())
    otp = _issue_otp(db, monkeypatch, household_id=hid, points=100)

    reasons = []
    for _ in range(rail.OTP_MAX_ATTEMPTS + 3):  # over-shoot on purpose
        out = rail.verify_pool_redemption_otp(db, otp_id=otp["otp_id"], code="BAD")
        assert out["ok"] is False
        reasons.append(out["reason"])
    assert "max_attempts" in reasons  # the flip happened exactly at budget
    assert reasons[-1] in ("failed", "max_attempts")  # post-flip calls refuse
    stored = db.get_collection("pool_otp").find_one({"otp_id": otp["otp_id"]})
    assert stored["status"] == "FAILED"
    assert int(stored["attempts"]) <= rail.OTP_MAX_ATTEMPTS
    # And the REAL code is now refused too (budget burned).
    final = rail.verify_pool_redemption_otp(db, otp_id=otp["otp_id"], code=otp["code"])
    assert final["ok"] is False
