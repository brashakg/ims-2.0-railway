"""
IMS 2.0 - Workshop lens-order lifecycle + ready-notify
======================================================
Two layers of coverage:

  1. The PURE transition guard `_next_lens_status_ok` — forward-only along
     NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED. Valid single forward steps
     allowed; skips, backwards moves, no-ops, and unknown values rejected.
  2. Endpoint role gating for POST /workshop/jobs/{id}/lens-status and
     /notify-ready. SALES_STAFF (and other non-workshop roles) must be 403;
     WORKSHOP_STAFF (and SUPERADMIN) must NOT be 403. Mounts the router on a
     bare FastAPI() app and overrides get_current_user — same pattern as
     test_expenses_gating.py — so no database is needed.
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

from api.routers import workshop  # noqa: E402
from api.routers.workshop import _next_lens_status_ok  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# 1. Pure transition guard
# ============================================================================


class TestNextLensStatusOk:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("NOT_ORDERED", "ORDERED"),
            ("ORDERED", "RECEIVED"),
            ("RECEIVED", "MOUNTED"),
        ],
    )
    def test_valid_forward_allowed(self, current, target):
        assert _next_lens_status_ok(current, target) is True

    def test_missing_current_treated_as_not_ordered(self):
        # Legacy job with no lens_status set can still be moved to ORDERED.
        assert _next_lens_status_ok(None, "ORDERED") is True
        assert _next_lens_status_ok("", "ORDERED") is True
        assert _next_lens_status_ok("GARBAGE", "ORDERED") is True

    @pytest.mark.parametrize(
        "current,target",
        [
            # Skips (more than one step forward)
            ("NOT_ORDERED", "RECEIVED"),
            ("NOT_ORDERED", "MOUNTED"),
            ("ORDERED", "MOUNTED"),
            # Backwards
            ("ORDERED", "NOT_ORDERED"),
            ("RECEIVED", "ORDERED"),
            ("MOUNTED", "RECEIVED"),
            ("MOUNTED", "NOT_ORDERED"),
        ],
    )
    def test_skip_and_backward_rejected(self, current, target):
        assert _next_lens_status_ok(current, target) is False

    @pytest.mark.parametrize(
        "status", ["NOT_ORDERED", "ORDERED", "RECEIVED", "MOUNTED"]
    )
    def test_noop_rejected(self, status):
        # Same -> same is not a forward step.
        assert _next_lens_status_ok(status, status) is False

    def test_terminal_has_no_forward(self):
        assert _next_lens_status_ok("MOUNTED", "MOUNTED") is False

    @pytest.mark.parametrize(
        "current,target",
        [
            ("ORDERED", "NONSENSE"),
            ("ORDERED", ""),
            ("ORDERED", None),
        ],
    )
    def test_unknown_target_rejected(self, current, target):
        assert _next_lens_status_ok(current, target) is False


# ============================================================================
# 2. Endpoint role gating (bare app + dependency override, no DB)
# ============================================================================


def _client_as(roles):
    app = FastAPI()
    app.include_router(workshop.router, prefix="/workshop")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# (method, path, json_body)
GATED = [
    ("post", "/workshop/jobs/j1/lens-status", {"status": "ORDERED"}),
    ("post", "/workshop/jobs/j1/notify-ready", None),
]


class TestLensLifecycleGating:
    @pytest.mark.parametrize("method,path,body", GATED)
    def test_sales_staff_blocked(self, method, path, body):
        client = _client_as(["SALES_STAFF"])
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,body", GATED)
    def test_cashier_blocked(self, method, path, body):
        client = _client_as(["CASHIER"])
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,body", GATED)
    def test_optometrist_blocked(self, method, path, body):
        client = _client_as(["OPTOMETRIST"])
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,body", GATED)
    def test_workshop_staff_allowed(self, method, path, body):
        # Passes the gate. Without a DB the repo is None -> 503, which is
        # explicitly NOT 403 (the gate let it through).
        client = _client_as(["WORKSHOP_STAFF"])
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code != 403

    @pytest.mark.parametrize("method,path,body", GATED)
    def test_store_manager_allowed(self, method, path, body):
        client = _client_as(["STORE_MANAGER"])
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code != 403

    @pytest.mark.parametrize("method,path,body", GATED)
    def test_superadmin_allowed(self, method, path, body):
        client = _client_as(["SUPERADMIN"])
        resp = getattr(client, method)(path, json=body)
        assert resp.status_code != 403
