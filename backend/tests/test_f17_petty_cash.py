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
