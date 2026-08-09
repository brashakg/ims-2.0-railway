"""
IMS 2.0 - POS P3 backlog items: POS-9, POS-10, POS-11, POS-12, POS-14
=======================================================================
Model/unit-level regression tests (no DB or full app boot needed).

POS-9  Server-side length cap + sanitization on order/cart text fields.
       OrderItemCreate.product_name <= 200 chars.
       OrderItemCreate.discount_reason <= 200 chars.
       OrderItemCreate.item_note <= 200 chars.
       OrderCreate.notes <= 500 chars.
       OrderCreate.cart_discount_reason <= 200 chars.

POS-10 item_note / order_type persisted on create.
       OrderItemCreate now accepts item_note (max 200).
       OrderCreate now accepts order_type (max 50).
       Both fields survive round-trip through the schema (not silently dropped).

POS-11 cancelOrder sends reason as raw body -- should be a query param.
       Verified by reading the backend signature: reason: str = Query(...).
       The frontend fix (sales.ts) is tested indirectly here by asserting the
       backend schema does NOT have a Pydantic body model for the reason.

POS-12 Order status timeline/history with timestamps.
       BEHAVIOURAL: creating an order through the real handler stores a
       status_history whose first entry is DRAFT; OrderRepository.update_status
       APPENDS one well-formed entry per transition (asserted on the stored
       document, not on the source text).

POS-14 Extend Idempotency-Key to payments / returns / expense-create.
       Signature-level: all three accept the header.
       BEHAVIOURAL: posting the same payment / expense twice with one
       Idempotency-Key records exactly ONE money row, stamps the key on it and
       replays the original id; distinct keys (and no key at all) still record
       two. For returns the REPLAY half is behavioural; the stamping half stays
       textual and is justified inline.

NOTE ON SOURCE-TEXT ASSERTIONS
       Several checks in this file used to grep handler source for a substring.
       That cannot distinguish "the line exists" from "the line RUNS", and it
       silently pointed at the WRONG function when inspect.getsource
       desynchronised during a full-suite run. See tests/source_guard.py for the
       mechanism and the fail-loud wrapper the few surviving textual checks use.
"""

from __future__ import annotations

import inspect
import os
import sys
from typing import get_type_hints

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# Behavioural fixtures
# ---------------------------------------------------------------------------
# These wire the REAL routers/repositories to strict in-memory collections
# (tests/strict_fakes.py) so the handlers can be driven end-to-end through the
# TestClient with no MongoDB, and the assertion can be made against what was
# actually STORED rather than against the handler's source text.


@pytest.fixture
def payment_order(monkeypatch):
    """A CONFIRMED order wired into the orders router via the real repository."""
    from api.routers import orders as orders_module
    from database.repositories.order_repository import OrderRepository
    from strict_fakes import StrictCollection

    order = {
        "order_id": "ord-idem-pay",
        "store_id": "BV-TEST-01",
        "customer_id": "walkin-idem",
        "status": "CONFIRMED",
        "grand_total": 1000.0,
        "balance_due": 1000.0,
        "amount_paid": 0.0,
        "payments": [],
        "items": [],
    }
    coll = StrictCollection("orders", [order])
    repo = OrderRepository(coll)
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
    # The order is a walk-in, so the credit-limit path is never entered; keep
    # the customer repo absent so nothing silently reaches for a real DB.
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: None)
    return {"collection": coll, "repo": repo, "order": coll.docs[0]}


@pytest.fixture
def expense_env(monkeypatch):
    """Real ExpenseRepository over a strict collection, wired into the router."""
    from api.routers import expenses as expenses_module
    from database.repositories.expense_repository import ExpenseRepository
    from strict_fakes import StrictCollection

    coll = StrictCollection("expenses")
    repo = ExpenseRepository(coll)
    monkeypatch.setattr(expenses_module, "get_expense_repository", lambda: repo)
    return {"expenses": coll, "repo": repo}


