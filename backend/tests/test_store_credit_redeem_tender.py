"""
IMS 2.0 -- Store-credit REDEMPTION tender (the missing spend side)
==================================================================
Every refund ISSUES store credit and the till DISPLAYS the balance, but there
was no tender to SPEND it -- the liability sat on the books forever. The fix
extracts the /store-credit/redeem route's atomic branch into ONE reusable
door, ``customers.redeem_store_credit_atomic`` (guarded conditional decrement
via repo.try_debit_store_credit -> money_guard.debit, the voucher mechanism),
which the POS STORE_CREDIT payment method also calls.

THE KEY TESTS (per the build brief):
  1. a concurrent double-redeem of the same credit must not spend it twice --
     driven through the REAL atomic path over a strict fake whose update_one
     is made atomic exactly the way Mongo's is (single-document, one lock);
  2. redeeming another customer's credit by id must be refused -- the HTTP
     route now object-scopes the customer (write -> 403), so a customer id in
     the URL/body is not authority to spend that customer's money;
  3. the ledger must reconcile -- issued minus redeemed equals the balance,
     on BOTH the authoritative customer.store_credit field and the sum of
     signed ledger deltas.

Discriminating power was MEASURED by reverting each guard (see the PR notes):
replacing the guarded debit with a read-modify-write fails test 1; restoring
the unscoped ``repo.find_by_id`` fails test 2; dropping the ledger insert on
redeem fails test 3.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-store-credit-redeem")

from fastapi import HTTPException  # noqa: E402

from strict_fakes import StrictCollection  # noqa: E402


class AtomicStrictCollection(StrictCollection):
    """StrictCollection whose update_one is ATOMIC under threads.

    Real MongoDB guarantees a single filtered update_one is atomic on one
    document -- the guard (store_credit >= amount) and the $inc happen as one
    step. The plain fake matches then applies in separate Python statements,
    so threads could interleave INSIDE the fake and fake a bug (or hide one).
    One lock restores exactly Mongo's per-operation guarantee, no more: the
    read-modify-write pattern this build forbids would still race across TWO
    calls, which is what test 1 exercises.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._lock = threading.Lock()

    def update_one(self, *a, **k):
        with self._lock:
            return super().update_one(*a, **k)


def _wire(monkeypatch, customers_docs, ledger_docs=None, coll_cls=AtomicStrictCollection):
    """Bind the customers router to a fake repo + fake ledger collection.
    Returns (customers_module, customers_collection, ledger_collection)."""
    import api.routers.customers as customers
    from database.repositories.customer_repository import CustomerRepository

    coll = coll_cls("customers", customers_docs)
    repo = CustomerRepository(coll)
    ledger = StrictCollection("credit_note_ledger", ledger_docs or [])
    monkeypatch.setattr(customers, "get_customer_repository", lambda: repo)
    monkeypatch.setattr(customers, "_ledger_coll", lambda: ledger)
    return customers, coll, ledger


def _admin():
    return {"user_id": "u-admin", "roles": ["ADMIN"], "active_store_id": "BV-01"}


def _store_user(store_id="BV-01", roles=("STORE_MANAGER",)):
    return {
        "user_id": "u-mgr",
        "roles": list(roles),
        "active_store_id": store_id,
        "store_ids": [store_id],
    }


# ---------------------------------------------------------------------------
# 1. Concurrency: the same rupees cannot be spent twice.
# ---------------------------------------------------------------------------

def test_concurrent_double_redeem_spends_the_credit_once(monkeypatch):
    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C1", "home_store_id": "BV-01", "store_credit": 500.0}],
    )

    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        barrier.wait()
        try:
            out = customers.redeem_store_credit_atomic(
                "C1", 400.0, reason="race", ref="ord-race", store_id="BV-01",
                user_id="u1",
            )
            outcomes.append(("ok", out["balance"]))
        except HTTPException as exc:
            outcomes.append(("http", exc.status_code))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [o for o in outcomes if o[0] == "ok"]
    losses = [o for o in outcomes if o[0] == "http"]
    # Exactly ONE spend: 500 - 400 leaves 100, which cannot cover another 400.
    assert len(wins) == 1, f"expected exactly one winner, got {outcomes}"
    assert len(losses) == 1 and losses[0][1] == 400
    assert wins[0][1] == 100.0
    # The document agrees, and exactly one REDEEMED audit row was written.
    assert coll.docs[0]["store_credit"] == 100.0
    redeemed_rows = [e for e in ledger.docs if e["type"] == "REDEEMED"]
    assert len(redeemed_rows) == 1
    assert redeemed_rows[0]["delta"] == -400.0


def test_insufficient_redeem_is_refused_and_writes_nothing(monkeypatch):
    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C1", "home_store_id": "BV-01", "store_credit": 100.0}],
    )
    with pytest.raises(HTTPException) as exc:
        customers.redeem_store_credit_atomic("C1", 400.0, ref="ord-1")
    assert exc.value.status_code == 400
    assert "insufficient store credit" in str(exc.value.detail)
    assert coll.docs[0]["store_credit"] == 100.0
    assert ledger.docs == []


