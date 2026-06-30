"""
IMS 2.0 - Bill-to-member (Account / Member) Phase 1 tests
=========================================================
Council COUNCIL_ACCOUNT_BILLING_DECISION_2026-06-19 (LOCKED).

Covers:
  * member_billing pure resolver (build/ensure/choose/find).
  * order-create member resolution end-to-end (TestClient + FakeDB):
      - valid patient_id accepted + persisted,
      - patient_id of a DIFFERENT account -> 422,
      - missing patient_id -> auto-resolves to the account's Primary (NOT
        rejected) and the order carries that member,
      - account with NO members yet -> a Primary is seeded + the order bills it,
      - walk-in (no account doc) -> synthetic Primary stamped on the order.
  * account-create auto-adds a Primary member (is_primary + pointer).
  * backfill migration logic (PASS A + PASS B) against an in-memory FakeDB,
    including idempotency.
"""
import pytest

from api.services.member_billing import (
    build_primary_member,
    choose_primary_member,
    ensure_primary_member,
    find_member,
)


# ===========================================================================
# Pure resolver
# ===========================================================================

def test_build_primary_member_defaults():
    m = build_primary_member(name="Alka", mobile="9123456789")
    assert m["name"] == "Alka"
    assert m["mobile"] == "9123456789"
    assert m["relation"] == "Self"
    assert m["is_primary"] is True
    assert m["patient_id"]  # a uuid was minted


def test_choose_primary_prefers_is_primary_then_self_then_first():
    members = [
        {"patient_id": "p1", "name": "Kid", "relation": "Son"},
        {"patient_id": "p2", "name": "Dad", "relation": "Self"},
        {"patient_id": "p3", "name": "Mom", "relation": "Wife", "is_primary": True},
    ]
    assert choose_primary_member(members)["patient_id"] == "p3"
    # Without an explicit flag, Self wins.
    assert choose_primary_member(members[:2])["patient_id"] == "p2"
    # Neither flag nor Self -> first.
    assert choose_primary_member(members[:1])["patient_id"] == "p1"
    assert choose_primary_member([]) is None


def test_ensure_primary_seeds_when_empty():
    cust = {"customer_id": "c1", "name": "Mahesh", "mobile": "9000000001", "patients": []}
    primary, changed = ensure_primary_member(cust)
    assert changed is True
    assert primary["is_primary"] is True
    assert primary["name"] == "Mahesh"
    assert len(cust["patients"]) == 1
    assert cust["primary_patient_id"] == primary["patient_id"]


def test_ensure_primary_flags_existing_member_idempotently():
    cust = {
        "customer_id": "c1",
        "name": "Mahesh",
        "patients": [{"patient_id": "p1", "name": "Mahesh", "relation": "Self"}],
    }
    primary, changed = ensure_primary_member(cust)
    assert changed is True  # flagged is_primary + set pointer
    assert primary["patient_id"] == "p1"
    assert cust["patients"][0]["is_primary"] is True
    # Second run is a no-op.
    _p2, changed2 = ensure_primary_member(cust)
    assert changed2 is False


def test_find_member_returns_none_for_cross_account():
    cust = {
        "customer_id": "c1",
        "patients": [{"patient_id": "p1", "name": "A"}],
    }
    assert find_member(cust, "p1")["name"] == "A"
    assert find_member(cust, "does-not-exist") is None
    assert find_member(cust, "") is None


def test_find_member_legacy_self_fallback():
    # Legacy account where patient_id == customer_id with no real member row.
    cust = {"customer_id": "LEGACY-1", "name": "Old", "patients": []}
    m = find_member(cust, "LEGACY-1")
    assert m is not None and m["_legacy_self"] is True


# ===========================================================================
# Order-create resolution (end-to-end via TestClient + FakeDB)
# ===========================================================================

