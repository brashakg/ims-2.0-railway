"""Unit tests for services/stock_allocation.py (online vs in-store reconcile)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.stock_allocation import (  # noqa: E402
    recommend_allocation, classify, reconcile_items,
    OVERSELL_RISK, OVER_ALLOCATED, LISTED_UNKNOWN, OK, NOT_ONLINE,
)


def test_recommend_allocation():
    assert recommend_allocation(10) == 10
    assert recommend_allocation(10, safety_buffer=2) == 8
    assert recommend_allocation(1, safety_buffer=5) == 0       # never below 0
    assert recommend_allocation(10, safety_buffer=0, max_online=3) == 3
    assert recommend_allocation(None) == 0


def test_classify():
    assert classify(5, 8, 5, True) == OVERSELL_RISK       # online > on-hand
    assert classify(10, 9, 8, True) == OVER_ALLOCATED     # > safe but <= on-hand
    assert classify(10, 8, 8, True) == OK
    assert classify(10, 8, 8, False) == NOT_ONLINE


def test_classify_unknown_listed_is_never_ok():
    """Fix-round P1: an online SKU with an UNKNOWN listed qty (None -- outside
    the live-read coverage) must classify LISTED_UNKNOWN, never OK."""
    assert classify(10, None, 8, True) == LISTED_UNKNOWN
    # Not-online still wins over unknown (nothing to assess).
    assert classify(10, None, 8, False) == NOT_ONLINE


def test_reconcile_unknown_listed_rows():
    items = [
        {"sku": "A", "in_store": 5, "online": None, "is_online": True},  # unknown
        {"sku": "B", "in_store": 5, "online": 2, "is_online": True},     # ok
    ]
    r = reconcile_items(items, safety_buffer=0)
    s = r["summary"]
    assert s["listed_unknown"] == 1
    assert s["ok"] == 1
    by_sku = {i["sku"]: i for i in r["items"]}
    assert by_sku["A"]["status"] == LISTED_UNKNOWN
    assert by_sku["A"]["online"] is None
    assert by_sku["A"]["delta"] is None
    # Unknown sorts worse than OK (it is unverified, not clean).
    assert r["items"][0]["sku"] == "A"


def test_reconcile_flags_oversell():
    items = [
        {"sku": "A", "in_store": 2, "online": 5, "is_online": True},   # oversell by 3
        {"sku": "B", "in_store": 10, "online": 9, "is_online": True, "name": "B"},  # over-allocated (buffer 2 -> rec 8)
        {"sku": "C", "in_store": 10, "online": 4, "is_online": True},  # ok
        {"sku": "D", "in_store": 10, "online": 0, "is_online": False}, # not online
    ]
    r = reconcile_items(items, safety_buffer=2)
    s = r["summary"]
    assert s["oversell_risk"] == 1
    assert s["over_allocated"] == 1
    assert s["ok"] == 1
    assert s["not_online"] == 1
    assert s["oversell_risk_units"] == 3
    # worst-first ordering: A (oversell) leads
    assert r["items"][0]["sku"] == "A"
    assert r["items"][0]["status"] == OVERSELL_RISK
    assert r["items"][0]["recommended"] == 0  # 2 on-hand - 2 buffer


def test_reconcile_empty():
    r = reconcile_items([])
    assert r["summary"]["total"] == 0
    assert r["items"] == []
