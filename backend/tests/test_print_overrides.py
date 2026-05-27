"""
IMS 2.0 - Tests for backend/api/routers/print_overrides.py
==========================================================

Direct handler-call tests (no HTTP client). Uses a hand-rolled fake Mongo
collection so the CRUD flow exercises the real router code without any DB.
Covers:
  - upsert + list + get + delete round-trip
  - PUT same (entity_id, template_key) twice -> single row (updated, not
    duplicated) -> the editor's "save" is idempotent
  - invalid template_key -> 400 (with allowed list)
  - missing entity_id -> 422
  - empty-string fields are scrubbed on write
  - GET on a missing (entity, template) returns the empty envelope with
    exists=False (so the editor pre-populates from defaults cleanly)
  - role gate via _require_print_admin -> CASHIER 403
  - DELETE on a missing row reports deleted=False (idempotent)
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import print_overrides  # noqa: E402
from api.routers.print_overrides import (  # noqa: E402
    OverrideFields,
    OverridePut,
)


# ---------------------------------------------------------------------------
# Fake Mongo collection
# ---------------------------------------------------------------------------


class _FakeColl:
    """Minimal stand-in supporting find / find_one / insert_one / update_one /
    delete_one with the {key: value} query shape used by the router."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    def _match(self, d: Dict[str, Any], q: Dict[str, Any]) -> bool:
        return all(d.get(k) == v for k, v in q.items())

    def find(self, q: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        q = q or {}
        return [dict(d) for d in self.docs if self._match(d, q)]

    def find_one(self, q: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    def insert_one(self, doc: Dict[str, Any]) -> Any:
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def update_one(self, flt: Dict[str, Any], update: Dict[str, Any]) -> Any:
        for d in self.docs:
            if self._match(d, flt):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def delete_one(self, flt: Dict[str, Any]) -> Any:
        for i, d in enumerate(self.docs):
            if self._match(d, flt):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()


@pytest.fixture
def fake_coll(monkeypatch):
    coll = _FakeColl()
    monkeypatch.setattr(print_overrides, "_coll", lambda: coll)
    return coll


# ---------------------------------------------------------------------------
# Test users
# ---------------------------------------------------------------------------


def _admin_user() -> Dict[str, Any]:
    return {
        "user_id": "u-admin-001",
        "username": "admin",
        "roles": ["ADMIN"],
        "active_store_id": "BV-TEST-01",
    }


def _superadmin_user() -> Dict[str, Any]:
    return {
        "user_id": "u-sa-001",
        "username": "superadmin",
        "roles": ["SUPERADMIN"],
        "active_store_id": "BV-TEST-01",
    }


def _cashier_user() -> Dict[str, Any]:
    return {
        "user_id": "u-cash-001",
        "username": "cashier1",
        "roles": ["CASHIER"],
        "active_store_id": "BV-TEST-01",
    }


def _store_manager_user() -> Dict[str, Any]:
    return {
        "user_id": "u-mgr-001",
        "username": "mgr1",
        "roles": ["STORE_MANAGER"],
        "active_store_id": "BV-TEST-01",
    }


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


def test_put_creates_row(fake_coll):
    payload = OverridePut(
        fields=OverrideFields(
            signatory_name="A. K. Goyal",
            signatory_designation="Director",
            retention_years=7,
        )
    )
    out = asyncio.run(
        print_overrides.upsert_override(
            entity_id="ent-bv-001",
            template_key="tax_invoice",
            payload=payload,
            current_user=_admin_user(),
        )
    )
    assert out["entity_id"] == "ent-bv-001"
    assert out["template_key"] == "tax_invoice"
    assert out["fields"]["signatory_name"] == "A. K. Goyal"
    assert out["fields"]["signatory_designation"] == "Director"
    assert out["fields"]["retention_years"] == 7
    assert "created_at" in out
    assert out["created_by"] == "admin"
    assert out["exists"] is True
    assert len(fake_coll.docs) == 1


def test_put_twice_updates_not_duplicates(fake_coll):
    p1 = OverridePut(fields=OverrideFields(signatory_name="A. K. Goyal"))
    p2 = OverridePut(fields=OverrideFields(signatory_name="A. Kumar Goyal"))
    asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001", "tax_invoice", p1, current_user=_admin_user()
        )
    )
    out2 = asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001", "tax_invoice", p2, current_user=_superadmin_user()
        )
    )
    assert len(fake_coll.docs) == 1
    assert out2["fields"]["signatory_name"] == "A. Kumar Goyal"
    assert out2["updated_by"] == "superadmin"


def test_get_existing_row(fake_coll):
    asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001",
            "rx_card",
            OverridePut(fields=OverrideFields(ncahp_uid="OPT-IN-22-04412")),
            current_user=_admin_user(),
        )
    )
    out = asyncio.run(
        print_overrides.get_override(
            "ent-bv-001", "rx_card", current_user=_store_manager_user()
        )
    )
    assert out["exists"] is True
    assert out["fields"]["ncahp_uid"] == "OPT-IN-22-04412"


def test_get_missing_row_returns_empty_envelope(fake_coll):
    out = asyncio.run(
        print_overrides.get_override(
            "ent-wiz-001", "z_report", current_user=_store_manager_user()
        )
    )
    assert out["exists"] is False
    assert out["fields"] == {}
    assert out["entity_id"] == "ent-wiz-001"
    assert out["template_key"] == "z_report"


