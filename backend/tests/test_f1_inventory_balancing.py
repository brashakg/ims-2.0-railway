"""
IMS 2.0 - F1 Cross-store inventory balancing tests (intent-level)
================================================================
Exercises the REAL inventory_balancing service + its read-only router against a
faithful in-memory fake Mongo (no network, no live mongod). A hollow shell that
over-moves a donor below its own cover, ignores the highest-demand recipient,
counts a stale sale, counts a quarantined unit as on-hand, mutates stock, or
leaks another store's-only proposals to a single-store manager FAILS here.

The classify/propose math is PURE so most of the suite calls it directly with
explicit thresholds; the DB rollups are covered against the fake Mongo.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import inventory_balancing as svc  # noqa: E402

THRESH = {"window_days": 90, "overstock_days_cover": 120,
          "understock_days_cover": 21, "target_days_cover": 45}


# ============================================================================
# Faithful in-memory fake Mongo (find-only -- the rollups never use aggregate)
# ============================================================================


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        actual = doc.get(k)
        if isinstance(v, dict) and "$in" in v:
            if actual not in v["$in"]:
                return False
            continue
        if actual != v:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": 1})()

    def find(self, query=None, projection=None):
        return FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])


class FakeDB:
    def __init__(self):
        self._c: Dict[str, FakeCollection] = {}

    def get_collection(self, name):
        if name not in self._c:
            self._c[name] = FakeCollection()
        return self._c[name]

    def __getitem__(self, name):
        return self.get_collection(name)


@pytest.fixture()
def db():
    return FakeDB()


# ============================================================================
# Helpers to build stat rows + seed the fake DB
# ============================================================================


def _stat(pid, store, on_hand, units_sold, brand="Ray-Ban", category="FRAME"):
    return {"product_id": pid, "store_id": store, "on_hand": on_hand,
            "units_sold": units_sold, "brand": brand, "category": category}


def _seed_product(db, pid, brand="Ray-Ban", category="FRAME", status="active"):
    db.get_collection("products").insert_one(
        {"product_id": pid, "brand": brand, "category": category, "status": status})


def _seed_stock(db, pid, store, units, status="AVAILABLE"):
    for _ in range(units):
        db.get_collection("stock_units").insert_one(
            {"product_id": pid, "store_id": store, "status": status, "quantity": 1})


def _seed_order(db, pid, store, qty, *, days_ago, status="COMPLETED"):
    created = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    db.get_collection("orders").insert_one(
        {"order_id": f"O-{pid}-{store}-{days_ago}", "store_id": store, "status": status,
         "created_at": created, "items": [{"product_id": pid, "quantity": qty}]})


# ============================================================================
# Classification bands
# ============================================================================


def test_classify_dead_overstock_healthy_understock_stockout():
    # DEAD: stock but zero sales in window.
    d = svc.classify_store_stock(_stat("P", "A", on_hand=20, units_sold=0), thresholds=THRESH)
    assert d["classification"] == svc.DEAD
    assert d["surplus_units"] == 20            # whole on-hand movable (target 0)
    # OVERSTOCK: 200 on-hand, 10 sold/90d -> ~1800 days cover (>120).
    o = svc.classify_store_stock(_stat("P", "B", on_hand=200, units_sold=10), thresholds=THRESH)
    assert o["classification"] == svc.OVERSTOCK
    # HEALTHY: cover between understock(21) and overstock(120).
    h = svc.classify_store_stock(_stat("P", "C", on_hand=30, units_sold=45), thresholds=THRESH)
    assert h["classification"] == svc.HEALTHY  # 0.5/day -> 60 days cover
    # UNDERSTOCK: low cover, still stocked.
    u = svc.classify_store_stock(_stat("P", "D", on_hand=2, units_sold=45), thresholds=THRESH)
    assert u["classification"] == svc.UNDERSTOCK  # 0.5/day -> 4 days cover (<21)
    assert u["deficit_units"] > 0
    # STOCKOUT: zero on-hand, real demand.
    s = svc.classify_store_stock(_stat("P", "E", on_hand=0, units_sold=45), thresholds=THRESH)
    assert s["classification"] == svc.STOCKOUT
    assert s["deficit_units"] > 0


# ============================================================================
# propose_moves -- the core
# ============================================================================


def test_move_qty_is_min_of_surplus_and_deficit_never_over_moves():
    # A overstocks (200, sold 10 -> target 5, surplus 195). B is empty but sells
    # briskly (sold 30 -> 0.333/day, target 15, deficit 15).
    stats = [_stat("P", "A", 200, 10), _stat("P", "B", 0, 30)]
    moves = svc.propose_moves(stats, thresholds=THRESH)
    assert len(moves) == 1
    m = moves[0]
    assert m["from_store"] == "A" and m["to_store"] == "B"
    assert m["qty"] == 15                       # min(195, 15) -- only the deficit
    # the donor never drops below its own target cover.
    assert m["donor_on_hand_after"] == 185
    assert m["recipient_on_hand_after"] == 15


def test_dead_stock_donor_whole_on_hand_movable():
    # A is DEAD (50 on-hand, 0 sales). B needs 15.
    stats = [_stat("P", "A", 50, 0), _stat("P", "B", 0, 30)]
    moves = svc.propose_moves(stats, thresholds=THRESH)
    assert moves[0]["from_store"] == "A"
    assert moves[0]["donor_classification"] == svc.DEAD
    assert moves[0]["qty"] == 15


def test_single_store_product_yields_no_move():
    assert svc.propose_moves([_stat("P", "A", 200, 10)], thresholds=THRESH) == []


def test_no_recipient_yields_no_move():
    # Two overstock stores, nobody needs stock.
    stats = [_stat("P", "A", 200, 10), _stat("P", "B", 300, 8)]
    assert svc.propose_moves(stats, thresholds=THRESH) == []


def test_no_donor_yields_no_move():
    # Two stockouts, nobody has surplus.
    stats = [_stat("P", "A", 0, 30), _stat("P", "B", 0, 20)]
    assert svc.propose_moves(stats, thresholds=THRESH) == []


def test_highest_velocity_recipient_is_filled_first():
    # Donor A has a surplus of ~5 units only (60 on-hand, 90 sold -> 1/day,
    # target 45, surplus 15). Two recipients: B sells faster than C.
    stats = [
        _stat("P", "A", 60, 90),    # 1/day, 60 days cover -> HEALTHY? 60<120 and >21 -> HEALTHY, surplus 0
        _stat("P", "B", 1, 90),     # 1/day, 1 day cover -> UNDERSTOCK, deficit ~44
        _stat("P", "C", 1, 45),     # 0.5/day, 2 day cover -> UNDERSTOCK, deficit ~21
        _stat("P", "D", 500, 10),   # DEAD-ish overstock donor with big surplus
    ]
    moves = svc.propose_moves(stats, thresholds=THRESH)
    # the first move must target B (higher velocity) before C.
    assert moves[0]["to_store"] == "B"
    assert moves[0]["recipient_classification"] == svc.UNDERSTOCK


def test_donor_never_sheds_below_target_cover_invariant():
    # OVERSTOCK donor: 200 on-hand, 90 sold -> 1/day, 200 days cover (>120),
    # target 45, surplus 155. Recipient needs ~400 -> capped at the 155 surplus.
    stats = [_stat("P", "A", 200, 90), _stat("P", "B", 0, 400)]
    moves = svc.propose_moves(stats, thresholds=THRESH)
    assert moves[0]["qty"] == 155
    assert moves[0]["donor_on_hand_after"] == 45   # exactly the target cover, never below


def test_dead_donor_prioritized_over_overstock_donor():
    # Recipient needs 10. A DEAD donor (12 on-hand) and an OVERSTOCK donor both
    # qualify -- the DEAD stock should move first (free up frozen capital).
    stats = [_stat("P", "A", 12, 0),      # DEAD, surplus 12
             _stat("P", "OV", 300, 12),   # OVERSTOCK, surplus ~294
             _stat("P", "R", 0, 20)]      # needs 10
    moves = svc.propose_moves(stats, thresholds=THRESH)
    assert moves[0]["from_store"] == "A"
    assert moves[0]["donor_classification"] == svc.DEAD


def test_summary_counts_classifications_and_units():
    stats = [_stat("P", "A", 200, 10), _stat("P", "B", 0, 30)]
    enriched = svc.classify_all(stats, thresholds=THRESH)
    moves = svc.propose_moves(enriched, thresholds=THRESH)
    summary = svc.summarize(enriched, moves)
    assert summary["total_proposals"] == 1
    assert summary["total_units_to_move"] == 15
    assert summary["skus_with_proposals"] == 1
    assert summary["classification_counts"][svc.OVERSTOCK] == 1
    assert summary["classification_counts"][svc.STOCKOUT] == 1


# ============================================================================
# DB rollups -- velocity windowing + on-hand status filter
# ============================================================================


def test_units_sold_only_counts_in_window(db):
    _seed_order(db, "P", "A", 5, days_ago=10)    # inside 90d
    _seed_order(db, "P", "A", 7, days_ago=200)   # OUTSIDE 90d -- must NOT count
    _seed_order(db, "P", "A", 3, days_ago=5, status="CANCELLED")  # excluded status
    sold = svc._units_sold_by_product_store(db, now=datetime.utcnow(), window_days=90)
    assert sold[("P", "A")] == 5.0


def test_on_hand_excludes_non_sellable_units(db):
    _seed_stock(db, "P", "A", 4, status="AVAILABLE")
    _seed_stock(db, "P", "A", 3, status="QUARANTINED")  # excluded
    _seed_stock(db, "P", "A", 2, status="SOLD")         # excluded
    on_hand = svc._on_hand_by_product_store(db, ["P"])
    assert on_hand[("P", "A")] == 4


def test_gather_stats_merges_on_hand_and_sales_and_skips_inactive(db):
    _seed_product(db, "P")
    _seed_product(db, "DEADSKU", status="archived")     # inactive -> skipped
    _seed_stock(db, "P", "A", 10)
    _seed_stock(db, "DEADSKU", "A", 5)
    _seed_order(db, "P", "B", 4, days_ago=10)           # B has demand, no stock (stockout)
    stats = svc.gather_stats(db, window_days=90)
    by_key = {(s["product_id"], s["store_id"]): s for s in stats}
    assert ("P", "A") in by_key and by_key[("P", "A")]["on_hand"] == 10
    assert ("P", "B") in by_key and by_key[("P", "B")]["units_sold"] == 4
    assert not any(s["product_id"] == "DEADSKU" for s in stats)  # inactive product excluded


def test_gather_stats_brand_filter(db):
    _seed_product(db, "RB", brand="Ray-Ban")
    _seed_product(db, "OAK", brand="Oakley")
    _seed_stock(db, "RB", "A", 5)
    _seed_stock(db, "OAK", "A", 5)
    stats = svc.gather_stats(db, window_days=90, brand="Ray-Ban")
    assert {s["product_id"] for s in stats} == {"RB"}


def test_db_absent_is_failsoft():
    assert svc.gather_stats(None, window_days=90) == []
    assert svc._on_hand_by_product_store(None, ["P"]) == {}
    assert svc._units_sold_by_product_store(None, now=datetime.utcnow(), window_days=90) == {}


# ============================================================================
# ROUTER -- role gate + output store-scope
# ============================================================================


def _user(roles, store=None):
    return {"user_id": "U1", "roles": roles,
            "store_ids": [store] if store else [], "active_store_id": store}


def _seed_balanceable(db):
    """One product overstocked at BV-1, stocked-out at BV-2, plus a third store
    BV-3 pairing so a single-store manager's filter is observable."""
    _seed_product(db, "P")
    _seed_stock(db, "P", "BV-1", 200)
    _seed_order(db, "P", "BV-1", 10, days_ago=30)
    _seed_order(db, "P", "BV-2", 30, days_ago=20)   # BV-2 sells, no stock -> stockout
    # an unrelated pair BV-3(donor) -> BV-4(recipient) that does NOT touch BV-1/2
    _seed_product(db, "Q")
    _seed_stock(db, "Q", "BV-3", 200)
    _seed_order(db, "Q", "BV-3", 10, days_ago=30)
    _seed_order(db, "Q", "BV-4", 30, days_ago=20)


