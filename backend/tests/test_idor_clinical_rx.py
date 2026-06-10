"""
IMS 2.0 - Clinical eye-test + Rx-version IDOR hardening
========================================================
Regression tests for the 2026-06 audit follow-ups (same class as the
2026-06-05 medical-Rx IDOR P0 fixed in prescriptions.py reads):

P1  clinical.py reads were get_current_user-only (ANY authenticated role, ANY
    store): GET /tests/{test_id}, GET /tests/patient/{customer_phone}
    (phone-enumerable!), GET /tests/customer/{customer_id},
    GET /tests/{test_id}/soap-note. Now role-gated to the SAME set
    prescriptions reads use (prescriptions.require_rx_read / _RX_READ_ROLES)
    + per-object store scope with 404-hide.

P2  clinical.py role-gated-but-cross-store writes: complete_test,
    save_soap_note, queue PATCH status / DELETE / start-test now store-scope
    via the loaded doc's store_id (404-hide).

P1  prescriptions.py PATCH /{id}/version/{version_name} had NO role gate (any
    cashier could overwrite Rx versions chain-wide). Now gated like
    update_prescription (clinical write roles) + store-scoped; PUT /{id} and
    POST /{id}/finalize gained the same store-scope check.

Store-scope semantics:
  * clinical.py docs with NO store_id (legacy pre-store-stamp records) stay
    readable/writable by the role-gated caller (fail-open on missing
    store_id) so legacy eye tests keep working.
  * prescriptions.py mirrors its own BUG-088 read guards exactly
    (can_access_store_scoped): an unattributed Rx is only touchable by
    cross-store admins -- a store-level role can't even READ those, so
    letting it WRITE them would be incoherent.

CI-robustness: every repository accessor the touched handlers call is
monkeypatched; docs are explicitly seeded; assertions are on parsed fields,
never whole-JSON substrings. Standalone bare-app + dependency-override
pattern (mirrors test_clinical_lifecycle.py / test_prescriptions_update.py)
so no DB and no middleware are involved -- these tests pin the ROUTE gates
(defense-in-depth); the middleware layer is covered by
test_rbac_access_matrix.py. ASCII only (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical, prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE_A = "BV-STORE-A"
STORE_B = "BV-STORE-B"
PHONE = "9888877771"
CUSTOMER = "C-1"


# ============================================================================
# In-memory fakes (seeded; capture every write)
# ============================================================================


class _FakeEyeTestRepo:
    def __init__(self, docs):
        self._docs = [dict(d) for d in docs]

    def find_by_id(self, test_id):
        for d in self._docs:
            if d.get("test_id") == test_id:
                return dict(d)
        return None

    def get_patient_tests(self, phone):
        return [dict(d) for d in self._docs if d.get("customer_phone") == phone]

    def get_customer_tests(self, customer_id):
        return [dict(d) for d in self._docs if d.get("customer_id") == customer_id]

    def complete_test(self, test_id, right_eye, left_eye, pd=None, notes=None,
                      lens_recommendation=None, coating_recommendation=None,
                      clinical_findings=None, soap_note=None):
        for d in self._docs:
            if d.get("test_id") == test_id:
                d["status"] = "COMPLETED"
                return True
        return False

    def save_soap_note(self, test_id, note):
        for d in self._docs:
            if d.get("test_id") == test_id:
                d["soap_note"] = dict(note)
                return True
        return False


class _FakeQueueRepo:
    def __init__(self, docs):
        self._docs = {d["queue_id"]: dict(d) for d in docs}
        self.status_calls = []
        self.removed = []

    def find_by_id(self, queue_id):
        d = self._docs.get(queue_id)
        return dict(d) if d else None

    def update_status(self, queue_id, status):
        self.status_calls.append((queue_id, status))
        return True

    def remove_from_queue(self, queue_id):
        self.removed.append(queue_id)
        return True

    def update(self, queue_id, data):
        if queue_id in self._docs:
            self._docs[queue_id].update(data)
        return True


class _FakeRxRepo:
    def __init__(self, docs=()):
        self._docs = [dict(d) for d in docs]
        self.created = []
        self.updates = []

    def find_by_id(self, prescription_id):
        for d in self._docs:
            if d.get("prescription_id") == prescription_id:
                return dict(d)
        return None

    def find_by_eye_test(self, eye_test_id):
        for d in self._docs + self.created:
            if d.get("eye_test_id") == eye_test_id:
                return dict(d)
        return None

    def create(self, data):
        self.created.append(dict(data))
        return dict(data)

    def update(self, prescription_id, data):
        self.updates.append((prescription_id, dict(data)))
        for d in self._docs:
            if d.get("prescription_id") == prescription_id:
                d.update(data)
                return True
        return False


# ============================================================================
# Seed docs
# ============================================================================


def _seed_tests():
    return [
        {
            "test_id": "T-A",
            "store_id": STORE_A,
            "customer_id": CUSTOMER,
            "customer_phone": PHONE,
            "patient_name": "Asha",
            "status": "COMPLETED",
            "soap_note": {"chief_complaint": "blur", "assessment": "Myopia"},
        },
        {
            "test_id": "T-A2",
            "store_id": STORE_A,
            "customer_id": CUSTOMER,
            "customer_phone": PHONE,
            "patient_name": "Asha",
            "status": "IN_PROGRESS",
            "queue_id": "Q-A",
        },
        {
            "test_id": "T-B",
            "store_id": STORE_B,
            "customer_id": CUSTOMER,
            "customer_phone": PHONE,
            "patient_name": "Asha",
            "status": "COMPLETED",
        },
        {
            # Legacy doc minted before store stamping existed.
            "test_id": "T-LEGACY",
            "customer_id": CUSTOMER,
            "customer_phone": PHONE,
            "patient_name": "Asha",
            "status": "COMPLETED",
        },
    ]


def _seed_queue():
    return [
        {"queue_id": "Q-A", "store_id": STORE_A, "status": "WAITING",
         "patient_name": "Asha", "customer_phone": PHONE, "customer_id": CUSTOMER},
        {"queue_id": "Q-B", "store_id": STORE_B, "status": "WAITING",
         "patient_name": "Asha", "customer_phone": PHONE, "customer_id": CUSTOMER},
    ]


def _seed_rx():
    return [
        {
            "prescription_id": "RX-A",
            "store_id": STORE_A,
            "customer_id": CUSTOMER,
            "status": "in_progress",
            "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90},
            "left_eye": {"sph": "-1.25", "cyl": "-0.25", "axis": 85},
            "remarks": "initial",
        },
        {
            "prescription_id": "RX-FINAL-A",
            "store_id": STORE_A,
            "customer_id": CUSTOMER,
            "status": "in_progress",
            "versions": {
                "before_testing": None,
                "after_testing": None,
                "manual": None,
                "final": {
                    "right_eye": {"sphere": -1.0},
                    "left_eye": {"sphere": -1.25},
                    "pd": 62,
                },
            },
        },
        {
            # Legacy Rx with NO store stamp (pre-BUG-088 data).
            "prescription_id": "RX-LEGACY",
            "customer_id": CUSTOMER,
            "status": "in_progress",
            "right_eye": {"sph": "-1.00"},
            "left_eye": {"sph": "-1.00"},
        },
    ]


_GOOD_COMPLETE_BODY = {
    "rightEye": {"sphere": -1.25, "cylinder": -0.50, "axis": 90, "add": 0},
    "leftEye": {"sphere": -1.00, "cylinder": -0.25, "axis": 85, "add": 0},
    "pd": 62,
}


# ============================================================================
# Client builders -- EVERY accessor the touched handlers call is patched.
# ============================================================================


def _user(roles, store):
    async def _u():
        return {
            "user_id": "u-test",
            "username": "tester",
            "full_name": "Dr Test",
            "roles": list(roles),
            "store_ids": [store] if store else [],
            "active_store_id": store,
        }

    return _u


def _clinical_client(monkeypatch, *, roles=("OPTOMETRIST",), store=STORE_A,
                     test_repo=None, queue_repo=None, rx_repo=None):
    app = FastAPI()
    app.include_router(clinical.router, prefix="/api/v1/clinical")
    app.dependency_overrides[get_current_user] = _user(roles, store)
    monkeypatch.setattr(clinical, "get_eye_test_repository", lambda: test_repo)
    monkeypatch.setattr(clinical, "get_eye_test_queue_repository", lambda: queue_repo)
    monkeypatch.setattr(clinical, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(clinical, "get_audit_repository", lambda: None)
    return TestClient(app)


def _rx_client(monkeypatch, *, roles=("OPTOMETRIST",), store=STORE_A, rx_repo=None):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/api/v1/prescriptions")
    app.dependency_overrides[get_current_user] = _user(roles, store)
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(prescriptions, "get_audit_repository", lambda: None)
    return TestClient(app)


# ============================================================================
# P1 -- eye-test READS: role gate + per-object store scope (404-hide)
# ============================================================================


class TestEyeTestReadIDOR:
    def test_cross_store_optometrist_404_on_test_read(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, store=STORE_B, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get("/api/v1/clinical/tests/T-A")
        assert resp.status_code == 404

    def test_same_store_optometrist_reads_test(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, store=STORE_A, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get("/api/v1/clinical/tests/T-A")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "T-A"
        assert body["customerId"] == CUSTOMER

    def test_cross_store_optometrist_404_on_soap_note(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, store=STORE_B, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get("/api/v1/clinical/tests/T-A/soap-note")
        assert resp.status_code == 404

    def test_same_store_optometrist_reads_soap_note(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, store=STORE_A, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get("/api/v1/clinical/tests/T-A/soap-note")
        assert resp.status_code == 200
        note = resp.json()["soapNote"]
        assert note is not None
        assert note["chiefComplaint"] == "blur"

    def test_admin_reads_any_store(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, roles=("ADMIN",), store=STORE_A,
            test_repo=_FakeEyeTestRepo(_seed_tests()),
        )
        resp = client.get("/api/v1/clinical/tests/T-B")
        assert resp.status_code == 200
        assert resp.json()["id"] == "T-B"

    def test_legacy_unattributed_test_readable_by_clinical_role(self, monkeypatch):
        """A doc with NO store_id (legacy) stays readable by a store-level
        clinical role -- the role gate is the bound for unattributed records."""
        client = _clinical_client(
            monkeypatch, store=STORE_A, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get("/api/v1/clinical/tests/T-LEGACY")
        assert resp.status_code == 200
        assert resp.json()["id"] == "T-LEGACY"

    def test_workshop_staff_cross_store_denied(self, monkeypatch):
        """WORKSHOP_STAFF is in the read role set (lens fulfilment) but the
        per-object store scope still denies a foreign store's test (404)."""
        client = _clinical_client(
            monkeypatch, roles=("WORKSHOP_STAFF",), store=STORE_B,
            test_repo=_FakeEyeTestRepo(_seed_tests()),
        )
        resp = client.get("/api/v1/clinical/tests/T-A")
        assert resp.status_code == 404

    def test_cashier_denied_by_role_even_same_store(self, monkeypatch):
        """CASHIER (payment-only, no clinical need) is 403'd by the role gate
        even for the caller's OWN store."""
        client = _clinical_client(
            monkeypatch, roles=("CASHIER",), store=STORE_A,
            test_repo=_FakeEyeTestRepo(_seed_tests()),
        )
        resp = client.get("/api/v1/clinical/tests/T-A")
        assert resp.status_code == 403

    @pytest.mark.parametrize("role", ["ACCOUNTANT", "CATALOG_MANAGER"])
    def test_non_clinical_roles_denied_phone_lookup(self, monkeypatch, role):
        client = _clinical_client(
            monkeypatch, roles=(role,), store=STORE_A,
            test_repo=_FakeEyeTestRepo(_seed_tests()),
        )
        resp = client.get(f"/api/v1/clinical/tests/patient/{PHONE}")
        assert resp.status_code == 403


