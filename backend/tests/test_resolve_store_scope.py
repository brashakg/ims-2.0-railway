"""BUG-062 tail: resolve_store_scope is the shared guard for list/aggregation
endpoints that accept ?store_id (payroll config/registers/JV/ECR, users
list/role/search/summary, vouchers, vendor-returns). A store-scoped role must
never read another store (explicit) or all stores (omitted); HQ keeps all."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.dependencies import resolve_store_scope  # noqa: E402

MGR = {"roles": ["STORE_MANAGER"], "active_store_id": "BV-PUN-01", "store_ids": ["BV-PUN-01"]}
AREA = {"roles": ["AREA_MANAGER"], "active_store_id": "BV-PUN-01", "store_ids": ["BV-PUN-01", "BV-MUM-01"]}
ADMIN = {"roles": ["ADMIN"], "active_store_id": "BV-HQ"}
SUPER = {"roles": ["SUPERADMIN"], "active_store_id": "BV-HQ"}


def test_store_role_cross_store_explicit_is_403():
    with pytest.raises(HTTPException) as ei:
        resolve_store_scope("BV-BOK-01", MGR)
    assert ei.value.status_code == 403


def test_store_role_own_store_explicit_ok():
    assert resolve_store_scope("BV-PUN-01", MGR) == "BV-PUN-01"


def test_store_role_omitted_pins_to_own_store():
    # The silent leak: omitting ?store_id must NOT yield an all-stores list.
    assert resolve_store_scope(None, MGR) == "BV-PUN-01"


def test_admin_omitted_is_all_stores():
    assert resolve_store_scope(None, ADMIN) is None
    assert resolve_store_scope(None, SUPER) is None


def test_admin_explicit_passes_through():
    assert resolve_store_scope("BV-BOK-01", ADMIN) == "BV-BOK-01"


def test_area_manager_in_region_ok_out_of_region_403():
    assert resolve_store_scope("BV-MUM-01", AREA) == "BV-MUM-01"
    with pytest.raises(HTTPException) as ei:
        resolve_store_scope("BV-BOK-01", AREA)
    assert ei.value.status_code == 403


def test_area_manager_omitted_pins_to_own_store():
    # AREA_MANAGER is not HQ-all: omitting scopes to their active store.
    assert resolve_store_scope(None, AREA) == "BV-PUN-01"