@pytest.fixture
def returns_collection(monkeypatch):
    """Strict `returns` collection behind the returns router's _get_db()."""
    from api.routers import returns as returns_module
    from strict_fakes import StrictDB

    db = StrictDB()
    monkeypatch.setattr(returns_module, "_get_db", lambda: db)
    return db.get_collection("returns")


@pytest.fixture
def order_create_env(monkeypatch):
    """Wire POST /api/v1/orders to strict collections + real repositories."""
    from api.routers import orders as orders_module
    from api.routers import payout as payout_module
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository
    from strict_fakes import StrictDB

    db = StrictDB()
    order_repo = OrderRepository(db.get_collection("orders"))
    customer_repo = CustomerRepository(db.get_collection("customers"))
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(payout_module, "get_db", lambda: db)
    monkeypatch.setattr(payout_module, "get_user_repository", lambda: None)
    customer_repo.create(
        {
            "customer_id": "cust-hist",
            "name": "History Test",
            "mobile": "9100000042",
            "phone": "9100000042",
        }
    )
    return {"db": db, "orders": db.get_collection("orders"), "order_repo": order_repo}


# ---------------------------------------------------------------------------
# POS-9: text field length caps
# ---------------------------------------------------------------------------

from api.routers.orders import OrderItemCreate, OrderCreate  # noqa: E402
from pydantic import ValidationError  # noqa: E402


class TestPOS9LengthCaps:
    """Server-side length caps on order/cart text fields."""

    # ---- OrderItemCreate -----------------------------------------------

    def test_product_name_within_limit(self):
        """200-char product_name is accepted."""
        item = OrderItemCreate(
            item_type="FRAME",
            product_id="test-prod",
            product_name="A" * 200,
            unit_price=500.0,
        )
        assert len(item.product_name) == 200

    def test_product_name_over_limit_rejected(self):
        """201-char product_name is rejected with a clear 422."""
        with pytest.raises(ValidationError):
            OrderItemCreate(
                item_type="FRAME",
                product_id="test-prod",
                product_name="A" * 201,
                unit_price=500.0,
            )

    def test_discount_reason_within_limit(self):
        """200-char discount_reason is accepted."""
        item = OrderItemCreate(
            item_type="FRAME",
            product_id="test-prod",
            unit_price=500.0,
            discount_percent=5.0,
            discount_reason="X" * 200,
        )
        assert len(item.discount_reason) == 200

    def test_discount_reason_over_limit_rejected(self):
        """201-char discount_reason is rejected."""
        with pytest.raises(ValidationError):
            OrderItemCreate(
                item_type="FRAME",
                product_id="test-prod",
                unit_price=500.0,
                discount_reason="X" * 201,
            )

    def test_item_note_within_limit(self):
        """200-char item_note is accepted."""
        item = OrderItemCreate(
            item_type="FRAME",
            product_id="test-prod",
            unit_price=500.0,
            item_note="N" * 200,
        )
        assert len(item.item_note) == 200

    def test_item_note_over_limit_rejected(self):
        """201-char item_note is rejected."""
        with pytest.raises(ValidationError):
            OrderItemCreate(
                item_type="FRAME",
                product_id="test-prod",
                unit_price=500.0,
                item_note="N" * 201,
            )

    # ---- OrderCreate ---------------------------------------------------

    def test_notes_within_limit(self):
        """500-char cart notes are accepted."""
        order = OrderCreate(
            customer_id="cust-123",
            items=[
                OrderItemCreate(
                    item_type="FRAME", product_id="prod-1", unit_price=500.0
                )
            ],
            notes="Z" * 500,
        )
        assert len(order.notes) == 500

    def test_notes_over_limit_rejected(self):
        """501-char cart notes are rejected."""
        with pytest.raises(ValidationError):
            OrderCreate(
                customer_id="cust-123",
                items=[
                    OrderItemCreate(
                        item_type="FRAME", product_id="prod-1", unit_price=500.0
                    )
                ],
                notes="Z" * 501,
            )

    def test_cart_discount_reason_within_limit(self):
        """200-char cart_discount_reason is accepted."""
        order = OrderCreate(
            customer_id="cust-123",
            items=[
                OrderItemCreate(
                    item_type="FRAME", product_id="prod-1", unit_price=500.0
                )
            ],
            cart_discount_percent=5.0,
            cart_discount_reason="R" * 200,
        )
        assert len(order.cart_discount_reason) == 200

    def test_cart_discount_reason_over_limit_rejected(self):
        """201-char cart_discount_reason is rejected."""
        with pytest.raises(ValidationError):
            OrderCreate(
                customer_id="cust-123",
                items=[
                    OrderItemCreate(
                        item_type="FRAME", product_id="prod-1", unit_price=500.0
                    )
                ],
                cart_discount_reason="R" * 201,
            )

    def test_none_text_fields_accepted(self):
        """All text fields accept None (optional)."""
        item = OrderItemCreate(
            item_type="FRAME",
            product_id="test-prod",
            unit_price=500.0,
            product_name=None,
            discount_reason=None,
            item_note=None,
        )
        assert item.product_name is None
        assert item.discount_reason is None
        assert item.item_note is None


