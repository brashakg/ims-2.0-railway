"""
IMS 2.0 - Admin HSN->GST master CRUD tests
==========================================
Exercises /api/v1/admin/hsn (create/list/update/delete + 409 dedupe) against a
fake collection and verifies GST-cache invalidation on every write.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import admin_catalog  # noqa: E402


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, field, direction=1):
        self._docs = sorted(
            self._docs,
            key=lambda d: (d.get(field) is None, d.get(field, "")),
            reverse=direction == -1,
        )
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self):
        self.docs = []

    def find_one(self, q):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    def find(self, q=None):
        q = q or {}
        out = []
        for d in self.docs:
            ok = True
            for k, v in q.items():
                if isinstance(v, dict) and "$ne" in v:
                    if d.get(k) == v["$ne"]:
                        ok = False
                elif d.get(k) != v:
                    ok = False
            if ok:
                out.append(dict(d))
        return _Cursor(out)

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def update_one(self, flt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if all(d.get(k) == v for k, v in flt.items()):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()


@pytest.fixture
def fake(monkeypatch):
    coll = _FakeColl()
    monkeypatch.setattr(admin_catalog, "_coll", lambda name: coll)
    monkeypatch.setattr(admin_catalog, "seed_hsn_gst_master", lambda: 0)
    calls = {"invalidate": 0}
    monkeypatch.setattr(
        admin_catalog,
        "invalidate_gst_cache",
        lambda: calls.__setitem__("invalidate", calls["invalidate"] + 1),
    )
    return coll, calls


def test_create_list_update_delete(fake):
    coll, calls = fake

    created = asyncio.run(
        admin_catalog.create_hsn_rate(
            admin_catalog.HsnRateCreate(
                hsn_code="900130", gst_rate=5.0, category_hint="CONTACT_LENS"
            )
        )
    )
    assert created["hsn_code"] == "900130"
    assert created["gst_rate"] == 5.0
    hsn_id = created["hsn_id"]
    assert calls["invalidate"] == 1

    listed = asyncio.run(admin_catalog.list_hsn_rates())
    assert listed["total"] == 1
    assert listed["hsn_rates"][0]["hsn_code"] == "900130"

    updated = asyncio.run(
        admin_catalog.update_hsn_rate(
            hsn_id, admin_catalog.HsnRateUpdate(gst_rate=12.0)
        )
    )
    assert updated["gst_rate"] == 12.0
    assert calls["invalidate"] == 2

    deleted = asyncio.run(admin_catalog.delete_hsn_rate(hsn_id))
    assert deleted["deleted"] is True
    assert calls["invalidate"] == 3
    assert coll.docs == []


def test_duplicate_hsn_code_409(fake):
    coll, _ = fake
    asyncio.run(
        admin_catalog.create_hsn_rate(
            admin_catalog.HsnRateCreate(hsn_code="900130", gst_rate=5.0)
        )
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(
            admin_catalog.create_hsn_rate(
                admin_catalog.HsnRateCreate(hsn_code="900130", gst_rate=5.0)
            )
        )
    assert getattr(exc.value, "status_code", None) == 409
