"""
IMS 2.0 — Vendor Portal tests
==============================
Coverage:

Admin-side (existing JWT auth):
  - PATCH /workshop/jobs/{id}/vendor stamps vendor fields, looks up vendor name
  - PATCH /workshop/jobs/{id}/vendor 404 on unknown vendor
  - POST /workshop/jobs/{id}/vendor-status (ims_user) appends history,
    auto-stamps DISPATCHED/DELIVERED dates
  - POST /workshop/jobs/{id}/vendor-status 422 on unknown status
  - POST /workshop/jobs/{id}/vendor-status 400 if no vendor assigned
  - GET  /workshop/jobs/by-vendor/{vendor_id} filters and excludes DELIVERED
  - POST /vendors/{id}/portal-token gen — admin-only, returns token + url
  - POST /vendors/{id}/portal-token 403 for non-admin

Vendor-portal (token-auth):
  - GET  /vendor-portal/{token}/jobs returns ONLY the vendor's open jobs
  - GET  /vendor-portal/{token}/jobs redacts customer name to initials,
    no phone / address fields leak
  - Cross-vendor isolation: vendor B can't see vendor A's jobs
  - Invalid token → 401
  - Revoked token → 401
  - POST /vendor-portal/{token}/jobs/{id}/status logs source='vendor_portal',
    DISPATCHED auto-stamps vendor_dispatch_date
  - POST status on unknown enum → 422
  - POST status on a job belonging to another vendor → 404
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Reuse the FakeCollection / FakeDB pattern from test_walkouts (kept inline
# for self-contained discovery — pytest collects fixtures per-file).
# ============================================================================


def _doc_matches(doc, filter):
    if not filter:
        return True
    for k, expected in filter.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$nin":
                    if actual in op_val:
                        return False
                if op == "$in":
                    if actual not in op_val:
                        return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort_keys = None
        self._skip = 0
        self._limit = None

    def sort(self, keys):
        self._sort_keys = keys
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def _materialize(self):
        out = list(self._docs)
        if self._sort_keys:
            for key, direction in reversed(self._sort_keys):
                out.sort(
                    key=lambda d, k=key: (d.get(k) is None, d.get(k)),
                    reverse=(direction == -1),
                )
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return out

    def __iter__(self):
        return iter(self._materialize())


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filter):
                return d
        return None

    def find(self, filter=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter))

    def count_documents(self, filter=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter))

    def update_one(self, filter, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter):
                set_block = (update or {}).get("$set", {}) or {}
                push_block = (update or {}).get("$push", {}) or {}
                inc_block = (update or {}).get("$inc", {}) or {}
                d.update(set_block)
                for k, v in push_block.items():
                    arr = d.get(k)
                    if not isinstance(arr, list):
                        arr = []
                    arr.append(v)
                    d[k] = arr
                for k, v in inc_block.items():
                    d[k] = (d.get(k, 0) or 0) + v
                modified += 1
                break
        return type("R", (), {"modified_count": modified, "matched_count": modified})()

    def delete_one(self, filter):
        for i, d in enumerate(self.docs):
            if _doc_matches(d, filter):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def aggregate(self, pipeline):
        return iter([])


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


# ============================================================================
# Fixture wiring
# ============================================================================


@pytest.fixture
def patched(monkeypatch):
    """Patch dependency injection across the four touched routers."""
    fake_db = FakeDB()

    from api import dependencies as deps_module
    from api.routers import workshop as workshop_module
    from api.routers import vendors as vendors_module
    from api.routers import vendor_portal as vp_module

    from database.repositories.workshop_repository import WorkshopJobRepository
    from database.repositories.vendor_repository import VendorRepository
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.vendor_portal_token_repository import (
        VendorPortalTokenRepository,
    )
    from database.repositories.order_repository import OrderRepository

    workshop_repo = WorkshopJobRepository(fake_db.get_collection("workshop_jobs"))
    vendor_repo = VendorRepository(fake_db.get_collection("vendors"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    token_repo = VendorPortalTokenRepository(
        fake_db.get_collection("vendor_portal_tokens")
    )
    order_repo = OrderRepository(fake_db.get_collection("orders"))

    # Patch on dependencies module + per-router imports. Each router does
    # `from ..dependencies import get_X` which creates a local binding —
    # patching only deps_module wouldn't reach the resolved name inside
    # the router. So we set the same fake on every module that uses it.
    for mod in (deps_module, workshop_module, vp_module):
        monkeypatch.setattr(mod, "get_workshop_repository", lambda: workshop_repo, raising=False)
    for mod in (deps_module, vendors_module, workshop_module):
        monkeypatch.setattr(mod, "get_vendor_repository", lambda: vendor_repo, raising=False)
    for mod in (deps_module, vendors_module, workshop_module, vp_module):
        monkeypatch.setattr(mod, "get_audit_repository", lambda: audit_repo, raising=False)
    for mod in (deps_module, vendors_module, vp_module):
        monkeypatch.setattr(
            mod, "get_vendor_portal_token_repository", lambda: token_repo, raising=False
        )
    monkeypatch.setattr(workshop_module, "get_order_repository", lambda: order_repo, raising=False)

    # Reset rate-limit bucket between tests so we don't false-trigger 429s
    vp_module._portal_request_log.clear()

    # Seed two vendors
    vendor_repo.create({
        "vendor_id": "vendor-zeiss",
        "legal_name": "Zeiss India Pvt Ltd",
        "trade_name": "Zeiss",
        "vendor_type": "INDIAN",
        "gstin_status": "REGISTERED",
        "mobile": "9999900000",
        "is_active": True,
    })
    vendor_repo.create({
        "vendor_id": "vendor-essilor",
        "legal_name": "Essilor India",
        "trade_name": "Essilor",
        "vendor_type": "INDIAN",
        "gstin_status": "REGISTERED",
        "mobile": "8888800000",
        "is_active": True,
    })

    return {
        "db": fake_db,
        "workshop_repo": workshop_repo,
        "vendor_repo": vendor_repo,
        "audit_repo": audit_repo,
        "token_repo": token_repo,
    }


def _seed_job(workshop_repo, **kw):
    """Insert a workshop job. Defaults match the realistic POS shape."""
    job = {
        "job_id": kw.get("job_id"),
        "job_number": kw.get("job_number", "WS-260101-AAAA1111"),
        "order_id": kw.get("order_id", "order-1"),
        "order_number": kw.get("order_number", "BV-260101-001"),
        "store_id": kw.get("store_id", "BV-TEST-01"),
        "customer_name": kw.get("customer_name", "Avinash Kumar Gupta"),
        "customer_phone": kw.get("customer_phone", "9473457157"),
        "frame_details": kw.get("frame_details", {"brand": "Ray-Ban", "model": "Aviator"}),
        "lens_details": kw.get("lens_details", {"type": "Progressive", "coating": "Blue Cut"}),
        "prescription_id": kw.get("prescription_id", "rx-1"),
        "expected_date": kw.get("expected_date", "2026-06-01"),
        "status": kw.get("status", "PENDING"),
        "vendor_id": kw.get("vendor_id"),
    }
    return workshop_repo.create(job)


@pytest.fixture
def admin_headers():
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "test-admin-001",
        "username": "testadmin",
        "roles": ["SUPERADMIN"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def staff_headers():
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "test-staff-001",
        "username": "teststaff",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# Admin-side: PATCH /workshop/jobs/{id}/vendor
# ============================================================================


def test_patch_vendor_stamps_fields(client, admin_headers, patched):
    """PATCH /vendor sets vendor_id + vendor_order_id + tracking_url and
    auto-fills vendor_name from the vendor row."""
    job = _seed_job(patched["workshop_repo"], job_id="job-1")
    resp = client.patch(
        "/api/v1/workshop/jobs/job-1/vendor",
        json={
            "vendor_id": "vendor-zeiss",
            "vendor_order_id": "ZS-2026-0042",
            "vendor_tracking_url": "https://tracking.zeiss.com/ZS-2026-0042",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vendor_id"] == "vendor-zeiss"
    assert body["vendor_order_id"] == "ZS-2026-0042"

    # Persisted on the workshop_jobs row
    saved = patched["workshop_repo"].find_by_id("job-1")
    assert saved["vendor_id"] == "vendor-zeiss"
    assert saved["vendor_name"] == "Zeiss"  # auto-pulled from trade_name
    assert saved["vendor_order_id"] == "ZS-2026-0042"
    assert saved["vendor_tracking_url"] == "https://tracking.zeiss.com/ZS-2026-0042"


def test_patch_vendor_404_on_unknown_vendor(client, admin_headers, patched):
    _seed_job(patched["workshop_repo"], job_id="job-2")
    resp = client.patch(
        "/api/v1/workshop/jobs/job-2/vendor",
        json={"vendor_id": "vendor-doesnotexist"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


# ============================================================================
# Admin-side: POST /workshop/jobs/{id}/vendor-status
# ============================================================================


def test_vendor_status_from_ims_user_recorded_with_correct_source(
    client, admin_headers, patched
):
    """Admin logs DISPATCHED — history row has source='ims_user' and
    vendor_dispatch_date is auto-stamped."""
    _seed_job(
        patched["workshop_repo"],
        job_id="job-3",
        vendor_id="vendor-zeiss",
    )
    resp = client.post(
        "/api/v1/workshop/jobs/job-3/vendor-status",
        json={"status": "DISPATCHED", "note": "Lab phoned, dispatched yesterday"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vendor_status"] == "DISPATCHED"
    assert body["source"] == "ims_user"

    saved = patched["workshop_repo"].find_by_id("job-3")
    assert saved["vendor_status"] == "DISPATCHED"
    assert saved.get("vendor_dispatch_date")  # auto-stamped
    history = saved["vendor_status_history"]
    assert len(history) == 1
    assert history[0]["source"] == "ims_user"
    assert history[0]["logged_by"] == "test-admin-001"
    assert history[0]["status"] == "DISPATCHED"


def test_vendor_status_unknown_enum_returns_422(client, admin_headers, patched):
    _seed_job(patched["workshop_repo"], job_id="job-4", vendor_id="vendor-zeiss")
    resp = client.post(
        "/api/v1/workshop/jobs/job-4/vendor-status",
        json={"status": "FROBNICATED"},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_vendor_status_400_if_no_vendor_assigned(client, admin_headers, patched):
    _seed_job(patched["workshop_repo"], job_id="job-5")  # no vendor_id
    resp = client.post(
        "/api/v1/workshop/jobs/job-5/vendor-status",
        json={"status": "RECEIVED"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


# ============================================================================
# Admin-side: GET /workshop/jobs/by-vendor/{vendor_id}
# ============================================================================


def test_by_vendor_list_filtered_correctly(client, admin_headers, patched):
    """One vendor's queue, excluding DELIVERED/CANCELLED by default."""
    _seed_job(patched["workshop_repo"], job_id="job-a", vendor_id="vendor-zeiss",
              status="PENDING", expected_date="2026-06-01")
    _seed_job(patched["workshop_repo"], job_id="job-b", vendor_id="vendor-zeiss",
              status="IN_PROGRESS", expected_date="2026-06-02")
    _seed_job(patched["workshop_repo"], job_id="job-c", vendor_id="vendor-zeiss",
              status="DELIVERED", expected_date="2026-05-15")
    _seed_job(patched["workshop_repo"], job_id="job-d", vendor_id="vendor-essilor",
              status="PENDING", expected_date="2026-06-03")

    resp = client.get(
        "/api/v1/workshop/jobs/by-vendor/vendor-zeiss", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = sorted(j["id"] for j in body["jobs"])
    assert ids == ["job-a", "job-b"]  # job-c excluded (DELIVERED), job-d wrong vendor

    # include_delivered=True surfaces the closed row too
    resp = client.get(
        "/api/v1/workshop/jobs/by-vendor/vendor-zeiss?include_delivered=true",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    ids = sorted(j["id"] for j in resp.json()["jobs"])
    assert ids == ["job-a", "job-b", "job-c"]


# ============================================================================
# Token issuance — /vendors/{id}/portal-token
# ============================================================================


def test_token_issue_admin_only(client, admin_headers, staff_headers, patched):
    """Only SUPERADMIN/ADMIN can mint tokens."""
    # Staff is rejected
    resp = client.post(
        "/api/v1/vendors/vendor-zeiss/portal-token",
        json={"ttl_days": 30},
        headers=staff_headers,
    )
    assert resp.status_code == 403

    # Admin succeeds
    resp = client.post(
        "/api/v1/vendors/vendor-zeiss/portal-token",
        json={"ttl_days": 90},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_id"]
    assert len(body["token_id"]) >= 16  # uuid4
    assert body["vendor_id"] == "vendor-zeiss"
    assert body["portal_path"] == f"/vendor-portal/{body['token_id']}"


# ============================================================================
# Public surface — /vendor-portal/{token_id}/...
# ============================================================================


def _issue_token(client, admin_headers, vendor_id):
    resp = client.post(
        f"/api/v1/vendors/{vendor_id}/portal-token",
        json={"ttl_days": 30},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token_id"]


def test_portal_lists_only_this_vendors_open_jobs_with_pii_redacted(
    client, admin_headers, patched
):
    """Vendor sees their open jobs, customer_name redacted to initials,
    no phone field leaked."""
    _seed_job(patched["workshop_repo"], job_id="zj-1", vendor_id="vendor-zeiss",
              customer_name="Avinash Kumar Gupta", customer_phone="9473457157",
              expected_date="2026-06-05")
    _seed_job(patched["workshop_repo"], job_id="zj-2", vendor_id="vendor-zeiss",
              customer_name="Priya Sharma", customer_phone="9876543210",
              status="DELIVERED", expected_date="2026-05-15")
    _seed_job(patched["workshop_repo"], job_id="ej-1", vendor_id="vendor-essilor",
              customer_name="Other Customer", expected_date="2026-06-08")

    token = _issue_token(client, admin_headers, "vendor-zeiss")
    resp = client.get(f"/api/v1/vendor-portal/{token}/jobs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vendor_id"] == "vendor-zeiss"
    assert body["total"] == 1  # only open job
    job = body["jobs"][0]
    assert job["job_id"] == "zj-1"
    assert job["customer_initials"] == "A.G."
    # PII NEVER returned
    assert "customer_name" not in job
    assert "customer_phone" not in job
    assert "address" not in job
    # Useful fields ARE present
    assert job["frame_brand"] == "Ray-Ban"
    assert job["lens_type"] == "Progressive"


def test_portal_cross_vendor_isolation(client, admin_headers, patched):
    """Vendor A (Zeiss) token cannot see vendor B (Essilor)'s jobs."""
    _seed_job(patched["workshop_repo"], job_id="ej-1", vendor_id="vendor-essilor",
              customer_name="X Y")
    token_zeiss = _issue_token(client, admin_headers, "vendor-zeiss")

    # /jobs returns empty
    resp = client.get(f"/api/v1/vendor-portal/{token_zeiss}/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # Direct lookup of Essilor's job by id with Zeiss's token → 404
    resp = client.get(f"/api/v1/vendor-portal/{token_zeiss}/jobs/ej-1")
    assert resp.status_code == 404


def test_portal_invalid_token_returns_401(client, patched):
    resp = client.get("/api/v1/vendor-portal/notatoken-not-real-12345678/jobs")
    assert resp.status_code == 401


def test_portal_revoked_token_returns_401(client, admin_headers, patched):
    token = _issue_token(client, admin_headers, "vendor-zeiss")
    # Revoke it
    resp = client.delete(
        f"/api/v1/vendors/vendor-zeiss/portal-token/{token}",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    # Now the public surface refuses
    resp = client.get(f"/api/v1/vendor-portal/{token}/jobs")
    assert resp.status_code == 401


def test_portal_status_post_logs_source_vendor_portal_and_stamps_dispatch(
    client, admin_headers, patched
):
    """Status update from the portal: source='vendor_portal',
    DISPATCHED auto-stamps vendor_dispatch_date, audit row written."""
    _seed_job(patched["workshop_repo"], job_id="zj-9", vendor_id="vendor-zeiss",
              customer_name="C P")
    token = _issue_token(client, admin_headers, "vendor-zeiss")

    resp = client.post(
        f"/api/v1/vendor-portal/{token}/jobs/zj-9/status",
        json={
            "status": "DISPATCHED",
            "note": "Tracking number ABC123",
            "tracking_url": "https://shipper.com/ABC123",
            "vendor_order_id": "ZS-9999",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vendor_status"] == "DISPATCHED"

    saved = patched["workshop_repo"].find_by_id("zj-9")
    assert saved["vendor_status"] == "DISPATCHED"
    assert saved["vendor_tracking_url"] == "https://shipper.com/ABC123"
    assert saved["vendor_order_id"] == "ZS-9999"
    assert saved.get("vendor_dispatch_date")
    history = saved["vendor_status_history"]
    assert len(history) == 1
    assert history[0]["source"] == "vendor_portal"
    assert history[0]["logged_by"] == "vendor-zeiss"
    assert history[0]["note"] == "Tracking number ABC123"

    # Audit trail
    audit_actions = [
        d for d in patched["audit_repo"].collection.docs
        if d.get("action") == "workshop.vendor_status"
    ]
    assert any(
        a.get("detail", {}).get("source") == "vendor_portal" for a in audit_actions
    )


def test_portal_status_unknown_enum_returns_422(
    client, admin_headers, patched
):
    _seed_job(patched["workshop_repo"], job_id="zj-10", vendor_id="vendor-zeiss")
    token = _issue_token(client, admin_headers, "vendor-zeiss")
    resp = client.post(
        f"/api/v1/vendor-portal/{token}/jobs/zj-10/status",
        json={"status": "FROBBED"},
    )
    assert resp.status_code == 422


def test_portal_status_post_for_other_vendors_job_returns_404(
    client, admin_headers, patched
):
    """Trying to post status on an Essilor job using a Zeiss token must
    404, even though the job exists. Same response as 'not found' to
    block enumeration."""
    _seed_job(patched["workshop_repo"], job_id="ej-77", vendor_id="vendor-essilor")
    token_zeiss = _issue_token(client, admin_headers, "vendor-zeiss")
    resp = client.post(
        f"/api/v1/vendor-portal/{token_zeiss}/jobs/ej-77/status",
        json={"status": "RECEIVED"},
    )
    assert resp.status_code == 404


def test_portal_received_auto_stamps_vendor_received_date(
    client, admin_headers, patched
):
    """DELIVERED status update from portal auto-stamps vendor_received_date."""
    _seed_job(patched["workshop_repo"], job_id="zj-11", vendor_id="vendor-zeiss")
    token = _issue_token(client, admin_headers, "vendor-zeiss")
    resp = client.post(
        f"/api/v1/vendor-portal/{token}/jobs/zj-11/status",
        json={"status": "DELIVERED"},
    )
    assert resp.status_code == 200
    saved = patched["workshop_repo"].find_by_id("zj-11")
    assert saved.get("vendor_received_date")
    assert saved["vendor_status"] == "DELIVERED"


# ============================================================================
# Audit — every portal status update creates an audit row tagged with
# the token + source.
# ============================================================================


def test_admin_status_audit_log_tagged_ims_user(
    client, admin_headers, patched
):
    _seed_job(patched["workshop_repo"], job_id="zj-12", vendor_id="vendor-zeiss")
    resp = client.post(
        "/api/v1/workshop/jobs/zj-12/vendor-status",
        json={"status": "RECEIVED"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    rows = [
        d for d in patched["audit_repo"].collection.docs
        if d.get("action") == "workshop.vendor_status"
    ]
    assert any(r.get("detail", {}).get("source") == "ims_user" for r in rows)
