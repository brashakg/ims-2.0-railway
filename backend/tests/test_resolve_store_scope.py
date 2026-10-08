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


# Owner ruling 2026-10-07 (R3): a login that is not ADMIN / SUPERADMIN and has
# no shop must NOT read every shop. It used to: no active store -> None -> no
# filter -> all stores, on every Purchase tab and finance read that asks this
# rule. Fail closed, with a plain message.
NO_SHOP = "Your login has no shop assigned - ask an admin to assign one."


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT", "CATALOG_MANAGER"])
@pytest.mark.parametrize("token", [{}, {"store_ids": [], "active_store_id": None}])
def test_r3_a_non_admin_with_no_shop_reads_no_shop(role, token):
    with pytest.raises(HTTPException) as ei:
        resolve_store_scope(None, {"roles": [role], **token})
    assert ei.value.status_code == 403
    assert ei.value.detail == NO_SHOP


def test_r3_a_non_admin_with_no_shop_cannot_name_one_either():
    with pytest.raises(HTTPException) as ei:
        resolve_store_scope("BV-PUN-01", {"roles": ["ACCOUNTANT"], "store_ids": []})
    assert ei.value.status_code == 403


@pytest.mark.parametrize("user", [{"roles": ["ADMIN"]}, {"roles": ["SUPERADMIN"]}])
def test_r3_admins_with_no_shop_still_read_every_shop(user):
    assert resolve_store_scope(None, user) is None


@pytest.mark.parametrize("name,kw", [
    ("get_sell_through_analysis", {"days": 30}),
    ("get_overstock_analysis", {"overstocking_threshold": 3.0, "days": 30}),
])
def test_r3_a_catch_all_handler_passes_the_refusal_through(monkeypatch, name, kw):
    """These two asked the rule inside `except Exception -> 500`: a login with
    no shop (or one naming another shop) got a 500, not the plain 403."""
    import asyncio
    from unittest.mock import MagicMock
    from api.routers.inventory import analytics

    monkeypatch.setattr(analytics, "_get_db", lambda: MagicMock())
    with pytest.raises(HTTPException) as ei:
        asyncio.run(getattr(analytics, name)(store_id=None, current_user={"roles": ["STORE_MANAGER"]}, **kw))
    assert (ei.value.status_code, ei.value.detail) == (403, NO_SHOP)
