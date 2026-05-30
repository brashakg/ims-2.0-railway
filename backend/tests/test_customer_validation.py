"""
IMS 2.0 -- CustomerCreate / PatientCreate validation + relation-preservation
=============================================================================
Regression tests for three QA-reported bugs:

Bug 1 (P1): create_customer nested patients[] overwrote caller-supplied
            `relation` with a name-heuristic ("Spouse" -> "Other").
Bug 2 (P3): CustomerCreate accepted invalid customer_type ("B2X"), bad GSTIN,
            malformed email, and future DOB without rejecting.
Bug 3 (P2): upload-bill returned HTTP 200 with persisted=False when GridFS
            was unavailable; now raises 503 (see test_upload_bill_503.py).

These tests validate Pydantic model behaviour (no DB required).
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError  # noqa: E402
from api.routers.customers import CustomerCreate, PatientCreate  # noqa: E402


# ---------------------------------------------------------------------------
# Bug 1 -- relation preserved through nested create
# ---------------------------------------------------------------------------


class TestRelationPreserved:
    """PatientCreate.relation field must survive round-tripping through the
    model so the create_customer endpoint logic can read p.relation."""

    def test_explicit_relation_preserved(self):
        p = PatientCreate(name="Priya Sharma", relation="Spouse")
        assert p.relation == "Spouse"

    def test_son_relation_preserved(self):
        p = PatientCreate(name="Arjun Sharma", relation="Son")
        assert p.relation == "Son"

    def test_daughter_relation_preserved(self):
        p = PatientCreate(name="Sneha Sharma", relation="Daughter")
        assert p.relation == "Daughter"

    def test_relation_none_is_allowed(self):
        # Absent relation -- the endpoint will apply the name heuristic.
        p = PatientCreate(name="Test User")
        assert p.relation is None

    def test_self_relation_preserved(self):
        p = PatientCreate(name="Rahul Verma", relation="Self")
        assert p.relation == "Self"

    def test_customers_nested_patients_carry_relation(self):
        # CustomerCreate.patients list must preserve the relation on each item.
        c = CustomerCreate(
            name="Rahul Verma",
            mobile="9876543210",
            patients=[
                PatientCreate(name="Rahul Verma", relation="Self"),
                PatientCreate(name="Priya Verma", relation="Spouse"),
                PatientCreate(name="Arjun Verma", relation="Son"),
            ],
        )
        relations = [p.relation for p in c.patients]
        assert relations == ["Self", "Spouse", "Son"]


# ---------------------------------------------------------------------------
# Bug 2a -- customer_type validation
# ---------------------------------------------------------------------------


class TestCustomerTypeValidation:
    def test_b2c_accepted(self):
        c = CustomerCreate(name="Test User", mobile="9876543210", customer_type="B2C")
        assert c.customer_type == "B2C"

    def test_b2b_accepted(self):
        c = CustomerCreate(name="Test User", mobile="9876543210", customer_type="B2B")
        assert c.customer_type == "B2B"

    def test_default_is_b2c(self):
        c = CustomerCreate(name="Test User", mobile="9876543210")
        assert c.customer_type == "B2C"

    def test_b2x_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CustomerCreate(name="Test User", mobile="9876543210", customer_type="B2X")
        assert "customer_type" in str(exc_info.value).lower() or "B2X" in str(
            exc_info.value
        )

    def test_lowercase_b2c_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test User", mobile="9876543210", customer_type="b2c")

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test User", mobile="9876543210", customer_type="")

    def test_b2c_uppercase_only(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test User", mobile="9876543210", customer_type="B2c")


# ---------------------------------------------------------------------------
# Bug 2b -- GSTIN validation
# ---------------------------------------------------------------------------


class TestGSTINValidation:
    # Valid GSTIN: 2-digit state + 5-letter + 4-digit + 1-letter + 1 entity + Z + 1 check
    _VALID = "27AAPFU0939F1ZV"

    def test_valid_gstin_accepted(self):
        c = CustomerCreate(name="ABC Traders", mobile="9876543210", gstin=self._VALID)
        assert c.gstin == self._VALID

    def test_absent_gstin_accepted(self):
        c = CustomerCreate(name="Test User", mobile="9876543210")
        assert c.gstin is None

    def test_none_gstin_accepted(self):
        c = CustomerCreate(name="Test User", mobile="9876543210", gstin=None)
        assert c.gstin is None

    def test_too_short_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", gstin="27AAPFU0939F1Z")

    def test_too_long_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", gstin="27AAPFU0939F1ZVX")

    def test_lowercase_gstin_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", gstin="27aapfu0939f1zv")

    def test_missing_z_sentinel_rejected(self):
        # Replace the mandatory 'Z' at position 13 with 'X'
        bad = "27AAPFU0939F1XV"
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", gstin=bad)

    def test_b2b_without_gstin_accepted(self):
        # GSTIN is NOT required for B2B -- that is a policy call deferred to the user.
        c = CustomerCreate(
            name="ABC Corp", mobile="9876543210", customer_type="B2B"
        )
        assert c.gstin is None

    def test_another_valid_gstin(self):
        c = CustomerCreate(
            name="XYZ Pvt Ltd",
            mobile="9876543210",
            gstin="29ABCDE1234F1Z5",
        )
        assert c.gstin == "29ABCDE1234F1Z5"


# ---------------------------------------------------------------------------
# Bug 2c -- email validation
# ---------------------------------------------------------------------------


class TestEmailValidation:
    def test_valid_email_accepted(self):
        c = CustomerCreate(
            name="Test User", mobile="9876543210", email="user@example.com"
        )
        assert c.email == "user@example.com"

    def test_absent_email_accepted(self):
        c = CustomerCreate(name="Test User", mobile="9876543210")
        assert c.email is None

    def test_none_email_accepted(self):
        c = CustomerCreate(name="Test User", mobile="9876543210", email=None)
        assert c.email is None

    def test_no_at_sign_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", email="notanemail")

    def test_missing_domain_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", email="user@")

    def test_no_dot_in_domain_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", email="user@nodot")

    def test_whitespace_in_email_rejected(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test", mobile="9876543210", email="us er@b.com")

    def test_subdomain_email_accepted(self):
        c = CustomerCreate(
            name="Test User",
            mobile="9876543210",
            email="user@mail.example.co.in",
        )
        assert c.email == "user@mail.example.co.in"


# ---------------------------------------------------------------------------
# Bug 2d -- future DOB rejected on CustomerCreate and PatientCreate
# ---------------------------------------------------------------------------


class TestFutureDOBRejected:
    _YESTERDAY = date.today() - timedelta(days=1)
    _TODAY = date.today()
    _TOMORROW = date.today() + timedelta(days=1)
    _FAR_FUTURE = date(2099, 1, 1)

    # --- CustomerCreate ---

    def test_past_dob_accepted_on_customer(self):
        c = CustomerCreate(name="Test User", mobile="9876543210", dob=self._YESTERDAY)
        assert c.dob == self._YESTERDAY

    def test_today_dob_accepted_on_customer(self):
        # A newborn registered today is valid.
        c = CustomerCreate(name="Test User", mobile="9876543210", dob=self._TODAY)
        assert c.dob == self._TODAY

    def test_tomorrow_dob_rejected_on_customer(self):
        with pytest.raises(ValidationError) as exc_info:
            CustomerCreate(name="Test User", mobile="9876543210", dob=self._TOMORROW)
        assert "future" in str(exc_info.value).lower()

    def test_far_future_dob_rejected_on_customer(self):
        with pytest.raises(ValidationError):
            CustomerCreate(name="Test User", mobile="9876543210", dob=self._FAR_FUTURE)

    def test_absent_dob_accepted_on_customer(self):
        c = CustomerCreate(name="Test User", mobile="9876543210")
        assert c.dob is None

    # --- PatientCreate ---

    def test_past_dob_accepted_on_patient(self):
        p = PatientCreate(name="Test Patient", dob=self._YESTERDAY)
        assert p.dob == self._YESTERDAY

    def test_today_dob_accepted_on_patient(self):
        p = PatientCreate(name="Test Patient", dob=self._TODAY)
        assert p.dob == self._TODAY

    def test_tomorrow_dob_rejected_on_patient(self):
        with pytest.raises(ValidationError) as exc_info:
            PatientCreate(name="Test Patient", dob=self._TOMORROW)
        assert "future" in str(exc_info.value).lower()

    def test_far_future_dob_rejected_on_patient(self):
        with pytest.raises(ValidationError):
            PatientCreate(name="Test Patient", dob=self._FAR_FUTURE)

    def test_absent_dob_accepted_on_patient(self):
        p = PatientCreate(name="Test Patient")
        assert p.dob is None

    def test_customer_nested_patient_future_dob_rejected(self):
        # A future DOB on a nested patient must also be caught.
        with pytest.raises(ValidationError):
            CustomerCreate(
                name="Test User",
                mobile="9876543210",
                patients=[PatientCreate(name="Baby", dob=self._TOMORROW)],
            )
