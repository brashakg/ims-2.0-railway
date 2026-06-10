"""Fcostfloor -- POS sell-path cost+pct% price floor (DECISIONS sec 9).

INTENT-LEVEL acceptance tests for the packet
``docs/roadmap/features/Fcostfloor.md`` + owner sign-off 2026-06-09
("enable everywhere": the E2 flag ``pricing.cost_floor_enabled`` defaults ON).

Covers: post-discount enforcement (per-line AND cart-share), the live E2
``pricing.cost_floor_pct`` knob, flag-OFF = pre-change behavior, fail-OPEN on
missing/zero/virtual cost, composition with role/category/luxury-brand caps
(the tighter bound wins), the Rs 0 / 100%-discount exemption, the boundary
(eff == floor accepted), GST-inclusive extraction (compare GST-exclusive
like-for-like), B2B parity, and the raw-server-cost guarantee with a
SALES_CASHIER actor (never an F35-masked DTO).

CI-robustness: every repo/db accessor the create-order handler touches is
monkeypatched (order/customer/product/stock/walkin/audit repos + the catalog
fallback + the E2 policy engine's _coll/cache), and every doc a guard reads is
seeded. Assertions read response JSON fields, never whole-body substrings for
absence. No emoji.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


# --------------------------------------------------------------------------
# Fixture: fake repos for orders + fake E2 policy store
# --------------------------------------------------------------------------


@pytest.fixture
def floor_env(monkeypatch):
    from tests.test_walkouts import FakeDB
    from tests.test_policy_engine_e2 import _PolicyColl, _StoresColl, _FakeCache
    from api.routers import orders as orders_module
    from api import dependencies as deps_module
    from api.services import policy_engine as pe
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.product_repository import ProductRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    product_repo = ProductRepository(fake_db.get_collection("products"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: product_repo)
    monkeypatch.setattr(orders_module, "get_stock_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(orders_module, "_get_catalog_collection", lambda: None)
    # orders.py imports get_audit_repository lazily from ..dependencies.
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)

    # E2 policy engine on a fake policy_settings/stores Mongo + a fresh cache
    # (no cross-test staleness; no real-DB reads).
    pcoll = _PolicyColl()
    scoll = _StoresColl({})  # store has no entity -> chain is store -> global
    cache = _FakeCache()
    monkeypatch.setattr(pe, "cache", cache)
    monkeypatch.setattr(
        pe,
        "_coll",
        lambda name="policy_settings": {"policy_settings": pcoll, "stores": scoll}.get(name),
    )

    # Exclusive GST mode by default for paise-clean expectations: the line's
    # taxable_value then equals its post-discount price. The inclusive-mode
    # test below deletes the env var to exercise the default extraction.
    monkeypatch.setenv("GST_PRICING_MODE", "exclusive")

    customer_repo.create(
        {"customer_id": "cust-x", "name": "Test", "mobile": "9100000099",
         "phone": "9100000099"}
    )
    customer_repo.create(
        {"customer_id": "cust-b2b", "name": "Biz Optics LLP",
         "mobile": "9100000098", "phone": "9100000098",
         "customer_type": "B2B", "gstin": "27AAACB1234C1Z5",
         "state": "Maharashtra"}
    )

    return {
        "db": fake_db,
        "order_repo": order_repo,
        "product_repo": product_repo,
        "audit_repo": audit_repo,
        "policy": pcoll,
        "cache": cache,
        "monkeypatch": monkeypatch,
    }


def _set_policy(env, key: str, value, addr: str = "global") -> None:
    """Write a dotted policy key into the fake policy_settings doc for `addr`
    and clear the engine cache so the next read sees it."""
    doc = env["policy"].docs.setdefault(addr, {"_id": addr, "values": {}})
    cur = doc.setdefault("values", {})
    parts = key.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
    env["cache"].store.clear()


def _seed_product(env, *, pid, cost_price="unset", mrp=200.0,
                  discount_category="MASS", brand=None, name="Floor Frame"):
    doc = {
        "product_id": pid, "name": name, "category": "FRAME",
        "mrp": mrp, "discount_category": discount_category, "is_active": True,
    }
    if cost_price != "unset":
        doc["cost_price"] = cost_price
    if brand:
        doc["brand"] = brand
    env["product_repo"].create(doc)
    return pid


def _item(pid, unit_price, **over):
    it = {"product_id": pid, "product_name": "Floor Frame", "item_type": "FRAME",
          "category": "FRAME", "quantity": 1, "unit_price": unit_price}
    it.update(over)
    return it


def _post(client, headers, items, customer_id="cust-x", **extra):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": customer_id, "items": items, **extra},
        headers=headers,
    )


@pytest.fixture
def cashier_headers():
    """JWT for a SALES_CASHIER (10% role cap) -- packet test 5 actor."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-cashier-001",
            "username": "testcashier",
            "roles": ["SALES_CASHIER"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
            "discount_cap": 10.0,
        }
    )
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# 1-2. Floor enforced POST-discount (flag ON by default -- owner sign-off)
# --------------------------------------------------------------------------