class TestPhoneAndCustomerLookupScoped:
    def test_phone_lookup_scoped_to_callers_store(self, monkeypatch):
        """The phone-enumerable lookup only returns the caller's store's tests
        (+ legacy unattributed) -- never another store's medical history."""
        client = _clinical_client(
            monkeypatch, store=STORE_A, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get(f"/api/v1/clinical/tests/patient/{PHONE}")
        assert resp.status_code == 200
        body = resp.json()
        ids = {t["id"] for t in body["tests"]}
        assert ids == {"T-A", "T-A2", "T-LEGACY"}
        assert "T-B" not in ids
        assert body["total"] == 3

    def test_customer_lookup_scoped_to_callers_store(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, store=STORE_B, test_repo=_FakeEyeTestRepo(_seed_tests())
        )
        resp = client.get(f"/api/v1/clinical/tests/customer/{CUSTOMER}")
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()["tests"]}
        assert ids == {"T-B", "T-LEGACY"}

    def test_admin_phone_lookup_sees_all_stores(self, monkeypatch):
        client = _clinical_client(
            monkeypatch, roles=("ADMIN",), store=STORE_A,
            test_repo=_FakeEyeTestRepo(_seed_tests()),
        )
        resp = client.get(f"/api/v1/clinical/tests/patient/{PHONE}")
        assert resp.status_code == 200
        ids = {t["id"] for t in resp.json()["tests"]}
        assert ids == {"T-A", "T-A2", "T-B", "T-LEGACY"}


