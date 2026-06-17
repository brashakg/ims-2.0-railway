"""
IMS 2.0 - Promo engine DARK-BY-DEFAULT proof (F11/F12)
======================================================
The revenue-critical guarantee: when ``PROMO_ENGINE_ENABLED`` is unset/off,
``orders.create_order`` behaves EXACTLY as before -- the promo engine is never
called, no discount is applied, ``applied_promos`` stays [], and the order
totals are byte-identical to the no-engine path. This holds EVEN WHEN active
promo rules exist in the DB that WOULD fire if the flag were on.

We assert it three ways:
  1. The dark gate ``promotions.promo_engine_enabled()`` defaults False.
  2. An order created with the flag OFF + a matching active promo rule present
     gets the SAME grand_total as the same order with NO rules at all (the
     rule is ignored), and applied_promos == [], promo_discount_total == 0.
  3. The pure engine returns a no-op for an empty rule list (the flag-off path
     never passes any rules to the engine).

Reuses the in-memory FakeDB harness from test_walkouts (no real mongo needed).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


# ---------------------------------------------------------------------------
# 1. The dark gate defaults OFF
# ---------------------------------------------------------------------------
def test_promo_engine_flag_defaults_off(monkeypatch):
    from api.routers import promotions

    monkeypatch.delenv("PROMO_ENGINE_ENABLED", raising=False)
    assert promotions.promo_engine_enabled() is False


def test_promo_engine_flag_truthy_values(monkeypatch):
    from api.routers import promotions

    for val in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("PROMO_ENGINE_ENABLED", val)
        assert promotions.promo_engine_enabled() is True
    for val in ("", "0", "false", "off", "no"):
        monkeypatch.setenv("PROMO_ENGINE_ENABLED", val)
        assert promotions.promo_engine_enabled() is False


# ---------------------------------------------------------------------------
# 2. End-to-end: flag OFF ignores an active matching promo rule entirely
# ---------------------------------------------------------------------------
@pytest.fixture
def promo_orders(monkeypatch):
    """Wire FakeDB + repos into orders, AND wire orders.create_order's _get_db
    (which the promo block reads) to the same FakeDB so an active promo rule is
    visible to the would-be engine path."""
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from api.routers import promotions as promo_module
    from api import dependencies as deps_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)
    # The promo block in create_order reads orders._get_db(); point it at FakeDB
    # so an active promo rule IS present (proves the flag, not absence-of-rules,
    # is what makes it dark).
    monkeypatch.setattr(orders_module, "_get_db", lambda: fake_db)
    monkeypatch.setattr(promo_module, "_get_db", lambda: fake_db)
    # Neutralise the period-lock check (it also calls _get_db -> FakeDB).
    import api.routers.finance as finance_module

    monkeypatch.setattr(finance_module, "check_period_locked", lambda *a, **k: None)

    customer_repo.create(
        {"customer_id": "cust-x", "name": "Test", "mobile": "9100000099",
         "phone": "9100000099"}
    )
    return {"db": fake_db, "order_repo": order_repo}


def _post_frame_order(client, auth_headers, unit_price=1000.0):
    payload = {
        "customer_id": "cust-x",
        "items": [
            {
                "product_id": "custom-frame",
                "product_name": "Test Frame",
                "item_type": "FRAME",
                "category": "FRAME",
                "discount_category": "MASS",
                "quantity": 1,
                "unit_price": unit_price,
            }
        ],
    }
    return client.post("/api/v1/orders", json=payload, headers=auth_headers)


def _seed_matching_promo(fake_db):
    """An active THRESHOLD promo that WOULD give 10% off a >=500 cart."""
    fake_db.get_collection("promo_rules").insert_one({
        "promo_id": "PR-DARK-TEST",
        "name": "Dark test 10% over 500",
        "promo_type": "THRESHOLD",
        "reward_value": 10,
        "min_cart_value": 500,
        "active": True,
        "stackable": False,
        "uses_count": 0,
        "store_ids": None,
    })


def test_flag_off_ignores_active_promo_totals_identical(
    client, auth_headers, promo_orders, monkeypatch
):
    """Flag OFF: an order created with a matching active promo rule present has
    the SAME grand_total as one created with NO rules -> the engine is dark."""
    monkeypatch.delenv("PROMO_ENGINE_ENABLED", raising=False)
    fake_db = promo_orders["db"]

    # Baseline: NO promo rules at all.
    resp_baseline = _post_frame_order(client, auth_headers, 1000.0)
    assert resp_baseline.status_code in (200, 201), resp_baseline.text
    baseline = resp_baseline.json()
    baseline_total = baseline.get("grand_total")
    assert baseline.get("applied_promos", []) == []
    assert baseline.get("promo_discount_total", 0) == 0

    # Now add a matching active promo rule and create the SAME order again.
    _seed_matching_promo(fake_db)
    resp_with_rule = _post_frame_order(client, auth_headers, 1000.0)
    assert resp_with_rule.status_code in (200, 201), resp_with_rule.text
    with_rule = resp_with_rule.json()

    # DARK PROOF: totals identical, no promo applied, despite the live rule.
    assert with_rule.get("grand_total") == baseline_total
    assert with_rule.get("applied_promos", []) == []
    assert with_rule.get("promo_discount_total", 0) == 0
    # And nothing was written to the audit collection.
    assert fake_db.get_collection("promo_applications").count_documents({}) == 0
    # The rule's uses_count was NOT incremented.
    rule = fake_db.get_collection("promo_rules").find_one(
        {"promo_id": "PR-DARK-TEST"}
    )
    assert rule["uses_count"] == 0


# ---------------------------------------------------------------------------
# 3. Pure engine: empty rule list is a no-op (the flag-off path passes none)
# ---------------------------------------------------------------------------
def test_engine_empty_rules_is_noop():
    from api.services import promo_engine as pe

    cart = {"items": [{"product_id": "a", "item_id": "a", "quantity": 1,
                       "unit_price": 1000.0, "discount_category": "MASS"}]}
    out = pe.evaluate_promos(cart, None, None, [])
    assert out["applied"] is False
    assert out["total_discount"] == 0.0


# ---------------------------------------------------------------------------
# 4. ON path: evaluate_for_order applies, commit increments + audits atomically
# ---------------------------------------------------------------------------
class _AtomicDB:
    """FakeDB-style DB that also supports find_one_and_update (the atomic
    uses_count guard) so the ON-path apply/commit can be exercised."""

    is_connected = True

    def __init__(self):
        from tests.test_walkouts import FakeCollection

        class _AtomicColl(FakeCollection):
            def find_one_and_update(self, flt, update, return_document=None):
                from tests.test_walkouts import _doc_matches

                for d in self.docs:
                    if _doc_matches(d, flt):
                        inc = (update or {}).get("$inc", {}) or {}
                        for k, v in inc.items():
                            d[k] = (d.get(k) or 0) + v
                        st = (update or {}).get("$set", {}) or {}
                        d.update(st)
                        return dict(d)
                return None

        self._coll_cls = _AtomicColl
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = self._coll_cls()
        return self._collections[name]


def test_on_path_applies_and_commits_atomically():
    from api.routers import promotions

    db = _AtomicDB()
    db.get_collection("promo_rules").insert_one({
        "promo_id": "PR-ON", "name": "10% over 500", "promo_type": "THRESHOLD",
        "reward_value": 10, "min_cart_value": 500, "active": True,
        "stackable": False, "uses_count": 0, "store_ids": None,
        "max_uses_total": 100,
    })
    items = [{"product_id": "a", "item_id": "a", "quantity": 1,
              "unit_price": 1000.0, "discount_category": "MASS",
              "item_total": 1000.0, "cost_at_sale": 400.0}]

    ev = promotions.evaluate_for_order(
        db, store_id="BV-TEST-01", customer_id="cust-x", items=items, customer=None
    )
    assert ev["applied"] is True
    assert ev["total_discount"] == 100.0  # 10% of 1000, under the 15% MASS cap
    assert ev["applied_promos"][0]["promo_id"] == "PR-ON"

    promotions.commit_promo_application(
        db, order_id="ORD-1", order_number="BV-1", store_id="BV-TEST-01",
        customer_id="cust-x", cashier_id="u-1", items=items,
        evaluation=ev["evaluation"],
    )
    # uses_count incremented atomically.
    rule = db.get_collection("promo_rules").find_one({"promo_id": "PR-ON"})
    assert rule["uses_count"] == 1
    # promo_applications audit row written with real margin.
    apps = list(db.get_collection("promo_applications").find({}))
    assert len(apps) == 1
    assert apps[0]["order_id"] == "ORD-1"
    assert apps[0]["total_discount_given"] == 100.0
    assert apps[0]["estimated_cogs"] == 400.0
    assert apps[0]["cogs_is_estimated"] is False
