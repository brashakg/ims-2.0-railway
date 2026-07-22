"""
Shopify Payments payouts puller tests.
Pins: pure node normalisation, a clean "not enabled" signal when the store has no
Shopify Payments account, and an IDEMPOTENT upsert keyed on payout id (re-pulling
the same window updates rows in place, never duplicates).

The Shopify network boundary (_graphql) is injected -- no HTTP is made.
"""

import asyncio
import os
import sys
import types

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import shopify_payouts as sp  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _FakeUpsertColl:
    def __init__(self):
        self.docs = []

    def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                d.update(update.get("$set", {}))
                return types.SimpleNamespace(upserted_id=None, matched_count=1)
        if upsert:
            new = {}
            new.update(filt)
            new.update(update.get("$setOnInsert", {}))
            new.update(update.get("$set", {}))
            self.docs.append(new)
            return types.SimpleNamespace(upserted_id="new", matched_count=0)
        return types.SimpleNamespace(upserted_id=None, matched_count=0)


class _FakeDb:
    def __init__(self):
        self._colls = {}

    def get_collection(self, name):
        return self._colls.setdefault(name, _FakeUpsertColl())


def test_normalize_payout_flattens_net_money():
    node = {
        "id": "gid://shopify/ShopifyPaymentsPayout/1",
        "status": "PAID",
        "issuedAt": "2026-07-20T00:00:00Z",
        "net": {"amount": "15340.50", "currencyCode": "INR"},
    }
    row = sp.normalize_payout(node)
    assert row == {
        "payout_id": "gid://shopify/ShopifyPaymentsPayout/1",
        "status": "PAID",
        "date": "2026-07-20T00:00:00Z",
        "amount": 15340.5,
        "currency": "INR",
    }
    assert sp.normalize_payout({"status": "PAID"}) is None  # no id -> dropped


def test_upsert_payouts_is_idempotent():
    db = _FakeDb()
    rows = [
        {"payout_id": "p1", "status": "PAID", "date": "d1", "amount": 10.0, "currency": "INR"},
        {"payout_id": "p2", "status": "SCHEDULED", "date": "d2", "amount": 20.0, "currency": "INR"},
    ]
    first = sp.upsert_payouts(db, rows)
    assert first["upserted"] == 2 and first["updated"] == 0

    # Re-pull the same window (p2 now PAID) -> updates in place, no duplicates.
    rows2 = [
        {"payout_id": "p1", "status": "PAID", "date": "d1", "amount": 10.0, "currency": "INR"},
        {"payout_id": "p2", "status": "PAID", "date": "d2", "amount": 20.0, "currency": "INR"},
    ]
    second = sp.upsert_payouts(db, rows2)
    assert second["upserted"] == 0 and second["updated"] == 2

    coll = db.get_collection("shopify_payouts")
    assert len(coll.docs) == 2
    p2 = next(d for d in coll.docs if d["payout_id"] == "p2")
    assert p2["status"] == "PAID"  # latest wins


def test_fetch_payouts_not_enabled_is_clean_signal(monkeypatch):
    # Creds present, but the store has no Shopify Payments account (null).
    monkeypatch.setattr(sp, "_graphql", None, raising=False)
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: True)

    async def fake_gql(db, query, variables):
        return {"data": {"shopifyPaymentsAccount": None}}

    res = _run(sp.fetch_payouts(_FakeDb(), graphql=fake_gql))
    assert res["ok"] is True and res["enabled"] is False
    assert "not enabled" in (res["reason"] or "")


def test_fetch_payouts_parses_edges(monkeypatch):
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: True)

    async def fake_gql(db, query, variables):
        return {
            "data": {
                "shopifyPaymentsAccount": {
                    "id": "acct",
                    "payouts": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "edges": [
                            {"node": {"id": "po1", "status": "PAID", "issuedAt": "d1",
                                      "net": {"amount": "5.00", "currencyCode": "INR"}}},
                        ],
                    },
                }
            }
        }

    res = _run(sp.fetch_payouts(_FakeDb(), graphql=fake_gql))
    assert res["ok"] is True and res["enabled"] is True
    assert res["payouts"][0]["payout_id"] == "po1"


def test_pull_payouts_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr("api.services.shopify_push._has_shopify_creds", lambda db, *a, **k: True)

    async def fake_gql(db, query, variables):
        return {"data": {"shopifyPaymentsAccount": {"id": "a", "payouts": {"edges": [
            {"node": {"id": "po9", "status": "PAID", "issuedAt": "d",
                      "net": {"amount": "1.0", "currencyCode": "INR"}}}]}}}}

    db = _FakeDb()
    res = _run(sp.pull_payouts(db, apply=False, graphql=fake_gql))
    assert res["applied"] is False and res["fetched"] == 1
    assert res["upserted"] == 0
    assert db._colls.get("shopify_payouts") is None or db.get_collection("shopify_payouts").docs == []