def test_list_returns_all_overrides_for_entity(fake_coll):
    asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001",
            "tax_invoice",
            OverridePut(fields=OverrideFields(signatory_name="A")),
            current_user=_admin_user(),
        )
    )
    asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001",
            "grn",
            OverridePut(fields=OverrideFields(signatory_name="B")),
            current_user=_admin_user(),
        )
    )
    # Another entity -- must not leak
    asyncio.run(
        print_overrides.upsert_override(
            "ent-wiz-001",
            "tax_invoice",
            OverridePut(fields=OverrideFields(signatory_name="C")),
            current_user=_admin_user(),
        )
    )
    out = asyncio.run(
        print_overrides.list_overrides(
            entity_id="ent-bv-001", current_user=_store_manager_user()
        )
    )
    assert out["total"] == 2
    keys = sorted(r["template_key"] for r in out["overrides"])
    assert keys == ["grn", "tax_invoice"]


def test_delete_removes_row(fake_coll):
    asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001",
            "tax_invoice",
            OverridePut(fields=OverrideFields(signatory_name="A")),
            current_user=_admin_user(),
        )
    )
    out = asyncio.run(
        print_overrides.delete_override(
            "ent-bv-001", "tax_invoice", current_user=_admin_user()
        )
    )
    assert out["deleted"] is True
    assert fake_coll.docs == []
    # Idempotent: deleting again says deleted=False
    out2 = asyncio.run(
        print_overrides.delete_override(
            "ent-bv-001", "tax_invoice", current_user=_admin_user()
        )
    )
    assert out2["deleted"] is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_invalid_template_key_rejected(fake_coll):
    payload = OverridePut(fields=OverrideFields(signatory_name="A"))
    with pytest.raises(Exception) as exc:
        asyncio.run(
            print_overrides.upsert_override(
                "ent-bv-001",
                "po",  # not in TEMPLATE_KEYS
                payload,
                current_user=_admin_user(),
            )
        )
    assert getattr(exc.value, "status_code", None) == 400
    assert "tax_invoice" in str(exc.value.detail)


def test_missing_entity_id_rejected(fake_coll):
    payload = OverridePut(fields=OverrideFields(signatory_name="A"))
    with pytest.raises(Exception) as exc:
        asyncio.run(
            print_overrides.upsert_override(
                "",
                "tax_invoice",
                payload,
                current_user=_admin_user(),
            )
        )
    assert getattr(exc.value, "status_code", None) == 422


def test_empty_string_fields_scrubbed_on_write(fake_coll):
    # signatory_name is intentionally "" -- it should not land in storage.
    payload = OverridePut(
        fields=OverrideFields(signatory_name="", signatory_designation="Director")
    )
    out = asyncio.run(
        print_overrides.upsert_override(
            "ent-bv-001", "tax_invoice", payload, current_user=_admin_user()
        )
    )
    assert "signatory_name" not in out["fields"]
    assert out["fields"]["signatory_designation"] == "Director"


# ---------------------------------------------------------------------------
# Role gate
# ---------------------------------------------------------------------------


def test_cashier_cannot_write():
    with pytest.raises(Exception) as exc:
        asyncio.run(print_overrides._require_print_admin(current_user=_cashier_user()))
    assert getattr(exc.value, "status_code", None) == 403


def test_store_manager_cannot_write():
    with pytest.raises(Exception) as exc:
        asyncio.run(
            print_overrides._require_print_admin(current_user=_store_manager_user())
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_admin_can_write():
    out = asyncio.run(print_overrides._require_print_admin(current_user=_admin_user()))
    assert out["username"] == "admin"


def test_superadmin_can_write():
    out = asyncio.run(
        print_overrides._require_print_admin(current_user=_superadmin_user())
    )
    assert out["username"] == "superadmin"


# ---------------------------------------------------------------------------
# DB down behaviour
# ---------------------------------------------------------------------------


def test_db_down_get_returns_empty_envelope(monkeypatch):
    monkeypatch.setattr(print_overrides, "_coll", lambda: None)
    out = asyncio.run(
        print_overrides.get_override(
            "ent-bv-001", "tax_invoice", current_user=_admin_user()
        )
    )
    assert out["exists"] is False
    assert out["fields"] == {}


def test_db_down_list_returns_empty_envelope(monkeypatch):
    monkeypatch.setattr(print_overrides, "_coll", lambda: None)
    out = asyncio.run(
        print_overrides.list_overrides(
            entity_id="ent-bv-001", current_user=_admin_user()
        )
    )
    assert out["total"] == 0
    assert out["overrides"] == []


def test_db_down_put_503(monkeypatch):
    monkeypatch.setattr(print_overrides, "_coll", lambda: None)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            print_overrides.upsert_override(
                "ent-bv-001",
                "tax_invoice",
                OverridePut(fields=OverrideFields(signatory_name="A")),
                current_user=_admin_user(),
            )
        )
    assert getattr(exc.value, "status_code", None) == 503


# ---------------------------------------------------------------------------
# Meta endpoint
# ---------------------------------------------------------------------------


def test_meta_templates_lists_keys(fake_coll):
    out = asyncio.run(
        print_overrides.list_template_keys(current_user=_admin_user())
    )
    keys = sorted(t["key"] for t in out["templates"])
    assert keys == sorted(
        ["tax_invoice", "thermal_receipt", "rx_card", "job_card", "grn", "z_report"]
    )
    field_names = {f["name"] for f in out["fields"]}
    for must in (
        "header_subtitle",
        "declaration_text",
        "signatory_name",
        "drug_licence_no",
        "ncahp_uid",
        "footer_terms",
        "retention_years",
    ):
        assert must in field_names
