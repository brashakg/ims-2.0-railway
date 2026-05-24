"""
IMS 2.0 - Workshop Labels + Scan-to-Advance tests
==================================================
Covers (TestClient + fakes, no live DB / network):

  - next_stage() pure gating: forward-only, no skip, terminal + branch -> None
  - POST /workshop/jobs/{id}/scan-advance
      - auth required
      - success path advances + stamps history
      - WRONG_JOB when scanned code mismatches
      - TERMINAL_STAGE at DELIVERED
      - WRONG_STATION when station's step != the job's ready move
      - NOT_FOUND / REPO_UNAVAILABLE fail-soft
  - GET /workshop/jobs/{id}/label payload shape (traveler / ready)
  - GET /workshop/product-label payload (frame tag + CL box)
  - QZ sign + cert fail-soft (204) when env unset
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Fakes
# ============================================================================


def _mk_job(job_id: str, status: str = "PENDING", **extra) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "job_id": job_id,
        "job_number": f"WS-{job_id}",
        "order_id": f"ORD-{job_id}",
        "status": status,
        "store_id": "BV-TEST-01",
        "customer_name": "Asha Verma",
        "customer_phone": "9876500000",
        "prescription_id": "RX-1",
        "frame_details": {"brand": "Ray-Ban", "model": "RB1234", "color": "Black"},
        "lens_details": {"type": "Single Vision", "coating": "Anti-Glare"},
        "expected_date": "2026-06-01",
    }
    doc.update(extra)
    return doc


class FakeWorkshopRepo:
    """Minimal WorkshopJobRepository stub backed by an in-memory dict."""

    def __init__(self, jobs: List[Dict[str, Any]]):
        self._jobs = {j["job_id"]: dict(j) for j in jobs}
        self.write_ok = True

    def find_by_id(self, job_id):
        doc = self._jobs.get(job_id)
        return dict(doc) if doc else None

    def update_status(self, job_id, status, by_user=None, notes=None):
        if not self.write_ok or job_id not in self._jobs:
            return False
        self._jobs[job_id]["status"] = status
        self._jobs[job_id]["status_updated_by"] = by_user
        return True

    def update(self, job_id, data):
        if job_id not in self._jobs:
            return False
        self._jobs[job_id].update(data)
        return True


class FakeRxRepo:
    def __init__(self, rx: Optional[Dict[str, Any]]):
        self._rx = rx

    def find_by_id(self, _pid):
        return self._rx


class FakeProductRepo:
    def __init__(self, prod: Optional[Dict[str, Any]]):
        self._prod = prod

    def find_by_id(self, _pid):
        return self._prod


@pytest.fixture
def patch_repo(monkeypatch):
    """Install a FakeWorkshopRepo into the labels module."""
    from api.routers import labels as labels_module

    def install(jobs, write_ok=True):
        repo = FakeWorkshopRepo(jobs)
        repo.write_ok = write_ok
        monkeypatch.setattr(labels_module, "get_workshop_repository", lambda: repo)
        return repo

    return install


# ============================================================================
# next_stage() pure gating
# ============================================================================


class TestNextStage:
    def test_forward_spine(self):
        from api.routers.labels import next_stage

        assert next_stage("PENDING") == "IN_PROGRESS"
        assert next_stage("IN_PROGRESS") == "COMPLETED"
        assert next_stage("COMPLETED") == "READY"
        assert next_stage("READY") == "DELIVERED"

    def test_terminal_has_no_next(self):
        from api.routers.labels import next_stage

        assert next_stage("DELIVERED") is None

    def test_branch_and_cancelled_have_no_next(self):
        from api.routers.labels import next_stage

        # QC_FAILED is a branch off COMPLETED, not a forward scan step.
        assert next_stage("QC_FAILED") is None
        assert next_stage("CANCELLED") is None

    def test_missing_or_unknown_treated_as_pending(self):
        from api.routers.labels import next_stage

        assert next_stage(None) == "IN_PROGRESS"
        assert next_stage("") == "IN_PROGRESS"
        assert next_stage("GIBBERISH") == "IN_PROGRESS"

    def test_case_insensitive(self):
        from api.routers.labels import next_stage

        assert next_stage("in_progress") == "COMPLETED"

    def test_no_skipping(self):
        """next_stage only ever returns the IMMEDIATE next stage (never a skip)."""
        from api.routers.labels import next_stage, STAGE_PIPELINE

        for i, stage in enumerate(STAGE_PIPELINE[:-1]):
            assert next_stage(stage) == STAGE_PIPELINE[i + 1]


# ============================================================================
# scan-advance
# ============================================================================


class TestScanAdvanceAuth:
    def test_requires_auth(self, client):
        resp = client.post("/api/v1/workshop/jobs/j1/scan-advance", json={})
        assert resp.status_code == 401


def test_scan_advance_success(client, auth_headers, patch_repo):
    repo = patch_repo([_mk_job("j1", "PENDING")])
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={"scanned_code": "WS-j1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["previous"] == "PENDING"
    assert body["stage"] == "IN_PROGRESS"
    # State actually mutated + history appended.
    assert repo.find_by_id("j1")["status"] == "IN_PROGRESS"
    assert len(repo.find_by_id("j1")["scan_history"]) == 1


def test_scan_advance_wrong_job(client, auth_headers, patch_repo):
    repo = patch_repo([_mk_job("j1", "PENDING")])
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={"scanned_code": "WS-SOMETHING-ELSE"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["reason"] == "WRONG_JOB"
    # No state change.
    assert repo.find_by_id("j1")["status"] == "PENDING"


def test_scan_advance_terminal_stage(client, auth_headers, patch_repo):
    patch_repo([_mk_job("j1", "DELIVERED")])
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={"scanned_code": "WS-j1"},
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "TERMINAL_STAGE"


def test_scan_advance_wrong_station(client, auth_headers, patch_repo):
    """A PENDING job is ready to move to IN_PROGRESS (INTAKE station). Scanning
    it at the QC station (which advances to READY) must be rejected."""
    repo = patch_repo([_mk_job("j1", "PENDING")])
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={"scanned_code": "WS-j1", "station": "QC"},
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "WRONG_STATION"
    assert body["expected"] == "READY"   # the QC station's target
    assert body["got"] == "IN_PROGRESS"  # what the job is actually ready for
    assert repo.find_by_id("j1")["status"] == "PENDING"


def test_scan_advance_right_station(client, auth_headers, patch_repo):
    """COMPLETED job at the QC station correctly advances to READY."""
    repo = patch_repo([_mk_job("j1", "COMPLETED")])
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={"scanned_code": "WS-j1", "station": "QC"},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["stage"] == "READY"
    assert repo.find_by_id("j1")["status"] == "READY"


def test_scan_advance_not_found(client, auth_headers, patch_repo):
    patch_repo([])  # empty repo
    resp = client.post(
        "/api/v1/workshop/jobs/nope/scan-advance",
        headers=auth_headers,
        json={"scanned_code": "WS-nope"},
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "NOT_FOUND"


def test_scan_advance_repo_unavailable(client, auth_headers, monkeypatch):
    from api.routers import labels as labels_module

    monkeypatch.setattr(labels_module, "get_workshop_repository", lambda: None)
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={},
    )
    # Fail-soft: 200 with ok=false, never a 500.
    assert resp.status_code == 200
    assert resp.json()["reason"] == "REPO_UNAVAILABLE"


def test_scan_advance_no_code_trusts_path(client, auth_headers, patch_repo):
    """Omitting scanned_code (a button press, not a physical scan) advances
    using the path job_id without a wrong-job check."""
    repo = patch_repo([_mk_job("j1", "IN_PROGRESS")])
    resp = client.post(
        "/api/v1/workshop/jobs/j1/scan-advance",
        headers=auth_headers,
        json={},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["stage"] == "COMPLETED"
    assert repo.find_by_id("j1")["status"] == "COMPLETED"


# ============================================================================
# label payloads
# ============================================================================


class TestLabelPayload:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/workshop/jobs/j1/label")
        assert resp.status_code == 401

    def test_traveler_label_shape(self, client, auth_headers, patch_repo, monkeypatch):
        patch_repo([_mk_job("j1", "IN_PROGRESS")])
        from api.routers import labels as labels_module

        rx = {
            "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90, "add": "+2.00"},
            "left_eye": {"sph": "-1.25"},
        }
        monkeypatch.setattr(
            labels_module, "get_prescription_repository", lambda: FakeRxRepo(rx)
        )
        resp = client.get(
            "/api/v1/workshop/jobs/j1/label?type=traveler", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["type"] == "traveler"
        assert body["barcode_value"] == "WS-j1"
        assert body["customer_name"] == "Asha Verma"
        assert body["frame"].startswith("Ray-Ban")
        assert "SPH -1.00" in body["rx"]["right"]
        assert "AX 90" in body["rx"]["right"]
        assert body["stage"] == "IN_PROGRESS"
        assert body["next_stage"] == "COMPLETED"
        # Traveler is not the ready label -> no follow-up section.
        assert body["include_followup"] is False

    def test_ready_label_sets_followup_flag(self, client, auth_headers, patch_repo):
        patch_repo([_mk_job("j1", "READY")])
        resp = client.get(
            "/api/v1/workshop/jobs/j1/label?type=ready", headers=auth_headers
        )
        body = resp.json()
        assert body["type"] == "ready"
        assert body["include_followup"] is True

    def test_label_repo_unavailable_minimal(self, client, auth_headers, monkeypatch):
        from api.routers import labels as labels_module

        monkeypatch.setattr(labels_module, "get_workshop_repository", lambda: None)
        resp = client.get("/api/v1/workshop/jobs/j1/label", headers=auth_headers)
        # Fail-soft minimal payload, never 500.
        assert resp.status_code == 200
        body = resp.json()
        assert body["barcode_value"] == "j1"
        assert body["ok"] is False


class TestProductLabel:
    def test_requires_id(self, client, auth_headers):
        resp = client.get("/api/v1/workshop/product-label", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["reason"] == "MISSING_ID"

    def test_frame_tag(self, client, auth_headers, monkeypatch):
        from api.routers import labels as labels_module

        prod = {
            "name": "RB1234 Aviator",
            "brand": "Ray-Ban",
            "sku": "RB-1234",
            "category": "SUNGLASSES",
            "mrp": 5990,
        }
        monkeypatch.setattr(
            labels_module, "get_product_repository", lambda: FakeProductRepo(prod)
        )
        resp = client.get(
            "/api/v1/workshop/product-label?product_id=P1", headers=auth_headers
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["brand"] == "Ray-Ban"
        assert body["is_contact_lens"] is False
        assert body["price_label"] == "Rs 5990"  # ASCII, never the rupee glyph

    def test_cl_box(self, client, auth_headers, monkeypatch):
        from api.routers import labels as labels_module

        prod = {
            "name": "Acuvue Oasys",
            "brand": "Acuvue",
            "category": "CONTACT_LENS",
            "modality": "FORTNIGHTLY",
            "base_curve": "8.4",
            "diameter": "14.0",
            "cl_power": "-2.00",
            "pack_size": 6,
        }
        monkeypatch.setattr(
            labels_module, "get_product_repository", lambda: FakeProductRepo(prod)
        )
        resp = client.get(
            "/api/v1/workshop/product-label?product_id=P9", headers=auth_headers
        )
        body = resp.json()
        assert body["is_contact_lens"] is True
        assert body["cl"]["modality"] == "FORTNIGHTLY"
        assert body["cl"]["base_curve"] == "8.4"


# ============================================================================
# QZ sign + cert fail-soft
# ============================================================================


class TestQzFailSoft:
    def test_cert_204_when_unset(self, client, auth_headers, monkeypatch):
        monkeypatch.delenv("QZ_CERT", raising=False)
        monkeypatch.delenv("QZ_CERT_B64", raising=False)
        resp = client.get("/api/v1/print/qz/cert", headers=auth_headers)
        assert resp.status_code == 204

    def test_sign_204_when_key_unset(self, client, auth_headers, monkeypatch):
        monkeypatch.delenv("QZ_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("QZ_PRIVATE_KEY_B64", raising=False)
        resp = client.post(
            "/api/v1/print/qz/sign",
            headers=auth_headers,
            json={"request": "anything-to-sign"},
        )
        assert resp.status_code == 204

    def test_sign_204_when_no_payload(self, client, auth_headers):
        resp = client.post(
            "/api/v1/print/qz/sign", headers=auth_headers, json={}
        )
        assert resp.status_code == 204

    def test_sign_requires_auth(self, client):
        resp = client.post("/api/v1/print/qz/sign", json={"request": "x"})
        assert resp.status_code == 401

    def test_sign_with_key_returns_signature(self, client, auth_headers, monkeypatch):
        """When a valid RSA key IS set, signing returns a base64 signature."""
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
        except Exception:
            pytest.skip("cryptography not installed")

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        monkeypatch.setenv("QZ_PRIVATE_KEY", pem)
        resp = client.post(
            "/api/v1/print/qz/sign",
            headers=auth_headers,
            json={"request": "1234567890"},
        )
        assert resp.status_code == 200
        # base64 signature, non-empty.
        assert len(resp.text) > 40
