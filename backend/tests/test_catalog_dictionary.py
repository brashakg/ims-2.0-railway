"""
IMS 2.0 - Catalog Dictionary (settings-managed allowed values per field)
========================================================================
Covers the owner rule "only the values I save in Settings can be chosen in
Catalog":

  1. catalog_dictionary.normalize_items / match_canonical pure helpers.
  2. Loaders against a fake db (field options + Brand-Master brand filter).
  3. product_master.enforce_dictionary_values: brand gate (Brand Master,
     fail-open when empty/unreadable), per-field lists (reject + canonicalise
     casing), brand-managed fields ignored in the dictionary.
  4. normalise_payload runs the enforcement inside the create door.
  5. Router: GET open to authenticated users; PATCH rejects brand_name
     (Brand Master owns it) and normalizes items; RBAC rows catalogued.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_catalog_dictionary.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import catalog_dictionary as cd  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------


class TestNormalizeItems:
    def test_trims_dedupes_case_insensitively(self):
        out = cd.normalize_items(["  Acetate ", "acetate", "Metal", "", None, "TR-90"])
        assert out == ["Acetate", "Metal", "TR-90"]

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            cd.normalize_items("Acetate")

    def test_rejects_overlong_value(self):
        with pytest.raises(ValueError):
            cd.normalize_items(["x" * (cd.MAX_VALUE_LENGTH + 1)])


class TestMatchCanonical:
    def test_case_insensitive_returns_configured_casing(self):
        assert cd.match_canonical("  ray-ban ", ["Ray-Ban", "Oakley"]) == "Ray-Ban"

    def test_no_match_returns_none(self):
        assert cd.match_canonical("Nobody", ["Ray-Ban"]) is None
        assert cd.match_canonical("", ["Ray-Ban"]) is None


# ---------------------------------------------------------------------------
# 2. Loaders against a fake db
# ---------------------------------------------------------------------------


class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    def find(self, _query=None):
        return list(self._docs)


class _FakeDb:
    def __init__(self, colls):
        self._colls = colls

    def get_collection(self, name):
        return _FakeColl(self._colls.get(name, []))


class TestLoaders:
    def test_field_options_drops_blank_and_empty_lists(self):
        db = _FakeDb({
            cd.FIELD_OPTIONS_COLLECTION: [
                {"field_id": "frame_material", "items": ["Acetate", " ", "Metal"]},
                {"field_id": "tint", "items": []},  # saved-but-empty = unconfigured
                {"field_id": "", "items": ["x"]},
            ]
        })
        assert cd.load_field_options(db) == {"frame_material": ["Acetate", "Metal"]}

    def test_field_options_fail_soft(self):
        assert cd.load_field_options(None) == {}

        class _Boom:
            def get_collection(self, name):
                raise RuntimeError("down")

        assert cd.load_field_options(_Boom()) == {}

    def test_brand_options_filters_by_category_prefix(self):
        db = _FakeDb({
            cd.BRAND_COLLECTION: [
                {"name": "Ray-Ban", "categories": ["SG", "FR"], "is_active": True},
                {"name": "Titan", "categories": ["WT"], "is_active": True},
                {"name": "HouseBrand", "categories": []},  # empty = all categories
                {"name": "Old", "categories": ["SG"], "is_active": False},
            ]
        })
        assert cd.load_brand_options(db, "SG") == ["HouseBrand", "Ray-Ban"]
        assert cd.load_brand_options(db, "WT") == ["HouseBrand", "Titan"]

    def test_brand_options_none_on_failure_empty_list_on_empty_master(self):
        assert cd.load_brand_options(None, "SG") is None
        assert cd.load_brand_options(_FakeDb({}), "SG") == []


# ---------------------------------------------------------------------------
# 3. enforce_dictionary_values
# ---------------------------------------------------------------------------


_DB = object()  # sentinel: "a db is present"; loaders are monkeypatched


class TestEnforcement:
    def test_unknown_brand_rejected_with_brand_master_message(self, monkeypatch):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: ["Ray-Ban", "Oakley"])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {})
        with pytest.raises(pm.ProductMasterError) as exc:
            pm.enforce_dictionary_values(
                "FRAME", {"brand_name": "Nobody", "model_no": "X1"}, db=_DB
            )
        assert exc.value.status == 422
        assert exc.value.field == "brand_name"
        assert "Brand Master" in exc.value.message

    def test_brand_canonicalised_to_master_casing(self, monkeypatch):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: ["Ray-Ban"])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {})
        out = pm.enforce_dictionary_values("FRAME", {"brand_name": "ray-ban"}, db=_DB)
        assert out["brand_name"] == "Ray-Ban"

    def test_empty_brand_master_fails_open(self, monkeypatch):
        # [] (no brands yet) and None (read failed) both skip enforcement so
        # cataloguing is never bricked before the owner seeds the master.
        monkeypatch.setattr(cd, "load_field_options", lambda db: {})
        for master in ([], None):
            monkeypatch.setattr(cd, "load_brand_options", lambda db, p, m=master: m)
            out = pm.enforce_dictionary_values("FRAME", {"brand_name": "Anything"}, db=_DB)
            assert out["brand_name"] == "Anything"

    def test_configured_field_rejects_and_canonicalises(self, monkeypatch):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: [])
        monkeypatch.setattr(
            cd, "load_field_options", lambda db: {"frame_material": ["Acetate", "Metal"]}
        )
        # Wrong value -> 422 naming the field + the Settings location.
        with pytest.raises(pm.ProductMasterError) as exc:
            pm.enforce_dictionary_values("FRAME", {"frame_material": "Wood"}, db=_DB)
        assert exc.value.status == 422
        assert exc.value.field == "frame_material"
        assert "Catalog Dictionary" in exc.value.message
        # Case-insensitive value -> canonicalised.
        out = pm.enforce_dictionary_values("FRAME", {"frame_material": "acetate"}, db=_DB)
        assert out["frame_material"] == "Acetate"

    def test_unconfigured_and_blank_values_pass(self, monkeypatch):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: [])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {"tint": ["Grey"]})
        out = pm.enforce_dictionary_values(
            "FRAME", {"shape": "FreeText", "tint": "  "}, db=_DB
        )
        assert out["shape"] == "FreeText"

    def test_brand_managed_keys_in_dictionary_are_ignored(self, monkeypatch):
        # Even if a brand_name list sneaks into the collection, the Brand
        # Master (here: empty -> fail-open) governs brands, not the dictionary.
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: [])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {"brand_name": ["X"]})
        out = pm.enforce_dictionary_values("FRAME", {"brand_name": "Ray-Ban"}, db=_DB)
        assert out["brand_name"] == "Ray-Ban"

    def test_no_db_passthrough(self):
        out = pm.enforce_dictionary_values("FRAME", {"brand_name": "Whatever"}, db=None)
        assert out == {"brand_name": "Whatever"}


# ---------------------------------------------------------------------------
# 4. The create door runs the enforcement
# ---------------------------------------------------------------------------


class TestCreateDoorIntegration:
    def _payload(self):
        return dict(
            category="FRAME",
            attributes={
                "brand_name": "ray-ban",
                "model_no": "RX5154",
                "colour_code": "BLACK",
            },
            mrp=5000,
            offer_price=4500,
            sku="FRTESTSKU1",  # supplied -> no counter/mint against the fake db
            discount_category="PREMIUM",
        )

    def test_normalise_payload_canonicalises_and_rejects(self, monkeypatch):
        monkeypatch.setattr(cd, "load_brand_options", lambda db, p: ["Ray-Ban"])
        monkeypatch.setattr(cd, "load_field_options", lambda db: {})
        doc = pm.normalise_payload(db=_DB, **self._payload())
        assert doc["attributes"]["brand_name"] == "Ray-Ban"
        assert doc["brand"] == "Ray-Ban"  # identity column follows the canonical value

        bad = self._payload()
        bad["attributes"]["brand_name"] = "Nobody"
        with pytest.raises(pm.ProductMasterError):
            pm.normalise_payload(db=_DB, **bad)


# ---------------------------------------------------------------------------
# 5. Router + RBAC
# ---------------------------------------------------------------------------


class TestRouter:
    def test_rbac_rows_catalogued(self):
        rows = [
            (p.get("method"), p.get("path"))
            for p in rbac.POLICY
            if "catalog-field-options" in str(p.get("path"))
        ]
        assert ("GET", "/api/v1/catalog-field-options") in rows
        assert ("PATCH", "/api/v1/catalog-field-options/{field_name}") in rows
        patch_row = next(
            p for p in rbac.POLICY
            if p.get("method") == "PATCH"
            and p.get("path") == "/api/v1/catalog-field-options/{field_name}"
        )
        assert set(patch_row["allowed"]) == {"ADMIN", "CATALOG_MANAGER"}

    def test_patch_refuses_brand_managed_fields(self, client, auth_headers):
        r = client.patch(
            "/api/v1/catalog-field-options/brand_name",
            json={"items": ["Ray-Ban"]},
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "Brand Master" in r.json()["detail"]

    def test_patch_denied_for_sales_staff(self, client, staff_headers):
        r = client.patch(
            "/api/v1/catalog-field-options/frame_material",
            json={"items": ["Acetate"]},
            headers=staff_headers,
        )
        assert r.status_code == 403

    def test_patch_rejects_bad_payload(self, client, auth_headers):
        r = client.patch(
            "/api/v1/catalog-field-options/frame_material",
            json={"items": "Acetate"},
            headers=auth_headers,
        )
        assert r.status_code == 400

    def test_get_requires_auth(self, client):
        r = client.get("/api/v1/catalog-field-options")
        assert r.status_code in (401, 403)
