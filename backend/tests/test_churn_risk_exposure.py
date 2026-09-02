# ============================================================================
# A contact list, not a data export
# ============================================================================
# GET /crm/customers/churn-risk/list was AUTHENTICATED - any signed-in user -
# while the only screen that calls it (/customers/segmentation) was gated to
# SUPERADMIN / ADMIN / STORE_MANAGER. Gated on the page, not on the server.
#
# And the handler returned `{**customer}` for up to 500 rows (limit is
# ge=1, le=500), so a cashier could ask for the lot and receive five hundred
# COMPLETE customer documents: address, credit limit, GSTIN, store-credit
# balance, family members.
#
# Two independent failures, so two independent fixes and two sets of tests:
# the role gate closes the door, and the projection means that even a caller
# who is allowed through cannot pull fields the screen never shows.

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.routers.crm as crm  # noqa: E402

# A customer document carrying every sensitive field a real one carries.
SENSITIVE = {
    "customer_id": "C-1",
    "name": "Ramesh Kumar",
    "phone": "9100000001",
    "mobile": "9100000001",
    "email": "r@example.com",
    "loyalty_points": 120,
    "total_purchases": 4,
    # None of the following may ever leave this endpoint.
    "address": "12 MG Road, Bokaro",
    "gstin": "20AAACR0000A1ZZ",
    "credit_limit": 50000,
    "ar_outstanding": 12500,
    "store_credit_balance": 3400,
    "date_of_birth": "1979-04-02",
    "patients": [{"name": "Aarav Kumar", "mobile": "9100000002"}],
    "notes": "prefers evening appointments",
}

LEAKY_KEYS = (
    "address", "gstin", "credit_limit", "ar_outstanding",
    "store_credit_balance", "date_of_birth", "patients", "notes",
)


def test_the_row_carries_only_what_the_panel_renders():
    row = crm._churn_row(SENSITIVE, "high", 210, 4)
    for key in LEAKY_KEYS:
        assert key not in row, f"churn row still leaks {key!r}: {row}"


def test_the_row_still_carries_what_the_panel_needs():
    """The negative control. Without it, a projection returning {} would pass
    every leak assertion above."""
    row = crm._churn_row(SENSITIVE, "high", 210, 4)
    for key in ("customer_id", "name", "phone", "mobile", "loyalty_points"):
        assert row.get(key) == SENSITIVE[key], key
    assert row["churn_risk_level"] == "high"
    assert row["days_since_last_purchase"] == 210
    assert row["total_orders"] == 4


def test_a_sparse_customer_does_not_crash_or_invent_fields():
    row = crm._churn_row({"customer_id": "C-2"}, "low", 40, 1)
    assert row["customer_id"] == "C-2"
    assert "name" not in row  # absent, not None - the shape mirrors the doc
    assert row["churn_risk_level"] == "low"


def test_the_endpoint_is_manager_gated_not_merely_authenticated():
    """The role gate, read off the route's own dependency.

    Asserted structurally because the alternative - standing up the whole app
    with a cashier token - is a much heavier test for a one-line contract, and
    this is the line that actually decides who gets the data.
    """
    import inspect

    src = inspect.getsource(crm.get_churn_risk_customers)
    body = src[: src.index('"""')]
    assert "require_roles(" in body, (
        "the churn-risk route is not role-gated - it was AUTHENTICATED, which "
        "let any signed-in cashier read the customer book"
    )
    assert "get_current_user" not in body, body


def test_the_policy_registry_agrees_with_the_route():
    """A route gate and a policy row that disagree is how this repo's RBAC has
    drifted before: the middleware consults the REGISTRY, so a tightened route
    with a stale AUTHENTICATED row is still reachable through it."""
    from api.services import rbac_policy

    rows = [
        r for r in rbac_policy.POLICY
        if r.get("path") == "/api/v1/crm/customers/churn-risk/list"
        and r.get("method") == "GET"
    ]
    assert len(rows) == 1, rows
    allowed = rows[0]["allowed"]
    assert allowed != "AUTHENTICATED", "policy row still lets every signed-in user in"
    assert set(allowed) == {"SUPERADMIN", "ADMIN", "STORE_MANAGER"}, allowed


def test_the_rfm_report_is_flagged_not_fixed_here():
    """GET /crm/customers/segment/rfm has the same AUTHENTICATED shape and loads
    the whole customer collection. It is a separate decision (it publishes
    average customer value, which is business data rather than personal data),
    so this test records that it is KNOWN and still open rather than letting it
    pass unnoticed. Delete this test when that door is dealt with."""
    from api.services import rbac_policy

    rows = [
        r for r in rbac_policy.POLICY
        if r.get("path") == "/api/v1/crm/customers/segment/rfm"
    ]
    assert rows, "the RFM row vanished - re-check what replaced it"
    assert rows[0]["allowed"] == "AUTHENTICATED", (
        "the RFM door changed. If it was tightened deliberately, update or "
        "delete this reminder test."
    )
