"""Catalog Manager backend pins: GET /catalog/products review-queue filters
(PR: catalog manager).

The 4,393 BVI-imported docs live in catalog_products with needs_review=true,
source="bvi_import", and (for BVI DRAFT/ARCHIVED products) is_active=false --
which the legacy default is_active=true filter HID. The list endpoint gained:

  * needs_review / source filters (in-Python, additive),
  * an is_active='all' sentinel (default stays 'true' -- legacy callers
    unchanged),
  * category canonicalisation on BOTH sides (imported docs store canonical
    long-form FRAME/SUNGLASS; native docs store the short prefix FR/SG),
  * a coalesce(created_at, migrated_at) sort key so imports don't sink,
  * `total` stays the post-filter pre-slice count (existing contract).

Called directly with monkeypatched _all_catalog_products (no DB needed).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import catalog as catalog_mod  # noqa: E402


def _user():
    return {
        "user_id": "u1",
        "username": "t",
        "roles": ["SUPERADMIN"],
        "active_store_id": "S1",
    }


_DOCS = [
    {
        # Native catalog doc: SHORT-code category, created_at string, active.
        "id": "native1",
        "sku": "FR-RB-1001",
        "title": "Ray-Ban Native Frame",
        "category": "FR",
        "attributes": {"brand_name": "Ray-Ban"},
        "is_active": True,
        "created_at": "2026-07-01T10:00:00",
    },
    {
        # BVI import: canonical long-form category, migrated_at datetime,
        # needs_review, ACTIVE.
        "id": "cuid_active",
        "title": "Vogue Imported Frame",
        "category": "FRAME",
        "attributes": {"brand_name": "Vogue"},
        "is_active": True,
        "needs_review": True,
        "pos_ready": False,
        "source": "bvi_import",
        "migrated_at": datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc),
    },
    {
        # BVI import that was DRAFT/ARCHIVED in BVI -> is_active FALSE. These
        # are precisely the docs needing review; the 'all' sentinel must
        # surface them.
        "id": "cuid_inactive",
        "title": "Prada Imported Sunglass",
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Prada"},
        "is_active": False,
        "needs_review": True,
        "pos_ready": False,
        "source": "bvi_import",
        "migrated_at": datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc),
    },
    {
        # Promoted import: review already cleared.
        "id": "cuid_done",
        "title": "Oakley Imported Done",
        "category": "SUNGLASS",
        "attributes": {"brand_name": "Oakley"},
        "is_active": True,
        "needs_review": False,
        "pos_ready": True,
        "source": "bvi_import",
        "migrated_at": datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc),
    },
]


def _call(monkeypatch, **kwargs):
    monkeypatch.setattr(
        catalog_mod, "_all_catalog_products", lambda: [dict(d) for d in _DOCS]
    )
    params = {
        "category": None,
        "brand": None,
        "search": None,
        "is_active": "true",
        "needs_review": None,
        "source": None,
        "limit": 50,
        "page": 1,
        "current_user": _user(),
    }
    params.update(kwargs)
    return asyncio.run(catalog_mod.list_catalog_products(**params))


def test_legacy_default_active_only(monkeypatch):
    out = _call(monkeypatch)
    ids = {p["id"] for p in out["products"]}
    assert "cuid_inactive" not in ids  # default is_active=true preserved
    assert {"native1", "cuid_active", "cuid_done"} <= ids


def test_needs_review_filter(monkeypatch):
    out = _call(monkeypatch, needs_review=True, is_active="all")
    ids = {p["id"] for p in out["products"]}
    assert ids == {"cuid_active", "cuid_inactive"}
    assert out["total"] == 2  # post-filter pre-slice count


def test_is_active_all_surfaces_inactive_imports(monkeypatch):
    out = _call(monkeypatch, is_active="all")
    ids = {p["id"] for p in out["products"]}
    assert "cuid_inactive" in ids
    assert out["total"] == 4


def test_source_filter(monkeypatch):
    out = _call(monkeypatch, source="bvi_import", is_active="all")
    ids = {p["id"] for p in out["products"]}
    assert ids == {"cuid_active", "cuid_inactive", "cuid_done"}


def test_category_filter_canonicalises_both_sides(monkeypatch):
    # Canonical param matches BOTH the native short-code doc and the imported
    # long-form doc.
    out = _call(monkeypatch, category="FRAME", is_active="all")
    assert {p["id"] for p in out["products"]} == {"native1", "cuid_active"}
    # Legacy short-code param can only gain matches (never lose the native doc).
    out_short = _call(monkeypatch, category="FR", is_active="all")
    assert {p["id"] for p in out_short["products"]} == {"native1", "cuid_active"}


def test_sort_coalesces_migrated_at(monkeypatch):
    # Newest-first across created_at AND migrated_at: the July-3 import beats
    # the July-2 import beats the July-1 native doc beats the June-30 import.
    out = _call(monkeypatch, is_active="all")
    order = [p["id"] for p in out["products"]]
    assert order == ["cuid_inactive", "cuid_active", "native1", "cuid_done"]


def test_total_is_pre_slice_with_pagination(monkeypatch):
    out = _call(monkeypatch, is_active="all", limit=2, page=2)
    assert out["total"] == 4
    assert len(out["products"]) == 2
    assert out["total_pages"] == 2
