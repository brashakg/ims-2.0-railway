"""BUG-116a (patient-safety): the generic PATCH /jobs/{id}/status must NOT let a
job reach READY-for-pickup without a QC record. COMPLETED->READY requires
qc_passed or qc_waived; the QC-fail path (->QC_FAILED) stays open."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _Repo:
    def __init__(self, job):
        self._job = job
        self.updated = None

    def find_by_id(self, jid):
        return self._job if self._job and self._job.get("job_id") == jid else None

    def update_status(self, jid, status, uid, notes=None):
        self.updated = status
        return True


def _client(monkeypatch, job):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    repo = _Repo(job)
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: repo)

    async def _u():
        return {"user_id": "u1", "roles": ["WORKSHOP_STAFF"], "active_store_id": "BV-TEST-01"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app), repo


def _job(**kw):
    d = {"job_id": "J1", "status": "COMPLETED", "store_id": "BV-TEST-01"}
    d.update(kw)
    return d


def test_ready_blocked_without_qc(monkeypatch):
    c, repo = _client(monkeypatch, _job())  # no qc_passed / qc_waived
    r = c.patch("/workshop/jobs/J1/status", json={"status": "READY"})
    assert r.status_code == 400, r.text
    assert repo.updated is None  # the status write never happened


def test_ready_allowed_when_qc_passed(monkeypatch):
    c, repo = _client(monkeypatch, _job(qc_passed=True))
    r = c.patch("/workshop/jobs/J1/status", json={"status": "READY"})
    assert r.status_code == 200, r.text
    assert repo.updated == "READY"


def test_ready_allowed_when_qc_waived(monkeypatch):
    c, repo = _client(monkeypatch, _job(qc_waived=True))
    r = c.patch("/workshop/jobs/J1/status", json={"status": "READY"})
    assert r.status_code == 200, r.text
    assert repo.updated == "READY"


def test_qc_failed_path_open_without_qc(monkeypatch):
    # COMPLETED -> QC_FAILED is the fail route; it must NOT require qc_passed.
    c, repo = _client(monkeypatch, _job())
    r = c.patch("/workshop/jobs/J1/status", json={"status": "QC_FAILED"})
    assert r.status_code == 200, r.text
    assert repo.updated == "QC_FAILED"