# ============================================================================
# P2 -- clinical WRITES: store scope via the loaded doc (404-hide)
# ============================================================================


class TestClinicalWriteStoreScope:
    def test_queue_status_cross_store_404(self, monkeypatch):
        queue_repo = _FakeQueueRepo(_seed_queue())
        client = _clinical_client(monkeypatch, store=STORE_B, queue_repo=queue_repo)
        resp = client.patch(
            "/api/v1/clinical/queue/Q-A/status", json={"status": "COMPLETED"}
        )
        assert resp.status_code == 404
        assert queue_repo.status_calls == []

    def test_queue_status_same_store_ok(self, monkeypatch):
        queue_repo = _FakeQueueRepo(_seed_queue())
        client = _clinical_client(monkeypatch, store=STORE_A, queue_repo=queue_repo)
        resp = client.patch(
            "/api/v1/clinical/queue/Q-A/status", json={"status": "COMPLETED"}
        )
        assert resp.status_code == 200
        assert queue_repo.status_calls == [("Q-A", "COMPLETED")]

    def test_queue_delete_cross_store_404(self, monkeypatch):
        queue_repo = _FakeQueueRepo(_seed_queue())
        client = _clinical_client(monkeypatch, store=STORE_B, queue_repo=queue_repo)
        resp = client.delete("/api/v1/clinical/queue/Q-A")
        assert resp.status_code == 404
        assert queue_repo.removed == []

    def test_queue_delete_same_store_ok(self, monkeypatch):
        queue_repo = _FakeQueueRepo(_seed_queue())
        client = _clinical_client(monkeypatch, store=STORE_A, queue_repo=queue_repo)
        resp = client.delete("/api/v1/clinical/queue/Q-A")
        assert resp.status_code == 200
        assert queue_repo.removed == ["Q-A"]

    def test_start_test_cross_store_404(self, monkeypatch):
        queue_repo = _FakeQueueRepo(_seed_queue())
        test_repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(
            monkeypatch, store=STORE_B, queue_repo=queue_repo, test_repo=test_repo
        )
        resp = client.post("/api/v1/clinical/queue/Q-A/start-test")
        assert resp.status_code == 404
        assert queue_repo.status_calls == []

    def test_complete_cross_store_404(self, monkeypatch):
        test_repo = _FakeEyeTestRepo(_seed_tests())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(
            monkeypatch, store=STORE_B, test_repo=test_repo,
            queue_repo=_FakeQueueRepo(_seed_queue()), rx_repo=rx_repo,
        )
        resp = client.post(
            "/api/v1/clinical/tests/T-A2/complete", json=_GOOD_COMPLETE_BODY
        )
        assert resp.status_code == 404
        assert rx_repo.created == []
        # The test doc was never flipped COMPLETED.
        assert test_repo.find_by_id("T-A2")["status"] == "IN_PROGRESS"

    def test_complete_same_store_ok(self, monkeypatch):
        test_repo = _FakeEyeTestRepo(_seed_tests())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(
            monkeypatch, store=STORE_A, test_repo=test_repo,
            queue_repo=_FakeQueueRepo(_seed_queue()), rx_repo=rx_repo,
        )
        resp = client.post(
            "/api/v1/clinical/tests/T-A2/complete", json=_GOOD_COMPLETE_BODY
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["testId"] == "T-A2"
        assert body["prescriptionId"] is not None
        assert len(rx_repo.created) == 1
        assert rx_repo.created[0]["store_id"] == STORE_A

    def test_soap_note_write_cross_store_404(self, monkeypatch):
        test_repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(monkeypatch, store=STORE_B, test_repo=test_repo)
        resp = client.post(
            "/api/v1/clinical/tests/T-A/soap-note",
            json={"chiefComplaint": "tampered"},
        )
        assert resp.status_code == 404
        # The stored note is untouched.
        assert test_repo.find_by_id("T-A")["soap_note"]["chief_complaint"] == "blur"

    def test_soap_note_write_same_store_ok(self, monkeypatch):
        test_repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(monkeypatch, store=STORE_A, test_repo=test_repo)
        resp = client.post(
            "/api/v1/clinical/tests/T-A/soap-note",
            json={"chiefComplaint": "rechecked"},
        )
        assert resp.status_code == 200
        assert (
            test_repo.find_by_id("T-A")["soap_note"]["chief_complaint"] == "rechecked"
        )


# ============================================================================
# P1 -- prescriptions: version PATCH role gate + write-path store scope
# ============================================================================


class TestRxVersionWriteIDOR:
    @pytest.mark.parametrize("role", ["CASHIER", "SALES_STAFF", "WORKSHOP_STAFF"])
    def test_non_clinical_role_cannot_patch_version(self, monkeypatch, role):
        """The core P1: PATCH version had NO role gate. Read-only POS/workshop
        roles and payment-only cashiers must get the canonical clinical 403."""
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, roles=(role,), store=STORE_A, rx_repo=rx_repo)
        resp = client.patch(
            "/api/v1/prescriptions/RX-A/version/manual",
            json={"right_eye": {"sphere": -1.0}},
        )
        assert resp.status_code == 403
        assert "clinical" in resp.json()["detail"].lower()
        assert rx_repo.updates == []

    def test_cross_store_optometrist_404_on_version_write(self, monkeypatch):
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_B, rx_repo=rx_repo)
        resp = client.patch(
            "/api/v1/prescriptions/RX-A/version/manual",
            json={"right_eye": {"sphere": -1.0}},
        )
        assert resp.status_code == 404
        assert rx_repo.updates == []

    def test_same_store_optometrist_writes_version(self, monkeypatch):
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_A, rx_repo=rx_repo)
        resp = client.patch(
            "/api/v1/prescriptions/RX-A/version/manual",
            json={"right_eye": {"sphere": -2.0}, "source": "manual_override"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["prescription_id"] == "RX-A"
        assert body["versions"]["manual"]["right_eye"]["sphere"] == -2.0
        assert len(rx_repo.updates) == 1

    def test_put_cross_store_404(self, monkeypatch):
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_B, rx_repo=rx_repo)
        resp = client.put("/api/v1/prescriptions/RX-A", json={"remarks": "x"})
        assert resp.status_code == 404
        assert rx_repo.updates == []

    def test_put_same_store_still_works(self, monkeypatch):
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_A, rx_repo=rx_repo)
        resp = client.put("/api/v1/prescriptions/RX-A", json={"remarks": "edited"})
        assert resp.status_code == 200
        assert rx_repo.find_by_id("RX-A")["remarks"] == "edited"

    def test_finalize_cross_store_404(self, monkeypatch):
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_B, rx_repo=rx_repo)
        resp = client.post("/api/v1/prescriptions/RX-FINAL-A/finalize")
        assert resp.status_code == 404
        assert rx_repo.updates == []

    def test_finalize_same_store_ok(self, monkeypatch):
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_A, rx_repo=rx_repo)
        resp = client.post("/api/v1/prescriptions/RX-FINAL-A/finalize")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "finalized"
        assert rx_repo.find_by_id("RX-FINAL-A")["status"] == "finalized"

    def test_legacy_unattributed_rx_blocked_for_store_role(self, monkeypatch):
        """prescriptions mirrors its own BUG-088 read semantics: an Rx with NO
        store_id is out of reach for store-level roles (they can't read it
        either), so the write is 404-hidden too..."""
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(monkeypatch, store=STORE_A, rx_repo=rx_repo)
        resp = client.patch(
            "/api/v1/prescriptions/RX-LEGACY/version/manual",
            json={"right_eye": {"sphere": -1.0}},
        )
        assert resp.status_code == 404
        assert rx_repo.updates == []

    def test_legacy_unattributed_rx_writable_by_admin(self, monkeypatch):
        """...while a cross-store ADMIN can still maintain legacy records."""
        rx_repo = _FakeRxRepo(_seed_rx())
        client = _rx_client(
            monkeypatch, roles=("ADMIN",), store=STORE_A, rx_repo=rx_repo
        )
        resp = client.patch(
            "/api/v1/prescriptions/RX-LEGACY/version/manual",
            json={"right_eye": {"sphere": -1.0}},
        )
        assert resp.status_code == 200, resp.text
        assert len(rx_repo.updates) == 1


