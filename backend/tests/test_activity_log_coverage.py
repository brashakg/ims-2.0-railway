"""
IMS 2.0 - Comprehensive activity-log coverage
=============================================
The owner reported the SUPERADMIN Activity Log was missing whole classes of
action -- clinic / Rx saves, customer creation, mobile-number edits. Two
mechanisms now guarantee "Audit Everything":

  1. RICH DOMAIN AUDIT in the key routers -- customers / prescriptions /
     clinical write a hash-chained ``audit_logs`` row (source="domain") with
     before/after state on each mutation.
  2. A REQUEST-TIME middleware (api/middleware/audit_activity.py) that writes a
     baseline row for EVERY successful authenticated mutating /api/v1/* request,
     so even un-instrumented (and future) endpoints reach the log.

These tests prove both, plus the contracts that matter:
  * creating a customer writes a CUSTOMER_CREATED row the LIST endpoint returns
  * editing the mobile writes MOBILE_NUMBER_CHANGED with before/after state
  * saving an Rx writes a PRESCRIPTION_CREATED row
  * the middleware writes a baseline row for an arbitrary mutating request
    (and correctly SKIPS reads / unauthenticated / non-mutating requests)
  * a failing audit logger NEVER breaks the underlying business request

All tests run against a BARE FastAPI app with the router mounted, an in-memory
fake repo, and a capturing fake audit repo -- no real database required (mirrors
the dependency-override pattern in test_prescriptions_update.py).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import customers, prescriptions, clinical, settings as settings_router  # noqa: E402
from api.routers.auth import get_current_user, create_access_token  # noqa: E402


# ===========================================================================
# Fakes
# ===========================================================================


class FakeAuditRepo:
    """Captures every audit row in-memory + supports the read-endpoint surface
    (find_many / count) so the SAME GET /settings/audit-logs handler can query
    the rows this test wrote -- proving the list endpoint surfaces them."""

    def __init__(self):
        self.rows: list[dict] = []

    def create(self, row: dict):
        # Mirror BaseRepository: stamp an id; the chain helper would add seq/hash
        # in prod, irrelevant to what the Activity Log reads.
        row = dict(row)
        row.setdefault("log_id", f"log-{len(self.rows) + 1}")
        self.rows.append(row)
        return row

    def _matches(self, row: dict, flt: dict) -> bool:
        for k, v in flt.items():
            if k == "timestamp":
                continue  # range clause ignored in this in-memory fake
            if row.get(k) != v:
                return False
        return True

    def find_many(self, flt=None, sort=None, skip=0, limit=50):
        flt = flt or {}
        hits = [r for r in self.rows if self._matches(r, flt)]
        # newest-first by insertion order proxy (later rows are newer)
        hits = list(reversed(hits))
        return hits[skip : skip + limit]

    def count(self, flt=None):
        flt = flt or {}
        return len([r for r in self.rows if self._matches(r, flt)])


class ExplodingAuditRepo:
    """An audit repo whose create() always raises -- proves the domain helpers
    and the middleware swallow audit failures and never break the request."""

    def create(self, row: dict):
        raise RuntimeError("audit backend is down")

    def find_many(self, *a, **k):
        raise RuntimeError("audit backend is down")

    def count(self, *a, **k):
        raise RuntimeError("audit backend is down")


class FakeCustomerRepo:
    """Minimal in-memory CustomerRepository stand-in."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self._by_mobile: dict[str, str] = {}

    def find_by_mobile(self, mobile):
        cid = self._by_mobile.get(mobile)
        return dict(self.docs[cid]) if cid else None

    def create(self, data):
        cid = data.get("customer_id") or f"cust-{len(self.docs) + 1}"
        data["customer_id"] = cid
        self.docs[cid] = dict(data)
        if data.get("mobile"):
            self._by_mobile[data["mobile"]] = cid
        return dict(self.docs[cid])

    def find_by_id(self, cid):
        return dict(self.docs[cid]) if cid in self.docs else None

    def update(self, cid, data):
        if cid not in self.docs:
            return False
        self.docs[cid].update(data)
        if data.get("mobile"):
            self._by_mobile[data["mobile"]] = cid
        return True

    def add_patient(self, cid, patient):
        if cid not in self.docs:
            return False
        self.docs[cid].setdefault("patients", []).append(patient)
        return True


