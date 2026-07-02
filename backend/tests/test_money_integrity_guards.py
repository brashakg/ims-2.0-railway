"""
IMS 2.0 - Money-integrity guard regression tests
=================================================
These cover three CRITICAL persistence-boundary money bugs fixed to mirror the
voucher redeem gold-standard (a single guarded find_one_and_update whose FILTER
encodes the spend guard; a no-match is a hard failure):

  P1-A  returns.create_return -- over-refund / repeatable-refund:
          * return_qty > purchased -> 400
          * valid partial -> 201
          * cumulative returns across two calls exceeding purchased -> 2nd 400
          * unresolvable line -> 400, unresolvable order -> 400
          * concurrent double-submit -> the atomic order-line claim rejects the
            second (409)
  P2-A  loyalty redeem / adjust-debit -- double-spend / negative balance:
          * the guarded decrement (try_debit) rejects when balance < points
            (the concurrent/stale case, simulated via the filter precondition)
          * redeem within balance succeeds
  P2-B  store-credit redeem -- double-spend:
          * the guarded decrement (try_debit_store_credit) rejects when the
            balance is insufficient
          * a redeem within balance returns the POST-update balance
          * two concurrent redeems of the same last rupees -> 2nd 400

The fakes implement find_one_and_update / a filtered update_one with Mongo's
match-then-modify atomicity, so the race tests are meaningful: by the time the
second op runs the doc no longer satisfies the guard, so it matches nothing.
"""

from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import returns as returns_module  # noqa: E402
from api.routers import loyalty as loyalty_module  # noqa: E402
from api.routers import customers as customers_module  # noqa: E402


# ============================================================================
# Shared user helpers
# ============================================================================


def _cashier() -> Dict[str, Any]:
    return {
        "user_id": "U-cash",
        "username": "cash",
        "full_name": "Cash Ier",
        "roles": ["CASHIER"],
        "active_store_id": "BV-01",
    }


def _admin() -> Dict[str, Any]:
    return {
        "user_id": "U-adm",
        "username": "adm",
        "roles": ["ADMIN"],
        "active_store_id": "BV-01",
    }


# ============================================================================
# P1-A: RETURNS over-refund / repeatable-refund
# ============================================================================


class _FakeOrdersColl:
    """orders collection modelling items.$elemMatch + positional items.$.$inc."""

    def __init__(self, orders: List[Dict[str, Any]]):
        self.docs = [copy.deepcopy(o) for o in orders]

    @staticmethod
    def _elem_matches(elem: Dict[str, Any], cond: Dict[str, Any]) -> bool:
        for key, c in cond.items():
            if key == "$or":
                if not any(_FakeOrdersColl._elem_matches(elem, sub) for sub in c):
                    return False
                continue
            val = elem.get(key)
            if isinstance(c, dict):
                for op, operand in c.items():
                    if op == "$lte" and not (val is not None and val <= operand):
                        return False
                    if op == "$lt" and not (val is not None and val < operand):
                        return False
                    if op == "$gte" and not (val is not None and val >= operand):
                        return False
                    if op == "$exists" and (bool(operand) != (key in elem)):
                        return False
            elif val != c:
                return False
        return True

    def find_one_and_update(self, query, update, return_document=None):
        order_id = (query or {}).get("order_id")
        elem_cond = ((query or {}).get("items") or {}).get("$elemMatch") or {}
        inc = (update or {}).get("$inc", {}) or {}
        for d in self.docs:
            if d.get("order_id") != order_id:
                continue
            for line in d.get("items") or []:
                if self._elem_matches(line, elem_cond):
                    for field, delta in inc.items():
                        leaf = field.split(".")[-1]
                        line[leaf] = (line.get(leaf) or 0) + delta
                    return copy.deepcopy(d)
            return None
        return None


