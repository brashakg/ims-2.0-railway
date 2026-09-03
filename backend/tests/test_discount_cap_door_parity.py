"""Door-parity discount caps + promo cap clamp (audit findings, 2026-09-03).

FINDING 1 (CRITICAL, orders.py): POST /orders/{id}/items resolved products
with a NARROWER lookup (find_by_id only) than create_order's tolerant
_resolve_product_doc (product_id -> sku -> _id -> catalog fallback). A product
referenced by SKU or Mongo _id silently missed, and the MRP ceiling, cost
floor, HQ-offer rule, category/luxury-brand caps and the discount-reason
requirement ALL no-op'd; catalog-only products create_order refuses were
billed outright. Fixed by deleting the narrow copy: both doors resolve via
_resolve_billable_product and evaluate every guard via _enforce_line_pricing.

THE KEY TEST IS DIFFERENTIAL: the same product is driven through BOTH doors by
every reference shape (product_id / sku / _id / catalog-only / unknown) and
the doors must agree -- same acceptance, same rejection, same persisted money
fields. A test that exercises only one door cannot see a two-implementations
bug.

FINDING 2 (MEDIUM, promo_engine.py): the promo cap clamp read
``discount_category or category`` -- and the live order path never stamped
discount_category, so the clamp always saw the MERCHANDISING label and
defaulted every line to the MASS 15% cap (including NON_DISCOUNTABLE 0% and
LUXURY 5% lines). It also measured the cap against the pre-discount line
value with no credit for the discount already given, so a line already at its
cap got a fresh full cap of promo on top. Fixed: orders.py stamps the
master's discount_category + mrp onto each line; the clamp reads the tier
only and grants only the REMAINING cap headroom measured on MRP.

The fakes here honour ``$or`` faithfully (strict_fakes.StrictCollection) --
a permissive double that ignores query operators is blind to a lookup bug,
which is exactly finding 1.

No emoji in this file (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

# The three reference shapes a POS client actually sends for the SAME product.
PID = "FR-CART-77"
SKU = "SKU-CART-77"
HEXID = "6a0ebf45fbd46b17c0b52ffc"  # 24-hex string _id (TechCherry import shape)

MRP = 30000.0
COST = 12000.0


@pytest.fixture
def doors(monkeypatch):
    """Both order doors wired over in-memory fakes.

    products + catalog_products ride strict_fakes.StrictCollection (faithful
    $or), orders/customers ride the FakeDB the other order suites use.
    """
    from tests.test_walkouts import FakeDB
    from tests.strict_fakes import StrictCollection
    from api.routers import orders as orders_module
    from api import dependencies as deps_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.product_repository import ProductRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))

    products = StrictCollection("products")
    # Inserted RAW (not via repo.create) so the _id is exactly the imported
    # 24-hex string shape the POS sends for TechCherry rows.
    products.insert_one({
        "_id": HEXID,
        "product_id": PID,
        "sku": SKU,
        "name": "Cartier Panthere",
        "category": "FRAME",
        "brand": "Cartier",
        "mrp": MRP,
        "cost_price": COST,
        "discount_category": "LUXURY",
        "is_active": True,
    })
    product_repo = ProductRepository(products)

    catalog = StrictCollection("catalog_products")
    catalog.insert_one({
        "id": "CATONLY-1",
        "sku": "CATSKU-1",
        "title": "Catalog Only Frame",
        "category": "FRAME",
        "pricing": {"mrp": 5000.0, "offer_price": 5000.0, "cost_price": 2000.0,
                    "discount_category": "MASS"},
        "is_active": True,
    })

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: product_repo)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(orders_module, "_get_catalog_collection", lambda: catalog)
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)

    customer_repo.create({
        "customer_id": "cust-x", "name": "Test",
        "mobile": "9100000099", "phone": "9100000099",
    })
    return {"db": fake_db, "orders": order_repo, "products": products}


def _seed_draft(doors, order_id):
    """A DRAFT order the add-item door can append to."""
    doors["db"].get_collection("orders").insert_one({
        "order_id": order_id,
        "order_number": "SO-" + order_id,
        "store_id": "BV-TEST-01",
        "customer_id": "cust-x",
        "status": "DRAFT",
        "items": [],
        "amount_paid": 0,
        "grand_total": 0,
        "balance_due": 0,
        "cart_discount_percent": 0,
    })
    return order_id


def _line(ref, unit_price, **over):
    it = {
        "product_id": ref,
        "product_name": "Cartier Panthere",
        "item_type": "FRAME",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": unit_price,
    }
    it.update(over)
    return it


def _create(client, headers, item):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": [item]},
        headers=headers,
    )


def _add(client, headers, order_id, item):
    return client.post(f"/api/v1/orders/{order_id}/items", json=item, headers=headers)


# ===========================================================================
# FINDING 1 -- differential: both doors, every reference shape
# ===========================================================================
@pytest.mark.parametrize("ref", [PID, SKU, HEXID], ids=["product_id", "sku", "_id"])
def test_differential_below_cost_rejected_by_both_doors(
    client, staff_headers, doors, ref
):
    """Rs 100 on a Rs 12,000-cost frame: the cost floor must fire on BOTH
    doors for every reference shape. Pre-fix the add door appended the line
    (Rs 29,900 of margin gone on one line) when referenced by sku/_id."""
    r_create = _create(client, staff_headers, _line(ref, 100.0))
    assert r_create.status_code == 400, r_create.text
    assert "below cost" in r_create.text.lower()

    oid = _seed_draft(doors, f"ORD-COST-{ref[:6]}")
    r_add = _add(client, staff_headers, oid, _line(ref, 100.0))
    assert r_add.status_code == 400, r_add.text
    assert "below cost" in r_add.text.lower()
    # Nothing was appended.
    stored = doors["orders"].find_by_id(oid)
    assert stored["items"] == []


@pytest.mark.parametrize("ref", [PID, SKU, HEXID], ids=["product_id", "sku", "_id"])
def test_differential_luxury_brand_cap_agrees_on_both_doors(
    client, staff_headers, doors, ref
):
    """Rs 29,000 on MRP 30,000 = 3.33% implied, over the Cartier 2% brand
    cap. Both doors must 403 and both must state the SAME 2.0% limit."""
    item = _line(ref, 29000.0, discount_reason="test: parity probe")

    r_create = _create(client, staff_headers, item)
    assert r_create.status_code == 403, r_create.text
    assert "limit of 2.0%" in r_create.text

    oid = _seed_draft(doors, f"ORD-CAP-{ref[:6]}")
    r_add = _add(client, staff_headers, oid, item)
    assert r_add.status_code == 403, r_add.text
    assert "limit of 2.0%" in r_add.text
    assert doors["orders"].find_by_id(oid)["items"] == []


@pytest.mark.parametrize("ref", [PID, SKU, HEXID], ids=["product_id", "sku", "_id"])
def test_differential_within_cap_sale_agrees_on_price_fields(
    client, staff_headers, doors, ref
):
    """Rs 29,500 (1.67%, inside the 2% Cartier cap, reason given) must be
    ACCEPTED by both doors, and the persisted money fields must be equal --
    the doors agreeing only on rejection is not agreement."""
    item = _line(ref, 29500.0, discount_reason="test: parity probe")

    r_create = _create(client, staff_headers, item)
    assert r_create.status_code in (200, 201), r_create.text
    created = doors["orders"].find_by_id(r_create.json()["order_id"])
    created_line = created["items"][0]

    oid = _seed_draft(doors, f"ORD-OK-{ref[:6]}")
    r_add = _add(client, staff_headers, oid, item)
    assert r_add.status_code == 200, r_add.text
    added_line = doors["orders"].find_by_id(oid)["items"][0]

    for field in (
        "unit_price",
        "discount_percent",
        "effective_discount_percent",
        "cost_at_sale",
        "mrp",
        "discount_category",
        "brand",
        "item_total",
    ):
        assert created_line.get(field) == added_line.get(field), (
            f"doors disagree on {field}: create={created_line.get(field)!r} "
            f"add={added_line.get(field)!r} (ref shape {ref})"
        )
    # And the values are the REAL master values, not client echoes.
    assert added_line["cost_at_sale"] == COST
    assert added_line["mrp"] == MRP
    assert added_line["discount_category"] == "LUXURY"
    assert added_line["brand"] == "Cartier"


def test_differential_catalog_only_product_refused_by_both_doors(
    client, staff_headers, doors
):
    """A product living only in catalog_products has no billing spine.
    create_order always refused it; the add door BILLED it. Both must 400."""
    item = _line("CATONLY-1", 5000.0)

    r_create = _create(client, staff_headers, item)
    assert r_create.status_code == 400, r_create.text
    assert "only in the catalog" in r_create.text

    oid = _seed_draft(doors, "ORD-CATONLY")
    r_add = _add(client, staff_headers, oid, item)
    assert r_add.status_code == 400, r_add.text
    assert "only in the catalog" in r_add.text
    assert doors["orders"].find_by_id(oid)["items"] == []


def test_differential_unknown_product_refused_by_both_doors(
    client, staff_headers, doors
):
    item = _line("NOPE-404", 1000.0)

    r_create = _create(client, staff_headers, item)
    assert r_create.status_code == 400, r_create.text
    assert "Product not found" in r_create.text

    oid = _seed_draft(doors, "ORD-NOPE")
    r_add = _add(client, staff_headers, oid, item)
    assert r_add.status_code == 400, r_add.text
    assert "Product not found" in r_add.text
    assert doors["orders"].find_by_id(oid)["items"] == []


def test_add_by_sku_typed_below_price_needs_a_reason(client, staff_headers, doors):
    """Owner ruling 2026-08-30: a typed price under the catalog price is a
    manual discount and needs a written reason. Pre-fix, an add-by-SKU line
    skipped this entirely (the reported 'line carries no discount reason')."""
    oid = _seed_draft(doors, "ORD-REASON")
    r_add = _add(client, staff_headers, oid, _line(SKU, 29500.0))  # no reason
    assert r_add.status_code == 400, r_add.text
    assert "discount reason" in r_add.text.lower()
    assert doors["orders"].find_by_id(oid)["items"] == []


# ===========================================================================
# FINDING 2 -- promo clamp: discount TIER + remaining headroom, end to end
# ===========================================================================
@pytest.fixture
def promo_doors(monkeypatch):
    """create_order with the promo engine ON, over fakes, with an active
    10%-over-500 THRESHOLD promo seedable per test."""
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from api.routers import promotions as promo_module
    from api import dependencies as deps_module
    import api.routers.finance as finance_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.product_repository import ProductRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    products = fake_db.get_collection("products")
    product_repo = ProductRepository(products)

    products.insert_one({
        "product_id": "P-ND", "name": "Solution Kit", "category": "ACCESSORY",
        "brand": "Generic", "mrp": 10000.0, "cost_price": 4000.0,
        "discount_category": "NON_DISCOUNTABLE", "is_active": True,
    })
    products.insert_one({
        "product_id": "P-MASS", "name": "Mass Frame", "category": "FRAME",
        "brand": "Generic", "mrp": 10000.0, "cost_price": 1000.0,
        "discount_category": "MASS", "is_active": True,
    })

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: product_repo)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)
    # The promo block reads orders._get_db(); the rules read the same handle.
    monkeypatch.setattr(orders_module, "_get_db", lambda: fake_db)
    monkeypatch.setattr(promo_module, "_get_db", lambda: fake_db)
    monkeypatch.setattr(finance_module, "check_period_locked", lambda *a, **k: None)
    monkeypatch.setenv("PROMO_ENGINE_ENABLED", "1")

    customer_repo.create({
        "customer_id": "cust-x", "name": "Test",
        "mobile": "9100000099", "phone": "9100000099",
    })
    return {"db": fake_db, "orders": order_repo}


def _seed_promo(fake_db):
    fake_db.get_collection("promo_rules").insert_one({
        "promo_id": "PR-10", "name": "10% over 500", "promo_type": "THRESHOLD",
        "reward_value": 10, "min_cart_value": 500, "active": True,
        "stackable": False, "uses_count": 0, "store_ids": None,
    })


def _post_order(client, headers, item):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": [item]},
        headers=headers,
    )


def test_promo_never_discounts_a_non_discountable_line(
    client, auth_headers, promo_doors
):
    """The clamp must read the product's DISCOUNT TIER. A NON_DISCOUNTABLE
    (0% cap) line with an active 10% promo must come out byte-identical to
    the same order with no promo at all. Pre-fix the clamp saw only the
    merchandising 'ACCESSORY' label -> MASS 15% default -> the full 10%
    (Rs 1,000) was given away on a 0%-cap line -- the only door through
    which a NON_DISCOUNTABLE item could be discounted at all."""
    item = {"product_id": "P-ND", "product_name": "Solution Kit",
            "item_type": "ACCESSORY", "category": "ACCESSORY",
            "quantity": 1, "unit_price": 10000.0}

    r_base = _post_order(client, auth_headers, item)
    assert r_base.status_code in (200, 201), r_base.text
    base = promo_doors["orders"].find_by_id(r_base.json()["order_id"])

    _seed_promo(promo_doors["db"])
    r_promo = _post_order(client, auth_headers, item)
    assert r_promo.status_code in (200, 201), r_promo.text
    promoted = promo_doors["orders"].find_by_id(r_promo.json()["order_id"])

    assert promoted["grand_total"] == base["grand_total"]
    assert not promoted.get("applied_promos")
    assert "promo_discount_amount" not in promoted["items"][0]


def test_promo_gets_zero_headroom_on_a_line_already_at_its_cap(
    client, auth_headers, promo_doors
):
    """A MASS line already carrying its full 15% manual discount has NO cap
    headroom left: the 10% promo must add NOTHING. Pre-fix the clamp
    measured a fresh 15% of the pre-discount value -> Rs 1,000 more given
    away per Rs 10,000 line beyond the enforced cap."""
    item = {"product_id": "P-MASS", "product_name": "Mass Frame",
            "item_type": "FRAME", "category": "FRAME", "quantity": 1,
            "unit_price": 10000.0, "discount_percent": 15.0,
            "discount_reason": "test: at-cap manual discount"}

    r_base = _post_order(client, auth_headers, item)
    assert r_base.status_code in (200, 201), r_base.text
    base = promo_doors["orders"].find_by_id(r_base.json()["order_id"])
    assert base["items"][0]["item_total"] == 8500.0

    _seed_promo(promo_doors["db"])
    r_promo = _post_order(client, auth_headers, item)
    assert r_promo.status_code in (200, 201), r_promo.text
    promoted = promo_doors["orders"].find_by_id(r_promo.json()["order_id"])

    assert promoted["grand_total"] == base["grand_total"]
    assert "promo_discount_amount" not in promoted["items"][0]
    assert not promoted.get("applied_promos")


def test_promo_is_clamped_to_the_remaining_headroom_measured_on_mrp(
    client, auth_headers, promo_doors
):
    """A MASS line typed at Rs 9,000 on MRP 10,000 already carries 10% of the
    15% cap -- exactly Rs 500 of headroom remains, measured on MRP. The 10%
    promo (raw Rs 900 on the typed price) must be clamped to Rs 500. Pre-fix
    it measured 15% of the typed value with no credit for the markdown ->
    the full Rs 900 went through."""
    _seed_promo(promo_doors["db"])
    item = {"product_id": "P-MASS", "product_name": "Mass Frame",
            "item_type": "FRAME", "category": "FRAME", "quantity": 1,
            "unit_price": 9000.0,
            "discount_reason": "test: typed below MRP"}

    r = _post_order(client, auth_headers, item)
    assert r.status_code in (200, 201), r.text
    doc = promo_doors["orders"].find_by_id(r.json()["order_id"])
    line = doc["items"][0]
    assert line.get("promo_discount_amount") == 500.0
    assert line["item_total"] == 8500.0  # 9000 typed - 500 promo = MRP - 15%


# ===========================================================================
# FINDING 2 -- pure clamp: the tier is the ONLY category input
# ===========================================================================
def test_clamp_ignores_merchandising_labels_that_collide_with_tier_names():
    """A line whose merchandising category happens to read 'PREMIUM' (a 20%
    TIER name) but carries no real discount_category must clamp at the MASS
    15% default -- the clamp may never read the merchandising label."""
    from api.services import promo_engine as pe

    cart = {"items": [{
        "product_id": "a", "item_id": "a", "quantity": 1,
        "unit_price": 10000.0, "category": "PREMIUM",  # merchandising label
    }]}
    rule = {"promo_id": "P20", "name": "20 off", "promo_type": "PERCENT",
            "reward_value": 20}
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["applied"] is True
    # 20% raw = 2000; clamped to the MASS default 15% = 1500, never the
    # PREMIUM 20% the colliding label would have granted.
    assert out["total_discount"] == 1500.0