# ---------------------------------------------------------------------------
# POS-10: item_note / order_type persisted on create
# ---------------------------------------------------------------------------


class TestPOS10FieldPersistence:
    """item_note and order_type survive schema round-trip (not silently dropped)."""

    def test_item_note_field_exists_on_schema(self):
        """OrderItemCreate accepts and retains item_note."""
        item = OrderItemCreate(
            item_type="LENS",
            product_id="lens-001",
            unit_price=1200.0,
            item_note="Please tint 40% gray",
        )
        assert item.item_note == "Please tint 40% gray"

    def test_item_note_empty_string_treated_as_provided(self):
        """Empty string is a valid (but unlikely) item_note."""
        item = OrderItemCreate(
            item_type="LENS",
            product_id="lens-001",
            unit_price=1200.0,
            item_note="",
        )
        # Pydantic stores the empty string; the order_create handler converts
        # '' to None via `or None` before persisting.
        assert item.item_note == ""

    def test_order_type_field_exists_on_schema(self):
        """OrderCreate accepts and retains order_type."""
        order = OrderCreate(
            customer_id="cust-abc",
            items=[
                OrderItemCreate(
                    item_type="FRAME", product_id="prod-1", unit_price=500.0
                )
            ],
            order_type="quick_sale",
        )
        assert order.order_type == "quick_sale"

    def test_order_type_within_max_length(self):
        """order_type max 50 chars is enforced."""
        # 50 chars -- accepted
        order = OrderCreate(
            customer_id="cust-abc",
            items=[
                OrderItemCreate(
                    item_type="FRAME", product_id="prod-1", unit_price=500.0
                )
            ],
            order_type="X" * 50,
        )
        assert len(order.order_type) == 50

    def test_order_type_over_limit_rejected(self):
        """order_type > 50 chars is rejected."""
        with pytest.raises(ValidationError):
            OrderCreate(
                customer_id="cust-abc",
                items=[
                    OrderItemCreate(
                        item_type="FRAME", product_id="prod-1", unit_price=500.0
                    )
                ],
                order_type="X" * 51,
            )

    def test_order_type_optional(self):
        """order_type defaults to None (backward-compatible)."""
        order = OrderCreate(
            customer_id="cust-abc",
            items=[
                OrderItemCreate(
                    item_type="FRAME", product_id="prod-1", unit_price=500.0
                )
            ],
        )
        assert order.order_type is None


# ---------------------------------------------------------------------------
# POS-11: cancel_order contract (backend reads reason as Query param)
# ---------------------------------------------------------------------------


