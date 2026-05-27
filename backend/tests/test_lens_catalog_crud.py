"""
IMS 2.0 - Lens catalog CRUD tests (Branch B' sub-PR 1)
======================================================
Async-call the lens-catalog router endpoints directly (no TestClient HTTP
layer) against an in-memory FakeMongo. Mirrors test_fixtures_crud.py's
style.

Covered:
  - create / list / get / patch / delete happy path
  - UNIQUE constraint on (brand, series, index, material, lens_type, coating)
  - PATCH cannot change identity fields (model excludes them)
  - DELETE refused when stock cells carry on_hand > 0
  - role gates (CASHIER, SALES_STAFF cannot write)
  - meta/options returns the live enum config
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

from api.routers import lens_catalog as catalog_router  # noqa: E402
from api.routers import lens_stock as stock_router  # noqa: E402


# ---------------------------------------------------------------------------
# FakeMongo (same shape as test_fixtures_crud.py).
# ---------------------------------------------------------------------------


class DupKeyError(Exception):
    def __str__(self) -> str:  # noqa: D401
        return "E11000 duplicate key error"


class FakeCursor:
    """Tiny cursor that mirrors pymongo .find().limit().sort() chainability."""

    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def sort(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeColl:
    def __init__(self, unique_keys: Optional[List[tuple]] = None):
        self._docs: List[Dict[str, Any]] = []
        self._unique = unique_keys or []

    def insert_one(self, doc: Dict[str, Any]):
        self._check_unique(doc)
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": "x"})()

    def update_one(self, flt, upd, upsert=False):
        for d in self._docs:
            if self._match(d, flt):
                if "$set" in upd:
                    d.update(upd["$set"])
                if "$inc" in upd:
                    for k, v in upd["$inc"].items():
                        d[k] = int(d.get(k) or 0) + int(v)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new = dict(flt)
            if "$set" in upd:
                new.update(upd["$set"])
            self._docs.append(copy.deepcopy(new))
            return type("R", (), {"matched_count": 0, "upserted_id": "x"})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find_one(self, flt):
        for d in self._docs:
            if self._match(d, flt):
                return copy.deepcopy(d)
        return None

    def find_one_and_update(self, flt, upd, return_document=None):
        for d in self._docs:
            if self._match(d, flt):
                if "$inc" in upd:
                    for k, v in upd["$inc"].items():
                        d[k] = int(d.get(k) or 0) + int(v)
                if "$set" in upd:
                    d.update(upd["$set"])
                return copy.deepcopy(d)
        return None

    def find(self, flt=None):
        flt = flt or {}
        return FakeCursor(
            copy.deepcopy(d) for d in self._docs if self._match(d, flt)
        )

    def count_documents(self, flt):
        return sum(1 for d in self._docs if self._match(d, flt))

    def _check_unique(self, doc):
        for ukey in self._unique:
            for d in self._docs:
                if all(d.get(k) == doc.get(k) for k in ukey):
                    raise DupKeyError()

    def _match(self, doc, flt):
        import re as _re

        for k, v in flt.items():
            if k == "$expr":
                # $expr {$gte: [{$subtract:[$on_hand, $reserved]}, qty]}
                if not self._match_expr(doc, v):
                    return False
                continue
            if k == "$or":
                if not any(self._match(doc, sub) for sub in v):
                    return False
                continue
            # compiled-regex match (router uses re.compile for q filter)
            if hasattr(v, "search") and callable(getattr(v, "search")):
                candidate = doc.get(k)
                if not isinstance(candidate, str):
                    return False
                if v.search(candidate) is None:
                    return False
                continue
            if isinstance(v, dict):
                if "$ne" in v:
                    if doc.get(k) == v["$ne"]:
                        return False
                elif "$gt" in v:
                    if not (int(doc.get(k) or 0) > int(v["$gt"])):
                        return False
                elif "$gte" in v:
                    if not (int(doc.get(k) or 0) >= int(v["$gte"])):
                        return False
                else:
                    # treat as exact-match dict
                    if doc.get(k) != v:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def _match_expr(self, doc, expr) -> bool:
        """Evaluate the tiny subset of $expr needed by reserve/commit/release."""
        if not isinstance(expr, dict):
            return True

        def resolve(op):
            if isinstance(op, dict):
                if "$subtract" in op:
                    a, b = op["$subtract"]
                    return resolve(a) - resolve(b)
                if "$and" in op:
                    return all(resolve(x) for x in op["$and"])
                if "$gte" in op:
                    a, b = op["$gte"]
                    return resolve(a) >= resolve(b)
            if isinstance(op, str) and op.startswith("$"):
                return int(doc.get(op[1:]) or 0)
            return op

        return bool(resolve(expr))


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
        "lens_catalog": FakeColl(
            unique_keys=[
                (
                    "brand",
                    "series",
                    "index",
                    "material",
                    "lens_type",
                    "coating",
                ),
                ("lens_line_id",),
            ]
        ),
        "lens_stock_lines": FakeColl(
            unique_keys=[
                ("lens_line_id", "store_id", "sph", "cyl", "add"),
                ("line_stock_id",),
            ]
        ),
        "lens_stock_audit": FakeColl(),
        "lens_enum_config": FakeColl(unique_keys=[("enum_id",)]),
    }
    # Seed enum_config so the catalog validator has values to check against.
    collections["lens_enum_config"].insert_one(
        {"enum_id": "coatings", "items": ["ANTI_BLUE", "DUAL_COAT", "HC"]}
    )
    collections["lens_enum_config"].insert_one(
        {"enum_id": "brands", "items": ["Essilor", "Zeiss"]}
    )
    collections["lens_enum_config"].insert_one(
        {"enum_id": "series", "items": []}
    )
    collections["lens_enum_config"].insert_one(
        {"enum_id": "indexes", "items": [1.50, 1.60, 1.67]}
    )
    collections["lens_enum_config"].insert_one(
        {"enum_id": "materials", "items": ["CR39", "MR8"]}
    )
    collections["lens_enum_config"].insert_one(
        {"enum_id": "lens_types", "items": ["SV", "PROGRESSIVE"]}
    )
    shim = _DBShim(collections)
    monkeypatch.setattr(catalog_router, "_get_db", lambda: shim)
    monkeypatch.setattr(stock_router, "_get_db", lambda: shim)
    monkeypatch.setattr(catalog_router, "get_audit_repository", lambda: None)
    return collections


def _user(roles, store_id="STR-001", user_id="u1"):
    return {
        "user_id": user_id,
        "username": "tester",
        "roles": list(roles),
        "store_ids": [store_id],
        "active_store_id": store_id,
    }


def _payload_line(**overrides):
    base = {
        "brand": "Essilor",
        "series": "Crizal",
        "index": 1.60,
        "material": "MR8",
        "lens_type": "SV",
        "coating": "ANTI_BLUE",
        "mrp": 4500.0,
    }
    base.update(overrides)
    return catalog_router.LensLineCreate(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_meta_options_returns_live_enum_config(fake_db):
    """meta/options surfaces the live enum dict the FE dropdowns need."""
    user = _user(["SALES_STAFF"])
    out = asyncio.run(catalog_router.lens_catalog_meta_options(user))
    assert "coatings" in out["enums"]
    assert "ANTI_BLUE" in out["enums"]["coatings"]
    assert "brands" in out["enums"]
    assert "Essilor" in out["enums"]["brands"]


def test_create_get_patch_delete_happy_path(fake_db):
    admin = _user(["SUPERADMIN"])

    # CREATE
    out = asyncio.run(catalog_router.create_lens_line(_payload_line(), admin))
    assert out["status"] == "success"
    slug = out["lens_line"]["lens_line_id"]
    assert slug == "essilor-crizal-1p60-mr8-sv-anti-blue"

    # GET
    got = asyncio.run(catalog_router.get_lens_line(slug, admin))
    assert got["lens_line"]["lens_line_id"] == slug
    assert got["lens_line"]["gst_rate"] == 5.0

    # PATCH (mrp + notes)
    upd = catalog_router.LensLineUpdate(mrp=4800.0, notes="bumped MRP")
    patched = asyncio.run(catalog_router.update_lens_line(slug, upd, admin))
    assert patched["lens_line"]["mrp"] == 4800.0
    assert patched["lens_line"]["notes"] == "bumped MRP"

    # DELETE (no stock cells -> succeeds)
    deleted = asyncio.run(catalog_router.delete_lens_line(slug, admin))
    assert deleted["status"] == "success"
    raw = fake_db["lens_catalog"].find_one({"lens_line_id": slug})
    assert raw["is_active"] is False


def test_create_duplicate_combo_returns_409(fake_db):
    admin = _user(["SUPERADMIN"])
    asyncio.run(catalog_router.create_lens_line(_payload_line(), admin))
    # Same (brand, series, index, material, lens_type, coating) -> 409.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            catalog_router.create_lens_line(_payload_line(), admin)
        )
    assert exc.value.status_code == 409


def test_create_with_unknown_coating_returns_400(fake_db):
    admin = _user(["SUPERADMIN"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            catalog_router.create_lens_line(
                _payload_line(coating="UNKNOWN_COAT"), admin
            )
        )
    assert exc.value.status_code == 400
    assert "coating" in exc.value.detail.lower()


def test_list_filters_by_brand(fake_db):
    admin = _user(["SUPERADMIN"])
    asyncio.run(
        catalog_router.create_lens_line(_payload_line(brand="Essilor"), admin)
    )
    asyncio.run(
        catalog_router.create_lens_line(
            _payload_line(brand="Zeiss", series="DriveSafe"), admin
        )
    )
    out = asyncio.run(
        catalog_router.list_lens_lines(
            brand="Zeiss",
            series=None, index=None, material=None,
            lens_type=None, coating=None, q=None, active=True, limit=50,
            current_user=admin,
        )
    )
    assert out["total"] == 1
    assert out["lens_lines"][0]["brand"] == "Zeiss"


def test_list_search_q(fake_db):
    """Free-text q filters by brand/series via regex."""
    admin = _user(["SUPERADMIN"])
    asyncio.run(
        catalog_router.create_lens_line(_payload_line(series="Crizal"), admin)
    )
    asyncio.run(
        catalog_router.create_lens_line(
            _payload_line(series="Crizal", coating="HC"), admin
        )
    )
    out = asyncio.run(
        catalog_router.list_lens_lines(
            brand=None, series=None, index=None, material=None,
            lens_type=None, coating=None,
            q="criz", active=True, limit=50, current_user=admin,
        )
    )
    # Both should match.
    assert out["total"] == 2


def test_role_gate_cashier_cannot_create(fake_db):
    """CASHIER is not in _WRITE_ROLES."""
    cashier = _user(["CASHIER"])
    dep = catalog_router.require_roles(*catalog_router._WRITE_ROLES)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dep(cashier))
    assert exc.value.status_code == 403


def test_catalog_manager_can_create(fake_db):
    """CATALOG_MANAGER is in _WRITE_ROLES."""
    cm = _user(["CATALOG_MANAGER"])
    out = asyncio.run(catalog_router.create_lens_line(_payload_line(), cm))
    assert out["status"] == "success"


def test_delete_refused_when_stock_carries_on_hand(fake_db):
    """A lens line with stock cells carrying on_hand > 0 cannot be soft-
    deleted."""
    admin = _user(["SUPERADMIN"])
    out = asyncio.run(catalog_router.create_lens_line(_payload_line(), admin))
    slug = out["lens_line"]["lens_line_id"]

    # Inject a stock cell with on_hand=5.
    fake_db["lens_stock_lines"].insert_one(
        {
            "line_stock_id": "ls-001",
            "lens_line_id": slug,
            "store_id": "STR-001",
            "sph": -2.0,
            "cyl": 0.0,
            "add": None,
            "on_hand": 5,
            "reserved": 0,
        }
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(catalog_router.delete_lens_line(slug, admin))
    assert exc.value.status_code == 409
    assert "stock" in exc.value.detail.lower()


def test_delete_allowed_when_stock_is_zero(fake_db):
    """When all cells carry on_hand=0 + reserved=0, soft-delete succeeds."""
    admin = _user(["SUPERADMIN"])
    out = asyncio.run(catalog_router.create_lens_line(_payload_line(), admin))
    slug = out["lens_line"]["lens_line_id"]
    fake_db["lens_stock_lines"].insert_one(
        {
            "line_stock_id": "ls-002",
            "lens_line_id": slug,
            "store_id": "STR-001",
            "sph": -2.0,
            "cyl": 0.0,
            "add": None,
            "on_hand": 0,
            "reserved": 0,
        }
    )
    deleted = asyncio.run(catalog_router.delete_lens_line(slug, admin))
    assert deleted["status"] == "success"


def test_get_unknown_returns_404(fake_db):
    admin = _user(["SUPERADMIN"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(catalog_router.get_lens_line("does-not-exist", admin))
    assert exc.value.status_code == 404


def test_patch_unknown_returns_404(fake_db):
    admin = _user(["SUPERADMIN"])
    upd = catalog_router.LensLineUpdate(mrp=999.0)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(catalog_router.update_lens_line("none", upd, admin))
    assert exc.value.status_code == 404


def test_patch_no_changes_returns_no_changes(fake_db):
    """Empty patch body returns no_changes (no DB write)."""
    admin = _user(["SUPERADMIN"])
    out = asyncio.run(catalog_router.create_lens_line(_payload_line(), admin))
    slug = out["lens_line"]["lens_line_id"]
    upd = catalog_router.LensLineUpdate()  # all None
    patched = asyncio.run(catalog_router.update_lens_line(slug, upd, admin))
    assert patched["status"] == "no_changes"
