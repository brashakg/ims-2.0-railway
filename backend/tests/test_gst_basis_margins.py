"""Margins compare cost and price on the SAME side of GST (services/margin_basis).

Catalogue cost_price and an order line's cost_at_sale are GST-EXCLUSIVE; MRP,
selling price and what the customer pays are GST-INCLUSIVE. Every site below
used to take the ex-GST cost away from the incl-GST price, counting the GST
owed to the government as the shop's margin.

The fixture is one 5% frame and one 18% sunglass, each worth Rs 1000 before
GST (paid 1050 and 1180) and each costing Rs 600:
  right:  revenue 2000, cost 1200, profit 800, margin 40%  (each item 40%)
  wrong:  revenue 2230 -> profit 1030, margin 46.19%; per item 42.86% / 49.15%
Each test is red when its site's fix is reverted.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import gst_rates, margin_basis as mb  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402

SU = {"user_id": "su", "roles": ["SUPERADMIN"], "active_store_id": "S1",
      "store_ids": ["S1"]}


@pytest.fixture(autouse=True)
def _static_gst(monkeypatch):
    """The static GST table only (no hsn_gst_master), inclusive pricing."""
    monkeypatch.setattr(gst_rates, "_load_lookup", lambda: {"by_hsn": {}, "by_cat": {}})
    monkeypatch.delenv("GST_PRICING_MODE", raising=False)


def _line(cat, rate, paid, cost=600.0):
    taxable = 1000.0
    return {"product_id": "P-" + cat, "item_type": cat, "category": cat, "quantity": 1,
            "unit_price": paid, "item_total": paid, "gst_rate": rate,
            "taxable_value": taxable, "tax_amount": round(paid - taxable, 2),
            "cost_at_sale": cost}


def _orders():
    now = datetime.now()
    out = []
    for oid, (cat, rate, paid) in (("O5", ("FR", 5.0, 1050.0)), ("O18", ("SG", 18.0, 1180.0))):
        out.append({"order_id": oid, "store_id": "S1", "customer_id": "C1",
                    "status": "CONFIRMED", "created_at": now, "pricing_model": "inclusive",
                    "grand_total": paid, "tax_amount": round(paid - 1000.0, 2),
                    "round_off": 0.0, "items": [_line(cat, rate, paid)]})
    return out


class _Repo:
    def __init__(self, rows):
        self.rows = rows

    def find_many(self, *_a, **_k):
        return list(self.rows)


class _Cursor(list):
    def limit(self, _n):
        return self


class _Coll:
    def __init__(self, rows, agg):
        self.rows, self.agg = rows, agg

    def find(self, _flt=None, projection=None, *_a, **_k):
        """Honours an inclusion projection, as Mongo does: a field the
        projection leaves out is not there to read."""
        if not projection:
            return _Cursor(self.rows)
        keep = {k for k, v in projection.items() if v}
        if projection.get("_id", 1):
            keep.add("_id")
        return _Cursor([{k: v for k, v in r.items() if k in keep} for r in self.rows])

    def aggregate(self, _p):
        return iter(self.agg)


class _Db:
    """find() returns the seeded rows; aggregate() returns `agg` rows."""

    def __init__(self, agg=None, **colls):
        self.colls, self.agg = colls, agg or {}

    def get_collection(self, name):
        return _Coll(self.colls.get(name, []), self.agg.get(name, []))


# --------------------------------------------------------------------------
# the helper
# --------------------------------------------------------------------------


def test_helper_backs_gst_out_of_the_price():
    assert mb.price_ex_gst(1050, 5) == pytest.approx(1000.0)
    assert mb.price_ex_gst(1180, 18) == pytest.approx(1000.0)
    assert mb.margin_pct_ex_gst(1050, 600, 5) == 40.0
    assert mb.margin_pct_ex_gst(1180, 600, 18) == 40.0
    assert mb.margin_pct_ex_gst(0, 600, 5) is None
    assert mb.product_gst_rate({"category": "FR"}) == 5.0
    assert mb.product_gst_rate({"category": "SG"}) == 18.0
    # a stamped line is read as stamped; a legacy line is backed out at its rate
    assert mb.line_revenue_ex_gst(_line("SG", 18.0, 1180.0)) == 1000.0
    assert mb.line_revenue_ex_gst({"item_total": 1180.0, "category": "SG"}) == pytest.approx(1000.0)
    assert mb.order_revenue_ex_gst(_orders()[1]) == 1000.0
    assert mb.line_unit_cost({"cost_at_sale": 600.0}) == 600.0


def test_exclusive_mode_price_already_is_taxable(monkeypatch):
    monkeypatch.setenv("GST_PRICING_MODE", "exclusive")
    assert mb.price_ex_gst(1000, 18) == 1000.0


# --------------------------------------------------------------------------
# the fixed sites
# --------------------------------------------------------------------------


def test_vendor_margins(monkeypatch):
    from api.routers import analytics_v2 as av2

    products = [
        {"product_id": "P5", "store_id": "S1", "brand": "B", "vendor_name": "V",
         "category": "FR", "mrp": 1050.0, "cost_price": 600.0},
        {"product_id": "P18", "store_id": "S1", "brand": "B", "vendor_name": "V",
         "category": "SG", "mrp": 1180.0, "cost_price": 600.0},
    ]
    monkeypatch.setattr(av2, "_get_db", lambda: _Db(products=products))
    res = asyncio.run(av2.vendor_margins(store_id="S1", current_user=SU))
    assert sorted(p["margin_pct"] for p in res["best_margin_products"]) == [40.0, 40.0]
    v = res["vendors"][0]
    assert v["avg_margin_pct"] == 40.0
    assert v["total_revenue"] == 2000.0 and v["total_cost"] == 1200.0


def test_enterprise_kpis_gross_margin(monkeypatch):
    from api.routers import analytics as an

    monkeypatch.setattr(an, "get_order_repository", lambda: _Repo(_orders()))
    monkeypatch.setattr(an, "get_stock_repository", lambda: _Repo([]))
    monkeypatch.setattr(an, "get_customer_repository", lambda: _Repo([]))
    res = asyncio.run(an.get_enterprise_kpis(current_user=SU, period="month", store_id="S1"))
    m = res["margins"]
    assert res["revenue"]["total"] == 2230.0  # what was paid, unchanged
    assert m["revenue_ex_gst"] == 2000.0
    assert m["gross_profit"] == 800.0
    assert m["gross_margin_percent"] == 40.0


def test_finance_pnl(monkeypatch):
    from api.routers.finance import pnl as pnl_mod

    monkeypatch.setattr(pnl_mod, "_get_db", lambda: _Db(orders=_orders()))
    monkeypatch.setattr(pnl_mod, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pnl_mod, "_payroll_cost", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pnl_mod.je_service, "pnl_adjustments", lambda *_a, **_k: {})
    pnl = asyncio.run(pnl_mod.get_pnl(store_id=None, from_date=None, to_date=None,
                                      current_user=SU))
    assert pnl["revenue"] == 2000.0
    assert pnl["cogs"] == 1200.0
    assert pnl["gross_profit"] == 800.0
    assert pnl["gross_margin"] == 40.0
    assert pnl["net_margin"] == 40.0


def test_finance_pnl_by_store(monkeypatch):
    from api.routers.finance import pnl_breakdown as pb

    monkeypatch.setattr(pb, "_get_db", lambda: _Db(orders=_orders()))
    monkeypatch.setattr(pb, "_store_maps", lambda _db: ({"S1": "E1"}, {}))
    monkeypatch.setattr(pb, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pb, "_payroll_by_store", lambda *_a, **_k: {})
    monkeypatch.setattr(pb, "_store_name_map", lambda _db: {"S1": "Shop"})
    res = asyncio.run(pb.get_pnl_by_store(from_date=None, to_date=None, entity_id=None,
                                          current_user=SU))
    row = res["stores"][0]
    assert row["revenue"] == 2000.0 and row["cogs"] == 1200.0
    assert row["net_profit"] == 800.0 and row["net_margin"] == 40.0


def test_finance_pnl_by_category():
    from api.routers.finance._shared import pnl_by_category

    cats = {c["category"]: c for c in pnl_by_category(_orders(), {})}
    for cat in ("FR", "SG"):
        assert cats[cat]["revenue"] == 1000.0
        assert cats[cat]["gross_profit"] == 400.0
        assert cats[cat]["gross_margin"] == 40.0


def test_cogs_fallback_is_on_revenue_net_of_gst():
    from api.routers.finance._shared import compute_cogs_with_flag

    orders = _orders()
    for o in orders:
        o["items"][0]["cost_at_sale"] = None
    assert compute_cogs_with_flag(orders, {}, fallback_rate=0.6) == (1200.0, 2, 2)


def _period():
    d = ist_today()  # the business day, not the box clock (BUG-104)
    return d - timedelta(days=1), d + timedelta(days=1)


def test_reports_expense_vs_revenue(monkeypatch):
    from api.routers.reports import customers as rc

    monkeypatch.setattr(rc, "get_order_repository", lambda: _Repo(_orders()))
    f, t = _period()
    body = asyncio.run(rc.expense_vs_revenue(store_id=None, from_date=f, to_date=t,
                                             current_user=SU))
    assert body == {"revenue": 2000.0, "cost": 1200.0, "profit": 800.0,
                    "margin_percent": 40.0}


def test_reports_profit_by_category(monkeypatch):
    from api.routers.reports import growth

    monkeypatch.setattr(growth, "get_order_repository", lambda: _Repo(_orders()))
    f, t = _period()
    body = asyncio.run(growth.profit_by_category(store_id=None, from_date=f, to_date=t,
                                                 current_user=SU))
    rows = {r["category"]: r for r in body["data"]}
    for cat in ("FR", "SG"):
        assert rows[cat]["revenue"] == 1000.0 and rows[cat]["cost"] == 600.0
        assert rows[cat]["margin_percent"] == 40.0
    assert body["total_profit"] == 800.0


def test_reports_profit_by_store(monkeypatch):
    from api.routers.reports import growth

    monkeypatch.setattr(growth, "get_order_repository", lambda: _Repo(_orders()))
    f, t = _period()
    body = asyncio.run(growth.profit_by_store(from_date=f, to_date=t, current_user=SU))
    row = body["data"][0]
    assert row["revenue"] == 2000.0 and row["cost"] == 1200.0 and row["profit"] == 800.0


def test_reports_purchase_recommendation_margin(monkeypatch):
    from api.routers.reports import purchase as recs

    sales = [{"_id": "P18", "units_sold": 6, "revenue": 7080.0, "avg_price": 1180.0}]
    product = {"_id": "P18", "product_id": "P18", "name": "Sunglass", "category": "SG",
               "offer_price": 1180.0, "cost_price": 600.0, "reorder_quantity": 5}
    # The last 30 days' sales THE buy rule reads (S5: no level -> 30-day sales).
    from datetime import datetime, timedelta
    orders = [{"store_id": "S1", "status": "DELIVERED",
               "created_at": datetime.utcnow() - timedelta(days=2),
               "items": [{"product_id": "P18", "quantity": 6}]}]
    monkeypatch.setattr(recs, "get_db", lambda: _Db(agg={"orders": sales},
                                                    products=[product], orders=orders))
    body = asyncio.run(recs.purchase_recommendations(
        store_id="S1", lookback_days=90, min_velocity=1, limit=50, current_user=SU))
    rec = body["recommendations"][0]
    assert rec["unit_margin"] == 400.0  # 1180 / 1.18 - 600, not 1180 - 600
    assert rec["estimated_margin"] == round(rec["suggested_order_qty"] * 400.0, 2)


# --------------------------------------------------------------------------
# review round 2 (2026-10-21)
# --------------------------------------------------------------------------

_CATALOGUE = [{"product_id": "P-FR", "cost_price": 600.0},
              {"product_id": "P-SG", "cost_price": 600.0}]


def _no_snapshot_orders():
    """The fixture with no cost_at_sale on any line (older / online orders):
    the cost has to come from the product's catalogue cost_price."""
    orders = _orders()
    for o in orders:
        del o["items"][0]["cost_at_sale"]
    return orders


