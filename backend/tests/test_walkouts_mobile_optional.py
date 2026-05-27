"""
IMS 2.0 - Walkouts mobile-optional tests (Module i, mobile-optional patch)
=========================================================================
Owner asked: "make mobile number optional in pos walkout as some
customers do not share but warn before saving" — so:

  * Empty mobile is accepted at the API and stored as None (the FE
    surfaces the warning).
  * A partial mobile (1-9 digits or non-numeric) is still rejected at
    422 so we don't silently log junk numbers.
  * When mobile is absent, the customer-auto-create skeleton step is
    SKIPPED (no orphan rows on a no-mobile walk-in) and the walkout
    doc carries customer_id=None.

These tests reuse the in-memory FakeDB fixture from test_walkouts.py
so they don't need a real Mongo.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Re-use the patched_walkouts fixture wholesale.
from test_walkouts import patched_walkouts, _full_payload  # noqa: E402,F401


# ----------------------------------------------------------------------------
# Empty / partial mobile handling
# ----------------------------------------------------------------------------


def test_create_walkout_with_empty_mobile_succeeds(
    client, auth_headers, patched_walkouts
):
    """Empty string mobile is accepted (server normalizes to None)
    and the walkout persists without a phone link."""
    payload = _full_payload(mobile="")
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Server normalised to None
    assert body.get("mobile") in (None, "")
    # And the walkout still gets a walkout_id
    assert body["walkout_id"].startswith("WO-")


def test_create_walkout_with_null_mobile_succeeds(
    client, auth_headers, patched_walkouts
):
    """Explicit null mobile is also fine — same as omitting the field."""
    payload = _full_payload()
    payload["mobile"] = None
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("mobile") in (None, "")


def test_empty_mobile_does_not_auto_create_customer(
    client, auth_headers, patched_walkouts
):
    """No mobile -> no skeleton customer is auto-created and the
    walkout's customer_id stays None. This is the load-bearing
    invariant: a phone-less walk-in must not produce an orphan
    'cust-XXXXXXXX' record with mobile=''."""
    customer_repo = patched_walkouts["customer_repo"]
    audit_repo = patched_walkouts["audit_repo"]
    pre_customer_count = len(customer_repo.collection.docs)

    payload = _full_payload(mobile="")
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # customer_id is None on the walkout doc
    assert body["customer_id"] is None
    # And the customer collection was not touched
    assert len(customer_repo.collection.docs) == pre_customer_count
    # Audit trail: a walkout.create row exists but NO customer.create
    actions = [d.get("action") for d in audit_repo.collection.docs]
    assert "walkout.create" in actions
    assert "customer.create" not in actions


@pytest.mark.parametrize(
    "bad_mobile",
    ["1", "12345", "123456789", "12345678901", "abcdefghij", "98765abcde"],
)
def test_partial_or_non_numeric_mobile_still_rejected(
    client, auth_headers, patched_walkouts, bad_mobile
):
    """A 1-9 digit number or mixed-character string is still 422 — the
    optional path only opens up empty/null, not garbage values."""
    payload = _full_payload(mobile=bad_mobile)
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 422


def test_ten_digit_mobile_still_auto_creates_customer(
    client, auth_headers, patched_walkouts
):
    """The 10-digit happy path is unchanged — auto-create still fires."""
    customer_repo = patched_walkouts["customer_repo"]
    payload = _full_payload(mobile="9988776655", customer_name="With Mobile")
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["customer_id"] is not None
    # A customer was created
    cust = next(
        c for c in customer_repo.collection.docs if c["mobile"] == "9988776655"
    )
    assert cust["name"] == "With Mobile"


# ----------------------------------------------------------------------------
# PATCH update — same validator
# ----------------------------------------------------------------------------


def test_patch_clearing_mobile_succeeds(client, auth_headers, patched_walkouts):
    """A logged walkout can have its mobile cleared via PATCH
    (customer asks not to be contacted)."""
    # Seed with a mobile, then clear it.
    payload = _full_payload(mobile="9111122223")
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    walkout_id = resp.json()["walkout_id"]

    resp = client.patch(
        f"/api/v1/walkouts/{walkout_id}",
        json={"mobile": ""},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("mobile") in (None, "")


def test_patch_partial_mobile_rejected(client, auth_headers, patched_walkouts):
    """PATCH inherits the validator — a partial number is 422."""
    payload = _full_payload(mobile="9111122223")
    resp = client.post("/api/v1/walkouts", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    walkout_id = resp.json()["walkout_id"]

    resp = client.patch(
        f"/api/v1/walkouts/{walkout_id}",
        json={"mobile": "123"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
