"""
IMS 2.0 - Customer Self-Service Portal tests
=============================================
Covers the two public surfaces in api/routers/portal.py:

  ORDER TRACKING (public, tokenized)
    - token mint + lookup returns the SAFE subset (status, timeline, items
      as "Brand Category", store name/phone) and NEVER leaks cost / margin /
      salesperson / customer_id.
    - unknown token -> 404.
    - ensure_tracking_token mints + persists a token for a token-less order.

  Rx OTP FLOW (OTP-gated, medical data)
    - request -> verify happy path (we inject/lookup the hashed code).
    - wrong OTP rejected (attempt counter bumps).
    - expired OTP rejected.
    - attempt lockout after N wrong tries.
    - never reveals whether a phone exists (generic success either way).
    - /portal/rx returns ONLY the matching customer's prescriptions; a token
      scoped to customer A cannot read customer B's Rx.

No network, no live Mongo -- a FakeDB / fake repos are injected via
monkeypatch, exactly like test_non_moving_stock.py.
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers import portal as portal_module


# ============================================================================
# Fakes
# ============================================================================


class FakeOrderRepo:
    """Stand-in for OrderRepository -- only the methods portal.py calls."""

    def __init__(self, orders: List[Dict[str, Any]]):
        self._orders = orders

    def find_one(self, flt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        token = flt.get("tracking_token")
        for o in self._orders:
            if o.get("tracking_token") == token:
                return o
        return None

    def find_by_id(self, oid: str) -> Optional[Dict[str, Any]]:
        for o in self._orders:
            if o.get("order_id") == oid:
                return o
        return None

    def update(self, oid: str, data: Dict[str, Any]) -> bool:
        for o in self._orders:
            if o.get("order_id") == oid:
                o.update(data)
                return True
        return False


class FakeStoreRepo:
    def __init__(self, stores: Dict[str, Dict[str, Any]]):
        self._stores = stores

    def find_by_id(self, sid: str) -> Optional[Dict[str, Any]]:
        return self._stores.get(sid)


class FakeCustomerRepo:
    def __init__(self, customers: List[Dict[str, Any]]):
        self._customers = customers

    def find_by_mobile(self, mobile: str) -> Optional[Dict[str, Any]]:
        for c in self._customers:
            if mobile in (c.get("mobile"), c.get("phone")):
                return c
        return None

    def find_by_id(self, cid: str) -> Optional[Dict[str, Any]]:
        for c in self._customers:
            if c.get("customer_id") == cid:
                return c
        return None


class FakeRxRepo:
    def __init__(self, prescriptions: List[Dict[str, Any]]):
        self._rx = prescriptions

    def find_by_customer(self, customer_id: str) -> List[Dict[str, Any]]:
        return [r for r in self._rx if r.get("customer_id") == customer_id]


class FakeCollection:
    """In-memory stand-in for a pymongo collection (otp_codes)."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc: Dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def find_one(self, flt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return d
        return None

    def delete_many(self, flt: Dict[str, Any]) -> None:
        self.docs = [
            d for d in self.docs if not all(d.get(k) == v for k, v in flt.items())
        ]

    def update_one(self, flt: Dict[str, Any], update: Dict[str, Any]) -> None:
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                for k, inc in update.get("$inc", {}).items():
                    d[k] = int(d.get(k, 0) or 0) + inc
                for k, val in update.get("$set", {}).items():
                    d[k] = val
                return


class FakeDB:
    def __init__(self) -> None:
        self.is_connected = True
        self._colls: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        return self._colls.setdefault(name, FakeCollection())


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def fake_db(monkeypatch) -> FakeDB:
    db = FakeDB()
    monkeypatch.setattr(portal_module, "_get_db", lambda: db)
    return db


