"""
IMS 2.0 - IDOR / value-trust hardening tests: loyalty + vouchers
=================================================================
Locks the fixes for two audit findings:

P1 (loyalty.py): POST /loyalty/earn + /redeem were AUTHENTICATED-only and
   earn TRUSTED the caller-supplied rupee_value -- any role could mint or
   drain any customer's points for any amount. Now:
     * both gated to the POS money family (loyalty._POS_ROLES);
     * earn derives its rupee basis SERVER-SIDE from the order
       (grand_total - tax_amount); an inflated client value is clamped, a
       lower one honoured; no order_id -> 400; foreign order -> 400;
     * the atomic redeem debit is untouched (regression-covered elsewhere).

P2 (vouchers.py): POST /{code}/cancel let a STORE_MANAGER cancel ANY store's
   gift card, and POST / accepted an arbitrary store_id. Now:
     * cancel requires can_access_store_scoped(voucher.store_id) for non-HQ
       actors (ADMIN/SUPERADMIN bypass);
     * issue validates body.store_id via validate_store_access;
     * REDEEM deliberately stays chain-wide (gift cards redeem at any store).

CI-robustness: EVERY repo/DB accessor the routes touch is monkeypatched and
all documents are seeded explicitly -- no dependence on CI's shared Mongo,
and no whole-JSON substring assertions (field-level asserts only).
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")


# ============================================================================
# Token helpers (real JWTs -- the RBAC middleware and route gates both run)
# ============================================================================


def _headers(roles: List[str], store: str = "BV-01", stores: Optional[List[str]] = None):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": f"u-{'-'.join(r.lower() for r in roles)}-{store.lower()}",
            "username": "tester",
            "roles": roles,
            "store_ids": stores if stores is not None else [store],
            "active_store_id": store,
        }
    )
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# Minimal Mongo fakes (per-document-atomic find_one_and_update)
# ============================================================================


def _matches(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, operand in cond.items():
                if op == "$gte" and not (val is not None and val >= operand):
                    return False
                if op == "$lte" and not (val is not None and val <= operand):
                    return False
                if op == "$gt" and not (val is not None and val > operand):
                    return False
                if op == "$lt" and not (val is not None and val < operand):
                    return False
                if op == "$ne" and val == operand:
                    return False
        elif val != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, _n):
        return self

    def limit(self, _n):
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeColl:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def create_index(self, *_a, **_k):
        return "idx"

    def find_one(self, flt=None, projection=None):
        for d in self.docs:
            if _matches(d, flt or {}):
                return dict(d)
        return None

    def find(self, flt=None, projection=None):
        return _Cursor(dict(d) for d in self.docs if _matches(d, flt or {}))

    def count_documents(self, flt=None):
        return sum(1 for d in self.docs if _matches(d, flt or {}))

    @staticmethod
    def _apply(d, update):
        for k, v in (update.get("$set") or {}).items():
            d[k] = v
        for k, v in (update.get("$inc") or {}).items():
            d[k] = (d.get(k) or 0) + v
        for k, v in (update.get("$push") or {}).items():
            d.setdefault(k, []).append(v)

    def update_one(self, flt, update):
        for d in self.docs:
            if _matches(d, flt):
                self._apply(d, update)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find_one_and_update(self, flt, update, return_document=None, **_k):
        for d in self.docs:
            if _matches(d, flt):
                before = dict(d)
                self._apply(d, update)
                return dict(d) if return_document else before
        return None


class FakeOrderRepo:
    def __init__(self):
        self._orders: Dict[str, Dict[str, Any]] = {}

    def seed(self, order_id, customer_id, grand_total, tax_amount=0.0):
        self._orders[order_id] = {
            "order_id": order_id,
            "customer_id": customer_id,
            "grand_total": float(grand_total),
            "tax_amount": float(tax_amount),
        }

    def find_by_id(self, order_id):
        return self._orders.get(order_id)


# ============================================================================
# Fixtures -- monkeypatch EVERY accessor the two routers use
# ============================================================================


@pytest.fixture
def loyalty_ctx(monkeypatch):
    """Real loyalty repositories over in-memory fakes, wired into the router."""
    from api.routers import loyalty as loyalty_module
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltySettingsRepository,
        LoyaltyTransactionRepository,
    )

    accounts = LoyaltyAccountRepository(FakeColl())
    txns = LoyaltyTransactionRepository(FakeColl())
    settings = LoyaltySettingsRepository(FakeColl())
    orders = FakeOrderRepo()

    monkeypatch.setattr(
        loyalty_module, "get_loyalty_account_repository", lambda: accounts
    )
    monkeypatch.setattr(
        loyalty_module, "get_loyalty_transaction_repository", lambda: txns
    )
    monkeypatch.setattr(
        loyalty_module, "get_loyalty_settings_repository", lambda: settings
    )
    monkeypatch.setattr(loyalty_module, "get_audit_repository", lambda: None)
    monkeypatch.setattr(loyalty_module, "get_order_repository", lambda: orders)

    return {"accounts": accounts, "txns": txns, "orders": orders}


@pytest.fixture
def voucher_ctx(monkeypatch):
    """Fake vouchers collection wired into the router; seed() mints ACTIVE
    voucher docs in the canonical shape."""
    from api.routers import vouchers as vouchers_module

    store = FakeColl()

    class _DB:
        def get_collection(self, name):
            assert name == "vouchers"
            return store

    monkeypatch.setattr(vouchers_module, "_get_db", lambda: _DB())

    def seed(code, amount, store_id, status="ACTIVE"):
        doc = {
            "voucher_id": f"v-{code.lower()}",
            "code": code,
            "type": "GIFT_CARD",
            "initial_amount": float(amount),
            "balance": float(amount),
            "currency": "INR",
            "status": status,
            "store_id": store_id,
            "issued_to_customer_id": None,
            "issued_by": "seed",
            "expiry_date": None,
            "redemptions": [],
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        store.insert_one(doc)
        return doc

    return {"coll": store, "seed": seed}


def _earn_ledger(ctx, customer_id):
    return [
        d
        for d in ctx["txns"].collection.docs
        if d.get("customer_id") == customer_id and d.get("type") == "EARN"
    ]


def _seed_balance(ctx, customer_id, points):
    ctx["accounts"].find_or_create(customer_id)
    ctx["accounts"].adjust_balance(
        customer_id, delta_points=points, delta_lifetime_earned=points
    )


# ============================================================================
# P1a -- role gates on earn / redeem
# ============================================================================


class TestLoyaltyRoleGates:
    def test_earn_403_for_optometrist(self, client, loyalty_ctx):
        loyalty_ctx["orders"].seed("ORD-G1", "cust-g1", 1000.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-g1", "order_id": "ORD-G1"},
            headers=_headers(["OPTOMETRIST"]),
        )
        assert r.status_code == 403
        assert _earn_ledger(loyalty_ctx, "cust-g1") == []

    def test_earn_403_for_workshop_staff(self, client, loyalty_ctx):
        loyalty_ctx["orders"].seed("ORD-G2", "cust-g2", 1000.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-g2", "order_id": "ORD-G2"},
            headers=_headers(["WORKSHOP_STAFF"]),
        )
        assert r.status_code == 403
        assert _earn_ledger(loyalty_ctx, "cust-g2") == []

    def test_redeem_403_for_optometrist_balance_untouched(self, client, loyalty_ctx):
        _seed_balance(loyalty_ctx, "cust-g3", 500)
        r = client.post(
            "/api/v1/loyalty/redeem",
            json={"customer_id": "cust-g3", "points": 200, "order_value": 5000.0},
            headers=_headers(["OPTOMETRIST"]),
        )
        assert r.status_code == 403
        acct = loyalty_ctx["accounts"].find_by_id("cust-g3")
        assert acct["balance_points"] == 500

    def test_earn_allowed_for_sales_cashier(self, client, loyalty_ctx):
        loyalty_ctx["orders"].seed("ORD-G4", "cust-g4", 1000.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-g4", "order_id": "ORD-G4"},
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["awarded"] == 10  # 1% of the order's 1000 basis

    def test_redeem_allowed_for_cashier(self, client, loyalty_ctx):
        _seed_balance(loyalty_ctx, "cust-g5", 500)
        r = client.post(
            "/api/v1/loyalty/redeem",
            json={"customer_id": "cust-g5", "points": 200, "order_value": 5000.0},
            headers=_headers(["CASHIER"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["redeemed_points"] == 200
        acct = loyalty_ctx["accounts"].find_by_id("cust-g5")
        assert acct["balance_points"] == 300


# ============================================================================
# P1b -- server-derived earn basis (client value never trusted upward)
# ============================================================================


class TestEarnValueDerivedFromOrder:
    def test_inflated_client_value_clamped_to_order_basis(self, client, loyalty_ctx):
        # Order: grand_total 1180 incl. 180 GST -> taxable basis 1000.
        loyalty_ctx["orders"].seed("ORD-V1", "cust-v1", 1180.0, tax_amount=180.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={
                "customer_id": "cust-v1",
                "order_id": "ORD-V1",
                "rupee_value": 9999999.0,  # adversarial: would mint ~100k points
            },
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["awarded"] == 10  # 1% of 1000, NOT of 9,999,999
        assert body["rupee_value"] == 1000.0
        assert body["value_clamped"] is True
        rows = _earn_ledger(loyalty_ctx, "cust-v1")
        assert len(rows) == 1
        assert rows[0]["rupee_value"] == 1000.0
        acct = loyalty_ctx["accounts"].find_by_id("cust-v1")
        assert acct["balance_points"] == 10

    def test_omitted_client_value_uses_order_basis(self, client, loyalty_ctx):
        loyalty_ctx["orders"].seed("ORD-V2", "cust-v2", 1180.0, tax_amount=180.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-v2", "order_id": "ORD-V2"},
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["awarded"] == 10
        assert r.json()["value_clamped"] is False

    def test_lower_client_value_is_honoured(self, client, loyalty_ctx):
        loyalty_ctx["orders"].seed("ORD-V3", "cust-v3", 1000.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={
                "customer_id": "cust-v3",
                "order_id": "ORD-V3",
                "rupee_value": 500.0,  # partial award stays allowed
            },
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["awarded"] == 5
        assert r.json()["rupee_value"] == 500.0

    def test_earn_without_order_id_rejected(self, client, loyalty_ctx):
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-v4", "rupee_value": 100000.0},
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 400
        assert _earn_ledger(loyalty_ctx, "cust-v4") == []

    def test_earn_unknown_order_404(self, client, loyalty_ctx):
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-v5", "order_id": "ORD-NOPE"},
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 404
        assert _earn_ledger(loyalty_ctx, "cust-v5") == []

    def test_earn_foreign_customers_order_rejected(self, client, loyalty_ctx):
        # The order belongs to cust-owner; minting points for cust-thief off it
        # must fail (and would otherwise also dodge the (customer, order)
        # idempotency key).
        loyalty_ctx["orders"].seed("ORD-V6", "cust-owner", 50000.0)
        r = client.post(
            "/api/v1/loyalty/earn",
            json={"customer_id": "cust-thief", "order_id": "ORD-V6"},
            headers=_headers(["SALES_CASHIER"]),
        )
        assert r.status_code == 400
        assert _earn_ledger(loyalty_ctx, "cust-thief") == []


# ============================================================================
# P2 -- voucher cancel store scope + issue store validation
# ============================================================================


class TestVoucherCancelScope:
    def test_cross_store_manager_cannot_cancel(self, client, voucher_ctx):
        voucher_ctx["seed"]("GC-XSTORE1", 1000.0, "BV-02")
        r = client.post(
            "/api/v1/vouchers/GC-XSTORE1/cancel",
            headers=_headers(["STORE_MANAGER"], store="BV-01"),
        )
        assert r.status_code == 403
        doc = voucher_ctx["coll"].find_one({"code": "GC-XSTORE1"})
        assert doc["status"] == "ACTIVE"  # untouched

    def test_same_store_manager_can_cancel(self, client, voucher_ctx):
        voucher_ctx["seed"]("GC-SAME1", 1000.0, "BV-01")
        r = client.post(
            "/api/v1/vouchers/GC-SAME1/cancel",
            headers=_headers(["STORE_MANAGER"], store="BV-01"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"

    def test_admin_can_cancel_any_store(self, client, voucher_ctx):
        voucher_ctx["seed"]("GC-ADM1", 1000.0, "BV-02")
        r = client.post(
            "/api/v1/vouchers/GC-ADM1/cancel",
            headers=_headers(["ADMIN"], store="BV-01"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"

    def test_unattributed_voucher_is_admin_only(self, client, voucher_ctx):
        # store_id None: a store-level role must NOT be able to cancel it.
        voucher_ctx["seed"]("GC-NOSTORE", 1000.0, None)
        r = client.post(
            "/api/v1/vouchers/GC-NOSTORE/cancel",
            headers=_headers(["STORE_MANAGER"], store="BV-01"),
        )
        assert r.status_code == 403
        r2 = client.post(
            "/api/v1/vouchers/GC-NOSTORE/cancel",
            headers=_headers(["ADMIN"], store="BV-01"),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "CANCELLED"

    def test_unknown_code_still_404(self, client, voucher_ctx):
        r = client.post(
            "/api/v1/vouchers/GC-MISSING/cancel",
            headers=_headers(["ADMIN"]),
        )
        assert r.status_code == 404


class TestVoucherIssueStoreValidation:
    def test_issue_with_foreign_store_403(self, client, voucher_ctx):
        r = client.post(
            "/api/v1/vouchers",
            json={"amount": 500.0, "store_id": "BV-02"},
            headers=_headers(["STORE_MANAGER"], store="BV-01"),
        )
        assert r.status_code == 403
        assert voucher_ctx["coll"].count_documents({}) == 0  # nothing minted

    def test_issue_own_store_ok(self, client, voucher_ctx):
        r = client.post(
            "/api/v1/vouchers",
            json={"amount": 500.0, "store_id": "BV-01"},
            headers=_headers(["STORE_MANAGER"], store="BV-01"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["store_id"] == "BV-01"
        assert r.json()["status"] == "ACTIVE"

    def test_issue_defaults_to_active_store(self, client, voucher_ctx):
        r = client.post(
            "/api/v1/vouchers",
            json={"amount": 500.0},
            headers=_headers(["STORE_MANAGER"], store="BV-01"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["store_id"] == "BV-01"

    def test_admin_can_issue_for_any_store(self, client, voucher_ctx):
        r = client.post(
            "/api/v1/vouchers",
            json={"amount": 500.0, "store_id": "BV-02"},
            headers=_headers(["ADMIN"], store="BV-01"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["store_id"] == "BV-02"


class TestVoucherRedeemStaysChainWide:
    def test_redeem_at_another_store_still_works(self, client, voucher_ctx):
        """Regression: gift cards are redeemable at ANY store by design --
        the cancel/issue scoping must NOT leak into redeem."""
        voucher_ctx["seed"]("GC-CHAIN1", 1000.0, "BV-01")
        r = client.post(
            "/api/v1/vouchers/GC-CHAIN1/redeem",
            json={"amount": 400.0, "order_id": "O-CHAIN"},
            headers=_headers(["SALES_CASHIER"], store="BV-02"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["redeemed"] == 400.0
        assert body["balance"] == 600.0
        doc = voucher_ctx["coll"].find_one({"code": "GC-CHAIN1"})
        assert doc["balance"] == 600.0
        assert len(doc["redemptions"]) == 1


# ============================================================================
# Policy registry rows -- drift locks
# ============================================================================


class TestPolicyRows:
    _POS_SET = {
        "ADMIN",
        "AREA_MANAGER",
        "CASHIER",
        "SALES_CASHIER",
        "SALES_STAFF",
        "STORE_MANAGER",
    }

    def test_loyalty_earn_row_is_pos_role_list(self):
        from api.services import rbac_policy as rbac

        entry = rbac.policy_for("POST", "/api/v1/loyalty/earn")
        assert entry is not None
        assert entry["allowed"] != rbac.AUTHENTICATED
        assert set(entry["allowed"]) == self._POS_SET

    def test_loyalty_redeem_row_is_pos_role_list(self):
        from api.services import rbac_policy as rbac

        entry = rbac.policy_for("POST", "/api/v1/loyalty/redeem")
        assert entry is not None
        assert entry["allowed"] != rbac.AUTHENTICATED
        assert set(entry["allowed"]) == self._POS_SET

    def test_check_access_denies_clinical_roles(self):
        from api.services import rbac_policy as rbac

        for role in ("OPTOMETRIST", "WORKSHOP_STAFF", "CATALOG_MANAGER"):
            assert rbac.check_access("POST", "/api/v1/loyalty/earn", [role]) is False
            assert rbac.check_access("POST", "/api/v1/loyalty/redeem", [role]) is False
        # POS + HQ still pass.
        for role in ("SALES_CASHIER", "CASHIER", "STORE_MANAGER", "SUPERADMIN"):
            assert rbac.check_access("POST", "/api/v1/loyalty/earn", [role]) is True
            assert rbac.check_access("POST", "/api/v1/loyalty/redeem", [role]) is True

    def test_voucher_write_rows_flagged_store_scoped(self):
        from api.services import rbac_policy as rbac

        assert rbac.is_store_scoped("POST", "/api/v1/vouchers") is True
        assert rbac.is_store_scoped("POST", "/api/v1/vouchers/GC-X/cancel") is True
        # Redeem is chain-wide by design -- must NOT be store_scoped.
        assert rbac.is_store_scoped("POST", "/api/v1/vouchers/GC-X/redeem") is False
