"""SEC-ENTITIES-PII: GET /entities + /entities/{id} must project out corporate
PAN / TAN / bank accounts for non-finance roles (a SALES_STAFF must not read
them); ADMIN / ACCOUNTANT / SUPERADMIN still see the full PII."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import entities as em  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

# Valid Indian PAN / TAN / GSTIN so entities._clean keeps them (GSTIN chars 3-12
# == the PAN; state code 27 matches the GSTIN prefix).
_ENTITY = {
    "entity_id": "E1",
    "name": "Better Vision Optics Pvt Ltd",
    "pan": "ABCDE1234F",
    "tan": "ABCD12345E",
    "gstins": [{"gstin": "27ABCDE1234F1Z5", "state_code": "27"}],
    "bank_accounts": [{"bank_account_no": "1234567890", "ifsc": "HDFC0000001"}],
    "is_active": True,
}


class _Coll:
    def find(self, q=None):
        return [dict(_ENTITY)]

    def find_one(self, q=None):
        return dict(_ENTITY)


class _DB:
    def get_collection(self, name):
        return _Coll()


def _client(monkeypatch, roles):
    app = FastAPI()
    app.include_router(em.router, prefix="/entities")
    monkeypatch.setattr(em, "_get_db", lambda: _DB())

    async def _u():
        return {"user_id": "u1", "roles": roles, "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def test_sales_staff_list_has_no_pii(monkeypatch):
    e = _client(monkeypatch, ["SALES_STAFF"]).get("/entities").json()["entities"][0]
    assert "pan" not in e
    assert "tan" not in e
    assert "bank_accounts" not in e
    assert e.get("name")  # structure still readable


def test_admin_list_sees_pii(monkeypatch):
    e = _client(monkeypatch, ["ADMIN"]).get("/entities").json()["entities"][0]
    assert e.get("pan") == "ABCDE1234F"
    assert e.get("tan") == "ABCD12345E"
    assert e.get("bank_accounts")


def test_sales_staff_get_one_redacted(monkeypatch):
    e = _client(monkeypatch, ["SALES_STAFF"]).get("/entities/E1").json()["entity"]
    assert "pan" not in e and "tan" not in e and "bank_accounts" not in e


def test_accountant_get_one_sees_pii(monkeypatch):
    e = _client(monkeypatch, ["ACCOUNTANT"]).get("/entities/E1").json()["entity"]
    assert e.get("pan") == "ABCDE1234F"
