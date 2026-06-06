"""NEW-IDOR-WALKOUT: a walkout carries customer PII (name + mobile). GET
/walkouts/{id} must existence-hide (404) a walkout whose store the caller can't
access; own-store + admin pass. Mirrors NEW-IDOR-LABEL."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import walkouts as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


_WALK = {
    "walkout_id": "W1",
    "store_id": "BV-BOK-01",
    "customer_name": "Secret Shopper",
    "customer_mobile": "9100000000",
    "status": "OPEN",
}


class _Repo:
    def find_by_walkout_id(self, wid):
        return _WALK if wid == _WALK["walkout_id"] else None


def _client(monkeypatch, roles, store):
    app = FastAPI()
    app.include_router(wm.router, prefix="/walkouts")
    monkeypatch.setattr(wm, "_walkout_repo", lambda: _Repo())
    # Focus on the store-scope gate, not serialization shape.
    monkeypatch.setattr(wm, "_serialize_walkout", lambda w: {"walkout_id": w.get("walkout_id")})

    async def _u():
        return {"user_id": "u1", "roles": roles, "active_store_id": store, "store_ids": [store]}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_cross_store_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")  # caller PUN, walkout BOK
    assert c.get("/walkouts/W1").status_code == 404


def test_own_store_ok(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")
    assert c.get("/walkouts/W1").status_code == 200


def test_admin_cross_store_ok(monkeypatch):
    c = _client(monkeypatch, ["ADMIN"], "BV-HQ")
    assert c.get("/walkouts/W1").status_code == 200


def test_nonexistent_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")
    assert c.get("/walkouts/nope").status_code == 404
