"""EXPENSE_SCHEMA must describe what api/routers/expenses.py actually persists.

WHY THIS FILE EXISTS
====================
``database/schemas.py`` holds MongoDB ``$jsonSchema`` validators that nothing
applies at startup -- but ``database/migrations.py::run_migrations`` WOULD apply
every one of them via ``collMod`` with ``validationLevel: "moderate"``, which
validates every insert. A schema that disagrees with its writers is therefore
not stale documentation, it is a live outage waiting for whoever runs the
migration: the module simply stops accepting new records (error 121).

EXPENSE_SCHEMA had drifted so far that, had the migration run, NO expense claim
could have been filed at all -- the schema required an ``expense_number`` no
writer mints, and its ``category`` enum shared exactly one value with the nine
the router can actually produce.

WHAT IS PINNED HERE
===================
1. The ``category`` enum equals ``expenses.EXPENSE_CATEGORIES`` (the same
   schema-mirrors-the-registry pattern as
   ``test_unification_8_category_registry::test_product_schema_category_enum_mirrors_registry``).
2. BEHAVIOURAL: the real ``POST /expenses`` -> ``/approve`` ->
   ``/send-to-accountant`` -> ``/mark-entered`` and ``/reject`` routes are driven
   through the REAL ``ExpenseRepository``, and the document that actually lands
   in the collection is validated against EXPENSE_SCHEMA after every step.
3. DISCRIMINATING POWER: the pre-fix schema is embedded verbatim and asserted to
   REJECT that same real document. If someone "fixes" this file by loosening the
   checker, test 3 goes green-on-nothing and fails.

The user fixture deliberately mirrors the REAL JWT payload built at
``auth.py:910-922``. In particular it carries no ``full_name`` claim -- because
the token does not -- which is why ``employee_name`` lands as ``None`` on every
expense and why the schema types it ``["string", "null"]``. A fixture that
helpfully supplied ``full_name`` would hide that.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from database.repositories.expense_repository import ExpenseRepository  # noqa: E402
from database.schemas import EXPENSE_SCHEMA  # noqa: E402

from bson_schema_check import assert_valid, validate  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402


# ---------------------------------------------------------------------------
# The schema exactly as it stood before this change. Test 3 proves the
# behavioural test above is not vacuous by showing this version REJECTS the very
# document the router produces.
# ---------------------------------------------------------------------------
PRE_FIX_EXPENSE_SCHEMA = {
    "bsonType": "object",
    "required": ["expense_id", "expense_number", "employee_id", "category", "amount", "status"],
    "properties": {
        "expense_id": {"bsonType": "string"},
        "expense_number": {"bsonType": "string"},
        "employee_id": {"bsonType": "string"},
        "employee_name": {"bsonType": "string"},
        "store_id": {"bsonType": "string"},
        "category": {"enum": ["TRAVEL", "FOOD", "COURIER", "REPAIRS",
                              "OFFICE_SUPPLIES", "CLIENT_RELATED", "PETTY_CASH", "OTHER"]},
        "amount": {"bsonType": "decimal"},
        "description": {"bsonType": "string"},
        "expense_date": {"bsonType": "date"},
        "has_bill": {"bsonType": "bool"},
        "bill_waived": {"bsonType": "bool"},
        "status": {"enum": ["DRAFT", "SUBMITTED", "PENDING_APPROVAL", "APPROVED",
                            "REJECTED", "PAID", "CANCELLED"]},
        "approved_by": {"bsonType": "string"},
        "approved_at": {"bsonType": "date"},
        "rejection_reason": {"bsonType": "string"},
        "paid_at": {"bsonType": "date"},
        "payment_reference": {"bsonType": "string"},
        "advance_id": {"bsonType": "string"},
        "created_at": {"bsonType": "date"},
    },
}


# ===========================================================================
# 1. Registry parity -- the enum mirrors the door's canonical category list
# ===========================================================================


def test_expense_schema_category_enum_mirrors_router_registry():
    """The only categories a caller can get past ExpenseCreate are
    EXPENSE_CATEGORIES; the validator must accept exactly those."""
    enum = EXPENSE_SCHEMA["properties"]["category"]["enum"]
    assert set(enum) == set(expenses.EXPENSE_CATEGORIES)


def test_expense_schema_requires_only_fields_the_create_door_writes():
    """`required` must not name a field no writer produces -- that is what made
    the old schema reject 100% of inserts."""
    assert "expense_number" not in EXPENSE_SCHEMA["required"]


# ===========================================================================
# 2. Behavioural -- drive the real routes, validate what really got stored
# ===========================================================================


def _jwt_payload(user_id: str, roles, *, active_store="BV-RAN-01"):
    """The REAL access-token payload shape (api/routers/auth.py:910-922).

    Deliberately NO `full_name` key -- the JWT has none, which is exactly why
    employee_name is written as None.
    """
    return {
        "user_id": user_id,
        "username": user_id,
        "roles": list(roles),
        "store_ids": [active_store],
        "active_store_id": active_store,
        "must_change_password": False,
        "module_access": {},
    }


@pytest.fixture
def wired(monkeypatch):
    """Real ExpenseRepository over a strict in-memory collection.

    Using the real repository (not a hand-rolled fake) matters: BaseRepository.
    _add_timestamps OVERWRITES the ISO string the router passes for created_at
    with a real datetime, and that is the only reason created_at is legitimately
    a BSON `date`. A double that skipped it would make this test prove nothing.
    """
    db = StrictDB()
    collection = db.get_collection("expenses")
    repo = ExpenseRepository(collection)

    monkeypatch.setattr(expenses, "get_expense_repository", lambda: repo)
    monkeypatch.setattr(expenses, "get_advance_repository", lambda: None)
    monkeypatch.setattr(expenses, "get_db", lambda: db)

    # _period_locked re-imports get_db from database.connection directly.
    import database.connection as connection

    monkeypatch.setattr(
        connection, "get_db", lambda: type("H", (), {"db": db})(), raising=False
    )
    return db, collection, repo


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(expenses.router, prefix="/api/v1/expenses")

    async def _fake_user():
        return user

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _stored(collection, expense_id: str) -> dict:
    for doc in collection.docs:
        if doc.get("expense_id") == expense_id:
            return doc
    raise AssertionError(f"expense {expense_id} was never stored")


def _create(client, **overrides) -> str:
    body = {
        "category": "travel",
        "amount": 1250.5,
        "description": "Auto to the Ranchi lens lab",
        "expense_date": "2026-08-24",
        "payment_mode": "CASH",
    }
    body.update(overrides)
    response = client.post("/api/v1/expenses", json=body)
    assert response.status_code == 201, response.text
    return response.json()["expense_id"]


def test_created_expense_document_satisfies_expense_schema(wired):
    _db, collection, _repo = wired
    staff = _jwt_payload("emp-1", ["SALES_STAFF"])

    expense_id = _create(_client(staff))
    doc = _stored(collection, expense_id)

    assert_valid(doc, EXPENSE_SCHEMA, label="created expense")

    # Spot-check the specific shapes the old schema got wrong, so a future
    # regression names itself instead of hiding behind a generic failure.
    assert doc["status"] == "PENDING"
    assert doc["category"] == "travel"
    assert isinstance(doc["amount"], float)
    assert isinstance(doc["expense_date"], str)
    assert "expense_number" not in doc
    # The JWT carries no full_name, so this is always None today.
    assert doc["employee_name"] is None


def test_approved_then_sent_then_entered_documents_satisfy_expense_schema(wired):
    _db, collection, _repo = wired
    staff = _jwt_payload("emp-1", ["SALES_STAFF"])
    manager = _jwt_payload("mgr-1", ["STORE_MANAGER"])
    accountant = _jwt_payload("acc-1", ["ACCOUNTANT"])

    expense_id = _create(_client(staff))

    approve = _client(manager).post(f"/api/v1/expenses/{expense_id}/approve")
    assert approve.status_code == 200, approve.text
    doc = _stored(collection, expense_id)
    assert doc["status"] == "APPROVED"
    assert isinstance(doc["approved_at"], str)  # ISO string, NOT a BSON date
    assert_valid(doc, EXPENSE_SCHEMA, label="approved expense")

    sent = _client(manager).post(f"/api/v1/expenses/{expense_id}/send-to-accountant")
    assert sent.status_code == 200, sent.text
    doc = _stored(collection, expense_id)
    assert doc["status"] == "SENT_TO_ACCOUNTANT"
    assert_valid(doc, EXPENSE_SCHEMA, label="expense sent to accountant")

    entered = _client(accountant).post(f"/api/v1/expenses/{expense_id}/mark-entered")
    assert entered.status_code == 200, entered.text
    doc = _stored(collection, expense_id)
    assert doc["status"] == "ENTERED"
    # No ledger_reference query param was supplied -> the field is stored as None.
    assert doc["ledger_reference"] is None
    assert_valid(doc, EXPENSE_SCHEMA, label="expense marked entered")


def test_rejected_expense_document_satisfies_expense_schema(wired):
    _db, collection, _repo = wired
    staff = _jwt_payload("emp-1", ["SALES_STAFF"])
    manager = _jwt_payload("mgr-1", ["STORE_MANAGER"])

    expense_id = _create(_client(staff))
    rejected = _client(manager).post(
        f"/api/v1/expenses/{expense_id}/reject", params={"reason": "No receipt"}
    )
    assert rejected.status_code == 200, rejected.text

    doc = _stored(collection, expense_id)
    assert doc["status"] == "REJECTED"
    assert_valid(doc, EXPENSE_SCHEMA, label="rejected expense")


@pytest.mark.parametrize("category", sorted(expenses.EXPENSE_CATEGORIES))
def test_every_accepted_category_validates(wired, category):
    """Every category the door can produce must pass the validator. PETTY_CASH
    is created here but not approved -- approval debits a store float, which is
    a different module's test."""
    _db, collection, _repo = wired
    staff = _jwt_payload("emp-1", ["SALES_STAFF"])

    expense_id = _create(_client(staff), category=category, amount=99.0)
    assert_valid(_stored(collection, expense_id), EXPENSE_SCHEMA, label=category)


