"""
IMS 2.0 - Sub-brands: Brand Master embed + cascade + Catalog enforcement
========================================================================
Covers the sub-brand layer added on top of the Catalog Dictionary:

  1. catalog_dictionary.load_subbrand_options: per-brand lookup (case-
     insensitive brand match, active-only, sorted, de-duped), [] for a brand
     with none / unknown brand, None on failure (fail-open).
  2. product_master.enforce_dictionary_values: subbrand enforced ONLY when
     the brand HAS sub-brands (reject + canonicalise), free otherwise.
  3. admin_catalog: GET /brands embeds subbrands per brand (batch), DELETE
     /brands/{id} cascade-deletes its subbrand_masters rows.
  4. GET /products/brand-options: authenticated read projection
     {brands: [{name, subbrands}]} + RBAC row catalogued.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_subbrands.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.routers import admin_catalog as ac  # noqa: E402
from api.services import catalog_dictionary as cd  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, docs):
        self._docs = list(docs)
        self.deleted_filters = []

    def find(self, query=None):
        query = query or {}
        out = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False
                elif isinstance(v, dict) and "$ne" in v:
                    if d.get(k) == v["$ne"]:
                        ok = False
                elif d.get(k) != v:
                    ok = False
            if ok:
                out.append(dict(d))
        return out

    def delete_many(self, query):
        self.deleted_filters.append(query)
        before = len(self._docs)
        self._docs = [
            d for d in self._docs
            if not all(d.get(k) == v for k, v in query.items())
        ]

        class _Res:
            deleted_count = before - len(self._docs)

        return _Res()


class _FakeDb:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return self._colls.get(name) or _FakeColl([])


_BRANDS = [
    {"brand_id": "b1", "name": "Ray-Ban", "categories": ["SG", "FR"], "is_active": True},
    {"brand_id": "b2", "name": "Titan", "categories": ["WT"], "is_active": True},
]
_SUBS = [
    {"subbrand_id": "s1", "brand_id": "b1", "name": "Aviator", "code": "AV"},
    {"subbrand_id": "s2", "brand_id": "b1", "name": "Wayfarer", "code": "WF"},
    {"subbrand_id": "s3", "brand_id": "b2", "name": "Raga", "code": "RG"},
]


def _db():
    return _FakeDb({
        cd.BRAND_COLLECTION: _FakeColl(_BRANDS),
        cd.SUBBRAND_COLLECTION: _FakeColl(_SUBS),
    })


# ---------------------------------------------------------------------------
# 1. load_subbrand_options
# ---------------------------------------------------------------------------


class TestSubbrandLoader:
    def test_case_insensitive_brand_match_sorted(self):
        assert cd.load_subbrand_options(_db(), "ray-ban") == ["Aviator", "Wayfarer"]

    def test_unknown_brand_or_no_subs_returns_empty(self):
        assert cd.load_subbrand_options(_db(), "Nobody") == []
        db = _FakeDb({
            cd.BRAND_COLLECTION: _FakeColl(_BRANDS),
            cd.SUBBRAND_COLLECTION: _FakeColl([]),
        })
        assert cd.load_subbrand_options(db, "Ray-Ban") == []

    def test_fail_open_none(self):
        assert cd.load_subbrand_options(None, "Ray-Ban") is None
        assert cd.load_subbrand_options(_db(), "") is None

        class _Boom:
            def get_collection(self, name):
                raise RuntimeError("down")

        assert cd.load_subbrand_options(_Boom(), "Ray-Ban") is None


# ---------------------------------------------------------------------------
# 2. Enforcement in the create door
# ---------------------------------------------------------------------------


_SENTINEL = object()


class TestSubbrandEnforcement:
    def _patch(self, monkeypatch, subs):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: ["Ray-Ban"])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {})
        monkeypatch.setattr(cd, "load_subbrand_options", lambda db, b: subs)

    def test_unknown_subbrand_rejected(self, monkeypatch):
        self._patch(monkeypatch, ["Aviator", "Wayfarer"])
        with pytest.raises(pm.ProductMasterError) as exc:
            pm.enforce_dictionary_values(
                "SUNGLASS", {"brand_name": "Ray-Ban", "subbrand": "Clubmaster"},
                db=_SENTINEL,
            )
        assert exc.value.status == 422
        assert exc.value.field == "subbrand"
        assert "Brand Master" in exc.value.message

    def test_subbrand_canonicalised(self, monkeypatch):
        self._patch(monkeypatch, ["Aviator"])
        out = pm.enforce_dictionary_values(
            "SUNGLASS", {"brand_name": "ray-ban", "subbrand": "aviator"}, db=_SENTINEL
        )
        assert out["brand_name"] == "Ray-Ban"
        assert out["subbrand"] == "Aviator"

    def test_brand_without_subbrands_is_free(self, monkeypatch):
        for subs in ([], None):
            self._patch(monkeypatch, subs)
            out = pm.enforce_dictionary_values(
                "SUNGLASS", {"brand_name": "Ray-Ban", "subbrand": "Anything"},
                db=_SENTINEL,
            )
            assert out["subbrand"] == "Anything"

    def test_no_brand_means_no_subbrand_gate(self, monkeypatch):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: [])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {})

        def _boom(db, b):  # pragma: no cover - must never run
            raise AssertionError("subbrand lookup without a brand")

        monkeypatch.setattr(cd, "load_subbrand_options", _boom)
        out = pm.enforce_dictionary_values("SUNGLASS", {"subbrand": "X"}, db=_SENTINEL)
        assert out["subbrand"] == "X"


# ---------------------------------------------------------------------------
# 3. Brand Master list embed + cascade delete
# ---------------------------------------------------------------------------


class TestAdminBrandMaster:
    def test_attach_subbrands_batches_per_brand(self, monkeypatch):
        subs = _FakeColl(_SUBS)
        monkeypatch.setattr(
            ac, "_coll", lambda name: subs if name == "subbrand_masters" else None
        )
        brands = [dict(b) for b in _BRANDS]
        out = ac._attach_subbrands(brands)
        assert [s["name"] for s in out[0]["subbrands"]] == ["Aviator", "Wayfarer"]
        assert [s["name"] for s in out[1]["subbrands"]] == ["Raga"]

    def test_attach_subbrands_fail_soft(self, monkeypatch):
        monkeypatch.setattr(ac, "_coll", lambda name: None)
        out = ac._attach_subbrands([dict(_BRANDS[0])])
        assert out[0]["subbrands"] == []

    def test_cascade_delete_removes_only_that_brand(self, monkeypatch):
        subs = _FakeColl(_SUBS)
        monkeypatch.setattr(
            ac, "_coll", lambda name: subs if name == "subbrand_masters" else None
        )
        assert ac._cascade_delete_subbrands("b1") == 2
        assert subs.deleted_filters == [{"brand_id": "b1"}]
        assert ac._cascade_delete_subbrands("b1") == 0  # already gone


# ---------------------------------------------------------------------------
# 4. GET /products/brand-options + RBAC
# ---------------------------------------------------------------------------


class TestBrandOptionsEndpoint:
    def test_rbac_row_catalogued(self):
        rows = [
            (p.get("method"), p.get("path"), p.get("allowed"))
            for p in rbac.POLICY
            if p.get("path") == "/api/v1/products/brand-options"
        ]
        assert rows == [("GET", "/api/v1/products/brand-options", "AUTHENTICATED")]

    def test_endpoint_readable_by_staff_and_fail_soft(self, client, staff_headers):
        r = client.get("/api/v1/products/brand-options?category=FR", headers=staff_headers)
        assert r.status_code == 200
        body = r.json()
        assert "brands" in body and isinstance(body["brands"], list)

    def test_endpoint_requires_auth(self, client):
        r = client.get("/api/v1/products/brand-options")
        assert r.status_code in (401, 403)
