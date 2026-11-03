"""
IMS 2.0 - POS money doors, round-26 money review of PR #1264 (head 024887c9)
============================================================================
The bill's store check and the key lookup run FIRST at the order door: a
re-press of a saved bill replays it even when a later gate (the Rx check, the
period lock) would now refuse. Once the lookup has run and missed, every
refusal says key_checked -- the till's only proof that no order exists under
the key. No emoji.
"""

from __future__ import annotations

import os
import sys

from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_orders_hardening import _draft_orders, _frame_item, _post_order_idem, hardening_orders  # noqa: E402,F401


def _rx_refuses(monkeypatch):
    def _refuse(*a, **k):
        raise HTTPException(status_code=422, detail="This lens line needs a valid prescription.")

    monkeypatch.setattr("api.routers.orders.create._validate_order_line_rx", _refuse)


def _period_locked(monkeypatch):
    def _locked(*a, **k):
        raise HTTPException(status_code=423, detail="This period is locked.")

    monkeypatch.setattr("api.routers.finance.check_period_locked", _locked)


def test_a_re_press_replays_its_order_even_when_the_rx_gate_now_refuses(client, auth_headers, hardening_orders,
                                                                         monkeypatch):
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-RX")
    assert r1.status_code in (200, 201), r1.text
    _rx_refuses(monkeypatch)
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-RX")
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"]


def test_a_re_press_replays_its_order_even_in_a_locked_period(client, auth_headers, hardening_orders, monkeypatch):
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-PL")
    _period_locked(monkeypatch)
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-PL")
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["order_id"] == r1.json()["order_id"] and len(_draft_orders(hardening_orders)) == 1


def test_a_refusal_after_a_missed_lookup_says_key_checked(client, auth_headers, hardening_orders, monkeypatch):
    import mongomock

    # The press holds its key's claim (money review 27): only then is a
    # refusal stamped key_checked.
    claims = mongomock.MongoClient().db.order_key_claims
    monkeypatch.setattr("api.routers.orders.create._claims_coll", lambda repo: claims)
    _period_locked(monkeypatch)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-NEW")
    assert r.status_code == 423, r.text
    assert r.json()["detail"] == {"message": "This period is locked.", "key_checked": True}
    unkeyed = _post_order_idem(client, auth_headers, [_frame_item(1000.0)])
    assert unkeyed.status_code == 423 and unkeyed.json()["detail"] == "This period is locked."  # no key: unchanged


def test_a_cancelled_order_refusal_says_key_checked(client, auth_headers, hardening_orders):
    _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-CX")
    for d in hardening_orders["order_repo"].collection.docs:
        d["status"] = "CANCELLED"
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-CX")
    detail = r.json()["detail"]
    assert r.status_code == 409 and detail["code"] == "ORDER_NOT_LIVE" and detail["key_checked"] is True
