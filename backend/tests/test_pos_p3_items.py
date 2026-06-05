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
       The initial DRAFT status_history entry is seeded at order_data build
       time. OrderRepository.update_status appends subsequent entries via $push.
       The order_to_frontend mapper already converts status_history to camelCase.

POS-14 Extend Idempotency-Key to payments / returns / expense-create.
       add_payment, create_return, and create_expense all accept the header
       (verified via function signature inspection).
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

    def test_order_create_data_includes_status_history(self):
        """The order_data dict built in create_order seeds a status_history
        list with a DRAFT entry. Verified by inspecting the source."""
        import ast
        import pathlib

        orders_src = pathlib.Path(__file__).parent.parent / "api" / "routers" / "orders.py"
        source = orders_src.read_text(encoding="utf-8")
        # The source must reference 'status_history' in the order_data block.
        assert '"status_history"' in source or "'status_history'" in source, (
            "orders.py order_data must include a status_history key"
        )
        # And it should include DRAFT as the initial status.
        assert "DRAFT" in source, "orders.py must mention DRAFT status"

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

    def test_update_status_pushes_to_history(self):
        """OrderRepository.update_status builds a status_history_entry dict
        with status/timestamp/changed_by keys."""
        import inspect as _inspect
        from database.repositories.order_repository import OrderRepository

        src = _inspect.getsource(OrderRepository.update_status)
        assert "status_history_entry" in src, (
            "update_status must build a status_history_entry"
        )
        assert '"timestamp"' in src or "'timestamp'" in src, (
            "status_history_entry must include a timestamp key"
        )
        assert '"changed_by"' in src or "'changed_by'" in src, (
            "status_history_entry must include a changed_by key"
        )
        assert "$push" in src, "update_status must $push to status_history"


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

    def test_payment_data_persists_idempotency_key(self):
        """The add_payment handler stamps idempotency_key on the payment_data
        dict so future lookups can find it. Verify via source inspection."""
        import inspect as _inspect
        from api.routers.orders import add_payment

        src = _inspect.getsource(add_payment)
        assert '"idempotency_key"' in src or "'idempotency_key'" in src, (
            "add_payment must persist idempotency_key on the payment_data dict"
        )

    def test_return_doc_persists_idempotency_key(self):
        """create_return stamps idempotency_key on the return doc."""
        import inspect as _inspect
        from api.routers.returns import create_return

        src = _inspect.getsource(create_return)
        assert '"idempotency_key"' in src or "'idempotency_key'" in src, (
            "create_return must persist idempotency_key on the return doc"
        )

    def test_expense_doc_persists_idempotency_key(self):
        """create_expense stamps idempotency_key on the expense doc."""
        import inspect as _inspect
        from api.routers.expenses import create_expense

        src = _inspect.getsource(create_expense)
        assert '"idempotency_key"' in src or "'idempotency_key'" in src, (
            "create_expense must persist idempotency_key on the expense doc"
        )
