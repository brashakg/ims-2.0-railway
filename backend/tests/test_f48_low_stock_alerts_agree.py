"""Audit F48 (2026-09-29): Low stock said 'Unknown Product', Alerts said nothing.

GET /inventory/low-stock returned only {_id, quantity}, so the screen printed
"Unknown Product - 4 left". GET /inventory/alerts counted stock from the legacy
products.stock_quantity field and filtered the catalogue by products.store_id,
so with real stock living in stock_units it answered "No Alerts" while the strip
said LOW STOCK 1.

Contract: the low-stock row names the product; Alerts reads the same ledger,
flags the low-stock products as LOW_STOCK (one rule: reorder_policy.low_stock_rows,
each shop's own level since D12),
and never calls stock that arrived today dead.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv  # noqa: E402
from api.routers.inventory import get_low_stock_alerts, get_stock_alerts  # noqa: E402

_NOW = datetime.utcnow()
_MGR = {"user_id": "m1", "roles": ["STORE_MANAGER"], "active_store_id": "S1", "store_ids": ["S1"]}

_PRODUCTS = [
    {
        "product_id": "P-AV",
        "name": "Ray-Ban RB3025 Aviator - Gold",
        "brand": "Ray-Ban",
        "sku": "RB3025-GLD",
        "reorder_levels": {"S1": 5},  # this shop's level (D12)
        "category": "SUNGLASS",
        "cost_price": 5000,
    },
    {
        "product_id": "P-WAY",
        "name": "Ray-Ban Wayfarer",
        "brand": "Ray-Ban",
        "sku": "RB2140",
        "reorder_levels": {"S1": 5},  # this shop's level (D12)
        "category": "SUNGLASS",
        "cost_price": 4000,
    },
]


def _units(pid, n, days_old=0, store="S1", **over):
    unit = {
        "product_id": pid,
        "store_id": store,
        "status": "AVAILABLE",
        "created_at": _NOW - timedelta(days=days_old),
    }
    unit.update(over)
    return [dict(unit) for _ in range(n)]


# Received today: 4 Aviators (low), 17 Wayfarers (healthy).
_UNITS = _units("P-AV", 4) + _units("P-WAY", 17)


def _wire(mp, units=_UNITS, products=_PRODUCTS):
    """A mongomock database behind the REAL StockRepository / ProductRepository,
    so both screens run the repository's own count, not a copy of it."""
    import mongomock

    from database.repositories.product_repository import ProductRepository, StockRepository

    db = mongomock.MongoClient().db
    db.products.insert_many([dict(p) for p in products])
    if units:
        db.stock_units.insert_many([dict(u) for u in units])
    mp.setattr(inv, "get_stock_repository", lambda: StockRepository(db.stock_units))
    mp.setattr(inv, "get_product_repository", lambda: ProductRepository(db.products))
    mp.setattr(inv, "_get_db", lambda: db)
    return db


def _alerts():
    return asyncio.run(
        get_stock_alerts(
            store_id=None, dead_days=90, lead_time_days=14, limit=200, current_user=_MGR
        )
    )


def _low():
    return asyncio.run(get_low_stock_alerts(store_id=None, current_user=_MGR))


def test_low_stock_row_names_the_product(monkeypatch):
    _wire(monkeypatch)
    res = _low()
    (row,) = res["items"]
    assert row["name"] == "Ray-Ban RB3025 Aviator - Gold"
    assert row["sku"] == "RB3025-GLD"
    assert row["brand"] == "Ray-Ban"
    assert row["id"] == row["product_id"] == "P-AV"
    assert row["quantity"] == 4


def test_alerts_agree_with_low_stock(monkeypatch):
    _wire(monkeypatch)
    low = _low()
    res = _alerts()
    low_names = {r["name"] for r in low["items"]}
    alert_low = {a["productName"] for a in res["alerts"] if a["alertType"] == "LOW_STOCK"}
    assert alert_low == low_names == {"Ray-Ban RB3025 Aviator - Gold"}
    av = next(a for a in res["alerts"] if a["productName"].startswith("Ray-Ban RB3025"))
    assert av["currentStock"] == 4


