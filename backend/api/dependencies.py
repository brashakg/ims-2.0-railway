"""
IMS 2.0 - API Dependencies
===========================
Dependency injection for repositories and services
"""

import os
import sys
import logging

logger = logging.getLogger(__name__)

# Add parent directory to path for database imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from database.connection import get_seeded_db
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )
    from database.repositories.order_repository import OrderRepository
    from database.repositories.user_repository import UserRepository
    from database.repositories.store_repository import StoreRepository
    from database.repositories.prescription_repository import PrescriptionRepository
    from database.repositories.task_repository import TaskRepository
    from database.repositories.workshop_repository import WorkshopJobRepository
    from database.repositories.handoff_repository import HandoffRepository
    from database.repositories.expense_repository import (
        ExpenseRepository,
        AdvanceRepository,
    )
    from database.repositories.vendor_repository import (
        VendorRepository,
        PurchaseOrderRepository,
        GRNRepository,
    )
    from database.repositories.hr_repository import (
        AttendanceRepository,
        LeaveRepository,
        PayrollRepository,
    )
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.clinical_repository import (
        EyeTestQueueRepository,
        EyeTestRepository,
    )
    from database.repositories.walkout_repository import WalkoutRepository
    from database.repositories.walkin_counter_repository import WalkInCounterRepository
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltySettingsRepository,
        LoyaltyTransactionRepository,
    )
    from database.repositories.vendor_portal_token_repository import (
        VendorPortalTokenRepository,
    )

    DATABASE_AVAILABLE = True
except ImportError as e:
    logger.warning("Database import error: %s", e)
    DATABASE_AVAILABLE = False


def get_db():
    """Get database connection (with seeded fallback)"""
    if DATABASE_AVAILABLE:
        return get_seeded_db()
    return None


# Owner ruling 2026-10-12 (R4): a login that is not ADMIN / SUPERADMIN and has
# NO shop assigned sees NO shop until an admin assigns one -- never every shop
# ("Control over Convenience"). resolve_store_scope hands such a login this
# reserved id instead of None: every caller filters with
# ``if scope: flt["store_id"] = scope`` and None there means EVERY shop, while
# this id is truthy and no store document ever carries it, so the same line
# matches nothing -- an empty list, never a crash and never all shops.
NO_SHOP_SCOPE = "__no_shop_assigned__"
NO_SHOP_DETAIL = "No shop is assigned to this login. Ask an admin to assign one."


def is_no_shop_scope(scope) -> bool:
    """True when ``scope`` is the reserved no-shop scope (R4) -- for a caller
    that must refuse to WRITE money or stock into it rather than list nothing."""
    return scope == NO_SHOP_SCOPE


def validate_store_access(store_id: str, current_user: dict) -> str:
    """
    Validate that the current user has access to the requested store.
    Returns the validated store_id (or user's active_store_id if none provided).
    Raises HTTPException(403) if user doesn't have access -- worded
    NO_SHOP_DETAIL when the login holds no shop at all (R4).
    """
    from fastapi import HTTPException

    user_roles = current_user.get("roles", [])
    is_admin = any(r in user_roles for r in ["SUPERADMIN", "ADMIN"])

    # If no store_id provided, use user's active store
    if not store_id:
        return current_user.get("active_store_id")

    # Admins can access any store
    if is_admin:
        return store_id

    # Store-level roles (incl. AREA_MANAGER) may access any store in their
    # store_ids OR their current active store. active_store_id is set at login
    # to one of the caller's own stores, so honouring it is correct -- it is
    # never the cross-store IDOR this guard exists to stop. (Without this, a
    # store-scoped token whose active store isn't duplicated into store_ids
    # would be 403'd from its OWN store.)
    user_store_ids = set(current_user.get("store_ids") or [])
    active = current_user.get("active_store_id")
    if active:
        user_store_ids.add(active)
    if not any(isinstance(s, str) and s for s in user_store_ids):
        # R4: a login with no shop holds none to ask for -- say so plainly.
        raise HTTPException(status_code=403, detail=NO_SHOP_DETAIL)
    if store_id not in user_store_ids:
        raise HTTPException(status_code=403, detail=f"No access to store {store_id}")
    return store_id


def user_store_scope(current_user: dict):
    """Resolve the caller's store reach for per-OBJECT authorization (BUG-088).

    Returns ``(is_cross_store, allowed_store_ids)``:
      - SUPERADMIN / ADMIN are cross-store by design -> ``(True, set())``.
      - Everyone else (AREA_MANAGER + store-level roles) is bounded to the union
        of their token's ``store_ids`` and ``active_store_id``.

    Mirrors validate_store_access's role model, but is for filtering objects we
    already hold (a prescription / customer doc) rather than a request param.
    """
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" in roles or "ADMIN" in roles:
        return True, set()
    stores = set(current_user.get("store_ids") or [])
    active = current_user.get("active_store_id")
    if active:
        stores.add(active)
    return False, stores


