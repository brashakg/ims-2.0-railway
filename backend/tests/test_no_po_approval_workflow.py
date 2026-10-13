"""
Owner ruling 2026-09-28: a purchase order has NO approval step -- it is
drafted, then sent to the vendor. Settings > Approval Workflows still offered a
'Purchase Order Approval' workflow (on, above Rs 50,000) that no code reads,
promising a step that does not exist. It is gone from the defaults, and a copy
an admin saved earlier is not shown either.
"""

import api.routers.settings as settings_mod
from strict_fakes import StrictCollection

_PO_APPROVAL = {
    "id": "wf-003",
    "type": "PO_APPROVAL",
    "name": "Purchase Order Approval",
    "description": "Purchase orders exceeding the configured amount threshold require approval.",
    "isEnabled": True,
    "thresholdType": "AMOUNT",
    "thresholdValue": 50000,
    "approverRoles": ["SUPERADMIN", "ADMIN", "AREA_MANAGER"],
}


def _types(client, headers):
    resp = client.get("/api/v1/settings/approval-workflows", headers=headers)
    assert resp.status_code == 200, resp.text
    return [w["type"] for w in resp.json()["workflows"]]


def test_the_defaults_offer_no_po_approval(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings_mod, "_get_settings_collection", lambda _name: None)
    types = _types(client, auth_headers)
    assert "PO_APPROVAL" not in types
    assert "DISCOUNT_APPROVAL" in types  # the real workflows stay


def test_a_saved_po_approval_is_not_shown(client, auth_headers, monkeypatch):
    discount = {**_PO_APPROVAL, "id": "wf-001", "type": "DISCOUNT_APPROVAL"}
    coll = StrictCollection(
        "approval_workflows", [{"_id": "default", "workflows": [discount, _PO_APPROVAL]}]
    )
    monkeypatch.setattr(settings_mod, "_get_settings_collection", lambda _name: coll)
    assert _types(client, auth_headers) == ["DISCOUNT_APPROVAL"]