def test_expense_without_a_store_validates(wired):
    """A user with no active store writes store_id: None -- the old schema typed
    it a bare string and would have rejected the insert."""
    _db, collection, _repo = wired
    user = _jwt_payload("emp-2", ["SALES_STAFF"])
    user["active_store_id"] = None
    user["store_ids"] = []

    expense_id = _create(_client(user))
    doc = _stored(collection, expense_id)
    assert doc["store_id"] is None
    assert_valid(doc, EXPENSE_SCHEMA, label="storeless expense")


# ===========================================================================
# 3. Discriminating power -- the OLD schema must reject the SAME real document
# ===========================================================================


def test_pre_fix_schema_would_have_rejected_the_real_created_document(wired):
    """If this ever passes, the checker has been neutered and every assert_valid
    above is worthless."""
    _db, collection, _repo = wired
    staff = _jwt_payload("emp-1", ["SALES_STAFF"])
    doc = _stored(collection, _create(_client(staff)))

    errors = validate(doc, PRE_FIX_EXPENSE_SCHEMA)
    assert errors, "the pre-fix schema accepted the real document -- checker is broken"

    joined = "\n".join(errors)
    # The five disagreements that would have taken the module down.
    assert "required field 'expense_number' is missing" in joined
    assert "category" in joined and "not in enum" in joined
    assert "amount" in joined and "decimal" in joined
    assert "expense_date" in joined and "date" in joined
    assert "status" in joined and "PENDING" in joined


def test_pre_fix_schema_also_rejected_the_approved_document(wired):
    _db, collection, _repo = wired
    staff = _jwt_payload("emp-1", ["SALES_STAFF"])
    manager = _jwt_payload("mgr-1", ["STORE_MANAGER"])
    expense_id = _create(_client(staff))
    _client(manager).post(f"/api/v1/expenses/{expense_id}/approve")

    errors = validate(_stored(collection, expense_id), PRE_FIX_EXPENSE_SCHEMA)
    joined = "\n".join(errors)
    assert "approved_at" in joined  # ISO string vs declared BSON date
