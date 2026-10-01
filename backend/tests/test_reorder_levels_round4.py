"""
Per-shop reorder levels - review round 4 (backend). One test per finding, each
revert-proofed (the guard it pins was broken, the test watched failing).
Helpers and the mongomock world come from test_per_shop_reorder_levels.
"""
import math
import sys

import pytest

sys.path.insert(0, __import__("os").path.dirname(__file__))

from test_per_shop_reorder_levels import (  # noqa: E402,F401
    BOK,
    DHN,
    _ADMIN,
    _DHN_MGR,
    _SUPER,
    _get,
    _levels,
    _levels_of,
    _migration_db,
    _orders,
    _product,
    _script,
    _set_level,
    _units,
    world,
)

# ---------------------------------------------------------------------------
# Item 2: out-of-stock comes from the one on-hand rule, not products.stock_quantity
# ---------------------------------------------------------------------------


def _stale_stock_world(world):
    # No sellable unit here, a stale stock_quantity of 10: sold out.
    world.products.insert_one(_product("P-STALE", stock_quantity=10, **_levels({DHN: 2})))
    # Units here, a stale stock_quantity of 0: NOT sold out.
    world.products.insert_one(_product("P-FULL", stock_quantity=0, **_levels({DHN: 2})))
    world.stock_units.insert_many(_units("P-FULL", DHN, 5))
    # Only sold units: sold out whatever the stale field says.
    world.products.insert_one(_product("P-SOLD", stock_quantity=7))
    world.stock_units.insert_many(_units("P-SOLD", DHN, 3, status="SOLD"))


def test_stock_count_tile_counts_out_of_stock_by_the_on_hand_rule(world):
    _stale_stock_world(world)
    status = _get("/api/v1/inventory/stock-count-status", store_id=DHN)
    # P-STALE and P-SOLD are out; P-FULL is in; P-FRAME/P-OWNER have 1 unit at DHN.
    assert status["out_of_stock"] == 2
    # Low: P-FRAME (1 <= 2) and P-STALE (0 <= 2); P-FULL is above its level.
    assert status["low_stock"] == 2


def test_owner_digest_counts_out_of_stock_by_the_on_hand_rule(world):
    _stale_stock_world(world)
    digest = _get("/api/v1/admin/owner-digest", user=_SUPER, store_id=DHN)
    assert digest["today"]["out_of_stock"] == 2


# ---------------------------------------------------------------------------
# Items 4: ONE top-up rule across ORACLE, the purchase report, the screen feed, JARVIS
# ---------------------------------------------------------------------------


def test_one_top_up_for_oracle_report_feed_and_jarvis(world):
    from agents.predictive_reorder import recommended_qty
    from api.routers import jarvis
    from api.services.reorder_policy import top_up

    world.products.insert_one(_product("P-TOP5", **_levels({DHN: 5})))
    world.stock_units.insert_many(_units("P-TOP5", DHN, 2))
    world.orders.insert_many(_orders("P-TOP5", DHN, 2))  # slow: only the level recommends
    want = top_up(5, 2)
    assert want == 4
    # ORACLE's order floor: a negligible burn rate leaves only the level's gap.
    assert recommended_qty(on_hand=2, effective_rate=0.01, horizon_days=14,
                           lead_time_days=7, reorder_point=5) == want
    body = _get("/api/v1/reports/purchase/recommendations", store_id=DHN, min_velocity=2)
    assert [r["suggested_order_qty"] for r in body["recommendations"]
            if r["product_id"] == "P-TOP5"] == [want]
    feed = _get("/api/v1/inventory/low-stock", store_id=DHN)["items"]
    assert [i["top_up_qty"] for i in feed if i["product_id"] == "P-TOP5"] == [want]
    inv = jarvis.JarvisAnalyticsEngine._compute_inventory_live() or {}
    recs = [r for r in inv.get("reorder_recommendations") or []
            if r.get("sku") == "SKU-P-TOP5"]
    assert [r["recommended_order"] for r in recs] == [want]


def test_oracle_floor_is_the_level_only_when_a_level_is_set():
    from agents.predictive_reorder import recommended_qty

    # Not set: velocity only, never a floor from a fake 0 level.
    assert recommended_qty(on_hand=0, effective_rate=0.0, horizon_days=14,
                           lead_time_days=7, reorder_point=None) == 1
    # Level 0 is real: alert once sold out, order one above it.
    assert recommended_qty(on_hand=0, effective_rate=0.0, horizon_days=14,
                           lead_time_days=7, reorder_point=0) == 1