def test_stock_received_today_is_never_dead(monkeypatch):
    """17 Wayfarers that arrived this morning have not had 90 days to sell."""
    _wire(monkeypatch)
    res = _alerts()
    assert not [a for a in res["alerts"] if a["alertType"] == "DEAD_STOCK"]


# ---------------------------------------------------------------------------
# Verifier round 2
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize(
    "legacy", [{"status": "available"}, {"status": None}], ids=["lowercase", "no-status"]
)
def test_a_legacy_unit_does_not_split_the_count(monkeypatch, legacy):
    """5 units marked AVAILABLE plus 1 legacy unit: both screens count the
    same units (reorder_policy.on_hand, the legacy unit included), so Alerts
    and Low stock both say 6 at this shop's level of 6, never 5 beside 6."""
    units = _units("P-AV", 5) + _units("P-AV", 1, **legacy)
    if legacy["status"] is None:
        units[-1].pop("status")
    _wire(monkeypatch, units=units, products=[{**_PRODUCTS[0], "reorder_levels": {"S1": 6}}])
    (row,) = _low()["items"]
    (alert,) = [a for a in _alerts()["alerts"] if a["productName"].startswith("Ray-Ban RB3025")]
    assert alert["currentStock"] == row["quantity"] == 6
    assert alert["alertType"] == "LOW_STOCK"
    assert alert["actionRequired"] == "Only 6 left - at or below the low-stock level"


def test_dead_stock_outranks_the_low_list(monkeypatch):
    """2 frames on the shelf 200 days, never sold: that is dead stock, not
    'running low'. Being on the low list (5 or fewer units) must not hide it."""
    products = [{**_PRODUCTS[0], "cost_price": 3000}]
    _wire(monkeypatch, units=_units("P-AV", 2, days_old=200), products=products)
    assert [r["_id"] for r in _low()["items"]] == ["P-AV"]  # it IS on the low list
    res = _alerts()
    (alert,) = res["alerts"]
    assert alert["alertType"] == "DEAD_STOCK"
    assert alert["costImpact"] == 6000
    assert res["stats"]["deadStockValue"] == 6000


# ---------------------------------------------------------------------------
# Verifier round 3
# ---------------------------------------------------------------------------


def test_a_discontinued_frame_still_on_the_shelf_is_on_both_screens(monkeypatch):
    """The Aviator is inactive (discontinued) but 3 units are still here. Low
    stock counts units whatever the catalogue flag says, so Alerts must too --
    it used to answer 'No Alerts' beside 'Aviator - Gold, 3 left'. An inactive
    product with nothing on the shelf stays silent (no reorder for it)."""
    products = [{**_PRODUCTS[0], "is_active": False}, {**_PRODUCTS[1], "is_active": False}]
    db = _wire(monkeypatch, units=_units("P-AV", 3), products=products)
    # The discontinued Wayfarer sold out last week: scored, it would be an
    # 'Out of stock - reorder' alert for a product nobody stocks any more.
    db.orders.insert_one({
        "status": "DELIVERED", "store_id": "S1", "created_at": _NOW - timedelta(days=5),
        "items": [{"barcode": "RB2140", "quantity": 2}],
    })
    (row,) = _low()["items"]
    assert row["name"] == "Ray-Ban RB3025 Aviator - Gold"
    (alert,) = _alerts()["alerts"]  # the Wayfarer (0 units) is not scored
    assert alert["productName"] == row["name"]
    assert alert["alertType"] == "LOW_STOCK"
    assert alert["currentStock"] == row["quantity"] == 3


# ---------------------------------------------------------------------------
# Verifier round 4: a discontinued product that is still selling
# ---------------------------------------------------------------------------


