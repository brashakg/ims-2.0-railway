"""
IMS 2.0 - Input-validation hardening sweep
==========================================
A backend write-path audit found a handful of WRITE endpoints that accepted
illegal / unbounded values which could be persisted, bypassing the
non-negotiable business rules (CLAUDE.md / SYSTEM_INTENT):

  - clinical POST /clinical/tests/{id}/complete: captured an eye test AND
    auto-created a prescription from raw right_eye/left_eye dicts with NO Rx
    range check -- an out-of-range SPH/CYL/AXIS/ADD (which the prescriptions
    endpoint rejects) could be saved through this side door.

  - vendors POST /vendors/purchase-orders: PO line quantity / unit_price were
    unbounded (negative qty or price persisted a corrupt PO + poisoned the
    subtotal/GST math).

  - vendors POST /vendors/grn: received / accepted / rejected receipt
    quantities were unbounded (a negative count mints a negative stock
    movement).

  - expenses POST /expenses and POST /expenses/advances: the rupee amount was
    unbounded -- a zero / negative amount slipped past the cap check
    (check_cap explicitly skips non-positive amounts) straight into the DB.

Each endpoint now routes its values through the shared guards / Pydantic
Field bounds. These tests assert, for each newly-guarded endpoint, that an
ILLEGAL value -> 4xx and a VALID value is accepted (not the validation
rejection). They use the standalone-app + dependency-override pattern from
test_pos_authz.py so they never need a live DB: schema (422) validation fires
before any DB access, and the clinical Rx guard runs before the repo lookup.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical, vendors, expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _client(router, prefix, roles=("SUPERADMIN",), uid="u1"):
    """Standalone app for one router with an authenticated SUPERADMIN override
    (auto-passes every require_roles gate, so the test isolates input
    validation, not authz). No DB is wired -> repos resolve to None and a
    VALID body reaches the demo fallback / a non-422 response."""
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    async def _u():
        return {
            "user_id": uid,
            "full_name": "T",
            "username": "t",
            "roles": list(roles),
            "store_ids": ["S1"],
            "active_store_id": "S1",
            "discount_cap": None,
        }

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


# ============================================================================
# clinical POST /tests/{id}/complete -- Rx range guard on the eye-test capture
# ============================================================================


def _clinical_client():
    return _client(clinical.router, "/api/v1/clinical")


def _eye(sph="-1.25", cyl="-0.50", axis=90, add="0"):
    return {"sph": sph, "cyl": cyl, "axis": axis, "add": add}


def _complete_body(right=None, left=None):
    return {
        "rightEye": right if right is not None else _eye(),
        "leftEye": left if left is not None else _eye(),
        "pd": 62.0,
    }


def test_clinical_complete_rejects_out_of_range_sph():
    # SPH +25 is outside the -20..+20 clinical range.
    body = _complete_body(right=_eye(sph="25"))
    r = _clinical_client().post("/api/v1/clinical/tests/t1/complete", json=body)
    assert r.status_code == 422


def test_clinical_complete_rejects_out_of_range_cyl():
    # CYL -8 is outside the -6..+6 spectacle range.
    body = _complete_body(left=_eye(cyl="-8.00"))
    r = _clinical_client().post("/api/v1/clinical/tests/t1/complete", json=body)
    assert r.status_code == 422


def test_clinical_complete_rejects_off_step_sph():
    # +1.30 is not on the 0.25-diopter grid.
    body = _complete_body(right=_eye(sph="1.30"))
    r = _clinical_client().post("/api/v1/clinical/tests/t1/complete", json=body)
    assert r.status_code == 422


def test_clinical_complete_rejects_bad_axis():
    # AXIS 200 is outside 1..180.
    body = _complete_body(right=_eye(axis=200))
    r = _clinical_client().post("/api/v1/clinical/tests/t1/complete", json=body)
    assert r.status_code == 422


def test_clinical_complete_accepts_valid_rx():
    # A clean in-range Rx (with a blank/zero cell + None axis on the left) must
    # pass validation. complete_test now 404s an unknown test_id (it no longer
    # mints an orphan Rx via a demo fallback), so a valid Rx on the synthetic
    # "t1" yields 200 (if resolvable) or 404 (not found) -- never 422. Only a
    # 422 would mean the valid Rx was wrongly rejected.
    body = _complete_body(
        right=_eye(sph="-1.25", cyl="-0.50", axis=90, add="0"),
        left={"sph": "0", "cyl": "", "axis": None, "add": "+2.00"},
    )
    r = _clinical_client().post("/api/v1/clinical/tests/t1/complete", json=body)
    assert r.status_code != 422
    assert r.status_code in (200, 404)


# ============================================================================
# vendors POST /purchase-orders -- PO line quantity / unit_price bounds
# ============================================================================


def _vendors_client():
    return _client(vendors.router, "/api/v1/vendors")


def _po_body(quantity=10, unit_price=100.0):
    return {
        "vendor_id": "v1",
        "delivery_store_id": "S1",
        "items": [
            {
                "product_id": "p1",
                "product_name": "Frame",
                "sku": "SKU1",
                "quantity": quantity,
                "unit_price": unit_price,
            }
        ],
    }


def test_po_rejects_zero_quantity():
    r = _vendors_client().post(
        "/api/v1/vendors/purchase-orders", json=_po_body(quantity=0)
    )
    assert r.status_code == 422


def test_po_rejects_negative_quantity():
    r = _vendors_client().post(
        "/api/v1/vendors/purchase-orders", json=_po_body(quantity=-5)
    )
    assert r.status_code == 422


def test_po_rejects_negative_unit_price():
    r = _vendors_client().post(
        "/api/v1/vendors/purchase-orders", json=_po_body(unit_price=-1.0)
    )
    assert r.status_code == 422


def test_po_accepts_valid_line():
    # Valid line clears validation + the role gate; no DB -> non-422 response.
    r = _vendors_client().post(
        "/api/v1/vendors/purchase-orders", json=_po_body(quantity=10, unit_price=100.0)
    )
    assert r.status_code != 422
    assert r.status_code != 403


# ============================================================================
# vendors POST /grn -- receipt quantity bounds (ge=0)
# ============================================================================


def _grn_body(received=10, accepted=10, rejected=0):
    return {
        "po_id": "po1",
        "vendor_invoice_no": "INV-1",
        "vendor_invoice_date": "2026-05-21",
        "items": [
            {
                "po_item_id": "pi1",
                "product_id": "p1",
                "received_qty": received,
                "accepted_qty": accepted,
                "rejected_qty": rejected,
            }
        ],
    }


def test_grn_rejects_negative_received_qty():
    r = _vendors_client().post("/api/v1/vendors/grn", json=_grn_body(received=-1))
    assert r.status_code == 422


def test_grn_rejects_negative_accepted_qty():
    r = _vendors_client().post("/api/v1/vendors/grn", json=_grn_body(accepted=-3))
    assert r.status_code == 422


def test_grn_rejects_negative_rejected_qty():
    r = _vendors_client().post("/api/v1/vendors/grn", json=_grn_body(rejected=-2))
    assert r.status_code == 422


def test_grn_accepts_valid_receipt():
    r = _vendors_client().post(
        "/api/v1/vendors/grn", json=_grn_body(received=10, accepted=8, rejected=2)
    )
    assert r.status_code != 422
    assert r.status_code != 403


# ============================================================================
# expenses POST / and POST /advances -- positive-amount guard (gt=0)
# ============================================================================


def _expenses_client():
    return _client(expenses.router, "/api/v1/expenses")


def _expense_body(amount=100.0):
    return {
        "category": "TRAVEL",
        "amount": amount,
        "description": "Cab to store",
        "expense_date": "2026-05-29",
    }


def _advance_body(amount=500.0):
    return {
        "advance_type": "TRAVEL",
        "amount": amount,
        "purpose": "Site visit",
    }


def test_expense_rejects_zero_amount():
    r = _expenses_client().post("/api/v1/expenses", json=_expense_body(amount=0))
    assert r.status_code == 422


def test_expense_rejects_negative_amount():
    r = _expenses_client().post("/api/v1/expenses", json=_expense_body(amount=-50.0))
    assert r.status_code == 422


def test_expense_accepts_positive_amount():
    # Valid amount clears validation; no DB -> non-422 response (not the
    # Pydantic rejection).
    r = _expenses_client().post("/api/v1/expenses", json=_expense_body(amount=100.0))
    assert r.status_code != 422


def test_advance_rejects_zero_amount():
    r = _expenses_client().post("/api/v1/expenses/advances", json=_advance_body(amount=0))
    assert r.status_code == 422


def test_advance_rejects_negative_amount():
    r = _expenses_client().post(
        "/api/v1/expenses/advances", json=_advance_body(amount=-100.0)
    )
    assert r.status_code == 422


def test_advance_accepts_positive_amount():
    r = _expenses_client().post(
        "/api/v1/expenses/advances", json=_advance_body(amount=500.0)
    )
    assert r.status_code != 422