def test_router_admin_sees_all_proposals(db, monkeypatch):
    import asyncio
    from api.routers import inventory_balancing as r
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_thresholds", lambda store_id: dict(THRESH))
    _seed_balanceable(db)
    out = asyncio.run(r.get_proposals(current_user=_user(["ADMIN"])))
    stores_touched = {(m["from_store"], m["to_store"]) for m in out["proposals"]}
    assert ("BV-1", "BV-2") in stores_touched
    assert ("BV-3", "BV-4") in stores_touched   # admin sees the unrelated pair too
    assert out["store_scoped"] is False


def test_router_single_store_manager_sees_only_their_proposals(db, monkeypatch):
    import asyncio
    from api.routers import inventory_balancing as r
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_thresholds", lambda store_id: dict(THRESH))
    _seed_balanceable(db)
    out = asyncio.run(r.get_proposals(current_user=_user(["STORE_MANAGER"], store="BV-1")))
    assert out["store_scoped"] is True
    # only proposals involving BV-1 survive; the BV-3->BV-4 pair is hidden.
    for m in out["proposals"]:
        assert "BV-1" in (m["from_store"], m["to_store"])
    assert all(not ("BV-3" == m["from_store"] and "BV-4" == m["to_store"]) for m in out["proposals"])
    assert any(m["from_store"] == "BV-1" for m in out["proposals"])


def test_router_rejects_floor_role(db, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from api.routers import inventory_balancing as r
    monkeypatch.setattr(r, "_get_db", lambda: db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.get_proposals(current_user=_user(["SALES_STAFF"], store="BV-1")))
    assert exc.value.status_code == 403


def test_router_never_mutates_stock(db, monkeypatch):
    """Running the report leaves stock_units + orders byte-for-byte unchanged."""
    import asyncio
    from api.routers import inventory_balancing as r
    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_thresholds", lambda store_id: dict(THRESH))
    _seed_balanceable(db)
    before_stock = len(db.get_collection("stock_units").docs)
    before_orders = len(db.get_collection("orders").docs)
    before_proposals = len(db.get_collection("stock_adjustment_proposals").docs)
    asyncio.run(r.get_proposals(current_user=_user(["ADMIN"])))
    assert len(db.get_collection("stock_units").docs) == before_stock
    assert len(db.get_collection("orders").docs) == before_orders
    # the report writes NO proposals/transfers anywhere.
    assert len(db.get_collection("stock_adjustment_proposals").docs) == before_proposals == 0