def test_no_atomic_collection_rejects_503_never_snapshot_spends(monkeypatch):
    """A collection that cannot do a conditional decrement must REFUSE the
    redeem (503), never fall back to the snapshot read-modify-write that
    caused the original double-spend."""

    class NoAtomicColl:
        def __init__(self, docs):
            self.docs = docs

        def find_one(self, flt, *a, **k):
            for d in self.docs:
                if all(d.get(kk) == vv for kk, vv in flt.items() if not isinstance(vv, dict)):
                    return dict(d)
            return None
        # deliberately: no update_one / find_one_and_update

    import api.routers.customers as customers
    from database.repositories.customer_repository import CustomerRepository

    coll = NoAtomicColl([{"customer_id": "C1", "store_credit": 500.0}])
    monkeypatch.setattr(customers, "get_customer_repository", lambda: CustomerRepository(coll))
    ledger = StrictCollection("credit_note_ledger", [])
    monkeypatch.setattr(customers, "_ledger_coll", lambda: ledger)

    with pytest.raises(HTTPException) as exc:
        customers.redeem_store_credit_atomic("C1", 50.0)
    assert exc.value.status_code == 503
    assert coll.docs[0]["store_credit"] == 500.0
    assert ledger.docs == []


# ---------------------------------------------------------------------------
# 2. Authority: a customer id is not permission to spend that customer's money.
# ---------------------------------------------------------------------------

def test_redeeming_another_stores_customer_by_id_is_refused(monkeypatch):
    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C-OTHER", "home_store_id": "WO-02", "store_credit": 900.0}],
    )
    body = customers.StoreCreditEntryRequest(amount=200.0, reason="steal", ref="ord-x")
    # A Bokaro store manager names a Pune customer's id. Object-level store
    # scope on the WRITE -> 403, and not a rupee moves.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            customers.redeem_store_credit(
                "C-OTHER", body, _store_user(store_id="BV-01")
            )
        )
    assert exc.value.status_code == 403
    assert coll.docs[0]["store_credit"] == 900.0
    assert ledger.docs == []


def test_issue_door_is_scoped_too(monkeypatch):
    """The same IDOR closure covers ISSUE: minting credit onto another store's
    customer by guessed id is refused (the audit found the ledger GET scoped
    while the money-moving doors did not)."""
    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C-OTHER", "home_store_id": "WO-02", "store_credit": 0.0}],
    )
    body = customers.StoreCreditEntryRequest(amount=500.0, reason="mint")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            customers.issue_store_credit("C-OTHER", body, _store_user(store_id="BV-01"))
        )
    assert exc.value.status_code == 403
    assert coll.docs[0]["store_credit"] == 0.0
    assert ledger.docs == []


def test_in_scope_redeem_succeeds_for_store_user(monkeypatch):
    """Control for the two refusal tests: the SAME store user CAN redeem their
    own store's customer -- so the 403s above prove scoping, not breakage."""
    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C1", "home_store_id": "BV-01", "store_credit": 300.0}],
    )
    body = customers.StoreCreditEntryRequest(amount=120.0, reason="pos", ref="ord-9")
    out = asyncio.run(
        customers.redeem_store_credit("C1", body, _store_user(store_id="BV-01"))
    )
    assert out["balance"] == 180.0
    assert coll.docs[0]["store_credit"] == 180.0
    assert ledger.docs[-1]["ref"] == "ord-9"


# ---------------------------------------------------------------------------
# 3. Reconciliation: issued - redeemed == balance, and the spend is linked
#    to the order that consumed it.
# ---------------------------------------------------------------------------

def test_ledger_reconciles_issued_minus_redeemed_equals_balance(monkeypatch):
    from api.services import store_credit_ledger as scl

    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C1", "home_store_id": "BV-01", "store_credit": 0.0}],
    )
    user = _admin()

    asyncio.run(customers.issue_store_credit(
        "C1", customers.StoreCreditEntryRequest(amount=300.0, reason="refund CN-1"), user))
    asyncio.run(customers.issue_store_credit(
        "C1", customers.StoreCreditEntryRequest(amount=200.0, reason="refund CN-2"), user))
    out = asyncio.run(customers.redeem_store_credit(
        "C1", customers.StoreCreditEntryRequest(amount=150.0, ref="ord-42"), user))

    # 300 + 200 - 150 = 350, agreed by BOTH the authoritative field and the
    # signed ledger deltas -- the liability comes off the books when spent.
    assert out["balance"] == 350.0
    assert coll.docs[0]["store_credit"] == 350.0
    assert scl.compute_balance(ledger.docs) == 350.0
    redeemed = [e for e in ledger.docs if e["type"] == "REDEEMED"]
    assert len(redeemed) == 1
    assert redeemed[0]["ref"] == "ord-42"  # spend is traceable to its order
    assert redeemed[0]["balance_after"] == 350.0


def test_legacy_add_door_now_writes_a_ledger_row(monkeypatch):
    """/store-credit/add used to $inc the bare field with NO ledger row,
    silently breaking issued-minus-redeemed==balance. It now routes through
    the issue door: same response keys, but the audit trail sees it."""
    customers, coll, ledger = _wire(
        monkeypatch,
        [{"customer_id": "C1", "home_store_id": "BV-01", "store_credit": 40.0}],
    )
    out = asyncio.run(customers.add_store_credit("C1", 60.0, _admin()))
    assert out["new_total"] == 100.0
    assert coll.docs[0]["store_credit"] == 100.0
    issued = [e for e in ledger.docs if e["type"] == "ISSUED"]
    assert len(issued) == 1 and issued[0]["delta"] == 60.0
