"""E1 money-guard -- INTENT-LEVEL acceptance tests (packet E1, Phase A).

These assert the INTENDED behavior of the unified guard, not mere existence: a
pass-through that ignored the engine would fail T1/T4/T9/T13. The fakes model
Mongo's match-then-modify atomicity (the guard lives in the filter, so by the
time a racing second op runs the document no longer satisfies the guard and it
matches nothing) -- the same modelling the existing test_money_integrity_guards
fakes use.

Scope note (CORRECTIONS P0-1, binding, outranks the E1 packet): Phase A is a
FACADE over the existing vouchers/loyalty_accounts/customers collections. NO
money_accounts SoR collection / index / migration is built here. The 3 new
account types (PETTY_CASH/FAMILY_WALLET/CONSIGNMENT) are deferred and return
reason="unavailable". The packet's T13 (a greenfield money_accounts WRITE
succeeding) is therefore deliberately deferred to a future Phase-2+ packet; this
file's T13 instead asserts the Phase-A deferral contract. T6 (existing suite
passes unchanged) is covered by backend/tests/test_money_integrity_guards.py.
T14 (cost+10% floor) is orders.py, not E1 -- out of scope by the packet.

No emoji (Windows cp1252).
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

from api.services import money_guard as mg  # noqa: E402


# ---------------------------------------------------------------------------
# Mongo-faithful fakes (match-then-modify atomicity)
# ---------------------------------------------------------------------------


def _has_dotted(doc, dotted, value):
    """Mongo array-element match: 'money_ledger.idempotency_key' == value when ANY
    element of money_ledger has idempotency_key == value."""
    arr_key, sub = dotted.split(".", 1)
    for el in (doc.get(arr_key) or []):
        if isinstance(el, dict) and el.get(sub) == value:
            return True
    return False


class _UpdateResult:
    def __init__(self, matched, modified):
        self.matched_count = matched
        self.modified_count = modified


class _FakeColl:
    """Supports find_one / find_one_and_update / update_one with $gte/$lte/$ne/$or,
    dotted-array eq, and $inc/$set/$push."""

    def __init__(self, docs=None, key_field="customer_id"):
        self.key_field = key_field
        self.docs = [copy.deepcopy(d) for d in (docs or [])]
        self.database = None

    def _match(self, doc, filt):
        for k, v in filt.items():
            if k == "$or":
                if not any(self._match(doc, clause) for clause in v):
                    return False
            elif isinstance(v, dict):
                if "$gte" in v:
                    cur = doc.get(k)
                    if cur is None or cur < v["$gte"]:
                        return False
                if "$lte" in v:
                    cur = doc.get(k)
                    if cur is None or cur > v["$lte"]:
                        return False
                if "$ne" in v:
                    if "." in k:
                        if _has_dotted(doc, k, v["$ne"]):
                            return False
                    elif doc.get(k) == v["$ne"]:
                        return False
            elif "." in k:
                if not _has_dotted(doc, k, v):
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def _apply(self, doc, update):
        for op, block in update.items():
            if op == "$inc":
                for f, d in block.items():
                    doc[f] = (doc.get(f) or 0) + d
            elif op == "$set":
                for f, val in block.items():
                    doc[f] = val
            elif op == "$push":
                for f, val in block.items():
                    doc.setdefault(f, []).append(val)

    def find_one(self, filt, *a, **k):
        for d in self.docs:
            if self._match(d, filt):
                return copy.deepcopy(d)
        return None

    def find_one_and_update(self, filt, update, return_document=None):
        for d in self.docs:
            if self._match(d, filt):
                self._apply(d, update)
                return copy.deepcopy(d)
        return None

    def update_one(self, filt, update):
        for d in self.docs:
            if self._match(d, filt):
                self._apply(d, update)
                return _UpdateResult(1, 1)
        return _UpdateResult(0, 0)


class _NoAtomicColl:
    """A minimal stand-in with NO find_one_and_update / update_one (T8)."""

    def __init__(self, docs):
        self.docs = [copy.deepcopy(d) for d in docs]

    def find_one(self, filt, *a, **k):
        for d in self.docs:
            if all(d.get(x) == y for x, y in filt.items() if not isinstance(y, dict)):
                return copy.deepcopy(d)
        return None


class _PymongoLikeColl(_FakeColl):
    """Emulates a real pymongo Collection: __getattr__ synthesizes a sub-collection
    for ANY missing attribute name (so instance hasattr(coll, 'get_collection') is
    deceptively True). This is the exact trap that broke _resolve_collection."""

    def __getattr__(self, name):
        return _PymongoLikeColl([], key_field=self.key_field)


class _FakeAuditRepo:
    def __init__(self):
        self.rows = []

    def create(self, data):
        self.rows.append(copy.deepcopy(data))
        return data


class _RaisingAuditRepo:
    def create(self, data):
        raise RuntimeError("audit backend down")


@pytest.fixture
def audit(monkeypatch):
    repo = _FakeAuditRepo()
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: repo, raising=False)
    return repo


# ---------------------------------------------------------------------------
# T1 -- no double-spend under race (the P0 contract)
# ---------------------------------------------------------------------------


def test_t1_no_double_spend_under_race(audit):
    coll = _FakeColl([{"customer_id": "C", "balance_points": 100}], key_field="customer_id")
    r1 = mg.debit(coll, "LOYALTY", "C", 100, reason="redeem")
    r2 = mg.debit(coll, "LOYALTY", "C", 100, reason="redeem")
    oks = [r1.ok, r2.ok]
    assert oks.count(True) == 1 and oks.count(False) == 1
    loser = r1 if not r1.ok else r2
    assert loser.reason == "insufficient"
    assert coll.find_one({"customer_id": "C"})["balance_points"] == 0
    # Exactly one money.debit audit row -- the winner only; the loser writes nothing
    # (filter on action too, so a stray failure-audit under another reason is caught).
    debit_rows = [x for x in audit.rows if x["entity_id"] == "C" and x["action"] == "money.debit"]
    assert len(debit_rows) == 1


def test_t1b_resolves_real_collection_not_subcollection(audit):
    """Regression: a real pymongo Collection's __getattr__ makes instance
    hasattr(coll,'get_collection') True. The engine must still target the ORIGINAL
    collection (class-level discriminator), not an empty synthesized sub-collection,
    else every live voucher/loyalty/store-credit spend fails-closed in prod."""
    coll = _PymongoLikeColl([{"customer_id": "C", "balance_points": 500}], key_field="customer_id")
    r = mg.debit(coll, "LOYALTY", "C", 200, reason="redeem")
    assert r.ok is True and r.balance == 300
    assert coll.find_one({"customer_id": "C"})["balance_points"] == 300  # ORIGINAL coll mutated


# ---------------------------------------------------------------------------
# T2 -- floor is hard (no negative balance for guarded types)
# ---------------------------------------------------------------------------


def test_t2_floor_is_hard(audit):
    coll = _FakeColl([{"customer_id": "C", "balance_points": 100}])
    r = mg.debit(coll, "LOYALTY", "C", 101, reason="redeem")
    assert r.ok is False and r.reason == "insufficient"
    assert coll.find_one({"customer_id": "C"})["balance_points"] == 100
    assert audit.rows == []  # failure writes no audit / ledger


# ---------------------------------------------------------------------------
# T3 -- credit is unconditional and paisa-exact
# ---------------------------------------------------------------------------


def test_t3_credit_paisa_exact(audit):
    coll = _FakeColl([{"customer_id": "C", "store_credit": 0.0}])
    r = mg.credit(coll, "STORE_CREDIT", "C", 250.55, reason="note")
    assert r.ok is True
    assert mg.get_balance(coll, "STORE_CREDIT", "C")["balance"] == 250.55
    doc = coll.find_one({"customer_id": "C"})
    ledger = [e for e in doc.get("money_ledger", []) if e["type"] == "CREDIT"]
    assert len(ledger) == 1
    assert round(ledger[0]["balance_after"], 2) == 250.55


# ---------------------------------------------------------------------------
# T4 -- idempotency key deduplicates retried mutations
# ---------------------------------------------------------------------------


def test_t4_idempotency_dedup(audit):
    coll = _FakeColl([{"customer_id": "C", "store_credit": 0.0}])
    a = mg.credit(coll, "STORE_CREDIT", "C", 100, reason="ret", idempotency_key="ret-123")
    b = mg.credit(coll, "STORE_CREDIT", "C", 100, reason="ret", idempotency_key="ret-123")
    assert a.ok and b.ok
    assert mg.get_balance(coll, "STORE_CREDIT", "C")["balance"] == 100.0  # applied once
    assert b.reason == "duplicate" and b.txn_id == a.txn_id
    doc = coll.find_one({"customer_id": "C"})
    keyed = [e for e in doc.get("money_ledger", []) if e.get("idempotency_key") == "ret-123"]
    assert len(keyed) == 1


def test_t4b_idempotency_holds_even_when_ledger_suppressed(audit):
    """Footgun guard: an idempotency_key must dedup EVEN when record_ledger=False
    (the dedup marker must be written whenever a key is supplied, independent of
    record_ledger), so a future caller cannot silently double-apply."""
    cc = _FakeColl([{"customer_id": "C", "store_credit": 0.0}])
    a = mg.credit(cc, "STORE_CREDIT", "C", 100, reason="ret", idempotency_key="K", record_ledger=False)
    b = mg.credit(cc, "STORE_CREDIT", "C", 100, reason="ret", idempotency_key="K", record_ledger=False)
    assert a.ok and b.ok and b.reason == "duplicate"
    assert mg.get_balance(cc, "STORE_CREDIT", "C")["balance"] == 100.0  # applied once

    lc = _FakeColl([{"customer_id": "C", "balance_points": 500}])
    d1 = mg.debit(lc, "LOYALTY", "C", 100, reason="x", idempotency_key="D", record_ledger=False)
    d2 = mg.debit(lc, "LOYALTY", "C", 100, reason="x", idempotency_key="D", record_ledger=False)
    assert d1.ok and d2.ok and d2.reason == "duplicate"
    assert lc.find_one({"customer_id": "C"})["balance_points"] == 400  # debited once, not twice


# ---------------------------------------------------------------------------
# T5 -- expiry guard blocks spend on expired instrument
# ---------------------------------------------------------------------------


def test_t5_expiry_blocks_spend(audit):
    coll = _FakeColl(
        [{"code": "V1", "balance": 100.0, "status": "ACTIVE", "expiry_date": "2000-01-01"}],
        key_field="code",
    )
    r = mg.debit(coll, "GIFT_VOUCHER", "V1", 10, reason="redeem")
    assert r.ok is False and r.reason == "expired"
    assert coll.find_one({"code": "V1"})["balance"] == 100.0


# ---------------------------------------------------------------------------
# T7 -- fail-soft on absent DB
# ---------------------------------------------------------------------------


def test_t7_fail_soft_db_none():
    r = mg.debit(None, "LOYALTY", "C", 10, reason="redeem")
    assert r.ok is False and r.reason == "unavailable"


# ---------------------------------------------------------------------------
# T8 -- fail-closed on no-atomic collection
# ---------------------------------------------------------------------------


def test_t8_fail_closed_no_atomic():
    coll = _NoAtomicColl([{"customer_id": "C", "balance_points": 100}])
    r = mg.debit(coll, "LOYALTY", "C", 10, reason="redeem")
    assert r.ok is False and r.reason == "no_atomic"
    assert coll.find_one({"customer_id": "C"})["balance_points"] == 100  # untouched


# ---------------------------------------------------------------------------
# T9 -- audit row emitted per successful mutation; audit failure never rolls back
# ---------------------------------------------------------------------------


def test_t9_audit_row_per_mutation(audit):
    coll = _FakeColl([{"customer_id": "C", "store_credit": 0.0}])
    mg.credit(coll, "STORE_CREDIT", "C", 50, reason="note", actor="u1", ref="ret-999")
    rows = [x for x in audit.rows if x["entity_id"] == "C"]
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "money.credit"
    assert row["entity_type"] == "money_account"
    assert row["detail"]["delta"] == 50 and row["detail"]["ref"] == "ret-999"


def test_t9b_audit_failure_does_not_rollback(monkeypatch):
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _RaisingAuditRepo(), raising=False)
    coll = _FakeColl([{"customer_id": "C", "store_credit": 0.0}])
    r = mg.credit(coll, "STORE_CREDIT", "C", 50, reason="note")  # must NOT raise
    assert r.ok is True
    assert coll.find_one({"customer_id": "C"})["store_credit"] == 50.0


# ---------------------------------------------------------------------------
# T10 -- voucher shim behavior-preserving (exact legacy dict shape)
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name)


def test_t10_voucher_shim_shape(audit):
    from api.routers.vouchers import redeem_voucher_atomic

    coll = _FakeColl([{"code": "V1", "balance": 100.0, "status": "ACTIVE", "expiry_date": None}],
                     key_field="code")
    db = _FakeDB({"vouchers": coll})
    r1 = redeem_voucher_atomic(db, "V1", 75.00, order_id="ORD-1", redeemed_by="u1")
    assert r1 == {"ok": True, "balance": 25.0, "status": "ACTIVE", "reason": None}
    r2 = redeem_voucher_atomic(db, "V1", 30.0, order_id="ORD-2", redeemed_by="u1")
    assert r2["ok"] is False
    assert coll.find_one({"code": "V1"})["balance"] == 25.0  # shim wrote the existing coll


# ---------------------------------------------------------------------------
# T11 -- store-credit shim behavior-preserving (DEBIT_NO_ATOMIC sentinel)
# ---------------------------------------------------------------------------


def test_t11_store_credit_shim(audit):
    from database.repositories.customer_repository import CustomerRepository

    coll = _FakeColl([{"customer_id": "CUST-1", "store_credit": 100.0}])
    repo = CustomerRepository(coll)
    post = repo.try_debit_store_credit("CUST-1", 50.0)
    assert post is not None and post["store_credit"] == 50.0
    assert repo.try_debit_store_credit("CUST-1", 60.0) is None  # insufficient

    no_atomic = CustomerRepository(_NoAtomicColl([{"customer_id": "CUST-1", "store_credit": 100.0}]))
    assert no_atomic.try_debit_store_credit("CUST-1", 10.0) == CustomerRepository.DEBIT_NO_ATOMIC


# ---------------------------------------------------------------------------
# T12 -- loyalty shim behavior-preserving with lifetime counters
# ---------------------------------------------------------------------------


def test_t12_loyalty_shim_lifetime(audit):
    from database.repositories.loyalty_repository import LoyaltyAccountRepository

    coll = _FakeColl([{"customer_id": "CUST-1", "balance_points": 300,
                       "lifetime_redeemed": 0, "lifetime_earned": 0}])
    repo = LoyaltyAccountRepository(coll)
    post = repo.try_debit("CUST-1", 200, delta_lifetime_redeemed=200)
    assert post is not None
    assert post["balance_points"] == 100 and post["lifetime_redeemed"] == 200
    # Second debit of 150 against the post-write 100 -> insufficient -> None.
    assert repo.try_debit("CUST-1", 150) is None


# ---------------------------------------------------------------------------
# T13 -- DEFERRED per CORRECTIONS P0-1. The packet's T13 (a greenfield
# money_accounts WRITE succeeding) is intentionally NOT built in Phase A:
# standalone Mongo, no money_accounts SoR/index/migration. Phase A asserts the
# deferral contract instead -- the new types are unavailable and no collection
# is created. The orchestrator re-activates the packet's T13 when a Phase-2+
# packet authorizes money_accounts on a replica-set deployment.
# ---------------------------------------------------------------------------


def test_t13_new_types_deferred_unavailable():
    for t in ("PETTY_CASH", "FAMILY_WALLET", "CONSIGNMENT"):
        assert mg.credit(None, t, "k", 100).reason == "unavailable"
        assert mg.debit(None, t, "k", 100).reason == "unavailable"
        assert mg.get_balance(None, t, "k")["status"] == "unavailable"
    # OTP verbs stubbed until E6 Wave 0b.
    assert mg.request_pool_redeem().reason == "unavailable"
    assert mg.confirm_pool_redeem().reason == "unavailable"
