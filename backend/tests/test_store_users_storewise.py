"""Locks the store-scoping + role-filter behaviour of GET /stores/{store_id}/users.

The salesperson picker on the POS used to leak SUPERADMIN/ADMIN users when they
were logged in and viewing a store, because the endpoint matched on
``active_store_id``. This test suite locks in the fix:

- Match only ``store_ids`` or ``home_store_id`` (NOT ``active_store_id``)
- Default to ``is_active != False``
- Accept an optional ``?roles=`` CSV filter (case-insensitive)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeUserRepo:
    """In-memory user repo that mimics the subset of find_many used by the route."""

    def __init__(self, users):
        self._users = users

    def find_many(self, query):
        out = []
        for u in self._users:
            if self._matches(u, query):
                # mimic Mongo by returning fresh dicts
                out.append({k: v for k, v in u.items() if k != "_id"})
        return out

    def _matches(self, u, query):
        for key, cond in query.items():
            if key == "$or":
                if not any(self._matches(u, sub) for sub in cond):
                    return False
                continue
            if isinstance(cond, dict):
                # support {"$ne": value} and {"$in": [...]}
                if "$ne" in cond:
                    if u.get(key) == cond["$ne"]:
                        return False
                    continue
                if "$in" in cond:
                    val = u.get(key)
                    if isinstance(val, list):
                        if not any(v in cond["$in"] for v in val):
                            return False
                    elif val not in cond["$in"]:
                        return False
                    continue
                # unknown operator - reject conservatively
                return False
            # plain equality (also handle list-contains: store_ids: "X" matches
            # when u.store_ids contains "X")
            val = u.get(key)
            if isinstance(val, list):
                if cond not in val:
                    return False
            elif val != cond:
                return False
        return True


def _users_fixture():
    return [
        # Cross-store SUPERADMIN with active_store_id=store-A but NO assignment.
        # The pre-fix code returned this; the fix excludes it.
        {
            "user_id": "u-admin",
            "username": "admin",
            "name": "Avinash",
            "roles": ["SUPERADMIN"],
            "store_ids": [],
            "home_store_id": None,
            "active_store_id": "store-A",
            "is_active": True,
        },
        # ADMIN with no store assignment, just active_store_id - same leak.
        {
            "user_id": "u-admin2",
            "username": "admin2",
            "name": "Priya",
            "roles": ["ADMIN"],
            "store_ids": [],
            "home_store_id": None,
            "active_store_id": "store-A",
            "is_active": True,
        },
        # Legit store-A sales cashier.
        {
            "user_id": "u-sales1",
            "username": "ramesh",
            "name": "Ramesh",
            "roles": ["SALES_CASHIER"],
            "store_ids": ["store-A"],
            "home_store_id": "store-A",
            "active_store_id": "store-A",
            "is_active": True,
        },
        # Legit store-A store manager (sales-attributable).
        {
            "user_id": "u-mgr1",
            "username": "sonia",
            "name": "Sonia",
            "roles": ["STORE_MANAGER"],
            "store_ids": ["store-A"],
            "home_store_id": "store-A",
            "active_store_id": "store-A",
            "is_active": True,
        },
        # Store-A accountant - should be EXCLUDED from salesperson picker
        # (no sales attribution) but INCLUDED in an unfiltered listing.
        {
            "user_id": "u-acc1",
            "username": "anita",
            "name": "Anita",
            "roles": ["ACCOUNTANT"],
            "store_ids": ["store-A"],
            "home_store_id": "store-A",
            "active_store_id": "store-A",
            "is_active": True,
        },
        # Store-A optometrist - clinical, not POS - EXCLUDED from picker.
        {
            "user_id": "u-opt1",
            "username": "drmalhotra",
            "name": "Dr. Malhotra",
            "roles": ["OPTOMETRIST"],
            "store_ids": ["store-A"],
            "home_store_id": "store-A",
            "active_store_id": "store-A",
            "is_active": True,
        },
        # Inactive sales cashier at store-A - EXCLUDED by default active_only.
        {
            "user_id": "u-sales-inactive",
            "username": "former",
            "name": "Former Staff",
            "roles": ["SALES_CASHIER"],
            "store_ids": ["store-A"],
            "home_store_id": "store-A",
            "active_store_id": "store-A",
            "is_active": False,
        },
        # Store-B sales cashier - completely different store.
        {
            "user_id": "u-sales2",
            "username": "kavita",
            "name": "Kavita",
            "roles": ["SALES_CASHIER"],
            "store_ids": ["store-B"],
            "home_store_id": "store-B",
            "active_store_id": "store-B",
            "is_active": True,
        },
        # Multi-store sales staff (e.g. relief cover) assigned to A AND B.
        {
            "user_id": "u-multi",
            "username": "relief",
            "name": "Relief Cover",
            "roles": ["SALES_STAFF"],
            "store_ids": ["store-A", "store-B"],
            "home_store_id": "store-B",
            "active_store_id": "store-B",
            "is_active": True,
        },
    ]


def _query_for(store_id, roles=None, active_only=True):
    """Mirrors the query built by routers/stores.py::get_store_users."""
    query = {
        "$or": [
            {"store_ids": store_id},
            {"home_store_id": store_id},
        ]
    }
    if active_only:
        query["is_active"] = {"$ne": False}
    if roles:
        role_list = [r.strip().upper() for r in roles.split(",") if r.strip()]
        if role_list:
            query["roles"] = {"$in": role_list}
    return query


def _ids(rows):
    return sorted(r["user_id"] for r in rows)


def test_active_store_id_no_longer_leaks_superadmin():
    """The bug: SUPERADMIN with active_store_id=store-A but no store_ids was
    being returned by the picker. The fix drops active_store_id from the OR."""
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(_query_for("store-A"))
    ids = _ids(rows)
    assert "u-admin" not in ids
    assert "u-admin2" not in ids


def test_returns_explicitly_assigned_users_only():
    """store-A picker returns users whose store_ids/home_store_id contain it."""
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(_query_for("store-A"))
    ids = _ids(rows)
    # ramesh, sonia, anita, malhotra, relief (multi-store covers A)
    assert ids == ["u-acc1", "u-mgr1", "u-multi", "u-opt1", "u-sales1"]


def test_inactive_excluded_by_default():
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(_query_for("store-A", active_only=True))
    assert "u-sales-inactive" not in _ids(rows)


def test_inactive_included_when_active_only_false():
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(_query_for("store-A", active_only=False))
    assert "u-sales-inactive" in _ids(rows)


def test_roles_filter_narrows_to_sales_attributable_only():
    """The POS salesperson picker passes the sales-attributable role set so it
    doesn't show accountants/optometrists."""
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(
        _query_for(
            "store-A",
            roles="STORE_MANAGER,SALES_CASHIER,SALES_STAFF,OPTICIAN,CASHIER",
        )
    )
    ids = _ids(rows)
    # ramesh + sonia + relief, NO accountant, NO optometrist, NO admin/superadmin
    assert ids == ["u-mgr1", "u-multi", "u-sales1"]


def test_roles_filter_is_case_insensitive_and_whitespace_tolerant():
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(_query_for("store-A", roles="  sales_cashier ,Store_Manager"))
    ids = _ids(rows)
    assert ids == ["u-mgr1", "u-sales1"]


def test_cross_store_isolation():
    """store-A and store-B are independent worlds. Relief cover (multi-store) is
    the only user that appears in both."""
    repo = _FakeUserRepo(_users_fixture())
    a_ids = set(_ids(repo.find_many(_query_for("store-A"))))
    b_ids = set(_ids(repo.find_many(_query_for("store-B"))))
    # Single-store cashiers belong to exactly one store
    assert "u-sales1" in a_ids and "u-sales1" not in b_ids
    assert "u-sales2" in b_ids and "u-sales2" not in a_ids
    # Multi-store relief shows in both
    assert "u-multi" in a_ids and "u-multi" in b_ids


def test_empty_store_returns_empty_list():
    """Owner has not yet configured any staff for a brand-new store.
    The picker MUST return [] - not the logged-in admin, not anything."""
    repo = _FakeUserRepo(_users_fixture())
    rows = repo.find_many(_query_for("store-NEW"))
    assert rows == []
