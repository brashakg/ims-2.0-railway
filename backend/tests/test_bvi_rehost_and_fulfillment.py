"""
BVI cutover-hardening tests:
  - fulfillment_store_health  (R6: online decrement no-ops when the fulfillment
                               store holds no serialized stock_units)
  - rehost_uploads_images     (R3: re-host /uploads/ images to durable storage)

In-memory fakes only -- no real DB, no real object storage, no HTTP. Mirrors the
style of test_bvi_drift_and_parity.py.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ECOMMERCE_DATABASE_URL", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import online_sync_health as sh  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal in-memory fakes (adds update_one to the shared pattern)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    def __iter__(self):
        return iter(self._rows)


def _matches(row, query) -> bool:
    for key, cond in (query or {}).items():
        val = row.get(key)
        if isinstance(cond, dict):
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


class _FakeColl:
    def __init__(self, rows=None):
        self._rows = [dict(r) for r in (rows or [])]

    def find(self, query=None, _projection=None, *_a, **_k):
        return _FakeCursor([r for r in self._rows if _matches(r, query or {})])

    def count_documents(self, query):
        return sum(1 for r in self._rows if _matches(r, query))

    def update_one(self, filt, update):
        for r in self._rows:
            if _matches(r, filt):
                r.update((update or {}).get("$set", {}))
                return
        return


class _FakeDb:
    def __init__(self, colls=None):
        self._colls = dict(colls or {})

    def __getitem__(self, name):
        return self._colls.get(name, _FakeColl([]))


# ---------------------------------------------------------------------------
# fulfillment_store_health  (R6)
# ---------------------------------------------------------------------------


def test_fulfillment_store_health_warns_when_no_stock(monkeypatch):
    """A fulfillment store with 0 AVAILABLE stock_units -> a loud warning so the
    operator knows online decrements will silently no-op."""
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    db = _FakeDb({"stock_units": _FakeColl([])})
    out = sh.fulfillment_store_health(db)
    assert out["checked"] is True
    assert out["store_id"] == "BV-BOK-01"
    assert out["available_units"] == 0
    assert out["warning"] is not None
    assert "no-op" in out["warning"] or "0 AVAILABLE" in out["warning"]


def test_fulfillment_store_health_ok_when_stock_present(monkeypatch):
    """A real store holding AVAILABLE units -> no warning, count reflects on-hand."""
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    db = _FakeDb(
        {
            "stock_units": _FakeColl(
                [
                    {"store_id": "BV-BOK-01", "status": "AVAILABLE"},
                    {"store_id": "BV-BOK-01", "status": "AVAILABLE"},
                    {"store_id": "BV-BOK-01", "status": "SOLD"},  # excluded
                    {"store_id": "OTHER", "status": "AVAILABLE"},  # excluded
                ]
            )
        }
    )
    out = sh.fulfillment_store_health(db)
    assert out["checked"] is True
    assert out["available_units"] == 2
    assert out["warning"] is None
    assert out["is_virtual_default"] is False


def test_fulfillment_store_health_flags_virtual_default(monkeypatch):
    """Resolving to the virtual default bucket is flagged even when it has stock."""
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-ONLINE-01")
    db = _FakeDb(
        {"stock_units": _FakeColl([{"store_id": "BV-ONLINE-01", "status": "AVAILABLE"}])}
    )
    out = sh.fulfillment_store_health(db)
    assert out["is_virtual_default"] is True
    assert out["available_units"] == 1
    assert out["warning"] is not None  # advisory, not a hard zero-stock warning


def test_fulfillment_store_health_none_db(monkeypatch):
    """None db -> checked False, never raises."""
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    out = sh.fulfillment_store_health(None)
    assert out["checked"] is False
    assert out["store_id"] == "BV-BOK-01"


def test_sync_health_includes_fulfillment_block(monkeypatch):
    """The fulfillment_store block is wired into the full sync_health summary."""
    monkeypatch.setenv("ONLINE_FULFILLMENT_STORE_ID", "BV-BOK-01")
    db = _FakeDb({"stock_units": _FakeColl([])})
    health = sh.sync_health(db)
    assert "fulfillment_store" in health
    assert health["fulfillment_store"]["store_id"] == "BV-BOK-01"


# ---------------------------------------------------------------------------
# rehost_uploads_images  (R3)
# ---------------------------------------------------------------------------


def _images_with_local():
    return _FakeColl(
        [
            {
                "image_id": "img1",
                "sku": "SKU001",
                "url": "/uploads/raw/a.jpg",
                "edited_url": "https://cdn.example/edited/a.png",  # already durable
            },
            {
                "image_id": "img2",
                "sku": "SKU002",
                "url": "https://cdn.example/b.jpg",  # durable -> not a candidate
            },
            {
                "image_id": "img3",
                "sku": "SKU003",
                "url": "uploads/c.webp",  # relative local -> candidate
            },
        ]
    )


def test_rehost_dry_run_plans_without_writing():
    """dry_run=True counts candidates + reports backend but writes nothing."""
    coll = _images_with_local()
    db = _FakeDb({"product_images": coll})
    out = sh.rehost_uploads_images(db, dry_run=True)
    assert out["checked"] is True
    assert out["dry_run"] is True
    # img1.url + img3.url are local (img1.edited_url + img2.url are durable).
    assert out["candidates"] == 2
    assert out["rehosted"] == 0
    assert out["storage_backend"] is not None
    # No DB write happened (urls unchanged).
    rows = {r["image_id"]: r for r in coll._rows}
    assert rows["img1"]["url"] == "/uploads/raw/a.jpg"
    # Each planned item carries a fetch_url.
    assert all("fetch_url" in it for it in out["items"])


def test_rehost_no_candidates():
    """All-durable catalog -> 0 candidates."""
    db = _FakeDb(
        {
            "product_images": _FakeColl(
                [{"image_id": "x", "url": "https://cdn.example/x.jpg"}]
            )
        }
    )
    out = sh.rehost_uploads_images(db, dry_run=True)
    assert out["candidates"] == 0
    assert out["items"] == []


def test_rehost_live_rewrites_url(monkeypatch):
    """dry_run=False fetches, uploads to durable storage, and rewrites the url."""

    class _FakeStorage:
        name = "s3"

        def available(self):
            return True

        def put(self, key, data, content_type="image/png"):
            return f"https://cdn.example/{key}"

    monkeypatch.setattr(
        "api.services.object_storage.get_object_storage", lambda: _FakeStorage()
    )
    monkeypatch.setattr(sh, "_fetch_bytes", lambda url: b"imgbytes")
    monkeypatch.setenv("BVI_UPLOADS_BASE_URL", "https://uniparallel.com")

    coll = _FakeColl([{"image_id": "img1", "sku": "SKU001", "url": "/uploads/a.jpg"}])
    db = _FakeDb({"product_images": coll})
    out = sh.rehost_uploads_images(db, dry_run=False)
    assert out["dry_run"] is False
    assert out["durable"] is True
    assert out["rehosted"] == 1
    assert out["failed"] == 0
    # The stored url was rewritten to the durable CDN url + dirty-flagged.
    row = coll._rows[0]
    assert row["url"].startswith("https://cdn.example/bvi-rehost/")
    assert row["locally_modified"] is True


def test_rehost_none_db():
    """None db -> checked False, never raises."""
    out = sh.rehost_uploads_images(None, dry_run=True)
    assert out["checked"] is False
    assert out["candidates"] == 0
