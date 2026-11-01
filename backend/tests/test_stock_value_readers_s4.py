"""S4 review: every stock-value reader reads THE rule (services/stock_value):
pieces on the shelf x the product's ONE average cost; pieces with no cost are
counted ("too low by N"), never priced at the offer; what we pay is the
accounts roles' only (a STORE_MANAGER gets the field masked).

Pinned per reader: analytics dashboard-summary (inventory_value), analytics
store-performance (stock_value), Jarvis (inventory_value, top_stock_value,
catalog_by_store). World: tests/test_per_shop_reorder_levels -- at BV-DHN-02
one piece each of two products at 4000 (8000), plus one piece of a product
with no cost.
"""

from tests.test_per_shop_reorder_levels import (  # noqa: F401 - `world` is a fixture
    DHN,
    PUN,
    _ADMIN,
    _DHN_MGR,
    _client,
    _product,
    _units,
    world,
)


def _with_uncosted(db):
    db.products.insert_one(_product("P-NOCOST", cost_price=None, offer_price=7000.0))
    db.stock_units.insert_many(_units("P-NOCOST", DHN, 1))


def test_dashboard_summary_inventory_value_is_pieces_times_average(world):
    _with_uncosted(world)
    body = _client(_ADMIN).get("/api/v1/analytics/dashboard-summary",
                               params={"store_id": DHN, "period": "month"}).json()
    assert (body["inventory_value"], body["inventory_uncosted_units"]) == (8000.0, 1)
    mgr = _client(_DHN_MGR).get("/api/v1/analytics/dashboard-summary",
                                params={"store_id": DHN, "period": "month"}).json()
    assert "inventory_value" not in mgr and "inventory_uncosted_units" not in mgr


def test_store_performance_stock_value_is_pieces_times_average(world):
    _with_uncosted(world)
    rows = {r["store_id"]: r for r in _client(_ADMIN).get(
        "/api/v1/analytics/store-performance", params={"period": "month"}).json()["stores"]}
    assert (rows[DHN]["stock_value"], rows[DHN]["stock_uncosted_units"]) == (8000.0, 1)
    res = _client(_DHN_MGR).get("/api/v1/analytics/store-performance", params={"period": "month"})
    assert res.status_code == 200 and res.json()["stores"]
    for row in res.json()["stores"]:
        assert "stock_value" not in row and "stock_uncosted_units" not in row


def test_jarvis_values_stock_at_cost_and_counts_the_uncosted(world):
    from api.routers import jarvis

    _with_uncosted(world)
    # 17 + 17 pieces at 4000 across the shops; the no-cost piece adds nothing.
    overview = jarvis.JarvisAnalyticsEngine._compute_overview_live()
    assert (overview["inventory"]["inventory_value"], overview["inventory"]["inventory_uncosted_units"]) == (
        136000.0, 1)
    inv = jarvis.JarvisAnalyticsEngine._compute_inventory_live() or {}
    assert (inv["totals"]["inventory_value"], inv["totals"]["inventory_uncosted_units"]) == (136000.0, 1)
    ctx = jarvis.JarvisAnalyticsEngine.get_extended_context()
    top = {r["name"]: r for r in ctx["top_stock_value"]}
    assert (top["Carrera P-FRAME"]["stock_value"], top["Carrera P-FRAME"]["pieces"]) == (68000.0, 17)
    assert (top["Carrera P-NOCOST"]["stock_value"], top["Carrera P-NOCOST"]["uncosted_pieces"]) == (0.0, 1)
    by_store = {r["store_id"]: r for r in ctx["catalog_by_store"]}
    assert (by_store[DHN]["total_stock_value"], by_store[DHN]["uncosted_pieces"]) == (8000.0, 1)
    assert by_store[PUN]["total_stock_value"] == 120000.0


def test_a_ledger_row_carries_the_average_never_a_catalogue_only_price():
    from api.routers.inventory.stock import _ledger_row

    def row(product):
        return _ledger_row({"product_id": "P", "sku": "S", **product}, 1, 0, {}, DHN)["cost_price"]

    assert row({"cost_price": 3340.0}) == 3340.0
    assert row({"cost_price": 999.0, "cost_price_is_catalogue_only": True}) is None
    assert row({"cost_price": "junk"}) is None


def test_inventory_intelligence_values_only_the_shelf_at_the_average(world):
    """S4 review round 3: inventory-intelligence reads THE rule -- sold and
    transferred pieces add nothing, and a piece whose product has no products
    doc is uncosted, never valued at its catalogue copy's price."""
    _with_uncosted(world)
    world.stock_units.insert_many(_units("P-FRAME", DHN, 1, status="SOLD"))
    world.stock_units.insert_one({"stock_id": "U-GONE", "product_id": "P-FRAME", "store_id": DHN,
                                  "status": "TRANSFERRED"})
    world.stock_units.insert_one({"stock_id": "U-ORPHAN", "product_id": "CAT-ONLY", "store_id": DHN,
                                  "status": "AVAILABLE", "quantity": 1})
    world.catalog_products.insert_one({"id": "CAT-ONLY", "sku": "CAT-ONLY", "title": "Twin",
                                       "pricing": {"cost_price": 1000.0}})
    cli = _client(_ADMIN)
    summary = cli.get("/api/v1/reports/inventory/summary", params={"store_id": DHN}).json()["summary"]
    intel = cli.get("/api/v1/analytics/inventory-intelligence", params={"store_id": DHN}).json()
    assert summary["total_value"] == 8000.0
    assert intel["total_inventory"]["value"] == summary["total_value"], intel["total_inventory"]
    assert intel["total_inventory"]["uncosted_units"] == summary["uncosted_units"] == 2
    assert intel["dead_stock"]["total_value"] <= summary["total_value"]


def test_inventory_intelligence_low_stock_total_is_pieces_times_the_average(world):
    """S4 review (fix round 1): the low-stock block's total_value reads THE
    rule -- on-hand pieces x the product's average. At Dhanbad P-FRAME (1
    piece, level 2) and P-OWNER (given a level of 2 here: 1 piece) are low;
    P-FRAME's price is catalogue-only, so it is no cost: 1 x 4000 + 0 = 4000
    (the shelf price would say 8000)."""
    world.products.update_one({"product_id": "P-OWNER"}, {"$set": {"reorder_levels": {DHN: 2}}})
    world.products.update_one({"product_id": "P-FRAME"}, {"$set": {"cost_price_is_catalogue_only": True}})
    intel = _client(_ADMIN).get("/api/v1/analytics/inventory-intelligence", params={"store_id": DHN}).json()
    assert intel["low_stock"]["count"] == 2
    assert intel["low_stock"]["total_value"] == 4000.0
    mgr = _client(_DHN_MGR).get("/api/v1/analytics/inventory-intelligence", params={"store_id": DHN}).json()
    assert "total_value" not in mgr["low_stock"]