class FakeRxRepo:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def create(self, data):
        pid = data.get("prescription_id") or f"rx-{len(self.docs) + 1}"
        data["prescription_id"] = pid
        self.docs[pid] = dict(data)
        return dict(self.docs[pid])

    def find_by_id(self, pid):
        return dict(self.docs[pid]) if pid in self.docs else None

    def update(self, pid, data):
        if pid not in self.docs:
            return False
        self.docs[pid].update(data)
        return True


# ===========================================================================
# Harness
# ===========================================================================


def _superadmin():
    return {
        "user_id": "u-super",
        "username": "superadmin",
        "full_name": "Super Admin",
        "active_store_id": "BV-TEST-01",
        "roles": ["SUPERADMIN"],
    }


def _customer_client(audit_repo, cust_repo, monkeypatch, user=None):
    app = FastAPI()
    app.include_router(customers.router, prefix="/customers")

    async def _fake_user():
        return user or _superadmin()

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(customers, "get_customer_repository", lambda: cust_repo)
    monkeypatch.setattr(customers, "get_audit_repository", lambda: audit_repo)
    return TestClient(app)


def _rx_client(audit_repo, rx_repo, cust_repo, monkeypatch, user=None):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return user or _superadmin()

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: cust_repo)
    monkeypatch.setattr(prescriptions, "get_audit_repository", lambda: audit_repo)
    return TestClient(app)


# ===========================================================================
# 1. Customer create -> CUSTOMER_CREATED, returned by the LIST endpoint
# ===========================================================================