@pytest.fixture
def patch_repos(monkeypatch):
    """Install fake repos on api.dependencies (portal imports them lazily)."""
    from api import dependencies as deps

    def install(*, orders=None, stores=None, customers=None, prescriptions=None):
        # NB: each lambda must bind its OWN repo (default-arg capture), not a
        # shared loop/closure variable -- otherwise every getter would return
        # the last-assigned repo (late-binding closure bug).
        if orders is not None:
            order_repo = FakeOrderRepo(orders)
            monkeypatch.setattr(deps, "get_order_repository", lambda r=order_repo: r)
        if stores is not None:
            store_repo = FakeStoreRepo(stores)
            monkeypatch.setattr(deps, "get_store_repository", lambda r=store_repo: r)
        if customers is not None:
            cust_repo = FakeCustomerRepo(customers)
            monkeypatch.setattr(deps, "get_customer_repository", lambda r=cust_repo: r)
        if prescriptions is not None:
            rx_repo = FakeRxRepo(prescriptions)
            monkeypatch.setattr(deps, "get_prescription_repository", lambda r=rx_repo: r)

    return install


def _sample_order(token: str = "tok_abcdefghijklmnopqrstuvwxyz") -> Dict[str, Any]:
    return {
        "order_id": "ORD-1",
        "order_number": "ORD-BOK01-2026-A1B2C3",
        "tracking_token": token,
        "store_id": "BV-BOK-01",
        "status": "PROCESSING",
        "customer_id": "CUST-1",
        "customer_name": "Avinash Kumar",
        "customer_phone": "9876543210",
        "salesperson_name": "Staff Person",
        "expected_delivery": "2026-06-05T00:00:00",
        "created_at": "2026-05-29T10:00:00",
        "grand_total": 8900.0,
        "items": [
            {
                "brand": "Ray-Ban",
                "category": "SUNGLASSES",
                "product_name": "Wayfarer RB2140",
                "quantity": 1,
                "unit_price": 7500.0,
                "cost_at_sale": 4000.0,
            },
            {
                "brand": "Zeiss",
                "category": "OPTICAL_LENS",
                "product_name": "Progressive 1.6",
                "quantity": 2,
                "unit_price": 1400.0,
            },
        ],
        "status_history": [
            {"status": "CONFIRMED", "timestamp": "2026-05-29T10:05:00", "changed_by": "u1"},
            {"status": "PROCESSING", "timestamp": "2026-05-30T09:00:00", "changed_by": "u2"},
        ],
    }


# ============================================================================
# ORDER TRACKING
# ============================================================================


class TestOrderTracking:
    def test_track_returns_safe_subset(self, client, patch_repos):
        token = "tok_abcdefghijklmnopqrstuvwxyz"
        patch_repos(
            orders=[_sample_order(token)],
            stores={"BV-BOK-01": {"store_name": "Better Vision Bokaro", "phone": "+91 6542 000001"}},
        )
        resp = client.get(f"/api/v1/portal/track/{token}")
        assert resp.status_code == 200
        body = resp.json()

        # Safe fields present
        assert body["order_number"] == "ORD-BOK01-2026-A1B2C3"
        assert body["status"] == "PROCESSING"
        assert body["store_name"] == "Better Vision Bokaro"
        assert body["store_phone"] == "+91 6542 000001"
        assert body["customer_first_name"] == "Avinash"
        assert body["item_count"] == 3  # 1 + 2
        # Items rendered as "Brand Category"
        descriptions = [it["description"] for it in body["items"]]
        assert "Ray-Ban Sunglass" in descriptions
        assert "Zeiss Spectacle Lens" in descriptions
        # Timeline preserved (status + timestamp), changed_by stripped
        assert len(body["status_history"]) == 2
        assert body["status_history"][0]["status"] == "CONFIRMED"
        assert "changed_by" not in body["status_history"][0]
        assert "changedBy" not in body["status_history"][0]

    def test_track_never_leaks_internal_fields(self, client, patch_repos):
        token = "tok_abcdefghijklmnopqrstuvwxyz"
        patch_repos(orders=[_sample_order(token)], stores={})
        body = client.get(f"/api/v1/portal/track/{token}").json()

        # Whole-payload scan: none of these internal keys may appear anywhere
        import json as _json

        blob = _json.dumps(body)
        for leaked in (
            "cost_at_sale",
            "unit_price",
            "salesperson",
            "customer_id",
            "customer_phone",
            "grand_total",
            "tracking_token",
        ):
            assert leaked not in blob, f"leaked internal field: {leaked}"

    def test_unknown_token_404(self, client, patch_repos):
        patch_repos(orders=[_sample_order("tok_realtokenrealtokenrealtoken")], stores={})
        resp = client.get("/api/v1/portal/track/tok_doesnotexist_000000000000")
        assert resp.status_code == 404

    def test_db_absent_404(self, client, monkeypatch):
        from api import dependencies as deps

        monkeypatch.setattr(deps, "get_order_repository", lambda: None)
        resp = client.get("/api/v1/portal/track/tok_anything_anything_anything")
        assert resp.status_code == 404

    def test_ensure_tracking_token_mints_and_persists(self):
        order = {"order_id": "ORD-9", "order_number": "X"}  # no tracking_token
        repo = FakeOrderRepo([order])
        token = portal_module.ensure_tracking_token(repo, order)
        assert token and len(token) >= 16
        # Persisted back onto the order doc
        assert repo.find_by_id("ORD-9")["tracking_token"] == token
        # Idempotent: an order that already has a token returns it unchanged
        again = portal_module.ensure_tracking_token(repo, repo.find_by_id("ORD-9"))
        assert again == token


