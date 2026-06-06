"""NEW-IDOR-PAYOUT: payout snapshots carry per-store payout money. GET
/payout/snapshot/{id}, /payout/export/{id}.csv and PATCH .../mark-paid must
existence-hide (404) a snapshot whose store the caller can't access. Mirrors the
NEW-IDOR-LABEL pattern."""
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


def test_get_snapshot_cross_store_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")  # caller PUN, snap BOK
    assert c.get("/payout/snapshot/snap_a").status_code == 404


def test_export_csv_cross_store_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")
    assert c.get("/payout/export/snap_a.csv").status_code == 404


def test_get_snapshot_own_store_ok(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")  # caller owns the store
    assert c.get("/payout/snapshot/snap_a").status_code == 200


def test_admin_cross_store_ok(monkeypatch):
    c = _client(monkeypatch, ["ADMIN"], "BV-HQ")  # global role reaches any store
    assert c.get("/payout/snapshot/snap_a").status_code == 200


def test_nonexistent_snapshot_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")
    assert c.get("/payout/snapshot/nope").status_code == 404


def test_mark_paid_non_superadmin_403(monkeypatch):
    # Role gate fires before the store check; a manager can never mark-paid.
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")
    assert c.patch("/payout/snapshot/snap_a/mark-paid", json={}).status_code == 403
