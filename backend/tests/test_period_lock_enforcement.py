"""Test that period locks are enforced across orders, returns, vendor-bills, and vendor-payments."""

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from fastapi import HTTPException


@pytest.fixture
def mock_db():
    """Mock database with period_locks collection."""
    db = Mock()
    db.get_collection = Mock(return_value=Mock())
    return db


@pytest.fixture
def locked_month():
    """Return a date in a locked month (month=6, year=2026)."""
    return date(2026, 6, 15)


@pytest.fixture
def unlocked_month():
    """Return a date in an unlocked month (month=5, year=2026)."""
    return date(2026, 5, 15)


def test_check_period_locked_raises_423_when_locked(mock_db, locked_month):
    """check_period_locked should raise 423 when posting_date is in a locked month."""
    from api.routers.finance import check_period_locked
    
    # Mock the period_locks collection to return a lock record for June 2026
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = {"month": 6, "year": 2026, "_id": "xyz"}
    mock_db.get_collection.return_value = period_locks_coll
    
    with pytest.raises(HTTPException) as exc_info:
        check_period_locked(mock_db, locked_month)
    
    assert exc_info.value.status_code == 423
    assert "locked" in exc_info.value.detail.lower()
    assert "06/2026" in exc_info.value.detail


def test_check_period_locked_passes_when_unlocked(mock_db, unlocked_month):
    """check_period_locked should not raise when posting_date is in an unlocked month."""
    from api.routers.finance import check_period_locked
    
    # Mock the period_locks collection to return None (no lock)
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = None
    mock_db.get_collection.return_value = period_locks_coll
    
    # Should not raise
    check_period_locked(mock_db, unlocked_month)


def test_check_period_locked_with_iso_string(mock_db, locked_month):
    """check_period_locked should accept ISO date strings."""
    from api.routers.finance import check_period_locked
    
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = {"month": 6, "year": 2026, "_id": "xyz"}
    mock_db.get_collection.return_value = period_locks_coll
    
    iso_string = locked_month.isoformat()
    with pytest.raises(HTTPException) as exc_info:
        check_period_locked(mock_db, iso_string)
    
    assert exc_info.value.status_code == 423


def test_check_period_locked_fails_soft_on_db_error(mock_db, locked_month):
    """check_period_locked should not raise when db lookup fails (fail-soft)."""
    from api.routers.finance import check_period_locked
    
    # Mock the period_locks collection to raise an exception
    period_locks_coll = Mock()
    period_locks_coll.find_one.side_effect = Exception("DB error")
    mock_db.get_collection.return_value = period_locks_coll
    
    # Should not raise (fail-soft)
    check_period_locked(mock_db, locked_month)


def test_check_period_locked_noop_when_db_none():
    """check_period_locked should not raise when db is None."""
    from api.routers.finance import check_period_locked
    
    # Should not raise
    check_period_locked(None, date(2026, 6, 15))


def test_orders_create_order_raises_423_in_locked_period(mock_db, locked_month):
    """create_order should raise 423 when today is in a locked month."""
    from api.routers.orders import create_order, OrderCreate, OrderItemCreate
    
    # Mock the DB and period_locks
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = {"month": locked_month.month, "year": locked_month.year}
    mock_db.get_collection.return_value = period_locks_coll
    
    # period_locks.find_one returns a lock doc for ANY query, so check_period_locked
    # (which queries by date.today()) always sees the period as locked -> no need to
    # patch the C-level datetime.date.today (which is unpatchable anyway).
    with patch("api.routers.orders._get_db", return_value=mock_db):
        with patch("api.routers.orders.get_order_repository", return_value=Mock()):
            with patch("api.routers.orders.get_customer_repository", return_value=Mock()):
                with patch("api.routers.orders.get_product_repository", return_value=Mock()):
                    order = OrderCreate(
                        customer_id="cust-1",
                        items=[
                            OrderItemCreate(
                                item_type="PRODUCT",
                                product_id="prod-1",
                                product_name="Test Product",
                                quantity=1,
                                unit_price=100.0,
                            )
                        ],
                    )
                    current_user = {
                        "user_id": "user-1",
                        "full_name": "Test User",
                        "roles": ["SALES_STAFF"],
                        "active_store_id": "store-1",
                    }
                    import asyncio

                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(create_order(order, current_user))
                    assert exc_info.value.status_code == 423


def test_returns_create_return_raises_423_in_locked_period(mock_db, locked_month):
    """create_return should raise 423 when today is in a locked month."""
    from api.routers.returns import create_return, ReturnCreate
    
    # Mock the DB and period_locks
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = {"month": locked_month.month, "year": locked_month.year}
    mock_db.get_collection.return_value = period_locks_coll
    
    with patch("api.routers.returns._get_db", return_value=mock_db):
        return_body = ReturnCreate(order_id="ord-1", items=[])
        current_user = {
            "user_id": "user-1",
            "roles": ["SALES_STAFF"],
            "active_store_id": "store-1",
        }
        import asyncio

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(create_return(return_body, current_user))
        assert exc_info.value.status_code == 423


def test_vendor_bill_create_raises_423_in_locked_period(mock_db):
    """create_vendor_bill should raise 423 when bill_date is in a locked month."""
    from api.routers.vendors import create_vendor_bill, VendorBillCreate
    
    bill_date_str = "2026-06-15"
    bill_date = date.fromisoformat(bill_date_str)
    
    # Mock the DB and period_locks
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = {"month": 6, "year": 2026}
    mock_db.get_collection.return_value = period_locks_coll
    
    with patch("api.routers.vendors._get_db", return_value=mock_db):
        with patch("api.routers.vendors.get_vendor_repository", return_value=Mock()):
            bill = VendorBillCreate(
                bill_number="INV-001",
                bill_date=bill_date_str,
                taxable_amount=1000.0,
                tax_amount=180.0,
                total_amount=1180.0
            )
            current_user = {
                "user_id": "user-1",
                "roles": ["ACCOUNTANT"],
                "active_store_id": "store-1"
            }
            
            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.run(create_vendor_bill("vendor-1", bill, current_user))
            
            assert exc_info.value.status_code == 423


def test_vendor_payment_create_raises_423_in_locked_period(mock_db):
    """create_vendor_payment should raise 423 when payment_date is in a locked month."""
    from api.routers.vendors import create_vendor_payment, VendorPaymentCreate
    
    payment_date_str = "2026-06-15"
    
    # Mock the DB and period_locks
    period_locks_coll = Mock()
    period_locks_coll.find_one.return_value = {"month": 6, "year": 2026}
    mock_db.get_collection.return_value = period_locks_coll
    
    with patch("api.routers.vendors._get_db", return_value=mock_db):
        with patch("api.routers.vendors.get_vendor_repository", return_value=Mock()):
            payment = VendorPaymentCreate(
                amount=1000.0,
                payment_date=payment_date_str,
                mode="BANK"
            )
            current_user = {
                "user_id": "user-1",
                "roles": ["ACCOUNTANT"],
                "active_store_id": "store-1"
            }
            
            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.run(create_vendor_payment("vendor-1", payment, current_user))
            
            assert exc_info.value.status_code == 423