def test_jarvis_value_at_risk_uses_the_top_up(world):
    from api.routers import jarvis

    world.products.insert_one(_product("P-TOP5", offer_price=100.0, **_levels({DHN: 5})))
    world.stock_units.insert_many(_units("P-TOP5", DHN, 2))
    ctx = jarvis.JarvisAnalyticsEngine.get_extended_context()
    row = [r for r in ctx["low_stock_value_at_risk"] if r["name"] == "Carrera P-TOP5"][0]
    assert row["value_at_risk"] == 100.0 * 4


# ---------------------------------------------------------------------------
# Item 1: the purchase report sends the server's verdict
# ---------------------------------------------------------------------------


def test_purchase_report_rows_carry_the_servers_low_stock_and_band(world):
    world.products.insert_one(_product("P-LVL5", **_levels({DHN: 5})))
    world.stock_units.insert_many(_units("P-LVL5", DHN, 5))
    world.orders.insert_many(_orders("P-LVL5", DHN, 3))
    body = _get("/api/v1/reports/purchase/recommendations", store_id=DHN, min_velocity=2)
    row = [r for r in body["recommendations"] if r["product_id"] == "P-LVL5"]
    assert row and row[0]["low_stock"] is True and row[0]["stock_status"] == "low"


# ---------------------------------------------------------------------------
# Item 9: integers only, capped at 100000, never OverflowError
# ---------------------------------------------------------------------------


def test_stock_status_compares_as_integers_and_never_overflows():
    from api.services.reorder_policy import MAX_LEVEL, stock_status

    assert stock_status(10**400, 5) == "not-set"  # over the cap = not set
    assert stock_status(MAX_LEVEL, 1) == "critical"
    assert stock_status(MAX_LEVEL + 1, 1) == "not-set"
    assert stock_status(5, 10**400) == "healthy"
    assert stock_status(4, 2) == "critical"  # 2*2 <= 4
    assert stock_status(5, 3) == "low"       # 3*2 > 5


@pytest.mark.parametrize("level", [1e30, 10**15, 10**400, 100001])
def test_a_level_above_the_cap_is_not_set(level):
    from api.services.reorder_policy import is_low_stock, reorder_level, stock_status, top_up

    prod = {"product_id": "P", **_levels({DHN: level})}
    assert reorder_level(prod, store_id=DHN) is None
    assert is_low_stock(prod, 0, store_id=DHN) is False
    assert top_up(level, 0) == 0
    assert stock_status(level, 0) == "not-set"


def test_the_cap_itself_is_a_level():
    from api.services.reorder_policy import MAX_LEVEL, reorder_level

    assert reorder_level({**_levels({DHN: MAX_LEVEL})}, store_id=DHN) == MAX_LEVEL


# ---------------------------------------------------------------------------
# Item 10: the PUT body is strict and the level field is required
# ---------------------------------------------------------------------------


def _raw_put(product_id, body, user=_ADMIN):
    from test_per_shop_reorder_levels import _client

    return _client(user).put(f"/api/v1/inventory/reorder-levels/{product_id}", json=body)


@pytest.mark.parametrize("bad", [True, False, "7", "", 7.5, 100001, -2, [3], {"a": 1}])
def test_put_refuses_a_level_that_is_not_a_real_integer(world, bad):
    res = _raw_put("P-FRAME", {"store_id": DHN, "level": bad})
    assert res.status_code == 422, (bad, res.status_code)
    assert _levels_of(world, "P-FRAME") == {DHN: 2}  # untouched


def test_put_refuses_a_body_with_no_level_field(world):
    res = _raw_put("P-FRAME", {"store_id": DHN})
    assert res.status_code == 422
    assert _levels_of(world, "P-FRAME") == {DHN: 2}  # not silently cleared


def test_put_null_clears_and_integers_set(world):
    assert _raw_put("P-FRAME", {"store_id": DHN, "level": None}).status_code == 200
    assert _levels_of(world, "P-FRAME") == {}
    assert _raw_put("P-FRAME", {"store_id": DHN, "level": 0}).json()["level"] == 0
    assert _levels_of(world, "P-FRAME") == {DHN: 0}
    assert _raw_put("P-FRAME", {"store_id": DHN, "level": 100000}).status_code == 200