class TestCustomerCreateAudited:
    def test_create_writes_customer_created_row(self, monkeypatch):
        audit = FakeAuditRepo()
        repo = FakeCustomerRepo()
        client = _customer_client(audit, repo, monkeypatch)

        resp = client.post(
            "/customers",
            json={"name": "Asha Verma", "mobile": "9876500001"},
        )
        assert resp.status_code == 201, resp.text
        cid = resp.json()["customer_id"]

        created = [r for r in audit.rows if r["action"] == "CUSTOMER_CREATED"]
        assert len(created) == 1
        row = created[0]
        assert row["entity_type"] == "CUSTOMER"
        assert row["entity_id"] == cid
        assert row["user_id"] == "u-super"
        assert row["source"] == "domain"
        assert row.get("timestamp") is not None
        assert row["after_state"]["mobile"] == "9876500001"

    def test_list_endpoint_returns_the_created_row(self, monkeypatch):
        """Prove the row this write produced is queryable through the REAL
        GET /settings/audit-logs handler (the screen + JARVIS read this)."""
        audit = FakeAuditRepo()
        repo = FakeCustomerRepo()

        # 1) create a customer -> writes the row into the shared fake audit repo
        c_client = _customer_client(audit, repo, monkeypatch)
        c_client.post("/customers", json={"name": "Ravi", "mobile": "9876500002"})

        # 2) mount the real settings audit-logs endpoint over the SAME repo
        app = FastAPI()
        app.include_router(settings_router.router, prefix="/settings")
        app.dependency_overrides[get_current_user] = lambda: _superadmin()
        monkeypatch.setattr(settings_router, "get_audit_repository", lambda: audit)
        s_client = TestClient(app)

        resp = s_client.get("/settings/audit-logs", params={"action": "CUSTOMER_CREATED"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] >= 1
        assert any(r["action"] == "CUSTOMER_CREATED" for r in body["logs"])
        # No _id leaks, timestamp present so the UI can render the "When" column.
        assert all("_id" not in r for r in body["logs"])


# ===========================================================================
# 2. Mobile edit -> MOBILE_NUMBER_CHANGED with before/after
# ===========================================================================


class TestMobileChangeAudited:
    def test_mobile_change_writes_before_after(self, monkeypatch):
        audit = FakeAuditRepo()
        repo = FakeCustomerRepo()
        # Seed an existing customer.
        repo.create({"customer_id": "c1", "name": "Old", "mobile": "9000000001"})
        client = _customer_client(audit, repo, monkeypatch)

        resp = client.put("/customers/c1", json={"mobile": "9000000099"})
        assert resp.status_code == 200, resp.text

        changes = [r for r in audit.rows if r["action"] == "MOBILE_NUMBER_CHANGED"]
        assert len(changes) == 1
        row = changes[0]
        assert row["entity_id"] == "c1"
        assert row["before_state"]["mobile"] == "9000000001"
        assert row["after_state"]["mobile"] == "9000000099"
        # A pure mobile change is NOT also logged as a generic update.
        assert not any(r["action"] == "CUSTOMER_UPDATED" for r in audit.rows)

    def test_non_mobile_edit_writes_customer_updated(self, monkeypatch):
        audit = FakeAuditRepo()
        repo = FakeCustomerRepo()
        repo.create({"customer_id": "c2", "name": "Keep", "mobile": "9000000002"})
        client = _customer_client(audit, repo, monkeypatch)

        resp = client.put("/customers/c2", json={"email": "keep@example.com"})
        assert resp.status_code == 200, resp.text

        updated = [r for r in audit.rows if r["action"] == "CUSTOMER_UPDATED"]
        assert len(updated) == 1
        assert "email" in updated[0]["detail"]["fields"]
        assert not any(r["action"] == "MOBILE_NUMBER_CHANGED" for r in audit.rows)


# ===========================================================================
# 3. Rx save -> PRESCRIPTION_CREATED
# ===========================================================================


class TestPrescriptionAudited:
    def test_create_rx_writes_prescription_row(self, monkeypatch):
        audit = FakeAuditRepo()
        rx_repo = FakeRxRepo()
        cust_repo = FakeCustomerRepo()
        cust_repo.create({"customer_id": "cust-1", "name": "Pat", "mobile": "9011100001"})
        client = _rx_client(audit, rx_repo, cust_repo, monkeypatch)

        resp = client.post(
            "/prescriptions",
            json={
                "patient_id": "pat-1",
                "customer_id": "cust-1",
                "optometrist_id": "opt-1",
                "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90},
                "left_eye": {"sph": "-1.25", "cyl": "0", "axis": 85},
            },
        )
        assert resp.status_code == 201, resp.text
        pid = resp.json()["prescription_id"]

        created = [r for r in audit.rows if r["action"] == "PRESCRIPTION_CREATED"]
        assert len(created) == 1
        row = created[0]
        assert row["entity_type"] == "PRESCRIPTION"
        assert row["entity_id"] == pid
        assert row["detail"]["customer_id"] == "cust-1"
        assert row["detail"]["rx_kind"] == "SPECTACLE"
        assert row["source"] == "domain"

    def test_update_rx_writes_prescription_updated(self, monkeypatch):
        audit = FakeAuditRepo()
        rx_repo = FakeRxRepo()
        cust_repo = FakeCustomerRepo()
        rx_repo.create(
            {
                "prescription_id": "rx-1",
                "customer_id": "cust-9",
                "rx_kind": "SPECTACLE",
                "right_eye": {"sph": "-1.00", "cyl": "0", "axis": 90},
                "left_eye": {"sph": "-1.00", "cyl": "0", "axis": 90},
            }
        )
        client = _rx_client(audit, rx_repo, cust_repo, monkeypatch)

        resp = client.put("/prescriptions/rx-1", json={"remarks": "rechecked"})
        assert resp.status_code == 200, resp.text

        updated = [r for r in audit.rows if r["action"] == "PRESCRIPTION_UPDATED"]
        assert len(updated) == 1
        assert updated[0]["entity_id"] == "rx-1"
        assert "remarks" in updated[0]["detail"]["fields"]


# ===========================================================================
# 4. Fail-soft: a broken audit logger must NOT break the business request
# ===========================================================================


class TestAuditFailSoft:
    def test_customer_create_succeeds_when_audit_explodes(self, monkeypatch):
        repo = FakeCustomerRepo()
        client = _customer_client(ExplodingAuditRepo(), repo, monkeypatch)
        resp = client.post("/customers", json={"name": "Resilient", "mobile": "9022200001"})
        # The customer is still created (201) even though every audit write raised.
        assert resp.status_code == 201, resp.text
        assert resp.json()["customer_id"]

    def test_mobile_edit_succeeds_when_audit_explodes(self, monkeypatch):
        repo = FakeCustomerRepo()
        repo.create({"customer_id": "cx", "name": "X", "mobile": "9033300001"})
        client = _customer_client(ExplodingAuditRepo(), repo, monkeypatch)
        resp = client.put("/customers/cx", json={"mobile": "9033300099"})
        assert resp.status_code == 200, resp.text


# ===========================================================================
# 5. Middleware: baseline row for an arbitrary mutating request
# ===========================================================================
# Drive the real audit_activity_middleware over a tiny app with a couple of
# /api/v1/* routes. A valid SUPERADMIN bearer token is decoded by the middleware
# exactly as get_current_user would; the capturing fake audit repo records the
# baseline row.


def _middleware_app(audit_repo, monkeypatch):
    from api.middleware.audit_activity import audit_activity_middleware
    from api import dependencies as deps

    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)

    app = FastAPI()
    app.middleware("http")(audit_activity_middleware)

    @app.post("/api/v1/things/{thing_id}")
    async def make_thing(thing_id: str):
        return {"ok": True, "id": thing_id}

    @app.get("/api/v1/things")
    async def list_things():
        return {"things": []}

    @app.post("/api/v1/auth/login")
    async def fake_login():
        return {"access_token": "x"}

    return TestClient(app)