def test_one_cost_rule_for_the_pnl_and_the_reports():
    from api.routers.finance import _shared

    assert _shared._item_cost is mb.line_unit_cost
    assert _shared._cost_by_product is mb.cost_by_product
    line = {"product_id": "P-FR"}
    assert mb.line_unit_cost(line, {"P-FR": 600.0}) == 600.0
    assert mb.line_unit_cost({**line, "cost_at_sale": 550.0}, {"P-FR": 600.0}) == 550.0
    assert mb.line_unit_cost(line, {}) is None
    assert mb.cost_by_product(_Db(products=_CATALOGUE)) == {"P-FR": 600.0, "P-SG": 600.0}
    assert mb.cost_by_product(None) == {}


def test_reports_fall_back_to_catalogue_cost(monkeypatch):
    from api.routers.reports import customers as rc, growth

    db = _Db(products=_CATALOGUE)
    for mod in (rc, growth):
        monkeypatch.setattr(mod, "get_order_repository",
                            lambda: _Repo(_no_snapshot_orders()))
        monkeypatch.setattr(mod, "get_db", lambda: db)
    f, t = _period()
    body = asyncio.run(rc.expense_vs_revenue(store_id=None, from_date=f, to_date=t,
                                             current_user=SU))
    assert body["cost"] == 1200.0 and body["margin_percent"] == 40.0
    body = asyncio.run(growth.profit_by_category(store_id=None, from_date=f, to_date=t,
                                                 current_user=SU))
    assert {r["category"]: r["cost"] for r in body["data"]} == {"FR": 600.0, "SG": 600.0}
    assert body["total_profit"] == 800.0
    body = asyncio.run(growth.profit_by_store(from_date=f, to_date=t, current_user=SU))
    assert body["data"][0]["cost"] == 1200.0 and body["total_profit"] == 800.0