# ---------------------------------------------------------------------------
# Item 7 (PUT half): reorder_levels that is not an object
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("junk", ["oops", ["a"], 5, None])
def test_put_sets_a_level_on_a_doc_whose_levels_are_not_an_object(world, junk):
    world.products.update_one({"product_id": "P-FRAME"}, {"$set": {"reorder_levels": junk}})
    res = _set_level(_ADMIN, "P-FRAME", DHN, 4)
    assert res.status_code == 200, res.text
    assert _levels_of(world, "P-FRAME") == {DHN: 4}
    # And a second shop then joins the (now real) dict instead of replacing it.
    assert _set_level(_ADMIN, "P-FRAME", BOK, 6).status_code == 200
    assert _levels_of(world, "P-FRAME") == {DHN: 4, BOK: 6}


@pytest.mark.parametrize("junk", ["oops", ["a"]])
def test_put_clear_on_a_non_object_levels_doc_is_a_quiet_no_op(world, junk):
    world.products.update_one({"product_id": "P-FRAME"}, {"$set": {"reorder_levels": junk}})
    assert _set_level(_ADMIN, "P-FRAME", DHN, None).status_code == 200
    assert _set_level(_ADMIN, "P-NOPE", DHN, None).status_code == 404


def test_put_never_replaces_a_real_dict(world):
    assert _set_level(_ADMIN, "P-FRAME", BOK, 9).status_code == 200
    assert _levels_of(world, "P-FRAME") == {DHN: 2, BOK: 9}


# ---------------------------------------------------------------------------
# Items 6, 7, 8: the migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, want",
    [
        (float("inf"), 0), (float("-inf"), 0), (float("nan"), 0),
        (10**15, 0), (10**400, 0), (100001, 0), (True, 0), (False, 0),
        ("7", 0), ("", 0), (2.9, 0), (-1, 0), (0, 0), (5, 0), (5.0, 0),
        ([3], 0), ({"a": 1}, 0), (None, 0),
        (1, 1), (3, 3), (6, 6), (100000, 100000), (7.0, 7),
    ],
)
def test_migration_reads_an_old_value_like_the_policy_does(value, want):
    mod = _script()
    assert mod._typed(value) == want


def test_migration_plan_and_apply_survive_every_junk_value():
    mod = _script()
    import mongomock

    db = mongomock.MongoClient().db
    junk = [float("inf"), float("nan"), 10**15, True, "7", 2.9, 5, 0, -1, None]
    for i, v in enumerate(junk):
        db.products.insert_one({"product_id": f"J{i}", "reorder_point": v})
        db.stock_units.insert_many(_units(f"J{i}", DHN, 1))
    db.products.insert_one({"product_id": "GOOD", "reorder_point": 3})
    db.stock_units.insert_many(_units("GOOD", DHN, 1))
    rows = mod.plan(db)
    assert {r["product_id"]: r["levels"] for r in rows} == {"GOOD": {DHN: 3}}
    mod.apply(db, rows)
    assert _levels_of(db, "GOOD") == {DHN: 3}


@pytest.mark.parametrize("junk", ["oops", ["a"], 5])
def test_migration_apply_survives_a_levels_field_that_is_not_an_object(junk):
    mod = _script()
    import mongomock

    db = mongomock.MongoClient().db
    db.products.insert_many([
        {"product_id": "P-BAD", "reorder_point": 3, "reorder_levels": junk},
        {"product_id": "P-NEXT", "reorder_point": 4},
    ])
    db.stock_units.insert_many(
        _units("P-BAD", DHN, 1) + _units("P-BAD", BOK, 1) + _units("P-NEXT", DHN, 1)
    )
    mod.apply(db, mod.plan(db))  # must not abort on the first product
    assert _levels_of(db, "P-BAD") == {DHN: 3, BOK: 3}
    assert _levels_of(db, "P-NEXT") == {DHN: 4}
    assert mod.plan(db) == []  # a second run plans nothing


def test_migration_never_replaces_a_real_dict():
    mod = _script()
    db = _migration_db()
    mod.apply(db, mod.plan(db))
    assert _levels_of(db, "P-KEEP") == {DHN: 7, BOK: 2}
