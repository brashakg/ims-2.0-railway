"""
IMS 2.0 - reorder_quantity = -1 means "no auto-reorder" (owner decision)
========================================================================
Covers the 2026-07-04 owner decision:

  1. api/services/reorder_policy.auto_reorder_disabled semantics:
     explicit <= 0 -> disabled; missing/None/garbage -> enabled (legacy).
  2. The canonical create door stamps reorder_quantity = -1 on every new
     spine product (product_master.normalise_payload), and a door-supplied
     value (extra_fields) is honoured.
  3. Consumer guards -- a product with reorder_quantity = -1 gets NO reorder
     suggestion / auto-order:
       - inventory._build_stock_alert: no REORDER_ALERT; LOW_STOCK stays
         informational (recommendedOrder 0).
       - buy_desk.build_row: buy_signal is None (FE shows "-").
       - jarvis _compute_inventory_live: low-stock alert kept, reorder
         recommendation dropped.
       - TASKMASTER _draft_reorders: no auto-draft PO for a disabled SKU.
  4. catalog.py InventoryInput default + products.py ProductUpdate accepts -1.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_reorder_disable.py -q
"""

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services.reorder_policy import auto_reorder_disabled  # noqa: E402
from api.services import buy_desk as bd  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.routers import inventory as inv  # noqa: E402
from api.routers import catalog as cat  # noqa: E402
from api.routers import products as prod_router  # noqa: E402
from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """Keep the product-master mirror OFF (mirrors test_unification_9)."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Policy semantics
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_explicit_nonpositive_is_disabled(self):
        assert auto_reorder_disabled({"reorder_quantity": -1}) is True
        assert auto_reorder_disabled({"reorder_quantity": 0}) is True
        assert auto_reorder_disabled({"reorder_quantity": "-1"}) is True

    def test_positive_is_enabled(self):
        assert auto_reorder_disabled({"reorder_quantity": 1}) is False
        assert auto_reorder_disabled({"reorder_quantity": 25}) is False

    def test_missing_or_garbage_is_legacy_enabled(self):
        assert auto_reorder_disabled({}) is False
        assert auto_reorder_disabled({"reorder_quantity": None}) is False
        assert auto_reorder_disabled({"reorder_quantity": "n/a"}) is False
        assert auto_reorder_disabled(None) is False
        assert auto_reorder_disabled("not-a-doc") is False

    def test_catalog_products_nested_inventory_shape(self):
        assert auto_reorder_disabled(
            {"inventory": {"reorder_quantity": -1}}
        ) is True
        assert auto_reorder_disabled(
            {"inventory": {"reorder_quantity": 10}}
        ) is False


# ---------------------------------------------------------------------------
# 2. Create door stamps -1
# ---------------------------------------------------------------------------


def _frame_payload():
    return {
        "category": "FRAME",
        "brand": "Ray-Ban",
        "model": "RB-2140",
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "attributes": {},
    }


class TestCreateDoorStamp:
    def test_create_via_door_defaults_reorder_quantity_minus1(self):
        repo = ProductRepository(MockCollection("products"))
        created = pm.create_via_door(
            _frame_payload(), source="FORM", actor="tester", product_repo=repo
        )
        assert created["reorder_quantity"] == -1

    def test_door_supplied_value_wins_over_default(self):
        repo = ProductRepository(MockCollection("products"))
        created = pm.create_via_door(
            _frame_payload(),
            source="FORM",
            actor="tester",
            extra_fields={"reorder_quantity": 12},
            product_repo=repo,
        )
        assert created["reorder_quantity"] == 12

    def test_catalog_inventory_input_defaults_minus1(self):
        assert cat.InventoryInput().reorder_quantity == -1

    def test_product_update_accepts_minus1(self):
        upd = prod_router.ProductUpdate(reorder_quantity=-1)
        assert upd.reorder_quantity == -1
        with pytest.raises(Exception):
            prod_router.ProductUpdate(reorder_quantity=-2)


# ---------------------------------------------------------------------------
# 3a. /inventory/alerts classifier
# ---------------------------------------------------------------------------


def _alert_product(reorder_quantity):
    return {
        "sku": "FR-X-1",
        "name": "Test Frame",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "stock_quantity": 2,
        "cost_price": 1000.0,
        "reorder_point": 5,
        "reorder_quantity": reorder_quantity,
    }


class TestStockAlertGuard:
    def _classify(self, product):
        return inv._build_stock_alert(
            product,
            sold_30=30,  # 1/day velocity -> 2 days of stock left
            last_sale=datetime(2026, 7, 1),
            now=datetime(2026, 7, 4),
            dead_days=90,
            lead_time_days=14,
        )

    def test_enabled_product_gets_reorder_alert(self):
        alert = self._classify(_alert_product(reorder_quantity=25))
        assert alert is not None
        assert alert["alertType"] == "REORDER_ALERT"
        assert alert["recommendedOrder"] > 0

    def test_disabled_product_never_reorder_alert(self):
        alert = self._classify(_alert_product(reorder_quantity=-1))
        # Falls through to the informational LOW_STOCK tier with NO
        # suggested restock qty.
        assert alert is not None
        assert alert["alertType"] == "LOW_STOCK"
        assert alert["recommendedOrder"] == 0
        assert alert["costImpact"] == 0

    def test_legacy_product_without_field_unchanged(self):
        p = _alert_product(reorder_quantity=25)
        del p["reorder_quantity"]
        alert = self._classify(p)
        assert alert is not None
        assert alert["alertType"] == "REORDER_ALERT"


# ---------------------------------------------------------------------------
# 3b. Buy Desk buy_signal
# ---------------------------------------------------------------------------


class TestBuyDeskGuard:
    def _row(self, product):
        return bd.build_row(
            product,
            readiness={"complete": True, "missing": [], "blockers": [],
                       "purchasable": True},
            push_locked=False,
            on_hand=1,
            on_order=0,
            velocity_per_day=2.0,
        )

    def test_disabled_product_has_no_buy_signal(self):
        row = self._row({"product_id": "P1", "sku": "S1",
                         "reorder_quantity": -1})
        assert row["buy_signal"] is None

    def test_enabled_product_keeps_buy_signal(self):
        row = self._row({"product_id": "P1", "sku": "S1",
                         "reorder_quantity": 10})
        assert isinstance(row["buy_signal"], int)
        assert row["buy_signal"] > 0


# ---------------------------------------------------------------------------
# 3c. Jarvis inventory insights
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, docs):
        self._docs = [dict(d) for d in docs]
        self.inserted = []

    def find(self, query=None, projection=None):
        query = query or {}
        out = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False
                elif d.get(k) != v:
                    ok = False
            if ok:
                out.append(dict(d))
        return _FakeCursor(out)

    def find_one(self, query=None, projection=None):
        res = list(self.find(query))
        return res[0] if res else None

    def insert_one(self, doc):
        self.inserted.append(dict(doc))


class _FakeCursor(list):
    def limit(self, n):
        return _FakeCursor(self[:n])


class TestJarvisGuard:
    def test_disabled_product_alerted_but_not_recommended(self, monkeypatch):
        from api.routers import jarvis as jv

        products = _FakeColl([
            {  # low stock + auto-reorder DISABLED -> alert only
                "sku": "S-OFF", "name": "Disabled", "is_active": True,
                "stock_quantity": 2, "reorder_point": 5,
                "reorder_quantity": -1, "offer_price": 100,
            },
            {  # low stock + ENABLED -> alert + recommendation
                "sku": "S-ON", "name": "Enabled", "is_active": True,
                "stock_quantity": 2, "reorder_point": 5,
                "reorder_quantity": 30, "offer_price": 100,
            },
        ])
        monkeypatch.setattr(
            jv, "get_db_collection",
            lambda name: products if name == "products" else None,
        )
        out = jv.JarvisAnalyticsEngine._compute_inventory_live()
        assert out is not None
        rec_skus = [r["sku"] for r in out["reorder_recommendations"]]
        alert_skus = [a["sku"] for a in out["critical_alerts"]]
        assert "S-ON" in rec_skus
        assert "S-OFF" not in rec_skus
        assert "S-OFF" in alert_skus  # the low-stock ALERT is kept


# ---------------------------------------------------------------------------
# 3d. TASKMASTER auto-draft PO
# ---------------------------------------------------------------------------


class _FakeDb:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name)


class TestTaskmasterGuard:
    def _run(self, stock_docs, product_docs):
        from agents.implementations.taskmaster import TaskmasterAgent

        stock = _FakeColl(stock_docs)
        pos = _FakeColl([])
        products = _FakeColl(product_docs)
        audit = _FakeColl([])
        # The reorder scan matches $expr {quantity < reorder_point} -- the
        # fake can't evaluate $expr, so pre-filter and serve everything.
        stock.find = lambda q=None, p=None: _FakeCursor(
            [dict(d) for d in stock_docs]
        )
        agent = TaskmasterAgent(db=_FakeDb({
            "stock_units": stock,
            "purchase_orders": pos,
            "products": products,
            "agent_audit_log": audit,
        }))
        actions = asyncio.run(agent._draft_reorders())
        return actions, pos

    def test_disabled_sku_never_drafted(self):
        actions, pos = self._run(
            stock_docs=[
                {"sku": "SKU-OFF", "quantity": 1, "reorder_point": 10},
                {"sku": "SKU-ON", "quantity": 1, "reorder_point": 10},
            ],
            product_docs=[
                {"sku": "SKU-OFF", "reorder_quantity": -1},
                {"sku": "SKU-ON", "reorder_quantity": 15},
            ],
        )
        drafted = [a["sku"] for a in actions]
        assert "SKU-ON" in drafted
        assert "SKU-OFF" not in drafted
        assert all(po["sku"] != "SKU-OFF" for po in pos.inserted)

    def test_legacy_sku_without_master_row_still_drafts(self):
        actions, _pos = self._run(
            stock_docs=[{"sku": "SKU-LEGACY", "quantity": 1,
                         "reorder_point": 10}],
            product_docs=[],
        )
        assert [a["sku"] for a in actions] == ["SKU-LEGACY"]
