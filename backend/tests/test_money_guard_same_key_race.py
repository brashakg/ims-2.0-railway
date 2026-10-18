"""BRD-E1: two overlapping calls carrying the SAME idempotency key apply once.

The probe that found it: two petty payouts keyed payout:EXP-1 on a 1,000 float
left 400, not 700 -- the key marker was written only AFTER the balance write,
so a second call landing in between passed the 'key absent' filter too.

The overlap is made deterministic: a collection proxy runs the rival call the
moment the first call's guarded balance write lands (before anything else it
does). Real mongomock underneath, with the production petty-cash unique index.
No emoji (Windows cp1252).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.services import money_guard as mg  # noqa: E402
from api.services import petty_cash_service as pcs  # noqa: E402


class _Coll:
    """A mongomock collection that fires `hook` right after a balance write."""

    def __init__(self, real, hook):
        self._real, self._hook = real, hook

    def __getattr__(self, name):
        return getattr(self._real, name)

    def find_one_and_update(self, filt, update, *a, **k):
        out = self._real.find_one_and_update(filt, update, *a, **k)
        if "$inc" in update:
            self._hook()
        return out

    def update_one(self, filt, update, *a, **k):
        out = self._real.update_one(filt, update, *a, **k)
        if "$inc" in update:
            self._hook()
        return out


class _DB:
    def __init__(self, real):
        self._real = real
        self.rival = None

    def _fire(self):
        rival, self.rival = self.rival, None  # once: the rival must not recurse
        if rival:
            rival()

    def get_collection(self, name):
        return _Coll(self._real[name], self._fire)

    def __getitem__(self, name):
        return self.get_collection(name)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None, raising=False)


def test_two_overlapping_petty_payouts_for_one_expense_debit_once():
    real = mongomock.MongoClient().db
    pcs.ensure_indexes(real)  # the production unique store_id index
    assert pcs.open_float(real, store_id="S1", amount=1000.0, actor="m",
                          float_limit=10000.0, low_balance_threshold=0.0)["ok"]
    db = _DB(real)
    seen = []
    db.rival = lambda: seen.append(
        pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="EXP-1", actor="m2"))
    first = pcs.debit_float(db, store_id="S1", amount=300.0, expense_id="EXP-1", actor="m1")

    assert first["ok"] is True
    assert seen and seen[0]["ok"] is False and seen[0]["error"] == "duplicate"
    doc = real.petty_cash_floats.find_one({"store_id": "S1"})
    assert doc["balance"] == 700.0
    assert [r["type"] for r in doc["ledger"]].count("DEBIT") == 1
    keyed = [e for e in doc["money_ledger"] if e["idempotency_key"] == "payout:EXP-1"]
    assert len(keyed) == 1 and keyed[0]["balance_after"] == 700.0


# Every live account type the guard serves (petty cash, family wallet, store
# credit, loyalty, vouchers), both verbs, both write mechanisms.
_ACCOUNTS = [
    ("PETTY_CASH", "petty_cash_floats", {"store_id": "K", "balance": 1000.0, "status": "ACTIVE"}, "balance"),
    ("FAMILY_WALLET", "family_wallets", {"household_id": "K", "balance_points": 1000, "status": "ACTIVE"}, "balance_points"),
    ("STORE_CREDIT", "customers", {"customer_id": "K", "store_credit": 1000.0}, "store_credit"),
    ("LOYALTY", "loyalty_accounts", {"customer_id": "K", "balance_points": 1000}, "balance_points"),
    ("GIFT_VOUCHER", "vouchers", {"code": "K", "balance": 1000.0, "status": "ACTIVE", "expiry_date": None}, "balance"),
]


@pytest.mark.parametrize("verb,after", [("debit", 700), ("credit", 1300)])
@pytest.mark.parametrize("account_type,coll,seed,field", _ACCOUNTS, ids=[a[0] for a in _ACCOUNTS])
def test_same_key_overlap_applies_once_for_every_account_type(account_type, coll, seed, field, verb, after):
    real = mongomock.MongoClient().db
    real[coll].insert_one(dict(seed))
    db = _DB(real)
    move = getattr(mg, verb)
    seen = []
    db.rival = lambda: seen.append(move(db, account_type, "K", 300, idempotency_key="K-1"))
    first = move(db, account_type, "K", 300, idempotency_key="K-1")

    assert first.ok is True and first.reason is None
    assert seen and seen[0].ok is True and seen[0].reason == "duplicate"
    assert seen[0].txn_id == first.txn_id
    doc = real[coll].find_one({})
    assert doc[field] == after
    assert len([e for e in doc["money_ledger"] if e["idempotency_key"] == "K-1"]) == 1


def test_a_key_less_move_is_not_deduplicated():
    """Control: without a key two moves are two moves (the guard is the floor)."""
    real = mongomock.MongoClient().db
    real.customers.insert_one({"customer_id": "K", "store_credit": 1000.0})
    db = _DB(real)
    db.rival = lambda: mg.debit(db, "STORE_CREDIT", "K", 300, record_ledger=False)
    assert mg.debit(db, "STORE_CREDIT", "K", 300, record_ledger=False).ok
    assert real.customers.find_one({})["store_credit"] == 400.0
    assert "money_ledger" not in real.customers.find_one({})
