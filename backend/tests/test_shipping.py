"""
IMS 2.0 - Shiprocket shipping tests
===================================
Two layers (mirrors test_returns.py):
  1. Service gating (api.services.shiprocket): authenticate / create_shipment /
     track all FAIL SOFT and return SIMULATED when creds are unset or
     DISPATCH_MODE != 'live'. NO live network is ever hit in these tests
     (httpx is monkeypatched to explode if called).
  2. Endpoint smoke tests via FastAPI TestClient with monkeypatched fake repos /
     collections - no live DB. Asserts auth, store scoping, persistence, the
     SIMULATED book path, and last-known track fallback.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
# Default the gate to off so create_shipment simulates unless a test flips it.
os.environ["DISPATCH_MODE"] = "off"

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import shiprocket  # noqa: E402
from api.routers import shipping as shipping_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ============================================================================
# 1. SERVICE - fail-soft + DISPATCH_MODE gating (no network)
# ============================================================================


def _boom(*_a, **_k):
    raise AssertionError("network must NOT be called in this test")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Guard: any attempt to construct an httpx client fails the test loudly.
    Tests that exercise the live path opt in by re-patching this."""
    monkeypatch.setattr(shiprocket.httpx, "AsyncClient", _boom)
    # Make sure no env creds leak in from the host.
    monkeypatch.delenv("SHIPROCKET_EMAIL", raising=False)
    monkeypatch.delenv("SHIPROCKET_PASSWORD", raising=False)
    monkeypatch.setattr(shiprocket, "_reset_token_cache", lambda: None, raising=False)
    shiprocket._token_cache.update({"email": None, "token": None, "fetched_at": 0.0})


def test_credentials_present_false_when_unset(monkeypatch):
    assert shiprocket.credentials_present(db=None) is False


def test_authenticate_simulated_without_creds():
    res = _run(shiprocket.authenticate(db=None))
    assert res.ok is False
    assert res.status == "SIMULATED"
    assert "not configured" in (res.error or "")


def test_create_shipment_simulated_when_mode_off(monkeypatch):
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "off")
    order = {"order_id": "ORD-1", "order_number": "INV-1", "items": []}
    res = _run(shiprocket.create_shipment(order, {"pincode": "834001"}, db=None))
    assert res.ok is True
    assert res.status == "SIMULATED"
    assert res.awb and res.awb.startswith("SIMSR")
    assert "DISPATCH_MODE=off" in (res.error or "")


def test_create_shipment_simulated_when_live_but_no_creds(monkeypatch):
    # Even in live mode, missing creds must SIMULATE (never a live call / 500).
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "live")
    res = _run(shiprocket.create_shipment({"order_id": "ORD-9"}, {}, db=None))
    assert res.ok is True
    assert res.status == "SIMULATED"
    assert "not configured" in (res.error or "")


def test_track_simulated_without_creds():
    res = _run(shiprocket.track(awb="AWB123", db=None))
    assert res.ok is True
    assert res.status == "SIMULATED"
    assert res.awb == "AWB123"


def test_track_requires_identifier():
    res = _run(shiprocket.track(db=None))
    assert res.ok is False
    assert res.status == "FAILED"


def test_simulated_awb_is_deterministic():
    a = shiprocket._simulated_awb("ORD-ABC123")
    b = shiprocket._simulated_awb("ORD-ABC123")
    assert a == b
    assert a.startswith("SIMSR")


def test_build_payload_maps_items_and_address():
    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1001",
        "customer_name": "Asha",
        "customer_phone": "9876543210",
        "grand_total": 3000.0,
        "created_at": "2026-05-23T10:00:00",
        "items": [
            {"product_name": "Ray-Ban", "sku": "RB-1", "quantity": 1, "item_total": 1500},
            {"product_name": "Lens", "sku": "LN-1", "quantity": 2, "item_total": 1500},
        ],
    }
    addr = {"address": "12 MG Road", "city": "Ranchi", "state": "Jharkhand", "pincode": "834001"}
    p = shiprocket.build_shipment_payload(order, addr, pickup_location="Primary")
    assert p["order_id"] == "INV-1001"
    assert p["billing_pincode"] == "834001"
    assert p["pickup_location"] == "Primary"
    assert len(p["order_items"]) == 2
    assert p["order_items"][0]["sku"] == "RB-1"
    assert p["sub_total"] == 3000.0