class TestPOS11CancelContract:
    """Backend cancel_order uses reason: str = Query(...).
    The frontend must send it as a query param, not a JSON body."""

    def test_cancel_order_reason_is_query_param(self):
        """Verify that the cancel_order handler reads reason as a Query param
        (not from a Pydantic request-body model). This ensures the frontend fix
        (sending null body + ?reason=... params) matches the backend contract."""
        from api.routers.orders import cancel_order

        sig = inspect.signature(cancel_order)
        # The signature must have a `reason` parameter ...
        assert "reason" in sig.parameters, "cancel_order must have a `reason` param"
        param = sig.parameters["reason"]
        # FastAPI Query() returns a fastapi.params.Query instance. Detect by
        # class name to stay robust across FastAPI / Pydantic versions.
        default = param.default
        assert type(default).__name__ == "Query", (
            f"cancel_order.reason must be a fastapi.Query, got: {type(default).__name__!r}"
        )

    def test_cancel_reason_min_length_enforced(self):
        """The Query has min_length=10 so a 9-char reason is rejected at the
        route level. Confirm the constraint is present on the parameter."""
        from api.routers.orders import cancel_order

        sig = inspect.signature(cancel_order)
        param = sig.parameters["reason"]
        q = param.default
        assert type(q).__name__ == "Query", "reason param must be a Query"
        # FastAPI / Pydantic v1 stores min_length directly on FieldInfo;
        # Pydantic v2 (FastAPI >= 0.100) moves constraints into `metadata`.
        # Check both locations so the test stays version-agnostic.
        min_len_direct = getattr(q, "min_length", None)
        min_len_meta = None
        for constraint in getattr(q, "metadata", []):
            v = getattr(constraint, "min_length", None)
            if v is not None:
                min_len_meta = v
                break
        effective_min = min_len_direct if min_len_direct is not None else min_len_meta
        assert effective_min == 10, (
            f"Expected min_length=10 on reason Query; direct={min_len_direct}, meta={min_len_meta}"
        )


# ---------------------------------------------------------------------------
# POS-12: status_history seeded at order create + existing update_status path
# ---------------------------------------------------------------------------