# ============================================================================
# POLICY rows stay reconciled with the route gates (drift lock)
# ============================================================================


class TestPolicyRowsMatchRouteGates:
    _CLINICAL_READ_PATHS = [
        ("GET", "/api/v1/clinical/tests/{test_id}"),
        ("GET", "/api/v1/clinical/tests/{test_id}/soap-note"),
        ("GET", "/api/v1/clinical/tests/patient/{customer_phone}"),
        ("GET", "/api/v1/clinical/tests/customer/{customer_id}"),
    ]

    def test_clinical_read_rows_match_rx_read_roles(self):
        """The 4 tightened clinical read rows must list EXACTLY the
        prescriptions read set (_RX_READ_ROLES) -- never drift apart."""
        from api.services import rbac_policy as rbac
        from api.routers.prescriptions import _RX_READ_ROLES

        for method, path in self._CLINICAL_READ_PATHS:
            entry = rbac.policy_for(method, path)
            assert entry is not None, f"{method} {path} not catalogued"
            assert isinstance(entry["allowed"], list), (method, path)
            assert set(entry["allowed"]) == set(_RX_READ_ROLES), (method, path)

    def test_version_patch_row_matches_write_gate(self):
        from api.services import rbac_policy as rbac
        from api.routers.prescriptions import _RX_WRITE_ROLES

        entry = rbac.policy_for(
            "PATCH", "/api/v1/prescriptions/RX-1/version/manual"
        )
        assert entry is not None
        assert set(entry["allowed"]) == set(_RX_WRITE_ROLES)
        # Route raises the body-specific clinical 403 -> enforcer must defer.
        assert entry.get("self_enforced") is True
