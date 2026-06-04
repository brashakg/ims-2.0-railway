"""
SUPERADMIN Activity Log -- GET /settings/audit-logs enrichment
==============================================================
The audit trail stores only `user_id`; the activity-log screen needs to read
"who did what" by NAME and show field-level old->new changes. These tests cover
the two pure-ish enrichers + the endpoint wiring (no mongod needed -- fakes).
"""

import asyncio

from api.routers import settings as settings_mod
import api.dependencies as deps


# --- _audit_changes (pure) --------------------------------------------------

def test_audit_changes_before_after_diff_only_changed_keys():
    log = {
        "before_state": {"mrp": 100, "name": "A", "qty": 5},
        "after_state": {"mrp": 90, "name": "A", "qty": 3},
    }
    changes = settings_mod._audit_changes(log)
    assert changes == [
        {"field": "mrp", "old_value": 100, "new_value": 90},
        {"field": "qty", "old_value": 5, "new_value": 3},
    ]


def test_audit_changes_scalar_prev_new_pair():
    log = {"entity_type": "order", "previous_value": "DRAFT", "new_value": "CONFIRMED"}
    assert settings_mod._audit_changes(log) == [
        {"field": "order", "old_value": "DRAFT", "new_value": "CONFIRMED"}
    ]


def test_audit_changes_none_when_unstructured():
    # free-text only -> None (FE renders the description instead)
    assert settings_mod._audit_changes({"description": "did a thing"}) is None
    # whole-object prev/new (not scalar) -> None, not a noisy [object Object]
    assert (
        settings_mod._audit_changes({"previous_value": {"a": 1}, "new_value": {"a": 2}})
        is None
    )


# --- _resolve_user_names (batched lookup, fail-soft) ------------------------

class _FakeUserRepo:
    def find_many(self, filt, limit=None):
        ids = filt["user_id"]["$in"]
        data = {
            "u1": {"user_id": "u1", "full_name": "Asha Rao"},
            "u2": {"user_id": "u2", "username": "cashier2"},  # no full_name
        }
        return [data[i] for i in ids if i in data]


def test_resolve_user_names_maps_and_skips_unknown(monkeypatch):
    monkeypatch.setattr(deps, "get_user_repository", lambda: _FakeUserRepo())
    names = settings_mod._resolve_user_names({"u1", "u2", "u3"})
    assert names["u1"] == "Asha Rao"
    assert names["u2"] == "cashier2"  # falls back to username
    assert "u3" not in names  # unknown id stays unresolved


def test_resolve_user_names_empty_and_failsoft(monkeypatch):
    assert settings_mod._resolve_user_names(set()) == {}
    monkeypatch.setattr(deps, "get_user_repository", lambda: None)
    assert settings_mod._resolve_user_names({"u1"}) == {}


# --- endpoint wiring --------------------------------------------------------

class _FakeAuditRepo:
    def find_many(self, filt, sort=None, skip=0, limit=50):
        return [
            {
                "_id": "x",
                "log_id": "L1",
                "user_id": "u1",
                "action": "UPDATE",
                "entity_type": "product",
                "before_state": {"mrp": 100},
                "after_state": {"mrp": 90},
            },
            {"_id": "y", "log_id": "L2", "user_id": "uX", "action": "LOGIN"},
        ]

    def count(self, filt):
        return 2


def test_get_audit_logs_enriches_names_and_changes(monkeypatch):
    # get_audit_repository is imported at settings module top -> patch there.
    monkeypatch.setattr(settings_mod, "get_audit_repository", lambda: _FakeAuditRepo())
    # get_user_repository is lazy-imported inside the helper -> patch on deps.
    monkeypatch.setattr(deps, "get_user_repository", lambda: _FakeUserRepo())

    res = asyncio.run(
        settings_mod.get_audit_logs(current_user={"roles": ["SUPERADMIN"]})
    )
    logs = res["logs"]
    assert res["total"] == 2
    # actor resolved to a readable name; _id stripped
    assert logs[0]["user_name"] == "Asha Rao"
    assert logs[0]["username"] == "Asha Rao"
    assert "_id" not in logs[0]
    # structured change surfaced for the detail panel
    assert logs[0]["changes"] == [{"field": "mrp", "old_value": 100, "new_value": 90}]
    # unresolved actor degrades gracefully (no fabricated name)
    assert "user_name" not in logs[1]


def test_get_audit_logs_requires_superadmin_or_admin():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(settings_mod.get_audit_logs(current_user={"roles": ["CASHIER"]}))
    assert ei.value.status_code == 403


# --- summary endpoint -------------------------------------------------------

class _SummaryAuditRepo:
    def count(self, filt):
        # login query carries an explicit action; the total query does not
        return 3 if filt.get("action") == "login_success" else 17


class _SummaryOrderRepo:
    def count(self, filt):
        return 5


def test_get_audit_logs_summary_counts(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_audit_repository", lambda: _SummaryAuditRepo())
    monkeypatch.setattr(deps, "get_order_repository", lambda: _SummaryOrderRepo())
    res = asyncio.run(
        settings_mod.get_audit_logs_summary(current_user={"roles": ["ADMIN"]})
    )
    assert res["today"]["total_actions"] == 17
    assert res["today"]["logins"] == 3
    assert res["today"]["orders_created"] == 5  # from the orders collection, not audit


def test_get_audit_logs_summary_requires_role():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            settings_mod.get_audit_logs_summary(current_user={"roles": ["SALES_STAFF"]})
        )
    assert ei.value.status_code == 403