def test_a_discontinued_product_still_selling_is_never_a_reorder(monkeypatch):
    """Both frames are discontinued (catalog soft-delete: the counter can no
    longer sell them) but sold well this month. The Aviator (3 left, reorder
    qty 5) was 'REORDER_ALERT ~4 days of stock left - reorder 16 units'; the
    Wayfarer (30 left) was 'FAST_MOVING - keep well stocked'. Reorder is off
    for a discontinued product, so the Aviator is informational LOW_STOCK with
    no order qty, the Wayfarer says nothing, and the low-stock list flags the
    Aviator as reorder-off too."""
    products = [
        {**_PRODUCTS[0], "is_active": False, "reorder_quantity": 5},
        {**_PRODUCTS[1], "is_active": False, "reorder_quantity": 5},
    ]
    db = _wire(monkeypatch, units=_units("P-AV", 3) + _units("P-WAY", 30), products=products)
    db.orders.insert_many([
        {
            "status": "DELIVERED", "store_id": "S1", "created_at": _NOW - timedelta(days=d % 25 + 1),
            "items": [{"barcode": "RB3025-GLD", "quantity": 1}, {"barcode": "RB2140", "quantity": 1}],
        }
        for d in range(20)
    ])
    (alert,) = _alerts()["alerts"]
    assert alert["productName"] == "Ray-Ban RB3025 Aviator - Gold"
    assert alert["alertType"] == "LOW_STOCK"
    assert alert["recommendedOrder"] == 0 and alert["costImpact"] == 0
    (row,) = _low()["items"]
    assert row["auto_reorder_disabled"] is True
    assert row["discontinued"] is True


# ---------------------------------------------------------------------------
# A provisional product (ruling 13) is new until it is catalogued or deleted
# ---------------------------------------------------------------------------


def _po_door_frame(db, model, colour):
    """A frame bought on a PO before anyone catalogued it, made by the REAL PO
    door: provisional, inactive, catalog_status DRAFT. This shop's level 5."""
    from api.services import product_master as pm
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(db.products)
    doc = pm.create_via_door(
        {
            "category": "FR", "brand": "Ray-Ban", "model": model, "colour": colour,
            "size": "58", "mrp": 9000, "cost_price": 5000,
            "as_draft": True, "provisional": True,
        },
        source="FORM", actor="buyer", actor_name="buyer",
        product_repo=repo, audit_repo=None, db=db,
    )
    pid = doc["product_id"]
    repo.update(pid, {"reorder_levels": {"S1": 5}})
    return repo, pid


def _switched_on_and_sold(db, repo, pid, catalogued):
    """Switched on (optionally after cataloguing finished: the real restamp
    moves DRAFT -> ACTIVE) and sold 20 units this month."""
    from api.services import product_master as pm

    if catalogued:
        pm.apply_restamp_atomic(pid, repo.find_by_id(pid), {"offer_price": 8000}, product_repo=repo)
    repo.update(pid, {"offer_price": 8000, "is_active": True})
    sku = repo.find_by_id(pid)["sku"]
    db.orders.insert_many([
        {
            "status": "DELIVERED", "store_id": "S1", "created_at": _NOW - timedelta(days=d % 25 + 1),
            "items": [{"barcode": sku, "quantity": 1}],
        }
        for d in range(20)
    ])


def _wire_units(mp, db, units):
    from database.repositories.product_repository import ProductRepository, StockRepository

    db.stock_units.insert_many([dict(u) for u in units])
    mp.setattr(inv, "get_stock_repository", lambda: StockRepository(db.stock_units))
    mp.setattr(inv, "get_product_repository", lambda: ProductRepository(db.products))
    mp.setattr(inv, "_get_db", lambda: db)