class TestPOS12StatusHistory:
    """status_history is initialized with the DRAFT entry on order create."""

    def test_order_create_seeds_a_draft_status_history_entry(
        self, client, auth_headers, order_create_env
    ):
        """BEHAVIOURAL: POST an order through the real handler and assert the
        STORED document carries a status_history whose first entry is DRAFT.

        Replaces a source-text check that merely grepped the 4,600-line
        orders.py for the strings "status_history" and "DRAFT" -- both of which
        appear dozens of times in unrelated code, so the old assertion could
        never have failed even if the seeding were deleted outright.
        """
        resp = client.post(
            "/api/v1/orders",
            headers=auth_headers,
            json={
                "customer_id": "cust-hist",
                "items": [
                    {
                        "item_type": "FRAME",
                        "product_id": "p-hist",
                        "product_name": "Frame",
                        "sku": "SKU-HIST",
                        "quantity": 1,
                        "unit_price": 1000.0,
                        "category": "FRAME",
                    }
                ],
            },
        )
        assert resp.status_code == 201, resp.text

        stored = order_create_env["orders"].docs
        assert len(stored) == 1, f"expected exactly one stored order, got {len(stored)}"
        history = stored[0].get("status_history")
        assert isinstance(history, list) and history, (
            "create_order must seed status_history on the stored order document; "
            f"got {history!r}"
        )
        assert history[0].get("status") == "DRAFT", (
            f"first status_history entry must be DRAFT, got {history[0]!r}"
        )
        assert history[0].get("timestamp"), "seeded entry must carry a timestamp"

    def test_order_to_frontend_maps_status_history(self):
        """order_to_frontend converts status_history (snake) to statusHistory (camel)."""
        from api.routers.orders import order_to_frontend

        raw = {
            "order_id": "ord-001",
            "status": "DRAFT",
            "grand_total": 500.0,
            "status_history": [
                {
                    "status": "DRAFT",
                    "timestamp": "2026-06-01T10:00:00",
                    "changed_by": "user-1",
                },
                {
                    "status": "CONFIRMED",
                    "timestamp": "2026-06-01T10:05:00",
                    "changed_by": "user-1",
                },
            ],
        }
        result = order_to_frontend(raw)

        assert "statusHistory" in result, "order_to_frontend must map status_history -> statusHistory"
        hist = result["statusHistory"]
        assert len(hist) == 2
        assert hist[0]["status"] == "DRAFT"
        assert hist[0]["changedBy"] == "user-1"
        assert hist[1]["status"] == "CONFIRMED"

    def test_update_status_appends_an_entry_per_transition(self):
        """BEHAVIOURAL: run the real OrderRepository.update_status against a
        strict in-memory collection and assert the stored document.

        Replaces a source-text check for the substrings "status_history_entry",
        "timestamp", "changed_by" and "$push" -- which proved only that those
        characters occur somewhere in the function, not that a transition
        actually appends a well-formed entry (nor that a second transition
        appends rather than overwrites).
        """
        from database.repositories.order_repository import OrderRepository
        from strict_fakes import StrictCollection

        coll = StrictCollection("orders", [{"order_id": "ord-1", "status": "DRAFT"}])
        repo = OrderRepository(coll)

        assert repo.update_status("ord-1", "CONFIRMED", "user-7") is True
        doc = coll.docs[0]
        assert doc["status"] == "CONFIRMED"
        history = doc.get("status_history")
        assert isinstance(history, list) and len(history) == 1, history
        assert history[0]["status"] == "CONFIRMED"
        assert history[0]["changed_by"] == "user-7"
        assert history[0]["timestamp"], "entry must carry a timestamp"

        # A second transition APPENDS -- it must not replace the first entry
        # (a `$set` instead of `$push` would still contain every substring the
        # old source-text assertion looked for).
        assert repo.update_status("ord-1", "DELIVERED", "user-8") is True
        history = coll.docs[0]["status_history"]
        assert [h["status"] for h in history] == ["CONFIRMED", "DELIVERED"], history
        assert history[1]["changed_by"] == "user-8"

    def test_update_status_records_system_when_no_user_given(self):
        """An unattributed transition is stamped 'system', never dropped."""
        from database.repositories.order_repository import OrderRepository
        from strict_fakes import StrictCollection

        coll = StrictCollection("orders", [{"order_id": "ord-2", "status": "DRAFT"}])
        OrderRepository(coll).update_status("ord-2", "CANCELLED")
        assert coll.docs[0]["status_history"][0]["changed_by"] == "system"


# ---------------------------------------------------------------------------
# POS-14: Idempotency-Key extended to add_payment / create_return /
#          create_expense
# ---------------------------------------------------------------------------


class TestPOS14IdempotencyExtension:
    """Verify that the three newly-guarded endpoints accept an Idempotency-Key
    header parameter. This is a signature-level check -- no DB needed."""

    def _get_header_params(self, fn) -> list:
        """Return names of Header parameters on the given async handler.

        FastAPI Header() returns a ``fastapi.params.Header`` instance.
        We detect it by type name (robust across FastAPI versions).
        """
        sig = inspect.signature(fn)
        result = []
        for name, param in sig.parameters.items():
            d = param.default
            if type(d).__name__ == "Header":
                result.append(name)
        return result

    def test_add_payment_has_idempotency_key(self):
        """add_payment accepts an Idempotency-Key header."""
        from api.routers.orders import add_payment

        header_params = self._get_header_params(add_payment)
        assert "idempotency_key" in header_params, (
            "add_payment must have an idempotency_key Header parameter"
        )

    def test_create_return_has_idempotency_key(self):
        """create_return accepts an Idempotency-Key header."""
        from api.routers.returns import create_return

        header_params = self._get_header_params(create_return)
        assert "idempotency_key" in header_params, (
            "create_return must have an idempotency_key Header parameter"
        )

    def test_create_expense_has_idempotency_key(self):
        """create_expense accepts an Idempotency-Key header."""
        from api.routers.expenses import create_expense

        header_params = self._get_header_params(create_expense)
        assert "idempotency_key" in header_params, (
            "create_expense must have an idempotency_key Header parameter"
        )

    def test_create_order_still_has_idempotency_key(self):
        """create_order's original Idempotency-Key header is still present
        (regression guard — must not have been accidentally removed)."""
        from api.routers.orders import create_order

        header_params = self._get_header_params(create_order)
        assert "idempotency_key" in header_params, (
            "create_order must retain its idempotency_key Header parameter"
        )