def test_floor_blocks_below_floor_post_discount(client, auth_headers, floor_env):
    """Packet test 1: cost Rs100, pct 10 (registry default), flag ON (default).
    Unit Rs150 with 50% line discount -> eff Rs75 < Rs110 -> 400. The
    pre-discount price (150 >= 100) passes the legacy check, so a 400 here
    proves the floor reads the POST-discount effective price."""
    pid = _seed_product(floor_env, pid="FLR-1", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=50.0)])
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "floor" in detail.lower()
    assert "cost+10" in detail
    assert "110" in detail  # names the computed floor


def test_floor_accepts_post_discount_above_floor(client, auth_headers, floor_env):
    """Packet test 1 (accept half): same line at 20% discount -> eff Rs120 >=
    Rs110 -> created."""
    pid = _seed_product(floor_env, pid="FLR-2", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=20.0)])
    assert r.status_code in (200, 201), r.text
    assert r.json()["status"] == "DRAFT"


# --------------------------------------------------------------------------
# 3. Cart-level discount counts toward the effective price
# --------------------------------------------------------------------------


def test_cart_discount_drags_line_below_floor(client, auth_headers, floor_env):
    """Packet test 2: lines pass per-line (no line discount) but a 30% cart
    discount drags eff to Rs105 < Rs110 -> 400."""
    pid_a = _seed_product(floor_env, pid="FLR-3A", cost_price=100.0)
    pid_b = _seed_product(floor_env, pid="FLR-3B", cost_price=100.0)
    r = _post(
        client, auth_headers,
        [_item(pid_a, 150.0), _item(pid_b, 150.0)],
        cart_discount_percent=30.0,
    )
    assert r.status_code == 400, r.text
    assert "floor" in r.json()["detail"].lower()


def test_cart_discount_within_floor_accepted(client, auth_headers, floor_env):
    """Same cart at 20% -> eff Rs120 >= Rs110 -> created."""
    pid_a = _seed_product(floor_env, pid="FLR-4A", cost_price=100.0)
    pid_b = _seed_product(floor_env, pid="FLR-4B", cost_price=100.0)
    r = _post(
        client, auth_headers,
        [_item(pid_a, 150.0), _item(pid_b, 150.0)],
        cart_discount_percent=20.0,
    )
    assert r.status_code in (200, 201), r.text


# --------------------------------------------------------------------------
# 4. The E2 pct knob is LIVE (orders.py reads E2, not a constant)
# --------------------------------------------------------------------------


def test_knob_lowered_to_zero_disables_post_discount_floor(client, auth_headers, floor_env):
    """Packet test 3a: pricing.cost_floor_pct = 0 -> the eff-Rs75 line is
    accepted (the legacy pre-discount cost+0% check still passes: 150 >= 100)."""
    _set_policy(floor_env, "pricing.cost_floor_pct", 0.0)
    pid = _seed_product(floor_env, pid="FLR-5", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=50.0)])
    assert r.status_code in (200, 201), r.text