def test_build_payload_handles_datetime_created_at():
    # Orders persist created_at as a naive-UTC BSON DATETIME (POS always did;
    # online orders post-#935). Slicing it ([:10]) raised TypeError -- and the
    # build_shipment_payload call sits OUTSIDE create_shipment's try, so live
    # dispatch mode 500'd on every datetime-dated order.
    order = {
        "order_id": "ORD-3",
        "order_number": "INV-1002",
        "grand_total": 500.0,
        "created_at": datetime(2026, 7, 21, 9, 30, 0),
        "items": [],
    }
    p = shiprocket.build_shipment_payload(order, {})
    assert p["order_date"] == "2026-07-21"


def test_build_payload_missing_created_at_defaults_to_today():
    p = shiprocket.build_shipment_payload(
        {"order_id": "ORD-4", "grand_total": 1.0, "items": [], "created_at": None}, {}
    )
    assert p["order_date"] == datetime.now().date().isoformat()


def test_build_payload_empty_cart_gets_summary_line():
    p = shiprocket.build_shipment_payload(
        {"order_id": "ORD-2", "grand_total": 999.0, "items": []}, {}
    )
    assert len(p["order_items"]) == 1
    assert p["order_items"][0]["selling_price"] == 999.0


def test_create_shipment_live_books_via_fake_network(monkeypatch):
    """The one live-path test: mode=live + creds present -> hits a FAKE httpx
    that returns a booked AWB. Confirms BOOKED mapping + no exception."""
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "live")
    monkeypatch.setenv("SHIPROCKET_EMAIL", "ops@bv.in")
    monkeypatch.setenv("SHIPROCKET_PASSWORD", "secret")

    class _Resp:
        def __init__(self, code, payload):
            self.status_code = code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            if url.endswith("/auth/login"):
                return _Resp(200, {"token": "tok-123"})
            if url.endswith("/orders/create/adhoc"):
                return _Resp(
                    200,
                    {
                        "awb_code": "AWB777",
                        "shipment_id": 555,
                        "order_id": 999,
                        "courier_name": "Delhivery",
                        "status": "NEW",
                    },
                )
            return _Resp(404, {})

    monkeypatch.setattr(shiprocket.httpx, "AsyncClient", _FakeClient)
    res = _run(
        shiprocket.create_shipment(
            {"order_id": "ORD-1", "items": [], "grand_total": 100.0},
            {"pincode": "834001"},
            db=None,
        )
    )
    assert res.ok is True
    assert res.status == "BOOKED"
    assert res.awb == "AWB777"
    assert res.courier == "Delhivery"


# ============================================================================
# 2. ENDPOINT smoke tests (no DB, monkeypatched fakes)
# ============================================================================


class _FakeOrderRepo:
    def __init__(self, order=None):
        self._order = order

    def find_by_id(self, oid):
        if self._order and self._order.get("order_id") == oid:
            return self._order
        return None

    def find_by_order_number(self, num):
        if self._order and self._order.get("order_number") == num:
            return self._order
        return None


class _FakeCustomerRepo:
    def __init__(self):
        self.customers = {
            "CUST-1": {
                "customer_id": "CUST-1",
                "name": "Asha",
                "phone": "9876543210",
                "address": "12 MG Road",
                "city": "Ranchi",
                "state": "Jharkhand",
                "pincode": "834001",
            }
        }

    def find_by_id(self, cid):
        return self.customers.get(cid)


class _FakeResult:
    def __init__(self, matched=1):
        self.matched_count = matched
        self.modified_count = matched