# ---------------------------------------------------------------------------
# POS-14 (behavioural): a replayed request must not duplicate the money row
# ---------------------------------------------------------------------------
# These replace three source-text assertions that only checked the literal
# "idempotency_key" appeared somewhere in the handler's source. That cannot
# distinguish "the line exists" from "the line RUNS" -- and it is exactly the
# assertion that silently pointed at the WRONG function when inspect.getsource
# desynchronised mid-suite (see tests/source_guard.py). Each test below drives
# the real handler twice through the TestClient and asserts the observable
# outcome: ONE stored row, the key stamped on it, and the replay returning the
# original id.


class TestPOS14PaymentIdempotencyBehaviour:
    """POST the same payment twice with one Idempotency-Key -> one payment row."""

    ORDER_ID = "ord-idem-pay"

    def _post(self, client, auth_headers, key=None, amount=400.0):
        headers = dict(auth_headers)
        if key is not None:
            headers["Idempotency-Key"] = key
        return client.post(
            f"/api/v1/orders/{self.ORDER_ID}/payments",
            headers=headers,
            json={"method": "CASH", "amount": amount},
        )

    def test_duplicate_post_records_exactly_one_payment(
        self, client, auth_headers, payment_order
    ):
        first = self._post(client, auth_headers, key="pay-key-abc")
        assert first.status_code == 200, first.text
        second = self._post(client, auth_headers, key="pay-key-abc")
        assert second.status_code == 200, second.text

        payments = payment_order["order"].get("payments") or []
        assert len(payments) == 1, (
            f"a replayed Idempotency-Key must not record a second tender; "
            f"stored {len(payments)} payment rows: {payments!r}"
        )
        assert second.json()["payment_id"] == first.json()["payment_id"]
        assert second.json().get("_idempotent_replay") is True
        # The money must not move twice either.
        assert payment_order["order"]["balance_due"] == 600.0

    def test_key_is_stamped_on_the_stored_payment_row(
        self, client, auth_headers, payment_order
    ):
        """Without the stamp the guard above could never find the prior row,
        so the key MUST be persisted on the tender itself."""
        assert self._post(client, auth_headers, key="pay-key-stamp").status_code == 200
        row = payment_order["order"]["payments"][0]
        assert row.get("idempotency_key") == "pay-key-stamp", row

    def test_distinct_keys_still_record_two_payments(
        self, client, auth_headers, payment_order
    ):
        """The guard must key on the SUPPLIED value -- not dedupe blindly."""
        assert self._post(client, auth_headers, key="k1", amount=100.0).status_code == 200
        assert self._post(client, auth_headers, key="k2", amount=100.0).status_code == 200
        assert len(payment_order["order"]["payments"]) == 2

    def test_no_key_means_no_deduplication(self, client, auth_headers, payment_order):
        """Two genuine part-payments with no header must both be recorded."""
        assert self._post(client, auth_headers, amount=100.0).status_code == 200
        assert self._post(client, auth_headers, amount=100.0).status_code == 200
        assert len(payment_order["order"]["payments"]) == 2
        assert all(
            p.get("idempotency_key") is None
            for p in payment_order["order"]["payments"]
        )