def test_knob_raised_to_25_blocks_120(client, auth_headers, floor_env):
    """Packet test 3b: pct = 25 -> a Rs120 line on Rs100 cost (no discount at
    all) now 400s (floor Rs125)."""
    _set_policy(floor_env, "pricing.cost_floor_pct", 25.0)
    pid = _seed_product(floor_env, pid="FLR-6", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 120.0)])
    assert r.status_code == 400, r.text
    assert "cost+25" in r.json()["detail"]


# --------------------------------------------------------------------------
# 5. Flag OFF -> pre-change behavior exactly
# --------------------------------------------------------------------------


def test_flag_off_restores_pre_change_behavior(client, auth_headers, floor_env):
    """Packet test 4: with pricing.cost_floor_enabled = False the deep
    post-discount sale (eff Rs75 < cost Rs100!) is ACCEPTED again -- exactly
    today's pre-discount cost+0% check, which only sees unit_price 150."""
    _set_policy(floor_env, "pricing.cost_floor_enabled", False)
    pid = _seed_product(floor_env, pid="FLR-7", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=50.0)])
    assert r.status_code in (200, 201), r.text


def test_flag_off_legacy_below_cost_check_untouched(client, auth_headers, floor_env):
    """Flag OFF must not weaken the legacy guard: a unit_price BELOW cost
    still 400s with the legacy 'below cost' message (no 'floor' wording)."""
    _set_policy(floor_env, "pricing.cost_floor_enabled", False)
    pid = _seed_product(floor_env, pid="FLR-8", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 50.0)])
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "below cost" in detail.lower()
    # The NEW floor message ("Effective price ... cost+pct% floor") must not
    # appear -- field-aware check, not a whole-body substring scan.
    assert "effective price" not in detail.lower()
    assert "cost+" not in detail


def test_store_scope_off_overrides_global_default_on(client, auth_headers, floor_env):
    """E2 scoping: flag False at store scope (global stays default-ON) ->
    this store sells the deep-discount line; proves the per-store opt-out the
    orchestrator uses."""
    _set_policy(floor_env, "pricing.cost_floor_enabled", False,
                addr="store:BV-TEST-01")
    pid = _seed_product(floor_env, pid="FLR-9", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=50.0)])
    assert r.status_code in (200, 201), r.text


# --------------------------------------------------------------------------
# 6. FAIL-OPEN: missing / zero / virtual cost never blocks
# --------------------------------------------------------------------------


def test_missing_cost_fails_open(client, auth_headers, floor_env):
    """A product with NO cost_price sells normally at any legal discount --
    a chain with patchy cost data must not be bricked (flag is ON)."""
    pid = _seed_product(floor_env, pid="FLR-10")  # cost_price absent
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=50.0)])
    assert r.status_code in (200, 201), r.text


def test_zero_cost_fails_open(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="FLR-11", cost_price=0.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=50.0)])
    assert r.status_code in (200, 201), r.text


def test_virtual_line_fails_open(client, auth_headers, floor_env):
    """A virtual custom-/lens- line has no product doc -> no cost -> never
    floored (mirrors the legacy exemption)."""
    _seed_product(floor_env, pid="FLR-12", cost_price=100.0)  # repo non-empty
    r = _post(client, auth_headers,
              [_item("custom-lens-x", 5000.0, discount_percent=50.0)])
    assert r.status_code in (200, 201), r.text


# --------------------------------------------------------------------------
# 7. Composition with the existing role/category/brand caps (tighter wins)
# --------------------------------------------------------------------------


def test_role_cap_still_blocks_before_floor(client, staff_headers, floor_env):
    """Caps preserved: SALES_STAFF (10% cap) asking 12% on a cheap-cost line
    (floor easily satisfied: eff Rs88 >= Rs11) -> 403 from the CAP path."""
    pid = _seed_product(floor_env, pid="FLR-13", cost_price=10.0, mrp=100.0)
    r = _post(client, staff_headers, [_item(pid, 100.0, discount_percent=12.0)])
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.json()["detail"].lower()