class _FakeOrderRepo:
    def __init__(self, order: Dict[str, Any]):
        self._order = copy.deepcopy(order)
        # BUG-096: cumulative refund capped at amount_paid; model a PAID order.
        self._order.setdefault("amount_paid", 1_000_000_000.0)
        self.collection = _FakeOrdersColl([self._order])

    def find_by_id(self, oid):
        return self._order if self._order.get("order_id") == oid else None

    def find_by_order_number(self, num):
        return self._order if self._order.get("order_number") == num else None


class _FakeReturnsColl:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("return_id")})()

    def find(self, query=None, projection=None):
        q = query or {}
        matched = [
            copy.deepcopy(d)
            for d in self.docs
            if all(d.get(k) == v for k, v in q.items())
        ]
        return iter(matched)

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                out = copy.deepcopy(d)
                out.pop("_id", None)
                return out
        return None

    def count_documents(self, query=None):
        q = query or {}
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in q.items()))


@pytest.fixture
def returns_ctx(monkeypatch):
    """Wire the returns router against a fake order (one line, qty 1) + a shared
    returns collection so the already-returned scan sees prior returns."""
    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "payment_method": "UPI",
        "store_id": "BV-01",
        "items": [
            {
                "item_id": "li1",
                "product_id": "PRD-1",
                "product_name": "Ray-Ban Aviator",
                "sku": "RB-1",
                "quantity": 1,
                "unit_price": 1500,
                "gst_rate": 18.0,
                "taxable_value": 1500,
                "tax_amount": 270,
                "returned_qty": 0,
            }
        ],
    }
    order_repo = _FakeOrderRepo(order)
    returns_coll = _FakeReturnsColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            if name == "returns":
                return returns_coll
            return _FakeReturnsColl()

    fake_db = _FakeDB()
    monkeypatch.setattr(returns_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(returns_module, "get_customer_repository", lambda: None)
    monkeypatch.setattr(returns_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(returns_module, "get_stock_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        "api.dependencies.get_audit_repository", lambda: None, raising=False
    )
    return {"order_repo": order_repo, "returns_coll": returns_coll}


def _ret_line(**over):
    base = {
        "order_item_id": "li1",
        "product_id": "PRD-1",
        "product_name": "Ray-Ban Aviator",
        "sku": "RB-1",
        "return_qty": 1,
        "unit_price": 1500,
        "gst_rate": 18.0,
        "condition": "GOOD",
    }
    base.update(over)
    return returns_module.ReturnLine(**base)


def _ret_body(**over):
    kw = {
        "order_id": "ORD-1",
        "store_id": "BV-01",
        "return_type": "RETURN",
        "items": [_ret_line()],
    }
    kw.update(over)
    return returns_module.ReturnCreate(**kw)


@pytest.mark.asyncio
class TestReturnsOverRefund:
    async def test_over_qty_rejected(self, returns_ctx):
        # Line was qty 1; asking to return 100 must be a hard 400 (no refund).
        body = _ret_body(items=[_ret_line(return_qty=100)])
        with pytest.raises(HTTPException) as ei:
            await returns_module.create_return(body, current_user=_cashier())
        assert ei.value.status_code == 400
        assert "exceeds" in str(ei.value.detail).lower()
        # Nothing persisted.
        assert returns_ctx["returns_coll"].docs == []

    async def test_valid_partial_ok(self, returns_ctx):
        # Original line: qty 3, billed gross 5310 (4500 taxable + 810 tax) ->
        # per-unit billed gross 1770. A partial return of 2 is valid.
        for items in (
            returns_ctx["order_repo"]._order["items"],
            returns_ctx["order_repo"].collection.docs[0]["items"],
        ):
            items[0].update(
                {"quantity": 3, "taxable_value": 4500, "tax_amount": 810}
            )
        body = _ret_body(items=[_ret_line(return_qty=2)])
        out = await returns_module.create_return(body, current_user=_cashier())
        assert out["return_type"] == "RETURN"
        # 2 units * 1770 per-unit billed gross = 3540.
        assert out["returned_value"] == 3540.0
        assert out["refund_amount"] == 3540.0
        assert out["return_id"].startswith("RET-")
        assert len(returns_ctx["returns_coll"].docs) == 1

    async def test_cumulative_across_two_calls_second_rejected(self, returns_ctx):
        # qty 2 line; first return 1 (ok), second return 2 -> only 1 left -> 400.
        returns_ctx["order_repo"]._order["items"][0]["quantity"] = 2
        returns_ctx["order_repo"].collection.docs[0]["items"][0]["quantity"] = 2

        out1 = await returns_module.create_return(
            _ret_body(items=[_ret_line(return_qty=1)]), current_user=_cashier()
        )
        assert out1["return_id"].startswith("RET-")

        with pytest.raises(HTTPException) as ei:
            await returns_module.create_return(
                _ret_body(items=[_ret_line(return_qty=2)]), current_user=_cashier()
            )
        assert ei.value.status_code == 400
        assert "exceeds" in str(ei.value.detail).lower()
        # Exactly one return persisted; the over-cap second never recorded.
        assert len(returns_ctx["returns_coll"].docs) == 1

    async def test_cumulative_exact_remaining_ok(self, returns_ctx):
        # qty 2 line; return 1 then 1 -> both within cap.
        returns_ctx["order_repo"]._order["items"][0]["quantity"] = 2
        returns_ctx["order_repo"].collection.docs[0]["items"][0]["quantity"] = 2
        await returns_module.create_return(
            _ret_body(items=[_ret_line(return_qty=1)]), current_user=_cashier()
        )
        out2 = await returns_module.create_return(
            _ret_body(items=[_ret_line(return_qty=1)]), current_user=_cashier()
        )
        assert out2["return_id"].startswith("RET-")
        assert len(returns_ctx["returns_coll"].docs) == 2

    async def test_unresolvable_line_rejected(self, returns_ctx):
        # product_id not on the order + no matching order_item_id -> 400.
        body = _ret_body(
            items=[_ret_line(order_item_id=None, product_id="PRD-NOPE")]
        )
        with pytest.raises(HTTPException) as ei:
            await returns_module.create_return(body, current_user=_cashier())
        assert ei.value.status_code == 400
        assert "original order" in str(ei.value.detail).lower()
        assert returns_ctx["returns_coll"].docs == []

    async def test_unresolvable_order_rejected(self, returns_ctx):
        # An order id that doesn't exist -> 400 (never blind-refund).
        body = _ret_body(order_id="ORD-MISSING", order_number=None)
        with pytest.raises(HTTPException) as ei:
            await returns_module.create_return(body, current_user=_cashier())
        assert ei.value.status_code == 400
        assert "could not be resolved" in str(ei.value.detail).lower()
        assert returns_ctx["returns_coll"].docs == []

    async def test_concurrent_double_submit_loses_on_claim(self, returns_ctx):
        """Simulate the concurrent double-submit: the returnable qty on the order
        line is already exhausted at the DB level (another worker claimed it),
        but the prior-returns scan can't see it (its return doc not yet visible).
        The atomic order-line claim must still reject the second with 409."""
        # qty 1 line. Pre-exhaust the order line's returned_qty to 1 directly,
        # WITHOUT inserting a `returns` doc -> the scan sees already=0 (so it
        # would pass), but the atomic claim sees no remaining qty -> 409.
        returns_ctx["order_repo"].collection.docs[0]["items"][0]["returned_qty"] = 1
        body = _ret_body(items=[_ret_line(return_qty=1)])
        with pytest.raises(HTTPException) as ei:
            await returns_module.create_return(body, current_user=_cashier())
        assert ei.value.status_code == 409
        assert "another transaction" in str(ei.value.detail).lower()
        assert returns_ctx["returns_coll"].docs == []

    async def test_claim_releases_other_lines_on_partial_failure(self, returns_ctx):
        """Two-line return where the SECOND line loses the claim: the first
        line's reservation must be released so it isn't left phantom-reserved."""
        # Order has two lines: li1 (qty 5) and li2 (qty 1, already exhausted).
        order = returns_ctx["order_repo"]._order
        order["items"][0]["quantity"] = 5
        order["items"].append(
            {
                "item_id": "li2",
                "product_id": "PRD-2",
                "product_name": "Case",
                "sku": "C-2",
                "quantity": 1,
                "unit_price": 100,
                "gst_rate": 18.0,
                "taxable_value": 100,
                "tax_amount": 18,
                "returned_qty": 1,  # already fully returned
            }
        )
        coll_doc = returns_ctx["order_repo"].collection.docs[0]
        coll_doc["items"][0]["quantity"] = 5
        coll_doc["items"].append(copy.deepcopy(order["items"][1]))

        body = _ret_body(
            items=[
                _ret_line(order_item_id="li1", product_id="PRD-1", return_qty=2),
                _ret_line(
                    order_item_id="li2",
                    product_id="PRD-2",
                    product_name="Case",
                    sku="C-2",
                    return_qty=1,
                ),
            ]
        )
        with pytest.raises(HTTPException) as ei:
            await returns_module.create_return(body, current_user=_cashier())
        assert ei.value.status_code == 409
        # li1's reservation must have been released back to 0 (not left at 2).
        assert coll_doc["items"][0].get("returned_qty", 0) == 0


# ============================================================================
# P2-A: LOYALTY redeem / adjust double-spend
# ============================================================================


def _loy_doc_matches(doc, flt):
    for k, expected in (flt or {}).items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
        elif actual != expected:
            return False
    return True


class _LoyAccountsColl:
    """loyalty_accounts with atomic find_one_and_update (guard-in-filter)."""

    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _loy_doc_matches(d, flt):
                return copy.deepcopy(d)
        return None

    def update_one(self, flt, update):
        for d in self.docs:
            if _loy_doc_matches(d, flt):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                for k, v in (update.get("$inc") or {}).items():
                    d[k] = (d.get(k) or 0) + v
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find_one_and_update(self, flt, update, return_document=None, **_kw):
        for d in self.docs:
            if _loy_doc_matches(d, flt):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                for k, v in (update.get("$inc") or {}).items():
                    d[k] = (d.get(k) or 0) + v
                return copy.deepcopy(d)
        return None


def _make_account_repo(balance: int):
    from database.repositories.loyalty_repository import LoyaltyAccountRepository

    coll = _LoyAccountsColl()
    coll.insert_one(
        {
            "customer_id": "CUST-1",
            "_id": "CUST-1",
            "balance_points": balance,
            "tier": "BRONZE",
            "lifetime_earned": balance,
            "lifetime_redeemed": 0,
        }
    )
    return LoyaltyAccountRepository(coll), coll


class TestLoyaltyTryDebit:
    def test_try_debit_within_balance_succeeds(self):
        repo, coll = _make_account_repo(500)
        out = repo.try_debit("CUST-1", 200, delta_lifetime_redeemed=200)
        assert out is not None
        assert out["balance_points"] == 300
        assert out["lifetime_redeemed"] == 200

    def test_try_debit_rejects_when_balance_below_points(self):
        # The concurrent/stale case: ask to debit more than the balance -> the
        # filter (balance_points >= points) matches nothing -> None (rejected).
        repo, coll = _make_account_repo(100)
        out = repo.try_debit("CUST-1", 101)
        assert out is None
        # Balance untouched.
        assert coll.find_one({"customer_id": "CUST-1"})["balance_points"] == 100

    def test_try_debit_exact_balance_drains_to_zero(self):
        repo, coll = _make_account_repo(100)
        out = repo.try_debit("CUST-1", 100, delta_lifetime_redeemed=100)
        assert out is not None and out["balance_points"] == 0

    def test_two_concurrent_debits_cannot_overspend(self):
        # Balance 100. First debit 70 -> 30. Second debit 70 -> only 30 left ->
        # rejected. Net debit is exactly 70, never 140.
        repo, coll = _make_account_repo(100)
        first = repo.try_debit("CUST-1", 70, delta_lifetime_redeemed=70)
        assert first is not None and first["balance_points"] == 30
        second = repo.try_debit("CUST-1", 70, delta_lifetime_redeemed=70)
        assert second is None
        assert coll.find_one({"customer_id": "CUST-1"})["balance_points"] == 30

    def test_try_debit_no_atomic_support_returns_none(self):
        # A minimal collection without find_one_and_update -> None (caller fails
        # closed rather than silently double-spending).
        from database.repositories.loyalty_repository import LoyaltyAccountRepository

        class _MinimalColl:
            def find_one(self, *_a, **_k):
                return {"customer_id": "CUST-1", "balance_points": 500}

        repo = LoyaltyAccountRepository(_MinimalColl())
        assert repo.try_debit("CUST-1", 10) is None


@pytest.fixture
def loyalty_redeem_ctx(monkeypatch):
    """Wire the loyalty router's redeem path against fake repos."""
    from database.repositories.loyalty_repository import (
        LoyaltyTransactionRepository,
    )

    account_repo, accounts_coll = _make_account_repo(500)

    class _TxnColl:
        def __init__(self):
            self.docs: List[Dict[str, Any]] = []

        def insert_one(self, doc):
            self.docs.append(copy.deepcopy(doc))
            return type("R", (), {"inserted_id": doc.get("_id")})()

        def find_one(self, *_a, **_k):
            return None

    txns = LoyaltyTransactionRepository(_TxnColl())

    monkeypatch.setattr(
        loyalty_module, "get_loyalty_account_repository", lambda: account_repo
    )
    monkeypatch.setattr(
        loyalty_module, "get_loyalty_transaction_repository", lambda: txns
    )
    monkeypatch.setattr(loyalty_module, "get_audit_repository", lambda: None)
    monkeypatch.setattr(loyalty_module, "get_order_repository", lambda: None)
    return {"accounts": account_repo, "accounts_coll": accounts_coll, "txns": txns}


@pytest.mark.asyncio
class TestLoyaltyRedeemEndpoint:
    async def test_redeem_within_balance_ok(self, loyalty_redeem_ctx):
        body = loyalty_module.RedeemRequest(
            customer_id="CUST-1", points=200, order_value=5000.0
        )
        out = await loyalty_module.redeem(body, current_user=_admin())
        assert out["redeemed_points"] == 200
        acct = loyalty_redeem_ctx["accounts_coll"].find_one({"customer_id": "CUST-1"})
        assert acct["balance_points"] == 300
        # One REDEEM ledger row written (only after the debit succeeded).
        assert len(loyalty_redeem_ctx["txns"].collection.docs) == 1

    async def test_redeem_rejected_when_balance_drained_concurrently(
        self, loyalty_redeem_ctx, monkeypatch
    ):
        # The concurrent/stale case: another redeem drained the balance to 50
        # AFTER the advisory calc_redeem read passed (snapshot said 500). We
        # simulate that by forcing calc_redeem to approve 200 while the DB row
        # holds only 50 -> the atomic try_debit guard (balance_points >= 200)
        # matches nothing -> 409 and NO ledger row. This is exactly the property
        # that prevents two racing redeems from both succeeding.
        loyalty_redeem_ctx["accounts_coll"].docs[0]["balance_points"] = 50
        monkeypatch.setattr(
            loyalty_module,
            "calc_redeem",
            lambda points, balance, order_value, settings: {
                "ok": True,
                "capped_points": 200,
                "rupee_value": 200.0,
                "was_capped": False,
                "requested_points": 200,
            },
        )
        # order_value supplies the required order linkage (the over-redeem guard);
        # rupee_value 200 is well within it, so the ATOMIC DEBIT guard is what
        # this test exercises.
        body = loyalty_module.RedeemRequest(
            customer_id="CUST-1", points=200, order_value=5000.0
        )
        with pytest.raises(HTTPException) as ei:
            await loyalty_module.redeem(body, current_user=_admin())
        assert ei.value.status_code == 409
        assert "insufficient" in str(ei.value.detail).lower()
        # Balance untouched + no ledger row recorded.
        assert (
            loyalty_redeem_ctx["accounts_coll"].find_one(
                {"customer_id": "CUST-1"}
            )["balance_points"]
            == 50
        )
        assert loyalty_redeem_ctx["txns"].collection.docs == []


@pytest.mark.asyncio
class TestLoyaltyAdjustDebit:
    async def test_adjust_debit_within_balance_ok(self, loyalty_redeem_ctx):
        body = loyalty_module.AdjustRequest(
            customer_id="CUST-1", points=-100, reason="manual debit"
        )
        out = await loyalty_module.adjust(body, current_user=_admin())
        assert out["delta"] == -100
        assert out["balance_after"] == 400

    async def test_adjust_debit_below_zero_rejected(self, loyalty_redeem_ctx):
        # Account has 500; debit 600 -> guard rejects -> 400, no ledger row.
        body = loyalty_module.AdjustRequest(
            customer_id="CUST-1", points=-600, reason="too much"
        )
        with pytest.raises(HTTPException) as ei:
            await loyalty_module.adjust(body, current_user=_admin())
        assert ei.value.status_code == 400
        assert (
            loyalty_redeem_ctx["accounts_coll"].find_one(
                {"customer_id": "CUST-1"}
            )["balance_points"]
            == 500
        )
        assert loyalty_redeem_ctx["txns"].collection.docs == []


# ============================================================================
# P2-B: STORE-CREDIT redeem double-spend
# ============================================================================


class _CreditCustomerColl:
    """customers collection with a filtered (atomic) update_one used by the
    store-credit guarded decrement."""

    def __init__(self, store_credit: float):
        self.docs = [{"customer_id": "CUST-1", "store_credit": store_credit}]

    def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _loy_doc_matches(d, flt):
                return copy.deepcopy(d)
        return None

    def update_one(self, flt, update):
        for d in self.docs:
            if _loy_doc_matches(d, flt):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                for k, v in (update.get("$inc") or {}).items():
                    d[k] = (d.get(k) or 0) + v
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()


def _make_credit_repo(store_credit: float):
    from database.repositories.customer_repository import CustomerRepository

    coll = _CreditCustomerColl(store_credit)
    return CustomerRepository(coll), coll


class TestStoreCreditTryDebit:
    def test_debit_within_balance_returns_post_doc(self):
        repo, coll = _make_credit_repo(500.0)
        out = repo.try_debit_store_credit("CUST-1", 200.0)
        assert isinstance(out, dict)
        assert out["store_credit"] == 300.0

    def test_debit_insufficient_rejected(self):
        repo, coll = _make_credit_repo(100.0)
        assert repo.try_debit_store_credit("CUST-1", 150.0) is None
        # Untouched.
        assert coll.find_one({"customer_id": "CUST-1"})["store_credit"] == 100.0

    def test_two_concurrent_debits_cannot_overspend(self):
        repo, coll = _make_credit_repo(100.0)
        first = repo.try_debit_store_credit("CUST-1", 70.0)
        assert first is not None and first["store_credit"] == 30.0
        # Second redeem of the last rupees finds no matching doc -> rejected.
        assert repo.try_debit_store_credit("CUST-1", 70.0) is None
        assert coll.find_one({"customer_id": "CUST-1"})["store_credit"] == 30.0

    def test_no_atomic_support_returns_sentinel(self):
        from database.repositories.customer_repository import CustomerRepository

        class _MinimalColl:
            pass

        repo = CustomerRepository(_MinimalColl())
        assert repo.try_debit_store_credit("CUST-1", 10.0) == repo.DEBIT_NO_ATOMIC


@pytest.fixture
def store_credit_ctx(monkeypatch):
    """Wire customers._post_credit_entry against a fake customer repo (with the
    atomic credit decrement) + a ledger collection."""
    repo, coll = _make_credit_repo(500.0)

    class _LedgerColl:
        def __init__(self):
            self.docs: List[Dict[str, Any]] = []

        def insert_one(self, doc):
            self.docs.append(copy.deepcopy(doc))
            return type("R", (), {"inserted_id": None})()

        def find(self, query=None, projection=None):
            q = query or {}
            return iter(
                copy.deepcopy(d)
                for d in self.docs
                if all(d.get(k) == v for k, v in q.items())
            )

    ledger = _LedgerColl()
    monkeypatch.setattr(customers_module, "get_customer_repository", lambda: repo)
    monkeypatch.setattr(customers_module, "_ledger_coll", lambda: ledger)
    return {"repo": repo, "coll": coll, "ledger": ledger}


class TestStoreCreditRedeemEndpoint:
    def test_redeem_within_balance_uses_post_update_balance(self, store_credit_ctx):
        body = customers_module.StoreCreditEntryRequest(amount=200.0, reason="POS")
        out = customers_module._post_credit_entry(
            "CUST-1", "REDEEMED", body, _admin()
        )
        # Authoritative balance from the decremented doc, not a snapshot.
        assert out["balance"] == 300.0
        assert out["entry"]["balance_after"] == 300.0
        assert out["entry"]["type"] == "REDEEMED"
        assert out["entry"]["delta"] == -200.0
        # Ledger row written.
        assert len(store_credit_ctx["ledger"].docs) == 1
        # Legacy store_credit number decremented atomically.
        assert store_credit_ctx["coll"].find_one(
            {"customer_id": "CUST-1"}
        )["store_credit"] == 300.0

    def test_redeem_insufficient_rejected(self, store_credit_ctx):
        body = customers_module.StoreCreditEntryRequest(amount=600.0, reason="POS")
        with pytest.raises(HTTPException) as ei:
            customers_module._post_credit_entry("CUST-1", "REDEEMED", body, _admin())
        assert ei.value.status_code == 400
        assert "insufficient" in str(ei.value.detail).lower()
        # No ledger row + balance untouched.
        assert store_credit_ctx["ledger"].docs == []
        assert store_credit_ctx["coll"].find_one(
            {"customer_id": "CUST-1"}
        )["store_credit"] == 500.0

    def test_two_concurrent_redeems_second_rejected(self, store_credit_ctx):
        # Balance 500. Redeem 400 -> 100. Second redeem 400 -> only 100 left ->
        # rejected. The legacy number never goes negative.
        body = customers_module.StoreCreditEntryRequest(amount=400.0, reason="POS")
        out1 = customers_module._post_credit_entry(
            "CUST-1", "REDEEMED", body, _admin()
        )
        assert out1["balance"] == 100.0
        with pytest.raises(HTTPException) as ei:
            customers_module._post_credit_entry("CUST-1", "REDEEMED", body, _admin())
        assert ei.value.status_code == 400
        assert store_credit_ctx["coll"].find_one(
            {"customer_id": "CUST-1"}
        )["store_credit"] == 100.0
        # Exactly one redeem ledger row.
        assert len(store_credit_ctx["ledger"].docs) == 1

    def test_issue_credit_still_works_via_snapshot(self, store_credit_ctx):
        # A credit (ISSUE) is not a double-spend risk -> still goes through the
        # snapshot path and bumps the balance.
        body = customers_module.StoreCreditEntryRequest(amount=250.0, reason="note")
        out = customers_module._post_credit_entry("CUST-1", "ISSUED", body, _admin())
        assert out["entry"]["type"] == "ISSUED"
        assert out["entry"]["delta"] == 250.0
        assert out["balance"] == 750.0
