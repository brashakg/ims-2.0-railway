"""NEW-IDOR-LABEL / by-id sweep: a workshop job carries customer phone + medical
Rx. GET /workshop/jobs/{id} and /workshop/jobs/{id}/label must existence-hide
(404) a job whose store the caller can't access; own-store + admin pass."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import workshop as wm  # noqa: E402
from api.routers import labels as lm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _Repo:
    def __init__(self, job):
        self._job = job

    def find_by_id(self, jid):
        return self._job if self._job and self._job.get("job_id") == jid else None


_JOB = {
    "job_id": "J1",
    "job_number": "WSJ-J1",
    "order_id": "ORD-1",
    "store_id": "BV-BOK-01",
    "status": "IN_PROGRESS",
    "customer_name": "Secret Patient",
    "customer_phone": "9100000000",
    "created_at": "2026-06-01T00:00:00",
    "items": [],
}


def _client(monkeypatch, roles, store):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    app.include_router(lm.router, prefix="")  # labels paths already include /workshop
    repo = _Repo(_JOB)
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(lm, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(lm, "get_prescription_repository", lambda: None)
    monkeypatch.setattr(lm, "get_store_repository", lambda: None)
    monkeypatch.setattr(lm, "get_product_repository", lambda: None)
    monkeypatch.setattr(lm, "get_stock_repository", lambda: None)

    async def _u():
        return {"user_id": "u1", "roles": roles, "active_store_id": store, "store_ids": [store]}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_get_job_cross_store_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")  # caller PUN, job BOK
    assert c.get("/workshop/jobs/J1").status_code == 404


def test_get_job_label_cross_store_404(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-PUN-01")
    assert c.get("/workshop/jobs/J1/label").status_code == 404


def test_get_job_own_store_ok(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")  # caller owns the job's store
    assert c.get("/workshop/jobs/J1").status_code == 200


def test_get_job_label_own_store_ok(monkeypatch):
    c = _client(monkeypatch, ["STORE_MANAGER"], "BV-BOK-01")
    assert c.get("/workshop/jobs/J1/label").status_code == 200


def test_admin_cross_store_ok(monkeypatch):
    c = _client(monkeypatch, ["ADMIN"], "BV-HQ")  # admin reaches any store
    assert c.get("/workshop/jobs/J1").status_code == 200
    assert c.get("/workshop/jobs/J1/label").status_code == 200
