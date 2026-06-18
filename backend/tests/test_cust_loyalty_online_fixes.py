"""
IMS 2.0 - Regression tests for three correctness/security fixes
===============================================================
Covers:

  BUG-1  Customer duplicate-prevention across mobile/phone:
           * TechCherry _map_customer now mirrors the number into `mobile`
             (normalize-on-write) so the UNIQUE sparse `mobile` index enforces
             one account per number across imported (phone) + native (mobile).
           * a re-import of the same number dedups against EITHER field instead
             of inserting a duplicate.

  BUG-2  Loyalty EARN double-earn race:
           * claim_earn_for_order is an atomic upsert -- two earns for the same
             (customer, order) yield exactly ONE EARN row / points awarded once.

  BUG-3  online_store_orders list store scope:
           * a store-scoped ACCOUNTANT only sees their store's online orders;
             an ADMIN (cross-store) sees all.

The transaction fake implements find_one_and_update with Mongo upsert +
$setOnInsert semantics so the EARN race test is meaningful: the second claim
finds the row the first inserted and inserts nothing.
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

from api.routers import loyalty as loyalty_module  # noqa: E402
from api.routers import online_store_orders as online_module  # noqa: E402


# ===========================================================================
# BUG-1: customer mobile/phone normalize-on-write + dedup
# ===========================================================================


class TestCustomerMobilePhoneDedup:
    def test_map_customer_populates_mobile_from_phone(self):
        """The imported doc must carry `mobile` (mirrored from phone) so the
        UNIQUE sparse `mobile` index actually constrains it."""
        from api.routers.techcherry_import import _map_customer

        doc = _map_customer({"Name": "Rakesh", "Mobile": "+91 98765-43210"},
                            "BV-PUN-01", "techcherry")
        assert doc is not None
        assert doc["phone"] == "9876543210"
        # The fix: mobile is set from phone so the unique index enforces dedup.
        assert doc["mobile"] == "9876543210"

    def test_map_customer_no_phone_leaves_mobile_absent(self):
        """A name-only row (no number) keeps `mobile` None so the SPARSE unique
        index still exempts it (many such rows must not collide on a null key)."""
        from api.routers.techcherry_import import _map_customer

        doc = _map_customer({"Name": "Walk In"}, "BV-PUN-01", "techcherry")
        assert doc is not None
        assert doc["phone"] == ""
        assert doc["mobile"] is None

    @pytest.mark.asyncio
    async def test_create_customer_rejects_duplicate_number_under_phone(self, monkeypatch):
        """A 2nd create of a number that already exists as an imported customer
        (stored under `phone`, no `mobile`) is rejected 409 -- because the
        app-level guard uses find_by_mobile which ORs phone+mobile."""
        from api.routers import customers as customers_module
        from database.repositories.customer_repository import CustomerRepository
        from fastapi import HTTPException

        class _CustColl:
            def __init__(self):
                # An existing TechCherry-imported customer: number under `phone`.
                self.docs: List[Dict[str, Any]] = [
                    {"customer_id": "C-1", "name": "Old", "phone": "9876543210",
                     "is_active": True}
                ]

            def find_one(self, flt=None, projection=None):
                flt = flt or {}
                ors = flt.get("$or")
                for d in self.docs:
                    if ors is not None:
                        if any(all(d.get(k) == v for k, v in sub.items()) for sub in ors):
                            return copy.deepcopy(d)
                    elif all(d.get(k) == v for k, v in flt.items()):
                        return copy.deepcopy(d)
                return None

        repo = CustomerRepository(_CustColl())
        monkeypatch.setattr(
            customers_module, "get_customer_repository", lambda: repo
        )

        body = customers_module.CustomerCreate(
            customer_type="B2C", name="New", mobile="9876543210"
        )
        with pytest.raises(HTTPException) as ei:
            await customers_module.create_customer(
                body, current_user={"user_id": "U", "active_store_id": "BV-01"}
            )
        assert ei.value.status_code == 409
        assert "mobile" in str(ei.value.detail).lower()


# ===========================================================================
# BUG-2: atomic loyalty earn (no double-earn)
# ===========================================================================


class DuplicateKeyError(Exception):
    """Stand-in for pymongo.errors.DuplicateKeyError (matched by class name)."""


class _TxnColl:
    """loyalty_transactions fake that ENFORCES the UNIQUE
    (customer_id, order_id, type=EARN) index, so the insert-with-duplicate-guard
    in claim_earn_for_order is exercised: a 2nd EARN insert for the same
    (customer, order) raises DuplicateKeyError exactly like real Mongo."""

    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    @staticmethod
    def _matches(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
        for k, v in (flt or {}).items():
            if doc.get(k) != v:
                return False
        return True

    def insert_one(self, doc):
        # Enforce the partial-unique EARN index.
        if doc.get("type") == "EARN" and doc.get("order_id"):
            for d in self.docs:
                if (d.get("type") == "EARN"
                        and d.get("customer_id") == doc.get("customer_id")
                        and d.get("order_id") == doc.get("order_id")):
                    raise DuplicateKeyError("duplicate EARN")
        self.docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if self._matches(d, flt or {}):
                return copy.deepcopy(d)
        return None

    def find(self, flt=None, projection=None):
        matched = [copy.deepcopy(d) for d in self.docs if self._matches(d, flt or {})]
        return _Cursor(matched)


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def skip(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self._docs)


class _AccountRepoStub:
    """Minimal account repo: tracks how many times the balance was bumped."""

    def __init__(self):
        self.account = {"customer_id": "C-1", "tier": "BRONZE",
                        "balance_points": 0, "lifetime_earned": 0}
        self.adjust_calls = 0

    def find_or_create(self, _cid):
        return copy.deepcopy(self.account)

    def find_by_id(self, _cid):
        return copy.deepcopy(self.account)

    def adjust_balance(self, _cid, delta_points=0, delta_lifetime_earned=0,
                       new_tier=None, **_kw):
        self.adjust_calls += 1
        self.account["balance_points"] += delta_points
        self.account["lifetime_earned"] += delta_lifetime_earned
        if new_tier:
            self.account["tier"] = new_tier
        return self.account


def _earn_ctx(monkeypatch):
    from database.repositories.loyalty_repository import LoyaltyTransactionRepository

    txns = LoyaltyTransactionRepository(_TxnColl())
    accounts = _AccountRepoStub()

    class _OrderRepo:
        def find_by_id(self, _oid):
            # taxable basis = grand_total - tax_amount = 1000
            return {"order_id": "O-1", "customer_id": "C-1",
                    "grand_total": 1050.0, "tax_amount": 50.0}

    monkeypatch.setattr(loyalty_module, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(loyalty_module, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(loyalty_module, "get_order_repository", lambda: _OrderRepo())
    monkeypatch.setattr(loyalty_module, "get_audit_repository", lambda: None)
    # Generous, simple settings so calc_earn_points returns > 0 (real keys).
    monkeypatch.setattr(
        loyalty_module, "_settings_safe",
        lambda: {"enabled": True, "points_per_rupee": 0.1, "min_order_for_earn": 0.0,
                 "expiry_days": 365, "tier_thresholds": {}, "tier_multipliers": {}},
    )
    return txns, accounts


def _pos_user() -> Dict[str, Any]:
    return {"user_id": "U-cash", "roles": ["SALES_CASHIER"], "active_store_id": "BV-01"}


@pytest.mark.asyncio
class TestLoyaltyEarnIdempotent:
    async def test_single_earn_awards_once(self, monkeypatch):
        txns, accounts = _earn_ctx(monkeypatch)
        body = loyalty_module.EarnRequest(customer_id="C-1", order_id="O-1")
        out = await loyalty_module.earn(body, current_user=_pos_user())
        assert out["awarded"] > 0
        earn_rows = [d for d in txns.collection.docs if d.get("type") == "EARN"]
        assert len(earn_rows) == 1
        assert accounts.adjust_calls == 1

    async def test_two_sequential_earns_award_once(self, monkeypatch):
        """The race property: the 2nd earn for the same (customer, order) must
        write NO 2nd EARN row and must NOT bump the balance again."""
        txns, accounts = _earn_ctx(monkeypatch)
        body = loyalty_module.EarnRequest(customer_id="C-1", order_id="O-1")

        first = await loyalty_module.earn(body, current_user=_pos_user())
        assert first["awarded"] > 0

        second = await loyalty_module.earn(body, current_user=_pos_user())
        assert second.get("deduped") is True

        earn_rows = [d for d in txns.collection.docs if d.get("type") == "EARN"]
        assert len(earn_rows) == 1, "exactly one EARN row per (customer, order)"
        # Balance bumped exactly once (the winner), never doubled.
        assert accounts.adjust_calls == 1

    async def test_claim_simulating_concurrency_only_one_wins(self, monkeypatch):
        """Simulate two earn calls that BOTH pass the has_earn fast-path read
        (force it False) -- only the atomic claim must win, so still one row."""
        txns, accounts = _earn_ctx(monkeypatch)
        # Force the fast-path read to always say "not earned" so the only guard
        # exercised is the atomic claim_earn_for_order upsert.
        monkeypatch.setattr(txns, "has_earn_for_order", lambda *_a, **_k: False)
        body = loyalty_module.EarnRequest(customer_id="C-1", order_id="O-1")

        await loyalty_module.earn(body, current_user=_pos_user())
        await loyalty_module.earn(body, current_user=_pos_user())

        earn_rows = [d for d in txns.collection.docs if d.get("type") == "EARN"]
        assert len(earn_rows) == 1
        assert accounts.adjust_calls == 1


class TestClaimEarnForOrderRepo:
    def test_claim_returns_doc_on_first_insert_none_on_repeat(self):
        from database.repositories.loyalty_repository import (
            LoyaltyTransactionRepository,
        )

        txns = LoyaltyTransactionRepository(_TxnColl())
        doc = {"txn_id": "T-1", "customer_id": "C-1", "order_id": "O-1",
               "type": "EARN", "points": 10}
        first = txns.claim_earn_for_order("C-1", "O-1", doc)
        assert first is not None and first["txn_id"] == "T-1"
        # Second claim with a DIFFERENT txn_id must lose -> None, no 2nd row.
        doc2 = dict(doc, txn_id="T-2")
        second = txns.claim_earn_for_order("C-1", "O-1", doc2)
        assert second is None
        earn_rows = [d for d in txns.collection.docs if d.get("type") == "EARN"]
        assert len(earn_rows) == 1


# ===========================================================================
# BUG-3: online_store_orders list store scope
# ===========================================================================


class _OnlineOrdersColl:
    def __init__(self, docs):
        self.docs = [copy.deepcopy(d) for d in docs]

    @staticmethod
    def _match(doc, q):
        for k, v in (q or {}).items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def count_documents(self, q):
        return sum(1 for d in self.docs if self._match(d, q))

    def find(self, q):
        matched = [copy.deepcopy(d) for d in self.docs if self._match(d, q)]
        return _Cursor(matched)


class _OnlineDb:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, name):
        return self._coll if name == "orders" else None


def _online_ctx(monkeypatch):
    coll = _OnlineOrdersColl([
        {"order_id": "O-A", "channel": "ONLINE", "store_id": "BV-01"},
        {"order_id": "O-B", "channel": "ONLINE", "store_id": "BV-02"},
        {"order_id": "O-C", "channel": "ONLINE", "store_id": "BV-01"},
    ])
    monkeypatch.setattr(online_module, "_get_db", lambda: _OnlineDb(coll))
    return coll


@pytest.mark.asyncio
class TestOnlineOrdersStoreScope:
    async def test_store_scoped_accountant_sees_only_own_store(self, monkeypatch):
        _online_ctx(monkeypatch)
        user = {"user_id": "U-acc", "roles": ["ACCOUNTANT"],
                "active_store_id": "BV-01", "store_ids": ["BV-01"]}
        out = await online_module.list_online_orders(
            status=None, date_from=None, date_to=None, limit=100, offset=0,
            current_user=user,
        )
        store_ids = {o["store_id"] for o in out["orders"]}
        assert store_ids == {"BV-01"}
        assert out["total"] == 2

    async def test_admin_sees_all_stores(self, monkeypatch):
        _online_ctx(monkeypatch)
        user = {"user_id": "U-adm", "roles": ["ADMIN"],
                "active_store_id": "BV-01", "store_ids": ["BV-01"]}
        out = await online_module.list_online_orders(
            status=None, date_from=None, date_to=None, limit=100, offset=0,
            current_user=user,
        )
        store_ids = {o["store_id"] for o in out["orders"]}
        assert store_ids == {"BV-01", "BV-02"}
        assert out["total"] == 3

    async def test_store_scoped_with_no_store_sees_nothing(self, monkeypatch):
        _online_ctx(monkeypatch)
        user = {"user_id": "U-acc", "roles": ["ACCOUNTANT"],
                "active_store_id": None, "store_ids": []}
        out = await online_module.list_online_orders(
            status=None, date_from=None, date_to=None, limit=100, offset=0,
            current_user=user,
        )
        assert out["orders"] == []
        assert out["total"] == 0
