"""
IMS 2.0 - F17/#17 Petty cash controls (INTENT-LEVEL acceptance tests)
=====================================================================
These exercise the REAL petty_cash_service + the REAL E1 money_guard PETTY_CASH
account type + the REAL E4 ApprovalEngine + the REAL expenses router, against a
faithful in-memory fake Mongo (no network, no live mongod). A hollow shell that
skips the atomic float guard, the receipt gate, the E4 over-threshold routing, or
the reversal FAILS here.

CI-robustness (HARD lessons folded in):
  (1) The FakeDB faithfully models Mongo's MATCH-THEN-MODIFY atomicity (the debit
      floor lives in the find_one_and_update FILTER, so a racing second debit
      matches nothing once the first applied), $push, the positional
      `ledger.$.balance_after`, dotted-array equality, and a UNIQUE store_id index
      (E11000 on a second open). EVERY repo/db accessor the router reads is
      monkeypatched and EVERY guard doc the atomic op reads (petty_cash_floats,
      users-with-PIN, approval_requests, expenses) is SEEDED -- no reliance on a
      fail-soft fallback that diverges local (no Mongo) vs CI (real Mongo).
  (2) No secret/value-absence is asserted via a whole-JSON substring check.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user, hash_password  # noqa: E402
from api.services import money_guard as mg  # noqa: E402
from api.services import petty_cash_service as pcs  # noqa: E402
from api.services import approvals as appr  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo
# ============================================================================


def _has_dotted_eq(doc, dotted, value):
    """Mongo array-element match: 'ledger.txn_id' == v when ANY element of ledger
    has txn_id == v. Also handles a plain dotted scalar path."""
    arr_key, sub = dotted.split(".", 1)
    container = doc.get(arr_key)
    if isinstance(container, list):
        return any(isinstance(el, dict) and el.get(sub) == value for el in container)
    if isinstance(container, dict):
        cur = container
        for part in dotted.split(".")[1:]:
            if not isinstance(cur, dict):
                return False
            cur = cur.get(part)
        return cur == value
    return False


def _matches(doc, filt):
    for k, v in filt.items():
        if k == "$or":
            if not any(_matches(doc, clause) for clause in v):
                return False
        elif isinstance(v, dict):
            cur = doc.get(k)
            if "$gte" in v and (cur is None or cur < v["$gte"]):
                return False
            if "$lte" in v and (cur is None or cur > v["$lte"]):
                return False
            if "$gt" in v and (cur is None or not cur > v["$gt"]):
                return False
            if "$lt" in v and (cur is None or not cur < v["$lt"]):
                return False
            if "$ne" in v:
                if "." in k:
                    if _has_dotted_eq(doc, k, v["$ne"]):
                        return False
                elif cur == v["$ne"]:
                    return False
            if "$in" in v and cur not in v["$in"]:
                return False
        elif "." in k:
            if not _has_dotted_eq(doc, k, v):
                return False
        else:
            if doc.get(k) != v:
                return False
    return True


def _apply(doc, update):
    for op, block in update.items():
        if op == "$inc":
            for f, d in block.items():
                doc[f] = (doc.get(f) or 0) + d
        elif op == "$set":
            for f, val in block.items():
                if f.startswith("ledger.$."):
                    # Positional update on the ledger element matched by the filter.
                    # The filter carried ledger.txn_id == <id>; update that element.
                    sub = f.split(".$.", 1)[1]
                    # The matched element is identified by the pending positional
                    # txn_id stashed on the doc by find_one_and_update/update_one.
                    target = doc.get("__pos_txn__")
                    for el in doc.get("ledger") or []:
                        if isinstance(el, dict) and el.get("txn_id") == target:
                            el[sub] = val
                else:
                    doc[f] = val
        elif op == "$push":
            for f, val in block.items():
                doc.setdefault(f, []).append(val)
        elif op == "$unset":
            for f in block:
                doc.pop(f, None)
        elif op == "$setOnInsert":
            pass


class _UpdateResult:
    def __init__(self, matched, modified, upserted_id=None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id


class _DuplicateKeyError(Exception):
    pass


class FakeCollection:
    def __init__(self, name, database=None):
        self.name = name
        self.docs = []
        self.database = database
        self._unique_fields = set()
        self._n = 0

    # --- index (records uniqueness so a second open raises E11000) ----------
    def create_index(self, keys, unique=False, **kw):
        if unique:
            if isinstance(keys, str):
                self._unique_fields.add(keys)
            elif isinstance(keys, (list, tuple)) and keys and isinstance(keys[0], tuple):
                self._unique_fields.add(keys[0][0])
        return "idx"

    def _violates_unique(self, doc, skip=None):
        for f in self._unique_fields:
            val = doc.get(f)
            if val is None:
                continue
            for d in self.docs:
                if d is skip:
                    continue
                if d.get(f) == val:
                    return True
        return False

    def insert_one(self, doc):
        doc = dict(doc)
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key")
        doc.setdefault("_id", f"oid-{self.name}-{self._n}")
        self._n += 1
        self.docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, filt, projection=None):
        for d in self.docs:
            if _matches(d, filt):
                out = copy.deepcopy(d)
                out.pop("__pos_txn__", None)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    def _stash_positional(self, filt, d):
        # Record which ledger element a positional $set should target.
        for k, v in filt.items():
            if k == "ledger.txn_id" and not isinstance(v, dict):
                d["__pos_txn__"] = v

    def find_one_and_update(self, filt, update, return_document=None, upsert=False, **kw):
        for d in self.docs:
            if _matches(d, filt):
                self._stash_positional(filt, d)
                _apply(d, update)
                d.pop("__pos_txn__", None)
                out = copy.deepcopy(d)
                out.pop("__pos_txn__", None)
                return out
        return None

    def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if _matches(d, filt):
                self._stash_positional(filt, d)
                _apply(d, update)
                d.pop("__pos_txn__", None)
                return _UpdateResult(1, 1)
        return _UpdateResult(0, 0)

    def delete_one(self, filt):
        for i, d in enumerate(self.docs):
            if _matches(d, filt):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()


class FakeDB:
    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, database=self)
        return self._collections[name]

    def __getitem__(self, name):
        return self.get_collection(name)


class FakeExpenseRepo:
    """Mirrors the subset of ExpenseRepository the router uses."""

    def __init__(self, db):
        self.coll = db.get_collection("expenses")

    def find_by_id(self, expense_id):
        return self.coll.find_one({"expense_id": expense_id})

    def find_one(self, filt):
        return self.coll.find_one(filt)

    def find_many(self, filt, limit=0):
        return [d for d in self.coll.docs if _matches(d, filt)]

    def update(self, expense_id, fields):
        return self.coll.update_one({"expense_id": expense_id}, {"$set": fields})

    def update_if(self, expense_id, expected, fields, unset=None):
        update = {"$set": fields, **({"$unset": {f: "" for f in unset}} if unset else {})}
        res = self.coll.update_one({**expected, "expense_id": expense_id}, update)
        return bool(res.matched_count)

    def create(self, doc):
        self.coll.insert_one(doc)
        return doc


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def db():
    pcs.ensure_indexes  # noqa: B018 - ensure import is exercised
    d = FakeDB()
    d.get_collection("petty_cash_floats").create_index("store_id", unique=True)
    return d


def _open(db, store_id="S1", amount=5000.0, threshold=500.0, actor="mgr1"):
    return pcs.open_float(
        db, store_id=store_id, amount=amount, actor=actor,
        float_limit=10000.0, low_balance_threshold=threshold,
    )


def _audit_actions(db, store_id="S1"):
    return [r.get("action") for r in db.get_collection("audit_logs").docs
            if r.get("entity_id") == store_id and r.get("entity_type") == "petty_cash_float"]


def _ledger(db, store_id="S1"):
    doc = db.get_collection("petty_cash_floats").find_one({"store_id": store_id}) or {}
    return doc.get("ledger") or []


# ============================================================================
# T1 -- float debit is concurrency-safe (no double-spend)
# ============================================================================


def test_t1_concurrent_debit_no_double_spend(db):
    _open(db, amount=500.0, threshold=0.0)
    r1 = pcs.debit_float(db, store_id="S1", amount=500.0, expense_id="E1", actor="mgr1")
    r2 = pcs.debit_float(db, store_id="S1", amount=500.0, expense_id="E2", actor="mgr1")
    oks = [r1.get("ok"), r2.get("ok")]
    assert oks.count(True) == 1 and oks.count(False) == 1
    loser = r1 if not r1.get("ok") else r2
    assert loser.get("error") == "insufficient"
    # Final balance is exactly 0 -- the float never went negative.
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 0
    # Exactly one DEBIT ledger row (the winner only).
    debits = [r for r in _ledger(db) if r.get("type") == "DEBIT"]
    assert len(debits) == 1
    # Exactly one payout audit row.
    assert _audit_actions(db).count("petty_cash.payout") == 1


# ============================================================================
# T2 -- float cannot go below zero (over-spend rejected, nothing written)
# ============================================================================


def test_t2_float_cannot_go_negative(db):
    _open(db, amount=500.0)
    r = pcs.debit_float(db, store_id="S1", amount=600.0, expense_id="E1", actor="mgr1")
    assert r.get("ok") is False and r.get("error") == "insufficient"
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 500.0  # unchanged
    assert [r for r in _ledger(db) if r.get("type") == "DEBIT"] == []  # no debit row
    assert "petty_cash.payout" not in _audit_actions(db)  # no payout audit row


# ============================================================================
# T5 -- the debit IS a real money move (not a hollow stub)
# ============================================================================


def test_t5_debit_moves_money_before_expense_stamp(db):
    """The float balance is decremented by the guarded debit BEFORE (and
    independent of) the expense being stamped APPROVED. A hollow stub that
    skipped the debit would leave the balance untouched -> this asserts the real
    money move happened and is auditable even if a later expense write fails."""
    _open(db, amount=1000.0, threshold=0.0)
    r = pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="E1", actor="mgr1")
    assert r.get("ok") is True and r.get("balance") == 700.0
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 700.0  # money moved, recoverable via the audit DEBIT row
    payout = [x for x in db.get_collection("audit_logs").docs
              if x.get("action") == "petty_cash.payout"]
    assert len(payout) == 1
    assert payout[0]["after_state"]["expense_id"] == "E1"


# ============================================================================
# T6 -- low-balance alert fires only when crossing below the threshold
# ============================================================================


def test_t6_low_balance_alert(db, monkeypatch):
    fired = []

    def _fake_emit(store_id, balance, threshold):
        if balance < threshold:
            fired.append({"store_id": store_id, "balance": balance})
            return True
        return False

    monkeypatch.setattr(pcs, "_maybe_emit_low_balance", _fake_emit)
    _open(db, amount=600.0, threshold=500.0)
    # 600 - 200 = 400 < 500 -> fires.
    r = pcs.debit_float(db, store_id="S1", amount=200.0, expense_id="E1", actor="mgr1")
    assert r.get("ok") is True
    assert fired and fired[-1]["balance"] == 400.0
    assert r.get("is_low") is True


def test_t6b_no_alert_above_threshold(db, monkeypatch):
    fired = []
    monkeypatch.setattr(
        pcs, "_maybe_emit_low_balance",
        lambda s, b, t: (fired.append(b) or True) if b < t else False,
    )
    _open(db, amount=600.0, threshold=100.0)
    # 600 - 200 = 400 >= 100 -> no alert.
    r = pcs.debit_float(db, store_id="S1", amount=200.0, expense_id="E1", actor="mgr1")
    assert r.get("ok") is True and r.get("is_low") is False
    assert fired == []


# ============================================================================
# T7 -- only one float per store (double-open blocked, balance not doubled)
# ============================================================================


def test_t7_double_open_blocked(db):
    r1 = _open(db, amount=5000.0)
    assert r1.get("ok") is True and r1.get("balance") == 5000.0
    r2 = _open(db, amount=5000.0)
    assert r2.get("ok") is False and r2.get("http") == 409
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 5000.0  # NOT doubled


# ============================================================================
# T8 -- reversal on rejection restores the float exactly
# ============================================================================


def test_t8_reversal_restores_float(db):
    _open(db, amount=1000.0, threshold=0.0)
    debit = pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="E1", actor="mgr1")
    assert debit.get("ok") is True
    txn_id = debit.get("txn_id")
    bal_after_debit = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal_after_debit == 700.0

    rev = pcs.reverse_payout(db, store_id="S1", txn_id=txn_id, actor="admin1")
    assert rev.get("ok") is True
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 1000.0  # restored by the exact original amount
    # A CREDIT reversal ledger row exists, linked to the reversed txn.
    credits = [r for r in _ledger(db) if r.get("type") == "CREDIT" and r.get("reverses") == txn_id]
    assert len(credits) == 1
    assert "petty_cash.reversal" in _audit_actions(db)

    # Idempotent: a second reversal of the same txn does NOT double-credit.
    rev2 = pcs.reverse_payout(db, store_id="S1", txn_id=txn_id, actor="admin1")
    assert rev2.get("ok") is False and rev2.get("error") == "already_reversed"
    bal2 = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal2 == 1000.0


# ============================================================================
# Router-level tests (T3, T4, T9, T10) -- real expenses router + E4 + money_guard
# ============================================================================


def _client(db, repo, roles, *, user_id="u1", store_ids=("S1",), active="S1"):
    """Mount the expenses router with the auth + db + repo accessors monkeypatched
    to the shared FakeDB. EVERY accessor the router/service touches is patched so
    there is no local-vs-CI fail-soft divergence."""
    app = FastAPI()
    app.include_router(expenses.router, prefix="/api/v1/expenses")

    async def _fake_user():
        return {
            "user_id": user_id,
            "full_name": "Tester",
            "active_store_id": active,
            "store_ids": list(store_ids),
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


@pytest.fixture
def wired(db, monkeypatch):
    """Patch every db/repo accessor the expenses router + petty_cash_service +
    approvals read, all pointed at the SAME FakeDB."""
    repo = FakeExpenseRepo(db)
    monkeypatch.setattr(expenses, "get_db", lambda: db)
    monkeypatch.setattr(expenses, "get_expense_repository", lambda: repo)
    monkeypatch.setattr(expenses, "get_advance_repository", lambda: None)
    # money_guard / approvals resolve the audit repo lazily via dependencies.
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None, raising=False)
    return db, repo


def _seed_expense(repo, *, expense_id, amount, employee_id="emp1", store_id="S1",
                  category="PETTY_CASH", bill=None, status="PENDING"):
    doc = {
        "expense_id": expense_id,
        "employee_id": employee_id,
        "store_id": store_id,
        "category": category,
        "amount": amount,
        "description": "tea + courier",
        "expense_date": "2026-05-15",
        "status": status,
    }
    if bill:
        doc["bill_file_id"] = bill
    repo.create(doc)
    return doc


def test_t3_receipt_required_above_200(wired):
    db, repo = wired
    _open(db, amount=5000.0)
    _seed_expense(repo, expense_id="E1", amount=250.0, bill=None)
    client = _client(db, repo, ["STORE_MANAGER"])
    resp = client.post("/api/v1/expenses/E1/approve")
    assert resp.status_code == 400
    assert "Receipt required" in str(resp.json().get("detail"))
    # The float is untouched.
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 5000.0


def test_t4_no_receipt_required_at_or_below_200(wired):
    db, repo = wired
    _open(db, amount=5000.0)
    _seed_expense(repo, expense_id="E1", amount=150.0, bill=None)
    client = _client(db, repo, ["STORE_MANAGER"])
    resp = client.post("/api/v1/expenses/E1/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The float was debited Rs 150 and the txn stamped on the expense.
    assert body.get("float_balance") == 4850.0
    exp = repo.find_by_id("E1")
    assert exp.get("status") == "APPROVED"
    assert exp.get("petty_cash_txn_id")


def test_t9_store_rbac_topup_other_store_blocked(wired):
    db, repo = wired
    _open(db, store_id="B", amount=1000.0)
    # A STORE_MANAGER scoped to store A cannot topup store B.
    client_a = _client(db, repo, ["STORE_MANAGER"], store_ids=("A",), active="A")
    resp = client_a.post("/api/v1/expenses/petty-cash/topup",
                         json={"store_id": "B", "amount": 500.0})
    assert resp.status_code == 403
    # An ADMIN succeeds for any store.
    client_admin = _client(db, repo, ["ADMIN"], store_ids=(), active=None)
    resp2 = client_admin.post("/api/v1/expenses/petty-cash/topup",
                              json={"store_id": "B", "amount": 500.0})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json().get("balance") == 1500.0


def test_t10_over_threshold_routes_through_e4(wired, monkeypatch):
    db, repo = wired
    _open(db, amount=5000.0)
    # An Rs 800 petty-cash claim (> default Rs 500 auto-threshold) with a receipt.
    _seed_expense(repo, expense_id="E1", amount=800.0, bill="file-123")
    # A different maker submits the expense; the manager approves it.
    repo.update("E1", {"employee_id": "emp1"})
    client = _client(db, repo, ["STORE_MANAGER"], user_id="mgr1")

    # First approve: opens an E4 approval request -> 202, NO debit yet.
    resp = client.post("/api/v1/expenses/E1/approve")
    assert resp.status_code == 202, resp.text
    detail = resp.json().get("detail")
    request_id = detail.get("request_id")
    assert request_id
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 5000.0  # unchanged -- no debit before approval

    # An ADMIN with a PIN approves the E4 request (the real E4 engine, PIN-gated).
    admin = db.get_collection("users")
    admin.insert_one({
        "user_id": "admin1", "roles": ["ADMIN"],
        "approval_pin_hash": hash_password("1234"),
        "pin_attempts": {"count": 0, "window_start": appr._now()},
    })
    eng = appr.ApprovalEngine(db=db)
    appr_res = eng.approve(request_id, approver_user_id="admin1",
                           approver_roles=["ADMIN"], pin="1234")
    assert appr_res.get("ok") is True, appr_res

    # Second approve (by the maker): consumes the approval + debits the float.
    resp2 = client.post("/api/v1/expenses/E1/approve")
    assert resp2.status_code == 200, resp2.text
    bal2 = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal2 == 4200.0  # 5000 - 800, debited only after E4 approval
    exp = repo.find_by_id("E1")
    assert exp.get("status") == "APPROVED" and exp.get("petty_cash_txn_id")


# ============================================================================
# Extra: money_guard registered PETTY_CASH live (R1), not greenfield
# ============================================================================


def test_petty_cash_account_type_is_live(db):
    """CORRECTIONS R1: PETTY_CASH is a LIVE money_guard type on its own
    petty_cash_floats collection -- not the cancelled money_accounts SoR, not
    greenfield. The atomic guard is the float floor."""
    spec = mg.ACCOUNT_TYPES["PETTY_CASH"]
    assert spec.greenfield is False
    assert spec.coll == "petty_cash_floats"
    assert spec.key_field == "store_id"
    assert spec.status_field == "status"


# ============================================================================
# Adversarial P1 regressions: cross-store IDOR, maker-checker, idempotency
# ============================================================================


def test_t11_cross_store_approve_blocked(wired):
    """P1 (adversarial): a petty-cash approval DEBITS the expense's store float, so a
    non-HQ approver scoped to another store must be 403'd -- without this a store-A
    manager could drain store B's float via its expense_id (cross-store IDOR). HQ
    (ADMIN) may approve any store."""
    db, repo = wired
    _open(db, store_id="B", amount=5000.0)
    _seed_expense(repo, expense_id="E1", amount=150.0, store_id="B", bill=None)
    client_a = _client(db, repo, ["STORE_MANAGER"], user_id="mgrA",
                       store_ids=("A",), active="A")
    resp = client_a.post("/api/v1/expenses/E1/approve")
    assert resp.status_code == 403
    assert db.get_collection("petty_cash_floats").find_one({"store_id": "B"})["balance"] == 5000.0
    client_admin = _client(db, repo, ["ADMIN"], user_id="adm", store_ids=(), active=None)
    resp2 = client_admin.post("/api/v1/expenses/E1/approve")
    assert resp2.status_code == 200, resp2.text
    assert db.get_collection("petty_cash_floats").find_one({"store_id": "B"})["balance"] == 4850.0


def test_t12_maker_cannot_self_approve_petty_e4(wired):
    """P1 (adversarial): petty_cash is now a MAKER_CHECKER action -- the manager who
    OPENS the over-threshold E4 request cannot also PIN-approve it (real two-person
    control). mgr1 is given ADMIN here so the ONLY rejection reason is the maker==
    approver bar, not a tier/role failure."""
    db, repo = wired
    _open(db, amount=5000.0)
    _seed_expense(repo, expense_id="E1", amount=800.0, employee_id="emp1", bill="file-1")
    client = _client(db, repo, ["STORE_MANAGER"], user_id="mgr1")
    resp = client.post("/api/v1/expenses/E1/approve")  # opens the E4 request
    assert resp.status_code == 202, resp.text
    request_id = resp.json()["detail"]["request_id"]
    db.get_collection("users").insert_one({
        "user_id": "mgr1", "roles": ["STORE_MANAGER", "ADMIN"],
        "approval_pin_hash": hash_password("1234"),
        "pin_attempts": {"count": 0, "window_start": appr._now()},
    })
    eng = appr.ApprovalEngine(db=db)
    self_appr = eng.approve(request_id, approver_user_id="mgr1",
                            approver_roles=["ADMIN"], pin="1234")
    assert self_appr.get("ok") is False
    assert self_appr.get("error") == "cannot_approve_own"


def test_t13_idempotent_debit_no_double(db):
    """P1 (adversarial): two debits for the SAME expense_id dedupe via the money_guard
    idempotency_key -- the float moves EXACTLY once, so a concurrent / retried
    /approve cannot double-debit the same expense (the atomic floor caps loss; the
    idempotency key makes it exactly-once)."""
    _open(db, amount=5000.0)
    r1 = pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="E1", actor="mgr1")
    assert r1["ok"] is True and r1["balance"] == 4700.0
    r2 = pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="E1", actor="mgr1")
    assert r2["ok"] is False and r2["error"] == "duplicate"
    assert db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"] == 4700.0


# ============================================================================
# T14 -- float TOP-UP ceiling is concurrency-safe (atomic, not TOCTOU)
# ============================================================================


def test_t14_topup_ceiling_is_atomic(db):
    """Two top-ups whose SUM would breach the float_limit: the atomic ceiling
    guard (money_guard.credit guard_extra: balance <= limit-amt merged into the
    CAS filter) lets EXACTLY ONE win, so the balance can never exceed the limit
    even when both racers read the same pre-credit balance. Previously the
    read-then-check in topup_float was TOCTOU-racy and both could apply."""
    # _open sets float_limit=10000. Start at 9600; two +300 top-ups would reach
    # 10200 > 10000 -- only the first may apply.
    _open(db, amount=9600.0, threshold=0.0)
    r1 = pcs.topup_float(db, store_id="S1", amount=300.0, actor="mgr1")
    r2 = pcs.topup_float(db, store_id="S1", amount=300.0, actor="mgr1")
    oks = [r1.get("ok"), r2.get("ok")]
    assert oks.count(True) == 1 and oks.count(False) == 1
    loser = r1 if not r1.get("ok") else r2
    assert loser.get("http") == 422
    assert loser.get("error") == "exceeds_float_limit"
    # Balance is exactly 9900 (9600 + 300), NEVER 10200.
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 9900.0


def test_t15_topup_within_limit_succeeds(db):
    """A normal top-up with headroom still succeeds (guard_extra is a no-op when
    balance + amount <= float_limit)."""
    _open(db, amount=5000.0, threshold=0.0)
    r = pcs.topup_float(db, store_id="S1", amount=1000.0, actor="mgr1")
    assert r.get("ok") is True
    assert r.get("balance") == 6000.0
    bal = db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]
    assert bal == 6000.0


def test_t16_topup_not_found_after_float_deleted_is_not_ceiling(db, monkeypatch):
    """A concurrent float close/delete makes the guarded credit return
    reason='not_found'. topup_float must NOT mislabel that as exceeds_float_limit
    -- a re-read distinguishes the vanished doc (404 float_not_open) from a real
    ceiling breach (422)."""
    _open(db, amount=5000.0, threshold=0.0)
    coll = db.get_collection("petty_cash_floats")

    def _fake_credit(*a, **k):
        # Simulate the float being deleted between the existence check and the
        # guarded write, then the write matching nothing.
        coll.delete_one({"store_id": "S1"})
        return mg.GuardResult(ok=False, reason="not_found")

    monkeypatch.setattr(pcs.mg, "credit", _fake_credit)
    r = pcs.topup_float(db, store_id="S1", amount=100.0, actor="mgr1")
    assert r.get("ok") is False
    assert r.get("http") == 404
    assert r.get("error") == "float_not_open"


# ============================================================================
# E4-4 -- an approval lands only while the expense is still PENDING
# ============================================================================


def _stale_once(repo, monkeypatch, snap):
    """The next request read the expense as `snap` (it read before a
    colleague's decision landed); every later read sees the store as it is."""
    real, left = repo.find_by_id, [1]

    def read(_id):
        if left[0]:
            left[0] = 0
            return dict(snap)
        return real(_id)

    monkeypatch.setattr(repo, "find_by_id", read)


def _approve(db, repo, user):
    return _client(db, repo, ["STORE_MANAGER"], user_id=user).post("/api/v1/expenses/E1/approve")


def _reject(db, repo, user):
    return _client(db, repo, ["STORE_MANAGER"], user_id=user).post(
        "/api/v1/expenses/E1/reject", params={"reason": "not ours"})


def _float(db):
    return db.get_collection("petty_cash_floats").find_one({"store_id": "S1"})["balance"]


@pytest.fixture
def claim(wired):
    db, repo = wired
    _open(db, amount=5000.0)
    _seed_expense(repo, expense_id="E1", amount=300.0, bill="file-1")
    return db, repo, repo.find_by_id("E1")


def test_double_approve_of_a_petty_claim_debits_once(claim, monkeypatch):
    db, repo, pending = claim
    assert _approve(db, repo, "mgr1").status_code == 200
    _stale_once(repo, monkeypatch, pending)
    second = _approve(db, repo, "mgr2")
    assert second.status_code == 409 and isinstance(second.json()["detail"], str)
    assert _float(db) == 4700.0
    assert [r["type"] for r in _ledger(db)].count("DEBIT") == 1
    assert repo.coll.find_one({"expense_id": "E1"})["approved_by"] == "mgr1"


def test_double_approve_of_an_ordinary_expense_is_one_approval(wired, monkeypatch):
    db, repo = wired
    _seed_expense(repo, expense_id="E2", amount=900.0, category="TRAVEL")
    pending = repo.find_by_id("E2")
    first = _client(db, repo, ["STORE_MANAGER"], user_id="mgr1").post("/api/v1/expenses/E2/approve")
    _stale_once(repo, monkeypatch, pending)
    second = _client(db, repo, ["STORE_MANAGER"], user_id="mgr2").post("/api/v1/expenses/E2/approve")
    assert first.status_code == 200, first.text
    assert second.status_code == 409 and isinstance(second.json()["detail"], str)
    assert repo.coll.find_one({"expense_id": "E2"})["approved_by"] == "mgr1"


def test_an_approval_that_loses_to_a_reject_hands_the_payout_back(claim, monkeypatch, caplog):
    """A raw REJECTED write lands between the float debit and the APPROVED
    stamp: the approval hands its payout back, and logs that it did."""
    db, repo, _ = claim
    real = expenses._do_petty_debit

    def debit_then_rejected(*a, **k):
        out = real(*a, **k)
        repo.update("E1", {"status": "REJECTED", "rejected_by": "mgr3"})
        return out

    monkeypatch.setattr(expenses, "_do_petty_debit", debit_then_rejected)
    resp = _approve(db, repo, "mgr1")
    assert resp.status_code == 409, resp.text
    doc = repo.find_by_id("E1")
    assert doc["status"] == "REJECTED" and "approved_by" not in doc
    assert _float(db) == 5000.0
    assert "hand-back: done" in caplog.text


# B1 -- a reject racing an approval: exactly one wins, and money follows it.


def test_a_reject_that_read_pending_loses_to_the_approval(claim, monkeypatch):
    db, repo, pending = claim
    assert _approve(db, repo, "mgr1").status_code == 200
    _stale_once(repo, monkeypatch, pending)
    lost = _reject(db, repo, "mgr2")
    assert lost.status_code == 409 and isinstance(lost.json()["detail"], str)
    doc = repo.coll.find_one({"expense_id": "E1"})
    assert (doc["status"], doc["approved_by"]) == ("APPROVED", "mgr1")
    assert _float(db) == 4700.0  # paid once, and the payout stands with the approval


def test_a_reject_that_wins_first_leaves_no_debit(claim, monkeypatch):
    db, repo, pending = claim
    assert _reject(db, repo, "mgr2").status_code == 200
    _stale_once(repo, monkeypatch, pending)  # the approver read it still PENDING
    assert _approve(db, repo, "mgr1").status_code == 409
    assert repo.coll.find_one({"expense_id": "E1"})["status"] == "REJECTED"
    assert _float(db) == 5000.0
    assert "DEBIT" not in [r["type"] for r in _ledger(db)]  # the claim failed: no money moved


def test_a_reject_before_the_payout_strands_nothing_even_if_the_approver_dies(claim, monkeypatch):
    """The approver read PENDING; a reject lands; the approver's worker is
    then killed right after paying out. The claim (PENDING -> APPROVING) comes
    before the payout, so it fails on the REJECTED expense and nothing is paid."""
    db, repo, _ = claim
    real_live, real_debit, fired = pcs.live_payout_for, expenses._do_petty_debit, []

    def reject_lands(*a, **k):
        if not fired:
            fired.append(None)  # once: the reject's own read must not re-fire it
            fired[0] = _reject(db, repo, "mgr2")
        return real_live(*a, **k)

    def debit_then_killed(*a, **k):
        real_debit(*a, **k)
        raise RuntimeError("worker killed")

    monkeypatch.setattr(pcs, "live_payout_for", reject_lands)
    monkeypatch.setattr(expenses, "_do_petty_debit", debit_then_killed)
    try:
        _approve(db, repo, "mgr1")
    except RuntimeError:
        pass
    assert fired[0].status_code == 200
    assert repo.coll.find_one({"expense_id": "E1"})["status"] == "REJECTED"
    assert _float(db) == 5000.0 and "DEBIT" not in [r["type"] for r in _ledger(db)]


def _stamp_fails_once(repo, monkeypatch):
    """The APPROVED stamp write fails once (the claim write goes through)."""
    real, left = repo.update_if, [1]

    def update_if(expense_id, expected, fields, *a, **k):
        if left[0] and fields.get("status") == "APPROVED":
            left[0] = 0
            raise RuntimeError("write timed out")
        return real(expense_id, expected, fields, *a, **k)

    monkeypatch.setattr(repo, "update_if", update_if)


def test_approving_again_finishes_an_approval_whose_stamp_failed(claim, monkeypatch):
    db, repo, _ = claim
    _stamp_fails_once(repo, monkeypatch)
    cut = _approve(db, repo, "mgr1")
    assert cut.status_code == 503 and "Approve again" in cut.json()["detail"]
    assert _float(db) == 4700.0 and repo.find_by_id("E1")["status"] == "APPROVING"
    done = _approve(db, repo, "mgr1")
    assert done.status_code == 200, done.text
    doc = repo.find_by_id("E1")
    assert doc["status"] == "APPROVED" and doc["petty_cash_txn_id"] == _ledger(db)[-1]["txn_id"]
    assert "approving_claim" not in doc
    assert _float(db) == 4700.0 and [r["type"] for r in _ledger(db)].count("DEBIT") == 1


def test_a_reject_cannot_land_while_an_approval_holds_its_payout(claim, monkeypatch):
    """A worker cut off after the payout leaves APPROVING with the money paid
    out. A reject is refused (it would strand the payout on a REJECTED
    expense); the next approval finishes on that payout."""
    db, repo, _ = claim
    _stamp_fails_once(repo, monkeypatch)
    assert _approve(db, repo, "mgr1").status_code == 503
    refused = _reject(db, repo, "mgr2")
    assert refused.status_code == 409 and "being approved" in refused.json()["detail"]
    assert repo.find_by_id("E1")["status"] == "APPROVING" and _float(db) == 4700.0
    assert _approve(db, repo, "mgr2").status_code == 200
    assert repo.find_by_id("E1")["status"] == "APPROVED" and _float(db) == 4700.0


@pytest.mark.parametrize("age_seconds,code,balance", [(5, 409, 5000.0), (600, 200, 4700.0)])
def test_a_claim_with_no_payout_is_taken_over_only_once_stale(claim, age_seconds, code, balance):
    """A worker that claimed and died before paying out: a fresh claim makes
    the next approval wait; a stale one is taken over and approved."""
    from datetime import datetime, timedelta, timezone

    db, repo, _ = claim
    at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    repo.update("E1", {"status": "APPROVING", "approving_claim": "dead", "approving_by": "mgr9", "approving_at": at})
    assert _approve(db, repo, "mgr1").status_code == code
    assert _float(db) == balance


def test_a_claim_lets_go_when_no_money_moves(claim):
    """An empty float refuses the payout: the claim goes back to PENDING."""
    db, repo, _ = claim
    db.get_collection("petty_cash_floats").update_one({"store_id": "S1"}, {"$set": {"balance": 100.0}})
    assert _approve(db, repo, "mgr1").status_code == 409
    doc = repo.find_by_id("E1")
    assert doc["status"] == "PENDING" and "approving_claim" not in doc


@pytest.mark.parametrize("landed,status,balance", [(True, "REJECTED", 5000.0), (False, "APPROVED", 4700.0)])
def test_a_failed_reversal_restores_approved_only_while_the_payout_stands(claim, monkeypatch, landed, status, balance):
    db, repo, _ = claim
    assert _approve(db, repo, "mgr1").status_code == 200
    real = pcs.reverse_payout

    def reports_failure(*a, **k):
        if landed:
            real(*a, **k)  # the hand-back happened, but its answer was lost
        return {"ok": False, "http": 503, "error": "timeout"}

    monkeypatch.setattr(pcs, "reverse_payout", reports_failure)
    _reject(db, repo, "mgr2")
    doc = repo.find_by_id("E1")
    assert doc["status"] == status and _float(db) == balance
    if status == "APPROVED":
        assert "rejected_by" not in doc


def test_a_colleague_who_finishes_the_approval_keeps_the_payout(claim, monkeypatch):
    """mgr2 approves (finishing on mgr1's payout) between mgr1's debit and
    stamp: mgr1's stamp loses, and must not hand back the payout mgr2 used."""
    db, repo, _ = claim
    real = expenses._do_petty_debit
    seen = []

    def debit_then_colleague(*a, **k):
        out = real(*a, **k)
        seen.append(_approve(db, repo, "mgr2"))
        return out

    monkeypatch.setattr(expenses, "_do_petty_debit", debit_then_colleague)
    assert _approve(db, repo, "mgr1").status_code == 409
    assert seen[0].status_code == 200, seen[0].text
    doc = repo.find_by_id("E1")
    assert doc["status"] == "APPROVED" and doc["approved_by"] == "mgr2"
    assert _float(db) == 4700.0 and [r["type"] for r in _ledger(db)].count("DEBIT") == 1


# --- review round 4 -----------------------------------------------------------


def _claim_age(repo, seconds, claim="dead"):
    from datetime import datetime, timedelta, timezone

    at = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    repo.update("E1", {"status": "APPROVING", "approving_claim": claim, "approving_by": "mgr9", "approving_at": at})


def test_the_approval_queue_keeps_an_approval_that_was_cut_off(claim):
    """A 503 after the claim, or a worker gone before or after paying out,
    leaves APPROVING: it stays in the queue, where Approve finishes it."""
    db, repo, _ = claim
    _seed_expense(repo, expense_id="E9", amount=120.0)
    _claim_age(repo, 5)
    queue = _client(db, repo, ["STORE_MANAGER"], user_id="mgr2").get("/api/v1/expenses/pending-approval")
    assert queue.status_code == 200, queue.text
    assert {e["expense_id"]: e["status"] for e in queue.json()["expenses"]} == {"E1": "APPROVING", "E9": "PENDING"}


def _payout_live(db):
    ledger = _ledger(db)
    undone = {r.get("reverses") for r in ledger}
    return [r for r in ledger if r["type"] == "DEBIT" and r["txn_id"] not in undone]


def test_a_colleague_still_holding_the_claim_keeps_the_payout(claim, monkeypatch):
    """mgr2 took the claim over (it had paid out) and has not stamped yet when
    mgr1's own stamp loses: mgr1 must not hand back the payout mgr2 is
    finishing on."""
    db, repo, _ = claim
    real = expenses._do_petty_debit

    def debit_then_taken_over(*a, **k):
        out = real(*a, **k)
        repo.update("E1", {"approving_claim": "mgr2-claim", "approving_by": "mgr2"})
        return out

    monkeypatch.setattr(expenses, "_do_petty_debit", debit_then_taken_over)
    assert _approve(db, repo, "mgr1").status_code == 409
    assert repo.find_by_id("E1")["status"] == "APPROVING"
    assert _float(db) == 4700.0 and len(_payout_live(db)) == 1
    monkeypatch.setattr(expenses, "_do_petty_debit", real)
    _claim_age(repo, 5, claim="mgr2-claim")
    assert _approve(db, repo, "mgr2").status_code == 200  # finishes on the live payout
    assert repo.find_by_id("E1")["status"] == "APPROVED" and _float(db) == 4700.0


def test_an_expense_released_to_pending_keeps_its_live_payout(claim, monkeypatch):
    """A colleague's takeover found the payout already made and let go (back to
    PENDING) before mgr1's stamp: mgr1 must not hand it back; the next approval
    finishes on it."""
    db, repo, _ = claim
    real = expenses._do_petty_debit

    def debit_then_released(*a, **k):
        out = real(*a, **k)
        repo.update("E1", {"status": "PENDING", "approving_claim": None})
        return out

    monkeypatch.setattr(expenses, "_do_petty_debit", debit_then_released)
    assert _approve(db, repo, "mgr1").status_code == 409
    assert _float(db) == 4700.0 and len(_payout_live(db)) == 1
    monkeypatch.setattr(expenses, "_do_petty_debit", real)
    assert _approve(db, repo, "mgr2").status_code == 200
    assert repo.find_by_id("E1")["status"] == "APPROVED" and _float(db) == 4700.0
    assert [r["type"] for r in _ledger(db)].count("DEBIT") == 1


@pytest.mark.parametrize("age,paid,code,status", [
    (600, False, 200, "REJECTED"),   # abandoned before any money moved: the reject takes it
    (5, False, 409, "APPROVING"),    # a live approval may be paying out right now
    (600, True, 409, "APPROVING"),   # paid out: the next approval finishes it
])
def test_a_reject_takes_over_only_a_stale_claim_with_no_payout(claim, age, paid, code, status):
    db, repo, _ = claim
    if paid:
        assert pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="E1", actor="mgr9")["ok"]
    _claim_age(repo, age)
    assert _reject(db, repo, "mgr2").status_code == code
    doc = repo.find_by_id("E1")
    assert doc["status"] == status
    if status == "REJECTED":
        assert "approving_claim" not in doc and _float(db) == 5000.0