def test_enterprise_kpis_fall_back_to_catalogue_cost(monkeypatch):
    from api.routers import analytics as an

    monkeypatch.setattr(an, "get_order_repository", lambda: _Repo(_no_snapshot_orders()))
    monkeypatch.setattr(an, "get_stock_repository", lambda: _Repo([]))
    monkeypatch.setattr(an, "get_customer_repository", lambda: _Repo([]))
    monkeypatch.setattr(an, "get_db", lambda: _Db(products=_CATALOGUE))
    res = asyncio.run(an.get_enterprise_kpis(current_user=SU, period="month", store_id="S1"))
    assert res["margins"]["gross_profit"] == 800.0
    assert res["margins"]["gross_margin_percent"] == 40.0


def _pnl(monkeypatch, orders, agg=None):
    from api.routers.finance import pnl as pnl_mod

    monkeypatch.setattr(pnl_mod, "_get_db", lambda: _Db(agg=agg, orders=orders))
    monkeypatch.setattr(pnl_mod, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pnl_mod, "_payroll_cost", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pnl_mod.je_service, "pnl_adjustments", lambda *_a, **_k: {})
    return asyncio.run(pnl_mod.get_pnl(store_id=None, from_date=None, to_date=None,
                                       current_user=SU))


def test_pnl_reports_gross_sales_incl_gst_beside_revenue(monkeypatch):
    pnl = _pnl(monkeypatch, _orders(),
               agg={"orders": [{"_id": None, "revenue": 2230.0, "tax": 230.0}]})
    assert pnl["gross_sales_incl_gst"] == 2230.0  # what the customers paid
    assert pnl["tax_collected"] == 230.0
    assert pnl["revenue"] == 2000.0  # the P&L's revenue: net of GST


