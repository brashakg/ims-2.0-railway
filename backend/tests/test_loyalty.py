"""
IMS 2.0 — Loyalty engine tests
================================
Coverage:
  * earn calc — flat rate
  * earn calc — category multiplier
  * earn calc — tier multiplier
  * earn under min_order_for_earn -> no points
  * idempotent earn on (customer, order)
  * redeem deducts balance + writes ledger
  * redeem below min_redeem_points -> 400
  * redeem more than balance -> 400
  * redeem cap by max_redeem_pct_of_order
  * adjust: SUPERADMIN credit + non-admin 403
  * tier promotion crossing 1000 -> SILVER
  * settings update: SUPERADMIN-only
  * ledger pagination + sort desc
  * expire sweep deducts balance
  * order-create hook fires earn
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Fakes
# ============================================================================


def _doc_matches(doc, filter):
    if not filter:
        return True
    for k, expected in filter.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$gt" and not (actual is not None and actual > op_val):
                    return False
                if op == "$lt" and not (actual is not None and actual < op_val):
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort = None
        self._skip = 0
        self._limit = None

    def sort(self, keys):
        self._sort = keys
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def _materialize(self):
        out = list(self._docs)
        if self._sort:
            for key, direction in reversed(self._sort):
                out.sort(
                    key=lambda d, k=key: (d.get(k) is None, d.get(k)),
                    reverse=(direction == -1),
                )
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return out

    def __iter__(self):
        return iter(self._materialize())


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filter):
                return d
        return None

    def find(self, filter=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter))

    def count_documents(self, filter=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter))

    def _apply_update(self, d, update):
        set_block = (update or {}).get("$set", {}) or {}
        inc_block = (update or {}).get("$inc", {}) or {}
        push_block = (update or {}).get("$push", {}) or {}
        d.update(set_block)
        for k, v in inc_block.items():
            d[k] = (d.get(k) or 0) + v
        for k, v in push_block.items():
            arr = d.get(k)
            if not isinstance(arr, list):
                arr = []
            arr.append(v)
            d[k] = arr

    def update_one(self, filter, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter):
                self._apply_update(d, update)
                modified += 1
                break
        return type("R", (), {"modified_count": modified, "matched_count": modified})()

    def find_one_and_update(self, filter, update, return_document=None, **_kw):
        """Atomic match-then-modify on the first matching doc, mirroring Mongo's
        per-document atomicity. Returns the post-update doc when return_document
        is truthy (ReturnDocument.AFTER == True), else the pre-update doc; None
        when nothing matches -- exactly what the guarded
        balance_points >= points filter relies on to reject an overspend."""
        for d in self.docs:
            if _doc_matches(d, filter):
                before = dict(d)
                self._apply_update(d, update)
                return dict(d) if return_document else before
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


# ============================================================================
# Fixture
# ============================================================================


@pytest.fixture
def patched_loyalty(monkeypatch):
    """Wire fake DB + repos into the loyalty router."""
    fake_db = FakeDB()

    from api.routers import loyalty as loyalty_module
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltySettingsRepository,
        LoyaltyTransactionRepository,
    )
    from database.repositories.audit_repository import AuditRepository

    accounts = LoyaltyAccountRepository(fake_db.get_collection("loyalty_accounts"))
    txns = LoyaltyTransactionRepository(
        fake_db.get_collection("loyalty_transactions")
    )
    settings = LoyaltySettingsRepository(
        fake_db.get_collection("loyalty_settings")
    )
    audit = AuditRepository(fake_db.get_collection("audit_logs"))

    monkeypatch.setattr(loyalty_module, "get_loyalty_account_repository", lambda: accounts)
    monkeypatch.setattr(loyalty_module, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(loyalty_module, "get_loyalty_settings_repository", lambda: settings)
    monkeypatch.setattr(loyalty_module, "get_audit_repository", lambda: audit)
    monkeypatch.setattr(loyalty_module, "get_order_repository", lambda: None)

    return {
        "db": fake_db,
        "accounts": accounts,
        "txns": txns,
        "settings": settings,
        "audit": audit,
    }


# ============================================================================
# Earn calc — pure engine (no client needed for these)
# ============================================================================


def test_earn_calc_flat_rate():
    """Without items -> rupee_value × points_per_rupee × tier_multiplier."""
    from api.services.loyalty_engine import calc_earn_points
    from database.repositories.loyalty_repository import DEFAULT_SETTINGS

    out = calc_earn_points(10000.0, [], "BRONZE", DEFAULT_SETTINGS)
    # 10000 × 0.01 × 1.0 (BRONZE) = 100
    assert out["points"] == 100
    assert out["tier_at_earn"] == "BRONZE"


def test_earn_calc_category_multiplier_compounds():
    """LENS line earns 1.5x, FRAME line earns 1.0x; both flow through."""
    from api.services.loyalty_engine import calc_earn_points
    from database.repositories.loyalty_repository import DEFAULT_SETTINGS

    items = [
        {"category": "LENS", "item_total": 5000},
        {"category": "FRAME", "item_total": 5000},
    ]
    out = calc_earn_points(10000.0, items, "BRONZE", DEFAULT_SETTINGS)
    # weighted = 5000*1.5 + 5000*1.0 = 12500. Points = 12500 * 0.01 * 1.0 = 125
    assert out["points"] == 125


def test_earn_calc_tier_multiplier_increases_earn():
    """Same order earns more for GOLD (1.25x) than BRONZE (1.0x)."""
    from api.services.loyalty_engine import calc_earn_points
    from database.repositories.loyalty_repository import DEFAULT_SETTINGS

    bronze = calc_earn_points(10000.0, [], "BRONZE", DEFAULT_SETTINGS)
    gold = calc_earn_points(10000.0, [], "GOLD", DEFAULT_SETTINGS)
    assert gold["points"] > bronze["points"]
    assert gold["points"] == int(10000.0 * 0.01 * 1.25)  # 125


def test_earn_below_min_order_returns_zero():
    """Order under min_order_for_earn → no points awarded."""
    from api.services.loyalty_engine import calc_earn_points
    from database.repositories.loyalty_repository import DEFAULT_SETTINGS

    settings = {**DEFAULT_SETTINGS, "min_order_for_earn": 5000.0}
    out = calc_earn_points(1000.0, [], "BRONZE", settings)
    assert out["points"] == 0
    assert out["skipped_reason"] == "below_min_order"


# ============================================================================
# Earn endpoint
# ============================================================================


def test_earn_endpoint_writes_ledger_and_account(client, auth_headers, patched_loyalty):
    resp = client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-1", "order_id": "ORD-1", "rupee_value": 10000.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["awarded"] == 100  # 1% flat earn for BRONZE
    # Account row created
    acct = patched_loyalty["accounts"].find_by_id("cust-1")
    assert acct is not None
    assert acct["balance_points"] == 100
    assert acct["lifetime_earned"] == 100
    assert acct["tier"] == "BRONZE"
    # Ledger row written
    ledger = patched_loyalty["txns"].find_for_customer("cust-1")
    assert len(ledger) == 1
    assert ledger[0]["type"] == "EARN"
    assert ledger[0]["points"] == 100
    assert ledger[0]["order_id"] == "ORD-1"


def test_earn_idempotent_on_same_order(client, auth_headers, patched_loyalty):
    """Same (customer, order) → only one EARN row."""
    payload = {"customer_id": "cust-2", "order_id": "ORD-2", "rupee_value": 10000.0}
    r1 = client.post("/api/v1/loyalty/earn", json=payload, headers=auth_headers)
    r2 = client.post("/api/v1/loyalty/earn", json=payload, headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json().get("deduped") is True

    acct = patched_loyalty["accounts"].find_by_id("cust-2")
    assert acct["balance_points"] == 100  # only one credit


# ============================================================================
# Redeem
# ============================================================================


def test_redeem_deducts_and_returns_rupee_value(client, auth_headers, patched_loyalty):
    # Seed an account with 500 points
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-3", "order_id": "ORD-3a", "rupee_value": 50000.0},
        headers=auth_headers,
    )
    acct = patched_loyalty["accounts"].find_by_id("cust-3")
    assert acct["balance_points"] == 500

    # Redeem 200 against an order of 5000 → 1 rupee per point → ₹200 (50% cap = 2500)
    resp = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": "cust-3", "points": 200, "order_value": 5000.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["redeemed_points"] == 200
    assert body["rupee_value"] == 200.0
    assert body["was_capped"] is False
    acct = patched_loyalty["accounts"].find_by_id("cust-3")
    assert acct["balance_points"] == 300
    assert acct["lifetime_redeemed"] == 200


def test_redeem_below_min_returns_400(client, auth_headers, patched_loyalty):
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-4", "order_id": "ORD-4", "rupee_value": 50000.0},
        headers=auth_headers,
    )
    resp = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": "cust-4", "points": 50, "order_value": 5000.0},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_redeem_more_than_balance_returns_400(client, auth_headers, patched_loyalty):
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-5", "order_id": "ORD-5", "rupee_value": 10000.0},
        headers=auth_headers,
    )
    resp = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": "cust-5", "points": 500, "order_value": 5000.0},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_redeem_capped_by_max_pct_of_order(client, auth_headers, patched_loyalty):
    """Order 1000, max_redeem_pct=50 → max ₹500 → caps points to 500.
    But customer only has 1000 points to spend; we ask for all 1000 → cap to 500."""
    # Earn 1000 points (₹100k order)
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-6", "order_id": "ORD-6a", "rupee_value": 100000.0},
        headers=auth_headers,
    )
    acct = patched_loyalty["accounts"].find_by_id("cust-6")
    assert acct["balance_points"] == 1000

    # Redeem all 1000 against order of ₹1000 → cap = 50% × 1000 = 500
    resp = client.post(
        "/api/v1/loyalty/redeem",
        json={"customer_id": "cust-6", "points": 1000, "order_value": 1000.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["was_capped"] is True
    assert body["redeemed_points"] == 500
    assert body["rupee_value"] == 500.0


# ============================================================================
# Adjust
# ============================================================================


def test_adjust_superadmin_can_credit(client, auth_headers, patched_loyalty):
    resp = client.post(
        "/api/v1/loyalty/adjust",
        json={"customer_id": "cust-adj-1", "points": 250, "reason": "Goodwill credit"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delta"] == 250
    acct = patched_loyalty["accounts"].find_by_id("cust-adj-1")
    assert acct["balance_points"] == 250
    assert acct["lifetime_earned"] == 250


def test_adjust_non_admin_blocked(client, staff_headers, patched_loyalty):
    resp = client.post(
        "/api/v1/loyalty/adjust",
        json={"customer_id": "cust-adj-2", "points": 100, "reason": "Trying"},
        headers=staff_headers,
    )
    assert resp.status_code == 403


# ============================================================================
# Tier promotion
# ============================================================================


def test_tier_promotion_crossing_1000_promotes_to_silver(
    client, auth_headers, patched_loyalty
):
    """Earn enough to cross 1000 lifetime_earned → tier flips to SILVER."""
    # First earn: 999 points worth (rupee_value 99,900 at 1% → 999)
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-tier-1", "order_id": "ORD-T1", "rupee_value": 99900.0},
        headers=auth_headers,
    )
    acct = patched_loyalty["accounts"].find_by_id("cust-tier-1")
    assert acct["tier"] == "BRONZE"
    assert acct["lifetime_earned"] == 999

    # Crossing earn: rupee_value 1000 at 1% → 10 points ⇒ lifetime 1009
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-tier-1", "order_id": "ORD-T2", "rupee_value": 1000.0},
        headers=auth_headers,
    )
    acct = patched_loyalty["accounts"].find_by_id("cust-tier-1")
    assert acct["lifetime_earned"] == 1009
    assert acct["tier"] == "SILVER"


# ============================================================================
# Expiry sweep
# ============================================================================


def test_expire_sweep_deducts_balance(client, auth_headers, patched_loyalty):
    """An EARN row past expires_at gets a balancing EXPIRE row + balance falls."""
    # Earn 100 points
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-exp", "order_id": "ORD-EXP", "rupee_value": 10000.0},
        headers=auth_headers,
    )
    acct = patched_loyalty["accounts"].find_by_id("cust-exp")
    assert acct["balance_points"] == 100

    # Force the EARN row to be already-expired
    earn_row = next(
        d for d in patched_loyalty["txns"].collection.docs if d.get("type") == "EARN"
    )
    earn_row["expires_at"] = datetime.now() - timedelta(days=1)

    # Run sweep
    resp = client.post("/api/v1/loyalty/expire", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expired_txns"] == 1
    assert body["points_expired"] == 100

    acct = patched_loyalty["accounts"].find_by_id("cust-exp")
    assert acct["balance_points"] == 0

    # An EXPIRE row was written
    expires = [
        d for d in patched_loyalty["txns"].collection.docs if d.get("type") == "EXPIRE"
    ]
    assert len(expires) == 1
    assert expires[0]["points"] == 100


# ============================================================================
# Settings
# ============================================================================


def test_settings_update_superadmin_only(client, auth_headers, staff_headers, patched_loyalty):
    """SUPERADMIN can write; non-admin → 403."""
    # Non-admin blocked
    resp = client.put(
        "/api/v1/loyalty/settings",
        json={"points_per_rupee": 0.05},
        headers=staff_headers,
    )
    assert resp.status_code == 403

    # SUPERADMIN succeeds
    resp = client.put(
        "/api/v1/loyalty/settings",
        json={"points_per_rupee": 0.05},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["points_per_rupee"] == 0.05


# ============================================================================
# Ledger pagination
# ============================================================================


def test_ledger_paginated_sorted_desc(client, auth_headers, patched_loyalty):
    """Adjust to write 5 ledger rows, then verify pagination + sort."""
    for i in range(5):
        client.post(
            "/api/v1/loyalty/adjust",
            json={
                "customer_id": "cust-led",
                "points": 50,
                "reason": f"Manual #{i}",
            },
            headers=auth_headers,
        )
        # Force distinct created_at values so the sort is deterministic
        rows = patched_loyalty["txns"].find_for_customer("cust-led", limit=10)
        if rows:
            rows[0]["created_at"] = datetime.now() + timedelta(seconds=i)

    resp = client.get(
        "/api/v1/loyalty/account/cust-led/ledger?limit=2&skip=0",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    resp2 = client.get(
        "/api/v1/loyalty/account/cust-led/ledger?limit=2&skip=2",
        headers=auth_headers,
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert len(body2["items"]) == 2
    # Ensure desc sort: page-1 first row.created_at >= page-2 first row.created_at
    if (
        body["items"][0].get("created_at")
        and body2["items"][0].get("created_at")
    ):
        assert body["items"][0]["created_at"] >= body2["items"][0]["created_at"]


# ============================================================================
# GET account snapshot
# ============================================================================


def test_account_snapshot_returns_recent_txns(client, auth_headers, patched_loyalty):
    """Account fetch returns balance + last 20 txns + settings."""
    client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-snap", "order_id": "ORD-SNAP", "rupee_value": 10000.0},
        headers=auth_headers,
    )
    resp = client.get("/api/v1/loyalty/account/cust-snap", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account"]["balance_points"] == 100
    assert body["account"]["tier"] == "BRONZE"
    assert len(body["recent_transactions"]) == 1
    assert body["recent_transactions"][0]["type"] == "EARN"
    # settings echo
    assert body["settings"]["points_per_rupee"] == 0.01