def test_floor_blocks_when_caps_pass_cashier_raw_cost(client, cashier_headers, floor_env):
    """Packet test 5 + composition: a SALES_CASHIER discount of 10% is WITHIN
    every cap (role 10%, MASS 15%) but eff Rs90 < cost Rs95 + 10% = Rs104.5 ->
    400 from the FLOOR. Proves (a) the tighter bound wins and (b) the guard
    reads the raw server-side cost -- a role that can never SEE cost in any
    read DTO still triggers it (F35 cost-masking cannot change this)."""
    pid = _seed_product(floor_env, pid="FLR-14", cost_price=95.0, mrp=100.0)
    r = _post(client, cashier_headers, [_item(pid, 100.0, discount_percent=10.0)])
    assert r.status_code == 400, r.text
    assert "floor" in r.json()["detail"].lower()


def test_luxury_brand_cap_still_enforced(client, staff_headers, floor_env):
    """Luxury brand caps preserved exactly: Cartier caps at 2%; a 5% ask is
    403'd by the cap even though the floor is satisfied (cost Rs10)."""
    pid = _seed_product(floor_env, pid="FLR-15", cost_price=10.0, mrp=100.0,
                        discount_category="LUXURY", brand="Cartier")
    r = _post(client, staff_headers, [_item(pid, 100.0, discount_percent=5.0)])
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.json()["detail"].lower()


def test_admin_cap_bypass_does_not_bypass_floor(client, auth_headers, floor_env):
    """SUPERADMIN bypasses discount caps but NOT the floor: 60% admin discount
    -> eff Rs60 < Rs110 -> 400."""
    pid = _seed_product(floor_env, pid="FLR-16", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=60.0)])
    assert r.status_code == 400, r.text
    assert "floor" in r.json()["detail"].lower()


# --------------------------------------------------------------------------
# 8. Rs 0 / 100%-discount exemption preserved (C-4 approval-gated instead)
# --------------------------------------------------------------------------


def test_full_line_discount_exempt_from_floor(client, auth_headers, floor_env):
    """Packet test 6: a 100%-discount line WITH its C-4 approver + reason is
    NOT floored -- the giveaway stays approval-gated, not floor-blocked."""
    pid = _seed_product(floor_env, pid="FLR-17", cost_price=100.0)
    r = _post(
        client, auth_headers,
        [_item(pid, 150.0, discount_percent=100.0,
               discount_approved_by="mgr-009",
               discount_reason="Warranty replacement")],
    )
    assert r.status_code in (200, 201), r.text
    saved = next(d for d in floor_env["order_repo"].collection.docs
                 if d.get("status") == "DRAFT")
    assert saved["zero_total"] is True


def test_full_line_discount_without_approval_still_400s_via_c4(
    client, auth_headers, floor_env
):
    """The exemption hands the line to C-4, which still 400s when the
    approver/reason is missing (floor does not swallow that gate)."""
    pid = _seed_product(floor_env, pid="FLR-18", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 150.0, discount_percent=100.0)])
    assert r.status_code == 400, r.text
    detail = r.json()["detail"].lower()
    assert "approver" in detail or "reason" in detail


# --------------------------------------------------------------------------
# 9. Boundary + GST handling (paisa-exact, GST-exclusive like-for-like)
# --------------------------------------------------------------------------


def test_boundary_exactly_at_floor_accepted(client, auth_headers, floor_env):
    """Packet test 7: eff == floor exactly (Rs110 on cost Rs100 @10%) is
    ACCEPTED (the 1e-6 epsilon absorbs binary-float dust)."""
    pid = _seed_product(floor_env, pid="FLR-19", cost_price=100.0)
    r = _post(client, auth_headers, [_item(pid, 110.0)])
    assert r.status_code in (200, 201), r.text