def test_legacy_order_with_only_total_is_not_zero(monkeypatch):
    legacy = {"order_id": "L1", "store_id": "S1", "status": "CONFIRMED",
              "created_at": datetime.now(), "total": 1180.0, "tax_amount": 180.0,
              # a legacy line: no taxable_value / tax_amount split to fall back on
              "items": [{"product_id": "P-SG", "category": "SG", "quantity": 1,
                         "total": 1180.0, "cost_at_sale": 600.0}]}
    assert mb.order_revenue_ex_gst(legacy) == 1000.0
    # The P&L order fetch projects `total` too (the fake honours projections).
    assert _pnl(monkeypatch, [legacy])["revenue"] == 1000.0


def _mixed_rate_rounded_order():
    """ONE bill: a 5% frame (taxable 1000.38, GST 50.02) and an 18% sunglass
    (taxable 1000, GST 180), each costing 600. The lines total 2230.40; the
    till rounds the bill to 2230 and books round_off -0.40. Taxable value =
    2230 - (-0.40) - 230.02 = 2000.38 (ignoring the round off: 1999.98)."""
    fr = {**_line("FR", 5.0, 1050.40), "taxable_value": 1000.38, "tax_amount": 50.02}
    sg = _line("SG", 18.0, 1180.0)
    return {"order_id": "M1", "store_id": "S1", "customer_id": "C1",
            "status": "CONFIRMED", "created_at": datetime.now(),
            "pricing_model": "inclusive", "grand_total": 2230.0,
            "tax_amount": 230.02, "round_off": -0.40, "items": [fr, sg]}


