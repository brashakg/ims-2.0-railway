"""NEW-IDOR-PAYOUT: payout snapshots carry per-store payout money. GET
/payout/snapshot/{id}, /payout/export/{id}.csv and PATCH .../mark-paid must
existence-hide (404) a snapshot whose store the caller can't access. Mirrors the
NEW-IDOR-LABEL pattern.

UPDATED 2026-08-13 (owner decision: payout reads are ADMIN / SUPERADMIN only).
These tests used STORE_MANAGER as the "entitled reader" to reach the store-scope
branch. That role is now refused at the ROLE gate, one layer earlier, so the
cross-store 404 branch (payout.py can_access_store_scoped) is currently
UNREACHABLE: the only roles that get past _check_view_permission are ADMIN and
SUPERADMIN, and user_store_scope reports both as cross-store.

The guard is kept anyway -- it is correct, it costs nothing, and it is what
holds if _VIEW_ROLES is ever widened again. But these tests now assert what is
actually true today (role gate first, admins reach any store) rather than
pretending to exercise a branch no request can enter. Do not read a green run
here as coverage of the store-scope branch; it is coverage of the role gate.
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import payout as pm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


_SNAP = {
    "_id": "snap_a",
    "snapshot_id": "snap_a",
    "store_id": "BV-BOK-01",
    "year": 2026,
    "month": 5,
    "status": "LOCKED",
    "grand_total": {"staff": 1000.0, "manager": 500.0, "all": 1500.0},
    "staff_payouts": [],
    "manager_bonuses": [],
}


class _Repo:
    def find_by_id(self, sid):
        return _SNAP if sid == _SNAP["snapshot_id"] else None


def _client(monkeypatch, roles, store):
    app = FastAPI()
    app.include_router(pm.router, prefix="/payout")
    monkeypatch.setattr(pm, "_snapshot_repo", lambda: _Repo())

    async def _u():
        return {"user_id": "u1", "roles": roles, "active_store_id": store, "store_ids": [store]}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_store_manager_is_refused_for_its_own_store_too(monkeypatch):
    """Stricter than the IDOR rule this file was written for: a store manager is
    now refused a payout even for a store they own, because the body names every
    colleague and states their incentive rupees."""
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")  # caller OWNS the store
    r = c.get("/payout/snapshot/snap_a")
    assert r.status_code == 403, r.text
    assert "1500" not in r.text and "1000" not in r.text


def test_store_manager_cross_store_is_refused(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")  # caller PUN, snap BOK
    assert c.get("/payout/snapshot/snap_a").status_code == 403


def test_export_csv_is_refused_for_a_store_manager(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")
    assert c.get("/payout/export/snap_a.csv").status_code == 403


def test_accountant_is_refused(monkeypatch):
    """ACCOUNTANT used to be inside _VIEW_ROLES. The owner declined the
    accountant carve-out, so this is now the same refusal."""
    c = _client(monkeypatch, ["ACCOUNTANT"], "BV-BOK-01")
    assert c.get("/payout/snapshot/snap_a").status_code == 403


def test_admin_cross_store_ok(monkeypatch):
    c = _client(monkeypatch, ["ADMIN"], "BV-HQ")  # global role reaches any store
    assert c.get("/payout/snapshot/snap_a").status_code == 200


def test_nonexistent_snapshot_404_for_an_admin(monkeypatch):
    """404 for a real reader -- proves the not-found path still works and was
    not swallowed by the new role gate."""
    c = _client(monkeypatch, ["ADMIN"], "BV-BOK-01")
    assert c.get("/payout/snapshot/nope").status_code == 404


def test_mark_paid_non_superadmin_403(monkeypatch):
    # Role gate fires before the store check; a manager can never mark-paid.
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")
    assert c.patch("/payout/snapshot/snap_a/mark-paid", json={}).status_code == 403
