"""Unit tests for services/stock_allocation.py (online vs in-store reconcile).

The verdict and the order are per LOCATION: the caller hands over `unbacked`
(units no shelf backs, location by location) and `excess` (units beyond the
writer's number, location by location) -- nothing here compares a pooled
online total with a pooled on-hand."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.stock_allocation import (  # noqa: E402
    recommend_allocation, classify, reconcile_items,
    OVERSELL_RISK, OVER_ALLOCATED, ONHAND_UNKNOWN, LISTED_UNKNOWN, OK, NOT_ONLINE,
)


def _item(sku, in_store, online, unbacked, excess, recommended, is_online=True):
    return {"sku": sku, "in_store": in_store, "online": online, "is_online": is_online,
            "unbacked": unbacked, "excess": excess, "recommended": recommended}


def test_recommend_allocation():
    assert recommend_allocation(10) == 10
    assert recommend_allocation(10, safety_buffer=2) == 8
    assert recommend_allocation(1, safety_buffer=5) == 0       # never below 0
    assert recommend_allocation(10, safety_buffer=0, max_online=3) == 3
    assert recommend_allocation(None) == 0


def test_classify():
    # classify(in_store, online, is_online, over, excess)
    assert classify(5, 8, True, 3, 3) == OVERSELL_RISK      # a location lists past its shelf
    assert classify(10, 9, True, 0, 1) == OVER_ALLOCATED    # past the writer's number, not the shelf
    assert classify(10, 8, True, 0, 0) == OK
    assert classify(10, 8, False, 3, 3) == NOT_ONLINE
    assert classify(10, 8, True, None, 0) == ONHAND_UNKNOWN  # a shop behind a listing unread
    assert classify(10, 8, True, 0, None) == ONHAND_UNKNOWN


def test_classify_pooled_totals_never_decide():
    """3 on the shelf, 3 listed -- pooled that is clean. Location by location
    the 3 are listed where the shelf is 0: OVERSELL_RISK. Put back a pooled
    `over = online - in_store` fallback and feed it -> OK -> this fails."""
    assert classify(3, 3, True, 3, 3) == OVERSELL_RISK


def test_classify_unknown_listed_is_never_ok():
    """Fix-round P1: an online SKU with an UNKNOWN listed qty (None -- outside
    the live-read coverage) must classify LISTED_UNKNOWN, never OK."""
    assert classify(10, None, True, 0, 0) == LISTED_UNKNOWN
    # Not-online still wins over unknown (nothing to assess).
    assert classify(10, None, False, 0, 0) == NOT_ONLINE


def test_classify_unknown_on_hand_is_never_oversell():
    """Recheck round 1: an online SKU whose ON-HAND is unknown (in_store=None
    -- the shop list or the stock read failed) is ONHAND_UNKNOWN, never a
    confident 0 + OVERSELL_RISK. Not-online still wins (nothing to assess)."""
    assert classify(None, 5, True, 5, 5) == ONHAND_UNKNOWN
    assert classify(None, None, True, None, None) == ONHAND_UNKNOWN
    assert classify(None, 5, False, 5, 5) == NOT_ONLINE


def test_reconcile_unknown_listed_rows():
    r = reconcile_items([
        _item("A", 5, None, None, None, 5),  # listed unknown
        _item("B", 5, 2, 0, 0, 5),           # ok
    ])
    s = r["summary"]
    assert s["listed_unknown"] == 1 and s["ok"] == 1
    by_sku = {i["sku"]: i for i in r["items"]}
    assert by_sku["A"]["status"] == LISTED_UNKNOWN
    assert by_sku["A"]["online"] is None and by_sku["A"]["delta"] is None
    # Unknown sorts worse than OK (it is unverified, not clean).
    assert r["items"][0]["sku"] == "A"


def test_reconcile_flags_oversell():
    r = reconcile_items([
        _item("A", 2, 5, 3, 3, 2),                  # 3 listed past a shelf
        _item("B", 10, 9, 0, 1, 8),                 # 1 past the writer's number
        _item("C", 10, 4, 0, 0, 8),                 # ok
        _item("D", 10, 0, 0, 0, 8, is_online=False),
    ])
    s = r["summary"]
    assert (s["oversell_risk"], s["over_allocated"], s["ok"], s["not_online"]) == (1, 1, 1, 1)
    assert s["oversell_risk_units"] == 3
    assert "safety_buffer" not in s  # the route adds the WRITER's buffer; no second one here
    assert [(i["sku"], i["status"], i["recommended"], i["delta"]) for i in r["items"][:2]] == [
        ("A", OVERSELL_RISK, 2, 3), ("B", OVER_ALLOCATED, 8, 1)]


def test_reconcile_orders_and_reports_delta_per_location_never_pooled():
    """Round 4 P1, the swapped pair: SKU-1 is BV-A 3 / BV-B 0 on the shelf
    against Shopify LOC_A 0 / LOC_B 3 -- 3 units unbacked at LOC_B, while the
    totals match (3 vs 3). SKU-2 is BV-A 0 / BV-B 1 against 1 / 1 -- 1 unit
    unbacked. SKU-1 is the worse oversell and must lead; its delta is 3, not
    'listed total minus recommended total' = 0. Put back `online - rec` for
    delta and the sort -> SKU-1 reads delta 0 and sorts below SKU-2 -> this
    fails."""
    r = reconcile_items([
        _item("SKU-2", 1, 2, 1, 1, 1),
        _item("SKU-1", 3, 3, 3, 3, 3),
    ])
    assert [(i["sku"], i["status"], i["delta"]) for i in r["items"]] == [
        ("SKU-1", OVERSELL_RISK, 3), ("SKU-2", OVERSELL_RISK, 1)]
    assert r["summary"]["oversell_risk_units"] == 4


def test_reconcile_empty():
    r = reconcile_items([])
    assert r["summary"]["total"] == 0
    assert r["items"] == []


def test_reconcile_unknown_on_hand_rows():
    r = reconcile_items([
        _item("A", None, 5, None, None, None),  # on-hand unknown
        _item("B", 2, 5, 3, 3, 2),              # a real oversell by 3
    ])
    by_sku = {i["sku"]: i for i in r["items"]}
    assert by_sku["A"]["status"] == ONHAND_UNKNOWN
    assert by_sku["A"]["in_store"] is None and by_sku["A"]["recommended"] is None and by_sku["A"]["delta"] is None
    s = r["summary"]
    assert s["onhand_unknown"] == 1 and s["oversell_risk"] == 1 and s["oversell_risk_units"] == 3
    # A real oversell sorts above an unknown; an unknown above OK.
    assert r["items"][0]["sku"] == "B"
