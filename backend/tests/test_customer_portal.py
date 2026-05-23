"""
IMS 2.0 - customer self-service portal
======================================
OTP hashing/verification, scope-locked token mint/decode (a staff token must
NOT pass), and the public endpoints' anti-enumeration + auth behavior.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import customer_portal_auth as cpa  # noqa: E402
from api.routers import customer_portal as cp  # noqa: E402


def test_generate_otp_is_six_digits():
    for _ in range(20):
        otp = cpa.generate_otp()
        assert len(otp) == 6 and otp.isdigit()


def test_hash_and_verify_otp():
    h = cpa.hash_otp("123456", "9876543210")
    assert cpa.verify_otp("123456", "9876543210", h) is True
    assert cpa.verify_otp("000000", "9876543210", h) is False
    assert cpa.verify_otp("123456", "9999999999", h) is False  # phone-bound
    assert cpa.verify_otp("123456", "9876543210", None) is False


def test_token_roundtrip_and_scope_lock():
    tok = cpa.issue_customer_token("CUST-1", "9876543210")
    payload = cpa.decode_customer_token(tok)
    assert payload and payload["sub"] == "CUST-1" and payload["scope"] == "customer_portal"


def test_staff_token_rejected():
    # A staff-style token (no customer_portal scope) must NOT decode as customer.
    staff = jwt.encode(
        {"sub": "u1", "roles": ["ADMIN"], "exp": datetime.utcnow() + timedelta(hours=1)},
        cpa.SECRET_KEY, algorithm=cpa.ALGORITHM,
    )
    assert cpa.decode_customer_token(staff) is None
    assert cpa.decode_customer_token("garbage") is None
    assert cpa.decode_customer_token(None) is None


def test_expired_token_rejected():
    expired = jwt.encode(
        {"sub": "CUST-1", "scope": "customer_portal", "exp": datetime.utcnow() - timedelta(minutes=1)},
        cpa.SECRET_KEY, algorithm=cpa.ALGORITHM,
    )
    assert cpa.decode_customer_token(expired) is None


def test_otp_expired_helper():
    assert cpa.otp_expired(None) is True
    assert cpa.otp_expired((datetime.utcnow() - timedelta(minutes=1)).isoformat()) is True
    assert cpa.otp_expired((datetime.utcnow() + timedelta(minutes=5)).isoformat()) is False


# ----- endpoint behavior (anti-enumeration + auth) -----

class _FakeCustomerRepo:
    def __init__(self, exists=True):
        self._exists = exists

    def find_by_mobile(self, phone):
        return {"customer_id": "CUST-1", "name": "Asha", "mobile": phone} if self._exists else None


class _FakeRxRepo:
    def find_by_customer(self, cid):
        return [{"prescription_id": "RX1", "customer_id": cid, "right_eye_sph": -1.25,
                 "internal_note": "SECRET", "store_id": "BLR"}]


def _client(monkeypatch, *, customer_exists=True):
    app = FastAPI()
    app.include_router(cp.router, prefix="/customer-portal")
    monkeypatch.setattr(cp, "get_customer_repository", lambda: _FakeCustomerRepo(customer_exists))
    monkeypatch.setattr(cp, "get_prescription_repository", lambda: _FakeRxRepo())
    monkeypatch.setattr(cp, "_otp_coll", lambda: None)  # no DB -> store is a no-op
    return TestClient(app)


def test_request_otp_is_generic(monkeypatch):
    c = _client(monkeypatch, customer_exists=True)
    r1 = c.post("/customer-portal/request-otp", json={"phone": "9876543210"})
    c2 = _client(monkeypatch, customer_exists=False)
    r2 = c2.post("/customer-portal/request-otp", json={"phone": "9000000000"})
    # Identical generic message whether or not the number is registered.
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()


def test_prescriptions_requires_customer_token(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/customer-portal/prescriptions").status_code == 401  # no token
    staff = jwt.encode({"sub": "u1", "roles": ["ADMIN"], "exp": datetime.utcnow() + timedelta(hours=1)},
                       cpa.SECRET_KEY, algorithm=cpa.ALGORITHM)
    assert c.get("/customer-portal/prescriptions", headers={"Authorization": f"Bearer {staff}"}).status_code == 401


def test_prescriptions_returns_sanitized_own_rx(monkeypatch):
    c = _client(monkeypatch)
    tok = cpa.issue_customer_token("CUST-1", "9876543210")
    r = c.get("/customer-portal/prescriptions", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    rx = r.json()["prescriptions"][0]
    assert rx["prescription_id"] == "RX1" and rx["right_eye_sph"] == -1.25
    assert "internal_note" not in rx and "store_id" not in rx  # sanitized
