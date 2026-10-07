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
# A provisional product (ruling 13) is new until it is switched on or deleted.
# Every step goes through a real door: the PO door, the catalog drawer
# (PUT / DELETE /catalog/products/{id}) and PUT /products/{id}.
# ---------------------------------------------------------------------------

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"], "active_store_id": "S1"}


class _Conn:
    """The dependencies.get_db() shape."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]


def _po_door_frame(mp, db, model, colour, reorder_quantity=5):
    """A frame bought on a PO before anyone catalogued it, made by the REAL PO
    door (provisional, inactive, catalog_status DRAFT), with the doors wired to
    the same database. This shop's level 5. Returns (spine id, drawer id)."""
    from api import dependencies as deps
    from api.routers import catalog
    from api.routers import products as products_mod
    from api.services import product_master as pm
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(db.products)
    for mod in (deps, products_mod):
        mp.setattr(mod, "get_product_repository", lambda: ProductRepository(db.products))
    mp.setattr(deps, "get_db", lambda: _Conn(db))
    mp.setattr(deps, "get_audit_repository", lambda: None)
    mp.setattr(catalog, "_catalog_coll", lambda: db.catalog_products)
    mp.setattr(catalog, "_get_db", lambda: db)
    mp.delenv("DISPATCH_MODE", raising=False)
    mp.delenv("SHOPIFY_DISPATCH_MODE", raising=False)
    mp.setenv("IMS_SHOPIFY_WRITES", "")  # dark: nothing reaches Shopify
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
    db.products.update_one(
        {"product_id": pid},
        {"$set": {"reorder_levels": {"S1": 5}, "reorder_quantity": reorder_quantity}},
    )
    return pid, doc["pim_product_id"]


def _drawer(twin_id, **fields):
    """PUT /catalog/products/{id} -- the Catalog drawer."""
    from api.routers import catalog

    if "offer_price" in fields:
        fields["pricing"] = catalog.PricingPatchInput(offer_price=fields.pop("offer_price"))
    asyncio.run(
        catalog.update_catalog_product(
            twin_id, catalog.ProductUpdateInput(**fields), current_user=_ADMIN
        )
    )


def _drawer_delete(twin_id):
    """DELETE /catalog/products/{id} -- the only product delete there is."""
    from api.routers import catalog

    asyncio.run(catalog.delete_catalog_product(twin_id, current_user=_ADMIN))


def _put_products(pid, **fields):
    """PUT /products/{id}."""
    from api.routers import products as products_mod

    asyncio.run(products_mod.update_product(pid, products_mod.ProductUpdate(**fields), _ADMIN))


def _sold_20(db, pid):
    sku = db.products.find_one({"product_id": pid})["sku"]
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


def _spine(db, pid):
    return db.products.find_one({"product_id": pid})


@pytest.mark.parametrize("door", ["put-products", "catalog-drawer"])
def test_a_po_door_product_catalogued_but_never_switched_on_is_new(monkeypatch, door):
    """Bought on the PO door, catalogued (selling price set), 3 received, not
    switched on yet. Finishing the catalogue switches nothing on, so it is
    still inactive -- and PUT /products moves it to catalog_status ACTIVE.
    It read 'Discontinued - not reordered' and Generate PO skipped it."""
    import mongomock

    db = mongomock.MongoClient().db
    pid, twin = _po_door_frame(monkeypatch, db, "RB3025", "Gold")
    if door == "put-products":
        _put_products(pid, offer_price=8000)
        assert _spine(db, pid)["catalog_status"] == "ACTIVE"  # the restamp ran
    else:
        _drawer(twin, offer_price=8000)
    assert _spine(db, pid)["is_active"] is False  # still not switched on
    _wire_units(monkeypatch, db, _units(pid, 3))
    (row,) = _low()["items"]
    assert row["discontinued"] is False
    assert row["auto_reorder_disabled"] is False


@pytest.mark.parametrize(
    "on_door,end",
    [("catalog-drawer", "switched-off"), ("catalog-drawer", "deleted"), ("put-products", "switched-off")],
)
def test_a_provisional_product_switched_on_sold_then_retired_is_never_a_reorder(
    monkeypatch, on_door, end
):
    """OPEN 1: a PO-door Aviator priced and switched on, sold 20 this month,
    then switched off or deleted with 3 left. 'provisional' is never cleared
    and nothing here moves catalog_status off DRAFT, so it read 'not
    discontinued': REORDER_ALERT 'reorder 16 units', and Generate PO raised a
    PO for it."""
    import mongomock

    db = mongomock.MongoClient().db
    pid, twin = _po_door_frame(monkeypatch, db, "RB3025", "Gold")
    if on_door == "catalog-drawer":
        _drawer(twin, offer_price=8000)
        _drawer(twin, is_active=True)
    else:
        _put_products(pid, offer_price=8000)
        _put_products(pid, is_active=True)
    _sold_20(db, pid)
    if end == "deleted":
        _drawer_delete(twin)
    elif on_door == "catalog-drawer":
        _drawer(twin, is_active=False)
    else:
        _put_products(pid, is_active=False)
    assert _spine(db, pid)["is_active"] is False
    _wire_units(monkeypatch, db, _units(pid, 3))
    (alert,) = _alerts()["alerts"]
    assert alert["alertType"] == "LOW_STOCK"
    assert alert["recommendedOrder"] == 0 and alert["costImpact"] == 0
    (row,) = _low()["items"]
    assert row["discontinued"] is True  # 'Discontinued - not reordered'
    assert row["auto_reorder_disabled"] is True  # Generate PO skips it


def test_a_provisional_product_deleted_before_it_was_ever_switched_on_is_discontinued(monkeypatch):
    """Bought on the PO door, 3 received, then deleted without ever going on
    sale: the delete wrote only is_active False to the spine, which reads
    exactly like 'not switched on yet', so it stayed a reorder."""
    import mongomock

    db = mongomock.MongoClient().db
    pid, twin = _po_door_frame(monkeypatch, db, "RB3025", "Gold")
    _drawer_delete(twin)
    assert _spine(db, pid)["deleted_at"]  # the spine carries the delete too
    _wire_units(monkeypatch, db, _units(pid, 3))
    (row,) = _low()["items"]
    assert row["discontinued"] is True
    assert row["auto_reorder_disabled"] is True


def test_a_provisional_product_switched_off_after_cataloguing_is_discontinued(monkeypatch):
    """OPEN 1, probe 2: the default reorder_quantity -1, catalogued, switched
    on, sold and switched off through PUT /products. The 30-left Wayfarer was
    'FAST_MOVING - keep well stocked', and the 3-left Aviator showed
    'Auto-reorder off - Enable it via the settings icon', not 'Discontinued'."""
    import mongomock

    db = mongomock.MongoClient().db
    units = []
    for model, colour, left in (("RB3025", "Gold", 3), ("RB2140", "Black", 30)):
        pid, _twin = _po_door_frame(monkeypatch, db, model, colour, reorder_quantity=-1)
        _put_products(pid, offer_price=8000)
        _put_products(pid, is_active=True)
        _sold_20(db, pid)
        _put_products(pid, is_active=False)
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