def test_mixed_rate_order_with_round_off(monkeypatch):
    from api.routers.finance import pnl_breakdown as pb
    from api.routers.finance._shared import pnl_by_category
    from api.routers.reports import growth

    order = _mixed_rate_rounded_order()
    assert mb.order_revenue_ex_gst(order) == 2000.38
    pnl = _pnl(monkeypatch, [order])
    assert pnl["revenue"] == 2000.38 and pnl["cogs"] == 1200.0
    assert pnl["gross_profit"] == 800.38

    monkeypatch.setattr(pb, "_get_db", lambda: _Db(orders=[order]))
    monkeypatch.setattr(pb, "_store_maps", lambda _db: ({"S1": "E1"}, {}))
    monkeypatch.setattr(pb, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pb, "_payroll_by_store", lambda *_a, **_k: {})
    monkeypatch.setattr(pb, "_store_name_map", lambda _db: {"S1": "Shop"})
    row = asyncio.run(pb.get_pnl_by_store(from_date=None, to_date=None, entity_id=None,
                                          current_user=SU))["stores"][0]
    assert row["revenue"] == 2000.38 and row["net_profit"] == 800.38

    cats = {c["category"]: c for c in pnl_by_category([order], {})}
    assert cats["FR"]["revenue"] == 1000.38 and cats["SG"]["revenue"] == 1000.0

    monkeypatch.setattr(growth, "get_order_repository", lambda: _Repo([order]))
    f, t = _period()
    body = asyncio.run(growth.profit_by_store(from_date=f, to_date=t, current_user=SU))
    assert body["data"][0]["revenue"] == 2000.38 and body["total_profit"] == 800.38


def test_revenue_breakdown_carries_revenue_ex_gst(monkeypatch):
    from api.routers.finance import pnl as pnl_mod

    row = {"_id": None, "total_revenue": 2230.0, "total_orders": 1, "total_tax": 230.02,
           "total_discount": 0.0, "total_round_off": -0.40}
    monkeypatch.setattr(pnl_mod, "_get_db", lambda: _Db(agg={"orders": [row]}))
    res = asyncio.run(pnl_mod.get_revenue(period="day", store_id=None, current_user=SU))
    assert res["total_revenue"] == 2230.0  # paid, unchanged
    assert res["revenue_ex_gst"] == 2000.38  # the P&L's basis


def test_mongo_line_rule_agrees_with_python():
    """The database sum and line_revenue_ex_gst give the same ex-GST revenue,
    including a line with neither taxable_value nor gst_rate (which the Python
    rule prices at its category rate; Mongo cannot, so it hands the gross back
    to group_revenue_ex_gst). Run on mongomock's real aggregation engine."""
    mongomock = pytest.importorskip("mongomock")
    lines = [
        {"product_id": "P", "item_total": 1180.0, "gst_rate": 18.0, "taxable_value": 1000.0},
        {"product_id": "P", "item_total": 1180.0, "gst_rate": 18.0},   # rated, no split
        {"product_id": "P", "item_total": 1180.0, "category": "SG"},   # unrated -> 18%
    ]
    coll = mongomock.MongoClient().db.orders
    coll.insert_many([{"items": [dict(it)]} for it in lines])
    rows = list(coll.aggregate([
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.product_id", **mb.LINE_REVENUE_EX_GST_GROUP}},
    ]))
    want = sum(mb.line_revenue_ex_gst(it) for it in lines)
    assert want == pytest.approx(3000.0)
    assert mb.group_revenue_ex_gst(rows[0]) == pytest.approx(want)