class _FakeColl:
    """Generic in-memory collection for `shipments`."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return _FakeResult(1)

    def find(self, query=None, projection=None):
        q = query or {}
        matched = [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]
        return _FakeCursor(matched)

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                out = dict(d)
                out.pop("_id", None)
                return out
        return None

    def count_documents(self, query=None):
        q = query or {}
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in q.items()))

    def update_one(self, flt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set", {}))
                return _FakeResult(1)
        return _FakeResult(0)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def skip(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self._docs)


def _staff_token(roles, store_id="BV-PUN-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


@pytest.fixture
def ctx(monkeypatch):
    """Wire the shipping router with fake repos + a fake shipments collection."""
    app = FastAPI()
    app.include_router(shipping_router.router, prefix="/api/v1/shipping")

    order = {
        "order_id": "ORD-1",
        "order_number": "INV-1001",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "customer_phone": "9876543210",
        "store_id": "BV-PUN-01",
        "grand_total": 3000.0,
        "items": [
            {"product_name": "Ray-Ban", "sku": "RB-1", "quantity": 1, "item_total": 3000}
        ],
    }
    order_repo = _FakeOrderRepo(order)
    customer_repo = _FakeCustomerRepo()
    shipments_coll = _FakeColl()

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return {"shipments": shipments_coll}.get(name, _FakeColl())

    fake_db = _FakeDB()

    monkeypatch.setattr(shipping_router, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(
        shipping_router, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr("api.dependencies.get_db", lambda: fake_db, raising=False)
    # Force the service into SIMULATED mode regardless of host env.
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "off")

    return {
        "client": TestClient(app),
        "order_repo": order_repo,
        "customer_repo": customer_repo,
        "shipments_coll": shipments_coll,
    }


def test_book_requires_auth(ctx):
    r = ctx["client"].post("/api/v1/shipping/shipments", json={"order_id": "ORD-1"})
    assert r.status_code == 401


def test_book_forbidden_for_wrong_role(ctx):
    tok = _staff_token(["OPTOMETRIST"])
    r = ctx["client"].post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-1"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403


def test_book_simulated_and_persists(ctx):
    tok = _staff_token(["STORE_MANAGER"])
    r = ctx["client"].post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-1"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "SIMULATED"
    assert data["simulated"] is True
    assert data["awb"].startswith("SIMSR")
    assert data["shipment_id"].startswith("SHP-")
    # Persisted with the resolved store + customer address.
    doc = ctx["shipments_coll"].docs[0]
    assert doc["shipment_id"] == data["shipment_id"]
    assert doc["store_id"] == "BV-PUN-01"
    assert doc["ship_to"]["pincode"] == "834001"  # pulled from customer doc
    assert doc["provider"] == "shiprocket"


def test_book_address_override_wins(ctx):
    tok = _staff_token(["ADMIN"])
    r = ctx["client"].post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-1", "address": {"pincode": "400001", "city": "Mumbai"}},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 201
    doc = ctx["shipments_coll"].docs[0]
    assert doc["ship_to"]["pincode"] == "400001"
    assert doc["ship_to"]["city"] == "Mumbai"


def test_list_shipments_store_scoped(ctx):
    tok_admin = _staff_token(["ADMIN"])
    tok_other = _staff_token(["CASHIER"], store_id="BV-OTHER-02")
    ctx["client"].post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-1"},
        headers={"Authorization": f"Bearer {tok_admin}"},
    )
    # Cashier at a different store sees none (pinned to their active store).
    r = ctx["client"].get(
        "/api/v1/shipping/shipments",
        headers={"Authorization": f"Bearer {tok_other}"},
    )
    assert r.status_code == 200
    assert r.json()["shipments"] == []
    # Admin can scope to the originating store and see it.
    r2 = ctx["client"].get(
        "/api/v1/shipping/shipments",
        params={"store_id": "BV-PUN-01"},
        headers={"Authorization": f"Bearer {tok_admin}"},
    )
    assert r2.json()["total"] == 1


def test_list_filter_by_order(ctx):
    tok = _staff_token(["ADMIN"])
    ctx["client"].post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-1"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    r = ctx["client"].get(
        "/api/v1/shipping/shipments",
        params={"order_id": "ORD-1", "store_id": "BV-PUN-01"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.json()["total"] == 1
    r2 = ctx["client"].get(
        "/api/v1/shipping/shipments",
        params={"order_id": "NOPE", "store_id": "BV-PUN-01"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.json()["total"] == 0


def test_track_returns_last_known(ctx):
    tok = _staff_token(["ADMIN"])
    created = ctx["client"].post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-1"},
        headers={"Authorization": f"Bearer {tok}"},
    ).json()
    sid = created["shipment_id"]
    # No creds -> service track SIMULATES, endpoint reports last_known.
    r = ctx["client"].get(
        f"/api/v1/shipping/shipments/{sid}/track",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["live"] is False
    assert data["source"] == "last_known"


def test_track_unknown_shipment_404(ctx):
    tok = _staff_token(["ADMIN"])
    r = ctx["client"].get(
        "/api/v1/shipping/shipments/NOPE/track",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 404
