"""
IMS 2.0 - Lens enum config router tests (Branch B' sub-PR 1)
=============================================================
Covers:
  - GET /lens-enums returns all six enum_types with seeded defaults
  - PATCH /lens-enums/{enum_type} replaces the list (wholesale)
  - POST /lens-enums/{enum_type}/items appends (de-duped)
  - DELETE /lens-enums/{enum_type}/items/{item} refuses when in use
  - role gates (CATALOG_MANAGER cannot edit; ADMIN can)
  - Q6 seed defaults present (technical dims only; brands/series empty)
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import lens_enums as enums_router  # noqa: E402
from api.services.lens_catalog_validation import DEFAULT_ENUM_ITEMS  # noqa: E402


# ---------------------------------------------------------------------------
# FakeMongo (minimal -- only the lookups the enums router uses).
# ---------------------------------------------------------------------------


class FakeColl:
    def __init__(self, unique_keys=None):
        self._docs: List[Dict[str, Any]] = []
        self._unique = unique_keys or []

    def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": "x"})()

    def update_one(self, flt, upd, upsert=False):
        for d in self._docs:
            if all(d.get(k) == v for k, v in flt.items() if not isinstance(v, dict)):
                if "$set" in upd:
                    d.update(upd["$set"])
                return type("R", (), {"matched_count": 1})()
        if upsert:
            new = dict(flt)
            if "$set" in upd:
                new.update(upd["$set"])
            self._docs.append(copy.deepcopy(new))
            return type("R", (), {"matched_count": 0, "upserted_id": "x"})()
        return type("R", (), {"matched_count": 0})()

    def find_one(self, flt):
        for d in self._docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return copy.deepcopy(d)
        return None

    def find(self, flt=None):
        flt = flt or {}
        return [
            copy.deepcopy(d)
            for d in self._docs
            if all(d.get(k) == v for k, v in flt.items())
        ]

    def count_documents(self, flt):
        return sum(
            1
            for d in self._docs
            if all(d.get(k) == v for k, v in flt.items())
        )


class _DBShim:
    def __init__(self, collections):
        self._collections = collections

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeColl()
        return self._collections[name]


@pytest.fixture
def fake_db(monkeypatch):
    collections = {
        "lens_enum_config": FakeColl(),
        "lens_catalog": FakeColl(),
    }
    # Seed the defaults the migration would create.
    for enum_type, items in DEFAULT_ENUM_ITEMS.items():
        collections["lens_enum_config"].insert_one(
            {"enum_id": enum_type, "items": list(items)}
        )
    shim = _DBShim(collections)
    monkeypatch.setattr(enums_router, "_get_db", lambda: shim)
    monkeypatch.setattr(enums_router, "get_audit_repository", lambda: None)
    return collections


def _user(roles, user_id="u1"):
    return {
        "user_id": user_id,
        "username": "tester",
        "roles": list(roles),
        "store_ids": [],
    }


# ---------------------------------------------------------------------------
# Seed defaults
# ---------------------------------------------------------------------------


def test_list_returns_all_six_enum_types(fake_db):
    user = _user(["SALES_STAFF"])
    out = asyncio.run(enums_router.list_enums(user))
    assert "coatings" in out["enums"]
    assert "brands" in out["enums"]
    assert "series" in out["enums"]
    assert "indexes" in out["enums"]
    assert "materials" in out["enums"]
    assert "lens_types" in out["enums"]
    # Technical dims seeded, business dims empty.
    assert "ANTI_BLUE" in out["enums"]["coatings"]
    assert "CR39" in out["enums"]["materials"]
    assert "SV" in out["enums"]["lens_types"]
    assert 1.50 in out["enums"]["indexes"]
    assert out["enums"]["brands"] == []
    assert out["enums"]["series"] == []


def test_get_single_enum(fake_db):
    user = _user(["SALES_STAFF"])
    out = asyncio.run(enums_router.get_enum("coatings", user))
    assert out["enum_type"] == "coatings"
    assert "ANTI_BLUE" in out["items"]


def test_get_unknown_enum_type_returns_404(fake_db):
    user = _user(["SUPERADMIN"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(enums_router.get_enum("colours", user))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def test_replace_enum_admin_only(fake_db):
    """CATALOG_MANAGER cannot PATCH. ADMIN can."""
    cm = _user(["CATALOG_MANAGER"])
    dep = enums_router.require_roles(*enums_router._WRITE_ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(cm))
    assert exc.value.status_code == 403
    admin = _user(["ADMIN"])
    out = asyncio.run(
        enums_router.replace_enum(
            "coatings",
            {"items": ["ANTI_BLUE", "POLARIZED", "MIRROR"]},
            admin,
        )
    )
    assert out["status"] == "success"
    assert out["enum"]["items"] == ["ANTI_BLUE", "POLARIZED", "MIRROR"]


def test_append_dedupes(fake_db):
    admin = _user(["ADMIN"])
    # Append an already-present coating -> still only one entry.
    before = list(fake_db["lens_enum_config"].find_one(
        {"enum_id": "coatings"}
    )["items"])
    out = asyncio.run(
        enums_router.append_item("coatings", {"item": "ANTI_BLUE"}, admin)
    )
    assert out["enum"]["items"].count("ANTI_BLUE") == 1
    assert len(out["enum"]["items"]) == len(before)


def test_append_new_index_coerced_to_float(fake_db):
    admin = _user(["ADMIN"])
    # Pass index as a string -- the router coerces to float.
    out = asyncio.run(
        enums_router.append_item("indexes", {"item": "1.59"}, admin)
    )
    assert 1.59 in out["enum"]["items"]


def test_remove_refused_when_in_use(fake_db):
    """If an active lens_catalog row uses the value, removal is 409."""
    admin = _user(["ADMIN"])
    fake_db["lens_catalog"].insert_one(
        {
            "lens_line_id": "x",
            "brand": "Brand",
            "coating": "ANTI_BLUE",
            "is_active": True,
        }
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            enums_router.remove_item("coatings", "ANTI_BLUE", admin)
        )
    assert exc.value.status_code == 409
    assert "1 active lens line" in exc.value.detail


def test_remove_succeeds_when_not_in_use(fake_db):
    admin = _user(["ADMIN"])
    out = asyncio.run(enums_router.remove_item("coatings", "MIRROR", admin))
    assert out["status"] == "success"
    assert "MIRROR" not in out["enum"]["items"]


def test_replace_indexes_validates_gt_one(fake_db):
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            enums_router.replace_enum(
                "indexes", {"items": [1.50, 1.0]}, admin
            )
        )
    assert exc.value.status_code == 400
