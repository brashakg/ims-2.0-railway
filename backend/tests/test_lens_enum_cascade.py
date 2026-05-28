"""
IMS 2.0 - Lens enum cascade-rename tests (Branch B' sub-PR 3)
=============================================================
Covers the POST /lens-enums/{enum_type}/rename cascade:
  - rename rewrites the enum master AND every lens_catalog row using the
    old value (brand/coating/index/material/lens_type);
  - dependent lens_stock_lines rows are stamped (enum_rename_at) so the
    cascade is observable across all three layers;
  - the cascade is audit-logged (kind="lens_enum_rename") via audit_logs;
  - rename of a missing value -> 404; identical old/new -> 400;
  - series rename is refused (per-brand list -> use PATCH);
  - a rename that would collide two lens lines onto one identity -> 409,
    enum master left UNCHANGED (no partial cascade);
  - role gate (SALES_STAFF blocked, CATALOG_MANAGER allowed);
  - delete still blocked (409) when an active lens line references the value.

The unit cases monkey-patch the router's _get_db seam with an in-memory
FakeMongo so no real Mongo is needed. A separate integration test rounds
the cascade through the real router code against a live mongo:7.0 (CI
provides one; skipped fail-soft locally). pytestmark = asyncio so the
async handlers can be awaited directly (pytest.ini sets asyncio_mode=auto).
"""

from __future__ import annotations

import copy
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers import lens_enums as enums_router  # noqa: E402
from api.services.lens_catalog_validation import (  # noqa: E402
    DEFAULT_ENUM_ITEMS,
)


# ---------------------------------------------------------------------------
# FakeMongo (extends the test_lens_enums FakeColl with update_many).
# ---------------------------------------------------------------------------


class FakeColl:
    def __init__(self) -> None:
        self._docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": "x"})()

    def _match(self, d: Dict[str, Any], flt: Dict[str, Any]) -> bool:
        for k, v in flt.items():
            if isinstance(v, dict) and "$in" in v:
                if d.get(k) not in v["$in"]:
                    return False
            elif isinstance(v, dict):
                # Unsupported operator in this fake -> treat as no-constraint.
                continue
            elif d.get(k) != v:
                return False
        return True

    def update_one(self, flt, upd, upsert=False):
        for d in self._docs:
            if self._match(d, flt):
                if "$set" in upd:
                    d.update(upd["$set"])
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            if "$set" in upd:
                new.update(upd["$set"])
            self._docs.append(copy.deepcopy(new))
            return type(
                "R", (), {"matched_count": 0, "modified_count": 0, "upserted_id": "x"}
            )()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def update_many(self, flt, upd):
        n = 0
        for d in self._docs:
            if self._match(d, flt):
                if "$set" in upd:
                    d.update(upd["$set"])
                n += 1
        return type("R", (), {"matched_count": n, "modified_count": n})()

    def find_one(self, flt):
        for d in self._docs:
            if self._match(d, flt):
                return copy.deepcopy(d)
        return None

    def find(self, flt=None):
        flt = flt or {}
        return [copy.deepcopy(d) for d in self._docs if self._match(d, flt)]

    def count_documents(self, flt):
        return sum(1 for d in self._docs if self._match(d, flt))


class _DBShim:
    def __init__(self, collections):
        self._collections = collections

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeColl()
        return self._collections[name]


