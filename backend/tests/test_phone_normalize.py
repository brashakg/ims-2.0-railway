"""
IMS 2.0 — canonical Indian-mobile normalization (cross-cutting)
================================================================
Every phone-accepting model now routes through services.phone so the stored
form never drifts. Before, customers.py / marketing.WalkinRequest used a RAW
^[6-9]\\d{9}$ pattern that 422'd common human input (+91 / 0 / spaces), while
users.py / portal.py normalized it -- so the same number could be stored two
ways across collections (breaking dedup/search/marketing matching).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-phone")


# --- the shared util -------------------------------------------------------
def test_all_human_variants_collapse_to_bare_10_digit():
    from api.services.phone import normalize_indian_mobile

    for raw in (
        "9876543210",
        "+919876543210",
        "09876543210",
        "91-9876543210",
        "98765 43210",
        "(+91) 9876543210",
        "  9876543210  ",
        "+91-98765-43210",
    ):
        assert normalize_indian_mobile(raw) == "9876543210", raw


def test_none_and_blank_pass_through_as_none():
    from api.services.phone import normalize_indian_mobile

    assert normalize_indian_mobile(None) is None
    assert normalize_indian_mobile("") is None
    assert normalize_indian_mobile("   ") is None


def test_invalid_numbers_raise():
    from api.services.phone import normalize_indian_mobile

    # Has digits but isn't a valid Indian mobile -> raise.
    for bad in ("1234567890", "5876543210", "98765", "12345678901234"):
        with pytest.raises(ValueError):
            normalize_indian_mobile(bad)


def test_no_digits_is_treated_as_no_phone():
    from api.services.phone import normalize_indian_mobile

    # Pure junk with no digits -> "no phone given" -> None (not a hard error),
    # consistent with users.py's prior optional-phone behaviour.
    assert normalize_indian_mobile("abcd") is None


def test_leading_91_only_stripped_when_it_leaves_10_digits():
    """A valid mobile that itself starts with 91 (9123456789) must NOT have its
    leading 91 stripped -- the old marketing re.sub(r'^\\+?91',...) corrupted it
    to 23456789. The length-checked strip keeps it intact."""
    from api.services.phone import normalize_indian_mobile

    assert normalize_indian_mobile("9123456789") == "9123456789"
    # but a real +91 prefix on that same number still normalizes
    assert normalize_indian_mobile("+919123456789") == "9123456789"


def test_is_valid_helper_never_raises():
    from api.services.phone import is_valid_indian_mobile

    assert is_valid_indian_mobile("+91 9876543210") is True
    assert is_valid_indian_mobile("123") is False
    assert is_valid_indian_mobile(None) is False


# --- wired into the models -------------------------------------------------
def test_customer_create_accepts_and_stores_canonical():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="+91 98765 43210")
    assert c.mobile == "9876543210"


def test_customer_create_rejects_garbage_mobile():
    from pydantic import ValidationError

    from api.routers.customers import CustomerCreate

    with pytest.raises(ValidationError):
        CustomerCreate(name="Asha", mobile="000")


def test_patient_mobile_optional_but_normalized():
    from api.routers.customers import PatientCreate

    assert PatientCreate(name="Kid").mobile is None
    assert PatientCreate(name="Kid", mobile="091-9988776655").mobile == "9988776655"


def test_marketing_walkin_normalizes():
    from api.routers.marketing import WalkinRequest

    assert WalkinRequest(phone="+919876543210").phone == "9876543210"


def test_users_normalize_delegates_to_shared_util():
    from api.routers.users import _normalize_phone

    assert _normalize_phone("+91 9876543210") == "9876543210"
    assert _normalize_phone(None) is None
