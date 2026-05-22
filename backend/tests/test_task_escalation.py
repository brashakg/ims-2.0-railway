"""
Task escalation chain tests (Tasks/SOP Phase 2)
===============================================
Pure tests of services.task_escalation -- the role-ladder resolver that
decides WHO a breached task escalates to. No DB; a fake find_by_role mirrors
UserRepository.find_by_role semantics (role membership + optional store_ids
scoping).
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.services.task_escalation import (  # noqa: E402
    next_rung_role,
    resolve_escalation_target,
)


def make_finder(users):
    """Build a find_by_role(role, store_id) over an in-memory user list.
    Records calls on `.calls` for scope assertions."""
    calls = []

    def find(role, store_id):
        calls.append((role, store_id))
        out = []
        for u in users:
            roles = [str(r).upper() for r in u.get("roles", [])]
            if role.upper() not in roles:
                continue
            if store_id is not None and store_id not in u.get("store_ids", []):
                continue
            out.append(u)
        return out

    find.calls = calls
    return find


# --- next_rung_role ---------------------------------------------------------


def test_worker_escalates_to_store_manager():
    assert next_rung_role(["SALES_STAFF"]) == "STORE_MANAGER"
    assert next_rung_role(["CASHIER"]) == "STORE_MANAGER"
    assert next_rung_role(["OPTOMETRIST"]) == "STORE_MANAGER"
    assert next_rung_role([]) == "STORE_MANAGER"
    assert next_rung_role(None) == "STORE_MANAGER"


def test_store_manager_escalates_to_area_manager():
    assert next_rung_role(["STORE_MANAGER"]) == "AREA_MANAGER"


def test_area_manager_escalates_to_admin():
    assert next_rung_role(["AREA_MANAGER"]) == "ADMIN"


def test_admin_escalates_to_superadmin():
    assert next_rung_role(["ADMIN"]) == "SUPERADMIN"


def test_superadmin_has_no_higher_rung():
    assert next_rung_role(["SUPERADMIN"]) is None


def test_highest_role_wins_for_mixed_roles():
    assert next_rung_role(["SALES_STAFF", "STORE_MANAGER"]) == "AREA_MANAGER"
    assert next_rung_role(["store_manager"]) == "AREA_MANAGER"  # case-insensitive


# --- resolve_escalation_target ----------------------------------------------

USERS = [
    {"user_id": "sm1", "roles": ["STORE_MANAGER"], "store_ids": ["S1"]},
    {"user_id": "am1", "roles": ["AREA_MANAGER"], "store_ids": ["S1", "S2"]},
    {"user_id": "ad1", "roles": ["ADMIN"], "store_ids": []},
]


def test_worker_resolves_to_store_manager():
    finder = make_finder(USERS)
    target = resolve_escalation_target(finder, "S1", {"user_id": "w1", "roles": ["SALES_STAFF"]})
    assert target["user_id"] == "sm1"
    # Store-scoped lookup used the store id.
    assert ("STORE_MANAGER", "S1") in finder.calls


def test_store_manager_resolves_to_area_manager():
    finder = make_finder(USERS)
    target = resolve_escalation_target(finder, "S1", {"user_id": "sm1", "roles": ["STORE_MANAGER"]})
    assert target["user_id"] == "am1"


def test_area_manager_resolves_to_admin_globally():
    finder = make_finder(USERS)
    target = resolve_escalation_target(finder, "S1", {"user_id": "am1", "roles": ["AREA_MANAGER"]})
    assert target["user_id"] == "ad1"
    # ADMIN rung is global -- looked up with store_id None.
    assert ("ADMIN", None) in finder.calls


def test_climbs_past_empty_rung():
    # Store S3 has no STORE_MANAGER and no AREA_MANAGER -> climb to global ADMIN.
    users = [
        {"user_id": "sm1", "roles": ["STORE_MANAGER"], "store_ids": ["S1"]},
        {"user_id": "ad1", "roles": ["ADMIN"], "store_ids": []},
    ]
    finder = make_finder(users)
    target = resolve_escalation_target(finder, "S3", {"user_id": "w9", "roles": ["SALES_STAFF"]})
    assert target["user_id"] == "ad1"


def test_excludes_self_from_candidates():
    # am1 wears both AREA_MANAGER and ADMIN; escalating am1 must skip am1 and
    # pick the other admin.
    users = [
        {"user_id": "am1", "roles": ["AREA_MANAGER", "ADMIN"], "store_ids": ["S1"]},
        {"user_id": "ad2", "roles": ["ADMIN"], "store_ids": []},
    ]
    finder = make_finder(users)
    target = resolve_escalation_target(finder, "S1", {"user_id": "am1", "roles": ["AREA_MANAGER"]})
    assert target["user_id"] == "ad2"


def test_superadmin_assignee_returns_none():
    finder = make_finder(USERS)
    assert resolve_escalation_target(finder, "S1", {"user_id": "su1", "roles": ["SUPERADMIN"]}) is None


def test_no_users_returns_none():
    finder = make_finder([])
    assert resolve_escalation_target(finder, "S1", {"user_id": "w1", "roles": ["SALES_STAFF"]}) is None


def test_finder_exception_is_swallowed():
    def boom(role, store_id):
        raise RuntimeError("db down")

    assert resolve_escalation_target(boom, "S1", {"user_id": "w1", "roles": ["SALES_STAFF"]}) is None
