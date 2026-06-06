"""Clinical-ROLE gate on prescription READS (require_rx_read).

Defense-in-depth on top of the BUG-088 store-scope guard: prescriptions carry
medical data (SPH/CYL/AXIS, IPD, lens Rx) + patient PII, so roles with no
clinical/fulfilment need (CASHIER payment-only, ACCOUNTANT, CATALOG_MANAGER,
INVENTORY_HQ) must not read them -- even within their own store. Clinical, POS
order-building, workshop, and management roles still may.
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers.prescriptions import require_rx_read  # noqa: E402

ALLOWED = [
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "OPTOMETRIST",
    "SALES_CASHIER",
    "SALES_STAFF",
    "WORKSHOP_STAFF",
]
BLOCKED = ["CASHIER", "ACCOUNTANT", "CATALOG_MANAGER", "INVENTORY_HQ"]


@pytest.mark.parametrize("role", ALLOWED)
def test_allowed_roles_pass(role):
    user = {"user_id": "u", "roles": [role]}
    assert require_rx_read(user) is user


@pytest.mark.parametrize("role", BLOCKED)
def test_blocked_roles_get_403(role):
    with pytest.raises(HTTPException) as ei:
        require_rx_read({"user_id": "u", "roles": [role]})
    assert ei.value.status_code == 403


def test_empty_or_missing_roles_403():
    for user in ({"roles": []}, {}, {"roles": None}):
        with pytest.raises(HTTPException) as ei:
            require_rx_read(user)
        assert ei.value.status_code == 403


def test_mixed_roles_pass_if_any_clinical():
    # Holding a blocked role does not strip access when the user ALSO holds an
    # allowed clinical role.
    user = {"user_id": "u", "roles": ["CASHIER", "OPTOMETRIST"]}
    assert require_rx_read(user) is user