def can_access_store_scoped(store_id, current_user: dict) -> bool:
    """True if the caller may read an object stamped with ``store_id``.

    Cross-store roles always may. For store-level roles the object's store must
    be one of theirs. An object with NO store_id is treated as out-of-scope for
    store-level roles -- only full admins may read unattributed records, so a
    legacy/migration doc that never got a store can never leak to store staff.
    """
    is_cross, stores = user_store_scope(current_user)
    if is_cross:
        return True
    return bool(store_id) and store_id in stores


def in_reach_or_404(doc, current_user: dict, detail: str):
    """THE by-id store guard of a purchase-side object (audit F63): a missing
    object and one outside the caller's shops -- another shop's, or a legacy
    one stamped with no shop -- get the SAME 404, so a guessed id never
    confirms that it exists or names its shop (validate_store_access's 403
    'No access to store X' did both). Admins reach every object. Returns
    `doc`."""
    from fastapi import HTTPException

    if not doc or not can_access_store_scoped(doc.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail=detail)
    return doc


def resolve_store_scope(store_id, current_user: dict):
    """Authorise + resolve the store filter for a LIST/aggregation endpoint that
    accepts an optional ?store_id (BUG-062 tail).

    - explicit store_id -> validate_store_access (403 if a store-scoped role asks
      for ANOTHER store; admins/area-managers pass per their reach).
    - omitted -> None (all stores) for SUPERADMIN/ADMIN; the caller's OWN active
      store for every other role (AREA_MANAGER + store-level), so a store-scoped
      role can never read an all-stores or other-store list by omitting the param.
      A token with no active store but its own store_ids (a multi-store
      AREA_MANAGER) resolves to its first own store -- the one login makes
      active (auth.login: ``user_store_ids[0]``) -- so it keeps its own reach
      and never reads every shop.
    - a non-admin with NO shop at all (no active store, no store_ids) ->
      NO_SHOP_SCOPE (owner ruling 2026-10-12, R4): a reserved id that matches
      no document, so its lists come back EMPTY. It used to be None, i.e. every
      shop -- while can_access_store_scoped already gave the same login
      nothing. An explicit store_id from such a login is a 403 (NO_SHOP_DETAIL).

    Callers use the returned value as the store filter: ``s = resolve_store_scope(
    store_id, user); if s: filter["store_id"] = s`` (None => no filter => all;
    only ADMIN / SUPERADMIN ever get None).
    """
    if store_id:
        return validate_store_access(store_id, current_user)
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" in roles or "ADMIN" in roles:
        return None
    active = current_user.get("active_store_id")
    if active:
        return active
    for own in current_user.get("store_ids") or []:
        if isinstance(own, str) and own:
            return own
    return NO_SHOP_SCOPE


def store_in_view(store_id, current_user: dict, *, writing: bool = False):
    """The shop a store-level screen reads (or writes) -- THE validate-or-active
    rule the reports, HR, payroll, tasks, workshop, expenses, inventory and
    clinical routes share, once ~60 hand-written copies of
    ``validate_store_access(store_id, user) or user.get("active_store_id")``.

    - an explicit store_id: validate_store_access (403 outside the caller's
      shops; NO_SHOP_DETAIL for a login with none);
    - none named: the caller's active shop -- an ADMIN / SUPERADMIN's too (his
      topbar shop; None = every shop when he has none). This is where it
      differs from resolve_store_scope, which gives an admin every shop (the
      Purchase / Finance org view with a shop filter);
    - a non-admin with no active shop: its first own shop, and with NO shop at
      all the reserved NO_SHOP_SCOPE (owner ruling 2026-10-12, R4) -- a filter
      no document matches, so its lists come back EMPTY. The copies returned
      None there: every shop's jobs, expenses, tasks and reports.
      ``writing=True`` refuses that login instead (403 NO_SHOP_DETAIL): a
      write never stamps the reserved id onto a record.
    """
    from fastapi import HTTPException

    shop = validate_store_access(store_id, current_user)
    if shop:
        return shop
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" in roles or "ADMIN" in roles:
        return None
    own = resolve_store_scope(None, current_user)
    if writing and is_no_shop_scope(own):
        raise HTTPException(status_code=403, detail=NO_SHOP_DETAIL)
    return own


def no_shop_scope_for(store_id, current_user: dict):
    """R4 for a list whose own shop rule lets a role read past its shops (an
    AREA_MANAGER is HQ on customer returns and stock transfers): the reserved
    NO_SHOP_SCOPE when the caller is a non-admin login with NO shop -- and a
    403 NO_SHOP_DETAIL if it named a shop -- else None, and the route's own
    rule applies to a login that holds a shop."""
    from fastapi import HTTPException

    if not is_no_shop_scope(resolve_store_scope(None, current_user)):
        return None
    if store_id:
        raise HTTPException(status_code=403, detail=NO_SHOP_DETAIL)
    return NO_SHOP_SCOPE