@pytest.fixture
def member_orders(monkeypatch):
    """Wire fake DB + repos into the orders router (mirrors hardening_orders)
    and seed two accounts: cust-A (one member) and cust-B (one member)."""
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
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

    customer_repo.create(
        {
            "customer_id": "cust-A",
            "name": "Account A",
            "mobile": "9100000001",
            "phone": "9100000001",
            "patients": [
                {"patient_id": "A-self", "name": "Account A", "relation": "Self", "is_primary": True},
                {"patient_id": "A-son", "name": "Son A", "relation": "Son"},
            ],
            "primary_patient_id": "A-self",
        }
    )
    customer_repo.create(
        {
            "customer_id": "cust-B",
            "name": "Account B",
            "mobile": "9100000002",
            "phone": "9100000002",
            "patients": [
                {"patient_id": "B-self", "name": "Account B", "relation": "Self", "is_primary": True},
            ],
            "primary_patient_id": "B-self",
        }
    )
    # cust-C: a legacy/imported account with NO members yet.
    customer_repo.create(
        {
            "customer_id": "cust-C",
            "name": "Account C",
            "mobile": "9100000003",
            "phone": "9100000003",
            "patients": [],
        }
    )

    return {"db": fake_db, "order_repo": order_repo, "customer_repo": customer_repo}


def _frame_item():
    return {
        "product_id": "custom-frame",
        "product_name": "Test Frame",
        "item_type": "FRAME",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": 1000.0,
    }


def _post(client, headers, customer_id, **extra):
    payload = {"customer_id": customer_id, "items": [_frame_item()], **extra}
    return client.post("/api/v1/orders", json=payload, headers=headers)


def _saved_order(member_orders, customer_id):
    docs = [
        d for d in member_orders["order_repo"].collection.docs
        if d.get("customer_id") == customer_id
    ]
    assert docs, f"no order saved for {customer_id}"
    return docs[-1]


def test_valid_patient_id_accepted_and_persisted(client, auth_headers, member_orders):
    r = _post(client, auth_headers, "cust-A", patient_id="A-son")
    assert r.status_code in (200, 201), r.text
    saved = _saved_order(member_orders, "cust-A")
    assert saved["patient_id"] == "A-son"
    assert saved["billed_to_member_name"] == "Son A"


def test_cross_account_patient_id_rejected_422(client, auth_headers, member_orders):
    # B-self belongs to cust-B, not cust-A -> reject.
    r = _post(client, auth_headers, "cust-A", patient_id="B-self")
    assert r.status_code == 422, r.text
    assert "member of this account" in r.text


def test_missing_patient_id_auto_resolves_to_primary(client, auth_headers, member_orders):
    r = _post(client, auth_headers, "cust-A")  # no patient_id
    assert r.status_code in (200, 201), r.text
    saved = _saved_order(member_orders, "cust-A")
    assert saved["patient_id"] == "A-self"  # the Primary
    assert saved["billed_to_member_name"] == "Account A"


def test_account_without_members_seeds_primary_and_bills_it(
    client, auth_headers, member_orders
):
    r = _post(client, auth_headers, "cust-C")  # cust-C has empty patients[]
    assert r.status_code in (200, 201), r.text
    saved = _saved_order(member_orders, "cust-C")
    assert saved["patient_id"], "a Primary should have been seeded + billed"
    assert saved["billed_to_member_name"] == "Account C"
    # The seeded Primary was persisted back onto the account.
    cust = member_orders["customer_repo"].find_by_id("cust-C")
    assert cust["patients"], "Primary not persisted on the account"
    assert cust["primary_patient_id"] == saved["patient_id"]


def test_walkin_gets_synthetic_primary(client, auth_headers, member_orders):
    wid = "walkin-123"
    r = _post(client, auth_headers, wid)
    assert r.status_code in (200, 201), r.text
    saved = _saved_order(member_orders, wid)
    assert saved["patient_id"], "walk-in order must still carry a member"
    assert saved["billed_to_member_name"]  # synthetic Primary name


# ===========================================================================
# Account-create auto-Primary
# ===========================================================================

@pytest.fixture
def customers_wired(monkeypatch):
    from tests.test_walkouts import FakeDB
    from api.routers import customers as customers_module
    from api import dependencies as deps_module
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(
        customers_module, "get_customer_repository", lambda: customer_repo
    )
    # _audit_customer pulls the audit repo from dependencies.
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)
    return {"db": fake_db, "customer_repo": customer_repo}


