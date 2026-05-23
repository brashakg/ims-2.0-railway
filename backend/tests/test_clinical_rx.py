"""
IMS 2.0 - Clinical Rx print + redo tracking
============================================
Two things under test:

1. `format_rx_value` -- the PURE renderer for an Rx power. Must emit an explicit
   sign + 2 decimals for non-zero, "Plano" for zero, and "" for None/missing,
   regardless of whether the stored value is an int, float, or numeric string
   (the prescriptions collection stores eye powers as strings).

2. Redo-endpoint role gating. Recording / reading redos must be restricted to
   optometry + manager roles (SALES_STAFF -> 403, OPTOMETRIST -> not 403).
   Mirrors the bare-app + dependency-override pattern in test_expenses_gating.py
   so no database is required.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers.clinical import format_rx_value, format_axis_value  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# format_rx_value -- pure renderer
# ============================================================================


class TestFormatRxValue:
    def test_none_is_blank(self):
        assert format_rx_value(None) == ""

    def test_empty_string_is_blank(self):
        assert format_rx_value("") == ""
        assert format_rx_value("   ") == ""

    def test_zero_is_plano(self):
        assert format_rx_value(0) == "Plano"
        assert format_rx_value(0.0) == "Plano"
        assert format_rx_value("0") == "Plano"
        assert format_rx_value("0.00") == "Plano"

    def test_negative_zero_is_plano(self):
        assert format_rx_value(-0.0) == "Plano"

    def test_positive_gets_explicit_plus_and_two_decimals(self):
        assert format_rx_value(0.5) == "+0.50"
        assert format_rx_value(1.25) == "+1.25"
        assert format_rx_value(2) == "+2.00"

    def test_negative_gets_minus_and_two_decimals(self):
        assert format_rx_value(-1.25) == "-1.25"
        assert format_rx_value(-0.75) == "-0.75"
        assert format_rx_value(-20) == "-20.00"

    def test_numeric_strings_are_parsed(self):
        assert format_rx_value("-1.25") == "-1.25"
        assert format_rx_value("+0.5") == "+0.50"
        assert format_rx_value("3.5") == "+3.50"

    def test_non_numeric_junk_is_blank(self):
        assert format_rx_value("abc") == ""
        assert format_rx_value("N/A") == ""

    def test_boundary_powers(self):
        # SPH range -20..+20, ADD up to +3.50 -- all render cleanly.
        assert format_rx_value(20.0) == "+20.00"
        assert format_rx_value(-6.0) == "-6.00"
        assert format_rx_value(3.5) == "+3.50"


class TestFormatAxisValue:
    def test_none_blank(self):
        assert format_axis_value(None) == ""
        assert format_axis_value("") == ""

    def test_whole_degrees(self):
        assert format_axis_value(90) == "90"
        assert format_axis_value(180) == "180"
        assert format_axis_value(1) == "1"

    def test_string_and_float_coerced_to_int(self):
        assert format_axis_value("45") == "45"
        assert format_axis_value(45.0) == "45"

    def test_junk_blank(self):
        assert format_axis_value("xyz") == ""


# ============================================================================
# Redo endpoint role gating (no DB needed)
# ============================================================================


def _client_as(roles):
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# (method, path, json-body) for the write endpoint that must be gated.
REDO_WRITE = ("post", "/clinical/prescriptions/rx-1/redo", {"reason": "lens remake"})


class TestRedoGating:
    def test_sales_staff_blocked(self):
        client = _client_as(["SALES_STAFF"])
        method, path, body = REDO_WRITE
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 403

    def test_cashier_blocked(self):
        client = _client_as(["CASHIER"])
        method, path, body = REDO_WRITE
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 403

    def test_optometrist_allowed(self):
        # No DB -> 503, but crucially NOT 403: the role gate let the request
        # through to the handler.
        client = _client_as(["OPTOMETRIST"])
        method, path, body = REDO_WRITE
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code != 403

    def test_store_manager_allowed(self):
        client = _client_as(["STORE_MANAGER"])
        method, path, body = REDO_WRITE
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code != 403

    def test_superadmin_allowed(self):
        client = _client_as(["SUPERADMIN"])
        method, path, body = REDO_WRITE
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code != 403


def _client_no_db(monkeypatch, roles):
    """Bare app with the prescription + store repos forced to None -> exercises
    the genuine fail-soft (no-DB) branches regardless of seeded test DB state."""
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(clinical, "get_prescription_repository", lambda: None)
    monkeypatch.setattr(clinical, "get_store_repository", lambda: None)
    return TestClient(app)


class TestRedoListAndPrintReadable:
    """Reads (redo history, print) are open to any authenticated user and must
    not 403; with no DB they fail soft rather than crashing."""

    def test_redo_history_not_forbidden_for_sales_staff(self):
        # Role gate only: any authenticated user may read (200/404, never 403).
        client = _client_as(["SALES_STAFF"])
        resp = client.get("/clinical/prescriptions/rx-1/redos")
        assert resp.status_code != 403

    def test_redo_history_fail_soft_when_no_db(self, monkeypatch):
        client = _client_no_db(monkeypatch, ["SALES_STAFF"])
        resp = client.get("/clinical/prescriptions/rx-1/redos")
        assert resp.status_code == 200
        assert resp.json() == {"redos": [], "total": 0}

    def test_print_not_forbidden_for_sales_staff(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.get("/clinical/prescriptions/rx-1/print")
        assert resp.status_code != 403

    def test_print_fails_soft_when_no_db(self, monkeypatch):
        client = _client_no_db(monkeypatch, ["SALES_STAFF"])
        resp = client.get("/clinical/prescriptions/rx-1/print")
        # No DB -> still a valid printable card (HTML), never a 500.
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "PRESCRIPTION" in resp.text


# ============================================================================
# Redo recording against a fake repo (happy path, no real DB)
# ============================================================================


class _FakeRxRepo:
    """Minimal stand-in for PrescriptionRepository: in-memory doc + update."""

    def __init__(self, doc):
        self._doc = doc

    def find_by_id(self, _id):
        return self._doc if self._doc and self._doc.get("prescription_id") == _id else None

    def update(self, _id, data):
        if not self._doc or self._doc.get("prescription_id") != _id:
            return False
        self._doc.update(data)
        return True


class TestRedoRecording:
    def _client_with_repo(self, monkeypatch, roles, repo):
        app = FastAPI()
        app.include_router(clinical.router, prefix="/clinical")

        async def _fake_user():
            return {
                "user_id": "u1",
                "username": "tester",
                "full_name": "Dr Test",
                "active_store_id": "store-001",
                "roles": roles,
            }

        app.dependency_overrides[get_current_user] = _fake_user
        monkeypatch.setattr(clinical, "get_prescription_repository", lambda: repo)
        return TestClient(app)

    def test_redo_appended_and_counted(self, monkeypatch):
        repo = _FakeRxRepo({"prescription_id": "rx-9", "patient_name": "A"})
        client = self._client_with_repo(monkeypatch, ["OPTOMETRIST"], repo)

        resp = client.post(
            "/clinical/prescriptions/rx-9/redo", json={"reason": "wrong axis"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["redoCount"] == 1
        assert data["redo"]["reason"] == "wrong axis"
        assert data["redo"]["redo_by_name"] == "Dr Test"
        # Persisted onto the doc.
        assert repo._doc["redo_count"] == 1
        assert repo._doc["redo_reason"] == "wrong axis"
        assert len(repo._doc["redos"]) == 1

        # A second redo appends, not overwrites.
        resp2 = client.post(
            "/clinical/prescriptions/rx-9/redo", json={"reason": "coating defect"}
        )
        assert resp2.status_code == 200
        assert resp2.json()["redoCount"] == 2
        assert len(repo._doc["redos"]) == 2

    def test_redo_404_when_missing(self, monkeypatch):
        repo = _FakeRxRepo(None)
        client = self._client_with_repo(monkeypatch, ["OPTOMETRIST"], repo)
        resp = client.post(
            "/clinical/prescriptions/nope/redo", json={"reason": "x"}
        )
        assert resp.status_code == 404

    def test_redo_requires_reason(self, monkeypatch):
        repo = _FakeRxRepo({"prescription_id": "rx-9"})
        client = self._client_with_repo(monkeypatch, ["OPTOMETRIST"], repo)
        # Empty reason fails validation (422), never silently records a blank.
        resp = client.post(
            "/clinical/prescriptions/rx-9/redo", json={"reason": ""}
        )
        assert resp.status_code == 422

    def test_history_returns_recorded_redos(self, monkeypatch):
        repo = _FakeRxRepo(
            {
                "prescription_id": "rx-9",
                "redos": [
                    {"redo_id": "r1", "reason": "first"},
                    {"redo_id": "r2", "reason": "second"},
                ],
            }
        )
        client = self._client_with_repo(monkeypatch, ["SALES_STAFF"], repo)
        resp = client.get("/clinical/prescriptions/rx-9/redos")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["redos"][0]["reason"] == "first"


# ============================================================================
# A5 card HTML rendering (pure builder)
# ============================================================================


class TestRxCardHtml:
    def test_card_contains_powers_and_patient(self):
        rx = {
            "prescription_id": "rx-1",
            "prescription_number": "RX-260523-ABC123",
            "patient_name": "Ravi Kumar",
            "age": 42,
            "customer_phone": "9876543210",
            "optometrist_name": "Dr Mehta",
            "right_eye": {"sph": "-1.25", "cyl": "-0.50", "axis": 90, "add": "0"},
            "left_eye": {"sph": "0", "cyl": "", "axis": None, "add": "+2.00"},
            "pd": 62,
        }
        store = {
            "store_name": "Better Vision Bokaro",
            "city": "Bokaro",
            "phone": "0654-1234567",
        }
        html = clinical._build_rx_card_html(rx, store)
        assert "@page" in html and "A5" in html
        assert "Better Vision Bokaro" in html
        assert "Ravi Kumar" in html
        assert "-1.25" in html  # OD sphere
        assert "Plano" in html  # OD add == 0 and OS sphere == 0
        assert "+2.00" in html  # OS add
        assert "62 mm" in html  # PD
        assert "Dr Mehta" in html

    def test_card_renders_with_no_store(self):
        # Fail-soft: missing store still produces a valid card.
        html = clinical._build_rx_card_html({"prescription_id": "rx-1"}, None)
        assert "<html" in html
        assert "PRESCRIPTION" in html

    def test_card_escapes_html_in_patient_name(self):
        rx = {"prescription_id": "rx-1", "patient_name": "<script>x</script>"}
        html = clinical._build_rx_card_html(rx, None)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html
