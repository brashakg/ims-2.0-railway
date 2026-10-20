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
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import gst_rates, margin_basis as mb  # noqa: E402

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

    def find(self, *_a, **_k):
        return _Cursor(self.rows)

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
    d = date.today()
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
    monkeypatch.setattr(recs, "get_db", lambda: _Db(agg={"orders": sales},
                                                    products=[product]))
    body = asyncio.run(recs.purchase_recommendations(
        store_id="S1", lookback_days=90, lead_time_days=7, reorder_cycle_days=30,
        safety_buffer_days=7, min_velocity=1, limit=50, current_user=SU))
    rec = body["recommendations"][0]
    assert rec["unit_margin"] == 400.0  # 1180 / 1.18 - 600, not 1180 - 600
    assert rec["estimated_margin"] == round(rec["suggested_order_qty"] * 400.0, 2)
