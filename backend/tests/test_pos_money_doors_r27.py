"""
IMS 2.0 - POS money doors, round-27 money review of PR #1264 (head 50403593)
============================================================================
ONE press of a till key at a time: right after the key lookup misses, the
press takes an in-flight claim (order_key_claims, unique per store and key).
A second press while it is held answers "still saving" (pending, never
key_checked). key_checked is stamped only by the press that holds the claim,
after a last read finds no order. A crashed press's stale claim is taken
over, after the twin is checked first. No emoji.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem, hardening_orders  # noqa: E402,F401


@pytest.fixture()
def claims(monkeypatch, hardening_orders):
    import mongomock

    coll = mongomock.MongoClient().db.order_key_claims
    monkeypatch.setattr("api.routers.orders.create._claims_coll", lambda repo: coll)
    return coll


def _store_of(client, auth_headers, hardening_orders):
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-PROBE-STORE")
    return hardening_orders["order_repo"].collection.docs[-1]["store_id"], r


def _held(claims, store_id, key, minutes_ago=0):
    claims.insert_one({"_id": f"{store_id}|{key}", "store_id": store_id, "key": key, "token": "press-1",
                       "started_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
                       "expire_at": datetime.now(timezone.utc) + timedelta(days=1)})


def _spend_refuses(monkeypatch):
    def _refuse(*a, **k):
        raise HTTPException(status_code=409, detail={"code": "DISCOUNT_APPROVAL_USED",
                                                     "message": "That approval was already used."})

    monkeypatch.setattr("api.routers.finance.check_period_locked", _refuse)


def test_a_second_press_while_the_first_holds_the_claim_is_still_saving(client, auth_headers, hardening_orders,
                                                                         claims):
    store, _ = _store_of(client, auth_headers, hardening_orders)
    _held(claims, store, "K-2")  # press 1 is in flight
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-2")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "SALE_STILL_SAVING" and "key_checked" not in detail
    assert len([d for d in _draft_orders(hardening_orders) if d.get("idempotency_key") == "K-2"]) == 0


def test_the_spend_probe_with_a_sibling_claim_never_says_key_checked(client, auth_headers, hardening_orders,
                                                                     claims, monkeypatch):
    """The finding's probe: press 1 holds the claim (it spent the approval);
    press 2's refusal path would 409 -- it never gets there, and nothing it
    answers says the key was checked."""
    store, _ = _store_of(client, auth_headers, hardening_orders)
    _held(claims, store, "K-SP")
    _spend_refuses(monkeypatch)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-SP")
    assert r.status_code == 409 and "key_checked" not in r.json()["detail"]


def test_an_order_that_landed_during_the_refusal_is_replayed(client, auth_headers, hardening_orders, claims,
                                                            monkeypatch):
    """This press holds the claim, but an order under the key is there by the
    time its refusal fires (press 1 landed late): it replays, never key_checked."""
    store, first = _store_of(client, auth_headers, hardening_orders)
    landed = dict(hardening_orders["order_repo"].collection.docs[-1])

    def _refuse_after_landing(*a, **k):
        hardening_orders["order_repo"].collection.docs.append({**landed, "order_id": "O-LATE",
                                                                "order_number": "BV/LATE",
                                                                "idempotency_key": "K-LATE"})
        raise HTTPException(status_code=423, detail="This period is locked.")

    monkeypatch.setattr("api.routers.finance.check_period_locked", _refuse_after_landing)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-LATE")
    assert r.status_code in (200, 201), r.text
    assert r.json()["order_id"] == "O-LATE" and first.status_code in (200, 201)


def test_a_stale_claim_is_taken_over_after_the_twin_is_checked(client, auth_headers, hardening_orders, claims,
                                                              monkeypatch):
    from api.routers.orders import create as create_mod

    store, _ = _store_of(client, auth_headers, hardening_orders)
    _held(claims, store, "K-ST", minutes_ago=10)  # a crashed press's claim
    seen = []
    real = create_mod._twin_of
    monkeypatch.setattr(create_mod, "_twin_of", lambda *a, **k: (seen.append(a[1]), real(*a, **k))[1])
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-ST")
    assert r.status_code in (200, 201), r.text
    assert "K-ST" in seen  # the twin was read before the takeover
    assert claims.count_documents({}) == 0  # taken over, then released on success


def test_the_claim_is_released_on_success_and_on_refusal(client, auth_headers, hardening_orders, claims,
                                                         monkeypatch):
    ok = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-OK")
    assert ok.status_code in (200, 201) and claims.count_documents({}) == 0

    def _locked(*a, **k):
        raise HTTPException(status_code=423, detail="This period is locked.")

    monkeypatch.setattr("api.routers.finance.check_period_locked", _locked)
    refused = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-NO")
    assert refused.status_code == 423 and refused.json()["detail"]["key_checked"] is True
    assert claims.count_documents({}) == 0


def test_the_claim_indexes_are_ensured_with_the_order_key_index():
    import inspect

    from database import connection

    src = inspect.getsource(connection)
    assert '"order_key_claims", [("store_id", 1), ("key", 1)], unique=True' in src
    assert 'name="uniq_order_key_claim"' in src and 'expireAfterSeconds=0, name="ttl_order_key_claim"' in src
