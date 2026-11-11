"""
Audit L1-F14: "Backup Database" must not report a backup that does not exist.

Reproduction: Settings > System > Backup Database showed "Backup created
successfully". The API behind it (POST /api/v1/admin/system/backups) answered
202 {"status": "requested", "note": "Snapshot handled by hosting provider..."}
after inserting a timestamp row. No dump, snapshot or provider call happens
anywhere, and nothing ever picks that row up.

The app cannot take a database backup, so the endpoint now says exactly that
(501, like its sibling restore / export endpoints) and writes nothing. This
test builds no backup and touches no real database.

Run: JWT_SECRET_KEY=test python -m pytest \
       backend/tests/test_audit_b04_backup_truth.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.routers import admin_extras  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402

_PATH = "/api/v1/admin/system/backups"


@pytest.fixture
def backups(monkeypatch):
    """The `backups` collection the endpoint would write its marker to."""
    coll = mongomock.MongoClient()["audit_b04_backup"]["backups"]
    real = admin_extras._coll
    monkeypatch.setattr(
        admin_extras, "_coll", lambda name: coll if name == "backups" else real(name)
    )
    return coll


def test_backup_request_is_refused_as_unsupported_and_records_nothing(
    client, auth_headers, backups
):
    r = client.post(_PATH, headers=auth_headers)

    assert r.status_code == 501, r.text
    detail = r.json()["detail"]
    assert "cannot create a database backup" in detail
    # No wording that a backup, a snapshot or a pending request now exists.
    body = r.text.lower()
    for claim in ("requested", "created", "success", "handled by"):
        assert claim not in body, claim
    assert "backup_id" not in r.json()
    assert backups.count_documents({}) == 0, "no marker row may be written"


def test_backup_list_makes_no_claim_that_backups_are_being_taken(
    client, auth_headers, backups
):
    r = client.get(_PATH, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["backups"] == []
    assert "does not create" in body["note"]


def test_old_request_markers_are_listed_as_what_they_are(client, auth_headers, backups):
    """Rows the old button wrote stay on file, shown as requests - never as
    completed backups."""
    backups.insert_one({"_id": "b1", "backup_id": "b1", "status": "requested",
                        "requested_at": "2026-11-01T00:00:00+00:00"})
    body = client.get(_PATH, headers=auth_headers).json()
    assert [b["status"] for b in body["backups"]] == ["requested"]
    assert "does not create" in body["note"]


def test_backup_routes_keep_their_admin_gate(client, staff_headers):
    assert sorted(rbac.policy_for("POST", _PATH)["allowed"]) == ["ADMIN", "SUPERADMIN"]
    assert client.post(_PATH, headers=staff_headers).status_code == 403
    assert client.post(_PATH).status_code in (401, 403)