class TestPOS14ExpenseIdempotencyBehaviour:
    """POST the same expense twice with one Idempotency-Key -> one expense row."""

    def _post(self, client, auth_headers, key=None, amount=250.0):
        headers = dict(auth_headers)
        if key is not None:
            headers["Idempotency-Key"] = key
        return client.post(
            "/api/v1/expenses",
            headers=headers,
            json={
                "category": "utilities",
                "amount": amount,
                "description": "Electricity bill",
                "expense_date": "2026-06-10",
                "payment_mode": "CASH",
            },
        )

    def test_duplicate_post_records_exactly_one_expense(
        self, client, auth_headers, expense_env
    ):
        first = self._post(client, auth_headers, key="exp-key-abc")
        assert first.status_code == 201, first.text
        second = self._post(client, auth_headers, key="exp-key-abc")
        assert second.status_code in (200, 201), second.text

        rows = expense_env["expenses"].docs
        assert len(rows) == 1, (
            f"a replayed Idempotency-Key must not file a second claim; "
            f"stored {len(rows)} expense rows"
        )
        assert second.json()["expense_id"] == first.json()["expense_id"]
        assert second.json().get("_idempotent_replay") is True

    def test_key_is_stamped_on_the_stored_expense(
        self, client, auth_headers, expense_env
    ):
        assert self._post(client, auth_headers, key="exp-key-stamp").status_code == 201
        assert expense_env["expenses"].docs[0].get("idempotency_key") == "exp-key-stamp"

    def test_distinct_keys_still_record_two_expenses(
        self, client, auth_headers, expense_env
    ):
        assert self._post(client, auth_headers, key="e1").status_code == 201
        assert self._post(client, auth_headers, key="e2").status_code == 201
        assert len(expense_env["expenses"].docs) == 2


class TestPOS14ReturnIdempotencyBehaviour:
    """A replayed return must not issue a second refund."""

    def test_replay_returns_the_original_and_writes_nothing(
        self, client, auth_headers, returns_collection
    ):
        """BEHAVIOURAL: seed a return carrying the key, then POST a create with
        the same Idempotency-Key. The real guard at the top of create_return
        must short-circuit: original id back, no second row written.

        This is the half of the contract that actually prevents a double
        refund. (The stamping half is covered by
        ``test_return_doc_persists_idempotency_key`` below, which remains
        textual -- see the comment there.)
        """
        returns_collection.insert_one(
            {
                "return_id": "RET-SEED-1",
                "idempotency_key": "ret-key-abc",
                "return_type": "RETURN",
                "net_refund": 750.0,
                "refund_amount": 750.0,
            }
        )
        resp = client.post(
            "/api/v1/returns",
            headers={**auth_headers, "Idempotency-Key": "ret-key-abc"},
            json={
                # A deliberately unresolvable order: if the guard did NOT
                # short-circuit, this request could not possibly succeed, so a
                # 201 carrying the seeded return_id can only come from the
                # idempotent replay path.
                "order_id": "ord-does-not-exist",
                "return_type": "RETURN",
                "items": [
                    {"product_id": "p1", "return_qty": 1, "unit_price": 500.0}
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload.get("_idempotent_replay") is True, payload
        assert payload["return_id"] == "RET-SEED-1"
        assert payload["net_refund"] == 750.0
        assert len(returns_collection.docs) == 1, (
            "the replay must not write a second return row"
        )

    def test_return_doc_persists_idempotency_key(self):
        """STILL TEXTUAL -- justified, and now guarded.

        The complementary half of the contract (create_return STAMPS the key on
        the row it writes) is not reachable DB-free: the write happens ~500
        lines into a path that resolves the order, recomputes per-line GST,
        restocks units and mints a credit note, and stubbing all of that would
        turn the test into mock theatre that proves less than this does. The
        stamp IS covered behaviourally on CI, where the returns E2E tests run
        against a real MongoDB.

        Meanwhile ``verified_source`` makes this check fail LOUDLY rather than
        silently degrade if getsource ever hands back another function's body.
        """
        from api.routers.returns import create_return
        from source_guard import verified_source

        src = verified_source(create_return, min_lines=50)
        assert '"idempotency_key"' in src or "'idempotency_key'" in src, (
            "create_return must persist idempotency_key on the return doc"
        )