def test_inclusive_mode_compares_gst_exclusive_taxable(client, auth_headers, floor_env):
    """Default GST-INCLUSIVE mode: the counter price embeds 5% GST (FRAME).
    Rs115 all-in -> taxable 115/1.05 = Rs109.52 < Rs110 -> 400, while Rs116
    -> Rs110.48 -> accepted. Proves the floor compares the GST-EXCLUSIVE
    taxable against the GST-exclusive cost, not the sticker price."""
    floor_env["monkeypatch"].delenv("GST_PRICING_MODE", raising=False)
    pid = _seed_product(floor_env, pid="FLR-20", cost_price=100.0)
    r_block = _post(client, auth_headers, [_item(pid, 115.0)])
    assert r_block.status_code == 400, r_block.text
    assert "floor" in r_block.json()["detail"].lower()
    r_ok = _post(client, auth_headers, [_item(pid, 116.0)])
    assert r_ok.status_code in (200, 201), r_ok.text


def test_b2b_customer_floored_same_as_b2c(client, auth_headers, floor_env):
    """B2B parity: the floor is tax-segment-agnostic -- a GSTIN-carrying B2B
    customer hits the identical guard on the identical effective price."""
    pid = _seed_product(floor_env, pid="FLR-21", cost_price=100.0)
    r = _post(client, auth_headers,
              [_item(pid, 150.0, discount_percent=50.0)],
              customer_id="cust-b2b")
    assert r.status_code == 400, r.text
    assert "floor" in r.json()["detail"].lower()


# --------------------------------------------------------------------------
# 10. Service-level unit checks + registry lock
# --------------------------------------------------------------------------


def _patch_policies(monkeypatch, enabled=True, pct=10.0):
    from api.services import policy_engine as pe
    from api.services import cost_floor as cf

    def fake_get_policy(key, scope=None, *, default=None):
        if key == cf.FLAG_KEY:
            return enabled
        if key == cf.PCT_KEY:
            return pct
        return default

    monkeypatch.setattr(pe, "get_policy", fake_get_policy)


def test_unit_missing_taxable_value_fails_open(monkeypatch):
    """A line the GST pass did not stamp is skipped, never blocked."""
    from api.services.cost_floor import enforce_cost_floor

    _patch_policies(monkeypatch)
    enforce_cost_floor(
        [{"product_id": "P1", "quantity": 1, "discount_percent": 0,
          "cost_at_sale": 100.0}],  # no taxable_value
        {"P1": 100.0}, "BV-TEST-01",
    )  # no raise


def test_unit_cost_by_pid_fallback_used(monkeypatch):
    """When cost_at_sale is absent the raw _cost_by_pid map still floors the
    line (canonical-pid fallback path)."""
    from fastapi import HTTPException
    from api.services.cost_floor import enforce_cost_floor

    _patch_policies(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        enforce_cost_floor(
            [{"product_id": "P2", "product_name": "X", "quantity": 2,
              "discount_percent": 0, "taxable_value": 100.0}],  # eff 50/unit
            {"P2": 100.0}, "BV-TEST-01",
        )
    assert exc.value.status_code == 400
    assert "floor" in exc.value.detail.lower()


def test_unit_policy_engine_down_falls_back_to_defaults(monkeypatch):
    """If the policy engine raises, the guard uses the registry defaults
    (enabled=True, pct=10) -- deterministic, matching a fresh DB."""
    from fastapi import HTTPException
    from api.services import policy_engine as pe
    from api.services.cost_floor import enforce_cost_floor

    def boom(*a, **k):
        raise RuntimeError("policy store down")

    monkeypatch.setattr(pe, "get_policy", boom)
    with pytest.raises(HTTPException):
        enforce_cost_floor(
            [{"product_id": "P3", "quantity": 1, "discount_percent": 0,
              "taxable_value": 50.0, "cost_at_sale": 100.0}],
            None, None,
        )


def test_registry_flag_defaults_on_and_pct_10():
    """Lock the owner sign-off: pricing.cost_floor_enabled exists, is a bool,
    defaults True (ON everywhere), store-overridable; pct default stays 10."""
    from api.services import policy_registry as preg

    flag = preg.REGISTRY.get("pricing.cost_floor_enabled")
    assert flag is not None
    assert flag.type == "bool"
    assert flag.default is True
    assert "store" in flag.scopes
    pct = preg.REGISTRY.get("pricing.cost_floor_pct")
    assert pct is not None
    assert pct.default == 10.0
