"""
IMS 2.0 - Voucher / gift-card tests (Stage 1)
==============================================
This is MONEY, so the suite leans hard on the redeem guard.

Two layers:
  1. HTTP-level via TestClient (client / auth_headers fixtures): auth
     gates, validation envelopes, and DB-less fail-soft behavior.
  2. Logic-level with a FakeVouchers collection patched into the router
     module. The fake implements find_one_and_update with the SAME
     match-then-modify atomicity Mongo gives us, so we can prove:
        - issue creates ACTIVE with balance == amount
        - get on unknown code -> valid False
        - partial redeem leaves correct balance + ACTIVE
        - full redeem flips to REDEEMED
        - over-balance redeem -> 400, balance untouched
        - expired / cancelled / redeemed -> rejected
        - a double-redeem race can't overspend (second redeem of the
          last rupees finds nothing to match and fails)

The fake is the key piece: find_one_and_update only mutates a doc whose
fields satisfy the filter at modify time. That is exactly the property
the production guard relies on, so the race test is meaningful.
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers import vouchers as vouchers_module


# ============================================================================
# Fake Mongo collection with atomic find_one_and_update semantics
# ============================================================================


def _matches(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    """Tiny subset of Mongo's query matcher: equality, $gte/$lte/$lt/$gt,
    and a top-level $or of sub-filters. Enough for the voucher guards."""
    for key, cond in flt.items():
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
        else:
            if val != cond:
                return False
    return True


class FakeVouchers:
    """In-memory stand-in for the vouchers pymongo collection.

    find_one_and_update is implemented as match-then-apply on the single
    stored doc, mirroring Mongo's per-document atomicity. Calls are
    serialized (single-threaded test), but because each call re-evaluates
    the filter against the *current* doc state, a second racing redeem of
    already-spent balance simply won't match — which is the whole point.
    """

    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        self._docs: List[Dict[str, Any]] = [copy.deepcopy(d) for d in (docs or [])]

    # --- write ---
    def insert_one(self, doc: Dict[str, Any]):
        for existing in self._docs:
            if existing.get("code") == doc.get("code"):
                raise _make_dup_key(doc.get("code"))
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("voucher_id")})()

    def create_index(self, *_args, **_kwargs):
        return "code_1"

    def find_one(self, flt: Dict[str, Any]):
        for d in self._docs:
            if _matches(d, flt):
                return copy.deepcopy(d)
        return None

    def find_one_and_update(self, flt, update, return_document=None):
        for d in self._docs:
            if not _matches(d, flt):
                continue
            # Apply $inc, $push, $set in place (atomic on this doc).
            for field, delta in update.get("$inc", {}).items():
                d[field] = (d.get(field) or 0) + delta
            for field, value in update.get("$push", {}).items():
                d.setdefault(field, []).append(value)
            for field, value in update.get("$set", {}).items():
                d[field] = value
            return copy.deepcopy(d)
        return None

    # --- read ---
    def find(self, flt: Optional[Dict[str, Any]] = None):
        flt = flt or {}
        matched = [copy.deepcopy(d) for d in self._docs if _matches(d, flt)]
        return _FakeCursor(matched)


class _FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, *_args, **_kwargs):
        return self

    def __iter__(self):
        return iter(self._docs)


def _make_dup_key(code):
    try:
        from pymongo.errors import DuplicateKeyError

        return DuplicateKeyError(f"E11000 duplicate key error: code {code}")
    except Exception:  # pragma: no cover
        return Exception(f"E11000 duplicate key error: code {code}")


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def fake_db(monkeypatch):
    """Patch the router's _get_db so _coll(db) -> our FakeVouchers.

    _coll() prefers db.get_collection() when present; we expose one that
    always returns the same FakeVouchers so state persists across calls.
    """
    store = FakeVouchers()

    class _DB:
        def get_collection(self, name):
            assert name == "vouchers"
            return store

    monkeypatch.setattr(vouchers_module, "_get_db", lambda: _DB())
    return store


def _admin_user() -> Dict[str, Any]:
    return {
        "user_id": "U-admin",
        "username": "admin",
        "roles": ["ADMIN"],
        "active_store_id": "BV-01",
    }


def _cashier_user() -> Dict[str, Any]:
    return {
        "user_id": "U-cash",
        "username": "cash",
        "roles": ["SALES_CASHIER"],
        "active_store_id": "BV-01",
    }


async def _issue(amount=1000.0, **kw):
    body = vouchers_module.VoucherCreate(amount=amount, **kw)
    return await vouchers_module.issue_voucher(body, current_user=_admin_user())


async def _redeem(code, amount, order_id=None):
    body = vouchers_module.VoucherRedeem(amount=amount, order_id=order_id)
    return await vouchers_module.redeem_voucher(
        code, body, current_user=_cashier_user()
    )


# ============================================================================
# HTTP-level: auth gates + DB-less fail-soft
# ============================================================================


class TestHttpGatesAndFailSoft:
    def test_issue_requires_auth(self, client):
        resp = client.post("/api/v1/vouchers", json={"amount": 500})
        assert resp.status_code == 401

    def test_redeem_requires_auth(self, client):
        resp = client.post("/api/v1/vouchers/GC-ABC/redeem", json={"amount": 10})
        assert resp.status_code == 401

    def test_list_requires_auth(self, client):
        assert client.get("/api/v1/vouchers").status_code == 401

    def test_cashier_cannot_issue(self, client, staff_headers):
        # SALES_STAFF is not in the issue role set -> 403.
        resp = client.post(
            "/api/v1/vouchers", json={"amount": 500}, headers=staff_headers
        )
        assert resp.status_code == 403

    def test_get_is_open_to_any_staff_dbless(self, client, staff_headers):
        # No DB in the test app -> fail-soft envelope, but auth passes.
        resp = client.get("/api/v1/vouchers/GC-NOPE", headers=staff_headers)
        assert resp.status_code == 200
        assert resp.json()["valid"] is False

    def test_issue_dbless_returns_empty(self, client, auth_headers, monkeypatch):
        # Force the genuinely DB-less path. CI runs the app in stub mode where
        # the db object is truthy (so a write "succeeds" against the stub), so
        # pin _get_db to None to deterministically exercise the fail-soft
        # branch in both local and CI.
        monkeypatch.setattr(vouchers_module, "_get_db", lambda: None)
        resp = client.post(
            "/api/v1/vouchers", json={"amount": 500}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_issue_rejects_zero_amount(self, client, auth_headers):
        resp = client.post(
            "/api/v1/vouchers", json={"amount": 0}, headers=auth_headers
        )
        assert resp.status_code == 422

    def test_list_dbless_envelope(self, client, auth_headers):
        resp = client.get("/api/v1/vouchers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"vouchers": [], "total": 0}

    def test_route_is_mounted(self, app):
        # Introspect via the OpenAPI schema (stable public API) rather than
        # app.routes directly: FastAPI 0.139's router groups included routers
        # into lazy _IncludedRouter wrappers, so app.routes no longer flattens
        # to a plain list of APIRoute objects with a .path attribute.
        paths = app.openapi()["paths"]
        assert "/api/v1/vouchers" in paths
        assert "/api/v1/vouchers/{code}/redeem" in paths


# ============================================================================
# Logic-level with FakeVouchers
# ============================================================================


@pytest.mark.asyncio
class TestVoucherLifecycle:
    async def test_issue_creates_active_with_full_balance(self, fake_db):
        v = await _issue(amount=2500.0)
        assert v["status"] == "ACTIVE"
        assert v["balance"] == 2500.0
        assert v["initial_amount"] == 2500.0
        assert v["currency"] == "INR"
        assert v["type"] == "GIFT_CARD"
        assert v["code"].startswith("GC-")
        assert len(v["code"]) == len("GC-") + 8

    async def test_issue_uppercases_supplied_code(self, fake_db):
        v = await _issue(amount=100.0, code="my-card-01")
        assert v["code"] == "MY-CARD-01"

    async def test_issue_duplicate_code_conflicts(self, fake_db):
        await _issue(amount=100.0, code="DUP1")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            await _issue(amount=100.0, code="DUP1")
        assert ei.value.status_code == 409

    async def test_get_unknown_code_invalid(self, fake_db):
        out = await vouchers_module.get_voucher(
            "GC-MISSING", current_user=_cashier_user()
        )
        assert out["valid"] is False
        assert out["reason"] == "not_found"

    async def test_get_active_is_valid(self, fake_db):
        v = await _issue(amount=300.0)
        out = await vouchers_module.get_voucher(
            v["code"], current_user=_cashier_user()
        )
        assert out["valid"] is True
        assert out["balance"] == 300.0
        assert out["status"] == "ACTIVE"

    async def test_partial_redeem_leaves_balance_active(self, fake_db):
        v = await _issue(amount=1000.0)
        out = await _redeem(v["code"], 400.0, order_id="O-1")
        assert out["redeemed"] == 400.0
        assert out["balance"] == 600.0
        assert out["status"] == "ACTIVE"
        # Redemption recorded on the doc.
        doc = fake_db.find_one({"code": v["code"]})
        assert len(doc["redemptions"]) == 1
        assert doc["redemptions"][0]["order_id"] == "O-1"

    async def test_full_redeem_sets_redeemed(self, fake_db):
        v = await _issue(amount=500.0)
        out = await _redeem(v["code"], 500.0)
        assert out["balance"] == 0.0
        assert out["status"] == "REDEEMED"

    async def test_two_partials_to_zero_sets_redeemed(self, fake_db):
        v = await _issue(amount=1000.0)
        await _redeem(v["code"], 600.0)
        out = await _redeem(v["code"], 400.0)
        assert out["balance"] == 0.0
        assert out["status"] == "REDEEMED"

    async def test_redeem_over_balance_rejected_and_unchanged(self, fake_db):
        from fastapi import HTTPException

        v = await _issue(amount=200.0)
        with pytest.raises(HTTPException) as ei:
            await _redeem(v["code"], 250.0)
        assert ei.value.status_code == 400
        assert "insufficient" in str(ei.value.detail).lower()
        # Balance untouched, still ACTIVE.
        doc = fake_db.find_one({"code": v["code"]})
        assert doc["balance"] == 200.0
        assert doc["status"] == "ACTIVE"
        assert doc["redemptions"] == []

    async def test_redeem_expired_rejected(self, fake_db):
        from fastapi import HTTPException

        yesterday = date.today() - timedelta(days=1)
        v = await _issue(amount=500.0, expiry_date=yesterday)
        with pytest.raises(HTTPException) as ei:
            await _redeem(v["code"], 100.0)
        assert ei.value.status_code == 400
        assert "expired" in str(ei.value.detail).lower()
        doc = fake_db.find_one({"code": v["code"]})
        assert doc["balance"] == 500.0  # untouched

    async def test_get_expired_is_invalid(self, fake_db):
        yesterday = date.today() - timedelta(days=1)
        v = await _issue(amount=500.0, expiry_date=yesterday)
        out = await vouchers_module.get_voucher(
            v["code"], current_user=_cashier_user()
        )
        assert out["valid"] is False
        assert out["reason"] == "expired"

    async def test_redeem_on_expiry_day_allowed(self, fake_db):
        # Expiry is end-of-day: a card expiring today is still redeemable.
        v = await _issue(amount=500.0, expiry_date=date.today())
        out = await _redeem(v["code"], 100.0)
        assert out["balance"] == 400.0

    async def test_cancel_then_redeem_rejected(self, fake_db):
        from fastapi import HTTPException

        v = await _issue(amount=500.0)
        cancelled = await vouchers_module.cancel_voucher(
            v["code"], current_user=_admin_user()
        )
        assert cancelled["status"] == "CANCELLED"
        with pytest.raises(HTTPException) as ei:
            await _redeem(v["code"], 100.0)
        assert ei.value.status_code == 400
        assert "cancel" in str(ei.value.detail).lower()

    async def test_redeem_fully_redeemed_card_rejected(self, fake_db):
        from fastapi import HTTPException

        v = await _issue(amount=500.0)
        await _redeem(v["code"], 500.0)  # now REDEEMED
        with pytest.raises(HTTPException) as ei:
            await _redeem(v["code"], 50.0)
        assert ei.value.status_code == 400

    async def test_cancel_already_cancelled_rejected(self, fake_db):
        from fastapi import HTTPException

        v = await _issue(amount=500.0)
        await vouchers_module.cancel_voucher(v["code"], current_user=_admin_user())
        with pytest.raises(HTTPException) as ei:
            await vouchers_module.cancel_voucher(
                v["code"], current_user=_admin_user()
            )
        assert ei.value.status_code == 400

    async def test_cancel_unknown_404(self, fake_db):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            await vouchers_module.cancel_voucher(
                "GC-NOPE", current_user=_admin_user()
            )
        assert ei.value.status_code == 404

    async def test_list_filters_by_status(self, fake_db):
        a = await _issue(amount=100.0)
        await _issue(amount=200.0)
        await vouchers_module.cancel_voucher(a["code"], current_user=_admin_user())
        out = await vouchers_module.list_vouchers(
            store_id=None, status="ACTIVE", current_user=_admin_user()
        )
        assert out["total"] == 1
        assert out["vouchers"][0]["status"] == "ACTIVE"


@pytest.mark.asyncio
class TestNoDoubleSpend:
    async def test_concurrent_redeem_cannot_overspend(self, fake_db):
        """Two redemptions that TOGETHER exceed the balance: the first
        succeeds, the second must fail. Because the guard is in the
        find_one_and_update filter, by the time the second runs the doc no
        longer satisfies balance>=amount, so it matches nothing."""
        from fastapi import HTTPException

        v = await _issue(amount=100.0)
        # First redeem takes 70 -> balance 30.
        first = await _redeem(v["code"], 70.0, order_id="O-A")
        assert first["balance"] == 30.0
        # Second tries to take 70 again -> only 30 left -> rejected.
        with pytest.raises(HTTPException) as ei:
            await _redeem(v["code"], 70.0, order_id="O-B")
        assert ei.value.status_code == 400
        # Net spend is exactly 70, never 140.
        doc = fake_db.find_one({"code": v["code"]})
        assert doc["balance"] == 30.0
        assert len(doc["redemptions"]) == 1

    async def test_exact_drain_then_second_fails(self, fake_db):
        from fastapi import HTTPException

        v = await _issue(amount=100.0)
        out = await _redeem(v["code"], 100.0, order_id="O-A")
        assert out["balance"] == 0.0
        assert out["status"] == "REDEEMED"
        with pytest.raises(HTTPException):
            await _redeem(v["code"], 0.01, order_id="O-B")
        doc = fake_db.find_one({"code": v["code"]})
        assert doc["balance"] == 0.0
        assert len(doc["redemptions"]) == 1
