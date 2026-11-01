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