class _CapturingAuditRepo:
    """Captures audit_logs.create() calls so tests can assert the cascade
    audit row landed (kind=lens_enum_rename)."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def create(self, doc: Dict[str, Any]):
        self.rows.append(copy.deepcopy(doc))
        return {"log_id": uuid.uuid4().hex}


@pytest.fixture
def fake_db(monkeypatch):
    collections = {
        "lens_enum_config": FakeColl(),
        "lens_catalog": FakeColl(),
        "lens_stock_lines": FakeColl(),
    }
    for enum_type, items in DEFAULT_ENUM_ITEMS.items():
        collections["lens_enum_config"].insert_one(
            {"enum_id": enum_type, "items": list(items)}
        )
    # Seed brands so a brand rename has something to cascade onto.
    collections["lens_enum_config"].update_one(
        {"enum_id": "brands"},
        {"$set": {"items": ["Essilor", "Zeiss"]}},
        upsert=True,
    )
    shim = _DBShim(collections)
    audit = _CapturingAuditRepo()
    monkeypatch.setattr(enums_router, "_get_db", lambda: shim)
    monkeypatch.setattr(enums_router, "get_audit_repository", lambda: audit)
    return {"collections": collections, "audit": audit}


def _user(roles, user_id="u1"):
    return {
        "user_id": user_id,
        "username": "tester",
        "roles": list(roles),
        "store_ids": [],
    }


def _seed_line(
    collections,
    *,
    lens_line_id,
    brand,
    coating="ANTI_BLUE",
    series="A",
    index=1.60,
    material="CR39",
    lens_type="SV",
):
    collections["lens_catalog"].insert_one(
        {
            "lens_line_id": lens_line_id,
            "brand": brand,
            "series": series,
            "index": index,
            "material": material,
            "lens_type": lens_type,
            "coating": coating,
            "is_active": True,
        }
    )


def _seed_stock(collections, *, line_stock_id, lens_line_id, store_id="S1"):
    collections["lens_stock_lines"].insert_one(
        {
            "line_stock_id": line_stock_id,
            "lens_line_id": lens_line_id,
            "store_id": store_id,
            "sph": -1.0,
            "cyl": 0.0,
            "add": None,
            "on_hand": 5,
            "reserved": 0,
        }
    )


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


async def test_rename_blocked_for_non_writer(fake_db):
    """SALES_STAFF cannot rename; CATALOG_MANAGER can (require_roles gate)."""
    dep = enums_router.require_roles(*enums_router._WRITE_ROLES)
    with pytest.raises(HTTPException) as exc:
        await dep(_user(["SALES_STAFF"]))
    assert exc.value.status_code == 403
    # CATALOG_MANAGER passes the gate.
    assert await dep(_user(["CATALOG_MANAGER"])) is not None


# ---------------------------------------------------------------------------
# Cascade happy path
# ---------------------------------------------------------------------------


async def test_rename_cascades_to_enum_catalog_and_stock(fake_db):
    """Rename 'Essilor' -> 'Essilor India' rewrites the enum master, every
    lens_catalog row, stamps the dependent stock rows, and audit-logs it."""
    collections = fake_db["collections"]
    audit = fake_db["audit"]
    _seed_line(collections, lens_line_id="line-1", brand="Essilor")
    _seed_line(collections, lens_line_id="line-2", brand="Essilor", series="B")
    _seed_line(collections, lens_line_id="line-3", brand="Zeiss")
    _seed_stock(collections, line_stock_id="st-1", lens_line_id="line-1")
    _seed_stock(collections, line_stock_id="st-2", lens_line_id="line-2")
    _seed_stock(collections, line_stock_id="st-3", lens_line_id="line-3")

    admin = _user(["ADMIN"])
    out = await enums_router.rename_item(
        "brands",
        {"old_value": "Essilor", "new_value": "Essilor India"},
        admin,
    )
    assert out["status"] == "success"
    # Enum master updated.
    assert "Essilor India" in out["enum"]["items"]
    assert "Essilor" not in out["enum"]["items"]
    # Catalog cascade: the two Essilor lines flipped, Zeiss untouched.
    assert out["cascade"]["catalog_rows_updated"] == 2
    assert set(out["cascade"]["affected_lens_line_ids"]) == {"line-1", "line-2"}
    line1 = collections["lens_catalog"].find_one({"lens_line_id": "line-1"})
    assert line1["brand"] == "Essilor India"
    line3 = collections["lens_catalog"].find_one({"lens_line_id": "line-3"})
    assert line3["brand"] == "Zeiss"
    # Stock cascade: the two dependent stock rows stamped, Zeiss's not.
    assert out["cascade"]["stock_rows_stamped"] == 2
    st1 = collections["lens_stock_lines"].find_one({"line_stock_id": "st-1"})
    assert st1.get("enum_rename_at") is not None
    st3 = collections["lens_stock_lines"].find_one({"line_stock_id": "st-3"})
    assert st3.get("enum_rename_at") is None
    # Audit-logged with the cascade kind + blast-radius metadata.
    rename_rows = [r for r in audit.rows if r.get("kind") == "lens_enum_rename"]
    assert len(rename_rows) == 1
    meta = rename_rows[0]["metadata"]
    assert meta["old_value"] == "Essilor"
    assert meta["new_value"] == "Essilor India"
    assert meta["catalog_rows_updated"] == 2
    assert meta["stock_rows_stamped"] == 2


async def test_rename_keeps_lens_line_id_slug_stable(fake_db):
    """The slug is the stable FK; a rename must NOT re-build it (stock rows
    keep their parent link)."""
    collections = fake_db["collections"]
    _seed_line(collections, lens_line_id="line-1", brand="Essilor")
    _seed_stock(collections, line_stock_id="st-1", lens_line_id="line-1")
    admin = _user(["ADMIN"])
    await enums_router.rename_item(
        "brands", {"old_value": "Essilor", "new_value": "Essilor India"}, admin
    )
    # Slug unchanged; stock row still points at it.
    assert collections["lens_catalog"].find_one({"lens_line_id": "line-1"})
    st1 = collections["lens_stock_lines"].find_one({"line_stock_id": "st-1"})
    assert st1["lens_line_id"] == "line-1"


async def test_rename_coating_cascades(fake_db):
    """Coating rename hits the `coating` field (verifies the field map)."""
    collections = fake_db["collections"]
    _seed_line(collections, lens_line_id="l-1", brand="Zeiss", coating="ANTI_BLUE")
    admin = _user(["ADMIN"])
    out = await enums_router.rename_item(
        "coatings", {"old_value": "ANTI_BLUE", "new_value": "BLUE_GUARD"}, admin
    )
    assert out["cascade"]["catalog_rows_updated"] == 1
    line = collections["lens_catalog"].find_one({"lens_line_id": "l-1"})
    assert line["coating"] == "BLUE_GUARD"
    assert "BLUE_GUARD" in out["enum"]["items"]


# ---------------------------------------------------------------------------
# Validation / guards
# ---------------------------------------------------------------------------


async def test_rename_missing_value_404(fake_db):
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.rename_item(
            "brands", {"old_value": "NotThere", "new_value": "X"}, admin
        )
    assert exc.value.status_code == 404


async def test_rename_identical_values_400(fake_db):
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.rename_item(
            "brands", {"old_value": "Essilor", "new_value": "Essilor"}, admin
        )
    assert exc.value.status_code == 400


async def test_rename_missing_body_keys_400(fake_db):
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.rename_item("brands", {"old_value": "Essilor"}, admin)
    assert exc.value.status_code == 400


async def test_series_rename_refused(fake_db):
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.rename_item(
            "series", {"old_value": "A", "new_value": "B"}, admin
        )
    assert exc.value.status_code == 400
    assert "PATCH" in exc.value.detail


async def test_unknown_enum_type_404(fake_db):
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.rename_item(
            "colours", {"old_value": "a", "new_value": "b"}, admin
        )
    assert exc.value.status_code == 404


async def test_rename_collision_409_leaves_enum_unchanged(fake_db):
    """Renaming 'Essilor' -> 'Zeiss' would push line-1 onto the same identity
    tuple as the existing Zeiss line-2 -> 409, and the enum master must be
    left UNCHANGED (no partial cascade)."""
    collections = fake_db["collections"]
    _seed_line(
        collections,
        lens_line_id="line-1",
        brand="Essilor",
        series="A",
        index=1.60,
        material="CR39",
        lens_type="SV",
        coating="ANTI_BLUE",
    )
    _seed_line(
        collections,
        lens_line_id="line-2",
        brand="Zeiss",
        series="A",
        index=1.60,
        material="CR39",
        lens_type="SV",
        coating="ANTI_BLUE",
    )
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.rename_item(
            "brands", {"old_value": "Essilor", "new_value": "Zeiss"}, admin
        )
    assert exc.value.status_code == 409
    # Enum master untouched + line-1 still 'Essilor' (no partial write).
    brands = collections["lens_enum_config"].find_one({"enum_id": "brands"})
    assert "Essilor" in brands["items"]
    line1 = collections["lens_catalog"].find_one({"lens_line_id": "line-1"})
    assert line1["brand"] == "Essilor"


async def test_rename_index_value_cascades(fake_db):
    """Index rename takes numeric values + uses tolerant float compare."""
    collections = fake_db["collections"]
    _seed_line(collections, lens_line_id="l-1", brand="Zeiss", index=1.60)
    admin = _user(["ADMIN"])
    out = await enums_router.rename_item(
        "indexes", {"old_value": 1.60, "new_value": 1.61}, admin
    )
    assert out["cascade"]["catalog_rows_updated"] == 1
    line = collections["lens_catalog"].find_one({"lens_line_id": "l-1"})
    assert abs(line["index"] - 1.61) < 1e-9
    assert 1.61 in out["enum"]["items"]
    assert 1.60 not in out["enum"]["items"]


# ---------------------------------------------------------------------------
# Delete still blocked when referenced (regression guard around the editor).
# ---------------------------------------------------------------------------


async def test_delete_blocked_when_referenced(fake_db):
    """The enum editor's delete must 409 with the in-use count when an active
    lens line still references the value."""
    collections = fake_db["collections"]
    _seed_line(collections, lens_line_id="l-1", brand="Zeiss", coating="ANTI_BLUE")
    admin = _user(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        await enums_router.remove_item("coatings", "ANTI_BLUE", admin)
    assert exc.value.status_code == 409
    assert "1 active lens line" in exc.value.detail


async def test_delete_allowed_when_unreferenced(fake_db):
    admin = _user(["ADMIN"])
    out = await enums_router.remove_item("coatings", "MIRROR", admin)
    assert out["status"] == "success"
    assert "MIRROR" not in out["enum"]["items"]


# ===========================================================================
# Integration test -- real mongo:7.0 (CI), skipped fail-soft locally.
# ===========================================================================


@pytest.fixture(scope="module")
def mongo_db():
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip("Mongo unavailable; skipping integration tests")
        return None

    db_name = "ims_test_enum_cascade_{rand}".format(rand=uuid.uuid4().hex[:8])
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


class _RealDBShim:
    def __init__(self, real_db):
        self._db = real_db

    def get_collection(self, name):
        return self._db[name]


async def test_cascade_round_trip_real_mongo(mongo_db, monkeypatch):
    """End-to-end against real mongo: seed enum + catalog + stock, rename,
    assert the cascade landed in all three collections + an audit row."""
    shim = _RealDBShim(mongo_db)
    audit = _CapturingAuditRepo()
    monkeypatch.setattr(enums_router, "_get_db", lambda: shim)
    monkeypatch.setattr(enums_router, "get_audit_repository", lambda: audit)

    mongo_db["lens_enum_config"].insert_one(
        {"enum_id": "brands", "items": ["Essilor", "Zeiss"]}
    )
    mongo_db["lens_catalog"].insert_one(
        {
            "lens_line_id": "int-line-1",
            "brand": "Essilor",
            "series": "Pro",
            "index": 1.60,
            "material": "MR8",
            "lens_type": "SV",
            "coating": "ANTI_BLUE",
            "is_active": True,
        }
    )
    mongo_db["lens_stock_lines"].insert_one(
        {
            "line_stock_id": "int-st-1",
            "lens_line_id": "int-line-1",
            "store_id": "STR-1",
            "sph": -2.0,
            "cyl": 0.0,
            "add": None,
            "on_hand": 7,
            "reserved": 0,
        }
    )

    out = await enums_router.rename_item(
        "brands",
        {"old_value": "Essilor", "new_value": "Essilor India"},
        {"user_id": "int-admin", "username": "int", "roles": ["SUPERADMIN"]},
    )
    assert out["status"] == "success"
    assert out["cascade"]["catalog_rows_updated"] == 1
    assert out["cascade"]["stock_rows_stamped"] == 1

    enum_doc = mongo_db["lens_enum_config"].find_one({"enum_id": "brands"})
    assert "Essilor India" in enum_doc["items"]
    assert "Essilor" not in enum_doc["items"]
    line = mongo_db["lens_catalog"].find_one({"lens_line_id": "int-line-1"})
    assert line["brand"] == "Essilor India"
    st = mongo_db["lens_stock_lines"].find_one({"line_stock_id": "int-st-1"})
    assert st.get("enum_rename_at") is not None
    assert any(r.get("kind") == "lens_enum_rename" for r in audit.rows)
