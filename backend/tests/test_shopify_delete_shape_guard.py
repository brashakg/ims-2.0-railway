"""
IMS 2.0 - Shopify DELETE-payload shape guard (the DESTRUCTIVE direction)
=========================================================================
The Shopify topic is read from the UNSIGNED X-Shopify-Topic header while the
body is what the HMAC signs. So a captured, validly-signed CREATE body -- whose
top-level `id` IS a real live order / customer id -- can be re-delivered with
the topic relabelled to a DELETE topic. Per-topic fingerprint dedupe does not
stop it: each topic is its own canonical scope, so the relabelled body lands in
a fresh bucket and reads as a brand-new delivery.

PR #966 closed the mirror-image (constructive) direction. This file pins the
destructive one, at BOTH handlers:

  orders/delete    -> api.services.shopify_order_delete
                      a relabelled orders/create body must NOT void the order
  customers/delete -> api.services.shopify_customer_delete
                      a relabelled customers/create body must NOT flag erasure

EVERY refusal test asserts on PERSISTED STATE (the order's status / the
customer's erasure flag), never on the returned reason string: a handler that
returns a polite refusal while still writing is not a closed door.

Every refusal test has a POSITIVE CONTROL alongside it -- the genuine delete
body must STILL void / STILL flag -- so "refuses everything" cannot masquerade
as a fix.

The positive controls use Shopify's DOCUMENTED delete bodies verbatim (see
_documented_* below), NOT a hand-invented `{"id": N}`. An earlier draft of this
suite invented the customer control and passed while the guard refused every
real customers/delete webhook -- a fixture that supplied the answer instead of
testing it. The documented bodies are the load-bearing controls.

Pure: no network, no real Mongo. Uses the shared StrictDB double.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402

from api.services.shopify_customer_delete import (  # noqa: E402
    handle_shopify_customer_delete,
)
from api.services.shopify_delete_shape import (  # noqa: E402
    KIND_CUSTOMER,
    KIND_ORDER,
    delete_payload_refusal,
    unexpected_delete_keys,
)
from api.services.shopify_order_delete import (  # noqa: E402
    handle_shopify_order_delete,
)


@pytest.fixture
def db():
    return StrictDB()


# ---------------------------------------------------------------------------
# Captured payload bodies (shapes Shopify really sends).
# ---------------------------------------------------------------------------


def _captured_order_create(order_id=5001):
    """A real orders/create body. Its top-level `id` IS the live order id --
    which is exactly what makes the relabel attack work. Mirrors
    test_online_order_mapper._frame_order."""
    return {
        "id": order_id,
        "name": f"#{order_id}",
        "order_number": 1042,
        "financial_status": "paid",
        "fulfillment_status": None,
        "order_status_url": "https://bettervision.in/orders/xyz",
        "email": "buyer@example.com",
        "phone": "+91 98765 43210",
        "total_price": "999.00",
        "customer": {"id": 555, "first_name": "Ravi", "last_name": "Kumar"},
        "shipping_address": {"province_code": "20"},
        "line_items": [
            {
                "id": 9001,
                "variant_id": 999001,
                "sku": "RB-1234",
                "quantity": 1,
                "price": "999.00",
            }
        ],
    }


def _captured_customer_create(customer_id=555):
    """A real customers/create body. Its top-level `id` IS the live customer
    id."""
    return {
        "id": customer_id,
        "email": "ravi@example.com",
        "phone": "+919876543210",
        "first_name": "Ravi",
        "last_name": "Kumar",
        "orders_count": 3,
        "total_spent": "4497.00",
        "state": "enabled",
        "verified_email": True,
        "tags": "vip",
        "addresses": [{"city": "Ranchi", "province_code": "20"}],
        "default_address": {"city": "Ranchi"},
    }


def _documented_order_delete(order_id=5001):
    """Shopify's DOCUMENTED orders/delete body, verbatim shape.

    shopify.dev Admin REST webhook reference (2026-04 / -07 / -10):
        {"id": 820982911946154508}
    """
    return {"id": order_id}


def _documented_customer_delete(customer_id=555):
    """Shopify's DOCUMENTED customers/delete body, verbatim shape.

    shopify.dev Admin REST webhook reference (2026-04 / -07 / -10):
        {"id": 706405506930370084,
         "tax_exemptions": [],
         "admin_graphql_api_id": "gid://shopify/Customer/706405506930370084"}

    NOT `{"id": N}`. It carries `tax_exemptions`, which is why the guard must
    admit that key on the customer kind -- refusing it would mean IMS silently
    ignored every real GDPR/DPDP erasure request.
    """
    return {
        "id": customer_id,
        "tax_exemptions": [],
        "admin_graphql_api_id": f"gid://shopify/Customer/{customer_id}",
    }


def _seed_order(db, *, shopify_order_id="5001", status="CONFIRMED"):
    db.get_collection("orders").insert_one(
        {
            "order_id": "ord-abc",
            "shopify_order_id": shopify_order_id,
            "channel": "ONLINE",
            "source": "shopify",
            "status": status,
        }
    )


def _seed_customer(db, *, shopify_customer_id="555"):
    db.get_collection("customers").insert_one(
        {
            "customer_id": "CUST-1",
            "shopify_customer_id": shopify_customer_id,
            "name": "Ravi Kumar",
            "email": "ravi@example.com",
            "mobile": "9876543210",
        }
    )


def _order_row(db, shopify_order_id="5001"):
    return db.get_collection("orders").find_one(
        {"shopify_order_id": shopify_order_id}
    )


def _customer_row(db, shopify_customer_id="555"):
    return db.get_collection("customers").find_one(
        {"shopify_customer_id": shopify_customer_id}
    )


# ===========================================================================
# HANDLER 1 -- orders/delete
# ===========================================================================


def test_relabelled_order_create_does_not_void_the_live_order(db):
    """THE ATTACK. A captured, validly-signed orders/create body replayed with
    X-Shopify-Topic: orders/delete must leave the live order untouched.

    STATE assertion: status stays CONFIRMED and no void markers are written."""
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")

    handle_shopify_order_delete(
        db, _captured_order_create(5001), topic="orders/delete"
    )

    order = _order_row(db)
    assert order["status"] == "CONFIRMED", "a replayed create body VOIDED a live order"
    assert not order.get("shopify_deleted_at")
    assert not order.get("status_before_void")
    assert not order.get("void_reason")


def test_documented_order_delete_body_still_voids(db):
    """POSITIVE CONTROL for handler 1, using Shopify's DOCUMENTED orders/delete
    body. Without this, refusing everything would pass the test above."""
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")

    res = handle_shopify_order_delete(
        db, _documented_order_delete(5001), topic="orders/delete"
    )

    # STATE first: a guard so strict it refuses the real webhook shows up here.
    order = _order_row(db)
    assert order["status"] == "VOID", (
        "the DOCUMENTED orders/delete body was refused -- a Shopify-deleted "
        "order would keep counting as live IMS revenue"
    )
    assert order["status_before_void"] == "CONFIRMED"
    assert order.get("shopify_deleted_at")
    assert res["status"] == "voided"


def test_genuine_order_delete_with_delivery_metadata_still_voids(db):
    """Pure delivery metadata alongside the id is still a genuine delete. Pins
    the allowed-key margin so a future tightening is a conscious choice."""
    _seed_order(db, shopify_order_id="5001", status="PACKED")

    res = handle_shopify_order_delete(
        db,
        {
            "id": 5001,
            "admin_graphql_api_id": "gid://shopify/Order/5001",
            "shop_id": 690933842,
            "shop_domain": "bettervision.myshopify.com",
        },
        topic="orders/delete",
    )

    assert res["status"] == "voided"
    assert _order_row(db)["status"] == "VOID"


def test_relabelled_order_updated_body_does_not_void(db):
    """A later-lifecycle orders/updated body is also a capture candidate and
    also carries the live order id. Partial body, still order-shaped."""
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")

    handle_shopify_order_delete(
        db,
        {"id": 5001, "financial_status": "paid", "fulfillment_status": "fulfilled"},
        topic="orders/delete",
    )

    order = _order_row(db)
    assert order["status"] == "CONFIRMED"
    assert not order.get("shopify_deleted_at")


def test_refund_body_parent_pointer_does_not_void_the_parent_order(db):
    """A refunds/create body names its parent in `order_id`. The removed
    `or payload.get("order_id")` fallback made that pointer a live void target
    whenever the body carried no usable top-level id."""
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")

    handle_shopify_order_delete(
        db, {"order_id": 5001, "note": "partial refund"}, topic="orders/delete"
    )

    order = _order_row(db)
    assert order["status"] == "CONFIRMED"
    assert not order.get("shopify_deleted_at")


def test_minimality_alone_holds_the_order_door_at_the_handler(db):
    """The LOAD-BEARING rule, asserted on STATE at the real door.

    This body dodges every name in _ORDER_CREATE_MARKERS, so the marker list
    cannot save it -- only the positive minimality assertion can. Without a
    handler-level test like this, deleting the minimality rule would still leave
    every void/erasure STATE test green (the captured create bodies happen to
    carry markers), and the rule that actually makes the wrong thing impossible
    would be untested where it matters.
    """
    _seed_order(db, shopify_order_id="5001", status="CONFIRMED")

    handle_shopify_order_delete(
        db,
        {"id": 5001, "some_future_shopify_field": "x"},
        topic="orders/delete",
    )

    order = _order_row(db)
    assert order["status"] == "CONFIRMED", (
        "a content-bearing body with no KNOWN marker VOIDED a live order -- the "
        "minimality rule is not holding the destructive door"
    )
    assert not order.get("shopify_deleted_at")


def test_minimality_alone_holds_the_customer_door_at_the_handler(db):
    """Twin of the above at the erasure door. Dodges every name in
    _CUSTOMER_CREATE_MARKERS."""
    _seed_customer(db, shopify_customer_id="555")

    handle_shopify_customer_delete(
        db,
        {"id": 555, "some_future_shopify_field": "x"},
        topic="customers/delete",
    )

    cust = _customer_row(db)
    assert not cust.get("shopify_erasure_requested"), (
        "a content-bearing body with no KNOWN marker FLAGGED a real customer "
        "for erasure -- the minimality rule is not holding the destructive door"
    )
    assert not cust.get("shopify_erasure_requested_at")


def test_order_delete_refusal_is_reported_as_skipped(db):
    """Secondary (the STATE assertions above are what matter): the refusal is
    surfaced, not swallowed as success."""
    _seed_order(db, shopify_order_id="5001")

    res = handle_shopify_order_delete(
        db, _captured_order_create(5001), topic="orders/delete"
    )

    assert res["status"] == "skipped"
    assert res["reason"] in {
        "create_shaped_payload",
        "not_delete_shaped",
        "child_resource_payload",
    }


# ===========================================================================
# HANDLER 2 -- customers/delete  (the twin the first crew missed)
# ===========================================================================


def test_relabelled_customer_create_does_not_flag_erasure(db):
    """THE ATTACK, twin door. A captured customers/create body replayed with
    X-Shopify-Topic: customers/delete must not flag a real buyer for data
    erasure.

    STATE assertion: the erasure flag is absent/falsey on the stored row."""
    _seed_customer(db, shopify_customer_id="555")

    handle_shopify_customer_delete(
        db, _captured_customer_create(555), topic="customers/delete"
    )

    cust = _customer_row(db)
    assert not cust.get("shopify_erasure_requested"), (
        "a replayed create body flagged a real customer for data erasure"
    )
    assert not cust.get("shopify_erasure_requested_at")


def test_documented_customer_delete_body_still_flags(db):
    """POSITIVE CONTROL for handler 2, using Shopify's DOCUMENTED
    customers/delete body -- the one a live store actually sends.

    This is the test that catches a guard which is strict enough to break the
    real erasure flow. A hand-invented `{"id": 555}` control passes even when
    every genuine customers/delete webhook is being refused, because Shopify
    never sends `{"id": N}` alone for this topic: it always ships
    `tax_exemptions` and `admin_graphql_api_id` with it.
    """
    _seed_customer(db, shopify_customer_id="555")

    res = handle_shopify_customer_delete(
        db, _documented_customer_delete(555), topic="customers/delete"
    )

    # STATE first: this is what breaks if the guard is too strict.
    cust = _customer_row(db)
    assert cust.get("shopify_erasure_requested") is True, (
        "the DOCUMENTED customers/delete body was refused -- IMS would silently "
        "ignore every real GDPR/DPDP erasure request"
    )
    assert cust.get("shopify_erasure_requested_at")
    assert res["status"] == "erasure_flagged"
    # Still never a hard delete.
    assert db.get_collection("customers").count_documents({}) == 1


def test_genuine_customer_delete_with_delivery_metadata_still_flags(db):
    _seed_customer(db, shopify_customer_id="555")

    res = handle_shopify_customer_delete(
        db,
        {
            "id": 555,
            "admin_graphql_api_id": "gid://shopify/Customer/555",
            "shop_id": 690933842,
        },
        topic="customers/delete",
    )

    assert res["status"] == "erasure_flagged"
    assert _customer_row(db)["shopify_erasure_requested"] is True


def test_relabelled_customer_update_body_does_not_flag_erasure(db):
    """A customers/update body is a capture candidate too -- a thin one."""
    _seed_customer(db, shopify_customer_id="555")

    handle_shopify_customer_delete(
        db, {"id": 555, "email": "ravi@example.com"}, topic="customers/delete"
    )

    assert not _customer_row(db).get("shopify_erasure_requested")


def test_order_body_relabelled_to_customers_delete_does_not_flag(db):
    """Cross-resource relabel: an order body's top-level `id` could collide
    with a customer id. The minimality rule refuses it on shape alone."""
    _seed_customer(db, shopify_customer_id="5001")

    handle_shopify_customer_delete(
        db, _captured_order_create(5001), topic="customers/delete"
    )

    assert not _customer_row(db, "5001").get("shopify_erasure_requested")


def test_customer_delete_refusal_is_reported_as_skipped(db):
    _seed_customer(db, shopify_customer_id="555")

    res = handle_shopify_customer_delete(
        db, _captured_customer_create(555), topic="customers/delete"
    )

    assert res["status"] == "skipped"
    assert res["reason"] in {
        "create_shaped_payload",
        "not_delete_shaped",
        "child_resource_payload",
    }


# ===========================================================================
# The shared classifier itself -- ONE definition, both kinds.
# ===========================================================================


def test_both_kinds_refuse_a_content_bearing_body_via_the_same_helper():
    """The anti-drift property: one helper answers for both doors. If a future
    edit guards orders but not customers, this fails."""
    assert delete_payload_refusal(_captured_order_create(1), kind=KIND_ORDER)
    assert delete_payload_refusal(_captured_customer_create(1), kind=KIND_CUSTOMER)
    # And a content-bearing body is refused whichever kind it is classified as
    # -- the load-bearing minimality rule is kind-independent by construction.
    assert delete_payload_refusal(_captured_order_create(1), kind=KIND_CUSTOMER)
    assert delete_payload_refusal(_captured_customer_create(1), kind=KIND_ORDER)


def test_minimality_rule_catches_a_body_with_no_known_marker():
    """The load-bearing rule must not depend on the marker lists. A body that
    dodges every named create marker but still carries content is refused."""
    for kind in (KIND_ORDER, KIND_CUSTOMER):
        assert (
            delete_payload_refusal(
                {"id": 5001, "some_future_shopify_field": "x"}, kind=kind
            )
            == "not_delete_shaped"
        )


def test_minimal_delete_body_is_accepted_by_the_classifier():
    for kind in (KIND_ORDER, KIND_CUSTOMER):
        assert delete_payload_refusal({"id": 5001}, kind=kind) is None
        assert delete_payload_refusal({}, kind=kind) is None


def test_documented_delete_bodies_are_accepted_by_the_classifier():
    """Pins Shopify's REAL delete bodies against a future tightening. Any edit
    to the allowed-key sets that would start refusing a live webhook fails
    here, at the classifier, with a name that says what broke."""
    assert delete_payload_refusal(_documented_order_delete(5001), kind=KIND_ORDER) is None
    assert (
        delete_payload_refusal(_documented_customer_delete(555), kind=KIND_CUSTOMER)
        is None
    ), "the documented customers/delete body must never be refused"


def test_customer_tax_exemptions_margin_does_not_open_the_create_door():
    """`tax_exemptions` is admitted on the customer kind because the real
    delete body carries it. That margin must not let a customers/create body
    through: create bodies carry it TOO, and are refused on their other keys."""
    create = _captured_customer_create(555)
    create["tax_exemptions"] = []
    assert delete_payload_refusal(create, kind=KIND_CUSTOMER)
    # ...and the margin is customer-only: an order delete body may not carry it.
    assert (
        delete_payload_refusal({"id": 5001, "tax_exemptions": []}, kind=KIND_ORDER)
        == "not_delete_shaped"
    )


def test_parent_key_rule_is_diagnostic_only_by_construction():
    """Pins WHY deleting the parent-reference rule changes no door outcome.

    Rule 1 fires only when the payload carries `order_id` / `customer_id`.
    Neither key is in ANY allowed-key set, so whenever rule 1 would fire the
    minimality rule refuses the same payload anyway -- rule 1 only upgrades the
    log line from "not_delete_shaped" to "child_resource_payload". Deleting it
    is therefore an EQUIVALENT mutant, not a coverage hole.

    This invariant is what makes that true. If anyone ever adds `order_id` or
    `customer_id` to an allowed-key set, rule 1 becomes load-bearing and the
    equivalence silently stops holding -- so this test fails first.
    """
    from api.services.shopify_delete_shape import _allowed_keys, _PARENT_KEY

    for kind in (KIND_ORDER, KIND_CUSTOMER):
        parent = _PARENT_KEY[kind]
        assert parent not in _allowed_keys(kind), (
            f"{parent} became an allowed delete key -- the parent-reference "
            f"rule is now load-bearing and needs its own STATE test"
        )
        # ...and therefore the minimality rule alone already refuses it.
        assert (
            unexpected_delete_keys({"id": 1, parent: 99}, kind=kind) == [parent]
        )


def test_unknown_kind_is_refused_outright():
    """A caller-side typo at a future third destructive door must fail closed
    and loud, not silently fall back to the base rules."""
    assert delete_payload_refusal({"id": 5001}, kind="ordr") == "unknown_delete_kind"
    assert delete_payload_refusal({}, kind="") == "unknown_delete_kind"


def test_unexpected_delete_keys_is_kind_aware_and_sorted():
    """The log line names the offending keys; it must not report a key the kind
    legitimately allows."""
    assert unexpected_delete_keys(
        {"id": 1, "tax_exemptions": []}, kind=KIND_CUSTOMER
    ) == []
    assert unexpected_delete_keys(
        {"id": 1, "tax_exemptions": []}, kind=KIND_ORDER
    ) == ["tax_exemptions"]
    assert unexpected_delete_keys(
        {"zeta": 1, "id": 2, "alpha": 3}, kind=KIND_ORDER
    ) == ["alpha", "zeta"]


def test_every_delete_handler_in_the_nexus_table_is_guarded():
    """Anti-drift sweep. Walks the AUTHORITATIVE topic table and asserts that
    every DESTRUCTIVE topic routes to a handler whose module imports the shared
    guard. A future destructive topic added without a guard fails here."""
    from agents.implementations.nexus import NexusAgent

    table = NexusAgent._SHOPIFY_TOPIC_HANDLERS
    destructive = [t for t in table if t.endswith("/delete")]
    assert set(destructive) == {"orders/delete", "customers/delete"}, (
        "a new destructive Shopify topic appeared -- guard it with "
        "shopify_delete_shape.delete_payload_refusal and update this sweep"
    )

    import api.services.shopify_customer_delete as cust_mod
    import api.services.shopify_order_delete as order_mod

    for mod in (order_mod, cust_mod):
        assert getattr(mod, "delete_payload_refusal", None) is delete_payload_refusal
