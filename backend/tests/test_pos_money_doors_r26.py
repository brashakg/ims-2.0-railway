"""
IMS 2.0 - POS money doors, round-26 money review of PR #1264
============================================================
The key lookup runs FIRST at the order door: a re-press of a bill that was
saved replays it even when a later gate (the Rx check, the period lock) would
now refuse. Refusals keep their plain codes and messages; nothing in them
settles a pending bill key (the till keeps a pending bill until it is saved
or discarded -- money review 28). No emoji.
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


def test_a_refusal_keeps_its_plain_message(client, auth_headers, hardening_orders, monkeypatch):
    _period_locked(monkeypatch)
    r = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-NEW")
    assert r.status_code == 423 and r.json()["detail"] == "This period is locked."