# ============================================================================
# Rx OTP FLOW
# ============================================================================


def _stored_otp_plaintext(db: FakeDB, phone_norm: str) -> str:
    """Brute the 6-digit space against the stored hash so the test can learn
    the OTP the endpoint generated (we never store plaintext)."""
    row = db.get_collection("otp_codes").find_one({"phone": phone_norm})
    assert row is not None, "no OTP row was stored"
    target = row["otp_hash"]
    for n in range(1_000_000):
        candidate = f"{n:06d}"
        if hashlib.sha256(f"{candidate}:{phone_norm}".encode()).hexdigest() == target:
            return candidate
    raise AssertionError("could not recover OTP from hash")


class TestRxOtp:
    def test_request_then_verify_happy_path(self, client, fake_db, patch_repos):
        patch_repos(
            customers=[{"customer_id": "CUST-1", "name": "Avinash Kumar", "mobile": "9876543210"}],
        )
        # 1) Request OTP -> generic success, a hashed row is stored
        r1 = client.post("/api/v1/portal/rx/request-otp", json={"phone": "9876543210"})
        assert r1.status_code == 200
        assert r1.json()["ok"] is True

        otp = _stored_otp_plaintext(fake_db, "919876543210")

        # 2) Verify with the correct OTP -> view token issued
        r2 = client.post(
            "/api/v1/portal/rx/verify-otp", json={"phone": "9876543210", "otp": otp}
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["ok"] is True
        assert body["view_token"]
        # The OTP row is consumed (single-use)
        assert fake_db.get_collection("otp_codes").find_one({"phone": "919876543210"}) is None

    def test_request_is_generic_for_unknown_phone(self, client, fake_db, patch_repos):
        """No enumeration: an unknown phone returns the same success envelope
        and stores NO OTP row."""
        patch_repos(customers=[{"customer_id": "CUST-1", "name": "A", "mobile": "9876543210"}])
        resp = client.post("/api/v1/portal/rx/request-otp", json={"phone": "9999999999"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert fake_db.get_collection("otp_codes").find_one({"phone": "919999999999"}) is None

    def test_wrong_otp_rejected_and_bumps_attempts(self, client, fake_db, patch_repos):
        patch_repos(customers=[{"customer_id": "CUST-1", "name": "A", "mobile": "9876543210"}])
        client.post("/api/v1/portal/rx/request-otp", json={"phone": "9876543210"})

        # Recover the real OTP and submit a guaranteed-DIFFERENT code so the
        # test can never accidentally hit the correct one (no 1-in-1e6 flake).
        real = _stored_otp_plaintext(fake_db, "919876543210")
        wrong = f"{(int(real) + 1) % 1_000_000:06d}"

        resp = client.post(
            "/api/v1/portal/rx/verify-otp", json={"phone": "9876543210", "otp": wrong}
        )
        assert resp.status_code == 400
        row = fake_db.get_collection("otp_codes").find_one({"phone": "919876543210"})
        assert row is not None
        assert row["attempts"] == 1

    def test_expired_otp_rejected(self, client, fake_db, patch_repos):
        patch_repos(customers=[{"customer_id": "CUST-1", "name": "A", "mobile": "9876543210"}])
        client.post("/api/v1/portal/rx/request-otp", json={"phone": "9876543210"})
        otp = _stored_otp_plaintext(fake_db, "919876543210")

        # Force the row to be expired
        row = fake_db.get_collection("otp_codes").find_one({"phone": "919876543210"})
        row["expires_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)

        resp = client.post(
            "/api/v1/portal/rx/verify-otp", json={"phone": "9876543210", "otp": otp}
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    def test_attempt_lockout(self, client, fake_db, patch_repos):
        patch_repos(customers=[{"customer_id": "CUST-1", "name": "A", "mobile": "9876543210"}])
        client.post("/api/v1/portal/rx/request-otp", json={"phone": "9876543210"})

        # Hammer a guaranteed-WRONG code; the row allows _OTP_MAX_ATTEMPTS
        # (default 5). Derive the wrong code from the real one so we never
        # accidentally succeed and short-circuit the lockout.
        real = _stored_otp_plaintext(fake_db, "919876543210")
        wrong = f"{(int(real) + 1) % 1_000_000:06d}"
        statuses = []
        for _ in range(portal_module._OTP_MAX_ATTEMPTS + 1):
            r = client.post(
                "/api/v1/portal/rx/verify-otp",
                json={"phone": "9876543210", "otp": wrong},
            )
            statuses.append(r.status_code)
        # The final attempt(s) are locked out with 429
        assert 429 in statuses
        # And the row is purged after lockout
        assert fake_db.get_collection("otp_codes").find_one({"phone": "919876543210"}) is None

    def test_verify_without_request_rejected(self, client, fake_db, patch_repos):
        patch_repos(customers=[])
        resp = client.post(
            "/api/v1/portal/rx/verify-otp", json={"phone": "9876543210", "otp": "123456"}
        )
        assert resp.status_code == 400


# ============================================================================
# /portal/rx  -- scoped to the verified customer only
# ============================================================================


class TestRxView:
    def _get_token(self, client, fake_db, customer_id: str, phone: str) -> str:
        otp = _stored_otp_plaintext(fake_db, portal_module._normalize_phone(phone))
        r = client.post(
            "/api/v1/portal/rx/verify-otp", json={"phone": phone, "otp": otp}
        )
        assert r.status_code == 200, r.text
        return r.json()["view_token"]

    def test_rx_returns_only_my_prescriptions(self, client, fake_db, patch_repos):
        customers = [
            {"customer_id": "CUST-A", "name": "Alice A", "mobile": "9000000001"},
            {"customer_id": "CUST-B", "name": "Bob B", "mobile": "9000000002"},
        ]
        prescriptions = [
            {"prescription_id": "RX-A1", "customer_id": "CUST-A", "right_eye": {"sph": -1.0}},
            {"prescription_id": "RX-A2", "customer_id": "CUST-A", "right_eye": {"sph": -1.25}},
            {"prescription_id": "RX-B1", "customer_id": "CUST-B", "right_eye": {"sph": -2.0}},
        ]
        patch_repos(customers=customers, prescriptions=prescriptions)

        # Verify as customer A
        client.post("/api/v1/portal/rx/request-otp", json={"phone": "9000000001"})
        token_a = self._get_token(client, fake_db, "CUST-A", "9000000001")

        resp = client.get(
            "/api/v1/portal/rx", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["customer_id"] == "CUST-A"
        assert body["count"] == 2
        ids = {p["prescription_id"] for p in body["prescriptions"]}
        assert ids == {"RX-A1", "RX-A2"}
        assert "RX-B1" not in ids  # never another customer's data

    def test_rx_requires_token(self, client, patch_repos):
        patch_repos(prescriptions=[])
        resp = client.get("/api/v1/portal/rx")
        assert resp.status_code == 401

    def test_rx_rejects_garbage_token(self, client, patch_repos):
        patch_repos(prescriptions=[])
        resp = client.get(
            "/api/v1/portal/rx", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert resp.status_code == 401

    def test_rx_rejects_main_app_jwt(self, client, patch_repos, auth_headers):
        """A normal IMS user JWT (no portal_rx scope / wrong audience) must NOT
        unlock the Rx portal -- the audience claim blocks the replay."""
        patch_repos(prescriptions=[])
        resp = client.get("/api/v1/portal/rx", headers=auth_headers)
        assert resp.status_code == 401