def _bearer():
    token = create_access_token(
        {
            "user_id": "u-mw",
            "username": "mwuser",
            "roles": ["SUPERADMIN"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


class TestMiddlewareBaseline:
    def test_mutating_request_writes_baseline_row(self, monkeypatch):
        audit = FakeAuditRepo()
        client = _middleware_app(audit, monkeypatch)

        resp = client.post("/api/v1/things/abc123def4567890", headers=_bearer())
        assert resp.status_code == 200, resp.text

        assert len(audit.rows) == 1
        row = audit.rows[0]
        assert row["action"] == "CREATE"
        assert row["entity_type"] == "THING"
        assert row["entity_id"] == "abc123def4567890"
        assert row["user_id"] == "u-mw"
        assert row["user_name"] == "mwuser"
        assert row["source"] == "middleware"
        assert row["method"] == "POST"
        assert row["status_code"] == 200
        assert row.get("timestamp") is not None

    def test_get_request_is_not_logged(self, monkeypatch):
        audit = FakeAuditRepo()
        client = _middleware_app(audit, monkeypatch)
        resp = client.get("/api/v1/things", headers=_bearer())
        assert resp.status_code == 200
        assert audit.rows == []

    def test_unauthenticated_mutation_is_not_logged(self, monkeypatch):
        audit = FakeAuditRepo()
        client = _middleware_app(audit, monkeypatch)
        # No Authorization header -> anonymous -> nothing attributed/logged.
        resp = client.post("/api/v1/things/zzz999")
        assert resp.status_code == 200
        assert audit.rows == []

    def test_login_path_is_skipped(self, monkeypatch):
        audit = FakeAuditRepo()
        client = _middleware_app(audit, monkeypatch)
        # /auth/login is already audited by the auth router -> skipped here.
        resp = client.post("/api/v1/auth/login", headers=_bearer())
        assert resp.status_code == 200
        assert audit.rows == []

    def test_middleware_failsoft_when_audit_explodes(self, monkeypatch):
        client = _middleware_app(ExplodingAuditRepo(), monkeypatch)
        # Even though the audit write raises, the real request still returns 200.
        resp = client.post("/api/v1/things/boom1234567890ab", headers=_bearer())
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True


# ===========================================================================
# 6. Pure-helper unit tests for the middleware path/entity parsing
# ===========================================================================


class TestMiddlewareHelpers:
    def test_entity_and_id_from_id_path(self):
        from api.middleware.audit_activity import _entity_and_id

        entity, eid = _entity_and_id("/api/v1/customers/9b1c2d3e-aaaa-bbbb")
        assert entity == "CUSTOMER"
        assert eid == "9b1c2d3e-aaaa-bbbb"

    def test_entity_and_id_subresource_is_not_an_id(self):
        from api.middleware.audit_activity import _entity_and_id

        # /customers/{id}/patients -> the tail "patients" is a sub-resource verb,
        # not an id, so entity_id is dropped.
        entity, eid = _entity_and_id("/api/v1/customers/c1/patients")
        assert entity == "CUSTOMER"
        assert eid is None

    def test_entity_singularised_and_underscored(self):
        from api.middleware.audit_activity import _entity_and_id

        entity, _ = _entity_and_id("/api/v1/follow-ups/123")
        assert entity == "FOLLOW_UP"

    def test_should_consider_only_mutations_under_api_v1(self):
        from api.middleware.audit_activity import _should_consider

        assert _should_consider("POST", "/api/v1/orders") is True
        assert _should_consider("PUT", "/api/v1/customers/c1") is True
        assert _should_consider("DELETE", "/api/v1/inventory/x-1") is True
        assert _should_consider("GET", "/api/v1/orders") is False
        assert _should_consider("POST", "/health") is False
        assert _should_consider("POST", "/api/v1/auth/login") is False
        assert _should_consider("POST", "/api/v1/webhooks/shopify") is False
        assert _should_consider("POST", "/api/v1/audit/anything") is False
