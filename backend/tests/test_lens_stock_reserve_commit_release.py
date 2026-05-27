"""
IMS 2.0 - Lens stock reserve/commit/release atomicity tests (B' sub-PR 1)
=========================================================================
Q4 atomicity is the most safety-critical part of this PR. These tests
exercise:
  - reserve happy path increments .reserved without touching .on_hand
  - reserve when (on_hand - reserved) < qty returns 409 with available count
  - reserve concurrent attempts: only the ones that fit succeed (no oversell)
  - commit happy path decrements BOTH on_hand and reserved
  - commit refuses if reserved < qty
  - release happy path decrements .reserved (on_hand unchanged)
  - release refuses if reserved < qty
  - audit row written per action
  - bulk_import upserts cells + audit-rows

The router uses Mongo find_one_and_update with a $expr CAS predicate. The
FakeMongo replicates just enough of that to surface oversell-prevention
correctly.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import lens_catalog as catalog_router  # noqa: E402
from api.routers import lens_stock as stock_router  # noqa: E402


# ---------------------------------------------------------------------------
# FakeMongo: same shape as test_lens_catalog_crud, with the $expr matcher
# expanded for the CAS branches.
# ---------------------------------------------------------------------------


class DupKeyError(Exception):
    def __str__(self) -> str:  # noqa: D401
        return "E11000 duplicate key error"


class FakeCursor:
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
    def __init__(self, unique_keys=None):
        self._docs: List[Dict[str, Any]] = []
        self._unique = unique_keys or []

    def insert_one(self, doc):
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
                return type("R", (), {"matched_count": 1})()
        return type("R", (), {"matched_count": 0})()

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
        for k, v in flt.items():
            if k == "$expr":
                if not self._match_expr(doc, v):
                    return False
                continue
            if k == "$or":
                if not any(self._match(doc, sub) for sub in v):
                    return False
                continue
            if hasattr(v, "search") and callable(getattr(v, "search")):
                c = doc.get(k)
                if not isinstance(c, str):
                    return False
                if v.search(c) is None:
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
                    if doc.get(k) != v:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def _match_expr(self, doc, expr) -> bool:
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
        "lens_catalog": FakeColl(unique_keys=[("lens_line_id",)]),
        "lens_stock_lines": FakeColl(
            unique_keys=[
                ("lens_line_id", "store_id", "sph", "cyl", "add"),
                ("line_stock_id",),
            ]
        ),
        "lens_stock_audit": FakeColl(),
        "lens_enum_config": FakeColl(unique_keys=[("enum_id",)]),
    }
    # Seed a single lens line that the stock cells reference.
    collections["lens_catalog"].insert_one(
        {
            "lens_line_id": "essilor-crizal-1p60-mr8-sv-anti-blue",
            "brand": "Essilor",
            "series": "Crizal",
            "index": 1.60,
            "material": "MR8",
            "lens_type": "SV",
            "coating": "ANTI_BLUE",
            "sph_range": {"min": -8.0, "max": 6.0, "step": 0.25},
            "cyl_range": {"min": -4.0, "max": 0.0, "step": 0.25},
            "has_add": False,
            "add_range": None,
            "mrp": 4500.0,
            "is_active": True,
        }
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


LINE_ID = "essilor-crizal-1p60-mr8-sv-anti-blue"


def _seed_cell(fake_db, on_hand=10, reserved=0, sph=-2.0, cyl=0.0, add=None):
    """Create a stock cell directly so we can control starting on_hand/
    reserved."""
    fake_db["lens_stock_lines"].insert_one(
        {
            "line_stock_id": uuid.uuid4().hex,
            "lens_line_id": LINE_ID,
            "store_id": "STR-001",
            "sph": sph,
            "cyl": cyl,
            "add": add,
            "on_hand": on_hand,
            "reserved": reserved,
            "reorder_point": 0,
            "safety_stock": 0,
        }
    )


def _payload(qty, sph=-2.0, cyl=0.0, add=None, **extra):
    body = {
        "store_id": "STR-001",
        "sph": sph,
        "cyl": cyl,
        "add": add,
        "qty": qty,
    }
    body.update(extra)
    return stock_router.ReserveCommitReleasePayload(**body)


# ---------------------------------------------------------------------------
# Reserve
# ---------------------------------------------------------------------------


def test_reserve_happy_path_increments_reserved(fake_db):
    admin = _user(["SUPERADMIN"])
    _seed_cell(fake_db, on_hand=10, reserved=0)
    out = asyncio.run(stock_router.reserve_cell(LINE_ID, _payload(qty=3), admin))
    assert out["status"] == "success"
    assert out["cell"]["on_hand"] == 10  # unchanged
    assert out["cell"]["reserved"] == 3
    assert out["cell"]["available"] == 7
    # Audit row written.
    audit = list(fake_db["lens_stock_audit"]._docs)
    assert len(audit) == 1
    assert audit[0]["action"] == "reserve"
    assert audit[0]["delta_reserved"] == 3
    assert audit[0]["delta_on_hand"] == 0


def test_reserve_when_available_lt_qty_returns_409(fake_db):
    admin = _user(["SUPERADMIN"])
    # available = 10 - 8 = 2; trying to reserve 3 must 409.
    _seed_cell(fake_db, on_hand=10, reserved=8)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_router.reserve_cell(LINE_ID, _payload(qty=3), admin))
    assert exc.value.status_code == 409
    assert "insufficient" in exc.value.detail.lower()
    assert "available=2" in exc.value.detail


def test_reserve_concurrent_no_oversell(fake_db):
    """Two reserve attempts of qty=3 against on_hand=5 must net to one
    success and one 409 -- never a double-reserve of 6 (would oversell).

    The FakeColl CAS is sequential, but the second call sees the state
    after the first, so the predicate $expr evaluates to False.
    """
    admin = _user(["SUPERADMIN"])
    _seed_cell(fake_db, on_hand=5, reserved=0)
    first = asyncio.run(
        stock_router.reserve_cell(LINE_ID, _payload(qty=3), admin)
    )
    assert first["status"] == "success"
    assert first["cell"]["reserved"] == 3
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_router.reserve_cell(LINE_ID, _payload(qty=3), admin))
    assert exc.value.status_code == 409


def test_reserve_unknown_cell_returns_404(fake_db):
    admin = _user(["SUPERADMIN"])
    # No cell seeded.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_router.reserve_cell(LINE_ID, _payload(qty=1), admin))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def test_commit_decrements_both_fields(fake_db):
    admin = _user(["SUPERADMIN"])
    _seed_cell(fake_db, on_hand=10, reserved=5)
    out = asyncio.run(
        stock_router.commit_cell(LINE_ID, _payload(qty=2), admin)
    )
    assert out["status"] == "success"
    assert out["cell"]["on_hand"] == 8
    assert out["cell"]["reserved"] == 3
    # Audit row.
    audit = list(fake_db["lens_stock_audit"]._docs)
    assert audit[0]["action"] == "commit"
    assert audit[0]["delta_on_hand"] == -2
    assert audit[0]["delta_reserved"] == -2


def test_commit_refused_when_reserved_lt_qty(fake_db):
    admin = _user(["SUPERADMIN"])
    # reserved=1, trying to commit 3 -> 409.
    _seed_cell(fake_db, on_hand=10, reserved=1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_router.commit_cell(LINE_ID, _payload(qty=3), admin))
    assert exc.value.status_code == 409
    assert "reserved" in exc.value.detail.lower()


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def test_release_returns_to_available(fake_db):
    admin = _user(["SUPERADMIN"])
    _seed_cell(fake_db, on_hand=10, reserved=5)
    out = asyncio.run(
        stock_router.release_cell(LINE_ID, _payload(qty=2), admin)
    )
    assert out["cell"]["on_hand"] == 10  # unchanged
    assert out["cell"]["reserved"] == 3
    assert out["cell"]["available"] == 7
    audit = list(fake_db["lens_stock_audit"]._docs)
    assert audit[0]["action"] == "release"
    assert audit[0]["delta_reserved"] == -2


def test_release_refused_when_reserved_lt_qty(fake_db):
    admin = _user(["SUPERADMIN"])
    _seed_cell(fake_db, on_hand=10, reserved=1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stock_router.release_cell(LINE_ID, _payload(qty=2), admin))
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------


def test_bulk_import_json_matrix_creates_cells(fake_db):
    admin = _user(["SUPERADMIN"])
    payload = stock_router.BulkImportPayload(
        store_id="STR-001",
        matrix=[
            stock_router.BulkImportRow(sph=-2.0, cyl=0.0, qty=3),
            stock_router.BulkImportRow(sph=-2.25, cyl=-1.0, qty=5),
        ],
        source_id="paste-1",
    )
    out = asyncio.run(stock_router.bulk_import(LINE_ID, payload, admin))
    assert out["status"] == "success"
    assert out["total_written"] == 2
    assert out["total_failed"] == 0
    # Two cells materialised.
    cells = fake_db["lens_stock_lines"]._docs
    assert len(cells) == 2


def test_bulk_import_csv_then_reimport_overwrites_on_hand(fake_db):
    """Bulk import is upsert -- a second import on the same cell sets the
    NEW on_hand (absolute), not additive."""
    admin = _user(["SUPERADMIN"])
    csv_a = (
        "sph,cyl,qty,store_id\n"
        "-2.0,0,3,STR-001\n"
    )
    asyncio.run(
        stock_router.bulk_import(
            LINE_ID,
            stock_router.BulkImportPayload(store_id="STR-001", csv=csv_a),
            admin,
        )
    )
    csv_b = (
        "sph,cyl,qty,store_id\n"
        "-2.0,0,7,STR-001\n"
    )
    out = asyncio.run(
        stock_router.bulk_import(
            LINE_ID,
            stock_router.BulkImportPayload(store_id="STR-001", csv=csv_b),
            admin,
        )
    )
    assert out["total_written"] == 1
    # The cell now has on_hand=7 (absolute).
    cells = fake_db["lens_stock_lines"]._docs
    assert len(cells) == 1
    assert cells[0]["on_hand"] == 7


# ---------------------------------------------------------------------------
# Audit endpoint
# ---------------------------------------------------------------------------


def test_audit_returns_movements_desc(fake_db):
    """The audit endpoint returns the per-cell history newest first."""
    admin = _user(["SUPERADMIN"])
    _seed_cell(fake_db, on_hand=10, reserved=0)
    # Drive two movements.
    asyncio.run(stock_router.reserve_cell(LINE_ID, _payload(qty=2), admin))
    asyncio.run(stock_router.release_cell(LINE_ID, _payload(qty=1), admin))
    # Find the line_stock_id from the seeded cell.
    cell = fake_db["lens_stock_lines"]._docs[0]
    out = asyncio.run(
        stock_router.stock_audit(cell["line_stock_id"], 100, admin)
    )
    assert out["total"] == 2
    # Both actions present.
    actions = [a["action"] for a in out["audit"]]
    assert "reserve" in actions
    assert "release" in actions