def filter_docs_by_store(docs, current_user: dict, store_key: str = "store_id"):
    """Filter a list of store-stamped docs to those the caller may read.

    Cross-store roles get the list unchanged; store-level roles get only docs
    whose ``store_key`` is one of their stores (unattributed docs dropped).
    """
    is_cross, stores = user_store_scope(current_user)
    if is_cross:
        return list(docs or [])
    return [
        d
        for d in (docs or [])
        if isinstance(d, dict) and d.get(store_key) and d.get(store_key) in stores
    ]


def get_customer_repository():
    """Get CustomerRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return CustomerRepository(db.customers)
    return None


def get_product_repository():
    """Get ProductRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return ProductRepository(db.products)
    return None


def get_stock_repository():
    """Get StockRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return StockRepository(db.stock_units)
    return None


def get_order_repository():
    """Get OrderRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return OrderRepository(db.orders)
    return None


def get_user_repository():
    """Get UserRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return UserRepository(db.users)
    return None


def get_store_repository():
    """Get StoreRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return StoreRepository(db.stores)
    return None


def get_prescription_repository():
    """Get PrescriptionRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return PrescriptionRepository(db.prescriptions)
    return None


def get_task_repository():
    """Get TaskRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return TaskRepository(db.tasks)
    return None


def get_workshop_repository():
    """Get WorkshopJobRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return WorkshopJobRepository(db.get_collection("workshop_jobs"))
    return None


def get_handoff_repository():
    """Get HandoffRepository instance (file-handoff feature)."""
    db = get_db()
    if db is not None and db.is_connected:
        return HandoffRepository(db.get_collection("handoffs"))
    return None


def get_expense_repository():
    """Get ExpenseRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return ExpenseRepository(db.expenses)
    return None


def get_advance_repository():
    """Get AdvanceRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return AdvanceRepository(db.advances)
    return None


def get_vendor_repository():
    """Get VendorRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return VendorRepository(db.vendors)
    return None


def get_purchase_order_repository():
    """Get PurchaseOrderRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return PurchaseOrderRepository(db.purchase_orders)
    return None


def get_grn_repository():
    """Get GRNRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return GRNRepository(db.grns)
    return None


def get_attendance_repository():
    """Get AttendanceRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return AttendanceRepository(db.get_collection("attendance"))
    return None


def get_leave_repository():
    """Get LeaveRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return LeaveRepository(db.get_collection("leaves"))
    return None


def get_payroll_repository():
    """Get PayrollRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return PayrollRepository(db.get_collection("payroll"))
    return None


def get_audit_repository():
    """Get AuditRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return AuditRepository(db.audit_logs)
    return None


def get_eye_test_queue_repository():
    """Get EyeTestQueueRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return EyeTestQueueRepository(db.eye_test_queue)
    return None


def get_eye_test_repository():
    """Get EyeTestRepository instance"""
    db = get_db()
    if db is not None and db.is_connected:
        return EyeTestRepository(db.eye_tests)
    return None


def get_walkout_repository():
    """Get WalkoutRepository instance (Pune Incentive Module i)."""
    db = get_db()
    if db is not None and db.is_connected:
        try:
            return WalkoutRepository(db.get_collection("walkouts"))
        except Exception:
            return WalkoutRepository(db.walkouts)
    return None


def get_walkin_counter_repository():
    """Get WalkInCounterRepository instance (Pune Incentive Module i, P4)."""
    db = get_db()
    if db is not None and db.is_connected:
        try:
            return WalkInCounterRepository(db.get_collection("walk_in_counters"))
        except Exception:
            return WalkInCounterRepository(db.walk_in_counters)
    return None


# ============================================================================
# Loyalty engine repositories
# ============================================================================


def get_loyalty_account_repository():
    """Per-customer loyalty account (one row per customer)."""
    db = get_db()
    if db is not None and db.is_connected:
        try:
            return LoyaltyAccountRepository(db.get_collection("loyalty_accounts"))
        except Exception:
            return LoyaltyAccountRepository(db.loyalty_accounts)
    return None


def get_loyalty_transaction_repository():
    """Immutable EARN/REDEEM/EXPIRE/ADJUST ledger."""
    db = get_db()
    if db is not None and db.is_connected:
        try:
            return LoyaltyTransactionRepository(
                db.get_collection("loyalty_transactions")
            )
        except Exception:
            return LoyaltyTransactionRepository(db.loyalty_transactions)
    return None


def get_loyalty_settings_repository():
    """Single-doc engine config (rates, tiers, caps)."""
    db = get_db()
    if db is not None and db.is_connected:
        try:
            return LoyaltySettingsRepository(db.get_collection("loyalty_settings"))
        except Exception:
            return LoyaltySettingsRepository(db.loyalty_settings)
    return None


def get_vendor_portal_token_repository():
    """Get VendorPortalTokenRepository instance (vendor portal token-auth)."""
    db = get_db()
    if db is not None and db.is_connected:
        try:
            return VendorPortalTokenRepository(
                db.get_collection("vendor_portal_tokens")
            )
        except Exception:
            return VendorPortalTokenRepository(db.vendor_portal_tokens)
    return None
    return None