def test_a_po_door_product_never_switched_on_is_new_not_discontinued(monkeypatch):
    """Bought last week on the PO door, 3 received, not catalogued or switched
    on yet: inactive, but the Reorder dashboard must not call it
    'Discontinued - not reordered' -- it can be ordered like any product."""
    import mongomock

    db = mongomock.MongoClient().db
    repo, pid = _po_door_frame(db, "RB3025", "Gold")
    repo.update(pid, {"reorder_quantity": 5})
    _wire_units(monkeypatch, db, _units(pid, 3))
    (row,) = _low()["items"]
    assert row["discontinued"] is False
    assert row["auto_reorder_disabled"] is False


@pytest.mark.parametrize("catalogued", [False, True], ids=["deleted-as-draft", "deleted-after-cataloguing"])
def test_a_deleted_provisional_product_is_never_a_reorder(monkeypatch, catalogued):
    """OPEN 1, probe 1: a PO-door Aviator was switched on, sold 20 this month,
    then deleted (the real soft-delete) with 3 left. 'provisional' is never
    cleared, so it read 'not discontinued': REORDER_ALERT '~4 days of stock
    left - reorder 16 units', and Generate PO raised a PO for it."""
    import mongomock

    db = mongomock.MongoClient().db
    repo, pid = _po_door_frame(db, "RB3025", "Gold")
    repo.update(pid, {"reorder_quantity": 5})
    _switched_on_and_sold(db, repo, pid, catalogued)
    repo.soft_delete(pid)
    _wire_units(monkeypatch, db, _units(pid, 3))
    (alert,) = _alerts()["alerts"]
    assert alert["alertType"] == "LOW_STOCK"
    assert alert["recommendedOrder"] == 0 and alert["costImpact"] == 0
    (row,) = _low()["items"]
    assert row["discontinued"] is True  # 'Discontinued - not reordered'
    assert row["auto_reorder_disabled"] is True  # Generate PO skips it


def test_a_provisional_product_switched_off_after_cataloguing_is_discontinued(monkeypatch):
    """OPEN 1, probe 2: the same lifecycle with the default reorder_quantity
    -1, ending in a switch-off (no delete). The 30-left Wayfarer was
    'FAST_MOVING - keep well stocked', and the 3-left Aviator showed
    'Auto-reorder off - Enable it via the settings icon', not 'Discontinued'."""
    import mongomock

    db = mongomock.MongoClient().db
    units = []
    for model, colour, left in (("RB3025", "Gold", 3), ("RB2140", "Black", 30)):
        repo, pid = _po_door_frame(db, model, colour)
        assert repo.find_by_id(pid)["reorder_quantity"] == -1  # the door's default
        _switched_on_and_sold(db, repo, pid, catalogued=True)
        repo.update(pid, {"is_active": False})
        units += _units(pid, left)
    _wire_units(monkeypatch, db, units)
    alerts = _alerts()["alerts"]
    assert not [a for a in alerts if a["alertType"] in ("FAST_MOVING", "REORDER_ALERT")]
    (row,) = _low()["items"]
    assert row["quantity"] == 3
    assert row["discontinued"] is True


# ---------------------------------------------------------------------------
# After the per-shop reorder levels (D12, #1179): the shop's own level decides
# ---------------------------------------------------------------------------


def test_the_shops_own_level_decides_on_both_screens(monkeypatch):
    """4 Aviators at a shop whose level is 2, and 4 Wayfarers with no level
    set: neither is low on Low stock, so Alerts must not call either low (a
    fixed 5-unit cut would call both 'Only 4 left')."""
    products = [
        {**_PRODUCTS[0], "reorder_levels": {"S1": 2}},
        {**_PRODUCTS[1], "reorder_levels": {"S2": 9}},  # another shop's level only
    ]
    _wire(monkeypatch, units=_units("P-AV", 4) + _units("P-WAY", 4), products=products)
    assert _low()["items"] == []
    assert not [a for a in _alerts()["alerts"] if a["alertType"] == "LOW_STOCK"]