def test_account_create_auto_adds_primary(client, auth_headers, customers_wired):
    r = client.post(
        "/api/v1/customers",
        json={"name": "New Family", "mobile": "9876500011", "customer_type": "B2C"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    cid = r.json()["customer_id"]
    cust = customers_wired["customer_repo"].find_by_id(cid)
    assert cust["patients"], "no Primary member auto-added"
    primary = choose_primary_member(cust["patients"])
    assert primary["is_primary"] is True
    assert cust["primary_patient_id"] == primary["patient_id"]


# ===========================================================================
# Backfill migration logic (against in-memory FakeDB)
# ===========================================================================

@pytest.fixture
def migration_db():
    """A FakeDB-style db usable by the backfill passes. The passes call
    count_documents / find / update_one / insert_one, all provided by the
    FakeCollection stub used elsewhere."""
    from tests.test_walkouts import FakeDB
    return FakeDB()


def test_backfill_pass_a_seeds_primary(migration_db):
    from scripts.backfill_order_members import run_customers

    custs = migration_db.get_collection("customers")
    custs.insert_one({"customer_id": "c1", "name": "Empty One", "mobile": "9000000001", "patients": []})
    custs.insert_one({
        "customer_id": "c2", "name": "Has Self",
        "patients": [{"patient_id": "p2", "name": "Has Self", "relation": "Self"}],
    })

    res = run_customers(migration_db, dry_run=False)
    assert res["minted_primary"] == 1
    assert res["flagged_primary"] == 1

    c1 = custs.find_one({"customer_id": "c1"})
    assert c1["patients"] and c1["primary_patient_id"]
    c2 = custs.find_one({"customer_id": "c2"})
    assert c2["patients"][0]["is_primary"] is True

    # Idempotent: re-run touches nothing.
    res2 = run_customers(migration_db, dry_run=False)
    assert res2["minted_primary"] == 0 and res2["flagged_primary"] == 0


def test_backfill_pass_b_backfills_orders(migration_db):
    from scripts.backfill_order_members import run_customers, run_orders

    custs = migration_db.get_collection("customers")
    orders = migration_db.get_collection("orders")
    custs.insert_one({
        "customer_id": "c1", "name": "Fam",
        "patients": [
            {"patient_id": "p1", "name": "Fam", "relation": "Self", "is_primary": True},
            {"patient_id": "p2", "name": "Kid", "relation": "Son"},
        ],
        "primary_patient_id": "p1",
    })
    # order with no patient_id -> backfill to Primary p1
    orders.insert_one({"order_id": "o1", "customer_id": "c1", "customer_name": "Fam"})
    # order with null patient_id -> backfill too
    orders.insert_one({"order_id": "o2", "customer_id": "c1", "patient_id": None, "customer_name": "Fam"})
    # walk-in order, no customer doc -> synthetic Primary
    orders.insert_one({"order_id": "o3", "customer_id": "walkin-9", "customer_name": "Walk-in Customer"})
    # already-billed order -> left alone
    orders.insert_one({"order_id": "o4", "customer_id": "c1", "patient_id": "p2"})

    run_customers(migration_db, dry_run=False)
    res = run_orders(migration_db, dry_run=False)
    assert res["backfilled_account_primary"] == 2
    assert res["synthetic_primary"] == 1

    assert orders.find_one({"order_id": "o1"})["patient_id"] == "p1"
    assert orders.find_one({"order_id": "o2"})["patient_id"] == "p1"
    assert orders.find_one({"order_id": "o3"})["patient_id"].startswith("synthetic-")
    assert orders.find_one({"order_id": "o4"})["patient_id"] == "p2"  # untouched

    # Idempotent: re-run finds nothing left to backfill.
    res2 = run_orders(migration_db, dry_run=False)
    assert res2["missing_total"] == 0


def test_backfill_dry_run_writes_nothing(migration_db):
    from scripts.backfill_order_members import run_customers

    custs = migration_db.get_collection("customers")
    custs.insert_one({"customer_id": "c1", "name": "Empty", "patients": []})
    run_customers(migration_db, dry_run=True)
    # Dry run must NOT mutate the doc.
    assert custs.find_one({"customer_id": "c1"})["patients"] == []
